"""Execution node dispatch.

The node always initiates the connection. There is no inbound path to a Windows
machine, no listener, and no callback: the node polls this API outbound, claims
work, and posts results back.

Claiming is where a work order becomes a signed dispatch. The signature is not a
grant of trust on its own — the node re-validates everything before executing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..audit.service import AuditEvent, AuditService, Decision
from ..auth.work_order_signing import sign_work_order
from ..workorders.transitions import WorkOrderMode, WorkOrderState
from .deps import Caller, require_node

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class WorkspaceRegistration(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=200)
    allowed_harnesses: list[str] = Field(default_factory=list)
    allowed_modes: list[str] = Field(default_factory=lambda: ["readOnly"])


class NodeRegistration(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    workspaces: list[WorkspaceRegistration] = Field(default_factory=list)


class NodeView(BaseModel):
    node_id: UUID
    name: str
    workspaces: list[str]


class DispatchedWorkOrder(BaseModel):
    work_order_id: UUID
    token: str
    prompt: str
    workspace_id: str
    harness: str
    mode: WorkOrderMode
    correlation_id: str


class RunResult(BaseModel):
    outcome: str = Field(pattern="^(succeeded|failed|cancelled)$")
    summary: str = Field(max_length=4000)
    # Honest status boundary: implemented is not the same as deployed.
    status_boundary: str = Field(pattern="^(implemented|tested|deployed|user-confirmed)$")
    evidence: dict[str, Any] = Field(default_factory=dict)


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


@router.post("/register", response_model=NodeView)
async def register_node(
    body: NodeRegistration, request: Request, caller: Caller = Depends(require_node)
) -> NodeView:
    """A node registers itself and the workspace IDs it is willing to expose.

    The node names its own workspaces; the Gateway never invents one. Local paths
    are never sent here — only identifiers the node has bound locally.
    """
    pool = _pool(request)
    audit = AuditService(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Registration runs on every node start, so it must be idempotent and
            # must keep the same node id. Conflicting on device_id (not id, which
            # is freshly generated and can never conflict) is what makes a restart
            # return the node's existing identity instead of minting a new one.
            node_id = await conn.fetchval(
                """
                INSERT INTO execution_nodes (owner_user_id, device_id, name, last_seen_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (device_id) DO UPDATE
                    SET name = EXCLUDED.name, last_seen_at = now()
                RETURNING id
                """,
                caller.user_id,
                caller.device_id,
                body.name,
            )

            for workspace in body.workspaces:
                # Elevated mode is never registrable; it requires per-run owner
                # approval, so it cannot be granted by a node's own registration.
                modes = [m for m in workspace.allowed_modes if m in ("readOnly", "workspaceWrite")]
                node_workspace_id = await conn.fetchval(
                    """
                    INSERT INTO node_workspaces
                        (node_id, workspace_id, allowed_harnesses, allowed_modes)
                    VALUES ($1,$2,$3,$4)
                    ON CONFLICT (node_id, workspace_id) DO UPDATE
                        SET allowed_harnesses = EXCLUDED.allowed_harnesses,
                            allowed_modes = EXCLUDED.allowed_modes
                    RETURNING id
                    """,
                    node_id,
                    workspace.workspace_id,
                    workspace.allowed_harnesses,
                    modes,
                )

                # A node token is owner-bound and the local registry is the
                # authority for which named roots/modes this machine exposes.
                # Keep personal grants in sync with that declaration. Team
                # sharing remains a separate owner-controlled decision.
                await conn.execute(
                    """
                    UPDATE workspace_policy_grants
                    SET revoked_at = now()
                    WHERE node_workspace_id = $1
                      AND team_id IS NULL
                      AND NOT (mode = ANY($2::text[]))
                      AND revoked_at IS NULL
                    """,
                    node_workspace_id,
                    modes,
                )
                for mode in modes:
                    grant_id = await conn.fetchval(
                        """
                        SELECT id FROM workspace_policy_grants
                        WHERE node_workspace_id = $1 AND team_id IS NULL AND mode = $2
                        ORDER BY granted_at DESC LIMIT 1
                        """,
                        node_workspace_id,
                        mode,
                    )
                    if grant_id is None:
                        await conn.execute(
                            """
                            INSERT INTO workspace_policy_grants
                                (node_workspace_id, team_id, mode, granted_by)
                            VALUES ($1, NULL, $2, $3)
                            """,
                            node_workspace_id,
                            mode,
                            caller.user_id,
                        )
                    else:
                        await conn.execute(
                            """
                            UPDATE workspace_policy_grants
                            SET revoked_at = NULL, granted_by = $2, granted_at = now()
                            WHERE id = $1
                            """,
                            grant_id,
                            caller.user_id,
                        )

            workspace_ids = [workspace.workspace_id for workspace in body.workspaces]
            await conn.execute(
                """
                DELETE FROM node_workspaces
                WHERE node_id = $1
                  AND NOT (workspace_id = ANY($2::text[]))
                """,
                node_id,
                workspace_ids,
            )

            await audit.record_with(
                conn,
                AuditEvent(
                    action="node.register",
                    decision=Decision.ALLOWED,
                    correlation_id=f"node-{node_id}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    target_kind="node",
                    target_id=str(node_id),
                    detail=f"registered {len(body.workspaces)} workspace(s)",
                ),
            )

            rows = await conn.fetch(
                "SELECT workspace_id FROM node_workspaces WHERE node_id = $1", node_id
            )

    return NodeView(
        node_id=node_id, name=body.name, workspaces=[r["workspace_id"] for r in rows]
    )


@router.get("/work", response_model=list[DispatchedWorkOrder])
async def claim_work(
    request: Request, caller: Caller = Depends(require_node)
) -> list[DispatchedWorkOrder]:
    """Claim work assigned to this node.

    Only `assigned` orders belonging to this caller's own node are returned, and
    each is handed over as a freshly signed, short-lived, single-use dispatch.
    """
    pool = _pool(request)
    audit = AuditService(pool)
    signing_key = request.app.state.settings.signing_key
    dispatched: list[DispatchedWorkOrder] = []

    async with pool.acquire() as conn:
        async with conn.transaction():
            node = await conn.fetchrow(
                "SELECT id, owner_user_id FROM execution_nodes WHERE device_id = $1",
                caller.device_id,
            )
            if node is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="This device is not registered as an execution node.",
                )

            await conn.execute(
                "UPDATE execution_nodes SET last_seen_at = now() WHERE id = $1", node["id"]
            )

            rows = await conn.fetch(
                """
                SELECT w.id, w.prompt, w.workspace_id, w.harness, w.mode,
                       w.correlation_id, w.requested_by, w.team_id, w.profile_id
                FROM work_orders w
                WHERE w.execution_node_id = $1
                  AND w.state = 'assigned'
                  AND w.expires_at > now()
                ORDER BY w.created_at
                LIMIT 10
                FOR UPDATE OF w SKIP LOCKED
                """,
                node["id"],
            )

            for row in rows:
                signed = sign_work_order(
                    signing_key=signing_key,
                    work_order_id=str(row["id"]),
                    requesting_user_id=str(row["requested_by"]),
                    profile_id=str(row["profile_id"]),
                    team_id=str(row["team_id"]) if row["team_id"] else None,
                    execution_node_id=str(node["id"]),
                    workspace_id=row["workspace_id"],
                    harness=row["harness"],
                    mode=WorkOrderMode(row["mode"]),
                    correlation_id=row["correlation_id"],
                )

                # Record the nonce so a replayed dispatch is refused even if the
                # node's own in-memory set is lost on restart.
                await conn.execute(
                    """
                    INSERT INTO work_order_nonces (nonce, work_order_id)
                    VALUES ($1,$2) ON CONFLICT DO NOTHING
                    """,
                    signed.nonce,
                    row["id"],
                )
                await conn.execute(
                    "UPDATE work_orders SET state = 'inProgress', updated_at = now() WHERE id = $1",
                    row["id"],
                )
                await conn.execute(
                    """
                    INSERT INTO work_order_transitions
                        (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                    VALUES ($1,'assigned','inProgress',$2,'claimed by execution node',$3)
                    """,
                    row["id"],
                    caller.user_id,
                    row["correlation_id"],
                )
                await audit.record_with(
                    conn,
                    AuditEvent(
                        action="workorder.dispatch",
                        decision=Decision.ALLOWED,
                        correlation_id=row["correlation_id"],
                        actor_user_id=caller.user_id,
                        actor_device_id=caller.device_id,
                        profile_id=row["profile_id"],
                        team_id=row["team_id"],
                        target_kind="workorder",
                        target_id=str(row["id"]),
                        detail=f"dispatched to node for {row['harness']}",
                    ),
                )

                dispatched.append(
                    DispatchedWorkOrder(
                        work_order_id=row["id"],
                        token=signed.token,
                        prompt=row["prompt"],
                        workspace_id=row["workspace_id"],
                        harness=row["harness"],
                        mode=WorkOrderMode(row["mode"]),
                        correlation_id=row["correlation_id"],
                    )
                )

    return dispatched


@router.post("/work/{work_order_id}/result", status_code=status.HTTP_202_ACCEPTED)
async def submit_result(
    work_order_id: UUID,
    body: RunResult,
    request: Request,
    caller: Caller = Depends(require_node),
) -> dict[str, str]:
    """Return a run result. A node may only report on its own work orders."""
    pool = _pool(request)
    audit = AuditService(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT w.id, w.state, w.correlation_id, w.profile_id, w.team_id,
                       n.device_id
                FROM work_orders w
                JOIN execution_nodes n ON n.id = w.execution_node_id
                WHERE w.id = $1
                FOR UPDATE OF w
                """,
                work_order_id,
            )
            # 404 rather than 403 so one node cannot probe another's work.
            if row is None or row["device_id"] != caller.device_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
                )

            target = (
                WorkOrderState.READY_FOR_REVIEW
                if body.outcome == "succeeded"
                else WorkOrderState.FAILED
                if body.outcome == "failed"
                else WorkOrderState.CANCELLED
            )

            attempt = await conn.fetchval(
                "SELECT coalesce(max(attempt), 0) FROM work_order_runs WHERE work_order_id = $1",
                work_order_id,
            )
            await conn.execute(
                """
                UPDATE work_order_runs
                SET finished_at = now(), outcome = $3, status_boundary = $4, summary = $5
                WHERE work_order_id = $1 AND attempt = $2
                """,
                work_order_id,
                attempt,
                body.outcome,
                body.status_boundary,
                body.summary[:4000],
            )
            await conn.execute(
                "UPDATE work_orders SET state = $2, updated_at = now() WHERE id = $1",
                work_order_id,
                target.value,
            )
            await conn.execute(
                """
                INSERT INTO work_order_transitions
                    (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                work_order_id,
                row["state"],
                target.value,
                caller.user_id,
                f"node reported {body.outcome}",
                row["correlation_id"],
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="workorder.result",
                    decision=Decision.ALLOWED,
                    correlation_id=row["correlation_id"],
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=row["profile_id"],
                    team_id=row["team_id"],
                    target_kind="workorder",
                    target_id=str(work_order_id),
                    evidence=body.evidence,
                    detail=f"{body.outcome} ({body.status_boundary})",
                ),
            )

    return {"state": target.value}

"""Profile-scoped workstation tools for the personal Hermes runtime.

Hermes authenticates with its existing per-profile API key. The Gateway then
dispatches through the authoritative work-order tables; this is not a remote
shell and it does not introduce a second queue. The Windows node still resolves
every workspace identifier locally and re-validates the signed order.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..audit.service import AuditEvent, AuditService, Decision

router = APIRouter(prefix="/api/runtime/workstation", tags=["runtime-workstation"])

_ONLINE_WINDOW = timedelta(seconds=45)
_TERMINAL_STATES = frozenset(
    {"readyForReview", "resolved", "closed", "cancelled", "failed"}
)


class DispatchWork(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=20_000)
    workspace_id: str = Field(min_length=1, max_length=200)
    harness: Literal["shell", "claude", "codex"] = "shell"
    mode: Literal["readOnly", "workspaceWrite"] = "readOnly"


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The workstation service is temporarily unavailable.",
        )
    return pool


def _runtime_profile(request: Request) -> UUID:
    settings = request.app.state.settings
    expected = str(getattr(settings, "hermes_api_key", "") or "")
    supplied = request.headers.get("authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    else:
        supplied = ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized.")

    configured_profile = (
        str(getattr(settings, "workstation_profile_id", "") or "")
        or str(getattr(settings, "hermes_bootstrap_profile_id", "") or "")
    )
    try:
        return UUID(configured_profile)
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No personal workstation profile is configured.",
        ) from None


@router.get("/status")
async def workstation_status(request: Request) -> dict:
    """Named capabilities and liveness; local paths never leave the node."""
    profile_id = _runtime_profile(request)
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.name, n.last_seen_at, d.revoked_at,
                   nw.workspace_id, nw.allowed_harnesses, nw.allowed_modes
            FROM profiles p
            JOIN execution_nodes n ON n.owner_user_id = p.owner_user_id
            JOIN devices d ON d.id = n.device_id
            LEFT JOIN node_workspaces nw ON nw.node_id = n.id
            WHERE p.id = $1
            ORDER BY n.last_seen_at DESC NULLS LAST, nw.workspace_id
            """,
            profile_id,
        )

    now = datetime.now(timezone.utc)
    nodes: dict[str, dict] = {}
    for row in rows:
        node_id = str(row["id"])
        last_seen = row["last_seen_at"]
        online = bool(
            row["revoked_at"] is None
            and last_seen is not None
            and now - last_seen <= _ONLINE_WINDOW
        )
        node = nodes.setdefault(
            node_id,
            {
                "node_id": node_id,
                "name": row["name"],
                "online": online,
                "last_seen_at": last_seen.isoformat() if last_seen else None,
                "workspaces": [],
            },
        )
        if row["workspace_id"]:
            node["workspaces"].append(
                {
                    "workspace_id": row["workspace_id"],
                    "harnesses": list(row["allowed_harnesses"] or []),
                    "modes": list(row["allowed_modes"] or []),
                }
            )

    return {"available": True, "nodes": list(nodes.values())}


@router.post("/work", status_code=status.HTTP_202_ACCEPTED)
async def dispatch_workstation_work(
    request: Request,
    body: DispatchWork = Body(...),
) -> dict:
    """Create one already-authorized personal work order for an online node."""
    profile_id = _runtime_profile(request)
    pool = _pool(request)
    audit = AuditService(pool)
    correlation_id = f"workstation-{uuid4()}"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=12)

    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT p.owner_user_id, n.id AS node_id, n.name AS node_name
                FROM profiles p
                JOIN execution_nodes n ON n.owner_user_id = p.owner_user_id
                JOIN devices d ON d.id = n.device_id
                JOIN node_workspaces nw ON nw.node_id = n.id
                WHERE p.id = $1
                  AND d.revoked_at IS NULL
                  AND n.last_seen_at >= now() - interval '45 seconds'
                  AND nw.workspace_id = $2
                  AND $3 = ANY(nw.allowed_harnesses)
                  AND $4 = ANY(nw.allowed_modes)
                  AND EXISTS (
                      SELECT 1 FROM workspace_policy_grants g
                      WHERE g.node_workspace_id = nw.id
                        AND g.team_id IS NULL
                        AND g.mode = $4
                        AND g.revoked_at IS NULL
                  )
                ORDER BY n.last_seen_at DESC
                LIMIT 1
                FOR UPDATE OF n
                """,
                profile_id,
                body.workspace_id,
                body.harness,
                body.mode,
            )
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "No online workstation exposes that workspace, harness, and mode. "
                        "Read workstation status and use an exact advertised capability."
                    ),
                )

            work_order_id = await conn.fetchval(
                """
                INSERT INTO work_orders (
                    requested_by, profile_id, team_id, conversation_id,
                    assigned_user_id, execution_node_id, harness, workspace_id,
                    title, prompt, state, mode, completion_criteria,
                    correlation_id, expires_at
                ) VALUES (
                    $1,$2,NULL,$3,$1,$4,$5,$6,$7,$8,'assigned',$9,$10,$11,$12
                )
                RETURNING id
                """,
                target["owner_user_id"],
                profile_id,
                uuid4(),
                target["node_id"],
                body.harness,
                body.workspace_id,
                body.title,
                body.instruction,
                body.mode,
                ["Complete the requested action and report verifiable evidence."],
                correlation_id,
                expires_at,
            )
            await conn.execute(
                """
                INSERT INTO work_order_subscriptions (work_order_id, user_id, reason)
                VALUES ($1,$2,'requester') ON CONFLICT DO NOTHING
                """,
                work_order_id,
                target["owner_user_id"],
            )
            await conn.execute(
                """
                INSERT INTO work_order_transitions
                    (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                VALUES
                    ($1,'draft','submitted',$2,'explicit request through personal Sentry',$3),
                    ($1,'submitted','triaged',$2,'matched to an advertised workstation capability',$3),
                    ($1,'triaged','assigned',$2,'assigned to the online personal workstation',$3)
                """,
                work_order_id,
                target["owner_user_id"],
                correlation_id,
            )
            await conn.execute(
                """
                INSERT INTO work_order_runs
                    (work_order_id, attempt, harness, execution_node_id)
                VALUES ($1,1,$2,$3)
                """,
                work_order_id,
                body.harness,
                target["node_id"],
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="workstation.dispatch",
                    decision=Decision.ALLOWED,
                    correlation_id=correlation_id,
                    actor_user_id=target["owner_user_id"],
                    profile_id=profile_id,
                    target_kind="workorder",
                    target_id=str(work_order_id),
                    detail=(
                        f"dispatched {body.harness}/{body.mode} to "
                        f"{body.workspace_id} on {target['node_name']}"
                    ),
                ),
            )

    return {
        "available": True,
        "work_order_id": str(work_order_id),
        "state": "assigned",
        "node": target["node_name"],
        "workspace_id": body.workspace_id,
        "harness": body.harness,
        "mode": body.mode,
    }


@router.get("/work/{work_order_id}")
async def workstation_work_result(work_order_id: UUID, request: Request) -> dict:
    """Return the durable result summary for this profile's dispatched work."""
    profile_id = _runtime_profile(request)
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT w.id, w.title, w.state, w.workspace_id, w.harness, w.mode,
                   r.outcome, r.status_boundary, r.summary, r.finished_at
            FROM work_orders w
            LEFT JOIN LATERAL (
                SELECT outcome, status_boundary, summary, finished_at
                FROM work_order_runs
                WHERE work_order_id = w.id
                ORDER BY attempt DESC LIMIT 1
            ) r ON TRUE
            WHERE w.id = $1 AND w.profile_id = $2
            """,
            work_order_id,
            profile_id,
        )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found.")

    return {
        "available": True,
        "work_order_id": str(row["id"]),
        "title": row["title"],
        "state": row["state"],
        "terminal": row["state"] in _TERMINAL_STATES,
        "workspace_id": row["workspace_id"],
        "harness": row["harness"],
        "mode": row["mode"],
        "outcome": row["outcome"],
        "status_boundary": row["status_boundary"],
        "summary": row["summary"],
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }

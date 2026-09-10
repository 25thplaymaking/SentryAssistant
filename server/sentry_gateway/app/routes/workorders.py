"""Durable work-order routes.

The database row is the authority. Every transition goes through the tested
state machine, and every decision — allowed or denied — produces an audit event
in the same transaction as the change it describes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..audit.service import AuditEvent, AuditService, Decision
from ..workorders.transitions import (
    Actor,
    AuthorizationError,
    TeamRole,
    TransitionError,
    WorkOrderMode,
    WorkOrderSnapshot,
    WorkOrderState,
    starts_new_run,
    validate_transition,
)
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/workorders", tags=["workorders"])

DEFAULT_TTL = timedelta(hours=12)


class CreateWorkOrder(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    completion_criteria: list[str] = Field(default_factory=list)
    mode: WorkOrderMode = WorkOrderMode.READ_ONLY
    team_id: UUID | None = None


class TransitionRequest(BaseModel):
    target: WorkOrderState
    reason: str | None = Field(default=None, max_length=500)


class WorkOrderView(BaseModel):
    id: UUID
    title: str
    state: WorkOrderState
    mode: WorkOrderMode
    correlation_id: str


class _DeniedTransition(Exception):
    """Carries a denial out of the transaction so its audit row can be written
    on a fresh connection after the rollback."""

    def __init__(self, event: AuditEvent, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.event = event
        self.status_code = status_code
        self.detail = detail


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


async def _load_actor(conn, caller: Caller, team_id: UUID | None, requester_id: UUID | None,
                      assignee_id: UUID | None, node_owner_id: UUID | None) -> Actor:
    """Resolve the caller's role from current membership, never from the request."""
    role = TeamRole.OWNER
    if team_id is not None:
        row = await conn.fetchrow(
            """
            SELECT role FROM team_members
            WHERE team_id = $1 AND user_id = $2 AND removed_at IS NULL
            """,
            team_id,
            caller.user_id,
        )
        if row is None:
            # A removed member loses access immediately.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a current member of this team.",
            )
        role = TeamRole(row["role"])

    return Actor(
        user_id=str(caller.user_id),
        role=role,
        is_requester=requester_id == caller.user_id,
        is_assignee=assignee_id == caller.user_id,
        is_node_owner=node_owner_id == caller.user_id,
    )


@router.post("", response_model=WorkOrderView, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    body: CreateWorkOrder, request: Request, caller: Caller = Depends(require_caller)
) -> WorkOrderView:
    """Create a draft. A colleague may do this without choosing a harness, node,
    repository path, or implementation method."""
    pool = _pool(request)
    audit = AuditService(pool)
    correlation_id = f"wo-{uuid4()}"
    expires_at = datetime.now(timezone.utc) + DEFAULT_TTL

    async with pool.acquire() as conn:
        async with conn.transaction():
            if body.team_id is not None:
                member = await conn.fetchrow(
                    """
                    SELECT role FROM team_members
                    WHERE team_id = $1 AND user_id = $2 AND removed_at IS NULL
                    """,
                    body.team_id,
                    caller.user_id,
                )
                if member is None:
                    # record(), not record_with(): the raise below rolls this
                    # transaction back and would take the denial row with it.
                    await audit.record(
                        AuditEvent(
                            action="workorder.create",
                            decision=Decision.DENIED,
                            correlation_id=correlation_id,
                            actor_user_id=caller.user_id,
                            actor_device_id=caller.device_id,
                            team_id=body.team_id,
                            detail="non-member attempted to create a team work order",
                        )
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You are not a current member of this team.",
                    )

            row = await conn.fetchrow(
                """
                INSERT INTO work_orders (
                    requested_by, profile_id, team_id, conversation_id,
                    title, prompt, state, mode, completion_criteria,
                    correlation_id, expires_at
                ) VALUES ($1,$2,$3,$4,$5,$6,'draft',$7,$8,$9,$10)
                RETURNING id, title, state, mode, correlation_id
                """,
                caller.user_id,
                caller.profile_id,
                body.team_id,
                uuid4(),
                body.title,
                body.prompt,
                body.mode.value,
                body.completion_criteria
                or ["Complete the requested outcome and report verifiable evidence."],
                correlation_id,
                expires_at,
            )

            await conn.execute(
                """
                INSERT INTO work_order_subscriptions (work_order_id, user_id, reason)
                VALUES ($1, $2, 'requester') ON CONFLICT DO NOTHING
                """,
                row["id"],
                caller.user_id,
            )

            await audit.record_with(
                conn,
                AuditEvent(
                    action="workorder.create",
                    decision=Decision.ALLOWED,
                    correlation_id=correlation_id,
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=caller.profile_id,
                    team_id=body.team_id,
                    target_kind="workorder",
                    target_id=str(row["id"]),
                    detail=f"created draft {body.title!r}",
                ),
            )

    return WorkOrderView(
        id=row["id"],
        title=row["title"],
        state=WorkOrderState(row["state"]),
        mode=WorkOrderMode(row["mode"]),
        correlation_id=row["correlation_id"],
    )


@router.get("/{work_order_id}", response_model=WorkOrderView)
async def get_work_order(
    work_order_id: UUID, request: Request, caller: Caller = Depends(require_caller)
) -> WorkOrderView:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT w.id, w.title, w.state, w.mode, w.correlation_id,
                   w.requested_by, w.team_id
            FROM work_orders w WHERE w.id = $1
            """,
            work_order_id,
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

        # Direct-object-reference defence: reading requires being the requester or
        # a current member of the owning team.
        if row["requested_by"] != caller.user_id:
            if row["team_id"] is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
                )
            member = await conn.fetchrow(
                """
                SELECT 1 FROM team_members
                WHERE team_id = $1 AND user_id = $2 AND removed_at IS NULL
                """,
                row["team_id"],
                caller.user_id,
            )
            if member is None:
                # 404 rather than 403 so existence is not disclosed.
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
                )

    return WorkOrderView(
        id=row["id"],
        title=row["title"],
        state=WorkOrderState(row["state"]),
        mode=WorkOrderMode(row["mode"]),
        correlation_id=row["correlation_id"],
    )


@router.post("/{work_order_id}/transition", response_model=WorkOrderView)
async def transition_work_order(
    work_order_id: UUID,
    body: TransitionRequest,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> WorkOrderView:
    pool = _pool(request)
    audit = AuditService(pool)

    try:
        return await _apply_transition(pool, audit, work_order_id, body, caller)
    except _DeniedTransition as denied:
        # The transaction has already rolled back, so this write lands on a new
        # connection and survives.
        await audit.record(denied.event)
        raise HTTPException(status_code=denied.status_code, detail=denied.detail) from denied


async def _apply_transition(pool, audit, work_order_id, body, caller) -> WorkOrderView:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the row so two concurrent transitions cannot both validate
            # against the same stale state.
            row = await conn.fetchrow(
                """
                SELECT w.*, n.owner_user_id AS node_owner_id
                FROM work_orders w
                LEFT JOIN execution_nodes n ON n.id = w.execution_node_id
                WHERE w.id = $1
                FOR UPDATE OF w
                """,
                work_order_id,
            )
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
                )

            actor = await _load_actor(
                conn,
                caller,
                row["team_id"],
                row["requested_by"],
                row["assigned_user_id"],
                row["node_owner_id"],
            )

            grant = None
            if row["execution_node_id"] is not None and row["workspace_id"]:
                grant = await conn.fetchrow(
                    """
                    SELECT 1 FROM workspace_policy_grants g
                    JOIN node_workspaces nw ON nw.id = g.node_workspace_id
                    WHERE nw.node_id = $1 AND nw.workspace_id = $2
                      AND g.mode = $3 AND g.revoked_at IS NULL
                      AND (g.team_id IS NOT DISTINCT FROM $4)
                    """,
                    row["execution_node_id"],
                    row["workspace_id"],
                    row["mode"],
                    row["team_id"],
                )

            snapshot = WorkOrderSnapshot(
                state=WorkOrderState(row["state"]),
                mode=WorkOrderMode(row["mode"]),
                assignee_user_id=str(row["assigned_user_id"]) if row["assigned_user_id"] else None,
                execution_node_id=str(row["execution_node_id"]) if row["execution_node_id"] else None,
                harness=row["harness"],
                workspace_id=row["workspace_id"],
                has_policy_grant=grant is not None,
            )

            try:
                validate_transition(actor, snapshot, body.target)
            except (TransitionError, AuthorizationError) as exc:
                # Deliberately NOT record_with(conn): raising rolls this
                # transaction back, which would discard the very row recording
                # the denial. A denied decision is the most security-relevant
                # event there is, so it commits on its own connection.
                denial = AuditEvent(
                    action="workorder.transition",
                    decision=Decision.DENIED,
                    correlation_id=row["correlation_id"],
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=row["profile_id"],
                    team_id=row["team_id"],
                    target_kind="workorder",
                    target_id=str(work_order_id),
                    detail=f"{row['state']} -> {body.target}: {exc}",
                )
                code = (
                    status.HTTP_403_FORBIDDEN
                    if isinstance(exc, AuthorizationError)
                    else status.HTTP_409_CONFLICT
                )
                raise _DeniedTransition(denial, code, str(exc)) from exc

            updated = await conn.fetchrow(
                """
                UPDATE work_orders
                SET state = $2,
                    completion_criteria = CASE
                        WHEN cardinality(completion_criteria) = 0
                        THEN ARRAY['Complete the requested outcome and report verifiable evidence.']::text[]
                        ELSE completion_criteria
                    END,
                    updated_at = now()
                WHERE id = $1
                RETURNING id, title, state, mode, correlation_id
                """,
                work_order_id,
                body.target.value,
            )

            await conn.execute(
                """
                INSERT INTO work_order_transitions
                    (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                work_order_id,
                row["state"],
                body.target.value,
                caller.user_id,
                body.reason,
                row["correlation_id"],
            )

            # A changes-requested cycle starts a fresh run and never mutates the
            # previous one, so earlier evidence survives.
            if starts_new_run(body.target):
                attempt = await conn.fetchval(
                    "SELECT coalesce(max(attempt), 0) + 1 FROM work_order_runs WHERE work_order_id = $1",
                    work_order_id,
                )
                await conn.execute(
                    """
                    INSERT INTO work_order_runs
                        (work_order_id, attempt, harness, execution_node_id)
                    VALUES ($1,$2,$3,$4)
                    """,
                    work_order_id,
                    attempt,
                    row["harness"] or "unassigned",
                    row["execution_node_id"],
                )

            await audit.record_with(
                conn,
                AuditEvent(
                    action="workorder.transition",
                    decision=Decision.ALLOWED,
                    correlation_id=row["correlation_id"],
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=row["profile_id"],
                    team_id=row["team_id"],
                    target_kind="workorder",
                    target_id=str(work_order_id),
                    detail=f"{row['state']} -> {body.target}",
                ),
            )

    return WorkOrderView(
        id=updated["id"],
        title=updated["title"],
        state=WorkOrderState(updated["state"]),
        mode=WorkOrderMode(updated["mode"]),
        correlation_id=updated["correlation_id"],
    )

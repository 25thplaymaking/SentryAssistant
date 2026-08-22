"""Profile-scoped projection of Sentry's authoritative work orders.

The board is not a second task database. Cards are projections of work_orders;
creation and transitions reuse the same audited state machine as every other
Sentry client.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..workorders.transitions import WorkOrderMode, WorkOrderState, allowed_targets
from .deps import Caller, require_caller
from .workorders import (
    CreateWorkOrder,
    TransitionRequest,
    create_work_order,
    transition_work_order,
)

router = APIRouter(prefix="/api/kanban", tags=["kanban"])

_COLUMNS = (
    ("Draft", {"draft"}),
    ("Queued", {"submitted", "needsClarification", "triaged", "assigned"}),
    ("Working", {"inProgress", "needsInput", "changesRequested"}),
    ("Review", {"readyForReview"}),
    ("Done", {"resolved", "closed"}),
    ("Stopped", {"cancelled", "failed"}),
)


class CreateTask(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)
    description: str = Field(default="", max_length=20_000)
    mode: WorkOrderMode = WorkOrderMode.READ_ONLY


class MoveTask(BaseModel):
    target: WorkOrderState
    reason: str | None = Field(default=None, max_length=500)


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


def _task(row) -> dict[str, Any]:
    state = WorkOrderState(row["state"])
    targets = [target.value for target in allowed_targets(state)]
    if not all(
        row[key]
        for key in ("assigned_user_id", "execution_node_id", "harness", "workspace_id")
    ):
        targets = [target for target in targets if target not in {"assigned", "inProgress"}]
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "body": row["prompt"],
        "state": row["state"],
        "status": row["state"],
        "mode": row["mode"],
        "harness": row["harness"],
        "workspace_id": row["workspace_id"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "allowed_targets": targets,
    }


async def _row_for_profile(conn, work_order_id: UUID, profile_id: UUID):
    row = await conn.fetchrow(
        """
        SELECT id, title, prompt, state, mode, harness, workspace_id,
               assigned_user_id, execution_node_id, completion_criteria,
               correlation_id, created_at, updated_at
        FROM work_orders WHERE id = $1 AND profile_id = $2
        """,
        work_order_id,
        profile_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return row


@router.get("/board")
async def my_board(
    request: Request, caller: Caller = Depends(require_caller)
) -> dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, prompt, state, mode, harness, workspace_id,
                   assigned_user_id, execution_node_id, created_at, updated_at
            FROM work_orders
            WHERE profile_id = $1
            ORDER BY updated_at DESC
            LIMIT 500
            """,
            caller.profile_id,
        )
    tasks = [_task(row) for row in rows]
    return {
        "backend": "sentry-workorders",
        "read_only": False,
        "tasks": tasks,
        "columns": [
            {
                "name": name,
                "tasks": [task for task in tasks if task["state"] in states],
            }
            for name, states in _COLUMNS
        ],
    }


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    body: CreateTask,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict[str, Any]:
    prompt = (body.body or body.description or body.title).strip()
    view = await create_work_order(
        CreateWorkOrder(title=body.title, prompt=prompt, mode=body.mode),
        request,
        caller,
    )
    return {"task": view.model_dump(mode="json"), "backend": "sentry-workorders"}


@router.get("/tasks/{work_order_id}")
async def task_detail(
    work_order_id: UUID,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await _row_for_profile(conn, work_order_id, caller.profile_id)
        transitions = await conn.fetch(
            """
            SELECT from_state, to_state, reason, occurred_at
            FROM work_order_transitions WHERE work_order_id = $1
            ORDER BY occurred_at DESC LIMIT 50
            """,
            work_order_id,
        )
        runs = await conn.fetch(
            """
            SELECT attempt, harness, started_at, finished_at, outcome,
                   status_boundary, summary
            FROM work_order_runs WHERE work_order_id = $1 ORDER BY attempt DESC
            """,
            work_order_id,
        )
        events = await conn.fetch(
            """
            SELECT event_type, summary, occurred_at
            FROM work_order_events WHERE work_order_id = $1
            ORDER BY event_index DESC LIMIT 100
            """,
            work_order_id,
        )
    task = _task(row)
    task["completion_criteria"] = list(row["completion_criteria"] or [])
    task["correlation_id"] = row["correlation_id"]
    return {
        "backend": "sentry-workorders",
        "task": task,
        "transitions": [dict(item) for item in transitions],
        "runs": [dict(item) for item in runs],
        "events": [dict(item) for item in events],
    }


@router.post("/tasks/{work_order_id}/transition")
async def move_task(
    work_order_id: UUID,
    body: MoveTask,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict[str, Any]:
    await transition_work_order(
        work_order_id,
        TransitionRequest(target=body.target, reason=body.reason),
        request,
        caller,
    )
    return await task_detail(work_order_id, request, caller)

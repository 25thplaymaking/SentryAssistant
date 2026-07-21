"""The caller's own Kanban board — per-user replacement for the fork's dead
Kanban panel (which errored on an absent local agent package).

Scoped to the authenticated caller's profile and fail-closed: a person only ever
reads their own agent's board.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..agent_runtime.hermes import UnknownProfileError
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/kanban", tags=["kanban"])


@router.get("/board")
async def my_board(
    request: Request, caller: Caller = Depends(require_caller)
) -> dict[str, Any]:
    runtime = request.app.state.runtime
    try:
        return await runtime.read_work_board(caller.profile_id)
    except UnknownProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No agent runtime is provisioned for this profile.",
        ) from exc

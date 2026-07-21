"""The caller's own skills — per-user replacement for the fork's dead Skills
panel (which 500'd on an absent local agent package).

Reads the Gateway-owned ``skill_proposals`` governance table, scoped to the
caller's profile. Every skill carries its lifecycle state, so the panel shows an
honest picture (proposed / awaiting approval / active / rejected …) rather than
pretending an agent-authored skill is live before it has been approved.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .deps import Caller, require_caller

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillView(BaseModel):
    id: UUID
    name: str
    #: proposed | scanned | evaluated | awaitingApproval | rejected | active | superseded | rolledBack
    state: str
    content_hash: str
    created_at: datetime


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


@router.get("", response_model=list[SkillView])
async def my_skills(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[SkillView]:
    """Every skill in the caller's own profile, with its governance state."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, state, content_hash, created_at
            FROM skill_proposals
            WHERE profile_id = $1
            ORDER BY name, created_at DESC
            """,
            caller.profile_id,
        )
    return [
        SkillView(
            id=r["id"],
            name=r["name"],
            state=r["state"],
            content_hash=r["content_hash"],
            created_at=r["created_at"],
        )
        for r in rows
    ]

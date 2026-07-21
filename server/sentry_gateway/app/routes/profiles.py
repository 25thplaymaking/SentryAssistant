"""Profiles a caller can see — the per-user replacement for the fork's
agent-native Profiles panel.

Scoped to the authenticated caller: a person sees only profiles they own, never
another user's. This is the first Phase-2 surface that routes a management panel
through the Gateway (profile-scoped) instead of the fork reaching into a local
agent package.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .deps import Caller, require_caller

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileView(BaseModel):
    id: UUID
    kind: str
    display_name: str
    created_at: datetime
    #: True for the profile the caller's current token is bound to.
    is_active: bool


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


@router.get("/me", response_model=list[ProfileView])
async def my_profiles(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[ProfileView]:
    """Every non-archived profile owned by the caller. Never another user's."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, display_name, created_at
            FROM profiles
            WHERE owner_user_id = $1 AND archived_at IS NULL
            ORDER BY kind, display_name
            """,
            caller.user_id,
        )
    return [
        ProfileView(
            id=r["id"],
            kind=r["kind"],
            display_name=r["display_name"],
            created_at=r["created_at"],
            is_active=(r["id"] == caller.profile_id),
        )
        for r in rows
    ]

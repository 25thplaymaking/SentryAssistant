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
    id: str
    name: str
    #: proposed | scanned | evaluated | awaitingApproval | rejected | active | superseded | rolledBack
    state: str
    content_hash: str | None = None
    created_at: datetime | None = None
    description: str | None = None
    enabled: bool = True
    scope: str | None = None
    source: str = "sentry"


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
    governed = [
        SkillView(
            id=str(r["id"]),
            name=r["name"],
            state=r["state"],
            content_hash=r["content_hash"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    runtime = getattr(request.app.state, "runtime", None)
    if hasattr(runtime, "native_runtime_status"):
        try:
            status_view = await runtime.native_runtime_status(caller.profile_id)
            inventory = status_view.get("inventory") if isinstance(status_view, dict) else {}
            for item in (inventory or {}).get("skills") or []:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                governed.append(
                    SkillView(
                        id=f"codex:{item.get('scope') or 'user'}:{item['name']}",
                        name=str(item["name"]),
                        state="active" if item.get("enabled", True) else "disabled",
                        description=str(item.get("description") or item.get("short_description") or "") or None,
                        enabled=bool(item.get("enabled", True)),
                        scope=str(item.get("scope") or "") or None,
                        source="codex",
                    )
                )
        except Exception:
            # Governance records remain useful even while the workstation is offline.
            pass
    return governed

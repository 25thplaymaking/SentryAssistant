"""The caller's own durable memory — per-user replacement for the fork's dead
Memory panel. Gateway-owned (the Hermes API exposes no memory endpoint), scoped
to the caller's profile, and never shared across profiles.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .deps import Caller, require_caller

router = APIRouter(prefix="/api/memory", tags=["memory"])

_SECTION_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class MemorySection(BaseModel):
    section: str
    content: str
    updated_at: datetime


class MemoryWrite(BaseModel):
    content: str = Field(max_length=200_000)


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


@router.get("", response_model=list[MemorySection])
async def list_memory(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[MemorySection]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT section, content, updated_at FROM profile_memory WHERE profile_id = $1 ORDER BY section",
            caller.profile_id,
        )
    return [
        MemorySection(section=r["section"], content=r["content"], updated_at=r["updated_at"])
        for r in rows
    ]


@router.put("/{section}", response_model=MemorySection)
async def write_memory(
    section: str,
    body: MemoryWrite,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> MemorySection:
    if not _SECTION_RE.match(section):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid section name")
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO profile_memory (profile_id, section, content, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (profile_id, section)
            DO UPDATE SET content = EXCLUDED.content, updated_at = now()
            RETURNING section, content, updated_at
            """,
            caller.profile_id,
            section,
            body.content,
        )
    return MemorySection(section=row["section"], content=row["content"], updated_at=row["updated_at"])

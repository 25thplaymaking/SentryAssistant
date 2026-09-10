"""Caller-owned personal notes, independent of Hermes agent memory approvals.

Legacy list/PUT clients remain compatible. The workspace contract adds safe
create/update/delete with optimistic concurrency and profile-switch protection.
"""
from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import AwareDatetime, BaseModel, Field

from ..profile_memory_context import MAX_CONTEXT_CHARACTERS, MAX_CONTEXT_SECTIONS
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/memory", tags=["memory"])
_SECTION_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")


class MemorySection(BaseModel):
    section: str
    content: str
    updated_at: datetime


class MemoryWrite(BaseModel):
    content: str = Field(max_length=200_000)
    # Omitted = legacy unconditional write. Explicit null = create only.
    expected_updated_at: AwareDatetime | None = None
    expected_profile_id: UUID | None = None


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database is unavailable.")
    return pool


def _validate(section: str) -> None:
    if not _SECTION_RE.fullmatch(section):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid section name")


def _profile(caller: Caller, expected: UUID | None) -> None:
    if expected is not None and expected != caller.profile_id:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Profile changed. Close and reopen Memory before editing.")


def _conflict() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT,
                         "This section changed or was deleted. Reload before saving.")


def _section(row) -> MemorySection:
    return MemorySection(section=row["section"], content=row["content"], updated_at=row["updated_at"])


async def _read(request: Request, caller: Caller) -> list[MemorySection]:
    async with _pool(request).acquire() as conn:
        rows = await conn.fetch(
            "SELECT section, content, updated_at FROM profile_memory WHERE profile_id = $1 ORDER BY section",
            caller.profile_id,
        )
    return [_section(row) for row in rows]


@router.get("", response_model=list[MemorySection])
async def list_memory(request: Request, response: Response,
                      caller: Caller = Depends(require_caller)) -> list[MemorySection]:
    response.headers["Cache-Control"] = "no-store"
    return await _read(request, caller)


@router.get("/workspace")
async def memory_workspace(request: Request, response: Response,
                           caller: Caller = Depends(require_caller)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"profile_id": str(caller.profile_id), "sections": await _read(request, caller),
            "context_policy": {"private_gateway_chat": True,
                               "max_characters": MAX_CONTEXT_CHARACTERS,
                               "max_sections": MAX_CONTEXT_SECTIONS,
                               "agent_memory_is_separate": True}}


@router.put("/{section}", response_model=MemorySection)
async def write_memory(section: str, body: MemoryWrite, request: Request,
                       response: Response,
                       caller: Caller = Depends(require_caller)) -> MemorySection:
    _validate(section)
    _profile(caller, body.expected_profile_id)
    response.headers["Cache-Control"] = "no-store"
    async with _pool(request).acquire() as conn:
        if "expected_updated_at" not in body.model_fields_set:
            # Preserve old clients. New clients ALWAYS supply the version.
            row = await conn.fetchrow(
                """INSERT INTO profile_memory (profile_id, section, content, updated_at)
                VALUES ($1, $2, $3, clock_timestamp())
                ON CONFLICT (profile_id, section)
                DO UPDATE SET content = EXCLUDED.content,
                  updated_at = GREATEST(clock_timestamp(), profile_memory.updated_at + interval '1 microsecond')
                RETURNING section, content, updated_at""",
                caller.profile_id, section, body.content,
            )
        elif body.expected_updated_at is None:
            row = await conn.fetchrow(
                """INSERT INTO profile_memory (profile_id, section, content, updated_at)
                VALUES ($1, $2, $3, clock_timestamp())
                ON CONFLICT (profile_id, section) DO NOTHING
                RETURNING section, content, updated_at""",
                caller.profile_id, section, body.content,
            )
        else:
            row = await conn.fetchrow(
                """UPDATE profile_memory SET content = $3,
                  updated_at = GREATEST(clock_timestamp(), updated_at + interval '1 microsecond')
                WHERE profile_id = $1 AND section = $2 AND updated_at = $4
                RETURNING section, content, updated_at""",
                caller.profile_id, section, body.content, body.expected_updated_at,
            )
    if row is None:
        raise _conflict()
    return _section(row)


@router.delete("/{section}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(section: str, request: Request,
                        expected_updated_at: AwareDatetime = Query(...),
                        expected_profile_id: UUID = Query(...),
                        caller: Caller = Depends(require_caller)) -> Response:
    _validate(section)
    _profile(caller, expected_profile_id)
    async with _pool(request).acquire() as conn:
        row = await conn.fetchrow(
            """DELETE FROM profile_memory
            WHERE profile_id = $1 AND section = $2 AND updated_at = $3 RETURNING section""",
            caller.profile_id, section, expected_updated_at,
        )
    if row is None:
        raise _conflict()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})

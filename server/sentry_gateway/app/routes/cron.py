"""The caller's own scheduled jobs — per-user replacement for the fork's dead
Cron/Tasks panel. Gateway-owned (the Hermes API exposes no cron endpoint) and
scoped to the caller's profile. Execution (firing a job) is the Gateway
scheduler's concern; this surface owns the authoritative definitions.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .deps import Caller, require_caller

router = APIRouter(prefix="/api/cron", tags=["cron"])


class CronJob(BaseModel):
    id: UUID
    name: str
    schedule: str
    prompt: str
    enabled: bool
    created_at: datetime


class CronCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    schedule: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default="", max_length=8000)


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


def _view(r) -> CronJob:
    return CronJob(
        id=r["id"], name=r["name"], schedule=r["schedule"],
        prompt=r["prompt"], enabled=r["enabled"], created_at=r["created_at"],
    )


@router.get("", response_model=list[CronJob])
async def list_jobs(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[CronJob]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, schedule, prompt, enabled, created_at
            FROM profile_cron_jobs WHERE profile_id = $1 ORDER BY created_at DESC
            """,
            caller.profile_id,
        )
    return [_view(r) for r in rows]


@router.post("", response_model=CronJob)
async def create_job(
    body: CronCreate, request: Request, caller: Caller = Depends(require_caller)
) -> CronJob:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO profile_cron_jobs (profile_id, name, schedule, prompt)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, schedule, prompt, enabled, created_at
            """,
            caller.profile_id, body.name, body.schedule, body.prompt,
        )
    return _view(row)

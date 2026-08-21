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

from ..cron.scheduler import run_job
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/cron", tags=["cron"])

#: Every SELECT/RETURNING in this module carries the same columns as CronJob.
_COLUMNS = (
    "id, name, schedule, prompt, enabled, created_at,"
    " last_run_at, last_status, last_summary"
)


class CronJob(BaseModel):
    id: UUID
    name: str
    schedule: str
    prompt: str
    enabled: bool
    created_at: datetime
    #: Execution state, written only by the scheduler/runner (migration 013).
    last_run_at: datetime | None = None
    last_status: str | None = None
    last_summary: str | None = None


class CronCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    schedule: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default="", max_length=8000)


class CronUpdate(BaseModel):
    """Partial update: absent fields keep their stored value. Bounds match
    CronCreate so PUT cannot smuggle in what POST refuses."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    schedule: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, max_length=8000)
    enabled: bool | None = None


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
        last_run_at=r["last_run_at"], last_status=r["last_status"],
        last_summary=r["last_summary"],
    )


def _not_found() -> HTTPException:
    # 404 rather than 403 so another profile's job IDs cannot be probed.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


@router.get("", response_model=list[CronJob])
async def list_jobs(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[CronJob]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLUMNS} FROM profile_cron_jobs"
            " WHERE profile_id = $1 ORDER BY created_at DESC",
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
            "INSERT INTO profile_cron_jobs (profile_id, name, schedule, prompt)"
            f" VALUES ($1, $2, $3, $4) RETURNING {_COLUMNS}",
            caller.profile_id, body.name, body.schedule, body.prompt,
        )
    return _view(row)


@router.put("/{job_id}", response_model=CronJob)
async def update_job(
    job_id: UUID, body: CronUpdate, request: Request,
    caller: Caller = Depends(require_caller),
) -> CronJob:
    pool = _pool(request)
    async with pool.acquire() as conn:
        # COALESCE keeps the stored value for absent fields; the profile_id
        # predicate makes another profile's job indistinguishable from a
        # missing one.
        row = await conn.fetchrow(
            "UPDATE profile_cron_jobs SET"
            " name = COALESCE($3, name), schedule = COALESCE($4, schedule),"
            " prompt = COALESCE($5, prompt), enabled = COALESCE($6, enabled)"
            f" WHERE id = $1 AND profile_id = $2 RETURNING {_COLUMNS}",
            job_id, caller.profile_id,
            body.name, body.schedule, body.prompt, body.enabled,
        )
    if row is None:
        raise _not_found()
    return _view(row)


@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID, request: Request, caller: Caller = Depends(require_caller)
) -> dict:
    pool = _pool(request)
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM profile_cron_jobs"
            " WHERE id = $1 AND profile_id = $2 RETURNING id",
            job_id, caller.profile_id,
        )
    if deleted is None:
        raise _not_found()
    return {"deleted": True}


@router.post("/{job_id}/run")
async def run_job_now(
    job_id: UUID, request: Request, caller: Caller = Depends(require_caller)
) -> dict:
    """Fire once now, through the exact runner the scheduler uses, so a manual
    fire can never behave differently from a scheduled one."""
    pool = _pool(request)
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No agent runtime is configured.",
        )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, profile_id, prompt FROM profile_cron_jobs"
            " WHERE id = $1 AND profile_id = $2",
            job_id, caller.profile_id,
        )
    if row is None:
        raise _not_found()
    job_status, summary = await run_job(
        pool, runtime,
        job_id=job_id, profile_id=caller.profile_id,
        prompt=row["prompt"], actor=caller,
    )
    return {"status": job_status, "summary": summary}

"""Sentry Gateway application.

Owns identity, authorization, durable work orders, append-only audit, artifacts,
connector grants, notification policy, and the client APIs. Talks to an agent
runtime only through the AgentRuntime adapter.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from fastapi import FastAPI, Response, status

from .agent_runtime.base import AgentRuntime
from .agent_runtime.hermes import HermesInstance, HermesRuntime
from .config import Settings, get_settings


def build_runtime(settings: Settings) -> AgentRuntime:
    """Runtime selection is configuration, not a code path baked into clients."""
    if settings.runtime_name == "hermes":
        runtime = HermesRuntime(pinned_version=settings.hermes_pinned_version)
        # Hermes binds one process per profile, so instances are registered as
        # profiles are provisioned. The bootstrap profile is registered here.
        if settings.hermes_bootstrap_profile_id and settings.hermes_api_key:
            runtime.register(
                HermesInstance(
                    profile_id=UUID(settings.hermes_bootstrap_profile_id),
                    base_url=settings.hermes_base_url,
                    api_key=settings.hermes_api_key,
                    profile_name=settings.hermes_bootstrap_profile_name,
                )
            )
        return runtime
    raise ValueError(f"Unknown runtime {settings.runtime_name!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.runtime = build_runtime(settings)
    try:
        app.state.pool = await asyncpg.create_pool(
            settings.database_url, min_size=1, max_size=8
        )
    except Exception:
        # Startup must not crash-loop on a database blip; readiness reports it.
        app.state.pool = None
    try:
        yield
    finally:
        if app.state.pool is not None:
            await app.state.pool.close()
        aclose = getattr(app.state.runtime, "aclose", None)
        if aclose is not None:
            await aclose()


app = FastAPI(
    title="Sentry Gateway",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health/live")
async def live() -> dict[str, str]:
    """Liveness only proves the process is up; it must not touch dependencies."""
    return {"status": "live"}


@app.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    """Readiness fails if PostgreSQL, the runtime, or the signing key is unavailable.

    The payload names the runtime, its pinned version, and any degradation, but
    never exposes secrets or connection strings.
    """
    settings: Settings = app.state.settings
    checks: dict[str, Any] = {}

    checks["signingKey"] = "ok" if settings.has_signing_key else "missing"

    pool: asyncpg.Pool | None = getattr(app.state, "pool", None)
    if pool is None:
        checks["database"] = "unavailable"
    else:
        try:
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {type(exc).__name__}"

    # Probe the bootstrap profile so readiness reflects a real registered instance
    # rather than a synthetic one that would always report "unknown profile".
    probe_profile = (
        UUID(settings.hermes_bootstrap_profile_id)
        if settings.hermes_bootstrap_profile_id
        else UUID(int=0)
    )
    capabilities = await app.state.runtime.capabilities(probe_profile)
    checks["runtime"] = {
        "name": capabilities.runtime_name,
        "pinnedVersion": capabilities.pinned_version,
        "healthy": capabilities.is_healthy,
        "degradedReason": capabilities.degraded_reason,
        "supportsCancellation": capabilities.supports_cancellation,
        "supportsSessionSearch": capabilities.supports_session_search,
    }

    ok = (
        checks["signingKey"] == "ok"
        and checks["database"] == "ok"
        and capabilities.is_healthy
    )
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if ok else "degraded", "checks": checks}


@app.get("/health/runtime")
async def runtime_info() -> dict[str, Any]:
    capabilities = await app.state.runtime.capabilities(uuid4())
    return {
        "runtime": capabilities.runtime_name,
        "pinnedVersion": capabilities.pinned_version,
        "healthy": capabilities.is_healthy,
        "degradedReason": capabilities.degraded_reason,
    }

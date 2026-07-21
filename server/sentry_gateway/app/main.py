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
from .agent_runtime.endpoints import (
    EndpointCipher,
    refresh_profile_endpoint,
    register_persisted_endpoints,
)
from .agent_runtime.hermes import HermesInstance, HermesRuntime
from .auth.tokens import TokenService
from .config import Settings, get_settings
from .routes import admin as admin_routes
from .routes import agent_messages as agent_messages_routes
from .routes import auth as auth_routes
from .routes import chat as chat_routes
from .routes import cron as cron_routes
from .routes import kanban as kanban_routes
from .routes import memory as memory_routes
from .routes import nodes as nodes_routes
from .routes import profiles as profiles_routes
from .routes import skills as skills_routes
from .routes import teams as teams_routes
from .routes import workorders as workorders_routes


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

    # Without a signing key there is no token verification, and every
    # authenticated route fails closed rather than allowing anonymous access.
    app.state.tokens = (
        TokenService(settings.signing_key) if settings.has_signing_key else None
    )
    try:
        app.state.pool = await asyncpg.create_pool(
            settings.database_url, min_size=1, max_size=8
        )
    except Exception:
        # Startup must not crash-loop on a database blip; readiness reports it.
        app.state.pool = None

    # Revocation is persisted, so rehydrate it. Without this a gateway restart
    # would silently un-revoke every device whose token had not yet expired.
    if app.state.pool is not None and app.state.tokens is not None:
        try:
            async with app.state.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id FROM devices WHERE revoked_at IS NOT NULL"
                )
            app.state.tokens.revoked_devices.update(str(r["id"]) for r in rows)
        except Exception:
            # Fail closed: without the revocation list we cannot verify tokens
            # safely, so drop the service and let routes return 503.
            app.state.tokens = None

    # Register any persisted per-profile Hermes endpoints on top of the bootstrap
    # profile, so a provisioned teammate is routable without an env change and
    # restart. This runs after the pool exists. Failing here only leaves those
    # profiles unroutable (routing fails closed at turn time), so it must never
    # crash startup.
    runtime = app.state.runtime
    app.state.endpoint_cipher = None
    if (
        app.state.pool is not None
        and settings.has_runtime_enc_key
        and hasattr(runtime, "register")
    ):
        try:
            cipher = EndpointCipher(settings.runtime_enc_key)
            # Kept on app.state so a profile provisioned AFTER startup can have
            # its endpoint loaded on demand at turn time, instead of needing a
            # Gateway restart that is easy to forget (`compose up -d` no-ops).
            app.state.endpoint_cipher = cipher
            await register_persisted_endpoints(runtime, app.state.pool, cipher)

            # Let the runtime reload a profile's endpoint mid-turn. Provisioning
            # rotates a teammate's container key; without this the Gateway keeps
            # the key it read at boot and every turn is rejected until restart.
            _pool, _cipher = app.state.pool, cipher

            async def _refresh_endpoint(profile_id) -> bool:
                return await refresh_profile_endpoint(runtime, _pool, _cipher, profile_id)

            runtime.endpoint_refresher = _refresh_endpoint
        except Exception:
            # A bad key or malformed row must not take the whole Gateway down;
            # the affected profiles simply fail closed when a turn is attempted.
            pass

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


app.include_router(admin_routes.router)
app.include_router(auth_routes.router)
app.include_router(chat_routes.router)
app.include_router(profiles_routes.router)
app.include_router(kanban_routes.router)
app.include_router(skills_routes.router)
app.include_router(memory_routes.router)
app.include_router(agent_messages_routes.router)
app.include_router(cron_routes.router)
app.include_router(nodes_routes.router)
app.include_router(teams_routes.router)
app.include_router(workorders_routes.router)


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



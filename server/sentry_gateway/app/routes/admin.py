"""Admin surface for the runtime.

Read-mostly by design. This exposes enough to answer "is the runtime healthy and
why not", without becoming a remote console: there is no endpoint here that
executes anything, edits configuration, or returns a secret.

Diagnostics are phrased as findings with a remedy, because the common case is a
runtime that is up but cannot answer, and a bare "degraded" does not tell an
admin what to do about it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .deps import Caller, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


class Finding(BaseModel):
    """Something an admin should know, with what to do about it."""

    severity: str = Field(pattern="^(info|warning|critical)$")
    summary: str
    remedy: str = ""


class RuntimeCapabilityView(BaseModel):
    sessions: bool
    session_search: bool
    work_board: bool
    cancellation: bool
    delegation: bool


class HermesAdminSnapshot(BaseModel):
    runtime_name: str
    pinned_version: str
    healthy: bool
    degraded_reason: str | None
    #: False when the runtime is reachable but has no model behind it.
    can_answer: bool
    capabilities: RuntimeCapabilityView
    profiles_registered: int
    findings: list[Finding]


def _probe_profile(request: Request) -> UUID:
    settings = request.app.state.settings
    return (
        UUID(settings.hermes_bootstrap_profile_id)
        if settings.hermes_bootstrap_profile_id
        else UUID(int=0)
    )


@router.get("/hermes", response_model=HermesAdminSnapshot)
async def hermes_snapshot(
    request: Request, caller: Caller = Depends(require_admin)
) -> HermesAdminSnapshot:
    runtime = request.app.state.runtime
    capabilities = await runtime.capabilities(_probe_profile(request))

    findings: list[Finding] = []

    if not capabilities.is_healthy:
        findings.append(
            Finding(
                severity="critical",
                summary=capabilities.degraded_reason or "Runtime is degraded.",
                remedy="Check `docker compose logs hermes` on the control plane.",
            )
        )

    # A runtime that is up but has no model is the case an admin most needs
    # spelled out: everything looks green except the part that matters.
    can_answer = await _can_answer(runtime, _probe_profile(request))
    if capabilities.is_healthy and not can_answer:
        findings.append(
            Finding(
                severity="critical",
                summary="Runtime is reachable but has no inference provider, so it cannot answer.",
                remedy=(
                    "Add OPENAI_API_KEY, OPENROUTER_API_KEY, or ANTHROPIC_API_KEY to "
                    "deploy/linux/.env on the control plane, then restart the runtime."
                ),
            )
        )

    if capabilities.pinned_version in ("", "unpinned"):
        findings.append(
            Finding(
                severity="warning",
                summary="Runtime version is not pinned.",
                remedy="Set HERMES_VERSION in deploy/linux/.env so upgrades are deliberate.",
            )
        )

    if not findings:
        findings.append(
            Finding(severity="info", summary="Runtime is healthy and able to answer.")
        )

    # Adapters may expose this as a property or not at all, so read it defensively
    # rather than assuming Hermes' shape.
    registered = getattr(runtime, "registered_profile_count", 0)
    profile_count = registered if isinstance(registered, int) else 0

    return HermesAdminSnapshot(
        runtime_name=capabilities.runtime_name,
        pinned_version=capabilities.pinned_version,
        healthy=capabilities.is_healthy,
        degraded_reason=capabilities.degraded_reason,
        can_answer=can_answer,
        capabilities=RuntimeCapabilityView(
            sessions=capabilities.supports_sessions,
            session_search=capabilities.supports_session_search,
            work_board=capabilities.supports_work_board,
            cancellation=capabilities.supports_cancellation,
            delegation=capabilities.supports_delegation,
        ),
        profiles_registered=profile_count,
        findings=findings,
    )


async def _can_answer(runtime: Any, profile_id: UUID) -> bool:
    """Whether the runtime has a model behind it.

    Deliberately cheap and side-effect free: it asks the adapter, which checks
    without spending a token on a real completion.
    """
    probe = getattr(runtime, "has_inference_provider", None)
    if probe is None:
        return False
    try:
        return await probe(profile_id)
    except Exception:
        return False

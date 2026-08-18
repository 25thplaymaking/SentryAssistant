"""The agent's own credential and pending-write surfaces.

Two things live inside Hermes that the WebUI could never reach, because Hermes
keeps them in its CLI and the WebUI container never loads the agent:

* **Credential OAuth** — logging a Claude Pro/Max subscription into the harness
  required a TTY on the server.
* **Pending agent writes** — with ``memory.write_approval: true`` the agent
  stages memory and skill writes instead of applying them. Nothing could list or
  approve them, so proposals accumulated on disk permanently.

Both are served by the admin surface patched into Hermes' API server
(``deploy/linux/hermes/sentry_admin.py``); this module is the profile-scoped
proxy in front of it.

**This is NOT ``/api/memory``.** That router serves Gateway-owned per-user memory
stored in Postgres. The memory here is the *agent's* own memory inside Hermes —
a separate store with a separate lifecycle. They are easy to confuse and are not
interchangeable.

Every route is scoped to ``caller.profile_id``, so one profile can never read or
approve another's staged writes, nor enumerate another's credentials.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..agent_runtime.hermes import AdminSurfaceUnavailable, UnknownProfileError
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/agent", tags=["agent-admin"])

# Mirrors tools.write_approval._SUBSYSTEMS. Validated here as well as in Hermes
# so a typo is a 404 from the Gateway rather than a proxied round trip.
_SUBSYSTEMS = frozenset({"memory", "skills"})


def _runtime(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No agent runtime is configured.",
        )
    return runtime


def _require_subsystem(subsystem: str) -> str:
    if subsystem not in _SUBSYSTEMS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown subsystem '{subsystem}'.",
        )
    return subsystem


async def _call(coro) -> Any:
    """Run an admin-surface call, mapping runtime errors onto HTTP status.

    ``AdminSurfaceUnavailable`` already carries the status the caller should
    see — in particular 501, which means the Hermes image predates the admin
    patch and must be rebuilt. Flattening that into a generic 502 is what makes
    a missing surface look like a transient outage.
    """
    try:
        return await coro
    except UnknownProfileError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No agent runtime is provisioned for this profile.",
        )
    except AdminSurfaceUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


def _bound(runtime, method: str):
    """Return a runtime method, or 501 if this runtime has no admin surface.

    Checked BEFORE the call is built rather than caught around it: the missing
    attribute would otherwise raise while evaluating the argument to the error
    handler, escaping as an opaque 500.
    """
    fn = getattr(runtime, method, None)
    if fn is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="This agent runtime does not expose an admin surface.",
        )
    return fn


# ---------------------------------------------------------------------------
# Pending agent writes
# ---------------------------------------------------------------------------


@router.get("/pending/{subsystem}")
async def list_pending(
    subsystem: str, request: Request, caller: Caller = Depends(require_caller)
) -> dict:
    """Staged agent writes awaiting a decision, for this caller's profile."""
    _require_subsystem(subsystem)
    runtime = _runtime(request)
    fn = _bound(runtime, "list_pending_writes")
    return await _call(fn(caller.profile_id, subsystem))


@router.post("/pending/{subsystem}/{pending_id}/approve")
async def approve_pending(
    subsystem: str,
    pending_id: str,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict:
    """Apply a staged write. The record stays staged if the write fails."""
    _require_subsystem(subsystem)
    runtime = _runtime(request)
    fn = _bound(runtime, "decide_pending_write")
    return await _call(fn(caller.profile_id, subsystem, pending_id, approve=True))


@router.post("/pending/{subsystem}/{pending_id}/reject")
async def reject_pending(
    subsystem: str,
    pending_id: str,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict:
    """Discard a staged write without applying it."""
    _require_subsystem(subsystem)
    runtime = _runtime(request)
    fn = _bound(runtime, "decide_pending_write")
    return await _call(fn(caller.profile_id, subsystem, pending_id, approve=False))


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class OAuthStart(BaseModel):
    provider: str = Field(min_length=1, max_length=64)


class OAuthComplete(BaseModel):
    flow_id: str = Field(min_length=1, max_length=128)
    # The value Anthropic shows on the callback page is "<code>#<state>". The
    # state half is the CSRF binding and must be sent back intact, so this
    # accepts the whole pasted string rather than trying to split it here.
    code: str = Field(min_length=1, max_length=4096)
    label: str | None = Field(default=None, max_length=128)


@router.get("/auth/providers")
async def list_providers(
    request: Request, caller: Caller = Depends(require_caller)
) -> dict:
    """Credential providers and which of them this profile is logged into.

    ``oauth_over_http`` is the field the UI should gate its login button on:
    a provider can be OAuth-capable in Hermes and still have no browser flow,
    in which case it is CLI-only and the button would lead nowhere.
    """
    runtime = _runtime(request)
    fn = _bound(runtime, "list_auth_providers")
    return await _call(fn(caller.profile_id))


@router.post("/auth/oauth/start")
async def start_oauth(
    request: Request,
    body: OAuthStart = Body(...),
    caller: Caller = Depends(require_caller),
) -> dict:
    """Begin a browser OAuth login and return the URL to open."""
    runtime = _runtime(request)
    fn = _bound(runtime, "start_oauth")
    return await _call(fn(caller.profile_id, body.provider))


@router.post("/auth/oauth/complete")
async def complete_oauth(
    request: Request,
    body: OAuthComplete = Body(...),
    caller: Caller = Depends(require_caller),
) -> dict:
    """Exchange the pasted authorization code and store the credential.

    The token never transits this process in a form worth logging: it is
    exchanged inside Hermes and written to Hermes' credential pool. The response
    carries only the credential's id and display label.
    """
    runtime = _runtime(request)
    fn = _bound(runtime, "complete_oauth")
    return await _call(fn(caller.profile_id, body.flow_id, body.code, body.label))


@router.delete("/auth/providers/{provider}")
async def logout_provider(
    provider: str,
    request: Request,
    credential: str | None = Query(
        default=None,
        max_length=128,
        description="Credential id, unique label, or 1-based index. Omit to remove all.",
    ),
    caller: Caller = Depends(require_caller),
) -> dict:
    """Remove stored credentials for a provider."""
    runtime = _runtime(request)
    fn = _bound(runtime, "logout_provider")
    return await _call(fn(caller.profile_id, provider, credential))

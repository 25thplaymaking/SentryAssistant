"""Sentry admin HTTP surface for the Hermes API server.

Hermes exposes a rich CLI (``hermes auth ...``, ``/memory approve ...``) and a
deliberately narrow HTTP API. Sentry is a split-container deployment: the WebUI
never loads the agent, so anything the CLI alone can do is unreachable from the
browser. This module adds the missing HTTP surface for two things the CLI keeps
to itself:

* **Credential OAuth** — ``hermes auth add anthropic --type oauth`` runs a PKCE
  flow that ``input()``s the authorization code, so it needs a TTY. Split into
  ``start`` / ``complete`` it becomes a browser flow.
* **Pending write approvals** — memory/skill writes staged by
  ``tools.write_approval`` are only listable and approvable through the slash
  commands, so with ``memory.write_approval: true`` proposals accumulate on disk
  with no way to ever approve them.

Nothing here reimplements protocol details. The OAuth endpoints reuse
``agent.anthropic_adapter``'s own client id, endpoints, scopes, redirect URI and
PKCE generator, and persist through the same ``PooledCredential``/``load_pool``
path ``hermes_cli.auth_commands`` uses — so a Hermes upgrade that rotates an
endpoint carries through here without an edit. Where an upstream symbol is
missing we fail loudly rather than substituting a guessed constant.

Handlers are plain coroutines closed over the adapter, registered by
``patches/api_server_admin_surface.py``. Every one calls the adapter's own
``_check_auth`` first: the API server authenticates per-handler, not in
middleware, so a handler that skips it is unauthenticated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web

logger = logging.getLogger(__name__)

# Pending subsystems are whatever tools.write_approval defines; the tuple is
# read at call time rather than copied, so adding a subsystem upstream needs no
# change here.
_MAX_BODY_BYTES = 64 * 1024

# ---------------------------------------------------------------------------
# In-flight OAuth flows
# ---------------------------------------------------------------------------
# A PKCE flow spans two requests, so the code verifier has to outlive the first
# one. It is deliberately kept in memory only: the verifier is a bearer secret
# for the token exchange, and writing it to disk would leave a credential-
# equivalent artifact behind after a crash. Losing them on restart just means
# restarting the login.
_FLOW_TTL_SECONDS = 15 * 60
_MAX_FLOWS = 16
_flows: Dict[str, Dict[str, Any]] = {}


def _sweep_flows(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    for flow_id, flow in list(_flows.items()):
        if now - float(flow.get("created_at", 0)) > _FLOW_TTL_SECONDS:
            _flows.pop(flow_id, None)
    # Bound memory even if every flow is young: an authenticated caller can
    # still start flows in a loop.
    while len(_flows) > _MAX_FLOWS:
        oldest = min(_flows, key=lambda k: _flows[k].get("created_at", 0))
        _flows.pop(oldest, None)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _err(status: int, message: str, **extra: Any) -> web.Response:
    body = {"error": {"message": message, "type": "sentry_admin_error"}}
    body["error"].update(extra)
    return web.json_response(body, status=status)


async def _read_json(request: "web.Request") -> Tuple[Optional[Dict[str, Any]], Optional[web.Response]]:
    """Parse a JSON object body, or return an error response."""
    raw = await request.content.read(_MAX_BODY_BYTES + 1)
    if len(raw) > _MAX_BODY_BYTES:
        return None, _err(413, "Request body too large.")
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, _err(400, f"Invalid JSON body: {exc}")
    if not isinstance(parsed, dict):
        return None, _err(400, "Request body must be a JSON object.")
    return parsed, None


def _anthropic_oauth_module():
    """Return the anthropic adapter, asserting the symbols we borrow exist.

    Fails loudly and specifically: if an upgrade renames one of these, the
    correct response is to re-verify the flow against the new version, not to
    fall back to a stale copy of Anthropic's endpoints.
    """
    from agent import anthropic_adapter as mod

    required = (
        "_OAUTH_CLIENT_ID",
        "_OAUTH_TOKEN_URLS",
        "_OAUTH_REDIRECT_URI",
        "_OAUTH_SCOPES",
        "_OAUTH_TOKEN_USER_AGENT",
        "_generate_pkce",
    )
    missing = [name for name in required if not hasattr(mod, name)]
    if missing:
        raise RuntimeError(
            "agent.anthropic_adapter is missing "
            + ", ".join(missing)
            + " — Hermes has changed its OAuth internals. Re-verify the PKCE "
            "flow against the new version instead of using cached constants."
        )
    return mod


def _exchange_anthropic_code(code: str, state: str, verifier: str) -> Dict[str, Any]:
    """Blocking token exchange, mirroring anthropic_adapter's own request."""
    import urllib.request

    mod = _anthropic_oauth_module()
    payload = json.dumps(
        {
            "grant_type": "authorization_code",
            "client_id": mod._OAUTH_CLIENT_ID,
            "code": code,
            "state": state,
            "redirect_uri": mod._OAUTH_REDIRECT_URI,
            "code_verifier": verifier,
        }
    ).encode()

    last_error: Optional[Exception] = None
    for endpoint in mod._OAUTH_TOKEN_URLS:
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                # Not the claude-code/ UA: the token endpoint 429s that prefix.
                # See the constant's definition in anthropic_adapter.
                "User-Agent": mod._OAUTH_TOKEN_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001 — try the next host
            last_error = exc
            logger.debug("Anthropic token exchange failed at %s: %s", endpoint, exc)
    raise last_error if last_error is not None else RuntimeError("Token exchange failed")


def _pool_status(provider: str) -> Dict[str, Any]:
    """Structural view of a provider's credential pool.

    Structural on purpose: ``hermes auth status`` renders prose whose
    logged-OUT text contains both "credentials" and "authenticate", so keyword
    matching on it reports the opposite of the truth.
    """
    from agent.credential_pool import load_pool

    try:
        entries = list(load_pool(provider).entries())
    except Exception:
        logger.debug("credential pool unreadable for %s", provider, exc_info=True)
        return {"authenticated": False, "credentials": [], "unreadable": True}

    creds = []
    for entry in entries:
        expires_at_ms = getattr(entry, "expires_at_ms", None)
        creds.append(
            {
                "id": getattr(entry, "id", None),
                "label": getattr(entry, "label", None),
                "auth_type": getattr(entry, "auth_type", None),
                "source": getattr(entry, "source", None),
                "last_status": getattr(entry, "last_status", None),
                "expires_at_ms": expires_at_ms,
            }
        )
    return {"authenticated": bool(creds), "credentials": creds}


# ---------------------------------------------------------------------------
# Handlers — auth
# ---------------------------------------------------------------------------

def _build_auth_handlers(adapter) -> List[tuple]:

    async def list_providers(request: "web.Request") -> "web.Response":
        """GET /v1/auth/providers — which providers exist and which are logged in."""
        auth_err = adapter._check_auth(request)
        if auth_err:
            return auth_err

        try:
            from hermes_cli.auth import PROVIDER_REGISTRY
            from hermes_cli.auth_commands import _OAUTH_CAPABLE_PROVIDERS
        except Exception as exc:  # noqa: BLE001
            return _err(503, f"Hermes auth registry unavailable: {exc}")

        out = []
        for provider in sorted(PROVIDER_REGISTRY):
            status = _pool_status(provider)
            status["id"] = provider
            status["oauth_capable"] = provider in _OAUTH_CAPABLE_PROVIDERS
            # Only anthropic's flow is implemented over HTTP so far; the rest
            # are device-code flows with a different shape. Say so per-provider
            # rather than letting the UI offer a button that cannot work.
            status["oauth_over_http"] = provider in _HTTP_OAUTH_PROVIDERS
            out.append(status)
        return web.json_response({"object": "list", "data": out})

    async def start_oauth(request: "web.Request") -> "web.Response":
        """POST /v1/auth/oauth/start — begin a PKCE login, return the authorize URL."""
        auth_err = adapter._check_auth(request)
        if auth_err:
            return auth_err

        body, err = await _read_json(request)
        if err:
            return err
        provider = str((body or {}).get("provider", "") or "").strip().lower()
        if not provider:
            return _err(400, "Field 'provider' is required.")
        if provider not in _HTTP_OAUTH_PROVIDERS:
            return _err(
                501,
                f"Provider '{provider}' has no browser OAuth flow in Sentry yet. "
                f"Log it in on the server with: "
                f"hermes auth add {provider} --type oauth",
                provider=provider,
                cli_command=f"hermes auth add {provider} --type oauth",
            )

        try:
            mod = _anthropic_oauth_module()
        except Exception as exc:  # noqa: BLE001
            return _err(503, str(exc))

        verifier, challenge = mod._generate_pkce()
        state = secrets.token_urlsafe(32)
        flow_id = uuid.uuid4().hex

        from urllib.parse import urlencode

        params = {
            "code": "true",
            "client_id": mod._OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": mod._OAUTH_REDIRECT_URI,
            "scope": mod._OAUTH_SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        authorize_url = f"https://claude.ai/oauth/authorize?{urlencode(params)}"

        _sweep_flows()
        _flows[flow_id] = {
            "provider": provider,
            "verifier": verifier,
            "state": state,
            "created_at": time.time(),
        }

        return web.json_response(
            {
                "flow_id": flow_id,
                "provider": provider,
                "authorize_url": authorize_url,
                "expires_in": _FLOW_TTL_SECONDS,
                # Anthropic shows "<code>#<state>" on the callback page. The
                # state half is the CSRF binding, so the whole string must come
                # back — a bare code is rejected, exactly as the CLI rejects it.
                "instructions": (
                    "Open authorize_url, approve, then paste the full code "
                    "shown on the callback page (it looks like <code>#<state>)."
                ),
            }
        )

    async def complete_oauth(request: "web.Request") -> "web.Response":
        """POST /v1/auth/oauth/complete — exchange the pasted code for tokens."""
        auth_err = adapter._check_auth(request)
        if auth_err:
            return auth_err

        body, err = await _read_json(request)
        if err:
            return err
        body = body or {}
        flow_id = str(body.get("flow_id", "") or "").strip()
        pasted = str(body.get("code", "") or "").strip()
        if not flow_id or not pasted:
            return _err(400, "Fields 'flow_id' and 'code' are required.")

        _sweep_flows()
        flow = _flows.get(flow_id)
        if not flow:
            return _err(404, "Unknown or expired OAuth flow. Start a new login.")

        splits = pasted.split("#")
        code = splits[0].strip()
        received_state = splits[1].strip() if len(splits) > 1 else ""

        # RFC 6749 §10.12. Upstream aborts on mismatch and so do we; a bare code
        # (no "#state") therefore fails closed rather than downgrading CSRF
        # protection for convenience.
        if not secrets.compare_digest(received_state, flow["state"]):
            _flows.pop(flow_id, None)
            return _err(
                400,
                "Authorization state mismatch. Paste the ENTIRE value shown on "
                "the callback page, including the part after '#'.",
            )
        if not code:
            return _err(400, "No authorization code supplied.")

        _flows.pop(flow_id, None)
        provider = flow["provider"]

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, _exchange_anthropic_code, code, received_state, flow["verifier"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OAuth token exchange failed for %s: %s", provider, exc)
            return _err(502, f"Token exchange failed: {exc}")

        access_token = result.get("access_token", "")
        if not access_token:
            return _err(502, "Provider returned no access token.")

        try:
            persisted = _persist_oauth_credential(
                provider,
                access_token=access_token,
                refresh_token=result.get("refresh_token", ""),
                expires_in=result.get("expires_in", 3600),
                label=str(body.get("label", "") or "").strip() or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist %s credential: %s", provider, exc)
            return _err(500, f"Token obtained but could not be stored: {exc}")

        return web.json_response({"provider": provider, "credential": persisted})

    async def logout(request: "web.Request") -> "web.Response":
        """DELETE /v1/auth/providers/{provider} — drop stored credentials.

        Removes every credential for the provider, or just one when
        ``?credential=<id|label|index>`` is given.
        """
        auth_err = adapter._check_auth(request)
        if auth_err:
            return auth_err

        provider = request.match_info.get("provider", "").strip().lower()
        if not provider:
            return _err(400, "Provider is required.")
        try:
            from hermes_cli.auth import PROVIDER_REGISTRY
        except Exception as exc:  # noqa: BLE001
            return _err(503, f"Hermes auth registry unavailable: {exc}")
        if provider not in PROVIDER_REGISTRY:
            return _err(404, f"Unknown provider '{provider}'.")

        target = (request.query.get("credential") or "").strip()
        try:
            from agent.credential_pool import load_pool

            pool = load_pool(provider)
            if target:
                # resolve_target accepts an entry id, a unique label, or a
                # 1-based index, and reports ambiguity rather than guessing.
                index, _entry, err_msg = pool.resolve_target(target)
                if index is None:
                    return _err(404, err_msg or f'No credential matching "{target}".')
                removed = 1 if pool.remove_index(index) is not None else 0
            else:
                # remove_index is 1-based and renumbers the remaining entries,
                # so repeatedly removing the first one drains the pool.
                removed = 0
                while pool.remove_index(1) is not None:
                    removed += 1
        except Exception as exc:  # noqa: BLE001
            return _err(500, f"Could not clear credentials: {exc}")

        return web.json_response({"provider": provider, "removed": removed})

    return [
        ("GET", "/v1/auth/providers", list_providers),
        ("POST", "/v1/auth/oauth/start", start_oauth),
        ("POST", "/v1/auth/oauth/complete", complete_oauth),
        ("DELETE", "/v1/auth/providers/{provider}", logout),
    ]


# Providers whose OAuth flow this module can drive over HTTP. Anthropic is a
# redirect-and-paste PKCE flow, which splits cleanly into two requests. The
# other OAuth-capable providers are device-code flows that poll a token endpoint
# from inside a blocking helper; wiring those means a third endpoint shape, so
# they stay CLI-only rather than half-supported.
_HTTP_OAUTH_PROVIDERS = frozenset({"anthropic"})


def _persist_oauth_credential(
    provider: str,
    *,
    access_token: str,
    refresh_token: str,
    expires_in: Any,
    label: Optional[str],
) -> Dict[str, Any]:
    """Store an OAuth credential exactly as ``hermes auth add`` would."""
    from agent.credential_pool import (
        AUTH_TYPE_OAUTH,
        SOURCE_MANUAL,
        PooledCredential,
        label_from_token,
        load_pool,
    )
    from hermes_cli.auth_commands import _oauth_default_label, _provider_base_url

    try:
        expires_at_ms = int(time.time() * 1000) + int(expires_in) * 1000
    except (TypeError, ValueError):
        expires_at_ms = int(time.time() * 1000) + 3600 * 1000

    pool = load_pool(provider)
    resolved_label = label or label_from_token(
        access_token, _oauth_default_label(provider, len(pool.entries()) + 1)
    )
    entry = PooledCredential(
        provider=provider,
        id=uuid.uuid4().hex[:6],
        label=resolved_label,
        auth_type=AUTH_TYPE_OAUTH,
        priority=0,
        # Same marker the CLI's PKCE path writes, so pool bookkeeping that keys
        # on the source string treats browser and CLI logins identically.
        source=f"{SOURCE_MANUAL}:hermes_pkce",
        access_token=access_token,
        refresh_token=refresh_token or None,
        expires_at_ms=expires_at_ms,
        base_url=_provider_base_url(provider),
    )
    pool.add_entry(entry)
    return {"id": entry.id, "label": entry.label, "auth_type": AUTH_TYPE_OAUTH}


# ---------------------------------------------------------------------------
# Handlers — pending write approvals
# ---------------------------------------------------------------------------

def _build_pending_handlers(adapter) -> List[tuple]:

    def _valid_subsystem(name: str) -> bool:
        from tools import write_approval as wa

        return name in set(wa._SUBSYSTEMS)

    async def list_pending(request: "web.Request") -> "web.Response":
        """GET /v1/pending/{subsystem} — staged writes awaiting a decision."""
        auth_err = adapter._check_auth(request)
        if auth_err:
            return auth_err

        subsystem = request.match_info.get("subsystem", "").strip().lower()
        try:
            from tools import write_approval as wa
        except Exception as exc:  # noqa: BLE001
            return _err(503, f"Write-approval subsystem unavailable: {exc}")
        if not _valid_subsystem(subsystem):
            return _err(404, f"Unknown subsystem '{subsystem}'.")

        try:
            records = wa.list_pending(subsystem)
            enabled = wa.write_approval_enabled(subsystem)
        except Exception as exc:  # noqa: BLE001
            return _err(500, f"Could not read pending writes: {exc}")

        return web.json_response(
            {
                "object": "list",
                "subsystem": subsystem,
                "approval_required": bool(enabled),
                "data": records,
            }
        )

    async def approve_pending(request: "web.Request") -> "web.Response":
        """POST /v1/pending/{subsystem}/{pending_id}/approve — apply the write."""
        auth_err = adapter._check_auth(request)
        if auth_err:
            return auth_err

        subsystem = request.match_info.get("subsystem", "").strip().lower()
        pending_id = request.match_info.get("pending_id", "").strip()
        try:
            from tools import write_approval as wa
        except Exception as exc:  # noqa: BLE001
            return _err(503, f"Write-approval subsystem unavailable: {exc}")
        if not _valid_subsystem(subsystem):
            return _err(404, f"Unknown subsystem '{subsystem}'.")

        record = wa.get_pending(subsystem, pending_id)
        if not record:
            return _err(404, f"No pending {subsystem} write with id '{pending_id}'.")

        loop = asyncio.get_running_loop()
        try:
            ok, message = await loop.run_in_executor(None, _apply_pending, subsystem, record)
        except Exception as exc:  # noqa: BLE001
            return _err(500, f"Apply failed: {exc}")

        if not ok:
            # Leave the record staged: a failed apply that also deleted the
            # proposal would lose it with nothing written.
            return _err(500, message or "Apply failed.", pending_id=pending_id)

        wa.discard_pending(subsystem, pending_id)
        return web.json_response(
            {"subsystem": subsystem, "id": pending_id, "status": "approved"}
        )

    async def reject_pending(request: "web.Request") -> "web.Response":
        """POST /v1/pending/{subsystem}/{pending_id}/reject — discard the write."""
        auth_err = adapter._check_auth(request)
        if auth_err:
            return auth_err

        subsystem = request.match_info.get("subsystem", "").strip().lower()
        pending_id = request.match_info.get("pending_id", "").strip()
        try:
            from tools import write_approval as wa
        except Exception as exc:  # noqa: BLE001
            return _err(503, f"Write-approval subsystem unavailable: {exc}")
        if not _valid_subsystem(subsystem):
            return _err(404, f"Unknown subsystem '{subsystem}'.")

        if not wa.discard_pending(subsystem, pending_id):
            return _err(404, f"No pending {subsystem} write with id '{pending_id}'.")
        return web.json_response(
            {"subsystem": subsystem, "id": pending_id, "status": "rejected"}
        )

    return [
        ("GET", "/v1/pending/{subsystem}", list_pending),
        ("POST", "/v1/pending/{subsystem}/{pending_id}/approve", approve_pending),
        ("POST", "/v1/pending/{subsystem}/{pending_id}/reject", reject_pending),
    ]


def _apply_pending(subsystem: str, record: Dict[str, Any]) -> Tuple[bool, str]:
    """Replay a staged write. Mirrors write_approval_commands._apply_one."""
    from tools import write_approval as wa

    payload = record.get("payload", {}) or {}
    try:
        if subsystem == wa.MEMORY:
            from tools.memory_tool import apply_memory_pending, load_on_disk_store

            result = apply_memory_pending(payload, load_on_disk_store())
            return bool(result.get("success")), str(result.get("error", "") or "")
        from tools.skill_manager_tool import apply_skill_pending

        result = json.loads(apply_skill_pending(payload))
        return bool(result.get("success")), str(result.get("error", "") or "")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ---------------------------------------------------------------------------
# Entry point used by the build-time patch
# ---------------------------------------------------------------------------

def build_routes(adapter) -> List[tuple]:
    """Return ``(method, path, handler)`` rows for the Sentry admin surface."""
    routes = _build_auth_handlers(adapter) + _build_pending_handlers(adapter)
    logger.info("api_server: Sentry admin surface registered (%d routes)", len(routes))
    return routes

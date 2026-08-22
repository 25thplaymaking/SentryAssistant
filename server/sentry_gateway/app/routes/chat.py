"""Client chat turns — the one path a client uses to reach *their* agent.

The browser/desktop/phone never addresses a Hermes container directly. It sends a
turn here; the Gateway resolves the caller's profile from their token and forwards
to that profile's Hermes instance through the AgentRuntime adapter. Routing is
fail-closed: a profile with no registered runtime never falls back to another
profile's agent (that would run one person's work inside another's).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent_runtime.base import (
    RuntimeEvent,
    RuntimeExperience,
    RuntimeTurn,
    SessionScope,
)
from ..agent_runtime.endpoints import refresh_profile_endpoint
from ..agent_runtime.hermes import UnknownProfileError
from ..actions.recorder import ActionRecorder
from ..audit.service import AuditEvent, AuditService, Decision
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatTurnRequest(BaseModel):
    prompt: str = Field(min_length=1)
    #: Continue an existing conversation. Absent on the first turn.
    session_id: str | None = None
    #: Untrusted connector/document text, passed to the runtime as quoted data.
    quoted_context: list[str] = Field(default_factory=list)
    #: Which advertised model should answer. Absent keeps the profile's own
    #: configured default. A value that this profile does not advertise is
    #: REFUSED (400) rather than quietly downgraded -- see _validated_model.
    model: str | None = None
    #: The product lane controls the request-scoped Hermes capability surface.
    #: Work is the compatibility default for older clients; the Sentry UI sends
    #: its selected lane explicitly on every turn.
    experience: RuntimeExperience = RuntimeExperience.WORK
    #: Named local workspace for a native workstation runtime.  It is an
    #: allowlisted identifier, never a client-supplied filesystem path.
    workspace_id: str | None = Field(default=None, min_length=1, max_length=200)


class NativeRuntimeResponse(BaseModel):
    work_order_id: UUID
    request_id: str = Field(min_length=1, max_length=300)
    response: dict = Field(default_factory=dict)


def _json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


_EXPERIENCES = {
    "default": RuntimeExperience.CHAT.value,
    "boundary_note": (
        "Most linked accounts provide models through Hermes. Choosing a linked "
        "Codex subscription model activates the native Codex runtime on your "
        "connected workstation."
    ),
    "items": [
        {
            "id": RuntimeExperience.CHAT.value,
            "label": "Chat",
            "summary": "Talk with Hermes as your everyday assistant.",
            "available": [
                "Web research",
                "Images and vision",
                "Voice input",
                "Memory and conversation search",
            ],
            "blocked": [
                "Workspace files",
                "Terminal and code execution",
                "Browser and computer control",
                "Plugins and workstation actions",
                "Delegation and scheduled execution",
            ],
        },
        {
            "id": RuntimeExperience.WORK.value,
            "label": "Work",
            "summary": "Give Hermes a workspace and let it execute multi-step work.",
            "available": [
                "Profile-approved Hermes skills and tools",
                "Workspace, terminal, and code execution",
                "Browser and computer control",
                "Plugins, MCP, and workstation actions",
                "Delegation and scheduled execution",
                "Native Codex threads, skills, apps, approvals, and sandboxing when a Codex subscription model is selected",
            ],
            "blocked": [],
        },
    ],
}


def _fallback_model_group(models: tuple[str, ...]) -> list[dict]:
    visible = [model for model in models if model != "hermes-agent"]
    if not visible:
        return []
    return [
        {
            "provider": "Sentry routes",
            "provider_id": "sentry",
            "models": [{"id": model, "label": model} for model in visible],
        }
    ]


async def _linked_model_groups(runtime, profile_id, models: tuple[str, ...]) -> list[dict]:
    """Group only authenticated provider routes that Hermes can actually reach.

    Provider login and model routing are separate facts. The intersection with
    ``available_models`` prevents a stale OAuth status from displaying a route
    Hermes no longer advertises, while omitting unauthenticated providers keeps
    disconnected subscriptions out of the picker entirely.
    """
    if not hasattr(runtime, "list_auth_providers"):
        return _fallback_model_group(models)
    try:
        payload = await runtime.list_auth_providers(profile_id)
    except Exception:
        return _fallback_model_group(models)

    advertised = set(models)
    claimed: set[str] = set()
    provider_owned: set[str] = set()
    groups: list[dict] = []
    providers = payload.get("data", []) if isinstance(payload, dict) else []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        routed = []
        for route in provider.get("models") or []:
            if not isinstance(route, dict):
                continue
            alias = str(route.get("id") or "").strip()
            if not alias or alias not in advertised:
                continue
            provider_owned.add(alias)
            if not provider.get("authenticated") or alias in claimed:
                continue
            target = str(route.get("model") or alias).strip()
            routed.append({"id": alias, "label": target, "source_model": target})
            claimed.add(alias)
        if routed:
            native_runtime = str(provider.get("native_runtime") or "").strip() or None
            if native_runtime:
                for model in routed:
                    model["native_runtime"] = native_runtime
                    model["experience"] = RuntimeExperience.WORK.value
            groups.append(
                {
                    "provider": str(provider.get("name") or provider.get("id") or "Provider"),
                    "provider_id": str(provider.get("id") or "provider"),
                    "models": routed,
                    **({"native_runtime": native_runtime} if native_runtime else {}),
                }
            )

    unclaimed = [
        model
        for model in models
        if model != "hermes-agent" and model not in provider_owned
    ]
    if unclaimed:
        groups.append(
            {
                "provider": "Sentry routes",
                "provider_id": "sentry",
                "models": [{"id": model, "label": model} for model in unclaimed],
            }
        )
    return groups


async def _validated_model(runtime, profile_id, model: str | None) -> str | None:
    """Return *model* if this profile can actually route to it, else refuse.

    Falling back to the default on an unknown model is the specific bug this
    endpoint exists to prevent: the client would show one model while another
    answered. That failure is silent and survives inspection, so an unknown
    model has to be a loud 400.

    An unreadable registry is also refused. Treating "we could not ask" as
    "anything is fine" would reintroduce exactly the same silent mis-routing
    through a different door.
    """
    if model is None:
        return None
    try:
        advertised = await runtime.available_models(profile_id)
    except UnknownProfileError:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not read the available models for this profile.",
        ) from exc
    if model not in tuple(advertised):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Model {model!r} is not available to this profile. "
                "Only models this deployment can actually reach may be selected."
            ),
        )
    return model


@router.get("/actions")
async def list_actions(
    request: Request,
    limit: int = 100,
    session_id: str | None = None,
    caller: Caller = Depends(require_caller),
) -> dict:
    """What this caller's own agent has been doing, newest first.

    Scoped to the caller's profile with no override: one person's action log is
    not another's, and an operator-wide view would need its own admin route
    rather than a query parameter anyone could pass.
    """
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Action log storage is unavailable.",
        )
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT occurred_at, kind, model, tool_name, tool_args, result_preview,"
        " status, input_tokens, output_tokens, total_tokens, session_id"
        " FROM agent_actions WHERE profile_id = $1"
    )
    args: list = [caller.profile_id]
    if session_id:
        sql += " AND session_id = $2"
        args.append(session_id)
    sql += f" ORDER BY occurred_at DESC LIMIT {limit}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return {"actions": [dict(r) for r in rows]}


@router.get("/usage")
async def usage_summary(
    request: Request, days: int = 30, caller: Caller = Depends(require_caller)
) -> dict:
    """Per-model token totals for this caller over the last *days*.

    Turns whose model was never reported group under a NULL model rather than a
    literal "unknown", so a gap in instrumentation stays visibly a gap instead
    of masquerading as a model.
    """
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Action log storage is unavailable.",
        )
    days = max(1, min(int(days), 365))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT model, count(*) AS turns,"
            " coalesce(sum(input_tokens),0) AS input_tokens,"
            " coalesce(sum(output_tokens),0) AS output_tokens,"
            " coalesce(sum(total_tokens),0) AS total_tokens"
            " FROM agent_actions"
            " WHERE profile_id = $1 AND kind = 'turn'"
            f" AND occurred_at > now() - interval '{days} days'"
            " GROUP BY model ORDER BY total_tokens DESC",
            caller.profile_id,
        )
        tools = await conn.fetch(
            "SELECT tool_name, count(*) AS calls FROM agent_actions"
            " WHERE profile_id = $1 AND kind = 'tool' AND tool_name IS NOT NULL"
            f" AND occurred_at > now() - interval '{days} days'"
            " GROUP BY tool_name ORDER BY calls DESC LIMIT 20",
            caller.profile_id,
        )
    return {
        "days": days,
        "by_model": [dict(r) for r in rows],
        "by_tool": [dict(r) for r in tools],
    }


@router.get("/models")
async def list_models(request: Request, caller: Caller = Depends(require_caller)) -> dict:
    """Model aliases this caller's own profile can actually route to.

    This is the picker's only source. It deliberately has no fallback list: if a
    profile advertises nothing, the answer is an empty list, because showing a
    catalogue of models that cannot answer is the defect this replaced.
    """
    runtime = request.app.state.runtime
    try:
        models = await runtime.available_models(caller.profile_id)
    except UnknownProfileError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No agent runtime is provisioned for this profile.",
        )
    models = tuple(models)
    native_status = None
    if hasattr(runtime, "native_runtime_status"):
        try:
            discovered = await runtime.native_runtime_status(caller.profile_id)
            native_status = {
                "id": "codex",
                "available": bool(discovered.get("available")),
                "reason": discovered.get("reason"),
                "node_name": discovered.get("node_name"),
                "version": discovered.get("version"),
                "auth_mode": discovered.get("auth_mode"),
                "features": list(discovered.get("features") or []),
                "workspaces": list(discovered.get("workspaces") or []),
            }
        except Exception:
            native_status = {
                "id": "codex",
                "available": False,
                "reason": "Could not read the workstation's Codex capabilities.",
                "workspaces": [],
                "features": [],
            }
    return {
        "models": list(models),
        "groups": await _linked_model_groups(runtime, caller.profile_id, models),
        "experiences": _EXPERIENCES,
        "native_runtimes": [native_status] if native_status is not None else [],
    }


def _sse(event: RuntimeEvent) -> str:
    payload = {
        "type": event.type.value,
        "sessionId": event.session_id,
        "correlationId": event.correlation_id,
        "occurredAt": event.occurred_at.isoformat(),
        "summary": event.summary,
        "evidence": event.evidence,
        "maySpeak": event.may_speak,
    }
    return f"data: {json.dumps(payload)}\n\n"



async def _load_endpoint_on_demand(request: Request, runtime, profile_id) -> bool:
    """Try to register a profile provisioned since startup. Never raises.

    Returns True only when this profile's own persisted endpoint was found and
    registered, so an exception, a missing pool, or an absent encryption key all
    leave routing exactly as fail-closed as before.
    """
    pool = getattr(request.app.state, "pool", None)
    cipher = getattr(request.app.state, "endpoint_cipher", None)
    if pool is None or cipher is None or not hasattr(runtime, "register"):
        return False
    try:
        return await refresh_profile_endpoint(runtime, pool, cipher, profile_id)
    except Exception:
        return False


@router.post("/turn")
async def chat_turn(
    body: ChatTurnRequest,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> StreamingResponse:
    runtime = request.app.state.runtime
    correlation_id = uuid4().hex

    # Resolve the caller's own agent BEFORE returning a 200 stream. An
    # unregistered or mismatched profile must fail closed here rather than fall
    # through to another profile's runtime.
    try:
        session = await runtime.create_session(
            caller.profile_id, SessionScope(profile_id=caller.profile_id)
        )
    except UnknownProfileError as exc:
        # The caller may have been provisioned after this process started:
        # endpoints are otherwise read only at boot. Load THIS profile's own
        # persisted row and retry once. Still fail-closed -- a profile with no
        # row stays unroutable and never falls through to another agent.
        if not await _load_endpoint_on_demand(request, runtime, caller.profile_id):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No agent runtime is provisioned for this profile.",
            ) from exc
        try:
            session = await runtime.create_session(
                caller.profile_id, SessionScope(profile_id=caller.profile_id)
            )
        except UnknownProfileError as exc2:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No agent runtime is provisioned for this profile.",
            ) from exc2

    session_id = body.session_id or session.session_id

    # Validate the model BEFORE the audit write: a turn that will be refused
    # should not leave an audit row claiming it ran.
    chosen_model = await _validated_model(runtime, caller.profile_id, body.model)

    audit = _audit_service(request)
    if audit is None:
        # No database means no audit trail. Token verification needs no DB, so
        # this state is reachable and would otherwise run the turn UNRECORDED —
        # defeating the append-only audit guarantee the whole design rests on.
        # Refuse, exactly as a failed audit INSERT does below.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audit storage is unavailable; refusing to run an unrecorded turn.",
        )
    if audit is not None:
        # The prompt itself is never stored in audit; only that a turn happened,
        # by whom, in which profile/session. A turn that cannot be audited is
        # refused (fail closed on audit) rather than run unrecorded — but with a
        # clean 503, never a raw 500 stack trace to the client.
        try:
            await audit.record(
                AuditEvent(
                    action="chat.turn",
                    decision=Decision.ALLOWED,
                    correlation_id=correlation_id,
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=caller.profile_id,
                    target_kind="session",
                    target_id=session_id,
                )
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not record this turn for audit; refused.",
            ) from exc

    effective_experience = body.experience
    if hasattr(runtime, "experience_for_model"):
        effective_experience = runtime.experience_for_model(
            chosen_model, effective_experience
        )
    turn = RuntimeTurn(
        session_id=session_id,
        profile_id=caller.profile_id,
        prompt=body.prompt,
        correlation_id=correlation_id,
        quoted_context=tuple(body.quoted_context),
        model=chosen_model,
        experience=effective_experience,
        workspace_id=body.workspace_id,
    )

    # Recording lives here, not in the runtime adapter: this is where the pool
    # and the audit write already are, which keeps the adapter a pure
    # translation layer. It never raises -- telemetry that can truncate a live
    # answer is worse than no telemetry.
    recorder = ActionRecorder(getattr(request.app.state, "pool", None), caller.profile_id)

    async def stream():
        async for event in runtime.send_turn(turn):
            await recorder.record(event)
            yield _sse(event)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Sentry-Correlation-Id": correlation_id},
    )


@router.post("/native/respond")
async def respond_to_native_runtime(
    body: NativeRuntimeResponse,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict:
    """Answer one exact App Server request for this profile's live work order."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Native runtime response storage is unavailable.",
        )
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT w.id, w.state, w.correlation_id
                FROM work_orders w
                WHERE w.id = $1 AND w.profile_id = $2 AND w.harness = 'codex'
                  AND EXISTS (
                      SELECT 1 FROM work_order_events e
                      WHERE e.work_order_id = w.id
                        AND e.payload->>'request_id' = $3
                  )
                FOR UPDATE OF w
                """,
                body.work_order_id,
                caller.profile_id,
                body.request_id,
            )
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Native Codex request not found.",
                )
            if row["state"] != "inProgress":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This Codex request is no longer waiting for a response.",
                )
            existing = await conn.fetchrow(
                """
                SELECT response FROM work_order_responses
                WHERE work_order_id = $1 AND request_id = $2
                """,
                body.work_order_id,
                body.request_id,
            )
            if existing is not None:
                if _json_object(existing["response"]) == body.response:
                    return {"ok": True, "already_answered": True}
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This Codex request was already answered.",
                )
            await conn.execute(
                """
                INSERT INTO work_order_responses
                    (work_order_id, request_id, response, responded_by)
                VALUES ($1,$2,$3,$4)
                """,
                body.work_order_id,
                body.request_id,
                json.dumps(body.response),
                caller.user_id,
            )
            audit = AuditService(pool)
            await audit.record_with(
                conn,
                AuditEvent(
                    action="codex.native.respond",
                    decision=Decision.ALLOWED,
                    correlation_id=row["correlation_id"],
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=caller.profile_id,
                    target_kind="workorder",
                    target_id=str(body.work_order_id),
                    detail="answered a native Codex approval or input request",
                ),
            )
    return {"ok": True}


def _audit_service(request: Request) -> AuditService | None:
    pool = getattr(request.app.state, "pool", None)
    return AuditService(pool) if pool is not None else None

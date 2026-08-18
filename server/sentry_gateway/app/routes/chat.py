"""Client chat turns — the one path a client uses to reach *their* agent.

The browser/desktop/phone never addresses a Hermes container directly. It sends a
turn here; the Gateway resolves the caller's profile from their token and forwards
to that profile's Hermes instance through the AgentRuntime adapter. Routing is
fail-closed: a profile with no registered runtime never falls back to another
profile's agent (that would run one person's work inside another's).
"""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent_runtime.base import RuntimeEvent, RuntimeTurn, SessionScope
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
    return {"models": list(models)}


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

    turn = RuntimeTurn(
        session_id=session_id,
        profile_id=caller.profile_id,
        prompt=body.prompt,
        correlation_id=correlation_id,
        quoted_context=tuple(body.quoted_context),
        model=chosen_model,
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


def _audit_service(request: Request) -> AuditService | None:
    pool = getattr(request.app.state, "pool", None)
    return AuditService(pool) if pool is not None else None

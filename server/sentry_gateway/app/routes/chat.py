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
from ..audit.service import AuditEvent, AuditService, Decision
from ..profile_memory_context import load_profile_memory_context
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatTurnRequest(BaseModel):
    prompt: str = Field(min_length=1)
    #: Continue an existing conversation. Absent on the first turn.
    session_id: str | None = None
    #: Untrusted connector/document text, passed to the runtime as quoted data.
    quoted_context: list[str] = Field(default_factory=list)
    #: Applies only to the private Gateway turn, never a team/workspace turn.
    use_profile_memory: bool = True


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
    memory_context: tuple[str, ...] = ()
    if body.use_profile_memory:
        try:
            memory_context = await load_profile_memory_context(
                request.app.state.pool, caller.profile_id
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Personal memory is unavailable; this turn was not started. Retry or explicitly disable profile memory for this turn.",
            ) from exc

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
        quoted_context=tuple(body.quoted_context) + memory_context,
    )

    async def stream():
        async for event in runtime.send_turn(turn):
            yield _sse(event)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "X-Sentry-Correlation-Id": correlation_id,
            "X-Sentry-Memory": (
                "included" if memory_context else "empty" if body.use_profile_memory else "disabled"
            ),
        },
    )


def _audit_service(request: Request) -> AuditService | None:
    pool = getattr(request.app.state, "pool", None)
    return AuditService(pool) if pool is not None else None

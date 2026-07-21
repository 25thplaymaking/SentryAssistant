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
from ..agent_runtime.hermes import UnknownProfileError
from ..audit.service import AuditEvent, AuditService, Decision
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatTurnRequest(BaseModel):
    prompt: str = Field(min_length=1)
    #: Continue an existing conversation. Absent on the first turn.
    session_id: str | None = None
    #: Untrusted connector/document text, passed to the runtime as quoted data.
    quoted_context: list[str] = Field(default_factory=list)


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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No agent runtime is provisioned for this profile.",
        ) from exc

    session_id = body.session_id or session.session_id

    audit = _audit_service(request)
    if audit is not None:
        # The prompt itself is never stored in audit; only that a turn happened,
        # by whom, in which profile/session.
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

    turn = RuntimeTurn(
        session_id=session_id,
        profile_id=caller.profile_id,
        prompt=body.prompt,
        correlation_id=correlation_id,
        quoted_context=tuple(body.quoted_context),
    )

    async def stream():
        async for event in runtime.send_turn(turn):
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

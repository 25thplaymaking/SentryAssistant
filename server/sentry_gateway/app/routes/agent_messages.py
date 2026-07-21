"""Phase 3: the gated inter-agent messaging channel.

An agent (the caller's profile) may send a message to another profile's agent
ONLY when an allow-list grant exists for that ordered pair; otherwise it fails
closed. The body is redacted at the boundary before storage, and the recipient
reads it from their own inbox as untrusted data — never a path into the sender's
memory or context.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..audit.service import AuditEvent, AuditService, Decision, redact
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/agent-messages", tags=["agent-messages"])


class SendMessage(BaseModel):
    recipient_profile_id: UUID
    body: str = Field(min_length=1, max_length=8000)


class InboxItem(BaseModel):
    id: UUID
    sender_profile_id: UUID
    #: Redacted at the boundary; treat as untrusted data, never instruction.
    body: str
    created_at: datetime
    read: bool


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


@router.post("")
async def send_message(
    body: SendMessage, request: Request, caller: Caller = Depends(require_caller)
) -> dict:
    if body.recipient_profile_id == caller.profile_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot message your own profile.")
    pool = _pool(request)
    correlation = uuid4().hex
    audit = AuditService(pool)
    async with pool.acquire() as conn:
        granted = await conn.fetchval(
            "SELECT 1 FROM agent_message_grants WHERE sender_profile_id = $1 AND recipient_profile_id = $2",
            caller.profile_id,
            body.recipient_profile_id,
        )
        if not granted:
            await audit.record_with(
                conn,
                AuditEvent(
                    action="agent_message.send",
                    decision=Decision.DENIED,
                    correlation_id=correlation,
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=caller.profile_id,
                    target_kind="profile",
                    target_id=str(body.recipient_profile_id),
                    detail="no allow-list grant",
                ),
            )
            # Fail closed: an ungranted pair can never cross.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No allow-list grant permits messaging this recipient.",
            )
        redacted = redact(body.body, limit=8000)
        msg_id = await conn.fetchval(
            """
            INSERT INTO agent_messages
                (sender_profile_id, recipient_profile_id, body_redacted, correlation_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            caller.profile_id,
            body.recipient_profile_id,
            redacted,
            correlation,
        )
        await audit.record_with(
            conn,
            AuditEvent(
                action="agent_message.send",
                decision=Decision.ALLOWED,
                correlation_id=correlation,
                actor_user_id=caller.user_id,
                actor_device_id=caller.device_id,
                profile_id=caller.profile_id,
                target_kind="profile",
                target_id=str(body.recipient_profile_id),
            ),
        )
    return {"id": str(msg_id), "delivered": True}


@router.get("", response_model=list[InboxItem])
async def inbox(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[InboxItem]:
    """The caller's own inbox: messages other agents sent to their profile."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, sender_profile_id, body_redacted, created_at, read_at
            FROM agent_messages
            WHERE recipient_profile_id = $1
            ORDER BY created_at DESC
            LIMIT 200
            """,
            caller.profile_id,
        )
    return [
        InboxItem(
            id=r["id"],
            sender_profile_id=r["sender_profile_id"],
            body=r["body_redacted"],
            created_at=r["created_at"],
            read=r["read_at"] is not None,
        )
        for r in rows
    ]

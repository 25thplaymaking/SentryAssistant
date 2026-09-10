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


class CreateMessageGrant(BaseModel):
    recipient_profile_id: UUID
    sender_profile_id: UUID | None = None


class MessageGrantView(BaseModel):
    sender_profile_id: UUID
    recipient_profile_id: UUID
    created_at: datetime


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


@router.post("/{message_id}/read")
async def mark_read(
    message_id: UUID, request: Request, caller: Caller = Depends(require_caller)
) -> dict:
    """Mark one of the caller's own inbox messages read. Idempotent: marking an
    already-read message is a 200, because the client's goal state holds."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        marked = await conn.fetchval(
            """
            UPDATE agent_messages SET read_at = now()
            WHERE id = $1 AND recipient_profile_id = $2 AND read_at IS NULL
            RETURNING id
            """,
            message_id,
            caller.profile_id,
        )
        if marked is None:
            # Distinguish "already read" (idempotent success) from "not yours
            # or nonexistent" — the latter is a 404 rather than a 403 so a
            # sender's message IDs cannot be probed from another profile.
            exists = await conn.fetchval(
                "SELECT 1 FROM agent_messages"
                " WHERE id = $1 AND recipient_profile_id = $2",
                message_id,
                caller.profile_id,
            )
            if not exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
                )
    return {"read": True}


@router.get("/grants", response_model=list[MessageGrantView])
async def list_grants(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[MessageGrantView]:
    """List message grants where the caller's profile is the sender or recipient."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sender_profile_id, recipient_profile_id, created_at
            FROM agent_message_grants
            WHERE sender_profile_id = $1 OR recipient_profile_id = $1
            ORDER BY created_at DESC
            """,
            caller.profile_id,
        )
    return [
        MessageGrantView(
            sender_profile_id=r["sender_profile_id"],
            recipient_profile_id=r["recipient_profile_id"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/grants", response_model=MessageGrantView, status_code=status.HTTP_201_CREATED)
async def create_grant(
    body: CreateMessageGrant, request: Request, caller: Caller = Depends(require_caller)
) -> MessageGrantView:
    """Create an allow-list grant for an ordered pair (sender -> recipient).
    Defaults sender to caller.profile_id."""
    sender_id = body.sender_profile_id or caller.profile_id
    recipient_id = body.recipient_profile_id
    if sender_id == recipient_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot grant messaging permission to self.",
        )
    pool = _pool(request)
    audit = AuditService(pool)
    correlation = uuid4().hex

    async with pool.acquire() as conn:
        rec_exists = await conn.fetchval(
            "SELECT 1 FROM profiles WHERE id = $1", recipient_id
        )
        if not rec_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recipient profile not found.",
            )

        if sender_id != caller.profile_id:
            sender_owned = await conn.fetchval(
                "SELECT 1 FROM profiles WHERE id = $1 AND owner_user_id = $2",
                sender_id,
                caller.user_id,
            )
            if not sender_owned:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot create grant for a profile you do not own.",
                )

        row = await conn.fetchrow(
            """
            INSERT INTO agent_message_grants
                (sender_profile_id, recipient_profile_id, created_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (sender_profile_id, recipient_profile_id)
            DO UPDATE SET created_at = agent_message_grants.created_at
            RETURNING sender_profile_id, recipient_profile_id, created_at
            """,
            sender_id,
            recipient_id,
            caller.user_id,
        )

        await audit.record_with(
            conn,
            AuditEvent(
                action="agent_message_grant.create",
                decision=Decision.ALLOWED,
                correlation_id=correlation,
                actor_user_id=caller.user_id,
                actor_device_id=caller.device_id,
                profile_id=sender_id,
                target_kind="profile",
                target_id=str(recipient_id),
                detail=f"granted {sender_id} -> {recipient_id}",
            ),
        )

    return MessageGrantView(
        sender_profile_id=row["sender_profile_id"],
        recipient_profile_id=row["recipient_profile_id"],
        created_at=row["created_at"],
    )


@router.delete("/grants/{recipient_profile_id}")
async def delete_grant(
    recipient_profile_id: UUID, request: Request, caller: Caller = Depends(require_caller)
) -> dict:
    """Revoke an allow-list grant where caller's profile is the sender."""
    pool = _pool(request)
    audit = AuditService(pool)
    correlation = uuid4().hex
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            """
            DELETE FROM agent_message_grants
            WHERE sender_profile_id = $1 AND recipient_profile_id = $2
            RETURNING recipient_profile_id
            """,
            caller.profile_id,
            recipient_profile_id,
        )
        if deleted is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")

        await audit.record_with(
            conn,
            AuditEvent(
                action="agent_message_grant.delete",
                decision=Decision.ALLOWED,
                correlation_id=correlation,
                actor_user_id=caller.user_id,
                actor_device_id=caller.device_id,
                profile_id=caller.profile_id,
                target_kind="profile",
                target_id=str(recipient_profile_id),
                detail=f"revoked {caller.profile_id} -> {recipient_profile_id}",
            ),
        )

    return {"deleted": True}

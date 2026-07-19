"""Append-only audit.

Every remote work order, approval, connector mutation, memory write, and result
gets an immutable event. The database refuses UPDATE and DELETE on this table, so
a bug here can fail to record something but can never rewrite history.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID


class Decision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    FAILED = "failed"


#: Substrings that must never reach an audit row. Audit stores a hash and a
#: redacted summary; the payload itself belongs in profile-scoped storage.
_SENSITIVE_HINTS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "token",
    "private_key",
    "refresh_token",
)


def redact(detail: str, limit: int = 500) -> str:
    """Drop anything that looks like a credential, then truncate."""
    lowered = detail.lower()
    for hint in _SENSITIVE_HINTS:
        if hint in lowered:
            return "[redacted: detail referenced a credential]"
    return detail[:limit]


def evidence_hash(payload: Any) -> str:
    """Stable hash so a row can be tied to evidence without storing it."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str
    decision: Decision
    correlation_id: str
    actor_user_id: UUID | None = None
    actor_device_id: UUID | None = None
    profile_id: UUID | None = None
    team_id: UUID | None = None
    target_kind: str | None = None
    target_id: str | None = None
    evidence: Any | None = None
    detail: str = ""


class SupportsExecute(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...


class AuditService:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def record(self, event: AuditEvent) -> None:
        async with self._pool.acquire() as conn:
            await self._write(conn, event)

    async def record_with(self, conn: SupportsExecute, event: AuditEvent) -> None:
        """Record inside a caller's transaction so the decision and its audit
        row commit together."""
        await self._write(conn, event)

    @staticmethod
    async def _write(conn: SupportsExecute, event: AuditEvent) -> None:
        await conn.execute(
            """
            INSERT INTO audit_events (
                actor_user_id, actor_device_id, profile_id, team_id,
                action, target_kind, target_id, decision,
                correlation_id, evidence_hash, redacted_detail
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            event.actor_user_id,
            event.actor_device_id,
            event.profile_id,
            event.team_id,
            event.action,
            event.target_kind,
            event.target_id,
            event.decision.value,
            event.correlation_id,
            evidence_hash(event.evidence) if event.evidence is not None else None,
            redact(event.detail) if event.detail else None,
        )

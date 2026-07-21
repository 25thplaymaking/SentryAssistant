"""The Sentry-owned agent runtime boundary.

Every runtime-specific detail stops here. No desktop, phone, web, or Node client
may call a Hermes endpoint directly, so all of them speak these Sentry types and
the adapter translates. Swapping Hermes for another runtime must not change any
identity, work-order, audit, or artifact record.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


class RuntimeEventType(StrEnum):
    """Normalized lifecycle. Adapters map their native events onto exactly these."""

    SESSION_STARTED = "session.started"
    TURN_STARTED = "turn.started"
    TOOL_PROGRESS = "tool.progress"
    APPROVAL_REQUIRED = "approval.required"
    NEEDS_INPUT = "needs.input"
    MESSAGE = "message"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    CANCELLED = "cancelled"
    ERROR = "error"


#: Only a resolved result may ever reach a speech provider. Tool progress and
#: command narration must never be speakable, so speech eligibility is decided
#: here rather than at the UI layer.
SPEAKABLE_EVENTS: frozenset[RuntimeEventType] = frozenset(
    {RuntimeEventType.TURN_COMPLETED}
)


class ContextVisibility(StrEnum):
    PRIVATE = "private"
    TEAM = "team"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """What a runtime can actually do, discovered rather than assumed."""

    runtime_name: str
    pinned_version: str
    supports_sessions: bool = False
    supports_session_search: bool = False
    supports_work_board: bool = False
    supports_cancellation: bool = False
    supports_delegation: bool = False
    degraded_reason: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.degraded_reason is None


@dataclass(frozen=True, slots=True)
class SessionScope:
    """Isolation boundary for a session. Team scope never carries personal context."""

    profile_id: UUID
    team_id: UUID | None = None
    workspace_id: str | None = None
    visibility: ContextVisibility = ContextVisibility.PRIVATE


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    session_id: str
    profile_id: UUID
    scope: SessionScope
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeTurn:
    session_id: str
    profile_id: UUID
    prompt: str
    correlation_id: str
    #: Untrusted connector/document content is passed as quoted data. It must never
    #: be able to select tools, workspaces, or approval modes.
    quoted_context: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: RuntimeEventType
    session_id: str
    correlation_id: str
    occurred_at: datetime
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def may_speak(self) -> bool:
        return self.type in SPEAKABLE_EVENTS


@dataclass(frozen=True, slots=True)
class ScopedSessionQuery:
    """Search is always scoped. A personal session must never surface in a team search."""

    profile_id: UUID
    query: str
    team_id: UUID | None = None
    workspace_id: str | None = None
    limit: int = 20


@dataclass(frozen=True, slots=True)
class SessionHit:
    session_id: str
    profile_id: UUID
    snippet: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class WorkOrderProjection:
    """A rebuildable execution projection keyed to the authoritative work-order ID."""

    work_order_id: UUID
    profile_id: UUID
    title: str
    state: str
    team_id: UUID | None = None

    @property
    def idempotency_key(self) -> str:
        """Projections must be replayable without duplicating execution."""
        return f"sentry-wo-{self.work_order_id}"


@runtime_checkable
class AgentRuntime(Protocol):
    """The only surface the Gateway may use to reach an agent runtime."""

    async def capabilities(self, profile_id: UUID) -> RuntimeCapabilities: ...

    async def create_session(
        self, profile_id: UUID, scope: SessionScope
    ) -> RuntimeSession: ...

    def send_turn(self, request: RuntimeTurn) -> AsyncIterator[RuntimeEvent]: ...

    async def cancel(self, run_id: UUID) -> None: ...

    async def search_sessions(self, query: ScopedSessionQuery) -> list[SessionHit]: ...

    async def project_work_order(self, projection: WorkOrderProjection) -> None: ...

    async def read_work_board(self, profile_id: UUID) -> dict: ...

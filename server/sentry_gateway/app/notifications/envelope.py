"""Notification envelopes and the redaction boundary.

A push payload leaves Sentry's control the moment it is handed to Apple. It
therefore carries a severity, a title, a deliberately vague summary, and a deep
link — never source code, email bodies, secrets, approval tokens, or evidence.
The detail is fetched afterwards, over an authenticated connection, once the
person has opened the app.

Redaction is enforced here rather than trusted to callers: `build` constructs the
payload and there is no path that puts caller-supplied text into it unchecked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class Severity(StrEnum):
    ROUTINE = "routine"
    IMPORTANT = "important"
    URGENT = "urgent"
    SECURITY = "security"


class Channel(StrEnum):
    IN_APP = "in_app"
    PUSH = "push"
    DISCORD_DM = "discord_dm"
    SMS = "sms"
    DIGEST = "digest"


class EventType(StrEnum):
    WORK_STARTED = "work.started"
    INPUT_REQUIRED = "input.required"
    APPROVAL_REQUIRED = "approval.required"
    RESOLVED = "task.resolved"
    FAILED = "task.failed"
    DEPLOY_COMPLETED = "deploy.completed"
    DEPLOY_FAILED = "deploy.failed"
    REMINDER_DUE = "reminder.due"
    CONNECTOR_FAILED = "connector.failed"
    DAILY_BRIEF = "daily.brief"
    SECURITY_ALERT = "security.alert"


#: Only a resolved result may ever be spoken, and only one sentence of it.
SPEAKABLE_EVENTS: frozenset[EventType] = frozenset({EventType.RESOLVED})

#: Channels that leave Sentry's infrastructure entirely. Anything delivered here
#: is assumed to be readable by the carrier or platform.
EXTERNAL_CHANNELS: frozenset[Channel] = frozenset(
    {Channel.PUSH, Channel.DISCORD_DM, Channel.SMS}
)

#: SMS is the narrowest channel and is reserved for events a person must act on
#: even with the app unavailable.
SMS_ELIGIBLE: frozenset[EventType] = frozenset(
    {EventType.SECURITY_ALERT, EventType.APPROVAL_REQUIRED, EventType.FAILED}
)

MAX_TITLE = 80
MAX_SUMMARY = 160

# Shapes that must never survive into an external payload. These are matched
# against the whole candidate string, not just its start.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{8,}", re.I),          # provider keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),                    # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}", re.I),            # Slack tokens
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),  # JWTs
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),  # addresses
    re.compile(r"(?i)\b(password|passwd|secret|api[_\-]?key|token|bearer)\b\s*[:=]"),
    re.compile(r"[A-Za-z]:\\[^\s]{4,}"),                            # Windows paths
    re.compile(r"(?<!\w)/(?:home|srv|etc|root|var)/[^\s]{2,}"),     # POSIX paths
    re.compile(r"```"),                                             # code fences
)

REDACTED = "Details are available in the app."


def contains_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def redact(text: str, limit: int) -> str:
    """Return text safe for an external payload, or a neutral stand-in.

    Deliberately all-or-nothing: partially masking a string tends to leak its
    shape, and a notification has no need for the specifics.
    """
    collapsed = " ".join(text.split())
    if not collapsed:
        return REDACTED
    if contains_sensitive(collapsed):
        return REDACTED
    if len(collapsed) > limit:
        return collapsed[: limit - 1].rstrip() + "…"
    return collapsed


@dataclass(frozen=True, slots=True)
class NotificationEnvelope:
    id: UUID
    event_type: EventType
    severity: Severity
    title: str
    redacted_summary: str
    deep_link: str
    allowed_channels: tuple[Channel, ...]
    expires_at: datetime
    correlation_id: str
    #: Kept server-side for in-app rendering; never included in a push payload.
    private_detail: str = field(default="", repr=False)

    @property
    def may_speak(self) -> bool:
        return self.event_type in SPEAKABLE_EVENTS

    def apns_payload(self) -> dict[str, Any]:
        """The dictionary actually handed to Apple.

        Contains no private detail by construction: this method reads only the
        already-redacted fields, so a caller cannot widen it by passing more.
        """
        return {
            "aps": {
                "alert": {"title": self.title, "body": self.redacted_summary},
                "sound": "default" if self.severity is not Severity.ROUTINE else None,
                "interruption-level": (
                    "time-sensitive"
                    if self.severity in (Severity.URGENT, Severity.SECURITY)
                    else "active"
                ),
                "thread-id": self.correlation_id,
                "category": self.event_type.value,
            },
            # Identifiers only. The app fetches detail over an authenticated
            # connection after the person opens it.
            "sentry": {
                "id": str(self.id),
                "event": self.event_type.value,
                "deepLink": self.deep_link,
                "correlationId": self.correlation_id,
            },
        }

    def sms_body(self) -> str:
        """SMS is the least private channel; it gets the least."""
        return f"Sentry: {self.title}. Open the app."


def build(
    *,
    event_type: EventType,
    severity: Severity,
    title: str,
    summary: str,
    work_order_id: UUID | None = None,
    correlation_id: str,
    ttl: timedelta = timedelta(hours=24),
    private_detail: str = "",
) -> NotificationEnvelope:
    """Construct an envelope with redaction already applied."""
    channels = [Channel.IN_APP, Channel.PUSH]
    if severity in (Severity.URGENT, Severity.SECURITY):
        channels.append(Channel.DISCORD_DM)
    if event_type in SMS_ELIGIBLE and severity in (Severity.URGENT, Severity.SECURITY):
        channels.append(Channel.SMS)

    deep_link = (
        f"sentry://workorders/{work_order_id}"
        if work_order_id
        else f"sentry://events/{correlation_id}"
    )

    return NotificationEnvelope(
        id=uuid4(),
        event_type=event_type,
        severity=severity,
        title=redact(title, MAX_TITLE),
        redacted_summary=redact(summary, MAX_SUMMARY),
        deep_link=deep_link,
        allowed_channels=tuple(channels),
        expires_at=datetime.now(timezone.utc) + ttl,
        correlation_id=correlation_id,
        private_detail=private_detail,
    )

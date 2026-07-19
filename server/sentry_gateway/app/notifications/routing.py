"""Notification routing: preferences, quiet hours, de-duplication, fallback.

Decides *whether* and *where* an envelope goes. What it may say is already
settled by `envelope.py`; nothing here can widen that.

Two rules shape most of this:

- A person's quiet hours are respected, but a security alert is not something
  Sentry may sit on. Urgent and security events override quiet hours; everything
  else defers to the next window.
- A retry must never become a second buzz. Delivery is keyed on the envelope's
  identity, so redelivery after a crash or a duplicate upstream event produces
  one user-visible notification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum

from .envelope import Channel, EventType, NotificationEnvelope, Severity


class DeliveryMode(StrEnum):
    OFF = "off"
    IN_APP = "in_app"
    PUSH = "push"
    DISCORD_DM = "discord_dm"
    SMS = "sms"
    DIGEST = "digest"


#: Events important enough to wake someone. Everything else waits.
QUIET_HOURS_OVERRIDE: frozenset[Severity] = frozenset(
    {Severity.URGENT, Severity.SECURITY}
)

#: How long a delivered envelope suppresses an identical one.
DEDUPE_WINDOW = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class QuietHours:
    """A local-time window. Crossing midnight is supported."""

    start: time
    end: time

    def contains(self, moment: datetime) -> bool:
        current = moment.timetz().replace(tzinfo=None)
        if self.start <= self.end:
            return self.start <= current < self.end
        # e.g. 22:00 -> 07:00
        return current >= self.start or current < self.end


@dataclass(frozen=True, slots=True)
class Preferences:
    """Per-person, per-event delivery policy."""

    modes: dict[EventType, DeliveryMode] = field(default_factory=dict)
    quiet_hours: QuietHours | None = None
    sms_verified: bool = False
    discord_linked: bool = False
    default_mode: DeliveryMode = DeliveryMode.PUSH

    def mode_for(self, event_type: EventType) -> DeliveryMode:
        return self.modes.get(event_type, self.default_mode)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    channels: tuple[Channel, ...]
    deferred_to_digest: bool = False
    suppressed_as_duplicate: bool = False
    reason: str = ""

    @property
    def delivers(self) -> bool:
        return bool(self.channels)


_MODE_TO_CHANNEL: dict[DeliveryMode, Channel] = {
    DeliveryMode.IN_APP: Channel.IN_APP,
    DeliveryMode.PUSH: Channel.PUSH,
    DeliveryMode.DISCORD_DM: Channel.DISCORD_DM,
    DeliveryMode.SMS: Channel.SMS,
}


class DeliveryLedger:
    """Remembers what has already been delivered, so a retry is not a second buzz."""

    def __init__(self) -> None:
        self._seen: dict[str, datetime] = {}

    @staticmethod
    def key(envelope: NotificationEnvelope) -> str:
        # Keyed on correlation and event, not the envelope id: an upstream retry
        # mints a new id for what is, to the person, the same notification.
        return f"{envelope.correlation_id}:{envelope.event_type.value}"

    def already_delivered(self, envelope: NotificationEnvelope, now: datetime) -> bool:
        previous = self._seen.get(self.key(envelope))
        if previous is None:
            return False
        return now - previous < DEDUPE_WINDOW

    def record(self, envelope: NotificationEnvelope, now: datetime) -> None:
        self._seen[self.key(envelope)] = now


def route(
    envelope: NotificationEnvelope,
    preferences: Preferences,
    *,
    now: datetime,
    ledger: DeliveryLedger | None = None,
    push_available: bool = True,
) -> RoutingDecision:
    """Decide where this envelope goes."""
    if ledger is not None and ledger.already_delivered(envelope, now):
        return RoutingDecision(
            (), suppressed_as_duplicate=True, reason="already delivered recently"
        )

    mode = preferences.mode_for(envelope.event_type)
    if mode is DeliveryMode.OFF:
        return RoutingDecision((), reason="event muted by preference")

    overrides_quiet = envelope.severity in QUIET_HOURS_OVERRIDE
    in_quiet_hours = (
        preferences.quiet_hours is not None and preferences.quiet_hours.contains(now)
    )

    if in_quiet_hours and not overrides_quiet:
        # In-app is silent, so it is always safe; anything that buzzes waits.
        return RoutingDecision(
            (Channel.IN_APP,),
            deferred_to_digest=True,
            reason="quiet hours; held for the digest",
        )

    if mode is DeliveryMode.DIGEST:
        return RoutingDecision(
            (Channel.IN_APP,), deferred_to_digest=True, reason="digest preference"
        )

    requested = _MODE_TO_CHANNEL[mode]

    # The envelope decides what a channel is *allowed* to carry. A preference can
    # narrow that but never widen it.
    if requested not in envelope.allowed_channels:
        return RoutingDecision(
            (Channel.IN_APP,),
            reason=f"{requested.value} not permitted for this event",
        )

    if requested is Channel.SMS and not preferences.sms_verified:
        return RoutingDecision(
            (Channel.IN_APP,), reason="no verified number; SMS withheld"
        )

    if requested is Channel.DISCORD_DM and not preferences.discord_linked:
        return RoutingDecision(
            (Channel.IN_APP,), reason="no linked Discord; falling back"
        )

    if requested is Channel.PUSH and not push_available:
        # Falling back on an urgent event is worth the extra channel; on a
        # routine one it is just noise, so it stays in-app.
        if overrides_quiet and Channel.SMS in envelope.allowed_channels and preferences.sms_verified:
            return RoutingDecision(
                (Channel.IN_APP, Channel.SMS), reason="push unavailable; urgent fallback to SMS"
            )
        return RoutingDecision((Channel.IN_APP,), reason="push unavailable")

    channels = [Channel.IN_APP]
    if requested is not Channel.IN_APP:
        channels.append(requested)

    return RoutingDecision(
        tuple(channels),
        reason="quiet hours overridden by severity" if (in_quiet_hours and overrides_quiet) else "",
    )

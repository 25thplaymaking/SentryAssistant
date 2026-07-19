"""Reminder scheduling.

Reminders are stored in PostgreSQL and fired by the Gateway, not by a runtime's
own scheduler: a reminder must survive a runtime restart, an upgrade, or a
change of runtime entirely.

Recurrence is computed in the user's own timezone and then converted to UTC. Doing
it the other way round drifts an hour twice a year — a 09:00 daily reminder must
stay at 09:00 local across a DST boundary, not become 08:00 or 10:00.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class Recurrence(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DeliveryOutcome(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReminderError(Exception):
    """Raised when a reminder is impossible to schedule."""


@dataclass(frozen=True, slots=True)
class Reminder:
    id: str
    profile_id: str
    text: str
    #: Always stored UTC. Local time is a presentation concern except when
    #: computing the next occurrence, which must respect the user's clock.
    next_fire_utc: datetime
    timezone_name: str
    recurrence: Recurrence = Recurrence.ONCE
    last_outcome: DeliveryOutcome = DeliveryOutcome.PENDING
    last_attempt_utc: datetime | None = None
    failure_count: int = 0
    active: bool = True

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def local_time(self) -> datetime:
        return self.next_fire_utc.astimezone(self.zone)


def _same_local_clock_next(local: datetime, recurrence: Recurrence) -> datetime:
    """Advance a local datetime by one period, preserving wall-clock time."""
    if recurrence is Recurrence.DAILY:
        return local + timedelta(days=1)

    if recurrence is Recurrence.WEEKDAYS:
        candidate = local + timedelta(days=1)
        while candidate.weekday() >= 5:  # Saturday, Sunday
            candidate += timedelta(days=1)
        return candidate

    if recurrence is Recurrence.WEEKLY:
        return local + timedelta(days=7)

    if recurrence is Recurrence.MONTHLY:
        # Clamp rather than roll over: a reminder set for the 31st fires on the
        # 30th in a short month instead of skipping to the 1st of the next.
        year = local.year + (local.month // 12)
        month = local.month % 12 + 1
        day = min(local.day, _days_in_month(year, month))
        return local.replace(year=year, month=month, day=day)

    raise ReminderError(f"{recurrence} does not recur.")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    first_next = datetime(year, month + 1, 1)
    return (first_next - timedelta(days=1)).day


def next_occurrence(reminder: Reminder) -> datetime | None:
    """The next UTC firing time, or None for a one-shot reminder.

    The arithmetic happens on the local wall clock and is converted back to UTC,
    so a 09:00 reminder stays at 09:00 through a DST transition.
    """
    if reminder.recurrence is Recurrence.ONCE:
        return None

    local = reminder.next_fire_utc.astimezone(reminder.zone)
    naive_next = _same_local_clock_next(local.replace(tzinfo=None), reminder.recurrence)
    # Re-attach the zone so the correct offset for that date is applied.
    return naive_next.replace(tzinfo=reminder.zone).astimezone(
        reminder.next_fire_utc.tzinfo
    )


def due(reminders: list[Reminder], now_utc: datetime) -> list[Reminder]:
    return [
        r for r in reminders if r.active and r.next_fire_utc <= now_utc
    ]


#: A reminder that keeps failing is deactivated rather than retried forever.
MAX_FAILURES = 5


def record_attempt(
    reminder: Reminder, outcome: DeliveryOutcome, at_utc: datetime
) -> Reminder:
    """Advance a reminder after a delivery attempt."""
    if outcome is DeliveryOutcome.FAILED:
        failures = reminder.failure_count + 1
        # Give up rather than retry indefinitely, but keep the row so the person
        # can see it stopped and why.
        still_active = failures < MAX_FAILURES
        return replace(
            reminder,
            last_outcome=outcome,
            last_attempt_utc=at_utc,
            failure_count=failures,
            active=still_active,
        )

    following = next_occurrence(reminder)
    return replace(
        reminder,
        last_outcome=outcome,
        last_attempt_utc=at_utc,
        failure_count=0,
        next_fire_utc=following or reminder.next_fire_utc,
        # A one-shot reminder is finished once delivered.
        active=following is not None,
    )

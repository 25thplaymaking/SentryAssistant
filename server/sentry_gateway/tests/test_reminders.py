from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.reminders.schedule import (
    MAX_FAILURES,
    DeliveryOutcome,
    Recurrence,
    Reminder,
    ReminderError,
    due,
    next_occurrence,
    record_attempt,
)

LONDON = "Europe/London"


def reminder(
    *,
    local_str="2026-07-19 09:00",
    tz=LONDON,
    recurrence=Recurrence.DAILY,
    **kwargs,
) -> Reminder:
    local = datetime.fromisoformat(local_str).replace(tzinfo=ZoneInfo(tz))
    return Reminder(
        id="r-1",
        profile_id="p-1",
        text="Stand-up",
        next_fire_utc=local.astimezone(timezone.utc),
        timezone_name=tz,
        recurrence=recurrence,
        **kwargs,
    )


class TestRecurrence:
    def test_daily_advances_one_day(self):
        following = next_occurrence(reminder())
        assert following is not None
        assert following.astimezone(ZoneInfo(LONDON)).day == 20

    def test_once_has_no_next(self):
        assert next_occurrence(reminder(recurrence=Recurrence.ONCE)) is None

    def test_weekly_advances_seven_days(self):
        following = next_occurrence(reminder(recurrence=Recurrence.WEEKLY))
        assert following.astimezone(ZoneInfo(LONDON)).day == 26

    def test_weekdays_skips_the_weekend(self):
        # 2026-07-17 is a Friday; the next weekday is Monday the 20th.
        friday = reminder(local_str="2026-07-17 09:00", recurrence=Recurrence.WEEKDAYS)
        following = next_occurrence(friday).astimezone(ZoneInfo(LONDON))
        assert following.weekday() == 0
        assert following.day == 20

    def test_monthly_clamps_rather_than_rolling_over(self):
        """The 31st fires on the 30th in a short month, not the 1st of the next."""
        jan31 = reminder(local_str="2026-01-31 09:00", recurrence=Recurrence.MONTHLY)
        following = next_occurrence(jan31).astimezone(ZoneInfo(LONDON))
        assert following.month == 2
        assert following.day == 28

    def test_monthly_crosses_the_year_boundary(self):
        dec = reminder(local_str="2026-12-15 09:00", recurrence=Recurrence.MONTHLY)
        following = next_occurrence(dec).astimezone(ZoneInfo(LONDON))
        assert (following.year, following.month, following.day) == (2027, 1, 15)


class TestDaylightSaving:
    """A 09:00 reminder must stay 09:00 local across a DST boundary."""

    def test_wall_clock_is_preserved_across_the_spring_transition(self):
        # UK clocks go forward on 2026-03-29.
        before = reminder(local_str="2026-03-28 09:00", recurrence=Recurrence.DAILY)
        following = next_occurrence(before).astimezone(ZoneInfo(LONDON))
        assert (following.hour, following.minute) == (9, 0)
        assert following.day == 29

    def test_wall_clock_is_preserved_across_the_autumn_transition(self):
        # UK clocks go back on 2026-10-25.
        before = reminder(local_str="2026-10-24 09:00", recurrence=Recurrence.DAILY)
        following = next_occurrence(before).astimezone(ZoneInfo(LONDON))
        assert (following.hour, following.minute) == (9, 0)
        assert following.day == 25

    def test_the_utc_offset_actually_changes(self):
        """Proof the wall clock held because the offset moved, not by luck."""
        before = reminder(local_str="2026-03-28 09:00", recurrence=Recurrence.DAILY)
        after = next_occurrence(before)
        assert after - before.next_fire_utc == timedelta(hours=23)


class TestDueSelection:
    def test_only_past_due_active_reminders_fire(self):
        now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
        past = reminder(local_str="2026-07-19 08:00")
        future = reminder(local_str="2026-07-19 20:00")
        inactive = reminder(local_str="2026-07-19 08:00", active=False)

        selected = due([past, future, inactive], now)
        assert past in selected
        assert future not in selected
        assert inactive not in selected


class TestAttempts:
    def test_delivery_advances_a_recurring_reminder(self):
        original = reminder()
        now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
        updated = record_attempt(original, DeliveryOutcome.DELIVERED, now)

        assert updated.last_outcome is DeliveryOutcome.DELIVERED
        assert updated.next_fire_utc > original.next_fire_utc
        assert updated.active

    def test_a_one_shot_reminder_finishes_after_delivery(self):
        original = reminder(recurrence=Recurrence.ONCE)
        now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
        updated = record_attempt(original, DeliveryOutcome.DELIVERED, now)
        assert not updated.active

    def test_failures_accumulate_and_eventually_deactivate(self):
        current = reminder()
        now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)

        for attempt in range(1, MAX_FAILURES):
            current = record_attempt(current, DeliveryOutcome.FAILED, now)
            assert current.active, f"deactivated too early at attempt {attempt}"
            assert current.failure_count == attempt

        current = record_attempt(current, DeliveryOutcome.FAILED, now)
        assert not current.active
        assert current.failure_count == MAX_FAILURES

    def test_a_failing_reminder_does_not_advance_its_schedule(self):
        original = reminder()
        now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
        failed = record_attempt(original, DeliveryOutcome.FAILED, now)
        # Still due, so a recovered channel delivers it rather than skipping it.
        assert failed.next_fire_utc == original.next_fire_utc

    def test_a_success_clears_earlier_failures(self):
        current = record_attempt(
            reminder(), DeliveryOutcome.FAILED,
            datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc),
        )
        assert current.failure_count == 1
        recovered = record_attempt(
            current, DeliveryOutcome.DELIVERED,
            datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc),
        )
        assert recovered.failure_count == 0


class TestErrors:
    def test_asking_a_one_shot_to_recur_is_an_error(self):
        from app.reminders.schedule import _same_local_clock_next

        with pytest.raises(ReminderError):
            _same_local_clock_next(datetime(2026, 7, 19, 9, 0), Recurrence.ONCE)

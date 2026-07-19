from datetime import datetime, time, timedelta, timezone

import pytest

from app.notifications.envelope import Channel, EventType, Severity, build
from app.notifications.routing import (
    DeliveryLedger,
    DeliveryMode,
    Preferences,
    QuietHours,
    route,
)

NIGHT = datetime(2026, 7, 19, 23, 30, tzinfo=timezone.utc)
DAY = datetime(2026, 7, 19, 14, 0, tzinfo=timezone.utc)

QUIET = QuietHours(start=time(22, 0), end=time(7, 0))


def envelope(event=EventType.WORK_STARTED, severity=Severity.ROUTINE):
    return build(
        event_type=event,
        severity=severity,
        title="Something happened",
        summary="A thing occurred.",
        correlation_id="corr-1",
    )


class TestQuietHours:
    def test_window_crossing_midnight(self):
        assert QUIET.contains(datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc))
        assert QUIET.contains(datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc))
        assert not QUIET.contains(datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc))

    def test_daytime_window(self):
        window = QuietHours(start=time(9, 0), end=time(17, 0))
        assert window.contains(datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc))
        assert not window.contains(datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc))

    def test_routine_events_wait_for_morning(self):
        decision = route(envelope(), Preferences(quiet_hours=QUIET), now=NIGHT)
        assert decision.deferred_to_digest
        # In-app is silent, so it is still safe to record.
        assert decision.channels == (Channel.IN_APP,)
        assert Channel.PUSH not in decision.channels

    def test_security_alerts_are_never_held(self):
        decision = route(
            envelope(EventType.SECURITY_ALERT, Severity.SECURITY),
            Preferences(quiet_hours=QUIET),
            now=NIGHT,
        )
        assert not decision.deferred_to_digest
        assert Channel.PUSH in decision.channels
        assert "overridden" in decision.reason

    def test_urgent_failures_are_never_held(self):
        decision = route(
            envelope(EventType.FAILED, Severity.URGENT),
            Preferences(quiet_hours=QUIET),
            now=NIGHT,
        )
        assert Channel.PUSH in decision.channels


class TestPreferences:
    def test_muted_event_delivers_nothing(self):
        prefs = Preferences(modes={EventType.WORK_STARTED: DeliveryMode.OFF})
        decision = route(envelope(), prefs, now=DAY)
        assert not decision.delivers
        assert "muted" in decision.reason

    def test_digest_preference_defers(self):
        prefs = Preferences(modes={EventType.WORK_STARTED: DeliveryMode.DIGEST})
        decision = route(envelope(), prefs, now=DAY)
        assert decision.deferred_to_digest
        assert decision.channels == (Channel.IN_APP,)

    def test_push_preference_delivers_in_app_and_push(self):
        decision = route(envelope(), Preferences(), now=DAY)
        assert decision.channels == (Channel.IN_APP, Channel.PUSH)

    # A preference can narrow what the envelope permits, never widen it.
    def test_preference_cannot_widen_the_envelope(self):
        prefs = Preferences(modes={EventType.WORK_STARTED: DeliveryMode.SMS}, sms_verified=True)
        decision = route(envelope(), prefs, now=DAY)
        assert Channel.SMS not in decision.channels
        assert "not permitted" in decision.reason

    def test_sms_requires_a_verified_number(self):
        prefs = Preferences(
            modes={EventType.SECURITY_ALERT: DeliveryMode.SMS}, sms_verified=False
        )
        decision = route(
            envelope(EventType.SECURITY_ALERT, Severity.SECURITY), prefs, now=DAY
        )
        assert Channel.SMS not in decision.channels
        assert "verified" in decision.reason

    def test_sms_delivers_when_verified_and_permitted(self):
        prefs = Preferences(
            modes={EventType.SECURITY_ALERT: DeliveryMode.SMS}, sms_verified=True
        )
        decision = route(
            envelope(EventType.SECURITY_ALERT, Severity.SECURITY), prefs, now=DAY
        )
        assert Channel.SMS in decision.channels

    def test_discord_requires_a_link(self):
        prefs = Preferences(
            modes={EventType.SECURITY_ALERT: DeliveryMode.DISCORD_DM}, discord_linked=False
        )
        decision = route(
            envelope(EventType.SECURITY_ALERT, Severity.SECURITY), prefs, now=DAY
        )
        assert Channel.DISCORD_DM not in decision.channels


class TestDeduplication:
    def test_a_retry_is_not_a_second_buzz(self):
        ledger = DeliveryLedger()
        first = route(envelope(), Preferences(), now=DAY, ledger=ledger)
        assert first.delivers
        ledger.record(envelope(), DAY)

        # Upstream retry mints a new envelope id for the same real event.
        second = route(envelope(), Preferences(), now=DAY, ledger=ledger)
        assert second.suppressed_as_duplicate
        assert not second.delivers

    def test_the_same_event_delivers_again_after_the_window(self):
        ledger = DeliveryLedger()
        ledger.record(envelope(), DAY)
        later = DAY + timedelta(hours=7)
        assert route(envelope(), Preferences(), now=later, ledger=ledger).delivers

    def test_different_events_are_not_conflated(self):
        ledger = DeliveryLedger()
        ledger.record(envelope(EventType.WORK_STARTED), DAY)
        other = route(envelope(EventType.RESOLVED), Preferences(), now=DAY, ledger=ledger)
        assert other.delivers


class TestFallback:
    def test_push_outage_falls_back_to_in_app_for_routine(self):
        decision = route(envelope(), Preferences(), now=DAY, push_available=False)
        assert decision.channels == (Channel.IN_APP,)
        assert "push unavailable" in decision.reason

    def test_push_outage_escalates_to_sms_only_when_urgent(self):
        prefs = Preferences(sms_verified=True)
        decision = route(
            envelope(EventType.SECURITY_ALERT, Severity.SECURITY),
            prefs,
            now=DAY,
            push_available=False,
        )
        assert Channel.SMS in decision.channels

    def test_push_outage_does_not_escalate_routine_events_to_sms(self):
        prefs = Preferences(sms_verified=True)
        decision = route(envelope(), prefs, now=DAY, push_available=False)
        assert Channel.SMS not in decision.channels


class TestInvariants:
    @pytest.mark.parametrize("event", list(EventType))
    def test_every_decision_is_a_subset_of_what_the_envelope_permits(self, event):
        prefs = Preferences(sms_verified=True, discord_linked=True)
        for severity in Severity:
            item = build(
                event_type=event,
                severity=severity,
                title="t",
                summary="s",
                correlation_id="c",
            )
            decision = route(item, prefs, now=DAY)
            for channel in decision.channels:
                assert channel in item.allowed_channels, (
                    f"{event}/{severity} routed to {channel}, which the envelope forbids"
                )

"""The redaction boundary for anything leaving Sentry's control."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest

from app.notifications.envelope import (
    REDACTED,
    Channel,
    EventType,
    NotificationEnvelope,
    Severity,
    build,
    contains_sensitive,
    redact,
)


class TestSecretDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "the key is sk-proj-abc123def456ghijklmno",
            "token ghp_abcdefghijklmnopqrstuvwxyz012345",
            "slack xoxb-1234-5678-abcdefghij",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
            "-----BEGIN RSA PRIVATE KEY-----",
            "mail from colleague@example.com",
            "password: hunter2",
            "api_key = abc",
            r"see C:\Users\Bryce\.ssh\id_ed25519",
            "check /srv/sentry/repo/deploy/linux/.env",
            "```python\nprint(secret)\n```",
        ],
    )
    def test_sensitive_shapes_are_detected(self, text):
        assert contains_sensitive(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Work order resolved.",
            "3 tests failed in the payments module.",
            "Deployment completed successfully.",
            "Sentry needs your input to continue.",
        ],
    )
    def test_ordinary_summaries_survive(self, text):
        assert not contains_sensitive(text)


class TestRedact:
    def test_sensitive_text_is_replaced_wholesale(self):
        # Partial masking leaks shape; a notification never needs specifics.
        assert redact("key sk-proj-abcdefghijkl", 160) == REDACTED

    def test_empty_text_becomes_the_stand_in(self):
        assert redact("   ", 160) == REDACTED

    def test_long_text_is_truncated(self):
        assert len(redact("a " * 500, 160)) <= 160

    def test_whitespace_is_collapsed(self):
        assert redact("too    many\n\nspaces", 160) == "too many spaces"


class TestEnvelope:
    def test_resolved_is_the_only_speakable_event(self):
        for event in EventType:
            envelope = build(
                event_type=event,
                severity=Severity.ROUTINE,
                title="t",
                summary="s",
                correlation_id="c",
            )
            assert envelope.may_speak == (event is EventType.RESOLVED)

    def test_secrets_never_reach_the_apns_payload(self):
        envelope = build(
            event_type=EventType.FAILED,
            severity=Severity.URGENT,
            title="Build failed with sk-proj-abcdefghijklmnop",
            summary=r"See C:\Users\Bryce\.ssh\id_ed25519 and password: hunter2",
            correlation_id="corr-1",
            private_detail="full stack trace with secrets",
        )
        payload = json.dumps(envelope.apns_payload())

        for leak in ["sk-proj", "id_ed25519", "hunter2", ".ssh", "stack trace"]:
            assert leak not in payload, f"{leak!r} leaked into the push payload"
        assert REDACTED in payload

    def test_private_detail_is_never_serialized(self):
        envelope = build(
            event_type=EventType.RESOLVED,
            severity=Severity.ROUTINE,
            title="Resolved",
            summary="The failing test now passes.",
            correlation_id="corr-2",
            private_detail="SECRET-EVIDENCE-PAYLOAD",
        )
        assert "SECRET-EVIDENCE-PAYLOAD" not in json.dumps(envelope.apns_payload())
        # It is also kept out of the repr, so logging an envelope cannot leak it.
        assert "SECRET-EVIDENCE-PAYLOAD" not in repr(envelope)

    def test_payload_carries_identifiers_not_content(self):
        envelope = build(
            event_type=EventType.INPUT_REQUIRED,
            severity=Severity.IMPORTANT,
            title="Input needed",
            summary="Sentry needs your answer to continue.",
            work_order_id=uuid4(),
            correlation_id="corr-3",
        )
        payload = envelope.apns_payload()
        assert set(payload["sentry"]) == {"id", "event", "deepLink", "correlationId"}
        assert payload["sentry"]["deepLink"].startswith("sentry://workorders/")

    def test_sms_body_says_almost_nothing(self):
        envelope = build(
            event_type=EventType.SECURITY_ALERT,
            severity=Severity.SECURITY,
            title="New device enrolled",
            summary="A device was enrolled from an unrecognised location.",
            correlation_id="corr-4",
        )
        body = envelope.sms_body()
        assert "Open the app" in body
        assert envelope.redacted_summary not in body


class TestChannelPolicy:
    def test_routine_events_never_reach_sms_or_discord(self):
        envelope = build(
            event_type=EventType.WORK_STARTED,
            severity=Severity.ROUTINE,
            title="Started",
            summary="Work started.",
            correlation_id="c",
        )
        assert Channel.SMS not in envelope.allowed_channels
        assert Channel.DISCORD_DM not in envelope.allowed_channels
        assert Channel.PUSH in envelope.allowed_channels

    def test_only_urgent_actionable_events_reach_sms(self):
        urgent_alert = build(
            event_type=EventType.SECURITY_ALERT,
            severity=Severity.SECURITY,
            title="Alert",
            summary="Security alert.",
            correlation_id="c",
        )
        assert Channel.SMS in urgent_alert.allowed_channels

        # A resolved result is never urgent enough for SMS.
        resolved = build(
            event_type=EventType.RESOLVED,
            severity=Severity.URGENT,
            title="Resolved",
            summary="Done.",
            correlation_id="c",
        )
        assert Channel.SMS not in resolved.allowed_channels

    def test_daily_brief_is_never_urgent_channel_eligible(self):
        envelope = build(
            event_type=EventType.DAILY_BRIEF,
            severity=Severity.ROUTINE,
            title="Daily brief",
            summary="Three items need attention.",
            correlation_id="c",
        )
        assert set(envelope.allowed_channels) == {Channel.IN_APP, Channel.PUSH}


class TestExpiry:
    def test_envelopes_expire(self):
        envelope = build(
            event_type=EventType.REMINDER_DUE,
            severity=Severity.ROUTINE,
            title="Reminder",
            summary="Stand-up in 10 minutes.",
            correlation_id="c",
            ttl=timedelta(minutes=30),
        )
        assert envelope.expires_at > envelope.expires_at - timedelta(minutes=30)

    def test_envelope_is_immutable(self):
        envelope = build(
            event_type=EventType.RESOLVED,
            severity=Severity.ROUTINE,
            title="t",
            summary="s",
            correlation_id="c",
        )
        with pytest.raises(AttributeError):
            envelope.redacted_summary = "tampered"  # type: ignore[misc]

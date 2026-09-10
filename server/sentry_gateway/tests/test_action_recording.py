"""Every turn leaves a record of what the agent did and what it cost.

The recorder sits in the chat route, not the runtime adapter: the route already
owns the pool and the audit write, and keeping persistence out of the adapter
leaves the runtime a pure translation layer.

Two rules the tests exist to hold:
  - recording NEVER breaks a turn. Telemetry that can take down chat is worse
    than no telemetry.
  - absent numbers stay absent. A fabricated 0 reads as a free turn.
"""

from app.actions.recorder import ActionRecorder, rows_for_event
from app.agent_runtime.base import RuntimeEvent, RuntimeEventType
from datetime import datetime, timezone
from uuid import UUID

PROFILE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def ev(etype, summary="", **evidence):
    return RuntimeEvent(
        type=etype, session_id="s1", correlation_id="c1",
        occurred_at=datetime.now(timezone.utc), summary=summary, evidence=evidence,
    )


class TestToolRows:
    def test_a_tool_call_is_recorded(self):
        rows = rows_for_event(PROFILE, ev(
            RuntimeEventType.TOOL_PROGRESS, "server_status({})",
            tool="mcp__server_control__server_status", display_name="server_status",
            args={"scope": "all"}, status="in_progress", tool_call_id="t1",
        ))
        assert len(rows) == 1
        r = rows[0]
        assert r["kind"] == "tool"
        assert r["tool_name"] == "mcp__server_control__server_status"
        assert r["tool_args"] == {"scope": "all"}
        assert r["status"] == "in_progress"

    def test_a_tool_result_records_a_bounded_preview(self):
        rows = rows_for_event(PROFILE, ev(
            RuntimeEventType.TOOL_PROGRESS, "x" * 5000,
            status="completed", result="y" * 5000, tool_call_id="t1",
        ))
        assert len(rows[0]["result_preview"]) <= 2000

    def test_a_tool_event_with_nothing_to_say_is_not_recorded(self):
        assert rows_for_event(PROFILE, ev(RuntimeEventType.TOOL_PROGRESS)) == []


class TestTurnRows:
    def test_usage_is_recorded_on_completion(self):
        rows = rows_for_event(PROFILE, ev(
            RuntimeEventType.TURN_COMPLETED,
            model="qwen3.6-35b-local",
            input_tokens=28470, output_tokens=60, total_tokens=28530,
        ))
        r = rows[0]
        assert r["kind"] == "turn"
        assert (r["input_tokens"], r["output_tokens"], r["total_tokens"]) == (28470, 60, 28530)
        assert r["model"] == "qwen3.6-35b-local"

    def test_absent_usage_stays_null_not_zero(self):
        """A fabricated 0 is indistinguishable from a genuinely free turn."""
        rows = rows_for_event(PROFILE, ev(RuntimeEventType.TURN_COMPLETED, model="m"))
        r = rows[0]
        assert r["input_tokens"] is None and r["total_tokens"] is None

    def test_unobserved_model_stays_null_not_unknown(self):
        """'unknown' as a literal makes a missing value look like a real model."""
        rows = rows_for_event(PROFILE, ev(RuntimeEventType.TURN_COMPLETED, input_tokens=5))
        assert rows[0]["model"] is None

    def test_message_events_are_not_recorded(self):
        """Prompt/response text must never reach this table."""
        assert rows_for_event(PROFILE, ev(RuntimeEventType.MESSAGE, "secret text")) == []


class TestRecordingNeverBreaksATurn:
    async def test_a_failing_pool_is_swallowed(self):
        class Boom:
            def acquire(self):
                raise RuntimeError("db down")

        rec = ActionRecorder(Boom(), PROFILE)
        await rec.record(ev(RuntimeEventType.TURN_COMPLETED, model="m", input_tokens=1))
        assert rec.failed >= 1   # counted, not raised

    async def test_no_pool_is_a_no_op(self):
        rec = ActionRecorder(None, PROFILE)
        await rec.record(ev(RuntimeEventType.TURN_COMPLETED, model="m"))
        assert rec.written == 0

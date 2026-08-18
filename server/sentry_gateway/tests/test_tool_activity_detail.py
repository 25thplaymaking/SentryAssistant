"""Tool activity must carry WHAT the agent did, not just that something happened.

Captured from a real turn against this deployment: the adapter produced EIGHT
tool.progress events with empty summary and empty evidence. The cause is
normalize_event_type defaulting every unrecognised event to TOOL_PROGRESS, so
response.created / output_item.added / output_item.done all arrived as
content-free "tool progress" -- noise shaped like signal, and nothing a UI could
render beyond a spinner.

Hermes was sending the detail all along, in OpenAI Responses format:

  response.output_item.added  item={type:function_call, name:..., arguments:...}
  response.output_item.added  item={type:function_call_output, call_id, output}

These tests pin that the detail survives into the event, and that events with
nothing to say stay silent instead of emitting a blank.
"""

import json
from uuid import UUID

from app.agent_runtime.base import RuntimeEventType, RuntimeTurn
from app.agent_runtime.hermes import HermesRuntime

PROFILE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def turn():
    return RuntimeTurn(
        session_id="s1", profile_id=PROFILE, prompt="hi", correlation_id="c1"
    )


def parse(payload):
    rt = HermesRuntime.__new__(HermesRuntime)
    return rt._parse_sse_line("data: " + json.dumps(payload), turn())


# Verbatim from a real captured turn (ids shortened).
CALL_ADDED = {
    "type": "response.output_item.added", "output_index": 0,
    "item": {"id": "fc_713", "type": "function_call", "status": "in_progress",
             "name": "mcp__server_control__server_status",
             "call_id": "3ZENJ5xx", "arguments": "{}"},
    "sequence_number": 1,
}
CALL_DONE = {
    "type": "response.output_item.done", "output_index": 0,
    "item": {"id": "fc_713", "type": "function_call", "status": "completed",
             "name": "mcp__server_control__server_status",
             "call_id": "3ZENJ5xx", "arguments": '{"scope":"all"}'},
    "sequence_number": 2,
}
CALL_OUTPUT = {
    "type": "response.output_item.added", "output_index": 1,
    "item": {"id": "fco_0bd", "type": "function_call_output", "call_id": "3ZENJ5xx",
             "output": [{"type": "input_text", "text": '{"result": "16 services"}'}]},
    "sequence_number": 3,
}


class TestToolCallsCarryDetail:
    def test_a_tool_call_names_the_tool(self):
        ev = parse(CALL_ADDED)
        assert ev is not None and ev.type is RuntimeEventType.TOOL_PROGRESS
        assert ev.evidence.get("tool") == "mcp__server_control__server_status"

    def test_the_summary_is_human_readable(self):
        """A UI must be able to render this without parsing evidence."""
        ev = parse(CALL_ADDED)
        assert ev.summary and "server_status" in ev.summary

    def test_arguments_are_carried(self):
        ev = parse(CALL_DONE)
        assert ev.evidence.get("args") == {"scope": "all"}

    def test_call_id_is_carried_so_calls_and_results_can_be_paired(self):
        assert parse(CALL_ADDED).evidence.get("tool_call_id") == "3ZENJ5xx"
        assert parse(CALL_OUTPUT).evidence.get("tool_call_id") == "3ZENJ5xx"

    def test_status_distinguishes_running_from_finished(self):
        assert parse(CALL_ADDED).evidence.get("status") == "in_progress"
        assert parse(CALL_DONE).evidence.get("status") == "completed"

    def test_the_tool_result_is_carried(self):
        ev = parse(CALL_OUTPUT)
        assert "16 services" in str(ev.evidence.get("result"))

    def test_a_result_is_marked_completed(self):
        assert parse(CALL_OUTPUT).evidence.get("status") == "completed"


class TestNoBlankNoise:
    def test_lifecycle_events_do_not_masquerade_as_tool_progress(self):
        """response.created became an empty tool.progress before this fix."""
        ev = parse({"type": "response.created", "response": {"id": "r1"}})
        assert ev is None or ev.type is not RuntimeEventType.TOOL_PROGRESS

    def test_a_message_item_is_not_reported_as_a_tool(self):
        ev = parse({
            "type": "response.output_item.added",
            "item": {"type": "message", "id": "m1"},
        })
        assert ev is None or ev.type is not RuntimeEventType.TOOL_PROGRESS

    def test_no_tool_progress_event_is_ever_content_free(self):
        """The exact defect: 8 events, all empty. Any tool.progress we emit
        must say something."""
        for payload in (CALL_ADDED, CALL_DONE, CALL_OUTPUT):
            ev = parse(payload)
            if ev is not None and ev.type is RuntimeEventType.TOOL_PROGRESS:
                assert ev.summary or ev.evidence, payload


class TestNothingRegressed:
    def test_text_deltas_still_stream_as_messages(self):
        ev = parse({"type": "response.output_text.delta", "delta": "hello"})
        assert ev.type is RuntimeEventType.MESSAGE
        assert ev.summary == "hello"

    def test_completion_still_maps(self):
        assert parse({"type": "response.completed"}).type is RuntimeEventType.TURN_COMPLETED

    def test_malformed_frame_still_becomes_an_error(self):
        rt = HermesRuntime.__new__(HermesRuntime)
        ev = rt._parse_sse_line("data: {not json", turn())
        assert ev.type is RuntimeEventType.ERROR

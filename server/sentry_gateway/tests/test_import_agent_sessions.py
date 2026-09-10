"""Rolling a transcript up into a session summary must not invent usage.

These numbers become the usage/spend record, so the failure that matters is not
a crash -- it is a plausible wrong number. Each test pins one way the roll-up
could quietly overcount or undercount.
"""

import importlib.util
import json
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "deploy" / "linux" / "import-agent-sessions.py"
)
_spec = importlib.util.spec_from_file_location("import_agent_sessions", _SCRIPT)
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)


def L(**kw) -> str:
    return json.dumps(kw)


def assistant(model="claude-opus-5", ts="2026-08-18T12:00:00Z", **usage) -> str:
    return L(type="assistant", timestamp=ts, sessionId="s1",
             message={"model": model, "usage": usage})


class TestTokenTotals:
    def test_usage_sums_across_messages(self):
        s = imp.summarize_claude_transcript([
            assistant(input_tokens=100, output_tokens=10),
            assistant(input_tokens=250, output_tokens=25),
        ])
        assert s["input_tokens"] == 350
        assert s["output_tokens"] == 35

    def test_cache_tokens_stay_in_their_own_columns(self):
        """Folding cache reads into input would make a cached session look
        far more expensive than it was."""
        s = imp.summarize_claude_transcript([
            assistant(input_tokens=10, output_tokens=5,
                      cache_read_input_tokens=9000, cache_creation_input_tokens=400),
        ])
        assert s["input_tokens"] == 10
        assert s["cache_read_tokens"] == 9000
        assert s["cache_write_tokens"] == 400

    def test_missing_usage_contributes_nothing(self):
        s = imp.summarize_claude_transcript([
            L(type="assistant", sessionId="s1", message={"model": "claude-opus-5"}),
            assistant(input_tokens=7, output_tokens=3),
        ])
        assert (s["input_tokens"], s["output_tokens"]) == (7, 3)

    def test_non_integer_usage_is_ignored(self):
        s = imp.summarize_claude_transcript([assistant(input_tokens="lots", output_tokens=4)])
        assert s["input_tokens"] == 0
        assert s["output_tokens"] == 4


class TestModels:
    def test_synthetic_model_is_not_counted(self):
        """<synthetic> is locally generated -- nobody called a model for it."""
        s = imp.summarize_claude_transcript([
            assistant(model="claude-opus-5"),
            assistant(model="<synthetic>"),
        ])
        assert s["models"] == ["claude-opus-5"]

    def test_distinct_models_are_deduped_and_sorted(self):
        s = imp.summarize_claude_transcript([
            assistant(model="b-model"), assistant(model="a-model"), assistant(model="b-model"),
        ])
        assert s["models"] == ["a-model", "b-model"]


class TestTimingAndIdentity:
    def test_first_and_last_timestamps_win_regardless_of_order(self):
        s = imp.summarize_claude_transcript([
            assistant(ts="2026-08-18T12:00:00Z"),
            assistant(ts="2026-08-18T09:00:00Z"),
            assistant(ts="2026-08-18T20:00:00Z"),
        ])
        assert s["started_at"] == "2026-08-18T09:00:00Z"
        assert s["ended_at"] == "2026-08-18T20:00:00Z"

    def test_session_id_and_cwd_are_picked_up(self):
        s = imp.summarize_claude_transcript([
            L(type="user", sessionId="abc-123", cwd="/home/bishop", timestamp="2026-08-18T10:00:00Z"),
        ])
        assert s["external_id"] == "abc-123"
        assert s["project_path"] == "/home/bishop"

    def test_message_roles_are_counted_separately(self):
        s = imp.summarize_claude_transcript([
            L(type="user", sessionId="s1"), L(type="user", sessionId="s1"),
            assistant(), L(type="system", sessionId="s1"),
        ])
        assert s["user_messages"] == 2
        assert s["assistant_messages"] == 1


class TestRobustness:
    def test_a_partial_trailing_line_does_not_abort_the_import(self):
        """These files are appended to by a live process, so the last line can
        be a half-written record. Aborting would mean an ACTIVE session never
        imports -- exactly the session you most want."""
        s = imp.summarize_claude_transcript([
            assistant(input_tokens=5, output_tokens=1),
            '{"type": "assistant", "message": {"usa',
        ])
        assert s["input_tokens"] == 5

    def test_blank_and_non_dict_lines_are_skipped(self):
        s = imp.summarize_claude_transcript(["", "   ", "[1,2,3]", "null",
                                             assistant(input_tokens=2, output_tokens=1)])
        assert s["input_tokens"] == 2

    def test_empty_transcript_yields_an_empty_summary_not_an_error(self):
        s = imp.summarize_claude_transcript([])
        assert s["external_id"] is None
        assert s["input_tokens"] == 0
        assert s["models"] == []


class TestCodexIsHonestAboutNotKnowing:
    def test_unknown_schema_reports_zero_rather_than_inventing_usage(self):
        s = imp.summarize_codex_transcript([json.dumps({"weird": "shape"})])
        assert s["input_tokens"] == 0
        assert s["output_tokens"] == 0

    def test_recognisable_fields_are_still_captured(self):
        s = imp.summarize_codex_transcript([
            json.dumps({"session_id": "cx-1", "cwd": "/srv", "timestamp": "2026-08-18T10:00:00Z",
                        "role": "user", "model": "gpt-5.4-codex"}),
        ])
        assert s["external_id"] == "cx-1"
        assert s["project_path"] == "/srv"
        assert s["models"] == ["gpt-5.4-codex"]
        assert s["user_messages"] == 1

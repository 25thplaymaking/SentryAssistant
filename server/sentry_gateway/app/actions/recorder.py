"""Persist what the agent did, and what it cost.

Separate from the audit trail on purpose. ``audit_events`` answers "who was
allowed to do what" and is append-only behind a trigger; this answers "what did
the agent do inside that turn" and is operational telemetry. Putting
high-volume tool chatter inside the security record would degrade both.

PROMPT AND RESPONSE TEXT NEVER REACH THIS TABLE. Tool names, arguments and a
bounded result preview do, because those are the actions -- an action log that
omits what the action was is not a log.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from ..agent_runtime.base import RuntimeEvent, RuntimeEventType

logger = logging.getLogger(__name__)

#: A tool result can be megabytes; this column is for glancing at, not storing.
RESULT_PREVIEW_LIMIT = 2000

_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def _int_or_none(value) -> int | None:
    """Return a non-negative int, or None.

    None rather than 0 is the whole point: a fabricated zero is
    indistinguishable from a genuinely free turn, and silently understating
    spend is worse than reporting nothing for that turn. ``bool`` is excluded
    because it is an ``int`` subclass and True would become 1 token.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def rows_for_event(profile_id: UUID, event: RuntimeEvent) -> list[dict]:
    """Map one runtime event onto zero or one action rows.

    Pure, so the shape of what gets persisted is testable without a database.
    """
    evidence = event.evidence if isinstance(event.evidence, dict) else {}

    if event.type is RuntimeEventType.TOOL_PROGRESS:
        tool_name = evidence.get("tool") or evidence.get("display_name")
        result = evidence.get("result")
        # A tool event carrying neither a name nor a result says nothing; a row
        # for it would be noise in the very log meant to explain the turn.
        if not tool_name and not result:
            return []
        args = evidence.get("args")
        preview = str(result)[:RESULT_PREVIEW_LIMIT] if result else None
        return [{
            "profile_id": profile_id,
            "session_id": event.session_id,
            "correlation_id": event.correlation_id,
            "kind": "tool",
            "model": None,
            "tool_name": str(tool_name) if tool_name else None,
            "tool_args": args if isinstance(args, dict) else None,
            "result_preview": preview,
            "status": str(evidence.get("status")) if evidence.get("status") else None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }]

    if event.type is RuntimeEventType.TURN_COMPLETED:
        model = evidence.get("model")
        row = {
            "profile_id": profile_id,
            "session_id": event.session_id,
            "correlation_id": event.correlation_id,
            "kind": "turn",
            # NULL, never the string "unknown": a value we did not observe must
            # stay countable as missing rather than look like a real model.
            "model": str(model) if isinstance(model, str) and model.strip() else None,
            "tool_name": None,
            "tool_args": None,
            "result_preview": None,
            "status": "completed",
        }
        row.update({key: _int_or_none(evidence.get(key)) for key in _USAGE_KEYS})
        return [row]

    # MESSAGE / errors / lifecycle: nothing to log here. MESSAGE in particular
    # carries the assistant's text and must never be persisted.
    return []


_INSERT = """
INSERT INTO agent_actions (
    profile_id, session_id, correlation_id, kind, model,
    tool_name, tool_args, result_preview, status,
    input_tokens, output_tokens, total_tokens
) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12)
"""


class ActionRecorder:
    """Writes action rows for one turn. Never raises into the turn.

    Telemetry that can take down chat is worse than no telemetry, so every
    failure is counted and logged rather than propagated. The caller streams to
    a live browser; a database hiccup must not truncate the user's answer.
    """

    def __init__(self, pool, profile_id: UUID):
        self._pool = pool
        self._profile_id = profile_id
        self.written = 0
        self.failed = 0

    async def record(self, event: RuntimeEvent) -> None:
        if self._pool is None:
            return
        rows = rows_for_event(self._profile_id, event)
        if not rows:
            return
        try:
            async with self._pool.acquire() as conn:
                for row in rows:
                    await conn.execute(
                        _INSERT,
                        row["profile_id"], row["session_id"], row["correlation_id"],
                        row["kind"], row["model"], row["tool_name"],
                        json.dumps(row["tool_args"]) if row["tool_args"] is not None else None,
                        row["result_preview"], row["status"],
                        row["input_tokens"], row["output_tokens"], row["total_tokens"],
                    )
                    self.written += 1
        except Exception:
            self.failed += 1
            logger.warning("agent action not recorded", exc_info=True)

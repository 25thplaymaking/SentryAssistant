-- What the models actually did, and what it cost.
--
-- Distinct from audit_events on purpose. audit_events answers "who was allowed
-- to do what" and is append-only with a hard trigger; this answers "what did
-- the agent do inside that turn" and is operational telemetry. Mixing them
-- would put high-volume tool chatter inside the security record.
--
-- PROMPT TEXT IS NEVER STORED. Tool names, arguments and a bounded result
-- preview are, because those ARE the actions -- an action log that omits what
-- the action was is not a log. Arguments can name a server or a path; they are
-- not the conversation.

CREATE TABLE IF NOT EXISTS agent_actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    session_id      TEXT NOT NULL,
    correlation_id  TEXT NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    --: 'tool'  — one tool invocation or its result
    --: 'turn'  — the turn finished; carries the token accounting
    kind            TEXT NOT NULL CHECK (kind IN ('tool', 'turn')),

    --: Which model answered. NULL rather than 'unknown': a turn whose model we
    --: genuinely did not observe must be countable as such, not silently
    --: merged into a bucket that looks like a real model.
    model           TEXT,

    tool_name       TEXT,
    tool_args       JSONB,
    --: Bounded at the writer. A tool result can be megabytes.
    result_preview  TEXT,
    status          TEXT,

    --: Omitted (NULL) when the runtime did not report them. A fabricated 0 is
    --: indistinguishable from a genuinely free turn and understates spend.
    input_tokens    BIGINT,
    output_tokens   BIGINT,
    total_tokens    BIGINT
);

CREATE INDEX IF NOT EXISTS agent_actions_profile_time_idx
    ON agent_actions (profile_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS agent_actions_session_idx
    ON agent_actions (session_id, occurred_at);

--: Usage rollups scan only completed turns; keep that path off the tool rows.
CREATE INDEX IF NOT EXISTS agent_actions_turn_usage_idx
    ON agent_actions (profile_id, occurred_at DESC)
    WHERE kind = 'turn';

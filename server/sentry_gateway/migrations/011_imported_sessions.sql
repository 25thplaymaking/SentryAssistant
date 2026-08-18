-- Daily import of agent sessions from other harnesses (Claude Code, Codex).
--
-- SESSION-LEVEL ONLY, deliberately. The transcripts are the source of truth and
-- already live on disk (~6 MB and growing for four sessions); copying their full
-- content into Postgres would duplicate gigabytes to answer questions that
-- metadata answers, and would put raw prompt text -- the one thing the audit
-- design keeps OUT of the database -- into a table. transcript_path points at
-- the file for anything deeper.
--
-- Token counts are stored; MONEY IS NOT. A hardcoded price table goes stale
-- silently and then misreports spend with total confidence, which is worse than
-- not answering. Cost is computed at read time against an operator-supplied
-- price list (see deploy/linux/pricing.example.json).

CREATE TABLE IF NOT EXISTS imported_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    --: Which harness this came from: 'claude-code' | 'codex'.
    source              TEXT NOT NULL CHECK (source IN ('claude-code', 'codex')),
    --: The harness's own session id. Unique per source, so re-importing the
    --: same session updates one row instead of accumulating duplicates.
    external_id         TEXT NOT NULL,
    project_path        TEXT,
    started_at          TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    --: Distinct models seen in the transcript, synthetic entries excluded.
    models              TEXT[] NOT NULL DEFAULT '{}',
    user_messages       INTEGER NOT NULL DEFAULT 0,
    assistant_messages  INTEGER NOT NULL DEFAULT 0,
    input_tokens        BIGINT  NOT NULL DEFAULT 0,
    output_tokens       BIGINT  NOT NULL DEFAULT 0,
    cache_read_tokens   BIGINT  NOT NULL DEFAULT 0,
    cache_write_tokens  BIGINT  NOT NULL DEFAULT 0,
    transcript_path     TEXT NOT NULL,
    transcript_bytes    BIGINT NOT NULL DEFAULT 0,
    --: Cheap change detection. A session file grows as a session continues, so
    --: re-import is keyed on the file actually changing rather than on a clock.
    content_hash        TEXT NOT NULL,
    first_imported_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    imported_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT imported_sessions_source_external UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS imported_sessions_started_idx
    ON imported_sessions (started_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS imported_sessions_source_idx
    ON imported_sessions (source, started_at DESC NULLS LAST);

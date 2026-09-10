# Daily session import

`import-agent-sessions.py` reads agent transcripts from other harnesses on this
host and records one row per session in `imported_sessions`. `sentry-import.timer`
runs it daily.

| Source | Location | Status |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | verified against real data |
| Codex | `~/.codex/sessions/**/*.jsonl` | **unverified** — Codex is not installed here |

## What it stores, and what it deliberately does not

**Session metadata and token counts only.** The transcripts are already on disk
and are the source of truth. Copying their content into Postgres would duplicate
gigabytes to answer questions metadata answers, and would put raw prompt text
into the one store the audit design keeps it out of. `transcript_path` points at
the file for anything deeper.

**No money.** Token counts are recorded; spend is not. A price table baked into
the importer goes stale silently and then misreports cost with full confidence.
Copy `pricing.example.json` to `pricing.json`, fill in your own numbers, and
compute cost at read time.

Cache reads and cache writes get their own columns because providers price them
differently from fresh input — folding them into `input_tokens` would make a
heavily-cached session look far more expensive than it was.

## Re-import is keyed on content, not on a clock

Each row carries a SHA-256 of the transcript. A session file grows while the
session is live, so an active session re-imports on each run and settles once it
ends. Re-running costs nothing and never duplicates: `(source, external_id)` is
unique and the write is an upsert.

## Usage

```bash
./import-agent-sessions.py --dry-run   # what would change
./import-agent-sessions.py             # import
./import-agent-sessions.py --stats     # totals per source
journalctl -u sentry-import.service -n 20
```

## Install

```bash
docker compose exec -T postgres psql -U sentry -d sentry \
  < ../../server/sentry_gateway/migrations/011_imported_sessions.sql
sudo install -m 0644 systemd/sentry-import.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sentry-import.timer
```

The unit runs as the transcript owner (`bishop`) on purpose: the files are mode
0600 under that user's home, so running as root would read a different home and
silently import nothing.

## The Codex caveat

`summarize_codex_transcript` has never seen a real Codex transcript. It captures
the fields it recognises and returns **zero** usage rather than guessing at a
schema, so a Codex session will import its existence and timing but may report
0 tokens. Verify it against a real transcript before trusting those columns.

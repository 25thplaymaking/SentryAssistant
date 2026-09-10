#!/usr/bin/env python3
"""Import agent sessions from other harnesses into Sentry.

    ./import-agent-sessions.py            # import anything new or changed
    ./import-agent-sessions.py --dry-run  # report what would change
    ./import-agent-sessions.py --stats    # what has been imported so far

Run daily by sentry-import.timer.

SCOPE: session-level metadata and token usage, not transcript content. The
transcripts already exist on disk and are the source of truth; copying them into
Postgres would duplicate gigabytes and would put raw prompt text into the one
store the audit design deliberately keeps it out of. Rows point at the file.

MONEY: token counts are recorded, spend is NOT. A price table baked in here goes
stale silently and then reports wrong numbers with full confidence. Compute cost
at read time against your own price list -- see pricing.example.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

COMPOSE_DIR = Path(__file__).resolve().parent

CLAUDE_ROOT = Path(os.environ.get("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects"))
CODEX_ROOT = Path(os.environ.get("CODEX_SESSIONS_DIR", Path.home() / ".codex" / "sessions"))

#: Placeholder model id Claude Code writes for locally-generated messages. It is
#: not a model anyone called, so counting it would invent usage.
SYNTHETIC_MODEL = "<synthetic>"


def summarize_claude_transcript(lines) -> dict:
    """Roll one Claude Code .jsonl transcript up into a session summary.

    Pure and stream-shaped: takes an iterable of lines so it can be tested on
    literals and run on a 3 MB file without loading it whole.

    Malformed lines are SKIPPED rather than fatal. These files are appended to
    by a live process, so the last line can legitimately be a partial write --
    aborting the whole import on it would mean an active session never imports.
    """
    out = {
        "external_id": None, "project_path": None,
        "started_at": None, "ended_at": None,
        "models": set(), "user_messages": 0, "assistant_messages": 0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
    }
    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue

        if not out["external_id"] and rec.get("sessionId"):
            out["external_id"] = str(rec["sessionId"])
        if not out["project_path"] and rec.get("cwd"):
            out["project_path"] = str(rec["cwd"])

        ts = rec.get("timestamp")
        if isinstance(ts, str) and ts:
            if out["started_at"] is None or ts < out["started_at"]:
                out["started_at"] = ts
            if out["ended_at"] is None or ts > out["ended_at"]:
                out["ended_at"] = ts

        rtype = rec.get("type")
        if rtype == "user":
            out["user_messages"] += 1
        elif rtype == "assistant":
            out["assistant_messages"] += 1

        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        model = msg.get("model")
        if isinstance(model, str) and model and model != SYNTHETIC_MODEL:
            out["models"].add(model)

        usage = msg.get("usage")
        if isinstance(usage, dict):
            # Cache reads/writes are billed differently from fresh input, so they
            # are kept as separate columns rather than folded into input_tokens.
            # Merging them would make a heavily-cached session look far more
            # expensive than it was.
            for key, field in (
                ("input_tokens", "input_tokens"),
                ("output_tokens", "output_tokens"),
                ("cache_read_input_tokens", "cache_read_tokens"),
                ("cache_creation_input_tokens", "cache_write_tokens"),
            ):
                val = usage.get(key)
                if isinstance(val, int) and val > 0:
                    out[field] += val

    out["models"] = sorted(out["models"])
    return out


def summarize_codex_transcript(lines) -> dict:
    """Roll one Codex session file up into the same shape.

    UNVERIFIED against real data: Codex is not installed on this host, so this
    reads the fields it can recognise and returns zeros rather than guessing at
    a schema it has never seen. It will import a session's existence and timing;
    if token fields differ, usage lands as 0 instead of as a fabricated number.
    Fix this against a real Codex transcript before trusting its usage columns.
    """
    out = {
        "external_id": None, "project_path": None,
        "started_at": None, "ended_at": None,
        "models": set(), "user_messages": 0, "assistant_messages": 0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
    }
    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue
        for key in ("id", "session_id", "conversation_id"):
            if not out["external_id"] and rec.get(key):
                out["external_id"] = str(rec[key])
                break
        for key in ("cwd", "workdir", "project"):
            if not out["project_path"] and rec.get(key):
                out["project_path"] = str(rec[key])
                break
        for key in ("timestamp", "created_at", "ts"):
            ts = rec.get(key)
            if isinstance(ts, str) and ts:
                if out["started_at"] is None or ts < out["started_at"]:
                    out["started_at"] = ts
                if out["ended_at"] is None or ts > out["ended_at"]:
                    out["ended_at"] = ts
                break
        role = rec.get("role") or rec.get("type")
        if role == "user":
            out["user_messages"] += 1
        elif role == "assistant":
            out["assistant_messages"] += 1
        model = rec.get("model")
        if isinstance(model, str) and model:
            out["models"].add(model)
    out["models"] = sorted(out["models"])
    return out


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if CLAUDE_ROOT.is_dir():
        found += [("claude-code", p) for p in sorted(CLAUDE_ROOT.rglob("*.jsonl"))]
    if CODEX_ROOT.is_dir():
        found += [("codex", p) for p in sorted(CODEX_ROOT.rglob("*.jsonl"))]
    return found


def psql(sql: str, *params: str) -> str:
    """Run one parameterised statement via the postgres container.

    Values go through psql variables, never string interpolation: a transcript
    path or model id is untrusted text as far as SQL is concerned.
    """
    args = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentry", "-d", "sentry", "-tAq"]
    for i, val in enumerate(params, start=1):
        args += ["-v", f"p{i}={val}"]
    # SQL goes in on STDIN, not via -c. psql only interpolates :'var' when
    # reading from stdin or a file; with -c the string is passed to the server
    # verbatim and every :'p1' arrives as a syntax error.
    proc = subprocess.run(args, input=sql, cwd=COMPOSE_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "psql failed")
    return proc.stdout.strip()


def existing_hashes() -> dict[tuple[str, str], str]:
    rows = psql("SELECT source || E'\\t' || external_id || E'\\t' || content_hash FROM imported_sessions;")
    out = {}
    for line in rows.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            out[(parts[0], parts[1])] = parts[2]
    return out


def upsert(source: str, s: dict, path: Path, digest: str, size: int) -> None:
    models_literal = "{" + ",".join('"%s"' % m.replace('"', '') for m in s["models"]) + "}"
    psql(
        """
        INSERT INTO imported_sessions (
            source, external_id, project_path, started_at, ended_at, models,
            user_messages, assistant_messages, input_tokens, output_tokens,
            cache_read_tokens, cache_write_tokens, transcript_path,
            transcript_bytes, content_hash
        ) VALUES (
            :'p1', :'p2', NULLIF(:'p3',''), NULLIF(:'p4','')::timestamptz,
            NULLIF(:'p5','')::timestamptz, :'p6'::text[],
            :'p7'::int, :'p8'::int, :'p9'::bigint, :'p10'::bigint,
            :'p11'::bigint, :'p12'::bigint, :'p13', :'p14'::bigint, :'p15'
        )
        ON CONFLICT (source, external_id) DO UPDATE SET
            project_path = EXCLUDED.project_path,
            started_at = EXCLUDED.started_at,
            ended_at = EXCLUDED.ended_at,
            models = EXCLUDED.models,
            user_messages = EXCLUDED.user_messages,
            assistant_messages = EXCLUDED.assistant_messages,
            input_tokens = EXCLUDED.input_tokens,
            output_tokens = EXCLUDED.output_tokens,
            cache_read_tokens = EXCLUDED.cache_read_tokens,
            cache_write_tokens = EXCLUDED.cache_write_tokens,
            transcript_path = EXCLUDED.transcript_path,
            transcript_bytes = EXCLUDED.transcript_bytes,
            content_hash = EXCLUDED.content_hash,
            imported_at = now();
        """,
        source, s["external_id"], s["project_path"] or "",
        s["started_at"] or "", s["ended_at"] or "", models_literal,
        str(s["user_messages"]), str(s["assistant_messages"]),
        str(s["input_tokens"]), str(s["output_tokens"]),
        str(s["cache_read_tokens"]), str(s["cache_write_tokens"]),
        str(path), str(size), digest,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        print(psql(
            "SELECT source || '  sessions=' || count(*) || '  in=' || sum(input_tokens) "
            "|| '  out=' || sum(output_tokens) || '  cache_read=' || sum(cache_read_tokens) "
            "FROM imported_sessions GROUP BY source;"
        ) or "(nothing imported yet)")
        return 0

    try:
        known = existing_hashes()
    except RuntimeError as exc:
        print(f"error: cannot read imported_sessions: {exc}", file=sys.stderr)
        return 1

    imported = skipped = failed = 0
    for source, path in discover():
        try:
            size = path.stat().st_size
            digest = file_digest(path)
            summarize = summarize_claude_transcript if source == "claude-code" else summarize_codex_transcript
            with path.open("r", errors="replace") as fh:
                summary = summarize(fh)
            if not summary["external_id"]:
                summary["external_id"] = path.stem  # filename is the session id
            if known.get((source, summary["external_id"])) == digest:
                skipped += 1
                continue
            if args.dry_run:
                print(f"would import {source} {summary['external_id']} "
                      f"({summary['assistant_messages']} replies, "
                      f"{summary['input_tokens']}in/{summary['output_tokens']}out)")
                imported += 1
                continue
            upsert(source, summary, path, digest, size)
            imported += 1
        except Exception as exc:
            # One unreadable transcript must not abandon the rest of the import.
            print(f"warn: {path}: {exc}", file=sys.stderr)
            failed += 1

    verb = "would import" if args.dry_run else "imported"
    print(f"{verb} {imported}, unchanged {skipped}, failed {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

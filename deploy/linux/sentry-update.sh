#!/usr/bin/env bash
#
# Pull updates for the Sentry stack, then bring it up.
#
# Run at boot by sentry-update.service, and safe to run by hand at any time.
#
# THE GOVERNING RULE: updating is best-effort, starting is not. Every failure
# path here still leaves the stack running on the last-known-good checkout and
# exits 0. A GitHub outage, an expired key, or a diverged branch must never be
# the reason the assistant is down.
#
# What it will NOT do, and why:
#   * It never merges or rebases. Only a fast-forward is accepted. Anything else
#     means local history diverged, which is a human's problem, not a boot-time
#     one.
#   * It never touches a repo with local modifications. This box edits live
#     configs in place; discarding those to take an update would destroy
#     operator work that exists nowhere else.
#   * It never passes --remove-orphans. The `llama` service is deliberately
#     profile-gated and out of scope here; --remove-orphans would see it as an
#     orphan and stop 24GB of loaded model.
#   * It never starts the local-model profile. Starting llama is always an
#     explicit act (see docs/local-inference.md).
#
set -uo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${COMPOSE_DIR}/../.." && pwd)"
WEBUI_DIR="${SENTRY_WEBUI_DIR:-/srv/sentry/webui}"
CONF="${SENTRY_UPDATE_CONF:-/etc/sentry-update.conf}"
LOCK="/run/lock/sentry-update.lock"

# Defaults; overridden by $CONF.
SENTRY_AUTO_UPDATE="${SENTRY_AUTO_UPDATE:-0}"
SENTRY_UPDATE_REMOTE="${SENTRY_UPDATE_REMOTE:-github}"
SENTRY_REPO_BRANCH="${SENTRY_REPO_BRANCH:-25vid/sentry-foundation}"
SENTRY_WEBUI_BRANCH="${SENTRY_WEBUI_BRANCH:-frontir}"

# Logs go to STDERR on purpose. update_repo() signals "HEAD moved" by printing
# to stdout and is read with $(...), so a log line on stdout would be captured
# as that signal and make every run look like it had changes.
log() { printf '[sentry-update] %s\n' "$*" >&2; }

# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"

if [ "${SENTRY_AUTO_UPDATE}" != "1" ]; then
    log "auto-update disabled (SENTRY_AUTO_UPDATE=${SENTRY_AUTO_UPDATE}); starting stack as-is"
    cd "$COMPOSE_DIR" && docker compose up -d 2>&1 | sed 's/^/[sentry-update] /'
    exit 0
fi

# The 2>/dev/null is scoped to the braces DELIBERATELY. Writing
# `exec 9>"$LOCK" 2>/dev/null` instead applies the stderr redirect to the whole
# shell for the rest of the run -- and since every log line goes to stderr, that
# silences the entire log while still appearing to work.
mkdir -p "$(dirname "$LOCK")" 2>/dev/null || true
if { exec 9>"$LOCK"; } 2>/dev/null; then
    if ! flock -n 9 2>/dev/null; then
        log "another update is already running; nothing to do"
        exit 0
    fi
else
    log "could not open ${LOCK}; continuing without a lock"
fi

# Fast-forward one checkout. Prints "changed" on stdout when HEAD moved.
# Every refusal is a clean return, never an abort: see the governing rule.
update_repo() {
    local dir="$1" branch="$2" name="$3"
    local before after

    [ -d "$dir/.git" ] || { log "${name}: ${dir} is not a git checkout; skipped"; return 0; }
    before="$(git -C "$dir" rev-parse HEAD 2>/dev/null)" || { log "${name}: cannot read HEAD; skipped"; return 0; }

    if ! git -C "$dir" diff --quiet HEAD -- 2>/dev/null; then
        log "${name}: LOCAL MODIFICATIONS present — refusing to update (operator edits win)"
        return 0
    fi
    if [ "$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)" != "$branch" ]; then
        log "${name}: on branch '$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)', expected '${branch}' — skipped"
        return 0
    fi
    if ! git -C "$dir" remote get-url "$SENTRY_UPDATE_REMOTE" >/dev/null 2>&1; then
        log "${name}: no '${SENTRY_UPDATE_REMOTE}' remote — skipped"
        return 0
    fi
    if ! timeout 120 git -C "$dir" fetch --quiet "$SENTRY_UPDATE_REMOTE" "$branch" 2>/dev/null; then
        log "${name}: fetch failed (offline? key expired?) — keeping current checkout"
        return 0
    fi
    # --ff-only is the whole safety story: it succeeds when we are strictly
    # behind and refuses on any divergence rather than inventing a merge.
    if ! git -C "$dir" merge --ff-only --quiet "${SENTRY_UPDATE_REMOTE}/${branch}" 2>/dev/null; then
        log "${name}: not a fast-forward (local history diverged) — keeping current checkout"
        return 0
    fi

    after="$(git -C "$dir" rev-parse HEAD 2>/dev/null)"
    if [ "$before" != "$after" ]; then
        log "${name}: updated ${before:0:8} -> ${after:0:8}"
        printf 'changed'
    else
        log "${name}: already current (${after:0:8})"
    fi
    return 0
}

repo_changed="$(update_repo "$REPO_DIR" "$SENTRY_REPO_BRANCH" "sentry")"
webui_changed="$(update_repo "$WEBUI_DIR" "$SENTRY_WEBUI_BRANCH" "webui")"

cd "$COMPOSE_DIR" || { log "compose dir missing; nothing started"; exit 0; }

# Rebuild only what actually moved. The webui image builds from its own
# checkout, so a webui-only change still needs a build here even though this
# repo did not move.
if [ -n "$repo_changed" ] || [ -n "$webui_changed" ]; then
    log "changes detected — rebuilding images"
    if ! docker compose build 2>&1 | sed 's/^/[sentry-update] /'; then
        log "BUILD FAILED — starting the stack on the existing images instead"
    fi
else
    log "no changes; no rebuild"
fi

# No --remove-orphans: see the header. Absent the local-model profile, llama is
# out of scope and is left exactly as it was.
log "starting stack"
docker compose up -d 2>&1 | sed 's/^/[sentry-update] /'
log "done"
exit 0

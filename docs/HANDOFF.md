# Frontir Sentry handoff

**Updated 2026-08-21 (America/Toronto).** This file describes the current
working tree and the live deployment after the finalization and Sol
continuation passes. Dated reports under `docs/handoffs/` remain historical
evidence.

## Current architecture

- Sentry Gateway is the identity and authorization boundary
  (`server/sentry_gateway`, FastAPI, postgres on 127.0.0.1:5433).
- The Frontir WebUI (fork of nesquena/hermes-webui, `dialect=sentry`) proxies
  every panel to the Gateway as the signed-in user; the WebUI container holds
  no per-user data of its own.
- Chat: WebUI → Gateway `/api/chat/turn` → the caller's own Hermes over
  `/v1/responses`. Sentry turns fail closed without a per-user token — there
  is no shared-credential fallback.
- Default inference: Nous Portal OAuth, `deepseek-v4-flash`, for chat,
  delegation, compression, and background review. It was the least expensive
  interactive, tool-capable DeepSeek route in the authenticated catalogue when
  selected. The registry publishes all 278 compatible catalogue models plus the
  friendly default alias; model availability does not change the DeepSeek
  default.
  A raw Nous proxy for host-local clients (Tardia) is published on
  `127.0.0.1:8645`. Local Qwen (`sentry-llama-1`) is a stopped, manual
  emergency option.
- The desktop shell binds IPv6 loopback forwards (IPv4 loopback is broken on
  Bryce's machine): WebUI `[::1]:8787`, Gateway `[::1]:8090`, Server Control
  `[::1]:17443`.
- Provider connection is an in-app flow. Nous, OpenAI Codex, xAI, and MiniMax
  use browser/device OAuth through Gateway → Hermes; Anthropic uses its
  browser-plus-paste callback. The browser never receives provider tokens or
  raw CLI output. Successful sign-in immediately publishes that account's
  selectable models and persists its managed routes; disconnect removes them
  from the live picker. In **Agent → Your AI subscriptions**, the user chooses
  a model and then selects **Use in chat**. Merely connecting or viewing an
  account never changes the DeepSeek default. Qwen OAuth is retired and shown
  unavailable.
- Bryce's Windows execution node polls Gateway outbound through `[::1]:8090`.
  It exposes only named `server-work` and `enfusion` roots to the existing
  signed work-order store; no local path is stored by Gateway and no inbound
  port exists. The hidden, limited scheduled task is `Frontir Sentry Execution
  Node`; credentials are current-user DPAPI protected under local app data.
  Hermes receives fixed `workstation_status`, `workstation_run`, and
  `workstation_result` tools from the existing Server Control MCP bridge.

## What the 2026-08-21 finalization changed

**Upstream merge.** The WebUI fork was 541 commits (139 releases) behind
nesquena/hermes-webui; `frontir` now includes upstream `exp-v0.52.260`
(merge d4084971) — SSE resumption, the whole approval-card id-routing
series, compression fencing, replay-merge `_row_id` (feeds the session
importer), atomic regeneration, bundled SQLite 3.53, and more. The two
diverged lines of `frontir` (local recovery work vs deployed Agent panel)
were reconciled first (3c882db8).

**Every unwired panel now either works or says why it cannot** (39095d90):

- Insights renders the Gateway envelope (turns, tokens by model, tool calls).
- Memory reads and writes the Gateway's per-profile store (writes used to
  land in a dead file inside the WebUI container).
- Tasks/cron is fully live: the Gateway grew cron CRUD **and a scheduler
  that actually fires jobs** (27f1135, e865061) — 5-field matcher,
  `SENTRY_CRON_TIMEZONE` (set to America/New_York in the live .env; the
  containers are UTC), audit-gated fail-closed runs, `last_run_at/status/
  summary` on each job. Verified live end to end: an every-minute job fired,
  the agent answered, the summary landed in the panel's history.
- Agent panel gained an Inbox (list / mark-read / allow-listed send) over
  `/api/agent-messages`; the Gateway grew the mark-read route.
- Kanban mutations, skill content/mutations, `model/set`, provider
  credentials, and profile mutations are honest 501s under sentry, each
  naming the real mechanism, instead of raw 500s / silent dead writes.
- Sentry turns report real token usage from completion evidence; approval
  warnings point at the Agent panel queue (the only approval gate this
  deployment configures).

**Provider flip committed.** The live Nous cutover had been running as
uncommitted drift; it is now in git (988bc57) with `start-hermes.sh` running
the agent gateway and the 8645 proxy as a reaped pair.

**Sol continuation completed.** Gateway cron hardening is deployed (eb50e5d,
migration 014); the second WebUI review wave is deployed (3afce1d4); and the
interactive Nous catalogue is mirrored into the owner seed, teammate seed,
and live owner profile (0d1278f). The live pre-catalogue config is retained at
`data/hermes/personal/config.yaml.pre-nous-catalog-20260821` for rollback.

**Provider and workstation continuation completed.** Provider OAuth is deployed
at SentryAssistant `366460f` / SentryWebUI `1b94347e`. The workstation bridge
and persistent Windows node are deployed at `dc04616`, with profile binding
fixed at `fb5f341`. `SENTRY_WORKSTATION_PROFILE_ID` in the live Compose env is
the enrolled Bryce personal profile; keep it separate from the historical
`SENTRY_BOOTSTRAP_PROFILE_ID`. The installer maintains that binding
automatically on a future reinstall.

**Subscription usability continuation completed.** SentryAssistant `9dd0f7c`
joins credential connection to live route publication without a restart, while
SentryWebUI `42840d6a` gives each connected account a direct model picker and a
single explicit **Use in chat** action. Managed non-Nous catalogues persist in
the existing Hermes route registry; the curated Nous catalogue remains the
source of its 278 interactive choices. The legacy server attachment script is
break-glass only and is no longer part of the user workflow.

## Verification (2026-08-21)

- Gateway suite: **515 passed** at eb50e5d. Live re-verification rejected an
  invalid schedule with 422, fired an every-minute job through Hermes, stored
  `ok` plus the exact reply, and deleted the throwaway job.
- WebUI focused sentry suites: 75 passed; frontir harness 39/39.
- WebUI full suite on grain: **15024 passed, 0 failed, 0 errors** — fully
  green for the first time. Two long-standing lies died here: the "19
  environmental errors" carried since July were bishop's `~/.gitconfig`
  (`tag.gpgsign=true`) breaking the fixtures' throwaway repos — conftest now
  nulls `GIT_CONFIG_GLOBAL/SYSTEM` for the run (8780ccb9); and every locale
  (not just en/zh) needs new UI strings or its parity test fails.
- After 3afce1d4, the full grain run remained green: **14993 passed, 268
  environment-dependent skips, 2 xfailed, 1 xpassed, 0 failed, 0 errors**.
  The frontir browser-layer harness remained 39/39.
- Live: gateway `/health/ready` ready, all containers healthy, scheduler
  fired a real job through Hermes/deepseek and recorded `ok` + the reply.
  Hermes and Gateway each advertise 280 unique picker entries (278 catalogue
  models, the friendly alias, and `hermes-agent`), with batch and embedding
  entries absent.
- Continuation: Gateway **533 passed**; Windows node **103 total, 101 passed and
  two opt-in smoke tests skipped**. A live OpenAI Codex device flow started and
  cancelled with zero orphan processes. DeepSeek `deepseek-v4-flash` called the
  workstation tools itself and completed read-only work order
  `ce5585de-1526-4ae5-b506-3653e95e864c` with `true`. The scheduled worker
  survived a restart, has one outbound loopback Gateway connection, and has no
  listening socket.
- Subscription usability: Gateway **537 passed**. The full grain WebUI suite
  completed with **15033 passed, 296 expected skips, 2 xfailed, 1 xpassed, and
  45 subtests passed**. Browser checks covered desktop, 390px, device sign-in,
  and an explicit model change. Production is healthy with 280 unique Hermes
  choices; the connected Nous account exposes 278 selectable models with no
  route error. Chat, delegation, compression, and background review remain on
  `deepseek/deepseek-v4-flash`. Public update cache:
  `hermes-shell-source-4ef260af074a0d4c`.

## Deploy loop (corrected)

Both server checkouts now track **origin** (the `/srv/git` bare repos) — they
previously tracked `github`, so a plain `git pull` silently found nothing
after a push to origin. Push to origin from a workstation, then on grain:

```bash
cd /srv/sentry/webui && git pull --ff-only && git push github frontir
cd /srv/sentry/repo  && git pull --ff-only && git push github 25vid/sentry-foundation
cd deploy/linux && docker compose build gateway hermes server-control-mcp webui
docker compose up -d gateway server-control-mcp hermes webui
```

**Unattended operation (enabled 2026-08-21):** `sentry-update.service` runs at
boot — it fetches the **`github`** remote explicitly (workstation-independent:
push to GitHub and reboot/restart the unit to roll grain forward; ff-only,
never touches a dirty checkout, and the stack comes up even if the fetch
fails). `sentry-import.timer` imports agent-session metadata daily. Both were
installed on 2026-08-18 but left disabled until today; both are verified live
(a forced update run completed clean, and the import recorded
"imported 1, unchanged 3, failed 0").

New SQL migrations are applied out-of-band (all are idempotent):
`docker exec -i sentry-postgres-1 psql -U $POSTGRES_USER -d $POSTGRES_DB < server/sentry_gateway/migrations/NNN_*.sql`
(010–014 are applied.)

## Known remaining items

- **WebSocket `/wyvrn/Synapse` 403s in gateway logs** are Razer Synapse on
  Bryce's Windows machine port-scanning localhost through the shell's 8090
  forward. Harmless; the gateway fails it closed.
- **Interactive exec-approvals under sentry are a deliberate non-goal**: the
  Gateway drives Hermes over `/v1/responses`, and answering an approval
  mid-turn needs the runs API (`/v1/runs/{id}/approval`). This deployment
  configures no exec-approval gates, so nothing hits this today.
- **Hermes cron-delivery log noise**: Hermes' own internal cron jobs log an
  ERROR per fire ("API server uses HTTP request/response…") and then succeed
  via fallback. Cosmetic, inside Hermes.
- **Upstream PRs worth tracking** (not yet merged upstream): #6829 (empty
  chunked POST bodies behind proxies — most relevant open PR to this
  deployment), #7105 (SSE half-open thread leak, branch
  `fix/7105-sse-subscriber-lease`), #7088 (PWA false-offline recovery).
- The 8645 Nous proxy has no liveness probe beyond start-hermes.sh's
  pair-reaping (the hermes container itself now has a compose healthcheck
  against the 8642 agent API).

On this Windows host, never run the WebUI's full pytest suite (subprocess
leak; one invocation at a time, output to a file — see AGENTS.md). Run the
full suite on grain: `cd /srv/sentry/webui && .venv/bin/python -m pytest -q`.

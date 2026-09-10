# Frontir Sentry handoff

**Updated 2026-08-22 (America/Toronto).** This file describes the current
working tree and live deployment after the Chat/Work and linked-model
continuation. Dated reports under `docs/handoffs/` remain historical evidence.

## Current architecture

- Sentry Gateway is the identity and authorization boundary
  (`server/sentry_gateway`, FastAPI, postgres on 127.0.0.1:5433).
- The Frontir WebUI (fork of nesquena/hermes-webui, `dialect=sentry`) proxies
  every panel to the Gateway as the signed-in user; the WebUI container holds
  no per-user data of its own.
- Chat and Work share one transport: WebUI → Gateway `/api/chat/turn` → the
  caller's own Hermes over `/v1/responses`. Sentry turns fail closed without a
  per-user token — there is no shared-credential fallback. The session records
  its lane and preserves it through forks, hidden child sessions, compression,
  and recovery.
- **Chat** is the default for new Sentry sessions and is enforced as a
  conversational-assistant lane. The browser hides workspace and execution
  surfaces; the Gateway sends a bounded safe toolset candidate list; Hermes
  intersects it with the operator-enabled set. Chat therefore cannot gain
  files, terminal/code, browser/computer, plugins/MCP, delegation, scheduling,
  or the workstation through a crafted or stale client request.
- **Work** retains the profile-approved Hermes execution surface, including
  skills/plugins, workspace, and the existing outbound Windows node. Legacy
  sessions without lane metadata resolve to Work so historical executable
  sessions retain their actual semantics.
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
  from the live picker. The shared picker is directly available in both Chat
  and Work and separates models into linked-provider sections; disconnected
  sections are hidden. **Agent → Your AI subscriptions** now opens that picker
  instead of duplicating a selector and **Use in chat** action. Merely
  connecting or viewing an account never changes the DeepSeek default. Qwen
  OAuth is retired and shown unavailable.
- Provider subscriptions provide model routes only. They do not expose their
  host applications' private skills/plugins for import. Skills, plugins/MCP,
  memory, workspace, workstation access, and their permissions remain owned by
  Sentry/Hermes and are explained in the picker and the Chat/Work **Access**
  panel.
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

**Chat/Work and linked-model continuation completed.** SentryAssistant
`3ca953a` enforces the two runtime experiences and hides disconnected provider
routes. SentryWebUI `ae53c578` adds the two lanes, grouped linked-account model
picker, Access explanation, persistent session semantics, and inheritance for
hidden/background children. Managed non-Nous catalogues persist in the existing
Hermes route registry; the curated Nous catalogue remains the source of its 278
interactive choices. The legacy server attachment script remains break-glass
only and is not part of the user workflow.

## Verification (2026-08-22)

- Chat/Work continuation: Gateway **542 passed**. The final full Linux WebUI
  suite completed with **15041 passed, 296 skipped, 2 xfailed, 1 xpassed, and
  45 subtests passed**. Production-browser QA at 1440x1000 and 390x844 verified
  the grouped picker, DeepSeek selection, Chat's absent workspace surface,
  Work's access surface, and no mobile horizontal overflow.
- Authenticated live catalog: Chat and Work are advertised with Chat default;
  `catalog_restricted=true`; DeepSeek is present; linked sections are
  Anthropic 12, Nous Research 278, OpenAI Codex 11, plus one Sentry friendly
  route. Disconnected xAI and MiniMax are absent. Chat, delegation,
  compression, and background review remain configured on
  `deepseek/deepseek-v4-flash`.
- Live rollout: Gateway, Hermes, WebUI, Postgres, and Server Control are
  healthy; cloudflared is running; Gateway readiness reports Hermes 0.19.0
  healthy. Public `/sw.js` is 200, `no-store`, and serves cache
  `hermes-shell-source-9078c2e6769e51c3`; hosted assets contain the Chat/Work
  switch and **Choose in Chat or Work** copy.

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

# Sol handoff — finalization day, 2026-08-21

Written by Claude (Fable 5) at the end of Bryce's last subscribed session, to be
picked up cold by Sol. Everything verified is marked as such; everything
unfinished says exactly where it stopped. Read `docs/HANDOFF.md` first for the
architecture; this file is the work ledger and the queue.

---

## Sol continuation — completed later on 2026-08-21

- Gateway cron hardening commit `eb50e5d` was rebuilt and deployed. Readiness
  passed; invalid schedule creation returned 422; a throwaway every-minute job
  fired through Hermes, recorded `ok` and the exact `CRON_OK` summary, then was
  deleted.
- WebUI review fixes were recovered from `stash@{0}`, completed across all 15
  locales, committed as `3afce1d4`, mirrored to GitHub, rebuilt, and deployed.
  The four `test_sprint3.py` workspace failures were proven to be Windows-only:
  they reproduced at clean `df1ac48f` on Windows and passed on grain. Focused
  verification was 348 passed plus the four known Windows failures; the
  browser-layer harness was 39/39. The required full grain suite finished with
  **14993 passed, 268 environment-dependent skips, 2 xfailed, 1 xpassed, 0
  failed, and 0 errors**.
- The authenticated Nous catalogue exposed 373 entries. The picker now carries
  the complete interactive agent subset: 278 entries that advertise tool use
  and are not batch-only. Embeddings and batch models are deliberately absent.
  The current short default alias remains, so Hermes and Gateway each advertise
  280 unique entries including `hermes-agent`. Owner and teammate seeds were
  committed as `0d1278f`; the live owner config was updated with a dated backup.
  A real Gateway turn selected `openai/gpt-5.6-sol` and completion evidence
  reported that exact model.

The immediate queue in sections 3 and 4 is complete. Section 6 remains an
optional follow-on polish queue; Kanban remains the separately scoped product
decision described in section 5.

---

## 1. State of the world (verified end of day)

- **Live and healthy on grain** (`bishop@205.209.116.114`): gateway, webui,
  hermes (with healthcheck), postgres, cloudflared, server-control-mcp. Public
  edge `https://sentry.frontir.solutions` serves the current bundle.
- **Deployed heads**: SentryAssistant `98d189f` (branch `25vid/sentry-foundation`),
  SentryWebUI `df1ac48f` (branch `frontir`) — identical across local clone,
  `/srv/git/*` bare repos, `/srv/sentry/*` checkouts, and GitHub mirrors.
- **Test state at those heads**: gateway 505 passed; WebUI full suite on grain
  **15024 passed, 0 failed, 0 errors** (first fully green run ever).
- **Unattended operation is armed**: `sentry-update.service` (boot; fetches the
  `github` remote explicitly — push to GitHub, restart the unit or reboot, and
  grain rolls forward with no workstation), `sentry-import.timer` (daily
  session import, verified firing), `grain-backup.timer` (nightly; now includes
  `sentry-hermes-data` = the agent's home/memory/workspace, and `sentry.env`;
  restore listing verified — 1741 files).
- The cron scheduler is live and was proven end-to-end: a real job fired
  through Hermes/deepseek and recorded `ok` + the reply in `last_summary`.

## 2. What shipped today (all deployed unless marked otherwise)

1. **Upstream merge**: hermes-webui `exp-v0.52.121 → exp-v0.52.260` (541
   commits) merged into `frontir` (d4084971), after reconciling the diverged
   local/server lines (3c882db8). Semantic invariant re-verified: sentry turns
   fail closed without a per-user token; no shared-key fallback.
2. **Every dead sentry panel wired or honestly gated** (39095d90 + follow-ups):
   Insights renders the Gateway envelope; Memory reads/writes the Gateway
   store; Tasks/cron has full CRUD **and a Gateway scheduler that actually
   fires jobs** (27f1135, e865061, migration 013, `SENTRY_CRON_TIMEZONE=
   America/New_York` in live .env + compose); Agent panel gained the Inbox
   (list/mark-read/send + Gateway mark-read route); Kanban retired wholesale on
   501 (df1ac48f) — nav hidden, one message, pollers stopped; Skills is a
   read-only governance view; Settings model/provider/profile mutations 501
   with the reason; real token usage from completion evidence.
3. **Provider flip committed**: Nous Portal OAuth / `deepseek-v4-flash-0731`
   default, raw Nous proxy for Tardia on `127.0.0.1:8645`, `start-hermes.sh`
   pair-reaping (988bc57) — was live-only drift, now in git.
4. **Deploy-loop traps fixed**: server checkouts now track `origin` (they
   tracked `github`, so `git pull` after a push to origin silently found
   nothing); `conftest.py` nulls `GIT_CONFIG_GLOBAL/SYSTEM` (bishop's
   `tag.gpgsign=true` was the REAL cause of the "19 environmental errors"
   blamed on upstream since July).
5. **Adversarial review pass** (three agents: WebUI diff + dead affordances,
   gateway scheduler, deployment/logs/backups). Deployment audit: all green;
   backup gap closed same day. The other two produced the queues below.

## 3. NOT yet deployed — Sol's first task

### 3a. Gateway: commit `eb50e5d` — pushed, migration applied, REBUILD PENDING

Fixes all five review-confirmed cron bugs, 515 tests green. Migration
`014_cron_claim.sql` is **already applied** to the live database (so the boot
updater auto-deploying this commit is safe); what remains is only
`docker compose build gateway && up -d gateway` on grain — or simply the next
reboot, since sentry-update will do it.

1. Schedules validated on create/update (422 on garbage; before, an invalid
   schedule was stored enabled and silently never fired).
2. Fall-back DST: the in-memory fired-minute guard is now a UTC instant
   (PEP 495 made the two local 1:30s compare equal, swallowing one).
3. Completion is terminal: a malformed trailing SSE frame can no longer flip a
   completed run to `failed`.
4. Manual runs share the scheduler's per-job guard (409 when busy) and the
   schedule claim moved to a new `claimed_minute` column (migration 014,
   already applied live) — so a manual run can't eat that minute's scheduled
   fire.
5. Each tick is bounded (`asyncio.timeout`, 120s) so a wedged DB logs and
   retries instead of freezing the loop forever. (Note: `asyncio.wait_for`
   specifically was avoided — 3.11 swallowed-cancellation bug hung the suite.)

Deploy: on grain, `cd /srv/sentry/repo && git pull --ff-only && git push
github 25vid/sentry-foundation` (already pushed to both remotes from the
workstation), then `cd deploy/linux && docker compose build gateway && docker
compose up -d gateway`. Re-verify with a throwaway every-minute job like the
one used on finalization day.

### 3b. WebUI: `stash@{0}` "review-fixes" on the local clone (NOT committed)

In `C:\Users\Bryce\Desktop\SentryWebUI` — working tree clean at df1ac48f, the
day's second wave of review fixes is parked in **`git stash list` → stash@{0}**.
Apply with `git stash pop`. It contains (all edits complete, mid-verification
when work stopped):

- `_sentry_cron_view` ships `schedule_display` + object `schedule
  {kind, expression}` — without this, **editing any Gateway job shows an empty
  required schedule field** (worst UX bug found).
- `/api/default-model` 501-gated under sentry (it escaped the settings gate —
  Save toasted success into a config no runtime reads).
- Attachments warn-and-strip on sentry turns (they were silently dropped while
  the transcript rendered them).
- Run-now timeouts raised (server 300s, client 310s — a >15s job read as
  "gateway unreachable" while it succeeded).
- Inbox: 401-no-identity renders as unavailable, not "No messages"; send
  success keyed on a guaranteed `delivered` flag.
- Embedded **terminal 403-gated under sentry — security fix**: it opens a
  shell in the SHARED WebUI container (env, other users' server-side state).
- Profiles: `single_profile_mode` + `profiles_backend` flags; no more false
  "Gateway stopped" dots or "API key: Not configured" rows.
- Providers pane: sentry-managed message instead of a dead credential catalog.
- Cron form/detail consume `cron_backend`: only Name/Schedule/Prompt render
  under sentry (the deliver/model/skills/toast selectors LOOKED live and were
  dropped server-side); false "gateway not configured" banner suppressed; new
  `Latest status` row. Two new i18n keys in ALL 15 locales
  (`cron_last_status_label`, `cron_sentry_form_hint`) + `providers_sentry_note`
  (en-only as stashed — **add the other 14 locales or the parity tests fail**).
- Regeneration token tests added to `test_sentry_token_no_shared_fallback.py`
  (the registered-session-token path regeneration depends on was unpinned).

**Open triage** (where I was interrupted): with the stash applied, running
`tests/test_sprint3.py` locally showed 4 failures
(`*_rejects_workspace_outside_trusted_root` returning 200 not 400). Unknown
whether stash-caused or a local-Windows artifact — the same tests passed on
grain at df1ac48f. Triage: pop the stash, run that file at clean HEAD vs with
changes, on grain if possible. Nothing in the stash touches workspace
validation on inspection, but verify, don't assume. Then: locale tests +
`node tests/frontir_layer_harness.mjs` + focused sentry files, commit, push,
rebuild webui on grain, and re-run the full suite there (expect ~15040+ green).

## 4. Bryce's new request: expose the full Nous model catalog

His words: now that Nous OAuth is connected, "a way to switch between all the
model offerings would be beneficial; we peeled that back during hosting, but
now it makes sense."

The design already supports this with **zero new code** — the route table IS
the registry (`docs/plans/2026-08-18-model-routing-design.md`):

1. List what the subscription offers: `curl http://127.0.0.1:8645/v1/models`
   on grain (the raw Nous proxy; accepts any bearer, loopback only).
2. For each model worth offering, add a `model_routes` entry under
   `platforms.api_server.extra.model_routes` in the **live** config
   `/srv/sentry/repo/deploy/linux/data/hermes/personal/config.yaml`
   (provider `nous`, no api_key — OAuth resolves via the provider chain).
   **The repo's `deploy/linux/hermes/config.yaml` is only a build-time seed** —
   edit both (live for effect, seed so a reprovision keeps it), but know that
   editing the seed alone changes nothing.
3. `docker compose restart hermes` (from `/srv/sentry/repo/deploy/linux`).
4. Verify the chain: hermes `/v1/models` → gateway `GET /api/chat/models` →
   the picker. The Gateway REFUSES un-advertised models (400) rather than
   substituting, so a stale picker is loud, not silent.
5. Teammates: mirror the routes in `hermes/config.teammate.yaml`.

Notes: keep the published list to models the subscription actually serves —
the picker showing an option that 402s at the provider is the "picker lies"
failure this design exists to prevent. `deploy/linux/register-hosted-models.py`
is unrelated (it registers a Server Control dashboard tab, not model routes).
The per-model pricing file (`pricing.example.json` → `pricing.json`) feeds
cost-at-read-time for imported sessions; extend it if cost display matters.

## 5. Kanban board source — recommendation on record

If Bryce wants a real board: **project it from `work_orders`**. Migration
`003_workorders.sql` says it outright: "a runtime work board is a rebuildable
projection keyed to work_orders.id." The 13-state machine maps onto columns,
transitions are audited, and routes exist for create/get/transition. Missing:
a `GET /api/workorders` list route, a board projection behind the Gateway's
`/api/kanban/board`, and wiring card-moves to `POST /{id}/transition`. The
WebUI's kanban retirement is response-driven, so the panel lights back up on
its own once the routes stop 501ing. Do NOT build a second task store
(two-registries drift), and don't wait for a Hermes board plugin (the runtime's
probe of `/api/plugins/kanban/board` 404s on current builds). Follow-on that
makes it sing: a bounded agent tool to list/transition its own orders, so the
agent can move its card to `readyForReview` — same pattern as Server Control
MCP. This also gives Tardia's filed work orders (which pile up as invisible
drafts) a face.

## 6. Remaining review findings — accepted, smallest first

From the WebUI dead-affordance sweep (none regress anything; all are "panel
shows local-container affordances under sentry"):

- **Settings default/aux model sections**: saves now 501 honestly (after 3b),
  but the sections still render; hide them under sentry. The cleanest dialect
  signal for Settings JS: the sentry `/api/models` envelope has
  `active_provider === "sentry"`, or inject a `__WEBUI_DIALECT__` token via
  `_render_index_shell_base()` (it already substitutes process constants).
- **Workspaces panel / composer workspace chip / file tree**: operate on the
  WebUI container's filesystem; a sentry turn transmits no workspace. Hide
  under sentry.
- **Todos panel**: fed by local `todo_state` events the sentry translation
  never emits; permanently empty. Hide.
- **Extensions / Plugins / MCP settings**: local-agent-only integration;
  toggles succeed locally, never affect a sentry chat. Gate or hide. Low.
- Cosmetics: memory-unavailable branch leaves stale detail content in the main
  view; Insights still fires `/api/wiki/status` + `/api/skills/usage` it
  ignores; usage harvesting drops digit-string token counts (accept
  `isinstance(int) or digit-str`); `agent_admin.js` inline-onclick id
  interpolation would be cleaner as data-attributes + listeners.

From the gateway review, documented-not-fixed (in the scheduler docstring):
spring-forward jobs in the skipped 02:00–02:59 hour don't fire that day (no
Vixie catch-up); stepped-star day fields (`*/2` in dom) count as "restricted"
for the dom/dow OR rule, diverging from Vixie. Also: cross-process *overlap*
(not double-fire) is possible with two gateways; DB/app clock skew beyond the
tick offset could theoretically reopen a same-minute race — one DB, one
`now()`, low risk.

Upstream PRs worth pulling when they merge: **#6829** (empty chunked POST
bodies behind proxies — most relevant open PR to this deployment), **#7105**
(SSE half-open thread leak; branch `fix/7105-sse-subscriber-lease`), #7088
(PWA false-offline recovery).

## 7. Runbook (the short version; details in docs/HANDOFF.md)

```bash
# Deploy loop (push from workstation to origin, then on grain):
cd /srv/sentry/webui && git pull --ff-only && git push github frontir
cd /srv/sentry/repo  && git pull --ff-only && git push github 25vid/sentry-foundation
cd deploy/linux && docker compose build gateway webui && docker compose up -d gateway webui

# Migrations (idempotent, out-of-band; no migrations table — check objects):
docker exec -i sentry-postgres-1 psql -U sentry -d sentry \
  < /srv/sentry/repo/server/sentry_gateway/migrations/014_cron_claim.sql

# Tests:
#   gateway (fine anywhere): server/sentry_gateway/.venv python -m pytest
#   WebUI FULL suite: ONLY on grain (/srv/sentry/webui/.venv). On Windows:
#   single files only, one invocation at a time, output to a file (AGENTS.md).
# Workstation-less updates: push to GitHub, then on grain
#   sudo systemctl restart sentry-update.service   # or just reboot
```

Landmines inherited and new: IPv6 loopback forwards only (`[::1]`) on Bryce's
machine; the live Hermes config is `data/hermes/personal/config.yaml`, never
the repo seed; every new UI string needs ALL 15 locales (parity tests) and
`{0}`-bearing keys must sit after each locale block's `_label` line; kanban
501s are load-bearing (they trigger the panel retirement); `/wyvrn/Synapse`
403s in gateway logs are Bryce's Razer Synapse port-scanning through the
tunnel — ignore; the `sudo ls /root-dir/glob*` trap (calling shell expands the
glob) — wrap in `sudo bash -c`.

Good luck. The platform is self-sufficient — reboot-safe, self-updating from
GitHub, backed up nightly. What remains is polish and the two queues above.

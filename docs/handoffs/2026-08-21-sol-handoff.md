# Sol handoff — finalization day, 2026-08-21

Written by Claude (Fable 5) at the end of Bryce's last subscribed session, to be
picked up cold by Sol. Everything verified is marked as such; everything
unfinished says exactly where it stopped. Read `docs/HANDOFF.md` first for the
architecture; this file is the work ledger and the queue.

---

## Dedicated design follow-up — completed and deployed 2026-08-22

The requested follow-up with the dedicated `ui-ux-pro-max:design` guidance is
live for end users. It is a bounded refinement of the existing Chat/Work model
picker rather than a rebrand or a new settings surface.

- The final SentryWebUI head is `3248caee`. It supersedes the intermediate
  design head `1a6de7e7`, is clean in `/srv/sentry/webui`, and is present at the
  same commit in the private remote and GitHub mirror.
- The picker now has a sticky composed header: the account/runtime advisory is
  split into a strong scope statement and quieter detail, and model search stays
  available while the 299-row catalogue scrolls. The surface is 448 px on
  desktop with restrained layered shadow, contained overscroll, and stable
  scrollbar space.
- Provider and vendor headings have clearer hover feedback. The selected model
  row has a theme-token accent rail, stronger name hierarchy, and more readable
  secondary identifiers in both themes. Mobile retains 44 px search controls
  and has no horizontal overflow.
- Authenticated production QA rendered all **299** choices, with **OpenAI Codex
  (8)** first, **Nous Portal (278)** available, and
  `deepseek-v4-flash` selected. The header remained fixed after 850–900 px of
  picker scrolling; Escape returned focus to the correct desktop and mobile
  model triggers. Dark desktop, dark 390 px mobile, and light desktop captures
  are stored in
  `C:\Users\Bryce\Documents\ServerWork\output\playwright\sentry-design-pass`.
- Live QA caught and fixed one mount-order defect before handoff: the initial
  sticky wrapper was empty because its children were reparented immediately
  before the catalogue rebuild. `3248caee` keeps both controls inside the
  wrapper and includes a regression assertion for that exact failure.
- Authoritative verification on the exact final head: **15032 passed, 268
  skipped, 2 xfailed, 1 xpassed, 13 warnings, and 45 subtests passed**. The
  focused picker suite, JavaScript syntax check, and whitespace check also
  passed. A fresh production tab had no console errors.
- There are no routing or default changes. Chat, delegation, compression, and
  background review all remain provider `nous` with
  `deepseek/deepseek-v4-flash`; no Sol model is automatic. Live `/api/models`
  reports the Codex workstation runtime available with both `server-work` and
  `enfusion` workspaces.
- The release sweep found the hidden Windows bridge process connected but with
  a stale heartbeat. With zero work orders in flight, its existing scheduled
  task was restarted automatically; the same enrolled device and execution-node
  identities resumed sub-five-second heartbeats. No server action was handed
  to the user.
- All six production services are running and every configured healthcheck is
  healthy. Public `/health` is `ok`. No new QA enrollment/device was created;
  the existing signed-in browser identity was reused, the stored dark theme was
  restored after the light-only visual probe, and all QA tabs were closed.

Minimum architecture decision: use the existing picker DOM and theme tokens.
No dependency, service, screen, route, persistent state, or design-system layer
was added.

---

## Final model-picker UI pass — completed and deployed 2026-08-22

The requested final `ui-ux-pro-max` pass is live for end users. It preserves
Sentry's restrained console visual language while making the linked-model and
native Codex Work controls easier to scan, operate, and understand on desktop
and mobile.

- The final SentryWebUI feature head is `c58d3dfe`. It is committed, present in
  the private remote and GitHub mirror, pulled into `/srv/sentry/webui`, built
  into the production WebUI image, and serving at the public edge.
- **OpenAI Codex** is the first picker section, so the eight models reported by
  the signed-in native workstation runtime are immediately available. The
  large Nous catalogue is divided into collapsible vendor sections; the vendor
  containing the current selection opens automatically. This keeps Codex and
  the selected DeepSeek route visible together without removing or filtering
  any choices.
- The live authenticated picker contains all **299** available choices.
  `deepseek-v4-flash` remains selected and its `nous::deepseek` section opens
  automatically. The server defaults remain provider `nous`, model
  `deepseek/deepseek-v4-flash`; no Sol model was made automatic by this pass.
- Provider and vendor disclosures now expose their expanded state to assistive
  technology. Model rows are keyboard-operable, identify the active choice,
  and meet the mobile touch-target floor. Escape closes only the nested mobile
  picker before the parent controls and reliably returns focus to the model
  trigger on both layouts. Reduced-motion preferences are respected.
- Selected, native-runtime, primary, and fallback badges now use theme tokens
  instead of dark-only colors. The final interface was visually checked in
  desktop dark, desktop light, and mobile dark layouts; the user's stored dark
  theme was restored after the non-persistent light-theme check.
- Native Codex controls now say **Workspace on your machine**, report runtime
  state through a live region, expose correct disclosure semantics, and provide
  clear focus, disabled, busy, success, and failure behavior without adding a
  new screen or settings workflow.
- Authoritative verification on the exact final head: **15031 passed, 268
  skipped, 2 xfailed, 1 xpassed, and 45 subtests passed**. JavaScript syntax and
  whitespace checks also passed. Live desktop and mobile probes both rendered
  all 299 rows, placed Codex first, kept DeepSeek selected at scroll position
  zero, and returned focus to the correct trigger after Escape.
- The temporary browser identity used for authenticated production QA was
  revoked through the Gateway API, including immediate in-process denial; both
  of its refresh-token rows are revoked. Temporary QA tabs are closed. Two
  unused pairing codes remain only as expired audit records and cannot be
  redeemed.

Minimum architecture decision: refine the existing picker, native-runtime
controls, and theme tokens. No dependency, service, route, screen, persistent
store, or design-system layer was added.

---

## Native Codex Work continuation — completed and deployed 2026-08-22

This continuation supersedes the earlier statement that an OpenAI Codex link
contributes models only. Selecting a connected `chatgpt-plan/*` model now moves
that Sentry session into **Work** and runs the turn through the official Codex
App Server on Bryce's own workstation. DeepSeek and every non-Codex selection
continue to run through Hermes exactly as before.

- Runtime feature heads are SentryAssistant `f8add52` and SentryWebUI
  `c0e3ad92`; this handoff-only commit follows `f8add52`. Both runtime heads are
  in the private server remotes and live checkouts. Migration
  `015_native_codex_relay.sql` is applied to production.
- The hidden Windows node is installed and running from release `f8add52`. It
  starts the npm package's native `codex.exe` directly—never a Store alias,
  `cmd /c`, or `npx` wrapper—and keeps the workstation outbound-only. There is
  still no inbound listener or arbitrary client-supplied path.
- The local App Server is the authoritative model catalogue. The live picker
  exposes all eight models it reported:
  `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`,
  `gpt-daybreak-blue-latest`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, and
  `gpt-5.3-codex-spark`. Newly reported models appear without relinking. The
  complete Codex section remains hidden when the Sentry subscription link or
  live workstation runtime is unavailable.
- Native Work retains the App Server's Codex thread and project-instruction
  behavior and surfaces its streamed lifecycle, plans/review/diffs, tool
  activity, token/rate-limit events, approvals, multi-question input, MCP
  elicitation, skills, apps/connectors, MCP servers, plugins, sandbox,
  filesystem/worktrees, web search, images, subagents, hooks, configuration,
  and interrupt capability. A Sentry session is durably mapped to its local
  Codex thread so later turns resume the same native session.
- Only the named `server-work` and `enfusion` roots can be selected. Local paths
  are sanitized before event relay. Chat cannot select a Codex model; native
  Codex is Work-only, with `workspace-write` inside the selected allowlisted
  root and App Server `on-request` approvals. Approval/question responses are
  profile-bound, exact-request, audited, idempotent, and sent back through the
  node's existing outbound long poll.
- Stopping or closing a live stream now cancels the durable work order, signals
  the node, calls native `turn/interrupt`, and force-cleans the direct App
  Server child tree if graceful interruption does not finish. The locally
  persisted thread mapping survives node and browser restarts.
- End-to-end production proof selected `chatgpt-plan/gpt-5.4-mini` in
  `server-work`, streamed real App Server MCP/thread/turn/item/token events, and
  returned exactly `NATIVE_CODEX_READY`. The temporary desktop QA identity was
  revoked; the successful native work order is durable as `readyForReview`.
- Verification: Gateway **549 passed**; Windows node **106 total, 104 passed
  and 2 deliberate opt-in skips**; affected WebUI slices **173 passed**; the
  authoritative full grain WebUI suite finished with **15054 passed, 296
  expected skips, 2 xfailed, 1 xpassed, and 45 subtests passed**. The public
  sign-in shell rendered with no browser-console errors. Gateway and WebUI are
  healthy, and the public edge responds normally.
- Process hygiene is unchanged after the real App Server turn: resident
  `cmd.exe` stayed at 8 and `node.exe` stayed at 10. The two visible
  `codex.exe` processes predate this release and belong to the Codex desktop
  app; the per-turn npm App Server process and its descendants were reaped.

Honest product boundary: Sentry embeds the native capabilities OpenAI exposes
through the public Codex App Server protocol. It does not claim to clone or
import proprietary ChatGPT UI, consumer chat history/memory, or any private
service that OpenAI does not expose to third-party clients. Within that public
surface, Codex models are no longer Hermes model proxies—they use the signed-in
local Codex runtime and its native tools/features.

Minimum architecture decision: extend the existing signed work-order store and
outbound workstation node with one App Server adapter, ordered event rows, and
exact response rows. No second queue, daemon, inbound port, model proxy, or
credential store was added.

---

## Chat/Work and linked-model continuation — completed 2026-08-22

This continuation is deployed to end users, mirrored to both remotes, and
verified against the live authenticated Sentry catalogue.

- Runtime feature heads are SentryAssistant `3ca953a` and SentryWebUI
  `ae53c578`. The live checkouts are `/srv/sentry/repo` and
  `/srv/sentry/webui`; this documentation update follows the runtime commit in
  SentryAssistant only.
- Sentry now has explicit **Chat** and **Work** lanes. New Sentry sessions start
  in Chat. Existing sessions retain their recorded lane, and legacy sessions
  without a lane remain Work so an old executable session is never silently
  downgraded or misrepresented.
- Chat is the conversational-assistant lane. The browser removes workspace,
  terminal, code, browser/computer, plugin/MCP, delegation, scheduling, and
  workstation affordances. The Gateway also enforces the boundary on every
  request: it sends only a bounded safe candidate set, and Hermes intersects
  that set with the operator-enabled toolsets. A hidden child session, fork,
  compression recovery, crafted request, or stale browser cannot promote Chat
  to Work.
- Work is the execution lane. It retains the profile-approved Hermes skills,
  plugins/MCP tools, workspace, and the existing outbound Windows execution
  node. Gateway remains the signed ingress/egress and audit boundary; no second
  queue, inbound workstation listener, or arbitrary path/shell was added.
- The model picker lives directly in both lanes and groups models by linked
  account. A provider section appears only while that account is connected.
  Model choices still route through Sentry, so provider credentials never enter
  the browser. The Agent subscription cards now open this shared picker instead
  of duplicating selectors or requiring a separate **Use in chat** action.
- Provider OAuth contributes model routes, not a provider application's private
  skills or plugins. Sentry/Hermes continues to own and govern skills, plugins,
  memory, workspace, and workstation access. The UI states this boundary in the
  picker and the per-lane **Access** explanation.
- DeepSeek remains the automatic selection:
  `deepseek/deepseek-v4-flash` is still configured for chat, delegation,
  compression, and background review. The user can choose any visible linked
  model per session without changing those defaults.
- Authenticated live `/api/models` verification returned Chat/Work with Chat as
  the default, `catalog_restricted=true`, DeepSeek present, and these visible
  sections: Anthropic 12, Nous Research 278, OpenAI Codex 11, and one Sentry
  friendly route. Disconnected xAI and MiniMax sections are absent rather than
  being relabelled as generic Sentry routes.
- Verification: Gateway **542 passed**. The final full Linux WebUI suite was
  **15041 passed, 296 skipped, 2 xfailed, 1 xpassed, and 45 subtests passed**.
  Browser QA used the production HTML/JS/CSS at 1440x1000 and 390x844; Chat had
  no workspace surface or arbitrary custom-model field, Work retained its
  access surface, the grouped picker fit without horizontal overflow, and
  DeepSeek remained selected.
- Production was rebuilt from those exact heads. Gateway, Hermes, WebUI,
  Postgres, and Server Control are healthy; cloudflared is running; Gateway
  readiness reports Hermes 0.19.0 healthy. The built Hermes image contains the
  request-scoped toolset patch, validation cap, configured-toolset
  intersection, and agent-thread propagation.
- The public service worker returns 200 with `Cache-Control: no-store` and cache
  key `hermes-shell-source-9078c2e6769e51c3`. Hosted assets contain the
  Chat/Work switch, Access explanation, and **Choose in Chat or Work** copy, so
  installed and browser clients discover this release without a manual cache
  clear.

Minimum architecture decision: reuse the Gateway policy boundary, Hermes
runtime, existing outbound workstation node, session record, and model picker.
No dependency, service, persistent store, or execution path was added beyond
what the requested separation requires.

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

Follow-up: at Bryce's request, chat, delegation, compression, and background
review now default to the least expensive interactive DeepSeek route,
`deepseek/deepseek-v4-flash`. `openai/gpt-5.6-sol` and its `-pro` variant were
removed from the owner live registry and both provisioning seeds. The earlier
Sol turn above remains historical verification evidence only; it never changed
the default.

Latest preference clarification: both Sol routes were restored to the optional
picker so it again mirrors all 278 interactive Nous models. DeepSeek remains the
default for every automatic path; restoring catalogue visibility did not alter
the selection.

### End-user rollout — complete

- The current runtime feature heads are SentryAssistant `9dd0f7c` and
  SentryWebUI `42840d6a` (followed only by documentation records in
  SentryAssistant).
  Both are present in the server bare repositories, live checkouts, and GitHub
  mirrors.
- Existing owner runtime configuration and both provisioning seeds expose the
  same 278-model interactive Nous catalogue plus the friendly alias. Hermes and
  Gateway each expose 280 picker choices including `hermes-agent`.
  `deepseek/deepseek-v4-flash` remains selected for chat, delegation,
  compression, and background review. Sol is optional only.
- WebUI `38da7f4e` closes the hosted browser-update gap: Compose builds now
  derive a deterministic source version when no release tag is supplied. The
  public service worker is live with cache key
  `hermes-shell-source-37528cc811798a79`, versioned asset URLs, and
  `Cache-Control: no-store`, so existing installed/browser clients discover the
  new bundle rather than remaining on `hermes-shell-unknown`.
- Final live verification: all six services are running; Gateway, Hermes,
  Postgres, server-control, and WebUI are healthy; public `/sw.js` returns 200;
  the WebUI PWA regression suite is 47/47. The pre-existing `stress` runtime is
  an isolated test profile and is not an end-user profile.

### Provider connection and workstation continuation — complete

The two follow-up requests that arrived after the original ledger are also
deployed, mirrored to GitHub, and live for end users.

- SentryAssistant `366460f` and SentryWebUI `1b94347e` replace the Agent panel's
  server chores with in-app provider connection flows. Nous, OpenAI Codex, xAI,
  and MiniMax use device/browser OAuth; Anthropic retains its browser-plus-paste
  flow. The browser receives only the public URL/code and status. OAuth tokens,
  CLI output, and child processes stay inside Hermes. Cancel reaps the process,
  and repeated Connect cancels the prior flow. Qwen is shown as retired rather
  than offering its discontinued OAuth command.
- SentryAssistant `9dd0f7c` closes the remaining post-login gap: a successful
  connection now publishes that account's chat-model routes immediately,
  persists the managed routes atomically, and removes them from the live
  picker on disconnect. Existing authenticated accounts are activated on the
  first provider-status read. No config edit or Hermes restart is part of the
  user flow. Nous retains the deployment's filtered 278-model interactive
  catalogue rather than importing its broader batch/embedding catalogue.
- SentryWebUI `42840d6a` replaces credential-centric cards with **Your AI
  subscriptions**. Each connected account has one model selector and one **Use
  in chat** action. Rendering or connecting an account does not change the
  conversation model; DeepSeek stays selected until the user explicitly picks
  another model. Route-publication failures remain visible instead of reporting
  the account as ready.
- The hosted JS bundle now contains the OAuth start/poll/cancel flow and contains
  neither `Sign in on the server` nor `hermes auth add`. A live OpenAI Codex
  device flow reached `auth.openai.com`, returned a public user code, cancelled,
  and left zero OAuth child processes. No credential or code was printed during
  verification.
- SentryAssistant `dc04616` adds a single outbound Windows execution node to the
  existing signed work-order store. `fb5f341` binds the bridge to the profile
  actually returned by enrollment instead of the deployment's historical
  runtime bootstrap UUID. There is no inbound listener, second queue, arbitrary
  path, or general shell.
- Bryce's machine is installed at the current-user local app-data boundary as a
  hidden, limited scheduled task named `Frontir Sentry Execution Node`. Its
  rotating access/refresh credentials and work-order signing key are protected
  with Windows DPAPI. It starts at logon, restarts after failure, and survived a
  manual stop/start with the same node identity.
- Only `server-work` and `enfusion` are exposed. Each advertises `shell` and
  `claude`, with `readOnly` and bounded `workspaceWrite`; read-only is the chat
  default and write mode is only for an explicit file-change request. Local
  paths never leave the node. Elevated work, credential access, deletion,
  network shell, git push/reset/clean, and arbitrary process control remain
  unavailable.
- Live DeepSeek verification used model `deepseek-v4-flash`. The model called
  `workstation_status`, then dispatched a read-only `git rev-parse
  --is-inside-work-tree` work order through `workstation_run`. Work order
  `ce5585de-1526-4ae5-b506-3653e95e864c` reached `readyForReview`, returned
  `true`, and was reported as succeeded. The node has one established IPv6
  loopback connection to Gateway port 8090 and zero listening sockets.
- Verification totals for this continuation: Gateway **533 passed**; execution
  node **103 total, 101 passed and 2 opt-in smoke tests skipped**; focused
  provider bridge **33 passed**; focused WebUI OAuth/model surfaces **63 passed**
  locally and **24 passed** on grain. Compose validation and self-contained
  Windows publishing passed. All six production services are running; Gateway,
  Hermes, WebUI, Postgres, and Server Control are healthy.
- Subscription-usability verification: Gateway **537 passed**; the complete
  grain WebUI suite finished with **15033 passed, 296 expected skips, 2 xfailed,
  1 xpassed, and 45 subtests passed**. Desktop, 390px, sign-in, and explicit
  model-selection browser passes had no product console errors or horizontal
  overflow. After rollout all services are running and the five healthchecked
  services are healthy. Hermes advertises 280 unique choices; Nous is connected
  with 278 selectable models and no route error. Chat, delegation, compression,
  and background review all remain `deepseek/deepseek-v4-flash`. The hosted PWA
  cache is `hermes-shell-source-4ef260af074a0d4c`.

---

## 1. State of the world (verified end of day)

- **Live and healthy on grain** (`bishop@205.209.116.114`): gateway, webui,
  hermes (with healthcheck), postgres, cloudflared, server-control-mcp. Public
  edge `https://sentry.frontir.solutions` serves the current bundle.
- **Deployed feature heads**: SentryAssistant `9dd0f7c` (branch
  `25vid/sentry-foundation`, followed only by handoff documentation) and
  SentryWebUI `42840d6a` (branch `frontir`) — mirrored across the local clone,
  `/srv/git/*` bare repos, `/srv/sentry/*` checkouts, and GitHub.
- **Current test state**: Gateway **537 passed**; the complete grain WebUI run
  finished with **15033 passed, 296 expected skips, 2 xfailed, 1 xpassed, and
  45 subtests passed**. The public subscription JS and service worker both
  return 200; the old server-command copy is absent and the new cache is served
  with `no-store`.
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

## 3. Completed continuation work (historical task brief)

Everything in this section is deployed. The original task detail remains below
as an audit trail; it is not an active queue.

### 3a. Gateway: commit `eb50e5d` — deployed and verified

Fixes all five review-confirmed cron bugs, 515 tests green. Migration
`014_cron_claim.sql` is applied to the live database. The Gateway was rebuilt,
and invalid-schedule rejection plus a real every-minute Hermes job were
verified end to end.

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

### 3b. WebUI review fixes — deployed and verified

The second-wave review fixes were recovered from the historical stash,
completed across all locales, committed as `3afce1d4`, and deployed. They
include:

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

The four workspace-validation failures were reproduced at the clean prior head
on Windows and passed on grain, confirming an environment-only discrepancy.
The browser-layer harness passed 39/39 and the required full grain suite
finished with zero failures or errors.

## 4. Full Nous model catalogue — completed

The picker now exposes the full interactive, tool-capable Nous catalogue while
keeping DeepSeek selected. The implementation deliberately uses the existing
route registry; no second catalogue or selection system was added.

The end-user path is now entirely inside Sentry:

1. Open **Agent → Your AI subscriptions** and choose **Connect**.
2. Complete the provider's secure browser/device flow. Anthropic uses the same
   browser flow with a pasted confirmation value.
3. When the account says **Ready**, choose one of its models and select **Use in
   chat**.

That is the complete workflow. Users are not asked to SSH, run a Hermes command,
edit YAML, or restart a service. DeepSeek stays selected unless they perform
step 3. Disconnecting an account immediately removes its routes from the live
model list.

Internally, the route table remains the single integration registry
(`docs/plans/2026-08-18-model-routing-design.md`). The admin bridge activates and
persists provider-owned route blocks after OAuth, while Gateway continues to
reject unadvertised models rather than silently substituting another backend.
The legacy `deploy/linux/attach-subscription.py` remains only as an operator
break-glass recovery tool; it is not a support instruction or normal onboarding
path. `deploy/linux/register-hosted-models.py` is unrelated (it registers a
Server Control dashboard tab, not model routes).

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

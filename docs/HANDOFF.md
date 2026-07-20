# Frontir Sentry — handoff

**As of 2026-07-20 06:45 UTC.** Written to be picked up cold, with no access
to the conversation that produced it.

Goal of the next session: **get Hermes to a working point.** Most of the way
there — the whole path works end to end today. What remains is listed in §6,
smallest-first.

---

## 1. Status: what works right now

Verified live at the time of writing, not assumed:

| Component | State |
|---|---|
| `sentry-hermes-1` | up, runtime `hermes` 0.18.2, `healthy: true`, no degraded reason |
| `sentry-gateway-1` | up (healthy), `127.0.0.1:8090` |
| `sentry-webui-1` | up (healthy), `127.0.0.1:8787` |
| `sentry-postgres-1` | up (healthy), `127.0.0.1:5433` |
| Inference | `openai-api` / `gpt-5.4`, returning real completions |
| Desktop app | `FrontirSentry.exe`, launches, dials its own tunnel, loads the UI |
| Chat, end to end | `/api/session/new` → `/api/chat/start` → reply present in transcript |

**Chat works.** A full turn was driven through the UI's own API and the session
came back with `msgs=2` (prompt + Hermes' reply).

### Run it

1. Launch **Frontir Sentry** (Desktop / Start Menu, or
   `%LOCALAPPDATA%\SentryAssistant\app\FrontirSentry.exe`).
2. Sign in. Password = `WEBUI_PASSWORD` in
   `/srv/sentry/repo/deploy/linux/.env` on grain.silo. *(Not written here —
   this file is in git.)*

The app brings up its own SSH tunnel; nothing else needs to be running. The
login cookie persists, so subsequent launches go straight in.

---

## 2. Architecture, in one paragraph

Four containers on grain.silo (`bishop@205.209.116.114`), all bound to
**loopback only, never published**. The desktop app opens an SSH local forward
for `8090` (Gateway) and `8787` (WebUI) and renders the WebUI in a WebView2
window. The WebUI **does not load Hermes in-process** — it holds its own
`HERMES_HOME` and talks to the `hermes` container over the network
(`HERMES_WEBUI_CHAT_BACKEND=gateway` → `http://hermes:8642`). That separation is
the security boundary: the WebUI can ask Hermes to do things, but cannot edit
its skills, config, or memory.

**The forward must bind `[::1]`, not `127.0.0.1`.** IPv4 loopback is broken on
Bryce's machine — a client "connects" to `127.0.0.1` but is forced onto the
IPv6 stack, and an IPv4-bound listener never accepts. Get this wrong and you
get a live tunnel, a healthy gateway, and a blank window.

---

## 3. Where everything lives

**Repos** (both are bare repos on grain.silo, pushed over SSH):

| Repo | Bare | Working copy | Branch |
|---|---|---|---|
| SentryAssistant | `/srv/git/SentryAssistant.git` | local: `…\SentryAssistant\.worktrees\sentry-foundation`<br>server: `/srv/sentry/repo` | `25vid/sentry-foundation` |
| SentryWebUI (fork of `nesquena/hermes-webui`) | `/srv/git/sentry-webui.git` | local: `C:\Users\Bryce\Desktop\SentryWebUI`<br>server: `/srv/sentry/webui` | `frontir` |

`SentryWebUI` also has `master` = pristine upstream mirror (**never commit to
it**) and an `upstream` remote for merges.

**Deploy loop** — both server copies are now real git checkouts:

```bash
# WebUI changes
ssh bishop@205.209.116.114
cd /srv/sentry/webui && git pull --ff-only
cd /srv/sentry/repo/deploy/linux && docker compose build webui && docker compose up -d webui

# compose / gateway changes
cd /srv/sentry/repo && git pull --ff-only
cd deploy/linux && docker compose up -d
```

`/srv/sentry/repo` was a hand-copied directory until today and silently
diverged from the repo; it is now a checkout tracking `origin`, clean at
`7180893`. **Never run compose from `/home/bishop`** — that is the 25vid
production stack.

`.env` and `deploy/linux/data/` (24MB of live container state) are gitignored
and must never be committed.

---

## 4. What was built (2026-07-20)

Full detail in `docs/2026-07-20-session-report.md`. Summary:

- **Speaker → a persistent mute.** Owns `frontir-muted`; enforced by wrapping
  `window.autoReadLastAssistant`, the single entry to every speech path.
  Explicitly *not* wired to `hermes-tts-enabled`, which only controls whether
  per-message read-aloud buttons are visible — it gates an affordance, not
  audio.
- **Projects sidebar section.** A presentation of upstream's existing project
  system (`/api/projects`, `projects.json`), not a second one. No new state, no
  new handlers.
- **Icon set regenerated** from traced vector geometry (99.4% IoU vs the brand
  master). Real multi-size `favicon.ico`; `any` and `maskable` split, since the
  old single icon was being clipped by Android's circle mask.
- **Desktop shell** (`tools/SentryShell`) — WinExe + WebView2, owns the SSH
  tunnel in-process. Replaces the Scheduled Task that flashed a PowerShell
  console on every logon and redial. Child ssh is held in a Windows job object
  so it cannot outlive the app.
- **Config fixes** — `HERMES_WEBUI_BOT_NAME=Sentry`,
  `HERMES_WEBUI_SKIP_ONBOARDING=1`, branded page titles.
- **`/srv/sentry/repo` converted to a real git checkout.**

---

## 5. Landmines — read before changing anything

1. **IPv4 loopback is broken on the Windows box.** Always `[::1]`.
2. **Never run the WebUI pytest suite on the Windows machine.** It spawns real
   `server.py` subprocesses that flash consoles and **leak** when interrupted.
   Two runs left orphans that hammered the machine for ~20 minutes. Run it on
   grain.silo. Also: a background-task "completed" notification with truncated
   output means the process **detached**, not that it died — check for live
   `python.exe`/`pythonw.exe` before retrying.
3. **Editing the local WebUI clone changes nothing that `:8787` serves.** It
   must be pushed and pulled on the server. This has burned one round of review
   feedback already.
4. **The WebUI's first-run wizard is not gateway-aware.** It probes for an
   in-process Hermes and reports `agent_unavailable` on a perfectly healthy
   deployment. Suppressed via `HERMES_WEBUI_SKIP_ONBOARDING=1`; do not "fix" it
   by running the wizard, which would write a local config nothing reads.
5. **`frontir.css` / `frontir.js` are additive.** Everything is scoped under
   `[data-skin="frontir"]`; selecting another skin must restore stock Hermes
   exactly. That reversibility is the design contract — see `FRONTIR-UI.md`.
6. **`boot.js` restores `window.autoReadLastAssistant`** when a voice session
   ends, dropping any wrapper. The mute re-asserts itself from a voice
   observer; if you touch that area, keep it.
7. Hermes' live config is `deploy/linux/data/hermes/personal/config.yaml` —
   **not** `deploy/linux/hermes/config.yaml`, which is only a build-time seed.
   Editing the seed alone changes nothing, silently.

---

## 6. What is left — smallest first

### 6a. Quick wins (no decisions needed)

- **Auto-launch the shell at logon.** Currently manual. A shortcut in
  `shell:startup`, or re-purpose the now-disabled `SentryGatewayTunnel`
  Scheduled Task to launch `FrontirSentry.exe` instead of PowerShell.
- **Run the full WebUI test suite on grain.silo** and record the baseline.
  Never run so far, so the pre-existing failure count is unknown. Expect some
  red from the rebrand (they assert the product is named "Hermes").
- **Light-mode `theme_color`** — the manifest allows one value (`#09090B`), so
  iOS light-theme users get a dark status bar until boot.js re-syncs. Cosmetic,
  first paint only.

### 6b. Needs a decision from Bryce

- **Voice-session mute.** Muting currently stops the current utterance but a
  *live voice session* keeps talking. `boot.js` only returns its turn loop to
  listening from *inside* `_speakResponse()`, so skipping that call would park
  the session in `thinking` forever. Making mute fully effective means patching
  `boot.js`'s speak path — a real upstream edit, deliberately not taken
  unilaterally. **Decide: patch it, or document the limit and move on.**
- **Sparse vs full checkout on the server.** `/srv/sentry/repo` is currently a
  full checkout, so the Windows app source sits on the Linux box. Harmless and
  keeps `git pull` simple; can be narrowed to `deploy/` + `server/` if
  preferred.

### 6c. Blocked on Bryce

- **Google Calendar** needs an OAuth client registered in his Google Cloud
  account. Nothing else in the integrations phase depends on it.

### 6d. Larger, still open

- **Push notifications** — VAPID keys, a `push` handler in `sw.js`, and a
  Gateway-side sender. Scope was agreed as *all* channels (web, SMS, email),
  with the Gateway owning team identity. Unblocked; nothing started.
- **Execution routing** between grain.silo and the Windows node.
- **Team distribution** — per-user enrolment via `teams.py`, and an upstream
  merge cadence for the fork. Note that `pigout`-class passwords stop being
  adequate once teammates hold tunnel access.
- **iOS** — ships as a PWA (no Mac needed). Never installed or tested on a real
  device.

---

## 7. Verification you can re-run

```bash
# behaviour of the Frontir layer — fast, no subprocesses, safe locally
node tests/frontir_layer_harness.mjs          # in the SentryWebUI clone

# the tests our changes touch (safe locally)
bash scripts/test.sh tests/test_pwa_manifest_sw.py tests/test_session_static_assets.py -q

# live stack, from the Windows box through the tunnel
curl -s -o /dev/null -w '%{http_code}\n' http://[::1]:8787/login      # 200
curl -s http://[::1]:8090/health/ready                                # runtime healthy:true
```

Last recorded results: harness **34/34**, those two pytest files **52/52**
(they were 43 passed / 3 failed before today — three tests asserted the product
was named "Hermes").

---

## 8. Open question worth raising

`hermes-webui` is a Hermes-native UI, while `CLAUDE.md` states that provider
runtimes are replaceable adapters, not product authorities. The fork inherits
that assumption throughout. It has not caused a problem yet, and it does not
block anything below — but it is the kind of thing that gets expensive to
unwind later, so it is worth an explicit decision rather than drift.

# 2026-07-20 — Frontir Sentry: UI decisions, icon set, desktop shell

Work log for the session of 2026-07-20. Written to be read cold.

---

## 1. Test it in five minutes

**Launch:** `Frontir Sentry` on the Desktop or in the Start Menu.
(Binary: `%LOCALAPPDATA%\SentryAssistant\app\FrontirSentry.exe`)

**Password:** the value of `WEBUI_PASSWORD` in
`/srv/sentry/repo/deploy/linux/.env` on grain.silo. Deliberately not written
here — this file is in git.

The app brings its own SSH tunnel up, so nothing else needs to be running.
First launch takes a few seconds while it dials; after login the cookie
persists, so later launches go straight in.

### What to look at

| Where | What changed | What you should see |
|---|---|---|
| Window title bar | new | `Sentry — …`, never "Hermes" |
| Taskbar icon | new | Frontir shield, sharp at small size |
| Sign-in page | branded earlier | near-black canvas, shield mark, no orange gradient |
| Sidebar, top of the conversation list | **new** | a **Projects** section — vertical rows, colour dots, "＋ New project" |
| Voice console (sidebar → **Voice**) | **new** | bottom dock; the **Speaker** button now toggles to **Muted** and stays muted |
| Any shared conversation link | branded earlier | dark, shield, Frontir copy |

### Things worth actually trying

1. **Projects** — create one, rename it (double-click), right-click for the
   menu, click it to filter the list. All of it is upstream's own project
   system; if any of it breaks, that is a regression worth reporting.
2. **Speaker mute** — open Voice, click Speaker. It should read **Muted**,
   survive closing and reopening the app, and stop replies being read aloud
   in text chat. **Known limit:** during a *live voice session* it stops the
   current sentence but the session keeps talking — see §3.
3. **Skin switch** — Settings → change skin away from Frontir. Everything
   above should revert to stock Hermes exactly. That reversibility is the
   whole design contract.
4. **Kill it from Task Manager** — the ssh child should die with it. No
   orphan should be left holding port 8787.

---

## 2. What shipped

### 2.1 Speaker → a real mute

Asked for: "set it up how it would be standard."

The prior note in `FRONTIR-UI.md` §9 said to wire this to
`hermes-tts-enabled`. **That note was wrong, and the distinction matters.**
That key's entire effect is the `body.tts-enabled` class, which CSS uses to
*show* the per-message read-aloud buttons. It gates an affordance, not audio
— a mute wired to it would have hidden buttons and silenced nothing. Hermes
has no mute preference at all, so the layer owns `frontir-muted`.

Enforcement is a wrapper on `window.autoReadLastAssistant`, which is the
single entry to every speech path: `messages.js` calls it on stream
completion, and `boot.js` overrides it so a live voice session routes into
its private `_speakResponse()`. Two consequences fall out of that seam:

- `boot.js` **restores the original** on voice deactivate, dropping our
  wrapper with it — so it is re-asserted from the existing voice observer.
- A live voice session is **deliberately let through**. `boot.js` only
  returns the turn loop to listening from *inside* `_speakResponse()`, so
  skipping it would park the session in `thinking` forever.

The dock's shared `aria-pressed="true"` styling would have made a muted
button look lit; `.is-muted` overrides it a specificity step above.

### 2.2 Projects → a real sidebar section

Asked for: ship it as a real feature.

Shipped as a **promotion of what already exists**, not a second system.
Hermes already has a full project system — create / rename / delete / colour,
profile-scoped, `projects.json`, `/api/projects` — rendered as `.project-bar`
at the top of the session list.

So `frontir.js` adds no state, no API calls and no click handlers. It labels
that bar (`role=group`, `aria-label`, an `<h3>`) and `frontir.css` lays it out
as the mock's vertical section. Every upstream interaction survives, and
another skin restores the stock chip row.

`renderSessionList()` rebuilds the bar on every render, so an observer
re-labels each new one.

The section appears with your first conversation — the bar is absent only on
a profile with no projects *and* no sessions, which is also the only moment it
could say nothing true.

### 2.3 Icon set

Four defects, three of them real bugs:

| Was | Now |
|---|---|
| `frontir-icon.svg` was a 192px PNG inside an `<image>` tag while the manifest advertised `sizes:"any"` — it claimed vector and wasn't | real geometry: shield alpha traced to 5 loops / 540 vertices, **verified 99.4% IoU** by re-rasterising the polygons against the source. 7.4KB, down from 21.7KB |
| `favicon.ico` was still the Hermes caduceus, one 256px frame | Frontir mark at **16/24/32/48/64/128/256** |
| one icon was `purpose:"any maskable"`, but the shape reaches **r=54.3%** where Android's circle mask allows 40% — it was clipped | `any` and `maskable` split; maskable composed to **r=38%** |
| small sizes reused the large composition → grey mush at 16px | optical sizing on two axes: mark grows 58.8% → 82% of canvas *and* sheds its two finest shapes (triangle ≤20px, lens ≤24px) |

`favicon-32/192/512.png`, `favicon.svg`, `favicon-512.svg` and
`apple-touch-icon.png` are upstream *filenames* rebranded in place. Deliberate
departure from "upstream files untouched": `messages.js` uses
`favicon-192`/`favicon-32` as the push-notification icon and badge, so leaving
them stock shipped the caduceus in **every notification**. Binary conflicts on
merge resolve as "always take ours".

Regenerate: `python scripts/frontir/gen_icons.py` (pure PIL, deterministic).

### 2.4 Desktop shell — `tools/SentryShell`

Asked for: "do all of this in the app — I hate having a million tabs opening."

One window, the Sentry UI inside it, the SSH forward owned in-process. No
browser tabs, no PowerShell, no console.

**Why the old path flashed a window:** the Scheduled Task ran
`powershell.exe -WindowStyle Hidden`, and powershell allocates a console
*before* the window style is applied. Every logon and every redial flashed.

This project is `OutputType=WinExe` — **PE subsystem 2**, which structurally
cannot allocate a console — and spawns ssh with `CreateNoWindow` +
`UseShellExecute=false`. Verified by enumerating visible top-level windows in
the process tree: only the app's own.

**Child cleanup is a job object, not a `finally` block.** `Dispose()` only
runs on a graceful close; Task Manager, a crash, or a debugger stop all skip
it and strand ssh holding the forwarded ports — which then fails the *next*
dial via `ExitOnForwardFailure`. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` makes
the OS guarantee it. A pid file additionally reaps orphans from older builds.

Other decisions:
- adopts an existing forward rather than fighting it, so it coexists with the
  Scheduled Task if that is ever re-enabled;
- binds `[::1]` — IPv4 loopback is broken on this machine, and an IPv4 bind
  yields a live tunnel plus a blank window;
- WebView2 user-data under `%LOCALAPPDATA%` so login and skin survive restart;
- `NewWindowRequested` routed to the real browser, so a share link cannot
  spawn a second chrome-less shell with no navigation;
- unpackaged on purpose — no MSIX identity, no registration, none of the AUMID
  launch handling the packaged app needs.

**The old `SentryGatewayTunnel` Scheduled Task is now disabled.** Re-enable
with `Enable-ScheduledTask -TaskName SentryGatewayTunnel` if ever needed.

### 2.5 First-run wizard suppressed

The WebUI showed "Welcome to Hermes Web UI" and reported the agent as
`agent_unavailable` / `missing_modules: ["run_agent"]`. That check is
**in-process**: it imports `run_agent` and looks for `config.yaml` inside the
WebUI container. Neither exists there by design — the container never loads
the agent, it speaks to the `hermes` container over the network. The wizard is
not gateway-aware, so it blocked first load on a deployment where chat works.

Fixed with `HERMES_WEBUI_SKIP_ONBOARDING: "1"`, the documented escape hatch
for pre-configured deployments. `onboarding.py` honours it unconditionally and
then refuses to overwrite operator config even if the wizard is reached by a
stale bundle.

Proven working afterwards, through the UI's own API:
`/api/session/new` → session, `/api/chat/start` → stream + turn id,
`/api/sessions` → `msgs=2` (prompt + Hermes' reply).

### 2.6 Branding and config fixes

- Sign-in page `<title>` and wordmark read "Hermes". They come from
  `settings.bot_name`, whose default is
  `os.getenv("HERMES_WEBUI_BOT_NAME", "Hermes")` — so the fix is deployment
  config, `HERMES_WEBUI_BOT_NAME: Sentry` in compose, not a code change.
- The shell-load fallback page said "Hermes is restarting" on a stock slate
  canvas. Rebranded and recoloured to brand ink.
- Three manifest-route tests asserted `name == "Hermes"` and had been red
  since the rebrand. They used the name as a sentinel that the route returned
  a manifest rather than HTML, so they now read it from `manifest.json`.
  That file goes **43 passed / 3 failed → 52 passed**.

---

## 3. Known limitations

- **Muting a live voice session** stops the current utterance but not the
  session. Gating voice properly requires patching `boot.js`'s speak path —
  a real upstream edit, deliberately not taken unilaterally. *Decision
  needed.*
- **Light-mode `theme_color`** — the PWA manifest allows one value
  (`#09090B`); iOS light-theme users see a dark status bar until boot.js
  re-syncs. Cosmetic, first paint only.
- **No full-suite test result.** See §5.
- **Auto-launch at logon** is not set up. The app must be started manually.

---

## 4. Verification

Passing:

| Check | Result |
|---|---|
| `tests/test_pwa_manifest_sw.py` + `test_session_static_assets.py` | **52 passed** (was 43 passed / 3 failed) |
| `node tests/frontir_layer_harness.mjs` (new, zero-dep) | **34 assertions, 0 failures** |
| webui → `hermes:8642` → completion | real reply, `gpt-5.4` |
| login through the tunnel from Windows | 200; cookie authorises `/api/sessions`; wrong password 401 |
| shell: visible windows in process tree | only the app's own |
| shell: hard-kill (Task Manager sim) | ssh died with it, zero strays |
| icon trace fidelity | 99.4% IoU vs brand master |
| maskable safe zone | r=38.0% (limit 40%) |

The new harness executes `frontir.js` against a minimal DOM and asserts the
Projects re-labelling (including across a session-list re-render) and the full
mute contract (including the boot.js clobber/re-wrap cycle). It spawns no
subprocesses and is safe to run locally.

---

## 5. Incidents and corrections

Recorded because each cost real time and would otherwise repeat.

1. **The full pytest suite hammered the machine.** It spawns real `server.py`
   subprocesses; each flashes a console on Windows, and they **leak** when the
   parent is interrupted. Worse, the background-task harness reported
   `completed (exit code 0)` for runs that were **still going** — pytest had
   detached as windowless `pythonw.exe`. Two suites ran concurrently for ~20
   minutes. Killed 10 processes. **A "completed" notification plus truncated
   output means the process detached, not that it died.** Do not run this
   suite on the desktop; run it on grain.silo. *(Saved to memory.)*
2. **Claimed a test result I did not have.** Reported the suite as "still
   running, will confirm" when it had produced nothing — the pipeline ended in
   an `echo`, so exit 0 came from that, not pytest. Corrected in-session.
3. **Wrong lever for the bot name.** First patched a fallback in `routes.py`
   that could never fire, because `load_settings()` already supplies a default.
   Reverted; used the env var.
4. **Wrong `.env` key.** Appended `HERMES_WEBUI_PASSWORD` when compose reads
   `${WEBUI_PASSWORD}`. The tell was `docker compose` printing `Running`
   instead of `Recreated`.
5. **Wrong login endpoint when verifying.** POSTed `/login` (the HTML page)
   and got 404; the real endpoint is `/api/auth/login`.

---

## 6. Operational notes

**Deploy loop** (grain.silo):
```
push → ssh bishop@205.209.116.114
       cd /srv/sentry/webui && git pull --ff-only
       cd /srv/sentry/repo/deploy/linux && docker compose build webui && docker compose up -d webui
```

**Gotcha:** `/srv/sentry/repo` is **not a git checkout** — a hand-copied
`deploy/` + `server/`. Compose changes pushed to the repo do **not** reach the
server; they must be copied. Today's `compose.yaml` happened to be otherwise
identical, but that is luck, not a guarantee. **Worth making it a real
checkout.**

Verify headlessly with `curl 127.0.0.1:8787/...` *on the box*; editing the
local clone changes nothing that `:8787` serves.

**Commits**

`SentryWebUI` (branch `frontir`):
- `73ed3aab` speaker mute + Projects section
- `c349b393` icon set from traced geometry
- `dfe573c6` FRONTIR-UI.md — closed the three open decisions
- plus the two title/bot-name commits

`SentryAssistant` (branch `25vid/sentry-foundation`):
- `3537312` `HERMES_WEBUI_BOT_NAME=Sentry`
- `372e850` desktop shell

---

## 7. Next

Unblocked and ready:
- **Push notifications** (VAPID + `push` handler + Gateway sender for
  web/SMS/email) — Phase 4.
- Auto-launch at logon for the shell, if wanted.
- Make `/srv/sentry/repo` a real checkout.

Blocked on you:
- **Google Calendar** needs an OAuth client registered in your Google Cloud
  account. Nothing else in Phase 4 depends on it.
- **Voice-session mute** — whether to patch `boot.js`.

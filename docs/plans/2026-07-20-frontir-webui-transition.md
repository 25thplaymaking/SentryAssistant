# Frontir WebUI transition — scope, overlap, and action plan

**Status:** proposal, awaiting approval. No UI work has started.
**Date:** 2026-07-20

---

## 1. The decision

Fork `nesquena/hermes-webui` (MIT, 16.3k stars, ~7,400 commits, released
daily), reskin it to the Frontir design, add Sentry's features, and ship it as a
Windows product distributed from grain.silo, serving a **team**.

Fork is live: `ssh://bishop@205.209.116.114/srv/git/sentry-webui.git`
(`master` = untouched upstream mirror, `frontir` = our work).

### Why fork rather than adopt or rebuild

Rebuilding chat, streaming, sessions, PWA, and mobile polish from scratch is
months. Adopting unmodified means Hermes' product decisions become ours. The
fork keeps ~7,400 commits of work — including dozens of iOS Safari/WKWebView
fixes we would otherwise rediscover the hard way — at the cost of a real merge
burden. That cost is accepted and quantified in §7.

---

## 2. What hermes-webui does

Verified by reading the repo, not the marketing.

| Area | What it provides |
|---|---|
| **Chat** | SSE token streaming, message rendering, KaTeX, Mermaid, composer |
| **Sessions** | Create, project/tag organisation, archive, fork, export/import |
| **Workspace** | File browser, preview, inline edit, git detection, resizable panel |
| **Voice** | Web Speech API microphone input |
| **Profiles** | Switch agent configs without restart; per-profile gateway status |
| **Auth** | Optional password, WebAuthn/passkeys, OIDC |
| **Scheduling** | View/manage Hermes cron jobs |
| **Skills/memory** | Inline editing of agent skills and memory files |
| **Appearance** | Theme (system/dark/light) × Skin (accent palette), 12 built-in skins |
| **Mobile** | Installable PWA — `manifest.json` + `sw.js`, standalone display |
| **Extensions** | `window.registerHermesSkin()` and a documented extension API |
| **Deployment** | Docker (1/2/3-container), Nix, systemd; binds `127.0.0.1` by default |

**Gateway-backed mode** (`HERMES_WEBUI_CHAT_BACKEND=gateway`) routes browser
turns to a running Hermes API server instead of importing Hermes in-process.
This is the mode that preserves our runtime boundary.

### What it does NOT provide

- **No push notifications.** `sw.js` handles `install`, `activate`, `fetch`,
  `notificationclick` — there is **no `push` handler**, no VAPID, no
  subscription storage anywhere in the repo. Local notifications only.
- **No calendar.** Nothing.
- **No native iOS app.** No `.xcodeproj`, no Swift, no TestFlight. PWA only.
- **No multi-user model.** Auth is one shared password / one identity provider,
  not per-user identity with roles.
- **Known limitation #681:** tools triggered from the WebUI run in the *WebUI*
  container, not the agent container.

---

## 3. What Sentry does

| Area | What it provides | Where |
|---|---|---|
| **Device enrolment** | One-time codes, per-device credentials, rotating refresh tokens, revocation | `routes/auth.py` |
| **Teams** | Multi-user surface | `routes/teams.py` |
| **Work orders** | Structured task dispatch with validation | `routes/workorders.py`, `WorkOrderValidator` |
| **Execution nodes** | Registration and connection of execution machines | `routes/nodes.py`, `Sentry.Node` |
| **Coding harnesses** | Claude/Codex/Grok adapters, stream parsing, **command allowlist** | `Sentry.Node/Harnesses` |
| **Hooks** | Installer + receiver alongside claude-presence | `Sentry.Node/Hooks` |
| **Workspaces** | Registry of real Windows workspaces | `Sentry.Node/Workspaces` |
| **Notification routing** | Quiet hours, dedupe, fallback, **redaction boundary** | gateway |
| **Skill governance** | Agent-authored skills inactive until a human reviews the diff | `write_approval`, `guard_agent_created` |
| **Connector scopes** | Untrusted-content boundary | contracts |
| **Reminders** | DST-correct recurrence | gateway |
| **Daily brief** | Source gating and attribution | gateway |
| **Presence** | Assistant state surface | `Presence.cs` |
| **Backup** | Encrypted, with a *verified* restore | gateway |
| **Runtime admin** | Hermes control centre, health, pinned version | `HermesAdmin.cs` |

**Sentry's actual differentiator** is not chat. It is *governance and
execution*: who may act, on which machine, with what approval, and what gets
redacted before it leaves. hermes-webui has none of that.

---

## 4. Overlap: drop, keep, or build

| # | Capability | hermes-webui | Sentry | Verdict |
|---|---|---|---|---|
| 1 | **Authentication** | password / WebAuthn / OIDC | device enrolment + rotating tokens + teams | **DROP webui's.** Sentry Gateway is the identity authority. Bridge webui to Gateway sessions. Non-negotiable: webui auth has no per-user identity, and the team requirement needs it. |
| 2 | **Skills editing** | inline, immediate | governed, human-reviewed | **DROP webui's write path.** Keep read/browse. Inline editing directly defeats `guard_agent_created`. Route edits through Sentry's approval flow. |
| 3 | **Profiles** | webui profile switcher | Sentry profiles (`SENTRY_BOOTSTRAP_PROFILE_*`) | **MERGE onto Sentry's.** Two profile concepts in one product is a bug. |
| 4 | **Scheduling** | Hermes cron jobs | reminders, DST-correct | **KEEP BOTH, relabel.** They are different: cron = agent jobs, reminders = user-facing. Present as "Automations" vs "Reminders" so they don't read as duplicates. |
| 5 | **Sessions vs work orders** | chat sessions | structured, validated work orders | **KEEP BOTH.** A conversation is not a work order. Surface them distinctly. |
| 6 | **Workspace browser** | browses the *container* | `Sentry.Node` browses the *Windows machine* | **KEEP BOTH, disambiguate hard.** Same-looking file tree, two different machines is a serious footgun. Label the host on every view. |
| 7 | **Voice** | Web Speech API (input) | `MicrophoneService`, TTS, opt-in spoken resolutions | **MERGE.** Keep webui's input; keep Sentry's speak-only-verified-resolution policy. |
| 8 | **Appearance** | theme × skin, 12 skins | WinUI themes, high-contrast verified | **KEEP webui's, extend.** Add a Frontir skin; preserve light/dark/high-contrast and reduced-motion. |
| 9 | **Notifications** | local only, no push | routing, quiet hours, dedupe, redaction | **BUILD NEW.** Sentry owns policy; webui gains delivery. See §6. |
| 10 | **Health/status** | `/api/health/agent` | `/health/ready` + runtime block | **MERGE onto Sentry's** — it already distinguishes gateway from runtime. |
| 11 | **Calendar** | none | none | **BUILD NEW.** Nothing exists on either side. |
| 12 | **Windows shell** | browser / PWA | WinUI app, launcher, tunnel, credential store | **MERGE.** WinUI becomes the shell; see §5. |

---

## 5. Architecture

```
┌─ Windows (per user) ───────────────┐     ┌─ grain.silo ──────────────────┐
│  Sentry.exe launcher               │     │  sentry-gateway  :8090 (lo)   │
│    └ preflight, tunnel, repair     │     │    auth · teams · workorders  │
│  WinUI shell + WebView2            │─────│    nodes · notifications      │
│    └ Frontir branding, MSIX        │ SSH │  sentry-hermes   :8642 (net)  │
│  Sentry.Node (harnesses)           │ tun │  sentry-webui    :8787 (lo)   │
└────────────────────────────────────┘     │  postgres        :5433 (lo)   │
                                            └───────────────────────────────┘
        iPhone / Android ── PWA over tunnel or Tailscale ──┘
```

**Why WinUI + WebView2 rather than Electron:** it keeps the launcher, the
supervised tunnel, the DPAPI credential store, one-click enrolment, and MSIX
packaging that already exist and work. Electron discards all of it and adds a
second runtime. The same UI still serves the phone as a PWA.

**Boundary rule:** the WebUI never imports Hermes. Chat goes through
`HERMES_WEBUI_CHAT_BACKEND=gateway`. Hermes keeps no Docker socket.

---

## 6. Your requests, captured

| # | Request | Disposition |
|---|---|---|
| R1 | Enrol-this-device runs a command that automates it | **DONE** — `8daa454`, verified against the live box |
| R2 | Info area describing the power of Hermes | Phase 3 — write from measured fact (17 tools, real limits), not marketing |
| R3 | Integrations | Phase 4 — needs your provider list (§8) |
| R4 | Scheduling | Phase 3 — surface Hermes cron + Sentry reminders as distinct things |
| R5 | Calendar over connections Hermes can view | Phase 4 — **blocked**: no calendar connection exists on either side (§8) |
| R6 | Hermes works from the dedi box *or* my machine | Phase 5 — `Sentry.Node` exists; needs routing + a visible host indicator |
| R7 | Fork, reskin, ship as Windows product from our remote, for the team | Phases 1–6 |
| R8 | Frontir logo | Assets confirmed present, white-on-transparent — suits the dark design |
| R9 | Target UI (dark, orb, glass cards, bottom bar) | Phase 2, Fable subagent + UI/UX skills |
| R10 | True push notifications | Phase 4 — build; upstream has none |
| R11 | Deploy gateway-backed | Phase 1 — compose service written, **not yet applied** |

---

## 7. Action plan

### Phase 1 — Gateway-backed deploy *(no UI work; reversible)*
1. Apply the `webui` compose service (written, uncommitted, awaiting approval).
2. Generate `WEBUI_PASSWORD`; add to `.env` (never committed).
3. Build from `/srv/sentry/webui` (fork checkout, `frontir` branch).
4. Add `[::1]:8787` to the tunnel supervisor; extend `Sentry.exe` preflight to a
   fourth layer with its own exit code.
5. Verify: gateway-backed chat reaches Hermes; Hermes container unchanged;
   nothing published publicly.

**Exit:** you can use it over the tunnel and on your phone.

### Phase 2 — Frontir reskin *(Fable subagent + `ui-ux-pro-max` / `frontend-design`)*
6. Design tokens: Frontir palette, type scale, spacing, motion.
7. Shell rebuild toward the target: sidebar (agent switcher, New task / Voice /
   Scheduled, Projects, Tasks with day counts), orb voice centrepiece, floating
   glass context/goals cards, bottom icon bar.
8. Frontir logo + wordmark; app icon composited on a dark tile; PWA manifest
   icons and `theme_color`.
9. Preserve: light/dark/high-contrast, reduced motion, keyboard nav, focus rings.

**Constraint:** `static/style.css` is 7,240 lines and the skin system only
drives `--accent`. This is a rework, not a palette swap — the main source of
merge burden. Keep changes in clearly-marked Frontir blocks or separate files
so upstream merges stay mechanical.

### Phase 3 — Sentry surfaces
10. Bridge webui auth → Sentry device sessions; retire the shared password.
11. Hermes capability/info area (R2).
12. Automations (cron) + Reminders (Sentry), clearly distinct (R4).
13. Gate skills editing behind Sentry approval; browse stays open (§4.2).

### Phase 4 — New capability
14. **True push:** VAPID keypair, `push` handler in `sw.js`, subscription
    storage, sender in the Gateway so quiet hours / dedupe / **redaction** apply
    before anything leaves. iOS requires an installed PWA on 16.4+.
15. **Calendar** (R5) — blocked on §8.
16. **Integrations** (R3) — blocked on §8.

### Phase 5 — Execution routing
17. Route work between grain.silo and the Windows node; always show which host
    is acting (R6). Reuse `CommandAllowlist` and `WorkOrderValidator`.

### Phase 6 — Team distribution
18. Per-user enrolment via existing `teams.py`; roles.
19. Distribution + update channel from grain.silo.
20. Upstream merge cadence — monthly `master` sync, resolve into `frontir`.

---

## 8. Decisions — ANSWERED 2026-07-20

1. **Calendar provider → Google.** Needs a Google Cloud OAuth client
   (Calendar API scopes) registered by Bryce.
2. **Integrations** — still open, but calendar leads.
3. **Team identity → the Gateway owns it.** Explicit exception to §8.6 below.
4. **Push scope → all of them.** Web push *and* SMS *and* email.
5. **Skills editing → NOT gated.** Hermes' model is accepted; Sentry's
   `write_approval` / `guard_agent_created` requirement is dropped as a product
   constraint. See the risk note in §9.

### 8.6 Merge direction — hermes-webui is the base

**Where the two overlap, hermes-webui wins and Sentry's version is removed.**
Sentry contributes only what Hermes lacks. The revised verdicts:

| # | Capability | Winner | Action |
|---|---|---|---|
| 1 | Authentication / teams | **Sentry** *(explicit exception)* | Gateway owns identity. Bridge webui auth to Gateway device sessions; retire the shared password. |
| 2 | Skills editing | **Hermes** | Keep inline editing. Drop the Sentry gating requirement. |
| 3 | Profiles | **Hermes** | Retire Sentry's profile concept; keep `SENTRY_BOOTSTRAP_PROFILE_*` only as the Hermes profile it maps to. |
| 4 | Scheduling | **Hermes** | Adopt Hermes cron. Retire Sentry's reminder scheduler. **Verify first** that cron covers DST-correct recurrence — if it does not, that capability is a gap, not a duplicate. |
| 5 | Sessions vs work orders | **Hermes for sessions** | Keep work orders ONLY for execution-node dispatch, which Hermes has no equivalent for. |
| 6 | Workspace browser | **Hermes** | Keep Hermes' browser. `Sentry.Node`'s workspace registry survives only as the host-side half of execution routing. Label the host on every view. |
| 7 | Voice | **Hermes** | Adopt Web Speech input. Retire Sentry's `MicrophoneService`/TTS unless the speak-only-verified-resolution policy is still wanted. |
| 8 | Appearance | **Hermes** | Reskin in place (Phase 2). |
| 9 | Notifications | **Sentry** *(no Hermes equivalent)* | Not an overlap. Build push/SMS/email; Gateway owns routing and redaction. |
| 10 | Health/status | **Hermes** | Adopt `/api/health/agent`; keep Gateway `/health/ready` for the launcher's layer attribution. |
| 11 | Calendar | — | New build, Google. |
| 12 | Windows shell | **Sentry** *(no Hermes equivalent)* | WinUI + WebView2. |

**Net effect:** Sentry stops being a product with its own UI and becomes the
control plane — identity, teams, execution nodes, notification routing,
work orders — behind a Hermes-native front end.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Merge burden — upstream ships daily | `master` stays pristine; contain changes; monthly sync; expect conflicts in `style.css` |
| Two file browsers, two machines | Label the host on every file view; never render them identically |
| Governance eroded by upstream features | Review each merge for new agent-write paths |
| WebUI tools run in the WebUI container (#681) | Gateway-backed chat routes execution to Hermes; verify per feature, don't assume |
| Push on iOS is weaker than APNs | Requires installed PWA on 16.4+; if that proves insufficient, native is a separate project |
| Fork drifts into an unmaintainable rewrite | Prefer extension APIs (`registerHermesSkin`) and additive files over editing core |

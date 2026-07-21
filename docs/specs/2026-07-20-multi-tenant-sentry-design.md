# Multi-Tenant Sentry — A Private Agent Per Person (Design)

> **Status (2026-07-20): FOLDED INTO the authoritative plan — not a competing plan.**
> The decision this analysis informs — the hermes-webui fork becomes Sentry's early
> web-gateway client (Approach A) — is recorded authoritatively in
> `docs/plans/2026-07-19-sentry-hermes-os-integration.md` (see its "2026-07-20
> Reconciliation" section). Follow that plan. Use this doc only for the as-found
> evidence and component detail below.

**Date:** 2026-07-20 · **Status:** draft for review · **Author:** Bryce + Claude
**Repos in scope:** `SentryAssistant` (Gateway, worktree `25vid/sentry-foundation`, `server/sentry_gateway`) · `SentryWebUI` (`frontir` branch)
**Decisions locked before drafting:** Approach **A** (Gateway is the multiplexer) · **fix** the dead panels, don't hide them · full per-user routing · scale **2–4 people** (inner circle) · inter-agent channel is **agent-autonomous but gated** (allow-listed pairs, redacted at the boundary).

---

## 1. Problem & goals

Sentry is to be a **team** personal assistant. Multiple people each get their **own private agent**. Hard requirements:

1. **Privacy between people.** One person's agent must never read or leak another's memory, skills, sessions, or tasks.
2. **The management surfaces must actually work** — Skills, Kanban, Memory, Cron, Profiles — scoped to the logged-in person, on real agent data (not the dead/500 panels of the current gateway-backed deployment).
3. **A gated channel for agents to talk to each other** — autonomous, but allow-listed and redacted so an agent can pass a message without exposing its own context.
4. **One shared WebUI** on grain.silo; each teammate logs in as themselves.

Non-goals (this spec): OIDC/Authentik SSO (schema reserves `users.external_id` for it — out of scope now); mobile/PWA multi-user; billing; >~10 users.

## 2. Current state (as-found, with evidence)

- **Chat bypasses the Gateway.** `deploy/linux/compose.yaml:137-139`: `HERMES_WEBUI_CHAT_BACKEND=gateway`, `HERMES_WEBUI_GATEWAY_BASE_URL=http://hermes:8642` — the WebUI talks *directly* to a single shared Hermes container's runs API. The Sentry Gateway (`:8090`) is **not** in the chat path today; it owns enrolment/auth/workorders only.
- **The WebUI is Hermes-native and gateway-agnostic for management.** Skills/Kanban/Memory/Cron/Profiles reach into a *local* agent package (`hermes_cli`/`agent`/`tools`/`cron`) that is **not installed** in the webui container → every such panel is dead/500 (Kanban leaks `No module named 'hermes_cli'`; Skills 500s on unguarded `from agent.skill_utils`; Profiles list returns a default-only stub while create silently orphans a dir). Full per-panel audit: `SentryWebUI` scratchpad functional audit, 2026-07-20.
- **Auth is a single shared password** (`HERMES_WEBUI_PASSWORD`) with **no per-user identity** — everyone who logs in is the same account.
- **The Gateway already has the multi-tenant foundation** (this is the pivotal finding):
  - Per-profile upstream resolution, **fail-closed**: `HermesRuntime._instance(profile_id)` → `dict[UUID, HermesInstance]`, raises `UnknownProfileError` on a miss rather than falling back (`agent_runtime/hermes.py:90-123`). Each `HermesInstance` carries its own `base_url` + `api_key` + `profile_name` (`hermes.py:73-87`). Consumed by `send_turn` (`hermes.py:234-269`).
  - `AgentRuntime` Protocol is the swap boundary; every method is keyed by `profile_id`/`SessionScope` (`agent_runtime/base.py:144-160, 66-74`).
  - `profiles` is first-class: `runtime_home TEXT NOT NULL UNIQUE`, one personal profile per user, teams get their own profile (`migrations/002_profiles_teams.sql:5-19`).
  - Identity reaches every handler: access tokens embed `pid` (`auth/tokens.py:118-135`); `require_caller` builds `Caller(user_id, device_id, profile_id)` for all non-health routes (`routes/deps.py:45-71`).
  - Skill governance lifecycle exists and is per-profile; `attempted_cross_profile_access` blocks approval (`skills/lifecycle.py`, `tests/test_skill_governance.py`; `migrations/003_workorders.sql:109-127`).
  - Egress redaction envelope + signed single-use work-order dispatch exist (`notifications/envelope.py`, `auth/work_order_signing.py`).
- **The gaps** (what is NOT built):
  1. No **persisted** profile→endpoint mapping — `profiles` stores `runtime_home` but no `base_url`/`port`/`api_key`; the mapping lives only in process memory and only the bootstrap profile is registered at startup (`main.py:28-44`). No admin/provisioning path calls `runtime.register()`.
  2. No **client-facing chat/turn route** — nothing invokes `send_turn`; only `capabilities()` is wired (`main.py:143,166`; `admin.py:67`). `/api/admin/hermes` is read-only by design (`routes/admin.py:1-10`).
  3. No **per-user WebUI login** and no repointing of the WebUI onto the Gateway.
  4. No **inter-agent messaging** primitive (`supports_delegation` is a reported-only flag defaulting `False`).

## 3. Target architecture

```
                         grain.silo
 teammate ─HTTPS─▶ sentry-webui ─────auth (per-user)─────▶ sentry-gateway :8090
 (own login)          │  chat + management                    │  resolves caller.profile_id
                      │  (Gateway client)                      │  routes per profile ↓
                      │                            ┌───────────┼─────────────────────┐
                      │                     sentry-hermes-bryce  sentry-hermes-alice  …
                      │                       (home: bryce)        (home: alice)
                      └── logged-in user's profile is the ONLY identity the UI holds
                                    ▲
              gated, redacted, allow-listed inter-agent channel (Gateway-mediated)
```

**Principle:** the WebUI becomes a **thin multi-tenant Gateway client**. The Gateway is the single source of truth for *who you are* and *which agent is yours*. Hermes containers are per-profile runtimes and never addressed directly by the browser.

**Isolation is enforced at three independent layers** (defense in depth):
1. **Process + home per person** — a dedicated `sentry-hermes-<user>` container with its own `HERMES_HOME`; Hermes' native per-process profile isolation, no runtime patching.
2. **Gateway routes by `caller.profile_id`, fail-closed** — an unregistered/mismatched profile errors rather than leaking into another agent (mechanism already exists).
3. **The WebUI only ever exposes the logged-in user's profile** — it holds no other user's identity, home, or endpoint.

**Governance is honored, not bypassed.** Because login is now per-user, every skill/memory write is attributable to a real person and lands only in their own profile; the Skills panel routes through the Gateway's existing proposal→approve→canary→active lifecycle. (This is the resolution of the earlier "shared-password = unattributable RCE into the agent" objection — identity restores the `write_approval` boundary.)

## 4. Components to build (mapped to the gaps)

| # | Component | Repo / area | Builds on |
|---|---|---|---|
| C1 | **Persist profile→endpoint** — migration adding `base_url`, `port`, encrypted `api_key` (or a `runtimes` table) beside `runtime_home`; load into the registry at startup + on provision | Gateway `migrations/`, `agent_runtime/hermes.py`, `main.py` | existing `profiles`, `HermesInstance` |
| C2 | **Client chat route** — `POST /v1/responses` (+ streaming) requiring `require_caller`, resolving `caller.profile_id` → `send_turn`, with audit | Gateway `routes/` (new), `agent_runtime/hermes.py` | `send_turn` (built), `Caller` |
| C3 | **Per-user WebUI login** — replace shared password with Gateway-token auth; WebUI session = a Gateway access token; repoint `GATEWAY_BASE_URL` at `sentry-gateway:8090` | SentryWebUI auth + gateway client | Gateway `auth/*`, enrolment |
| C4 | **Profile-scoped management routes** — Gateway client APIs for Skills (via governance lifecycle), Kanban, Memory, Cron, Profiles; WebUI panels call these instead of local agent libs | Gateway `routes/` (new), SentryWebUI panels | `skills/lifecycle.py`, per-profile scoping |
| C5 | **Provisioning flow** — repeatable "add a teammate": create user+personal profile+`runtime_home`, spin up `sentry-hermes-<user>` container, enrol, persist + register endpoint | Gateway admin (writable subset), compose/templating | `teams.py`, bootstrap script |
| C6 | **Gated inter-agent channel** — profile↔profile delegation/inbox, allow-listed pairs, redacted at the boundary | Gateway (new) | signed dispatch + redaction envelope |

## 5. Data flow

- **Chat:** browser → WebUI (holds user's Gateway token) → `POST sentry-gateway:8090/v1/responses` with bearer token → `require_caller` → `profile_id` → `HermesRuntime.send_turn` → that profile's `sentry-hermes-<user>:8642` → streamed back. Audit row written Gateway-side.
- **Management (e.g. Skills):** WebUI panel → Gateway profile-scoped route → governance lifecycle / per-profile store → response. No filesystem reach-through; no cross-profile visibility.
- **Inter-agent message:** agent A emits a delegate/message → Gateway checks the (A→B) allow-list → runs the payload through the redaction envelope → delivers to B's inbox, attributed "from A's agent," carrying only the explicit message. B's agent treats it as *data, not instruction* (connector-content denylist philosophy).

## 6. Phasing (each phase ships something real)

- **Phase 1 — Multi-tenant chat via the Gateway (the privacy core).** C1 + C2 + C3, proven as a **vertical slice with exactly two profiles** (Bryce + one test user): provision a 2nd Hermes container, persist+register both endpoints, add the chat route, per-user WebUI login, repoint the WebUI. **Exit test:** logged in as user A, chat reaches only agent A; as user B, only agent B; a forged/mismatched profile fails closed. *Delivers: real isolated multi-user chat.*
- **Phase 2 — Fix the dead panels for real, per-user.** C4, panel by panel, each scoped to `caller.profile_id`, Skills through the governance lifecycle. *Delivers: Skills/Kanban/Memory/Cron/Profiles alive on real per-user data.*
- **Phase 3 — Provisioning.** C5 — turn the manual Phase-1 steps into a repeatable "add a teammate" flow. *Delivers: onboarding without hand-wiring.*
- **Phase 4 — Gated inter-agent messaging.** C6. *Delivers: allow-listed, redacted agent-to-agent channel.*

## 7. Inter-agent messaging design (Phase 4 detail)

- **Initiation:** agent-autonomous (an agent may delegate/notify without a human in the loop) — but **only** for `(sender_profile, recipient_profile)` pairs present in an allow-list the Gateway owns.
- **Boundary:** every crossing payload passes the redaction envelope; only the explicit message text crosses — never memory, session, or context handles.
- **Trust:** delivered to the recipient as **untrusted inbound data**, fenced so it can never invoke privileged verbs (mirrors `DENIED_TO_CONNECTOR_CONTENT`, which already lists `send_message`, `dispatch_work_order`, …).
- **Audit:** each crossing is an append-only audit event (sender, recipient, hash, allow-list rule id).
- **Reuse:** signed single-use, audience-bound dispatch (`work_order_signing.py`) for the delivery token; the redaction envelope for egress.

## 8. Residuals, risks, non-goals

- **Provider keys** entered in the WebUI still won't affect chat (the forwarded body carries only model/provider/effort/tier). Under this design, provider config becomes **per-agent container config at provisioning time** (C5), and the WebUI provider panel is either wired to write the user's agent config or clearly relabeled. Tracked, not silently left lying.
- **Secret at rest:** per-profile Hermes `api_key` must be encrypted in the DB (C1), not plaintext.
- **Resource:** 2–4 always-on Hermes containers ≈ negligible on grain.silo (77 GB free, ~77 MiB/container). Revisit only past ~10 users (would need an on-demand pool).
- **Merge burden:** the WebUI diverges further from upstream hermes-webui (already accepted per the fork decision).
- **Biggest risk:** C2 (a correct streaming chat proxy with audit) is the load-bearing new Gateway code; Phase 1's two-profile slice exists to de-risk it before any feature breadth.

## 9. Resolved decisions (2026-07-20, approved by Bryce)

1. **WebUI login = reuse the Gateway's device-enrolment flow** (the same identity source the desktop app uses). No parallel username/password store in the WebUI. One identity system.
2. **Personal profiles only for Phases 1–3.** Shared team profiles (schema already supports them) are deferred until after core isolation ships.
3. **Provider keys: relabel the WebUI panel honestly now; wire real provider config per-agent at provisioning time (C5).** Not wired to write agent config from the WebUI today.

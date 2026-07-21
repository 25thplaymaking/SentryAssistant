# Multi-tenant Sentry is LIVE — cutover done, per-user login proven

**Date:** 2026-07-21 (evening) · **Supersedes:** `2026-07-21-multitenant-sentry-cutover.md`
and `2026-07-21-flip-brief.md` (both now historical; the flip-brief's "remove
HERMES_WEBUI_PASSWORD" suggestion was WRONG — see §3).

## State: shipped

The WebUI at `:8787` now runs `HERMES_WEBUI_GATEWAY_DIALECT=sentry` against
`http://gateway:8090`. Identity is per-user. Proven live end-to-end, not asserted:

| Proof | Evidence |
|---|---|
| Per-user login, no password | enrollment code redeemed → `{"ok": true}`, session cookie set |
| Identity resolution | Bryce's session sees only `Bryce (personal)`; Stress's sees only `Stress (personal)` |
| Chat routes to the caller's own agent | stream returned `ISOLATION OK`; audit row `chat.turn allowed Bryce (personal)` |
| Panels work per user | `/api/profiles /api/skills /api/memory /api/crons /api/agent-messages` → 200 |
| Shared password refused | 403 "Password sign-in is disabled…use your enrollment code" |
| Late provisioning needs no restart | `LateJoiner` provisioned 17:53 (gateway up since 17:52:46), chatted OK, zero restarts |

Versions: gateway `25vid/sentry-foundation` @ `1425e0e` (278 tests), fork
`frontir` @ `90dcb017` (46 sentry tests). Both agents on hermes-agent **0.19.0**.

## 1. How identity actually works (the question that started this)

There is **no username and no password** under the sentry dialect. A user redeems
a **single-use enrollment code** once per browser — like pairing a device — and the
browser then holds a per-user token bound to their profile. Every Gateway request
carries `pid`; the Gateway resolves *that profile's own* Hermes endpoint and fails
closed on a mismatch.

Credential ladder: enrollment code **5 min** → access token **15 min**
(auto-refreshed) → refresh token **30 days** (rotating) → WebUI session cookie
**30 days**. A browser in regular use stays signed in indefinitely.

**Re-entry:** `/api/auth/enroll/start` mints a code for *the caller's own* next
device, so a signed-in user can self-serve. Someone with no active session (cleared
cookies, new laptop, >30 days idle) needs an operator to mint one — acceptable at
2–4 people, and the obvious next improvement if it chafes.

## 2. Adding a teammate

```bash
cd /srv/sentry/repo/deploy/linux
./provision-teammate.sh --name "Alice" --slug alice
```

That is the whole path. It creates the per-user data + workspace dirs at 0700, seeds the
**governed** teammate config (`memory.write_approval`, `skills.write_approval`,
`skills.guard_agent_created` all on), starts a hardened container (caps dropped,
no-new-privileges, memory/CPU/PID limits, `restart unless-stopped`, no published port, no
docker socket), waits for the agent to actually serve, registers the profile + encrypted
endpoint, and prints a 5-minute enrollment code. **No gateway restart needed** — the
endpoint loads on demand at first turn.

> ⚠️ **Never seed a teammate's home by copying `data/hermes/personal/`.** An earlier
> version of this runbook suggested it. It carries the OWNER's `config.yaml` — whose
> documented risk acceptance leaves skill governance OFF — into another person's agent,
> which is precisely the case the scanner exists for. It also risks dragging real personal
> state across the isolation boundary. `provision-teammate.sh` seeds only the governed
> config into an empty home; let Hermes initialise the rest.

## 3. Two traps, both now closed (do not re-open)

**(a) Removing `HERMES_WEBUI_PASSWORD` would have disabled auth entirely.**
`is_auth_enabled()` counted only password/passkey/OIDC/trusted-header; with none
configured it returns False and the portal serves unauthenticated. The sentry
dialect is now itself an auth method, so the password can be dropped safely — but
it no longer needs to be, since password sign-in is refused outright while the
dialect is on. Recovery hatch: `HERMES_WEBUI_SENTRY_ALLOW_PASSWORD=1`.

**(b) `bound_profile` is not the Gateway profile id.** Passing the Gateway UUID
into `create_session(bound_profile=...)` made the active-profile visibility guard
403 *every* API call ("Profile access forbidden"). That field is a WebUI/Hermes
profile NAME. The Gateway identity lives in the session's `gateway` blob only.

## 4. Operational notes

- `docker compose up -d gateway` **no-ops when no config changed** — it does not
  restart. Use `docker compose restart gateway` when you actually need a restart.
- `audit_events` is append-only; live tests leave permanent rows (LateJoiner's
  remain by design, its user/profile rows are FK-referenced and were left in place).
- `/api/kanban/board` returns 503/unavailable: this Hermes build exposes no
  `/api/plugins/*`. Expected, documented, not a regression.
- Rollback: drop the two env vars from the `webui` service + `docker compose up -d
  webui`. Backup at `deploy/linux/compose.yaml.preflip.*`.

## 5. Open follow-ups (none blocking)

- Panel *visual* fidelity in a real browser — endpoints return 200 with correct
  shapes, but only eyes confirm rendering.
- Fork session tokens are stored plaintext in `.sessions.json` (as upstream does
  refresh tokens) — worth protecting.
- Self-service re-entry for a locked-out user with no active session.
- Memory/skills write-through has no approval gate yet.
- Hermes patch spike (#67457, #66148, #68282, #68166, #67947) in a throwaway
  container; prod stays on released 0.19.0 until one proves itself.
- Leftover slice-1 test users (`e2e-bryce`, `e2e-intruder`) — no endpoints, inert.

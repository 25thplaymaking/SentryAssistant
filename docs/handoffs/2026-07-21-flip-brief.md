# Flip brief — cutting the WebUI over to the Sentry Gateway (`dialect: sentry`)

> ⚠️ **SUPERSEDED — do not follow this document.**
> Read `2026-07-21-multitenant-LIVE.md` instead. Two instructions below are now
> known-wrong: seeding a teammate home from `data/hermes/personal/` (carries the
> owner's ungated skills config into someone else's agent), and removing
> `HERMES_WEBUI_PASSWORD` (that would have disabled authentication entirely).
> `docker compose up -d gateway` also does not restart an unchanged container.

**Date:** 2026-07-21 · **Status:** staged, NOT fired — awaiting Bryce's go
**Gateway:** `25vid/sentry-foundation` @ `20c1edf` · **Fork:** `frontir` @ `1bac875e`
**Box:** grain.silo (`ssh bishop@205.209.116.114`)

This is the write-up requested instead of firing the flip. Everything below is verified
against the live box, not recalled.

## 1. Where things stand (verified 2026-07-21)

| Piece | State |
|---|---|
| Gateway `sentry-gateway-1` | healthy, `/health/ready` 200, `pinnedVersion 0.19.0` |
| `sentry-hermes-1` (Bryce's agent) | up, **hermes-agent 0.19.0** |
| `sentry-hermes-stress` (teammate) | up, **0.19.0**, live chat proven through the Gateway |
| Runtime endpoints registered | **2 of 2** — `Bryce (personal)` → `http://hermes:8642`, `Stress (personal)` → `http://sentry-hermes-stress:8642` |
| WebUI checkout `/srv/sentry/webui` | **`1bac875e`** (pulled today; was `8f8d3d9a`, i.e. it did not yet contain any sentry-dialect code) |
| Running `sentry-webui-1` | still the **old build**, `dialect=hermes`, `BASE_URL=http://hermes:8642` — unchanged for users |

Two things were fixed today before this brief:

- **Bryce's profile had no runtime endpoint.** Registered → his own agent. Without it he
  could have signed in after the flip and had chat fail closed.
- **The box's WebUI checkout was 7 commits behind** the sentry-dialect work. Pulled. This is
  inert until a rebuild.

## 2. The exact change

`/srv/sentry/repo/deploy/linux/compose.yaml`, `webui:` service (env block starts line 119):

```diff
       HERMES_WEBUI_CHAT_BACKEND: gateway            # line 142, unchanged
-      HERMES_WEBUI_GATEWAY_BASE_URL: http://hermes:8642      # line 143
+      HERMES_WEBUI_GATEWAY_BASE_URL: http://gateway:8090
+      HERMES_WEBUI_GATEWAY_DIALECT: sentry
       HERMES_WEBUI_GATEWAY_API_KEY: ${HERMES_API_KEY:?...}   # line 144, unchanged
```

Then:

```bash
cd /srv/sentry/repo/deploy/linux
docker compose build webui && docker compose up -d webui
# mint Bryce a fresh browser enrollment code (5-min TTL — mint at the moment of use):
docker exec sentry-gateway-1 python scripts/provision_teammate.py \
  --display-name "Bryce" --slug bryce --hermes-base-url http://hermes:8642 \
  --hermes-api-key "$(grep '^HERMES_API_KEY=' .env | cut -d= -f2-)"
```

(The provision script is idempotent for an existing user: it reuses the user + personal
profile, upserts the endpoint, and mints a fresh code.)

## 3. Blast radius — corrected

The earlier handoff said the flip means *"only provisioned users can log in."* **That is
wrong**, and the difference matters:

- **Shared-password login keeps working.** `api/routes.py:16411` only takes the enrollment
  branch `if enrollment_code:` — an empty code falls through to `verify_password`.
  `HERMES_WEBUI_PASSWORD` is still set in compose.
- **But a password-only session has no per-user Gateway token**, and:
  - **Chat** falls back to `gateway_token or get_sentry_session_token(...) or api_key`
    (`api/gateway_chat.py:1007`). That `api_key` is the *Hermes* key, which the Sentry
    Gateway will reject → **401 → terminal error bubble in the chat UI.**
  - **Panels** are guarded by `if _tok:` (e.g. `api/routes.py:13441`) and fall through to the
    legacy local-agent path — i.e. today's already-broken behaviour (Skills 500 etc.), not a
    crash.

**So the real post-flip failure mode is "logs in fine, chat errors"** for anyone using the
shared password — more confusing than a clean lockout. Two ways to handle it, pick one at
flip time:

- **(a) Leave it.** Only you know the password; you'll be enrolled anyway. Zero extra work.
- **(b) Remove `HERMES_WEBUI_PASSWORD` from the webui service** so enrollment is the only
  door and there's no confusing half-working state. Cleaner, one more line in the same edit.

**Unaffected:** the WinUI desktop app (talks to the Gateway directly on `:8090`, not through
`:8787`), the Gateway itself, both Hermes containers, and `sentry-hermes-1`'s data.

## 4. Rollback (~1 min)

Remove `HERMES_WEBUI_GATEWAY_DIALECT`, restore `BASE_URL: http://hermes:8642`, then
`docker compose up -d webui`. No rebuild needed (env-only change, same image). Nothing in
the flip writes irreversible state; the only permanent artifacts are `audit_events` rows
from any live chat turns, which are append-only by design.

## 5. Verification checklist (headless, all run on the box)

Chosen mode: curl only — no browser driven on Bryce's desktop.

```bash
# 1. login page renders the enrollment field (sentry dialect active)
curl -s 127.0.0.1:8787/login | grep -c 'id="enroll-code"'          # expect 1

# 2. redeem the fresh code -> session cookie
curl -s -c /tmp/c.txt -X POST 127.0.0.1:8787/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"enrollment_code":"<CODE>","device_name":"Sentry Web"}'     # expect {"ok":true}

# 3. chat streams from BRYCE's agent (not the shared one)
curl -s -b /tmp/c.txt -X POST 127.0.0.1:8787/api/chat \
  -H 'Content-Type: application/json' -d '{"message":"say hello"}' | head

# 4. every panel returns real JSON, no 500
for p in /api/skills /api/kanban/board /api/memory /api/crons /api/profiles /api/agent-messages; do
  printf '%s -> ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' -b /tmp/c.txt "127.0.0.1:8787$p"
done
```

**Isolation spot-check (the invariant that matters):** enrol as Stress in a second cookie jar
and confirm their turn lands on `sentry-hermes-stress` while Bryce's lands on `hermes` —
`docker logs --since 2m sentry-hermes-stress` should show one and not the other.

**Only eyes can confirm:** panel *visual* fidelity — whether each Gateway JSON shape matches
what the fork's frontend expects. Each fix is a ~15-line branch in `SentryWebUI/api/routes.py`.

## 6. Open items after the flip (none block it)

- Panel shape fidelity per-panel (browser).
- Fork session tokens sit plaintext in `.sessions.json` (same as upstream refresh tokens).
- Gateway loads endpoints at startup only → provisioning still needs a gateway restart.
- Memory/skills write-through has no approval gate yet.
- Two leftover slice-1 test users (`e2e-bryce`, `e2e-intruder`) — no endpoints, harmless.
- Hermes patch spike (#67457, #66148, #68282, #68166, #67947) in a throwaway container —
  decided 2026-07-21: prod stays on clean released 0.19.0 until a patch proves itself.

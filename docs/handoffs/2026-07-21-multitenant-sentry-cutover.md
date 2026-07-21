# Handoff — Multi-tenant Sentry: code-complete, one supervised cutover to go live

**Date:** 2026-07-21 (night) · **Branch (gateway):** `25vid/sentry-foundation` @ `e5420f3`
**Branch (fork):** `SentryWebUI` `frontir` @ `1bac875e` · **Box:** grain.silo (`ssh bishop@205.209.116.114`)

## TL;DR

All application code for the three phases (multi-tenant chat, the five per-user panels, and the
gated inter-agent channel) is **written, committed, unit-tested, and the Gateway backend is
deployed and healthy** on grain.silo. Your live "Frontir Sentry" app is **unchanged** — everything
new is inert until the fork is flipped to `dialect=sentry`. The only thing left is a **~20-minute
supervised cutover** (needs the box + a browser). Do that (below) and your team is using it.

## What's live right now (unchanged for users)

- Gateway (`sentry-gateway-1`, `:8090` loopback) — healthy; carries the new routes but they're
  dormant for the fork until cutover.
- WebUI (`sentry-webui-1`, `:8787` loopback) — still shared-password, single shared agent
  (`dialect=hermes`). **Not touched.**
- Postgres `sentry`/`sentry` (`docker exec sentry-postgres-1 psql -U sentry -d sentry`).
- Migrations through `008` applied. `SENTRY_RUNTIME_ENC_KEY` in `deploy/linux/.env` (mode 600).
- Docker network is `sentry_sentry`; the Gateway's compose service name is **`gateway`** (reachable
  from the webui container as `http://gateway:8090`).

## What was built (all committed + pushed)

**Gateway (`server/sentry_gateway`, 273 tests, deployed):**
- Phase 1: `routes/chat.py` — `POST /api/chat/turn` (per-profile, fail-closed, SSE, audited).
  Isolation proven live earlier today.
- Phase 2 panels: `routes/{profiles,kanban,skills,memory,cron}.py` — all profile-scoped, fail-closed.
  `memory` (migration 006) + `cron` (008) are Gateway-native; `skills` reads the governance
  `skill_proposals` table; `kanban/board` reads via the runtime adapter.
- Phase 3: `routes/agent_messages.py` (migration 007) — allow-list (`agent_message_grants`),
  fail-closed, boundary-redacted, audited inbox.
- Endpoint plumbing: `agent_runtime/endpoints.py` + migration `005_runtime_endpoints.sql`
  (Fernet-encrypted per-profile keys).
- Provisioning: `scripts/provision_teammate.py`.

**Fork (`SentryWebUI`, `frontir`, 30 sentry tests, NOT deployed with sentry dialect):**
- `api/sentry_gateway_client.py` (get/post to the Gateway as the user),
  `api/sentry_gateway_auth.py` (enroll/refresh/jwt_exp), and in `api/gateway_chat.py`: the `sentry`
  dialect + SSE translation + per-user token (`sentry_access_token_from_handler`, session-keyed map).
- `api/auth.py` + `api/routes.py`: enrollment-code login, per-user token in the session, login-form
  field, and **all panel GET handlers** (`/api/skills`, `/api/kanban/board`, `/api/memory`,
  `/api/crons`, `/api/profiles`, `/api/agent-messages`) proxy to their Gateway route in sentry mode.

## THE MORNING TASK — the supervised cutover

> ⚠️ Step 3 changes the live user-facing app. Do it deliberately; it's reversible by removing the
> two env vars and redeploying the webui.

**1. Start a teammate's Hermes container** (or use your own profile for a first solo test). On the box:
```bash
KEY=$(docker exec sentry-gateway-1 python -c "import secrets;print(secrets.token_hex(24))")
echo "hermes key: $KEY"     # note it for step 2
docker run -d --name sentry-hermes-alice --network sentry_sentry \
  -e API_SERVER_ENABLED=true -e API_SERVER_KEY="$KEY" \
  -e API_SERVER_HOST=0.0.0.0 -e API_SERVER_PORT=8642 \
  -e HERMES_HOME=/home/hermes/.hermes \
  -v /srv/sentry/repo/deploy/linux/data/hermes/alice:/home/hermes/.hermes \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  sentry-hermes
# ⚠️ UNVERIFIED: a fresh HERMES_HOME may need config to serve /v1. If /v1/capabilities 404s or the
# container won't answer, copy a seed config into data/hermes/alice first (mirror data/hermes/personal).
```

**2. Provision the teammate** (creates user+profile+encrypted endpoint+enrollment code):
```bash
cd /srv/sentry/repo/deploy/linux
export SENTRY_RUNTIME_ENC_KEY=$(grep '^SENTRY_RUNTIME_ENC_KEY=' .env | cut -d= -f2-)
export SENTRY_DATABASE_URL="postgresql://sentry:$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)@127.0.0.1:5433/sentry"
docker exec -e SENTRY_RUNTIME_ENC_KEY -e SENTRY_DATABASE_URL sentry-gateway-1 \
  python scripts/provision_teammate.py --display-name "Alice" --slug alice --hermes-api-key "$KEY"
# prints an ENROLLMENT CODE (5-min TTL) + profile id
docker compose up -d gateway   # restart so the gateway registers the new endpoint in-process
```

**3. Flip the fork to sentry dialect.** Edit the `webui` service env in
`/srv/sentry/repo/deploy/linux/compose.yaml`, add:
```yaml
      HERMES_WEBUI_GATEWAY_DIALECT: sentry
      HERMES_WEBUI_GATEWAY_BASE_URL: http://gateway:8090   # was http://hermes:8642
```
then: `cd /srv/sentry/webui && git pull --ff-only && cd /srv/sentry/repo/deploy/linux && docker compose build webui && docker compose up -d webui`

**4. Verify in a browser** (over the tunnel to `:8787`): the login page now shows an enrollment-code
field → redeem the code → confirm the chat replies from *that user's* agent, then click through
Skills / Kanban / Memory / Cron / Profiles and confirm they render (no 500s). Panel-shape fidelity
to the fork's frontend is the one thing only a browser confirms — adjust the per-panel shape map in
`SentryWebUI/api/routes.py` if a panel renders oddly (each is a ~15-line branch).

**Rollback:** remove the two env vars from the webui service + `docker compose up -d webui`.

## Gotchas / notes

- `127.0.0.1` works on the Linux box; the `::1` issue is the Windows desktop only.
- `audit_events` is append-only (immutable trigger) — any live test leaves permanent rows; use real
  enrolled ids (the provision script handles this).
- The Hermes API server exposes only `/v1/*` + capabilities + health — that's why the panels are
  Gateway-native, and why `kanban/board` may be empty until a per-user agent has board data.
- Fork commits show noise from the `code-review-graph` pre-commit hook (cp1252 console encoding
  error) — cosmetic; commits succeed.

## Open follow-ups (non-blocking)

- Panel-shape fidelity per fork frontend (browser).
- Fork session tokens are stored plaintext in `.sessions.json` (like refresh tokens) — protect.
- Gateway registers endpoints at startup only (provisioning needs a gateway restart) — a lazy
  DB-backed `_instance` load would remove the restart.
- Memory/skills write-through governance (memory currently read+write with no approval gate).

## Pointers

Plan: `docs/plans/2026-07-19-sentry-hermes-os-integration.md` (2026-07-20 Reconciliation + slice-1 +
Phase-2/3 sections). Memory: `sentry-multitenant-design`, `sentry-live-ops-lessons`,
`goal-driven-work-mode`, `reconcile-sentry-plan-first`.

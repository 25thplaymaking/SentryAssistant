# Sentry Execution Status

**Updated:** 2026-07-19
**Branch:** `25vid/sentry-foundation`
**Commits:** `691bcd6` (desktop shell), `5aefc8e` (control plane)

This records what is actually verified, and the exact gates that stopped further
progress. Nothing here is claimed on the strength of a build alone.

---

## 1. Windows desktop — done and visually verified

`SentryPresenceView` replaces the animations that were scattered through
`MainPage`. It renders a unit-tested `PresenceDescriptor` from
`Sentry.Contracts`, which adds the missing `Queued`, `ReadyForReview`, and
`Offline` states and enforces that only `Resolved` may request speech. Reduced
motion drops animation while keeping tone, label, and the screen-reader
announcement.

Real-window inspection found four defects a green build had hidden:

| Defect | Cause |
|---|---|
| Rail and inspector pinned at full width; canvas clipped mid-word at 900px and 700px | `VisualStateManager.VisualStateGroups` was attached to the `Page`, not the root child, so adaptive triggers never evaluated |
| Composer collapsed to a fraction of its width | `HorizontalAlignment="Center"` sizes to content; needed `Stretch` with `MaxWidth` |
| Caption buttons nearly invisible on light theme | `AppWindow.TitleBar` button colours were never set, so they kept dark-theme values |
| Compact rail showed "WORKS", "RECEN", "Se…" | Section eyebrows and the recent card cannot fit a 70px rail |

All four are fixed and re-verified.

**Evidence:** 45/45 tests pass. Build is 0 warnings / 0 errors. Packaged window
inspected at 1360×860, 900×760, 700×700 in dark and light.

**Not verified:** Windows high contrast. Forcing it system-wide risked leaving
the machine in high-contrast mode if the session was interrupted. The
`HighContrast` theme dictionary uses `SystemColor*` resources, which is the
correct implementation, but it has not been seen running.

---

## 2. Linux control plane — deployed and live

Running on `grain.silo` (205.209.116.114) at `/srv/sentry/repo`.

```
sentry-gateway-1    Up (healthy)   127.0.0.1:8090
sentry-postgres-1   Up (healthy)   127.0.0.1:5433
sentry-hermes-1     Up             no published port
```

`GET /health/ready` returns **200**:

```json
{"status":"ready","checks":{"signingKey":"ok","database":"ok",
 "runtime":{"name":"hermes","pinnedVersion":"0.18.2","healthy":true,
            "degradedReason":null,"supportsCancellation":true,
            "supportsSessionSearch":true}}}
```

Readiness was independently observed returning **503 degraded** while Hermes was
down, so it genuinely gates on its dependencies rather than always reporting ok.

### The Hermes adapter was corrected against reality

The first adapter targeted invented `/sessions` and `/kanban/cards` endpoints.
The real Hermes API server is OpenAI-compatible on port 8642 with
`/v1/responses`, `/v1/capabilities`, `/v1/models`, `/health`, and a Kanban
plugin under `/api/plugins/kanban/`. It requires a bearer key even on loopback.

Most importantly: **Hermes selects its profile per process** (`hermes -p name
gateway`), not per request. Profile isolation therefore has to be one Hermes
container per profile with its own `HERMES_HOME`. A profile header would have
been silently ignored and every profile would have shared one agent.

### Verified security controls

- **Append-only audit** — enforced by trigger, not convention. Tested against the
  live database: `UPDATE` and `DELETE` both raise `audit_events is append-only`
  and the row survives with its original value.
- **Nothing published publicly** — PostgreSQL and the Gateway bind loopback only;
  Hermes has no `ports:` block and is reachable solely on the compose network.
- **Weak signing keys refused** — under 32 bytes fails at construction rather
  than warning at first use.
- **Restart safety** — full `docker compose restart` returns all three healthy,
  readiness 200, audit data intact. All services are `unless-stopped`.

### A deliberate deviation from the plan

The plan specifies `terminal.backend: docker` for Hermes. That is **not** set,
on purpose. Enabling it requires mounting the host Docker socket into the Hermes
container, which grants effective host root and breaks the very isolation it is
meant to provide. Hermes already runs as a non-root user in its own container
with no socket and no published port; that container is the whole-process
boundary the security policy asks for. Revisit only with rootless Docker or a
socket proxy.

---

## 3. Gates that stopped further progress

### Gate A — Hermes has no inference provider (credential)

Every turn fails with:

```
No inference provider configured. Run 'hermes model' to choose a provider and
model, or set an API key (OPENROUTER_API_KEY, OPENAI_API_KEY, etc.)
```

Everything up to inference is deployed and healthy. To clear it, add one key to
`/srv/sentry/repo/deploy/linux/.env` and `docker compose restart hermes`.

### Gate B — iOS cannot be built from this machine (hardware, not credential)

Building, signing, and installing a native iOS app requires **Xcode, which runs
only on macOS**. `xcodebuild` and `codesign` have no Windows equivalent. This
machine is Windows 11 Home with no Swift toolchain and no Mac on the network.

This is not a missing secret. No amount of safe continuation reaches a signed
`.ipa` on the iPhone from here. It needs a Mac, an Apple Developer Program
membership, and the physical device — plus the separate TestFlight/APNs setup.

### Gate C — no dedicated `sentry` OS account (credential)

`bishop` has full `sudo` but it requires a password; only four specific commands
are NOPASSWD. Creating a system user needs that password. The stack therefore
runs under `bishop` with containers non-root internally, and the Hermes image
builds its user with `HERMES_UID`/`HERMES_GID` matching the host owner so the
bind mount works without any privileged step.

---

## 3b. Authenticated API — built and verified live

Device enrollment, token rotation, and revocation are implemented and proven
against the running Gateway.

- **Enrollment is two-sided.** A code is mintable only from an authenticated
  session and redeemable once within five minutes. The first device is
  bootstrapped by an out-of-band operator script
  (`scripts/bootstrap_device.py`), so the API has no unauthenticated hole.
- **Refresh tokens rotate.** The presented token is retired as it is used;
  replay fails, and an access token cannot stand in for a refresh token.
- **Revocation is immediate and durable.** It is rehydrated from the database at
  startup — otherwise a restart would silently un-revoke every unexpired token.
  If the list cannot be loaded, the token service is dropped and routes return
  503 rather than failing open.
- **Refusals are indistinguishable.** Unknown, consumed, and expired codes give
  the same message. Revoking another user's device returns 404, not 403, so IDs
  cannot be enumerated.

**Evidence:** 20/20 auth checks pass live, plus a separate restart test showing a
revoked token is still refused after the process comes back. 13/13 work-order
checks. 81/81 unit tests.

A bug caught by the first live run and fixed: denied decisions were written
inside the transaction that then rolled back on raise, so refusals were never
persisted. They now commit on their own connection (`denied=0` → `denied=2`).

## 4. Not yet built

Deployed is the control-plane foundation, not the whole platform. Still absent:
OIDC/Authentik browser sign-in and passkeys, the **team** HTTP API (invitations,
membership, roles, sharing) on top of the schema, the Windows Sentry Node and its
Codex/Claude/Grok adapters, the connectors (Gmail, Discord, Steam), reminders,
notification routing and APNs, governed skill evaluation, and backup/restore.

Personal profiles are live end to end. Teams exist in the schema and in the
tested authorization logic — a removed member already loses access, and observers
already cannot dispatch — but no route creates a team or sends an invitation yet.

"Trusted execution" is half done: work orders are signed and the node-side
validator is written and tested (`work_order_signing.py`), but no Windows Node
process exists to receive them, so nothing has actually executed on a node.

Do not describe Sentry as production-ready. By the plan's own gate list this is
short of Gate 1 until the inference provider is set and a node is enrolled.

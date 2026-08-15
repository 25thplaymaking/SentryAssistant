# Frontir Sentry handoff

**Updated 2026-08-15 (America/Toronto).** This file describes the current
working tree. Dated reports under `docs/handoffs/` remain historical evidence,
not current deployment instructions.

## Current architecture

- Sentry Gateway is the identity and authorization boundary.
- The Frontir WebUI uses `dialect=sentry`; shared-password WebUI auth is not the
  user identity path.
- Browser chat and management requests go through the Gateway and are resolved
  to the caller's profile before a Hermes runtime is selected.
- The desktop shell binds IPv6 loopback forwards because IPv4 loopback is
  unreliable on Bryce's machine: WebUI `[::1]:8787`, Gateway `[::1]:8090`, and
  Server Control `[::1]:17443`.
- Grain containers and Server Control remain loopback/private-network only.

## Password-recovery increment

The implementation is complete across three workspaces. Gateway and WebUI are
live on Grain; Server Control is signed and staged behind the root gate:

- Gateway: operator-authorized recovery-code creation, hashed single-use
  codes, ten-minute expiry, password-policy enforcement, audit records, and
  full device/refresh-token revocation after success.
- Frontir WebUI: **Forgot password?** flow on the Sentry sign-in page, including
  an optional link to the locally forwarded Server Control panel.
- Server Control: MFA-authenticated, CSRF-protected and rate-limited recovery
  code issuance from Settings. It never receives or sets the new password.
- Desktop shell: forwards local port `17443` to Grain Server Control. Teammate
  packages disable that forward by default.

The shared credential is dedicated to recovery-code creation and is read from
`deploy/linux/data/gateway/sentry-recovery.token`. Compose projects it into the
Gateway as a read-only secret; Server Control reads the host file as root.

## Deployment order

1. **Done:** Grain Sentry is at `72317f8`; WebUI is at `b8b9042c`.
2. **Done:** migration 010 and the owner-only recovery key are provisioned;
   Gateway, WebUI, Hermes, and the Server Control MCP sidecar are healthy.
3. **Root gate:** install the previously staged Server Control v3 bootstrap,
   then apply signed release `20260815-044000-sentry-recovery` already present
   in `/home/bishop/server-control-signed-inbox`.
4. Verify Server Control Settings can issue a code, use it once in Sentry, and
   confirm both reuse and an old Sentry session are rejected.

Do not claim the feature live until all four steps are verified. Server
Control installation is the root-authority gate; code completion alone does
not prove activation.

## Verified locally

- Gateway full suite: 366 passed.
- Frontir WebUI Sentry-specific suite: 115 passed.
- Server Control backend: 48 passed in the isolated release build.
- Server Control frontend: lint passed, 3 tests passed, production build
  passed.
- Sentry desktop shell: Release build passed with zero warnings and errors.
- Docker Compose configuration and production JSON parsing passed.

On this Windows host, the repository's Bash wrapper cannot run because WSL is
disabled. WebUI verification therefore used its existing Windows virtual
environment directly. Do not run the WebUI's full Windows pytest suite: its
documented subprocess behavior can leak child processes; use the focused
Sentry files or the supported Linux wrapper.

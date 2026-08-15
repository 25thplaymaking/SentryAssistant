# Frontir Sentry

Frontir Sentry is a private, per-user assistant platform. The Windows desktop
shell opens SSH forwards to Grain and hosts the Frontir WebUI; the WebUI uses
the Sentry Gateway for identity and profile-scoped access; the Gateway routes
each signed-in person to their own Hermes runtime.

## Active components

- `server/sentry_gateway`: FastAPI identity, authorization, audit, profile,
  management, and runtime-routing boundary.
- `deploy/linux`: loopback-only PostgreSQL, Gateway, Hermes, WebUI, and
  Cloudflare Tunnel deployment.
- `tools/SentryShell`: Windows WebView2 shell and SSH tunnel manager.
- `docs`: architecture, handoffs, plans, and operational runbooks.

The WebUI fork is checked out separately at
`C:\Users\Bryce\Desktop\SentryWebUI` on its `frontir` branch.

## Password recovery

Password recovery is deliberately split across two authenticated surfaces:

1. Sign in to Server Control with its password and authenticator code.
2. In **Settings → Sentry account recovery**, create a ten-minute, single-use
   code for the Sentry username.
3. In the Sentry sign-in window, choose **Forgot password?** and enter that
   code with the new password.

Server Control never receives the new password. The Gateway stores only a hash
of the recovery code, invalidates earlier active codes, and revokes every old
Sentry device and refresh token when the password changes. A dedicated random
file authorizes only code creation; it cannot sign sessions.

To provision the database table and shared file on Grain after both repos are
deployed:

```bash
cd /srv/sentry/repo/deploy/linux
./provision-recovery-bridge.sh
```

The script preserves an existing key, applies the idempotent migration, and
rebuilds the Gateway. Server Control reads the same host file at the path in
its Grain production configuration.

## Development checks

Use the x64 .NET SDK on this machine:

```powershell
& .\server\sentry_gateway\.venv\Scripts\python.exe -m pytest .\server\sentry_gateway\tests
& 'C:\Program Files\dotnet\dotnet.exe' build .\tools\SentryShell\SentryShell.csproj -c Release
```

Gateway tests use the Python environment under `server/sentry_gateway`. See
`docs/HANDOFF.md` for the current verified commands and deployment boundary.

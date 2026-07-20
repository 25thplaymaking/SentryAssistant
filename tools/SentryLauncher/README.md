# Sentry launcher

One command to start Sentry, and one place to find out which layer is broken
when it does not start.

## Why

Sentry spans three layers that fail independently:

1. an SSH tunnel on this machine (the Gateway is loopback-only on the server),
2. the Gateway on grain.silo,
3. the Hermes runtime behind it.

Any one of them being down looks identical from the desktop app: a window that
says "offline". The launcher checks them in dependency order, so the first
failure names the layer that is actually broken.

## Build

Not referenced from `SentryAssistant.sln` on purpose — operator tooling should
never be able to break `dotnet test SentryAssistant.sln`. Build it explicitly:

```powershell
& 'C:\Program Files\dotnet\dotnet.exe' publish tools\SentryLauncher\SentryLauncher.csproj -c Release -o tools\SentryLauncher\dist
```

Produces a ~180 KB single-file `Sentry.exe`. Framework-dependent: it needs the
.NET 10 runtime, which this machine has. `dist/` is gitignored.

## Use

```
Sentry            Preflight all layers, then open the desktop app.
Sentry --check    Preflight only. Prints status and exits. No window.
Sentry --repair   Also try to restart the remote stack over SSH.
```

Exit codes: `0` healthy · `1` tunnel · `2` gateway · `3` runtime · `4` app not built.
Scriptable — `Sentry --check` is a health probe.

## Notes

- The tunnel **must** bind `[::1]`, not `127.0.0.1`. IPv4 loopback is broken on
  this machine: a client "connects" but is silently forced onto the IPv6 stack
  and a listener bound to `127.0.0.1` never accepts. Using IPv4 gives a live
  tunnel and an app that reports offline with no useful error.
- `--repair` is opt-in because restarting services should never be silent. It is
  scoped by absolute path to the Sentry compose project; the 25vid production
  stack lives elsewhere on the same host and is never touched.
- `/health/ready` answers **503** when the runtime is degraded, and that body
  carries the diagnosis. The launcher reads the body regardless of status code —
  treating non-2xx as failure would report a runtime outage as a tunnel outage.
- **The app is MSIX-packaged and cannot be started from its exe.** WinUI 3
  needs package identity; launching `bin/.../SentryAssistant.exe` directly dies
  immediately with `0xE0434352`. Worse, `Process.Start` reports success anyway,
  so a launcher that starts the exe claims it opened the app while nothing
  appears. The launcher resolves the AUMID and starts it through
  `shell:AppsFolder`, registering the loose build first if Windows does not
  know about it yet (a fresh clone or a clean build is always in that state).
  Registering a loose layout requires Developer Mode.
- After launching, the launcher waits for a `SentryAssistant` process to exist
  before reporting success — the shell reports success whether or not the app
  survived startup, so without that check it would keep claiming victory over a
  window that never opened.
- Set `SENTRY_APP_MANIFEST` to override where `AppX/AppxManifest.xml` is found.
  Otherwise the launcher looks beside itself, then walks up to six levels for a
  `bin/x64/{Debug,Release}/...` build.

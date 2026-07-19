# Plan: Connections, Credentials, and Harness Integration

**Written:** 2026-07-19
**Branch:** `25vid/sentry-foundation`
**Start here after a context reset.** This file assumes no memory of the session
that produced it.

---

## 0. Status — tasks 1 to 4 are done

| Task | State | Commit |
|---|---|---|
| 3 — Persist device credentials | Done, verified live | `35c83a4` |
| 1 — Connections surface | Done, verified live | `cf1132a` |
| 4 — Honest setup step 3 | Done | `8c44f26` |
| 2 — Sentry hooks | Done, verified against a copy | `35f41ef` |
| — Per-call gateway deadlines | Done, verified live | `e814f50` |
| 5 — Codex adapter | **Blocked** on decision 2 below | — |

Baseline is now 214 tests (212 pass, 2 opt-in skips), 0 warnings.

**Found while verifying, and worth remembering:** `HttpClient.Timeout` applies to
every request and is *not* overridden by a `CancellationToken`. A six-second
client with a forty-five-second linked token still aborts at six. The admin
endpoint takes 8.2s, so the Hermes control centre had never worked — it reported
an unreachable gateway on every call. Deadlines are now per call, with no global
timeout. This hid because every failure path is caught and turned into a calm
status, so a call that could never succeed looked like a quiet outage.

**Two things deliberately not done**, each needing a human decision:
- `~/.claude/settings.json` was **not** modified. The hook installer is verified
  against a copy. Run `scripts/install-sentry-hooks.ps1` to apply it for real.
- Hermes still has no inference provider, so it cannot answer. See decision 1.

**Left alone:** the `devices` table still holds test rows from earlier suites
(`e2e-*`, `dispatch-*`, `team-*`) and four `DESKTOP-CTQIKKN` devices, three of
them stale from before credentials persisted. Probe devices created during this
session were revoked. Clearing the rest is a judgement call about someone else's
data.

---

## 1. Verified state — do not re-derive

Everything below was confirmed by running it, not by reading code.

**Deployed on `grain.silo` (205.209.116.114, `ssh bishop@…` with `id_ed25519`):**

```
sentry-gateway-1    127.0.0.1:8090   healthy
sentry-postgres-1   127.0.0.1:5433   healthy
sentry-hermes-1     no host port     Hermes Agent 0.18.2
```

`GET /health/ready` → 200. Repo mirrored at `/srv/sentry/repo/`; secrets in
`/srv/sentry/repo/deploy/linux/.env` (0600, not in Git).

**Test baseline:** 162 .NET (2 opt-in skips), 235 gateway, 0 warnings.

**Working end to end:** device enrolment, token rotation, revocation surviving
restart, durable work orders, append-only audit, teams/roles, node dispatch with
signed orders, a real Claude harness run, encrypted backup with verified restore,
skill governance, notification routing/redaction, connector scopes, reminders,
daily brief, live gateway status in the desktop, and the setup walkthrough.

**Blocked, permanently, on this hardware:** iOS. No Mac, no Xcode. User has
confirmed they have neither. Treat as descoped, not pending.

**Blocked on a decision:** Hermes has no inference provider, so it cannot answer.
See §3.

---

## 2. What the machine survey found

This is the finding that shapes the whole plan.

| Thing | State |
|---|---|
| Claude Code | Installed, `claudeAiOauth` — **subscription OAuth, no API key** |
| Codex | Configured (`auth_mode`, `tokens`) — **OAuth, `OPENAI_API_KEY` empty** |
| Codex binary | **Not present on disk.** Configured but not installed |
| Sentry's own OpenAI key | Present, DPAPI-encrypted, CurrentUser scope |
| `skipDangerousModePermissionPrompt` | `true` globally in Claude settings |
| `permissions.defaultMode` | `acceptEdits` globally |
| `claude-presence` hooks | On all six events |
| Codex policy | `approval_policy = "never"`, `sandbox_mode = "workspace-write"` |
| MCP servers | `enfusion-mcp`, `context7`, `playwright`, `galactic` |
| Claude plugin OAuth | 20 connections (Notion, Slack, HubSpot, Figma, Atlassian, …) |

**The consequence:** there is no reusable API key on this machine for Hermes.
Both harnesses use subscription OAuth, which is not transferable to a
third-party runtime and would breach their terms if routed there. The only real
API key present is Sentry's own DPAPI-protected OpenAI key.

The global Claude and Codex policies are exactly what the integration plan warns
against inheriting. The Claude adapter already avoids them via its own settings
file; a future Codex adapter must do the same.

---

## 3. Decisions needed from Bryce

Nothing in §4 is blocked by these except where noted.

1. **Hermes inference credential.** Either
   (a) promote Sentry's existing DPAPI OpenAI key to the Linux `.env` — one
   credential shared by desktop and control plane, or
   (b) provision a fresh key for the server so the two are independent.
   (b) is safer; (a) is faster. Requires an explicit instruction either way —
   decrypting and transmitting a stored credential is not something to do
   unprompted.

2. **Codex.** Its CLI is not installed. Install it to enable that adapter, or
   leave Codex out of scope.

---

## 4. Implementation tasks

### Task 1 — Connections surface (the main ask)

Make Sentry detect its own integration state rather than requiring someone to
notice. This is the generalisation of "we're missing API keys": the app should
say so itself.

**Files**
- Create `src/Sentry.Contracts/Connections.cs`
- Create `Services/LocalConnectionProbe.cs`
- Create `tests/Sentry.Contracts.Tests/ConnectionsTests.cs`
- Modify `MainPage.xaml`, `MainPage.xaml.cs` (new `Connections` rail item)

**Model**

```csharp
enum ConnectionState { Connected, Detected, Missing, Misconfigured, NotApplicable }

record ConnectionDescriptor(
    string Name,          // "Claude Code"
    string Category,      // Harness | Runtime | Credential | Connector
    ConnectionState State,
    string Detail,        // what was actually observed
    string Remedy);       // the specific next action, or empty when Connected
```

Reuse `PresenceTone` for colour so it matches the rest of the shell.

**Probes** (all read-only; none may print or transmit a secret value)
- Harnesses: `claude`, `codex`, `grok` on PATH → Connected / Missing
- Codex split case: config present but binary absent → **Misconfigured**, remedy
  names the install step. This case exists today and must be represented.
- Credentials: presence and *shape* only. Distinguish OAuth from API key —
  reporting Claude as "has credentials" would imply a key Sentry can use.
- Gateway + runtime: reuse the existing `SentryGatewayClient` probes
- Node: whether `sentry-node` is enrolled and last seen

**Acceptance**
- Every state has a distinct label; nothing reads Connected without a probe
- `Missing` and `Misconfigured` are distinct — one needs installing, one needs
  fixing
- Every non-Connected row carries a remedy naming the exact file or command
- A credential probe never reads a secret's value into memory beyond a length or
  presence check
- Tests cover: OAuth-not-API-key, config-without-binary, all-present, and the
  no-gateway case

### Task 2 — Sentry hooks alongside `claude-presence`

The integration plan requires this and it is not built. `claude-presence` runs on
all six events; Sentry must add its own **without replacing them**.

**Files**
- Create `src/Sentry.Node/Hooks/SentryHookReceiver.cs`
- Create `scripts/install-sentry-hooks.ps1`
- Create `tests/Sentry.Node.Tests/HookMergeTests.cs`

**Acceptance**
- Installing is idempotent and additive; re-running does not duplicate entries
- Existing `claude-presence` entries survive byte-identical — assert this
- Uninstall removes only Sentry's entries
- The script backs up `settings.json` before writing
- Hook payloads are sanitised: no prompt text, no file contents, no secrets

### Task 3 — Persist device credentials

Found while testing: the access token lives only in memory, so setup must be
repeated on every launch. Not acceptable for a daily-driver app.

**Files:** `Services/SettingsService.cs`, `Services/SentryGatewayClient.cs`

**Acceptance**
- Refresh token stored DPAPI-encrypted, CurrentUser scope, same as the API key
- Access token is **not** persisted — it is short-lived and re-obtained by refresh
- On launch, a stored refresh token silently restores the session
- A revoked device fails refresh and returns the user to the walkthrough with a
  clear reason
- Nothing writes a token to a log or to the settings file in plaintext

### Task 4 — Honest setup step 3

Step 3 currently reports Done when the admin snapshot is unavailable, because
`snapshot is { CanAnswer: false }` does not match `null`. A non-admin therefore
sees "verified" without anything having verified the runtime can answer.

**Acceptance**
- Runtime reachable **and** confirmed able to answer → Done
- Runtime reachable, model confirmed absent → Failed, naming the missing provider
- Runtime reachable, capability **unknown** (not an admin) → Done with an explicit
  caveat that it could not be confirmed. Never silently imply verification.

### Task 5 — Codex adapter *(blocked on decision 2)*

Only after the CLI is installed. Must use a dedicated automation profile that
does **not** inherit `approval_policy = "never"`, `sandbox_mode = "workspace-write"`,
or the four global MCP servers. Mirror `ClaudeAdapter`'s separate-settings
approach. Phase one uses `codex exec --json` over local stdio.

---

## 5. Environment facts that cost time

Carry these forward; each one was learned the hard way.

- **`127.0.0.1` is broken machine-wide for HTTP.** `::1` and `localhost` work.
  Confirmed again: `ssh -L` creates listeners on *both*, and only the `::1` one
  answers. The gateway address must be `http://localhost:18090`, never
  `http://127.0.0.1:18090`.
- **The desktop is packaged, so `LocalApplicationData` redirects.** Settings live
  at `%LOCALAPPDATA%\Packages\ECE61934-…_1z32rh13vfry6\LocalCache\Local\SentryAssistant\settings.json`,
  not `%LOCALAPPDATA%\SentryAssistant\`. Looking in the obvious place shows no
  file and invites the wrong conclusion.
- **Check process start times before trusting a running window.** A stale
  instance from an earlier session looked like a fresh build and very nearly
  produced a false verification.
- **`HttpClient.Timeout` beats any `CancellationToken`.** See §0.
- Use `& 'C:\Program Files\dotnet\dotnet.exe'` — the `dotnet` first on PATH is
  x86 and lacks the SDK.
- Tests are xUnit v3 on Microsoft Testing Platform. Do not revert to VSTest; its
  test host never connects on this machine.
- The Gateway serializes **snake_case**; C# clients need explicit
  `JsonPropertyName`. Assuming camelCase deserializes to nulls silently.
- The admin endpoint runs a live runtime probe and is slow. Client-wide short
  timeouts will time it out and — unless handled — report it as a permissions
  failure.
- `docker compose config` interpolates and prints secrets. Do not run it where
  output is captured.
- PowerShell `-replace` with multi-line replacements breaks on escaping. Use the
  Edit tool for structured file changes.
- Reach the Gateway from Windows via `ssh -L 18090:127.0.0.1:8090` — it binds
  loopback on the server by design.
- Drive the WinUI app through UI Automation (`scripts/`-style helper using
  `UIAutomationClient`), which doubles as an accessibility-name check.

---

## 6. Order of work

1. Task 3 (persist credentials) — everything else is tedious to test without it
2. Task 1 (connections surface) — the main ask
3. Task 4 (honest step 3) — small, and it is currently misleading
4. Task 2 (hooks) — self-contained
5. Task 5 (Codex) — only if the CLI gets installed

Commit after each task with evidence in the message. Do not batch them.

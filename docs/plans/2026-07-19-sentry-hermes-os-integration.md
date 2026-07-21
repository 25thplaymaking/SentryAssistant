# Sentry Hermes OS Integration Plan

> **For agentic workers:** Implement this plan task-by-task with a fresh verification gate after each task. Do not begin a later task while an earlier task's acceptance checks are failing.

**Goal:** Build a private, always-on, profile-based Sentry assistant in which an isolated, replaceable agent runtime runs on Bryce's Linux server, team members exchange durable conversational work orders through it, available trusted nodes provide Codex, Claude, and audited Grok Build execution, and private phone, desktop, Discord, SMS, voice, and web clients provide remote access.

**Architecture:** The Linux server is the control plane for identity, isolated personal agent instances, restricted team instances, shared work-order conversations, durable memory, schedules, connectors, task routing, skill governance, audit, and notifications. The Sentry Gateway owns every product contract and talks to Hermes or another runtime only through an `AgentRuntime` adapter. Team members do not need a coding harness; submitted work is routed to an available owner-approved Windows or Linux execution node, which invokes Codex, Claude, Grok Build, or a sandboxed agent worker only inside allowlisted workspaces. Personal memories and credentials never enter a team profile automatically; collaboration happens through explicitly shared teams, workspaces, context, tasks, and audit records.

**Tech stack:** Hermes Agent on Python 3.11 as the current recommended runtime; FastAPI, PostgreSQL, rootless Docker, Caddy, Authentik/OIDC, ASP.NET Core/.NET 10 Windows Worker, WinUI 3, SwiftUI, APNs, optional Twilio SMS, Gmail API, Discord gateway, Steam Web API, native Windows/iOS speech, optional local Whisper, and optional OpenAI Audio/Realtime APIs.

## 2026-07-20 Reconciliation — the web client is the hermes-webui fork, brought forward

Decision (Bryce, 2026-07-20): the **hermes-webui fork "Frontir Sentry"
(`SentryWebUI`, branch `frontir`) becomes Sentry's primary web client**, wired to
this Gateway. It replaces and pulls forward the from-scratch `web/sentry-gateway/`
recovery surface described in **Task 11**. Rationale: the fork is already deployed
and in daily use, so making it a proper multi-tenant Gateway client is the fastest
path to a working per-person web assistant and simultaneously realizes this plan's
web-client goal.

**This reorders delivery** (web-gateway client lands early, alongside Tasks 2–5, not
at Task 11). It does **not** change any Gateway contract, isolation constraint, or
release Gate.

**Approach A — the Gateway is the multiplexer.** The fork authenticates each user via
the Gateway's existing device-enrolment identity (access-token `pid` claim = profile),
and the Gateway routes each user to their own `sentry-hermes-<user>` container via the
already-built, fail-closed `HermesRuntime._instance(profile_id)`
(`app/agent_runtime/hermes.py:115-123`). The fork must stop addressing a Hermes
container directly (today it wrongly points at a single shared `hermes:8642`).

**Prerequisites still unbuilt (the real work — all subsets of Tasks 2/5 already scoped):**
1. Persist the profile→Hermes-endpoint map (base_url/port/**encrypted** api_key) beside
   `profiles.runtime_home`, and register per profile at startup/provision. Today only
   the single bootstrap profile is registered, in process memory (`app/main.py:28-44`).
2. A client-facing chat/turn route that requires `require_caller`, resolves
   `caller.profile_id`, and drives `HermesRuntime.send_turn`. Nothing calls it yet
   (only `capabilities()` is wired).
3. Provision a 2nd `sentry-hermes-<user>` container + home; repoint the fork at the
   Gateway (`sentry-gateway:8090`); add per-user login to the fork.

**First slice (proves the whole thing) — two profiles** (Bryce + one test user): logged
in as A, chat + panels reach only agent A; as B, only agent B; a mismatched profile
fails closed. Maps to this plan's Gate 1 → Gate 2 progression for the web client.

**Locked sub-decisions:** login reuses Gateway device-enrolment (no parallel password
store); personal profiles only for the first slices (team profiles deferred); the
provider-key panel is relabelled honestly now and real provider config moves to
per-agent provisioning. Fork management panels (Skills/Kanban/Memory/Cron/Profiles) are
fixed by routing through Gateway profile-scoped APIs + the existing skill-governance
lifecycle — **not** filesystem writes to a shared home.

### Slice-1 build progress

- [x] **1. Persist profile→endpoint** — `runtime_endpoints` (migration 004), Fernet-encrypted bearer key (`SENTRY_RUNTIME_ENC_KEY`), load+register at startup on top of the bootstrap profile, fail-closed for unregistered profiles. `app/agent_runtime/endpoints.py`. Commit `61ab96f`; 7 new tests, 242 pass.
- [x] **2. Gateway client chat route** — `POST /api/chat/turn`: authed → `require_caller` → `caller.profile_id` → `AgentRuntime.send_turn`, SSE streamed, one audit event per turn; unregistered/mismatched profile fails closed (503) before any stream; unauth → 401. `app/routes/chat.py`. Commit `cacc2fb`; 8 new tests, 250 pass. (Follow-ups: no `previous_response_id` continuity yet; mid-stream runtime error truncates rather than emitting an error frame.)

--- items below touch the LIVE control plane (grain.silo) ---

- [x] **3. Backend deployed live** (2026-07-21) — migration renamed to `005_runtime_endpoints.sql` and applied to prod Postgres; `SENTRY_RUNTIME_ENC_KEY` generated in-container and stored in `deploy/linux/.env`; `SENTRY_RUNTIME_ENC_KEY` passthrough added to the gateway service; gateway rebuilt + restarted, `/health/ready` healthy, `/api/chat/turn` enforcing 401. Additive: current webui behaviour unchanged. (Permanent per-user `sentry-hermes-<user>` containers are Phase-3 provisioning; slice-1 proved routing with throwaway endpoints.)
- [ ] **4. Fork per-user login + repoint** the fork at `sentry-gateway:8090`, forward the user's Gateway token so chat routes to their agent (replaces the shared password + direct `hermes:8642`). **← the remaining slice-1 piece; SentryWebUI `frontir` branch.**
- [x] **5. Exit test PROVEN LIVE** (2026-07-21) — reused two throwaway e2e profiles + two SSE listeners: profile A → `ep-a` (`summary: ok-ep-a`), profile B → `ep-b` (`ok-ep-b`), each listener hit exactly once (no cross-talk), unregistered profile → 503 reaching nothing. Exercised the full path: DB-persisted encrypted endpoint → env-key decrypt → startup registration → per-profile routing → SSE. Torn down clean; gateway healthy.

**Follow-ups noted during the live proof (non-blocking):** a chat turn currently returns a bare 500 if the audit INSERT fails (fail-closed on audit is defensible, but should be a clean error); `deploy/linux/.env` is mode 664 (world-readable) and holds secrets — pre-existing hygiene item; two immutable audit rows from the proof remain (honest artefact, attributed to e2e test users).

Working analysis + as-found evidence: `docs/specs/2026-07-20-multi-tenant-sentry-design.md`
(subordinate to this plan). Fork state: `docs/2026-07-20-session-report.md`.

## Global constraints

- Keep identity, authorization, work orders, audit, artifacts, connector grants, and client APIs independent of Hermes internals. No desktop, phone, web, or Node client may call a runtime-specific endpoint directly.
- Run the selected agent runtime as a non-root Linux user and keep its administrative dashboard bound to loopback.
- Run every candidate agent runtime inside a reviewed whole-process OS/container boundary. Treat in-process skill scanners, command denylists, and output redaction as review aids, not containment.
- The Windows Node initiates the connection; never expose Codex, Claude, PowerShell, or a Windows listener directly to the internet.
- Keep Codex, Claude, and Grok Build/model credentials on the execution node. The Linux server stores connector OAuth and issues short-lived work-order credentials only.
- Treat Gmail, Discord, Steam, web pages, and uploaded documents as untrusted content, never as authority to execute code.
- Never execute against human facing actions first, always prompt the end user before sending a message, email, etc; anything that directly interacts with another human must be approved.
- Default Gmail to read-only. Drafting and sending require separately granted scopes and a visible approval.
- Default Steam to the read-only Web API. Do not store a Steam password or session cookie.
- Give each personal runtime instance its own messaging credentials and caller allowlist. Give each team instance separate bot credentials and an explicit member allowlist, security for each persons data is paramount.
- Isolate every person's runtime home, memory, sessions, skills, schedules, connectors, devices, and secrets. Never use one writable runtime data directory from multiple instances.
- Treat personal-to-team memory sharing as an explicit, attributable action. Never promote private memories, Gmail, reminders, Steam data, or raw transcripts into a team profile automatically.
- Limit shared team runtime instances to team-safe tools and shared workspaces. Personal or elevated actions must route through the initiating person's private profile.
- Let any collaborator describe and refine a team work order without owning Codex, Claude, or an execution node. Harness and node assignment happen during triage, not during request creation.
- Keep the Sentry Gateway work-order record authoritative for identity, permissions, state, messages, approvals, and audit. Treat runtime work boards, including Hermes Kanban, as rebuildable execution projections keyed to the authoritative work-order ID.
- Use durable work-order runs for long-lived work. Use runtime subagents only for bounded planning, research, decomposition, and review whose summaries return to the parent run.
- Allow the assistant to propose, test, and revise skills, memories, prompts, and presets, but never silently activate its own code. Every skill version requires provenance, a full-source diff, automated evaluation, an authorized approval, scoped activation, and a one-click rollback.
- Treat plugins as privileged product code, not ordinary skills. Never auto-install or self-modify plugins, hooks, gateway adapters, authentication code, or the agent runtime.
- Keep agent-created skills in `Proposed` state with `skills.write_approval: true` and `skills.guard_agent_created: true`; run them first in an isolated evaluation profile with no production secrets or personal data.
- Preserve every request, clarification, implementation attempt, artifact, review decision, and change request as an attributable append-only event; never overwrite prior evidence when a new attempt begins.
- Use APNs/in-app delivery as the primary phone channel, Discord DM as the secondary channel, and SMS as an optional urgent fallback.
- Never place source code, email bodies, secrets, approval tokens, or sensitive evidence in an SMS or push-notification payload.
- Allow inbound SMS to request status, create reminders, or queue low-risk questions only. Elevated work, email sending, memory deletion, and connector changes require the authenticated Sentry app and biometrics.
- Speak only `task.resolved` events, using one sentence and a user-visible global mute toggle.
- Default voice input to push-to-talk and voice output to off. Do not implement an always-listening wake word in the first release, retain raw audio by default, or send an API key to a desktop/browser/mobile client.
- Prefer a chained voice pipeline for work-order and approval flows: reviewable speech-to-text, normal text policy/orchestration, then text-to-speech only after an approved resolution. Keep low-latency speech-to-speech as an optional later mode.
- Admins can be specified to be able to administrate overall Hermes actions, review logs, etc.
- Disclose clearly whenever an OpenAI-generated or other synthetic voice is used.
- Preserve repository `AGENTS.md` and `CLAUDE.md` authority. Reconcile branch, status, handoffs, and guidance before dispatching edits.
- Never present a command run, test, deployment, or production state as complete without corresponding evidence.
- Do not let Codex, Claude, and Grok Build edit the same worktree concurrently. Use one primary worker and optional read-only reviewers.
- Every remote work order, approval, connector mutation, memory write, and result must have an immutable audit event.

## Reviewed current state

- `C:\Users\Bryce\Desktop\SentryAssistant` is a working WinUI prototype but is not yet a Git repository.
- The desktop prototype currently calls OpenAI directly and protects its API key with Windows DPAPI. This direct provider path will be replaced by the Linux gateway.
- Codex is configured for high-reasoning work with `workspace-write` and `approval_policy = "never"`; it has Enfusion, Node REPL, Galactic, and OpenAI documentation MCP servers. Sentry must use a dedicated automation profile rather than inheriting that global policy.
- Claude Code 2.1.215 is installed. Its global mode is `acceptEdits`, the dangerous-mode reminder is skipped, and presence hooks run for session, prompt, tool, stop, notification, and session-end events. Sentry must add its hook alongside `claude-presence`, not replace it.
- Claude already has Google Drive, Gmail, and Google Calendar OAuth connectors. Those credentials remain Claude-owned; Sentry receives its own Gmail OAuth grant through the Linux gateway.
- Codex provides stable non-interactive JSONL output and a stable MCP-server mode. Codex app-server provides richer streamed lifecycle events, but its WebSocket transport is experimental, so phase one uses local process/stdio boundaries only.
- Hermes native Windows support is beta. Running Hermes on the Linux server avoids that path while retaining the Windows machine as the execution node.
- Hermes already provides isolated profiles, per-profile `SOUL.md`, personality overlays, profile distributions, searchable sessions, a durable Kanban board, a learning loop, and native voice providers. It does not provide one official turnkey "personal assistant" distribution that matches this product; Sentry will ship reviewed presets as versioned profile distributions.
- Hermes skill and plugin code runs with agent-process privileges. Its own security policy requires a whole-process boundary for open web, inbound email, multi-user channels, and other untrusted input, which makes proposal-only skill activation and container isolation release requirements.
- OpenClaw is the strongest current alternative for the consumer-assistant surface because it already includes a local-first gateway, Windows and iOS companions, channel routing, skills, voice wake/talk modes, and device pairing. Hermes remains the current recommendation for Sentry's durable learning, session search, multi-profile Kanban, and work-order orchestration, but Task 2 must produce an evidence-backed runtime decision before production lock-in.
- Letta is a credible memory-first alternative, Open WebUI is a credible multi-user chat/voice shell, and n8n is a credible deterministic connector/workflow companion. None replaces Sentry's identity, work-order, approval, remote-code, and audit control plane; use them only behind the same runtime/connector boundaries if later evaluation justifies it.
- Grok Build is an Apache-2.0 Rust coding harness with headless streaming JSON and Agent Client Protocol support, so it is a useful third adapter and open-source comparison baseline. Its public source is a recent periodic snapshot rather than the entire production monorepo, and reported repository-upload behavior makes outbound traffic a release gate: pin and review the source, default it to local-first/no-upload operation, and prohibit private workspaces until network-isolation tests pass. Sources: [xAI announcement](https://x.ai/news/grok-build-open-source), [repository](https://github.com/xai-org/grok-build), and [Build overview](https://docs.x.ai/build/overview).

---

### Task 1: Baseline the product and establish contracts

**Files:**
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\.git\`
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\SentryAssistant.sln`
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\src\Sentry.Contracts\Sentry.Contracts.csproj`
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\src\Sentry.Contracts\WorkOrders.cs`
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\src\Sentry.Contracts\Events.cs`
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\tests\Sentry.Contracts.Tests\Sentry.Contracts.Tests.csproj`
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\Themes\SentryTheme.xaml`
- Create: `C:\Users\Bryce\Desktop\SentryAssistant\Themes\SentryControlStyles.xaml`
- Modify: `C:\Users\Bryce\Desktop\SentryAssistant\App.xaml`
- Modify: `C:\Users\Bryce\Desktop\SentryAssistant\MainWindow.xaml`
- Modify: `C:\Users\Bryce\Desktop\SentryAssistant\MainPage.xaml`
- Modify: `C:\Users\Bryce\Desktop\SentryAssistant\MainPage.xaml.cs`
- Modify: `C:\Users\Bryce\Desktop\SentryAssistant\.gitignore`
- Modify: `C:\Users\Bryce\Desktop\SentryAssistant\README.md`

**Interfaces:**

```csharp
public sealed record WorkOrder(
    Guid Id,
    Guid RequestedByUserId,
    Guid ProfileId,
    Guid? TeamId,
    Guid ConversationSessionId,
    Guid? AssignedUserId,
    Guid? ExecutionNodeId,
    string? Harness,
    string? WorkspaceId,
    string Prompt,
    WorkOrderState State,
    WorkOrderMode Mode,
    DateTimeOffset ExpiresAt,
    IReadOnlyList<string> CompletionCriteria,
    string CorrelationId);

public enum WorkOrderMode { ReadOnly, WorkspaceWrite, ApprovedElevated }

public enum WorkOrderState
{
    Draft,
    Submitted,
    NeedsClarification,
    Triaged,
    Assigned,
    InProgress,
    NeedsInput,
    ReadyForReview,
    ChangesRequested,
    Resolved,
    Closed,
    Cancelled,
    Failed
}

public sealed record HarnessEvent(
    Guid WorkOrderId,
    string Harness,
    string Type,
    DateTimeOffset OccurredAt,
    string Summary,
    JsonElement Evidence);

public sealed record NotificationEnvelope(
    Guid Id,
    string EventType,
    NotificationSeverity Severity,
    string Title,
    string RedactedSummary,
    Uri DeepLink,
    IReadOnlyList<string> AllowedChannels,
    DateTimeOffset ExpiresAt,
    string CorrelationId);

public enum NotificationSeverity { Routine, Important, Urgent, Security }
```

- [x] Initialize Git with branch `main`, record the current WinUI prototype as the baseline, and ensure `bin/`, `obj/`, local secrets, recordings, and generated packages are ignored.
- [x] Add the solution, contracts project, and xUnit contract tests.
- [x] Test JSON round-tripping and reject expired work orders, missing requester/profile/conversation identity, unknown states/modes, and empty completion criteria. Permit drafts without harness, workspace, assignee, or node; require those fields before transition to `Assigned` or `InProgress`.
- [x] Establish an original Sentry design system inspired by Codex's interaction grammar without copying OpenAI branding or assets: restrained neutral surfaces, compact 30-pixel navigation rows, 10-pixel control radii, a readable conversation column capped near 42rem, low-chrome dividers, and quiet status color reserved for actionable state.
- [x] Replace the generic `NavigationView` presentation with a custom adaptive three-zone shell: a narrow workstream rail, a focused conversation/command canvas, and a collapsible evidence/status inspector. Keep the composer docked, make active profile/workspace/harness identity continuously visible, and collapse the inspector and rail labels at smaller widths.
- [ ] Add purposeful, reduced-motion-aware state transitions for `Queued`, `Running`, `NeedsInput`, and `Resolved`; working states may pulse or travel subtly, while only `Resolved` receives the distinct completion flourish tied to optional one-sentence speech.
- [ ] Build and launch the packaged WinUI app, visually verify the real window at normal and narrow widths in light, dark, and high-contrast themes, and confirm keyboard navigation and accessible names before treating the shell foundation as complete.
- [x] Run `dotnet test SentryAssistant.sln -c Debug -p:Platform=x64`; expected result: all contract tests pass.
- [ ] Commit `chore: baseline Sentry assistant and work-order contracts`.

### Task 2: Prove the runtime boundary, select the agent runtime, and deploy the private Linux control plane

**Files:**
- Create: `deploy/linux/compose.yaml`
- Create: `deploy/linux/Caddyfile`
- Create: `deploy/linux/authentik/blueprint.yaml`
- Create: `deploy/linux/hermes/config.yaml`
- Create: `deploy/linux/hermes/SOUL.md`
- Create: `deploy/linux/hermes/USER.md`
- Create: `deploy/linux/systemd/sentry-gateway.service`
- Create: `server/sentry_gateway/pyproject.toml`
- Create: `server/sentry_gateway/app/main.py`
- Create: `server/sentry_gateway/app/config.py`
- Create: `server/sentry_gateway/app/agent_runtime/base.py`
- Create: `server/sentry_gateway/app/agent_runtime/capabilities.py`
- Create: `server/sentry_gateway/app/agent_runtime/hermes.py`
- Create: `server/sentry_gateway/app/agent_runtime/openclaw.py`
- Create: `server/sentry_gateway/app/agent_runtime/registry.py`
- Create: `server/sentry_gateway/app/hermes_profiles.py`
- Create: `server/sentry_gateway/tests/test_health.py`
- Create: `server/sentry_gateway/tests/test_agent_runtime_contract.py`
- Create: `docs/decisions/001-agent-runtime.md`

**Services:**

```text
Internet/iPhone -> Caddy :443 -> Sentry Gateway :8080
                                  -> Authentik OIDC
                                  -> PostgreSQL
                                  -> Selected runtime loopback adapter
Windows Node ---- outbound WSS ---^
Runtime administrative dashboards stay on 127.0.0.1 and are not proxied publicly.
```

**Runtime contract:**

```python
class AgentRuntime(Protocol):
    async def capabilities(self, profile_id: UUID) -> RuntimeCapabilities: ...
    async def create_session(self, profile_id: UUID, scope: SessionScope) -> RuntimeSession: ...
    async def send_turn(self, request: RuntimeTurn) -> AsyncIterator[RuntimeEvent]: ...
    async def cancel(self, run_id: UUID) -> None: ...
    async def search_sessions(self, query: ScopedSessionQuery) -> list[SessionHit]: ...
    async def project_work_order(self, projection: WorkOrderProjection) -> None: ...
```

- [ ] Create a dedicated non-root `sentry` account and persistent directories under `/srv/sentry/{gateway,runtimes,postgres,backups}` with owner-only permissions.
- [ ] Implement the `AgentRuntime` contract before using any Hermes- or OpenClaw-specific API. Normalize sessions, turns, tool progress, approvals, cancellation, capability discovery, work-order projections, memory search, errors, and usage into Sentry-owned event types.
- [ ] Run a time-boxed Hermes-versus-OpenClaw bake-off in separate rootless containers using the same fixtures: private and team profile isolation, a restarted multi-turn session, a durable work order with human input and revision, proposed-skill review, Discord/phone routing, push-to-talk transcription, resolution-only speech, node/harness dispatch, backup/restore, and version upgrade.
- [ ] Score security boundary, multi-user isolation, durable workflow fidelity, skill governance, memory/search quality, voice/device coverage, adapter complexity, observability, upgrade/rollback effort, resource use, license, and upstream maintenance. Record evidence and the selected runtime in `docs/decisions/001-agent-runtime.md`; the current default is Hermes, but selection must be reversible by configuration.
- [ ] Pin the selected runtime to a reviewed release/commit and create one container per active profile, each with a distinct data volume, service identity, resource limit, network policy, and lifecycle.
- [ ] If Hermes wins, configure personal instances with built-in memory, `memory.write_approval: true`, `skills.write_approval: true`, `skills.guard_agent_created: true`, explicit toolsets, runtime lazy installs disabled, and a rootless Docker terminal backend. Configure team instances with a smaller team-safe toolset and no personal connectors. If OpenClaw wins, map equivalent policies through its adapter and document any gaps that Sentry must enforce itself.
- [ ] Configure Caddy to expose only `/api`, `/auth`, `/gateway`, and the personal web UI through HTTPS.
- [ ] Configure Authentik from a checked-in secret-free blueprint with one OIDC web client, one public PKCE mobile client, one Node enrollment client, disabled public registration, and required WebAuthn/passkey validation.
- [ ] Add `/health/live` and `/health/ready`; readiness must fail if PostgreSQL, the selected runtime, or the signing-key provider is unavailable. Report runtime name, pinned version, capabilities, and degradation without exposing secrets.
- [ ] Store secrets outside Git in the server secret store; `config.yaml` contains only environment-variable references.
- [ ] Run `pytest server/sentry_gateway/tests/test_health.py server/sentry_gateway/tests/test_agent_runtime_contract.py -q`; expected result: both adapters pass the same contract fixtures and the selected runtime passes live container smoke checks.
- [ ] Run `docker compose -f deploy/linux/compose.yaml config`; expected result: valid configuration with no secret values rendered.
- [ ] Commit `feat: add replaceable private agent control plane`.

### Task 3: Add invite-only identity, device enrollment, and audit

**Files:**
- Create: `server/sentry_gateway/app/auth/oidc.py`
- Create: `server/sentry_gateway/app/auth/device_enrollment.py`
- Create: `server/sentry_gateway/app/auth/work_order_signing.py`
- Create: `server/sentry_gateway/app/audit/models.py`
- Create: `server/sentry_gateway/app/audit/service.py`
- Create: `server/sentry_gateway/migrations/001_identity_audit.sql`
- Create: `server/sentry_gateway/tests/test_auth.py`
- Create: `server/sentry_gateway/tests/test_work_order_signing.py`

**Security flow:**

```text
Each invited person authenticates with their own Authentik passkey.
Desktop/iPhone use OIDC Authorization Code + PKCE.
Each execution node uses one-time enrollment, then mTLS plus a rotating node token bound to its owner.
Gateway signs each WorkOrder; Node validates signature, audience, expiry, nonce,
requesting user, profile, team membership, execution-node ownership, workspace ID,
harness, and requested mode before execution.
```

- [ ] Configure invite-only human accounts, mandatory passkey enrollment, per-person recovery codes, Authentik group claims, and no public self-registration.
- [ ] Implement a five-minute device enrollment code that can be approved only from an authenticated gateway session.
- [ ] Bind every enrolled phone, browser session, desktop, and execution node to one user profile; permit explicit device names, last-seen status, and immediate revocation.
- [ ] Issue access tokens for 15 minutes and rotating refresh tokens bound to the enrolled user and device.
- [ ] Add append-only audit tables for actor, device, action, target, decision, correlation ID, and redacted evidence hash.
- [ ] Reject replayed nonces, expired work orders, unknown devices, wrong audiences, revoked devices, cross-user node selection, and non-member team access in tests.
- [ ] Run `pytest server/sentry_gateway/tests/test_auth.py server/sentry_gateway/tests/test_work_order_signing.py -q`; expected result: all auth and replay tests pass.
- [ ] Commit `feat: add private identity device enrollment and audit`.

### Task 4: Add isolated personal profiles and shared team spaces

**Files:**
- Create: `server/sentry_gateway/app/profiles/models.py`
- Create: `server/sentry_gateway/app/profiles/service.py`
- Create: `server/sentry_gateway/app/teams/models.py`
- Create: `server/sentry_gateway/app/teams/authorization.py`
- Create: `server/sentry_gateway/app/teams/invitations.py`
- Create: `server/sentry_gateway/migrations/002_profiles_teams.sql`
- Create: `server/sentry_gateway/tests/test_profile_isolation.py`
- Create: `server/sentry_gateway/tests/test_team_authorization.py`
- Create: `deploy/linux/hermes/profile-template/config.yaml`
- Create: `deploy/linux/hermes/team-template/config.yaml`

**Profile model:**

```python
class ProfileKind(StrEnum):
    PERSONAL = "personal"
    TEAM = "team"

class TeamRole(StrEnum):
    OWNER = "owner"
    COLLABORATOR = "collaborator"
    OBSERVER = "observer"

class ContextVisibility(StrEnum):
    PRIVATE = "private"
    TEAM = "team"
    WORKSPACE = "workspace"
```

- [ ] Provision one isolated selected-runtime container and data volume for each personal profile and one restricted instance for each team profile; never mount a personal profile directory into a team container.
- [ ] Give every personal profile separate `config.yaml`, secrets, `SOUL.md`, `USER.md`, memory, sessions, cron, connector grants, bot tokens, and backup encryption scope.
- [ ] Add team creation, expiring email invitation, accept/decline, member removal, leave-team, and team deletion flows with immutable audit events.
- [ ] Implement roles: owners manage members, shared workspaces, policies, and team connectors; collaborators create and review team work; observers can read shared results and evidence but cannot dispatch or approve work.
- [ ] Add explicit profile switching to the API contract. Every conversation, work order, memory, reminder, connector, notification, and audit event carries a `profile_id`; team-scoped records also carry `team_id`.
- [ ] Require an explicit `Share with team` action to copy a sanitized personal result or context item into team scope. Record the original author, source profile, content hash, visibility, and timestamp.
- [ ] Prevent team profiles from accessing personal Gmail, Steam, reminders, raw transcripts, personal memory, device lists, or private workspace registrations.
- [ ] Keep execution nodes private by default. Let a node owner expose only named workspace capabilities to a team, with allowed harnesses and work-order modes; never expose the rest of the machine or its private workspaces.
- [ ] Use distinct Discord bot tokens per concurrently running runtime profile. Use a dedicated team bot or team channel for shared work; never reuse a personal bot token across profiles.
- [ ] Test direct-object-reference attacks, profile-ID substitution, removed-member access, observer dispatch attempts, cross-profile search, cross-profile notification delivery, and concurrent container lifecycle.
- [ ] Run `pytest server/sentry_gateway/tests/test_profile_isolation.py server/sentry_gateway/tests/test_team_authorization.py -q`; expected result: every isolation and role test passes.
- [ ] Commit `feat: add isolated profiles and shared team spaces`.

### Task 5: Add conversational work orders and durable team orchestration

**Files:**
- Create: `server/sentry_gateway/app/workorders/models.py`
- Create: `server/sentry_gateway/app/workorders/service.py`
- Create: `server/sentry_gateway/app/workorders/transitions.py`
- Create: `server/sentry_gateway/app/workorders/messages.py`
- Create: `server/sentry_gateway/app/workorders/routing.py`
- Create: `server/sentry_gateway/app/workorders/hermes_kanban.py`
- Create: `server/sentry_gateway/app/workorders/skill_proposals.py`
- Create: `server/sentry_gateway/migrations/003_workorders_collaboration.sql`
- Create: `server/sentry_gateway/tests/test_workorder_conversation.py`
- Create: `server/sentry_gateway/tests/test_workorder_transitions.py`
- Create: `server/sentry_gateway/tests/test_workorder_routing.py`
- Create: `server/sentry_gateway/tests/test_team_skill_proposals.py`

**Conversation and execution model:**

```text
Colleague chats with the Team Assistant from phone/web/desktop/Discord.
  -> Selected runtime asks bounded clarifying questions.
  -> Gateway creates a Draft with acceptance criteria and links one team session.
  -> Colleague submits; Bryce receives a triage notification.
  -> Bryce/Assistant assigns server sandbox, Bryce's shared Node, or another approved Node.
  -> Each execution attempt becomes an immutable WorkOrderRun.
  -> NeedsInput messages return to the shared conversation.
  -> Implementation and evidence enter ReadyForReview.
  -> Requester accepts -> Resolved, or requests changes -> a new run.
```

**Authority split:**

```text
PostgreSQL WorkOrder + Message + Run + Decision = workflow and authorization authority
Selected-runtime team session = conversational context and searchable history
Runtime work board (Hermes Kanban when selected) = rebuildable execution projection
Sentry WorkOrder + WorkOrderRun rows = durable orchestration and decomposition authority
Git branch/PR + test/deploy evidence = implementation evidence
Object storage = immutable attachments and result artifacts
```

- [ ] Create append-only tables for work orders, messages, state transitions, assignments, runs, decisions, artifacts, subscriptions, and skill proposals. Every row carries actor, team, profile, timestamp, correlation ID, and content/evidence hash.
- [ ] Create one shared selected-runtime conversation session per work order. Store the canonical human messages in PostgreSQL with actor identity, and project them into that session so both collaborators can continue the same task without sharing either person's private chat history.
- [ ] Let a collaborator create a draft by natural conversation without choosing a harness, node, repository path, or implementation method. The assistant must produce a title, problem statement, desired outcome, constraints, acceptance criteria, priority, and unresolved questions for confirmation.
- [ ] Enforce the state machine in `transitions.py`. Only the requester can submit a draft; owners/collaborators with triage permission can assign; only the requester or an owner can accept `ReadyForReview`; every `ChangesRequested` transition creates a fresh run while preserving earlier results.
- [ ] Add a team inbox with `Submitted`, `NeedsClarification`, `NeedsAssignment`, `NeedsInput`, `ReadyForReview`, `Failed`, and `Resolved` views. Subscribe requester, assignee, and explicit watchers to terminal and attention-required events.
- [ ] Route read-only planning, summarization, decomposition, and research to the restricted Linux team sandbox when policy allows. Route repository implementation to an owner-approved Node/workspace or an isolated Linux repository worker.
- [ ] Require Bryce's approval before the first workspace-write run on a Node he owns unless that exact team/workspace/mode combination has a stored policy grant. Never auto-authorize merge, deployment, secret access, or elevated commands from a colleague's request.
- [ ] Project each submitted work order into the selected runtime using the work-order ID as the idempotency key and tenant/team ID as the namespace. The Hermes adapter uses Kanban; other adapters use their native work surface or a Sentry-owned projection. Reconcile runtime events into WorkOrderRun records without letting the projection bypass Gateway authorization.
- [ ] Let the assistant decompose a work order into durable Sentry child runs for research, implementation, test, and review, optionally projected into Hermes Kanban. Use `delegate_task` only when the selected runtime supports it and only inside a bounded run with `max_concurrent_children: 3` and `max_spawn_depth: 1`; persist only child summaries and evidence links into the work-order timeline.
- [ ] Add server-side skill proposals. A collaborator can describe or upload a proposed team skill, but it is staged with `skills.write_approval: true`; an owner reviews its full diff and supporting code before activation in the team profile.
- [ ] Allow new implementation text, patches, screenshots, files, repository URLs, and acceptance notes to be added to the shared conversation. Scan uploads, store immutable originals, extract safe context, and record provenance before the runtime consumes them.
- [ ] Generate a deterministic handoff packet for every assignment: work-order identity, current accepted requirements, applicable guidance hashes, workspace/repository state, prior attempts, unresolved questions, allowed actions, completion criteria, and required evidence.
- [ ] Generate a result packet for every run: actor/harness/node, branch/worktree, commits/PR, files changed, tests run, verification results, artifacts, remaining gates, concise resolution, and status boundary (`implemented`, `tested`, `deployed`, or `user-confirmed`).
- [ ] Test two-person clarification, requester/assignee message ordering, offline replies, idempotent submission, concurrent edits, reassignment, changes requested, failed runs, child-task completion, skill rejection, removed-member access, and exact reconstruction after Gateway restart.
- [ ] Run `pytest server/sentry_gateway/tests/test_workorder_conversation.py server/sentry_gateway/tests/test_workorder_transitions.py server/sentry_gateway/tests/test_workorder_routing.py server/sentry_gateway/tests/test_team_skill_proposals.py -q`; expected result: every workflow, authorization, and reconstruction test passes.
- [ ] Commit `feat: add conversational team work orders and durable orchestration`.

### Task 6: Add governed adaptive skills and personal-assistant presets

**Files:**
- Create: `server/sentry_gateway/app/skills/models.py`
- Create: `server/sentry_gateway/app/skills/proposals.py`
- Create: `server/sentry_gateway/app/skills/scanner.py`
- Create: `server/sentry_gateway/app/skills/evaluator.py`
- Create: `server/sentry_gateway/app/skills/registry.py`
- Create: `server/sentry_gateway/app/skills/activation.py`
- Create: `server/sentry_gateway/app/presets/service.py`
- Create: `server/sentry_gateway/migrations/004_skills_presets_runtime.sql`
- Create: `deploy/linux/profiles/personal-operator/distribution.yaml`
- Create: `deploy/linux/profiles/personal-operator/SOUL.md`
- Create: `deploy/linux/profiles/team-coordinator/distribution.yaml`
- Create: `deploy/linux/profiles/coding-orchestrator/distribution.yaml`
- Create: `deploy/linux/profiles/research-briefing/distribution.yaml`
- Create: `server/sentry_gateway/tests/test_skill_lifecycle.py`
- Create: `server/sentry_gateway/tests/test_skill_isolation.py`
- Create: `server/sentry_gateway/tests/test_presets.py`

**Governed learning lifecycle:**

```text
Observed need -> Proposed -> Scanned -> Evaluated -> AwaitingApproval
                                              |             |
                                           Rejected       Active -> Superseded
                                                             |
                                                          RolledBack
```

- [ ] Build four Sentry-owned profile distributions rather than depending on a nonexistent official turnkey personal-assistant preset: `Personal Operator` for reminders, briefs, personal memory, and concise Jarvis-style resolutions; `Team Coordinator` for shared work orders and follow-up; `Coding Orchestrator` for Codex/Claude routing and evidence; and `Research & Briefing` for source-attributed research. Keep secrets, user memories, sessions, and personal data out of distribution repositories.
- [ ] Let a user choose a preset during onboarding and preview its identity, tools, connector scopes, scheduled jobs, memory policy, voice behavior, and estimated model usage. Applying or updating a preset must preserve user-owned data and show a versioned diff.
- [ ] Let the assistant create a skill proposal after a user request or an accepted work order reveals a reusable procedure. The proposal must include the full `SKILL.md`, every script/dependency, authoring session, source work order, intended profiles, required tools/secrets/network domains, evaluation cases, and a plain-language risk summary.
- [ ] Keep proposals inactive. Scan the complete source tree for hidden executables, imports, subprocess/network use, credential access, prompt injection, encoded payloads, install-time code, and undeclared dependencies; store the scanner evidence but never claim that scanning is a security boundary.
- [ ] Evaluate each proposal in a disposable, secret-free container with synthetic fixtures and a deny-by-default network. Test success cases, refusal cases, prompt-injection resistance, cross-profile access, secret exfiltration, destructive commands, repeatability, latency, and model/token budget.
- [ ] Require an owner to review the full diff and evaluation report before activation. Activate only the approved immutable content hash for selected profiles, start with a canary profile, monitor failures and policy violations, and provide immediate rollback to the prior version.
- [ ] Allow an active skill to propose a new version from observed failures, but send that revision through the entire lifecycle again. Never let a running skill edit its own active files, lower its permissions, approve itself, or hide prior versions.
- [ ] Keep plugin, hook, runtime-adapter, authentication, and gateway changes outside this learning loop. They require normal repository review, tests, release signing, and deployment; no assistant conversation can install them directly.
- [ ] Give every skill a capability grant for filesystem roots, tools, network destinations, connector scopes, data visibility, maximum runtime, and spend. Default grants to none and never pass profile or provider credentials into an evaluation container.
- [ ] Add runtime capability/version drift checks so a preset or skill becomes `NeedsReview` instead of failing open when Hermes/OpenClaw, a model provider, or a tool contract changes.
- [ ] Test agent-authored, user-authored, imported, malicious, dependency-confused, superseded, rolled-back, cross-profile, and runtime-upgrade cases. Prove that no unapproved version can be loaded after restart or restore.
- [ ] Run `pytest server/sentry_gateway/tests/test_skill_lifecycle.py server/sentry_gateway/tests/test_skill_isolation.py server/sentry_gateway/tests/test_presets.py -q`; expected result: every lifecycle, isolation, evaluation, and rollback test passes.
- [ ] Commit `feat: add governed adaptive skills and assistant presets`.

### Task 7: Curate memory and synchronize context safely

**Files:**
- Create: `src/Sentry.Node/Context/ContextManifest.cs`
- Create: `src/Sentry.Node/Context/ContextCollector.cs`
- Create: `src/Sentry.Node/Context/SecretFilter.cs`
- Create: `server/sentry_gateway/app/context/routes.py`
- Create: `server/sentry_gateway/app/context/storage.py`
- Create: `server/sentry_gateway/app/context/memory_review.py`
- Create: `server/sentry_gateway/tests/test_context_ingest.py`
- Modify: `deploy/linux/hermes/USER.md`

**Context manifest:**

```json
{
  "workspaceId": "25thvid-website",
  "head": "git-sha",
  "branch": "branch-name",
  "dirty": true,
  "guidanceHashes": { "AGENTS.md": "sha256", "CLAUDE.md": "sha256" },
  "handoffs": [],
  "artifacts": [],
  "capturedAt": "RFC3339 timestamp"
}
```

- [ ] Seed Bryce's personal runtime user-context file with a user-reviewed digest: Windows-first workflow, 25thVID/Galactic/Palantir projects, evidence-before-completion, real provider wiring before preview polish, concise resolution summaries, and respect for scoped repository guidance. Give each additional person an independent onboarding and profile digest.
- [ ] Keep `memory.write_approval: true` for every personal and team profile; show proposed durable memories only to the relevant profile's authorized users before they are committed.
- [ ] Implement repository context manifests that collect Git status, HEAD, current branch, applicable guidance, handoff files, and selected evidence while excluding `.git`, credentials, environment files, dependencies, builds, binaries, and private vaults.
- [ ] Upload large approved context artifacts to profile-scoped encrypted object storage and keep hashes, ownership, visibility, provenance, and team/workspace metadata in PostgreSQL.
- [ ] Import normalized Codex memory summaries and approved Claude handoffs; do not bulk-copy raw transcripts by default.
- [ ] Add an explicit per-workspace `Allow raw transcript archive` switch, default off.
- [ ] Retain team work-order sessions, accepted decisions, run summaries, approved skills, and searchable handoff/result packets on the Linux server. Configure retention by record class; never let ordinary chat pruning delete an open work order or its audit/evidence chain.
- [ ] Index team work-order conversations for selected-runtime session search using team/workspace/work-order scope. Return actor-attributed messages and final decisions, and prevent personal sessions from appearing in team searches.
- [ ] Version approved team skills with author, reviewer, source work order, content hash, enabled profiles, and rollback target. Keep rejected and superseded proposals in the audit trail without loading them into the active runtime.
- [ ] Test secret filtering with API keys, OAuth tokens, SSH private-key markers, `.env` files, and protected document paths.
- [ ] Commit `feat: add reviewed memory and context synchronization`.

### Task 8: Build per-person Sentry Nodes and harness adapters

**Files:**
- Create: `src/Sentry.Node/Sentry.Node.csproj`
- Create: `src/Sentry.Node/Program.cs`
- Create: `src/Sentry.Node/Gateway/NodeConnection.cs`
- Create: `src/Sentry.Node/Security/WorkOrderValidator.cs`
- Create: `src/Sentry.Node/Workspaces/WorkspaceRegistry.cs`
- Create: `src/Sentry.Node/Harnesses/IHarnessAdapter.cs`
- Create: `src/Sentry.Node/Harnesses/CodexAdapter.cs`
- Create: `src/Sentry.Node/Harnesses/ClaudeAdapter.cs`
- Create: `src/Sentry.Node/Harnesses/GrokBuildAdapter.cs`
- Create: `src/Sentry.Node/Harnesses/HarnessProcess.cs`
- Create: `src/Sentry.Node/Hooks/SentryHookReceiver.cs`
- Create: `src/Sentry.Node/appsettings.json`
- Create: `tests/Sentry.Node.Tests/`

**Adapter contract:**

```csharp
public interface IHarnessAdapter
{
    string Name { get; }
    Task<WorkOrderResult> ExecuteAsync(
        WorkOrder order,
        WorkspaceRegistration workspace,
        IProgress<HarnessEvent> events,
        CancellationToken cancellationToken);
}
```

- [ ] Run each Node at sign-in under its owner's user account, not `SYSTEM`, and connect outbound to the Linux gateway with exponential backoff.
- [ ] Bind every Node to its owner and register workspace IDs to fixed local paths; never accept a remote raw filesystem path or let one person select another person's private node. Permit team work only through an owner-approved team workspace registration on that Node.
- [ ] Allow a colleague without a local coding harness to submit work to an explicitly shared team workspace on an owner-approved Node; preserve the requester, node owner, harness, approvals, and resulting evidence as separate audit identities.
- [ ] Accept only `Assigned` work orders whose assignee, execution node, harness, workspace, expiry, and mode have passed Gateway authorization. Stream run events back to the linked shared work-order conversation and never expose unrelated local harness sessions.
- [ ] Add a dedicated Codex automation profile with workspace-write containment, network disabled by default, a pinned model chosen from live Codex capabilities, and no inherited broad MCP servers.
- [ ] Use `codex exec --json` for phase-one delegated jobs and parse `thread.started`, `turn.started`, `item.*`, `turn.completed`, `turn.failed`, and `error` events. Treat the packaged Codex executable's current shell access failure as a preflight failure and install/use the supported standalone CLI before enabling dispatch.
- [ ] Add Codex `Stop` and `SessionStart` hooks that post sanitized events to the local Node; do not replace other hooks.
- [ ] Create a separate Claude automation settings file that does not inherit `skipDangerousModePermissionPrompt`, and invoke `claude -p --output-format stream-json` with explicit tool allowlists.
- [ ] Add Sentry hook commands alongside the existing `claude-presence hook` entries for session, prompt, stop, notification, and session end.
- [ ] Add Grok Build as an optional third adapter and open-source baseline. Prefer its Agent Client Protocol stdio boundary when stable; otherwise invoke `grok -p "..." --output-format streaming-json` and normalize its lifecycle, evidence, cancellation, and failure events through `IHarnessAdapter`.
- [ ] Pin Grok Build to an audited commit and record the source/license provenance. Before enabling a private workspace, review its outbound upload/telemetry paths, run it with no credentials beyond the selected model endpoint, deny network destinations other than that endpoint, and prove that repository files are not uploaded outside the explicitly approved prompt/tool exchange. A failed or unverifiable isolation check disables the adapter.
- [ ] Keep xAI or custom-model credentials node-local and permit local/self-hosted model endpoints where capability and quality tests pass. Never forward a provider credential through the Gateway or expose it to another person's profile.
- [ ] Run the same read-only, workspace-edit, recovery, cancellation, malformed-stream, and evidence-attribution fixtures across Codex, Claude, and Grok Build. Record quality, latency, token/cost, tool-safety, and reproducibility as baseline data; do not automatically route sensitive work to the cheapest or newest harness.
- [ ] Map `ReadOnly` to planning/read tools; map `WorkspaceWrite` to edits and a bounded command allowlist; reject `ApprovedElevated` unless the exact action was approved from an enrolled client.
- [ ] Create a fresh Git worktree for remote coding tasks. Never dispatch both harnesses as writers to the same worktree; optionally dispatch the second as a read-only reviewer after the primary finishes.
- [ ] Stream working events to the UI without speech. Emit a speakable event only after a verified `task.resolved` result.
- [ ] Test offline queueing, cancellation, process crash, malformed JSONL, denied workspace, expired order, and reconnect/resume behavior.
- [ ] Run `dotnet test SentryAssistant.sln -c Debug -p:Platform=x64`; expected result: contracts and Node tests pass.
- [ ] Commit `feat: add trusted Codex Claude and Grok Build execution node`.

### Task 9: Add profile-scoped Gmail, Discord, Steam, reminders, voice, and daily briefs

**Files:**
- Create: `server/sentry_gateway/app/connectors/gmail.py`
- Create: `server/sentry_gateway/app/connectors/discord.py`
- Create: `server/sentry_gateway/app/connectors/steam.py`
- Create: `server/sentry_gateway/app/connectors/registry.py`
- Create: `server/sentry_gateway/app/briefs/service.py`
- Create: `server/sentry_gateway/app/reminders/service.py`
- Create: `server/sentry_gateway/app/notifications/router.py`
- Create: `server/sentry_gateway/app/notifications/preferences.py`
- Create: `server/sentry_gateway/app/notifications/sms.py`
- Create: `server/sentry_gateway/app/notifications/twilio_webhook.py`
- Create: `server/sentry_gateway/app/voice/base.py`
- Create: `server/sentry_gateway/app/voice/openai_audio.py`
- Create: `server/sentry_gateway/app/voice/local_whisper.py`
- Create: `server/sentry_gateway/app/voice/service.py`
- Create: `server/sentry_gateway/migrations/005_connectors_reminders_notifications.sql`
- Create: `server/sentry_gateway/tests/test_connector_permissions.py`
- Create: `server/sentry_gateway/tests/test_daily_brief.py`
- Create: `server/sentry_gateway/tests/test_notification_routing.py`
- Create: `server/sentry_gateway/tests/test_sms_commands.py`
- Create: `server/sentry_gateway/tests/test_voice_policy.py`

- [ ] Implement Gmail OAuth per personal profile on the Linux endpoint with `gmail.readonly` first; expose connection health, last sync, selected labels, and disconnect/revoke controls only to that profile's owner.
- [ ] Add `gmail.compose` only when drafting is enabled. Require biometric/passkey approval for sending and never request the full-mailbox `mail.google.com` scope.
- [ ] Configure each personal runtime Discord gateway with a distinct bot token and that person's explicit user-ID allowlist. Configure team messaging with separate bot credentials and the current team-member allowlist; keep allow-all disabled everywhere.
- [ ] Use the Steam Web API for read-only profile, friends, owned/recent games, and selected game news. Keep SteamKit2 out of the initial release.
- [ ] Store reminders in PostgreSQL and execute them through the Gateway scheduler, optionally projected to the selected runtime's scheduler. Every reminder records timezone, recurrence, destination, and last-delivery outcome.
- [ ] Generate a scheduled daily brief from only enabled sources. Return one Jarvis sentence plus expandable source-attributed detail.
- [ ] Add event subscriptions for work started, input required, approval required, resolved, failed, deployment completed/failed, reminder due, connector failure, daily brief, and security alert.
- [ ] Add work-order events for submitted, clarification requested, assigned, implementation returned, changes requested, result accepted, and colleague reply; route them to the requester, assignee, owners, and explicit watchers without exposing private-profile activity.
- [ ] Add per-event delivery modes `off`, `in_app`, `push`, `discord_dm`, `sms`, and `digest`, plus quiet hours and an urgent-event override.
- [ ] Route ordinary events to the owning profile's in-app/APNs devices, selected updates to its Discord destination, and only urgent/security events to its verified SMS number unless that user changes the preference.
- [ ] Implement optional SMS with verified per-user E.164 numbers and one or more dedicated Twilio numbers. Verify Twilio webhook signatures, resolve the sender to exactly one profile, rate-limit inbound messages, and redact outbound content.
- [ ] Map inbound SMS commands to a fixed parser: `status`, `brief`, `remind <natural language>`, `queue <question>`, and `help`. Reject arbitrary tool names, shell commands, filesystem paths, approval decisions, and connector mutations.
- [ ] Let Discord DM and the iPhone app start normal assistant conversations through the Gateway. Keep runtime tool-progress delivery disabled so remote channels receive milestone/status events and final answers instead of command narration.
- [ ] Implement voice behind a provider interface. Phase one uses a chained pipeline: device-native push-to-talk or local `faster-whisper` for reviewable transcription, the normal text/work-order policy path, and device-native TTS or OpenAI text-to-speech only for the final `task.resolved` sentence.
- [ ] Offer per-profile settings for `Voice input: off/push-to-talk`, `Resolution speech: off/native/OpenAI`, voice choice, rate, test playback, audio retention, and fallback order. Default to push-to-talk input, native device TTS, no raw-audio retention, and a global mute that wins over every profile setting.
- [ ] Keep the OpenAI API key only on the Linux gateway. Use request-based speech generation for one-sentence resolutions; do not open a Realtime session for ordinary command runs. If a later opt-in conversational mode uses Realtime, mint short-lived ephemeral client credentials, attach a privacy-preserving user safety identifier, and keep tools/approvals server-controlled.
- [ ] Support a fully local server voice option with `faster-whisper` plus a reviewed local TTS provider, but distribute model files as an optional signed voice pack rather than inflating every Windows/iPhone install. Record model license, hash, size, CPU/GPU requirements, update path, and fallback behavior.
- [ ] Mark synthetic speech clearly in onboarding/settings, never clone a person's voice without explicit eligible-provider consent, and never retain microphone audio or generated speech beyond delivery unless that profile opts in.
- [ ] Defend against prompt injection by treating connector content as quoted data and preventing it from selecting tools, workspaces, or approval modes.
- [ ] Test that read-only connectors cannot draft, send, modify, or dispatch coding work.
- [ ] Test quiet hours, de-duplication, retry/backoff, expired events, channel fallback, webhook replay, unapproved phone numbers, redaction, and the inability of SMS to approve elevated actions.
- [ ] Test muted, native, OpenAI, local-offline, provider-failure, duplicate-resolution, non-resolution, raw-audio-retention, profile-isolation, and synthetic-voice-disclosure cases. Prove command/tool progress never invokes speech.
- [ ] Run `pytest server/sentry_gateway/tests/test_connector_permissions.py server/sentry_gateway/tests/test_daily_brief.py server/sentry_gateway/tests/test_notification_routing.py server/sentry_gateway/tests/test_sms_commands.py server/sentry_gateway/tests/test_voice_policy.py -q`; expected result: all connector, notification, voice, privacy, and fallback tests pass.
- [ ] Commit `feat: add personal connectors reminders voice briefs and messaging`.

### Task 10: Turn the WinUI prototype into the OS control surface

**Files:**
- Modify: `MainPage.xaml`
- Modify: `MainPage.xaml.cs`
- Modify: `Models/AssistantSettings.cs`
- Replace: `Services/OpenAIService.cs` with `Services/SentryGatewayClient.cs`
- Modify: `Services/SettingsService.cs`
- Create: `Services/NodeStatusService.cs`
- Create: `Services/ResolutionSpeechService.cs`
- Create: `Services/WindowsStartupService.cs`
- Create: `Views/ApprovalsPage.xaml`
- Create: `Views/ConnectionsPage.xaml`
- Create: `Views/WorkstreamsPage.xaml`
- Create: `Views/TeamInboxPage.xaml`
- Create: `Views/WorkOrderPage.xaml`
- Create: `Views/SkillsPage.xaml`
- Create: `Views/PresetsPage.xaml`

- [ ] Remove direct provider-key entry from the desktop settings and replace it with server sign-in, device status, and connector status.
- [ ] Continue the Task 1 Codex-inspired custom shell rather than reverting to stock WinUI navigation. Finish its compact militaristic visual language with terminal-like activity rows, precise typography, restrained motion, strong information hierarchy, and original Sentry iconography.
- [ ] Add an always-visible compact militaristic status overlay showing Gateway, selected runtime, Windows Node, Codex, Claude, Grok Build, and queue state.
- [ ] Add Workstreams for running/queued/completed jobs, with cancel, approve, open workspace, and view evidence actions.
- [ ] Add a Team Inbox and work-order conversation showing requester, current state, assignee, selected execution location, acceptance criteria, messages, runs, artifacts, decisions, evidence, and unread/attention markers.
- [ ] Add actions for submit, ask clarification, accept assignment, assign executor, approve bounded workspace work, respond to input, request changes, accept result, cancel, close, share artifact, and propose/review a team skill. Show only actions allowed by the active user's role and the current state.
- [ ] Add a profile switcher that clearly distinguishes `Personal` and named team profiles, displays the active profile beside every prompt, and prevents silent context carryover when switching.
- [ ] Add team settings for invitations, members, roles, shared workspaces, team memory review, team connector health, and leave/revoke actions.
- [ ] Add one searchable Settings surface with sections for account/devices, profiles/teams, runtime status/version, models/budgets, Codex/Claude/Grok Build nodes, workspaces, Gmail, Discord, Steam, notifications, voice, daily brief sources, reminders, memory/retention, skills/presets, privacy, backups, audit export, and advanced diagnostics. Keep common toggles visible and place dangerous or developer-only values behind an Advanced disclosure.
- [ ] Add Skills and Presets pages that show active versions, provenance, requested capabilities, evaluation results, pending approvals, updates, disable, rollback, and per-profile assignment. Never reduce a full code diff to a single trust button.
- [ ] Keep push-to-talk input. Route text/audio through the gateway and leave transcription reviewable before send.
- [ ] Use Windows local speech as the reliable default; keep server/OpenAI voice optional with an in-settings test button, synthetic-voice disclosure, latency/cost note, and clear fallback/error state.
- [ ] Speak only the final one-sentence resolution event. Never speak tool output, command output, intermediate progress, or approval prompts.
- [ ] Start the WinUI shell and Sentry Node at user sign-in, recover after network loss, and display server/offline/local-only modes without blocking normal Codex, Claude, or Grok Build use.
- [ ] Verify keyboard, mouse, touch, light/dark/high-contrast, Windows notifications, and packaged launch.
- [ ] Commit `feat: integrate Sentry into the Windows daily workflow`.

### Task 11: Build and privately distribute the iPhone app and web gateway

**Files:**
- Create: `ios/SentryMobile/SentryMobile.xcodeproj`
- Create: `ios/SentryMobile/SentryMobile/App/SentryMobileApp.swift`
- Create: `ios/SentryMobile/SentryMobile/Auth/AuthSession.swift`
- Create: `ios/SentryMobile/SentryMobile/Networking/GatewayClient.swift`
- Create: `ios/SentryMobile/SentryMobile/Networking/EventStream.swift`
- Create: `ios/SentryMobile/SentryMobile/Views/ChatView.swift`
- Create: `ios/SentryMobile/SentryMobile/Views/WorkstreamsView.swift`
- Create: `ios/SentryMobile/SentryMobile/Views/TeamInboxView.swift`
- Create: `ios/SentryMobile/SentryMobile/Views/WorkOrderView.swift`
- Create: `ios/SentryMobile/SentryMobile/Views/ApprovalsView.swift`
- Create: `ios/SentryMobile/SentryMobile/Views/RemindersView.swift`
- Create: `ios/SentryMobile/SentryMobile/Views/SettingsView.swift`
- Create: `server/sentry_gateway/app/push/apns.py`
- Create: `web/sentry-gateway/`

- [ ] Authenticate the iPhone app through OIDC Authorization Code + PKCE and store refresh credentials in the iOS Keychain.
- [ ] Require Face ID/Touch ID before elevated work approval, Gmail send, memory deletion, connector revocation, or device enrollment.
- [ ] Provide chat, voice input, workstreams, evidence, approvals, reminders, daily briefs, connection health, and global speech mute.
- [ ] Use iOS native speech input/output as the offline-capable default, show the transcript before send, and fetch optional OpenAI-generated resolution audio through the authenticated Gateway. Never embed a provider key or a large neural voice model in the initial app bundle.
- [ ] Add personal/team profile switching and show the active profile on chat, work-order creation, approvals, memories, reminders, notifications, and settings.
- [ ] Let a colleague create, clarify, submit, follow, and review a shared work order entirely from phone, web, desktop, Discord, or voice without installing a coding harness. Preserve one conversation and timeline across all clients.
- [ ] Let Bryce triage, assign, approve, answer, and return implementations from any client. Deep-link every attention notification to the exact work-order message, run, approval, or artifact.
- [ ] Let Bryce request assistant work from the phone through text or push-to-talk, select Codex/Claude/automatic routing, choose an allowlisted workspace, and see whether the Windows execution node is online before submission.
- [ ] Show remote jobs as queued, running, waiting for input, waiting for approval, resolved, failed, or cancelled, with reconnect-safe event history.
- [ ] Register APNs and send only redacted notification text; fetch sensitive detail after authenticated app open.
- [ ] Add notification categories for `View`, `Mute thread`, and `Open approval`. `Open approval` deep-links into the app and requires Face ID/Touch ID before any decision is sent.
- [ ] Add a notification settings screen for event types, delivery channels, quiet hours, daily digest time, SMS fallback, Discord DM, sound, badges, and spoken-resolution behavior.
- [ ] Build the same essential controls into a responsive personal web gateway as a recovery/admin surface.
- [ ] Distribute the iPhone build with Apple Developer Program `TestFlight Internal Only`. Add only explicitly invited Sentry users as internal or approved external testers; do not use Enterprise distribution.
- [ ] Run `xcodebuild test` against an iPhone simulator and verify authentication, offline queue display, notification deep links, biometric approval, reconnect, and remote cancellation.
- [ ] Commit `feat: add private Sentry iPhone and web clients`.

### Task 12: Harden, deploy, restore, and prove the full workflow

**Files:**
- Create: `docs/runbooks/linux-deployment.md`
- Create: `docs/runbooks/windows-node.md`
- Create: `docs/runbooks/iphone-release.md`
- Create: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/runtime-upgrade-rollback.md`
- Create: `docs/runbooks/skill-review-and-rollback.md`
- Create: `docs/runbooks/voice-privacy.md`
- Create: `docs/runbooks/incident-response.md`
- Create: `docs/runbooks/cost-and-quota-controls.md`
- Create: `tests/e2e/test_full_workflow.py`
- Create: `deploy/linux/backup/restic.sh`
- Create: `.github/workflows/ci.yml`

- [ ] Add CI for Python, .NET, WinUI packaging validation, Swift tests, dependency review, secret scanning, and container configuration validation.
- [ ] Pin and inventory runtime, model, local voice model, Python, npm, container, and mobile dependencies. Generate an SBOM, monitor advisories, and rehearse runtime/skill rollback before enabling automatic update notifications; never auto-upgrade the production agent runtime.
- [ ] Add encrypted nightly backups for PostgreSQL, selected-runtime memory/session state, configuration, and approved context artifacts; exclude provider and harness access tokens from backup exports unless separately encrypted.
- [ ] Perform a clean restore to an isolated location and record recovery time and hash verification.
- [ ] Verify this end-to-end acceptance flow: ask from iPhone, authenticate, route through the Linux Gateway and selected runtime, queue while PC offline, reconnect the Windows Node, dispatch to a selected harness, stream silent progress, complete with evidence, receive one spoken sentence, and inspect the immutable audit trail.
- [ ] Verify a two-person collaboration flow: invite a colleague, enroll their passkey and Node, keep both personal memories/searches/connectors isolated, share one workspace with the team profile, dispatch work from each account, review attributable results together, remove the colleague, and prove their team access is immediately revoked without deleting their personal profile.
- [ ] Verify the no-harness intermediary flow: a colleague with only the phone app describes a task, the Team Assistant asks clarifying questions and creates a draft, the colleague submits it, Bryce receives and assigns it to his approved Node/Codex, Claude, or Grok Build, progress remains silent, `NeedsInput` travels back through the shared conversation, the implementation/evidence returns for review, the colleague requests one revision, a second immutable run completes, and the colleague accepts the result.
- [ ] Restart Gateway, selected runtime, PostgreSQL, and the Windows Node during different stages of that flow and prove the work order, messages, runtime projection, runs, assignments, subscriptions, skills, and artifacts reconstruct exactly once without duplicate execution or notification.
- [ ] Verify the selected runtime can search an older team session, recover the accepted constraints and prior implementation evidence, and use them in a new related work order without reading either member's private profile memory.
- [ ] Verify a collaborator can propose a team skill from an accepted implementation, that the skill remains inactive until owner review, and that approval makes the versioned skill available only to the selected team profile.
- [ ] Verify an agent-created skill fails closed before approval, passes isolated evaluation, activates only by content hash in a canary profile, survives restart, and rolls back without losing its provenance or prior evaluation record.
- [ ] Verify a personal result cannot appear in team memory until its owner performs `Share with team`, and that the shared copy retains author, source, hash, and audit provenance.
- [ ] Verify an event update reaches APNs, an urgent fallback reaches SMS, a selected status update reaches Discord DM, and duplicate retries produce only one user-visible notification.
- [ ] Verify requests sent from the iPhone app and Discord DM reach the correct selected-runtime session; verify allowed SMS commands work and prohibited SMS actions fail closed with a link to the authenticated app.
- [ ] Verify push-to-talk works with native/local transcription, OpenAI TTS can speak exactly one resolved sentence, a provider outage falls back to native speech, global mute suppresses all output, and no command/tool event reaches any speech provider.
- [ ] Re-run the runtime contract suite against the pinned production runtime and the alternate adapter. Prove that switching a disposable profile between Hermes and OpenClaw preserves Sentry-owned identity, work-order, audit, and artifact records even when runtime-private session details differ.
- [ ] Verify self-started Claude, Codex, and Grok Build tasks each produce final Sentry summaries through hooks or the adapter event stream without speaking during command runs.
- [ ] Verify Gmail remains read-only until scopes are elevated, every Discord profile rejects callers outside its own allowlist, Steam stores no password/session token, and prompt injection in connector content cannot dispatch a work order.
- [ ] Verify that revoking the iPhone or Windows device immediately prevents new requests and approvals while preserving audit history.
- [ ] Configure spend alerts and per-profile budgets for text, audio, SMS, storage, and scheduled jobs; surface current usage and degraded modes in Settings, and test that quota exhaustion fails to a safe text/native/local path.
- [ ] Commit `chore: verify and document Sentry production operations`.

## Operational undertaking and release gates

This is a small private agent platform, not merely a chat window. Shipping it means operating authentication and recovery, multi-person authorization, remote code execution, connector OAuth applications, durable memory, skill supply-chain review, mobile distribution and push notifications, voice privacy, audit and retention, backups and restore, dependency updates, incident response, and recurring model/SMS/storage costs.

1. **Gate 0 - Runtime decision:** complete the Hermes/OpenClaw contract bake-off and record the reversible ADR. Do not build client behavior against either runtime directly.
2. **Gate 1 - Single-user local alpha:** Linux Gateway, one personal profile, Windows Node, text chat, read-only actions, audit, backup, and restore. No remote workspace writes, third-party connectors, adaptive skill activation, or phone release.
3. **Gate 2 - Two-user work-order alpha:** isolated profiles, team space, intermediary work orders, offline queueing, review, and revocation. Keep agent-authored skills inactive and remote execution read-only.
4. **Gate 3 - Gated coding beta:** enable allowlisted workspace writes through signed work orders, fresh worktrees, visible approvals, evidence, cancellation, and rollback. No merge, deploy, elevated shell, or secret access by default.
5. **Gate 4 - Assistant beta:** enable reviewed presets, canary-approved adaptive skills, Gmail/Discord/Steam, reminders, daily briefs, push-to-talk, and resolution-only speech. Complete prompt-injection and connector-scope tests first.
6. **Gate 5 - Private mobile beta:** enroll the iPhone app through TestFlight, APNs, biometrics, device revocation, native speech fallback, and remote approval deep links. Run a lost-phone and compromised-token drill.
7. **Gate 6 - Always-on operation:** prove restore, upgrade/rollback, quota failure, runtime outage, connector revocation, skill rollback, audit export, and incident response. Only then describe the system as production-ready.

Ongoing ownership includes weekly review of pending skills and failed jobs, monthly restore and access reviews, connector-token and device revocation, runtime/dependency patching, Apple/TestFlight maintenance, model/audio/SMS budget review, data-retention enforcement, and an emergency kill switch that disables remote execution while preserving chat and audit access.

## Delivery order

1. Tasks 1-3 produce the custom Codex-inspired Sentry shell foundation, stable product contracts, a documented Hermes/OpenClaw runtime decision, a secured Linux control plane, and invite-only device identity.
2. Task 4 establishes isolated personal profiles and shared team spaces before any personal data is imported.
3. Task 5 establishes the durable conversational work-order exchange, shared session, Kanban projection, and intermediary handoff loop.
4. Task 6 adds proposal-only adaptive skills and versioned personal-assistant presets before the assistant can modify its reusable behavior.
5. Tasks 7-8 add approved memory/context and each available Codex/Claude/Grok Build execution node; colleagues without a harness still use the work-order exchange.
6. Tasks 9-10 make it part of the daily desktop workflow with profile-scoped Gmail, Discord, Steam, messaging, reminders, governed skills, and concise resolution speech.
7. Task 11 adds private iPhone and web access with profile switching and the full team inbox.
8. Task 12 is the release gate; no system is called complete before this evidence passes.

## First usable milestone

Stop after Task 8 for the first internal milestone. At that point the custom Sentry desktop shell is already recognizable and usable, and a colleague with no coding harness can describe and refine a team task through the selected agent runtime, submit a durable work order, exchange clarifications with Bryce, and receive an implementation returned from Bryce's approved Codex, Claude, or audited Grok Build node with attributable evidence and retained team history. The work order queues safely while the node is offline and resumes when it reconnects. Skills may be proposed and evaluated but only explicitly approved versions can activate. Gmail, Discord, Steam, the fully polished Windows surface, and iPhone distribution then layer onto this working intermediary core.

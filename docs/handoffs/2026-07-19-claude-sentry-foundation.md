# Claude Handoff: Sentry Foundation and Full UI Modernization

**Prepared:** 2026-07-19

**Repository:** `C:\Users\Bryce\Desktop\SentryAssistant`

**Implementation worktree:** `C:\Users\Bryce\Desktop\SentryAssistant\.worktrees\sentry-foundation`

**Branch:** `25vid/sentry-foundation`

**Implementation commit:** `9740264b624a40b7894cb983e5d965204a725f5a`

**Base commit on `main`:** `e8bcbf1`
**Remote:** none configured

## Goal for Claude

Continue Sentry as a private, polished Windows personal-assistant and code-watcher control surface. First finish and substantially elevate the desktop UI into an unmistakably modern, original Sentry application; then continue the authoritative implementation plan toward the Linux Gateway, replaceable Hermes runtime, multiple profiles, team work orders, trusted execution nodes, voice, connectors, and private phone access.

Do not treat the current shell as finished. It proves the layout and contracts, but the user wants the visual experience **incredibly modernized and updated throughout**. The next desktop pass is a primary deliverable, not optional polish.

## Read before changing anything

1. `CLAUDE.md`
2. `docs/plans/2026-07-19-sentry-hermes-os-integration.md` — authoritative product and security plan
3. `MainPage.xaml` and `MainPage.xaml.cs` — current shell and prototype behavior
4. `Themes/SentryTheme.xaml` and `Themes/SentryControlStyles.xaml` — current design tokens
5. `src/Sentry.Contracts/WorkOrders.cs` and `src/Sentry.Contracts/Events.cs` — product-owned boundaries
6. `tests/Sentry.Contracts.Tests/WorkOrderContractTests.cs` — executable contract requirements

Then reconcile:

```powershell
git status --short
git branch --show-current
git log --oneline --decorate -5
```

Expected starting point after this handoff commit: clean `25vid/sentry-foundation` worktree with `9740264` in its history.

## User intent that must survive implementation

- Sentry is a personal assistant and code watcher, not merely a chat window.
- The Windows program must look purpose-built and modern, taking interaction-layout cues from Codex without cloning OpenAI branding or shipping a generic WinUI shell.
- Sentry should summarize a verified answer into one concise Jarvis-like sentence.
- TTS must run only on resolution, never during commands, tool calls, progress, or approval prompts.
- Voice output must have an obvious instant mute toggle. Voice input is push-to-talk and must remain reviewable before sending.
- Settings must be easy to find and search. Common toggles stay visible; credentials and dangerous controls belong behind an Advanced boundary.
- The animated Sentry presence must visibly distinguish idle, queued, working, needs input, failed, and resolved states.
- If the armored Sentry pet is brought into this app, every visual state must retain the same vest/uniform and fixed atlas bounds. Do not reintroduce the hover fallback to an old uniform or the left-edge sprite clipping seen in the Codex pet.
- Bryce and colleagues use personal profiles plus explicitly shared team profiles. Every person's memory, sessions, connectors, devices, secrets, and private workspaces remain isolated.
- A colleague without Codex or Claude can discuss and submit a team work order. Bryce can assign it to an approved node and return the implementation and evidence through the shared conversation.
- Any human-facing action — email, Discord message, SMS, invitation, post, or other communication — requires an explicit final approval before sending.
- The Linux/SSH box is the private control plane and authentication gateway. Windows execution nodes connect outbound; no coding harness or PowerShell listener is exposed to the internet.
- Hermes is the current recommended runtime but remains replaceable behind `AgentRuntime`.
- Codex, Claude, and Grok Build are peer harness adapters. Only one may write to a worktree at a time.
- Gmail replaces Outlook. Discord and Steam are included. Phone access, APNs, optional SMS, reminders, daily briefs, memory, advice, skills, and connectors come through governed profile-scoped services.
- Skills may be proposed and evaluated adaptively, but no agent-created skill silently activates. Approval, provenance, diff, evaluation, scope, version pin, and rollback are mandatory.

## What is implemented now

### Repository foundation

- Git repository and protected baseline exist.
- Feature work is isolated in `.worktrees/sentry-foundation` on `25vid/sentry-foundation`.
- Generated builds, packages, test artifacts, recordings, local configuration, environment files, and signing material are ignored.
- The repository has no remote; do not claim anything is pushed or shared externally.

### Product contracts

- `SentryAssistant.sln` contains the WinUI app, `Sentry.Contracts`, and contract tests.
- `WorkOrder` includes requester/profile/team/session, assignment, node, harness, workspace, mode, expiry, criteria, and correlation identity.
- Validation rejects expired orders, missing identity, unknown enum values, blank prompts/correlation IDs, and missing criteria.
- Drafts may omit execution assignment. `Assigned` and `InProgress` require assignee, node, harness, and workspace.
- `HarnessEvent` and `NotificationEnvelope` provide normalized evidence and redacted notification boundaries.
- JSON uses camel-case string enums and rejects numeric or unknown enum values.

### Desktop shell foundation

- The generic `NavigationView` was replaced with a custom adaptive three-zone layout:
  - compact workstream/profile rail;
  - central conversation or workspace canvas;
  - contextual execution/evidence inspector.
- The shell has a custom title bar, neutral dark theme, light/high-contrast resources, compact typography, restrained field-green accents, modern conversation surfaces, a docked composer, harness status, and resolution policy controls.
- The inspector currently displays Codex as primary, Claude as secondary, and Grok Build as an audit-gated open-source baseline.
- Current lifecycle states are `Idle`, `Working`, `NeedsInput`, `Resolved`, and `Faulted`.
- `Resolved` receives a distinct visual flourish. Working state pulses. The one-sentence voice policy is visible.
- Existing prototype chat, OpenAI response, microphone transcription, settings, local file watching, and activity log behavior remain wired.

## Verification already completed

Run on 2026-07-19 from the feature worktree:

```text
dotnet test SentryAssistant.sln -c Debug -p:Platform=x64
Passed: 16, Failed: 0, Skipped: 0

dotnet build SentryAssistant.csproj -c Debug -p:Platform=x64
Build succeeded: 0 warnings, 0 errors

git diff --check
Passed
```

The packaged app launched successfully as `SentryAssistant.exe`; at handoff time it was running with window title `Sentry Assistant`.

The first real-window inspection confirmed the layout and accessibility tree. It caught an overly bright stock Windows accent leaking through the selected navigation and primary action. That was replaced with custom Sentry styles, rebuilt successfully, and relaunched. The user pressed Escape during the final Computer Use inspection, so the post-fix responsive/theme visual review was intentionally stopped and remains a real acceptance gate.

## Known incomplete or prototype-only behavior

- Final visual QA after the custom navigation-state fix is incomplete.
- Narrow, light, dark, high-contrast, keyboard-only, touch, and reduced-motion behavior still require real-window proof.
- The current chat cards do not yet visually distinguish the user and Sentry strongly enough.
- `Queued` is not yet implemented as a lifecycle state.
- The current animations do not yet honor the Windows reduced-motion setting.
- The `New work order` button is visual only.
- Harness and Gateway status labels are visual placeholders, not live connections.
- The desktop still calls OpenAI directly and stores a key with Windows DPAPI. The authoritative plan replaces this with authenticated Gateway sign-in and server-owned provider calls.
- No Linux Gateway, Hermes adapter, profile service, team work-order service, Sentry Node, Codex adapter, Claude adapter, or Grok Build adapter exists yet.
- No Gmail, Discord, Steam, APNs, SMS, web, or iPhone client is implemented yet.
- Do not represent the UI shell, Hermes, any harness, voice model, or connector as production-connected.

## Immediate implementation checklist

Use this as the first concise plan, then execute it without another planning ceremony:

1. Reproduce the current clean branch, rerun contract tests/build, and launch the packaged app.
2. Conduct the full UI modernization pass described below, preserving the working prototype behaviors and the product-owned contracts.
3. Add UI/view-model tests where behavior is extractable; build after each XAML slice.
4. Verify the real packaged window at wide, medium, and compact sizes in light, dark, and high contrast, using keyboard navigation and Windows reduced motion.
5. Correct every visual, clipping, focus, contrast, layout, or state problem found. Rerun the entire verification gate.
6. Commit the modernized shell as a focused commit. Only then proceed to Task 2 of the authoritative plan if the goal still includes backend work.

## Full UI modernization directive

The design should feel like a 2026 native operations assistant: calm, fast, precise, and alive. It can inherit Codex's density and conversation-first hierarchy, but it must become an original Sentry identity.

### Visual system

- Keep neutral, low-chrome surfaces and quiet separators. Avoid glowing cyberpunk panels, neon green fills, fake scanlines, and generic military stencil overload.
- Use field green only for selected, healthy, or resolved meaning. Use amber for queued/working, blue for needs-input, and red only for failure/security.
- Refine typography into a deliberate hierarchy using Segoe UI Variable for reading and Cascadia Mono only for compact metadata, IDs, paths, and harness telemetry.
- Consolidate spacing, radii, strokes, elevations, motion durations, and semantic colors into reusable theme tokens. No one-off color values in page XAML.
- Use Mica or restrained layered material where it improves depth, with opaque high-contrast fallbacks.
- Create original Sentry icons/marks or use Fluent system icons consistently. Do not copy OpenAI marks, proprietary Codex assets, or exact CSS.

### Information architecture

- Preserve the compact left workstream rail, focused center canvas, and contextual right inspector.
- Make the active personal/team profile, workspace, node, harness, privacy boundary, and work-order state continuously understandable without adding clutter.
- Turn the center into a real conversation/work-order surface: modern differentiated user and Sentry messages, attachments/evidence, decisions, criteria, run history, and a composer that grows naturally.
- The right inspector should become contextual rather than permanently repetitive: current task state, approvals, execution node, harness, evidence, costs, and resolution controls.
- Make Workstreams, Team Inbox, Approvals, Skills, Presets, Connections, and Settings feel like one coherent product as they are added, not separate utility pages.
- Replace long settings walls with searchable navigation, clear categories, inline health, quick voice/privacy toggles, and an Advanced disclosure.

### Presence and state motion

- Create a reusable `SentryPresence` component bound to semantic lifecycle state rather than scattering animations through `MainPage`.
- Add `Idle`, `Queued`, `Running`, `NeedsInput`, `ReadyForReview`, `Resolved`, `Failed`, and `Offline` visual states.
- Running may use restrained movement or tactical activity. Resolved must be unmistakably calmer and complete rather than another working animation.
- The Sentry pet may use a combat-ready resolved pose and running/cover working poses only if assets are consistent and unclipped. Never change uniform or equipment because of hover/focus state.
- Respect the system animation setting and provide a reduced-motion transition path that still communicates state through color, icon, and text.
- Keep all working/command animations silent. Only `task.resolved` may request optional one-sentence speech.

### Interaction quality

- Every visible action must work or be clearly marked unavailable with an explanation. No silent no-op controls.
- Add intentional hover, press, focus, selection, loading, empty, offline, error, success, and disabled states.
- Maintain logical tab order, visible focus, accessible names, minimum targets, screen-reader state announcements, text scaling, and high-contrast semantics.
- Keep common actions one click away: mute, push-to-talk, new work order, current workstream, approvals, and Settings search.
- Do not hide dangerous actions behind attractive unlabeled icons. Use confirmation and explicit scope for elevated or human-facing actions.

### Real-window acceptance evidence

Capture and review the packaged app at approximately:

- 1360×860 with the right inspector visible;
- 900×760 with the inspector collapsed cleanly;
- 700×700 compact rail with no clipped labels or composer actions;
- light, dark, and Windows high-contrast themes;
- normal animation and reduced-motion settings.

Verify navigation, composer, push-to-talk controls, voice mute, watcher browse/start/stop, Settings, expandable content, scrolling, focus order, window resize, minimize/restore, and packaged relaunch. A successful XAML build is not visual acceptance.

## Architecture constraints for later tasks

- The Linux Gateway owns identity, authorization, durable work orders, append-only audit, artifacts, connector grants, notification policy, and client APIs.
- The selected agent runtime is isolated and replaceable through `AgentRuntime`; no desktop or mobile client calls Hermes-specific endpoints.
- The Windows Node initiates its connection and maps remote workspace IDs to fixed allowlisted local paths. Never accept a raw remote path.
- Remote tasks use fresh Git worktrees. One writer harness at a time; optional later reviewers are read-only.
- Codex phase one uses supported local process/stdio JSON events. Claude uses a separate safe automation settings file and stream JSON. Existing Claude presence hooks must be extended, not overwritten.
- Grok Build is the third optional harness and comparison baseline. Its Apache-2.0 source, streaming JSON, and ACP support make it useful, but private repositories remain disabled until the exact pinned build passes outbound-upload, telemetry, network-destination, evidence, cancellation, and recovery tests.
- Provider and harness credentials stay on the execution node. Connector OAuth stays on the Gateway. No provider key enters a renderer, phone app, or committed file.
- Notifications contain redacted summaries and deep links, never source code, email bodies, secrets, or approval tokens.
- Team memory receives personal context only through an attributable explicit share action.

## Test-host note

The initial `.NET 10` xUnit template used `Microsoft.NET.Test.Sdk 17.14.1`. Its VSTest `testhost.exe` launched but never connected on this machine. Updating the old SDK package alone did not fix it. The project was deliberately moved to xUnit v3 and Microsoft Testing Platform through `global.json`; all 16 tests then passed. Preserve this runner unless a newer verified replacement is introduced with passing evidence.

## Definition of a successful Claude continuation

At minimum, the continuation should end with:

- a visibly and measurably modernized Sentry desktop experience rather than a stock WinUI surface;
- no pet/uniform regression or sprite clipping if presence assets are included;
- distinct, accessible lifecycle communication for working versus resolved work;
- one-click voice mute and resolution-only speech policy preserved;
- tests, builds, and real packaged-window QA recorded separately;
- a clean focused commit on `25vid/sentry-foundation`;
- an updated handoff or plan showing exactly what is verified and what remains.

Do not claim the Linux control plane, Hermes, harness adapters, connectors, phone app, or end-to-end workflow is shipped until the corresponding implementation-plan gates have passed.

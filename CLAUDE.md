# Sentry Assistant Claude Guide

This repository is the private Windows control surface for Sentry, a personal and team assistant platform. Start every continuation by reading:

1. `docs/handoffs/2026-07-19-claude-sentry-foundation.md`
2. `docs/plans/2026-07-19-sentry-hermes-os-integration.md`

## Current checkout

- Work only in `C:\Users\Bryce\Desktop\SentryAssistant\.worktrees\sentry-foundation`.
- Expected branch: `25vid/sentry-foundation`.
- Reconcile `git status`, `git log`, this handoff, and the implementation plan before editing.
- The root checkout at `C:\Users\Bryce\Desktop\SentryAssistant` holds `main`; do not implement directly there.

## Working rules

- Give one concise implementation checklist, then execute it. Do not create a separate brainstorm/spec/plan ceremony.
- Preserve the Sentry-owned contracts. Provider runtimes and coding harnesses are replaceable adapters, not product authorities.
- Never commit provider keys, connector credentials, audio, recordings, generated packages, or local settings.
- Never send an email, message, post, invitation, or other human-facing action without the initiating user's explicit approval.
- Keep Codex, Claude, and Grok Build credentials on the execution node. Never route credentials through the Linux Gateway.
- Never run multiple writer harnesses in one worktree. One may write; others may review read-only after it finishes.
- Treat the current direct OpenAI desktop path and status labels as prototype behavior, not proof of the future Gateway or live harness connections.
- Do not call work complete from a build alone. Verify the packaged Windows window and state the exact remaining runtime gate.

## UI direction

The current three-zone shell is a structural foundation, not the final design. The user explicitly wants the entire UI modernized aggressively. Produce an original, high-quality Sentry experience inspired by Codex's compact interaction grammar without copying OpenAI branding, text, icons, or assets. Do not revert to a generic `NavigationView`, stock admin dashboard, or wall of WinUI cards.

Preserve accessible keyboard behavior, light/dark/high-contrast themes, reduced motion, and clear distinctions between queued, working, needs-input, failed, and resolved states. Voice output is opt-in, immediately mutable, and speaks only a one-sentence verified resolution. Command and tool output must remain silent.

## Local commands

The `dotnet` first on `PATH` is an x86 installation without the required SDK. Use the explicit x64 executable:

```powershell
& 'C:\Program Files\dotnet\dotnet.exe' test SentryAssistant.sln -c Debug -p:Platform=x64
& 'C:\Program Files\dotnet\dotnet.exe' build SentryAssistant.csproj -c Debug -p:Platform=x64
& 'C:\Program Files\dotnet\dotnet.exe' run --project SentryAssistant.csproj -c Debug -p:Platform=x64 --no-build
```

The test project uses xUnit v3 on Microsoft Testing Platform through `global.json`. Do not revert it to the older VSTest path that hung on this machine.

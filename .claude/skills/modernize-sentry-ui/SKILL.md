---
name: modernize-sentry-ui
description: Modernize and verify the native Sentry Windows interface. Use when changing WinUI shell, navigation, conversation, settings, presence, lifecycle motion, accessibility, responsiveness, theming, or packaged-window behavior.
---

# Modernize Sentry UI

1. Read the UI directive and known gaps in `docs/handoffs/2026-07-19-claude-sentry-foundation.md`.
2. Preserve working behavior and product contracts while replacing generic or prototype presentation.
3. Keep the three-zone conversation-first structure, original Sentry identity, semantic theme tokens, and resolution-only voice policy.
4. Model state explicitly: idle, queued, running, needs input, ready for review, resolved, failed, and offline.
5. Honor Windows reduced motion and high contrast. Keep keyboard order, focus, accessible names, text scaling, and compact resize behavior functional.
6. Build after each XAML slice. Use `/run` or `/verify` against the packaged app at wide, medium, and compact widths in light, dark, and high contrast.
7. Do not claim visual completion from compilation or screenshots of only one state.

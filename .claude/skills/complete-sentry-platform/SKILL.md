---
name: complete-sentry-platform
description: Complete the authorized Sentry platform goal from modern Windows UI through live Hermes authentication, trusted execution, private iOS installation, and end-to-end release proof. Use only when the user explicitly invokes this full-platform workflow.
disable-model-invocation: true
---

# Complete Sentry Platform

1. Read `CLAUDE.md`, the complete Claude handoff, the authoritative implementation plan, and every sibling Sentry skill before editing.
2. Reconcile Git and live machine state. Keep the goal active across milestones; make focused verified commits rather than one final unreviewable change.
3. Finish and prove the Windows UI milestone using `modernize-sentry-ui` instructions.
4. Implement Tasks 2-8 and deploy the live control plane using `deploy-sentry-hermes` and `secure-sentry-auth` instructions. Include profiles, durable work orders, audit, and at least one trusted execution path.
5. Implement the required connector, notification, reminder, and voice services, preserving explicit approval for every human-facing action and resolution-only speech.
6. Build, sign, privately install, and prove the phone client using `ship-sentry-ios` instructions. Simulator-only evidence is insufficient.
7. Apply `verify-sentry-release` after every milestone and for the final phone-to-Gateway-to-runtime/Node-to-resolution workflow.
8. Continue safe local work when an external gate appears. Stop only when the remaining step truly requires a user credential, DNS change, Apple signing/account action, physical-device interaction, destructive operation, or human-facing approval; report that one gate precisely.

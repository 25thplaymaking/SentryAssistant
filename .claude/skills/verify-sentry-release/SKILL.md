---
name: verify-sentry-release
description: Verify Sentry milestone or release claims across Windows, Linux Gateway, Hermes, execution Nodes, authentication, notifications, and iOS. Use before completion, release, deployment, or handoff claims.
---

# Verify Sentry Release

1. Read Task 12 in `docs/plans/2026-07-19-sentry-hermes-os-integration.md` and select only the gates relevant to the claimed milestone.
2. Reconcile the exact commit, configuration, deployed versions, live processes, databases, devices, and clients.
3. Run focused tests, full tests, production builds, package/signing checks, and secret/dependency scans.
4. Exercise the live boundary: authenticated request, authorization, persistence, audit, runtime/Node dispatch, silent progress, evidence, notification, one-sentence resolution, and client rendering.
5. Test offline/reconnect, cancellation, restart, duplicate delivery, token refresh, revocation, denial, backup, and isolated restore where applicable.
6. Separate test/build evidence, simulator evidence, physical-device evidence, and production evidence. Never infer one from another.
7. Leave the verified app/services running when safe, record exact failures and user-owned gates, and commit only after the claimed scope passes.

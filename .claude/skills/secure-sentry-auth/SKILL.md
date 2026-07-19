---
name: secure-sentry-auth
description: Implement or review Sentry authentication, authorization, device enrollment, profiles, teams, tokens, secrets, approvals, and audit across Gateway, Windows, web, and iOS code.
---

# Secure Sentry Authentication

1. Read the global constraints and Tasks 3-5 in `docs/plans/2026-07-19-sentry-hermes-os-integration.md`.
2. Treat Gateway identity and authorization as authoritative. Never delegate account, role, device, profile, team, or approval decisions to Hermes or a client.
3. Use OIDC Authorization Code with PKCE for public clients, short-lived access tokens, rotated refresh tokens, issuer/audience validation, replay defense, and revocable device sessions.
4. Store server secrets only in protected server storage. Use Windows protected storage and iOS Keychain/Secure Enclave-backed facilities only for revocable client credentials.
5. Isolate every person's profile, memory, sessions, connectors, devices, skills, and secrets. Share to teams only through an explicit attributable action.
6. Require fresh biometric or equivalent confirmation for elevated actions. Require explicit human approval before any outward communication.
7. Add negative tests for cross-profile access, confused deputy paths, replay, expired tokens, revoked devices, malicious deep links, connector prompt injection, and audit omission.
8. Do not call authentication complete until Windows and physical-iPhone sign-in, refresh, logout, revocation, recovery, and audit are verified against the live Gateway.

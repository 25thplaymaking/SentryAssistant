---
name: deploy-sentry-hermes
description: Deploy and operationally verify the private Sentry Linux Gateway and isolated Hermes runtime. Use only when the user explicitly asks to deploy, configure, upgrade, restore, or finish live Hermes infrastructure.
disable-model-invocation: true
---

# Deploy Sentry Hermes

1. Read Tasks 2-8 and 12 in `docs/plans/2026-07-19-sentry-hermes-os-integration.md` plus the post-UI outcome in the Claude handoff.
2. Inventory the actual Linux host, DNS/VPN route, ports, users, container runtime, firewall, storage, backups, and existing services before mutation.
3. Keep Sentry identity, work orders, audit, artifacts, and client APIs in the Gateway. Run pinned Hermes non-root in a reviewed whole-process boundary with its admin UI loopback-only.
4. Deploy PostgreSQL, Authentik/OIDC, Caddy/TLS, Gateway, runtime adapter, health/readiness, logs, budgets, encrypted backup, and restore procedures with least-privilege credentials.
5. Never expose Hermes, a coding harness, PowerShell, or a raw execution listener. Windows Nodes connect outbound with short-lived scoped credentials.
6. Require explicit approval before DNS, credential rotation, production migration, human-facing communication, or destructive recovery actions.
7. Prove restart, token rotation, device revocation, offline queueing, audit reconstruction, backup, and isolated restore. Record live evidence separately from builds.

# Claude Skills for Sentry

## Decision

Use Claude's official bundled runtime skills plus small project-owned Sentry workflows. Do not install a broad third-party “everything” bundle into a private authentication and remote-execution project.

Claude Code discovers project skills under `.claude/skills/`, loads only the matching skill body when needed, and can restrict side-effecting workflows to explicit user invocation. Skills are procedural context, not a security sandbox; permissions, hooks, process isolation, network policy, and human approvals still enforce safety. See the [official Claude Code skills guide](https://code.claude.com/docs/en/slash-commands) and [feature selection guide](https://code.claude.com/docs/en/features-overview).

## Use immediately

- `/run-skill-generator` once the Windows and iOS launch recipes stabilize. Commit the generated project-specific launch recipe.
- `/run` for real application launches.
- `/verify` for build-and-runtime evidence rather than test-only claims.
- `/code-review` before each milestone commit.
- `/debug` for reproducible build, simulator, authentication, APNs, or runtime failures.

These bundled skills are documented by Claude Code and do not require a third-party marketplace.

## Project-owned skills added here

| Skill | Role | Invocation |
| --- | --- | --- |
| `complete-sentry-platform` | Coordinate the complete UI → Hermes/auth → iOS → verification goal | Explicit user invocation only |
| `modernize-sentry-ui` | Native Windows redesign and real-window QA | Automatic or `/modernize-sentry-ui` |
| `deploy-sentry-hermes` | Live Linux Gateway, Hermes, database, ingress, and operations | Explicit user invocation only |
| `secure-sentry-auth` | OIDC, passkeys, device enrollment, token, profile, and audit boundaries | Automatic during auth work |
| `ship-sentry-ios` | Native SwiftUI client, signing, APNs, physical-device installation | Explicit user invocation only |
| `verify-sentry-release` | Cross-boundary evidence and release gate | Automatic or `/verify-sentry-release` |

The skills point back to the authoritative implementation plan instead of duplicating it. Update the plan first when product contracts change.

Use `/complete-sentry-platform` when one Claude goal should carry the project across all milestones. Use the narrower skills for later repairs or individually scoped follow-up.

## Reviewed iOS candidates

### Recommended focused candidate

[SwiftUI Pro by Paul Hudson](https://github.com/twostraws/SwiftUI-Agent-Skill) is a focused MIT-licensed skill covering modern SwiftUI, state, layout, performance, animation, and accessibility. Review and pin its exact source before installation. Prefer it when Claude needs a single expert SwiftUI reference rather than a large bundle.

### Recommended selective framework candidates

[Swift iOS Skills](https://github.com/dpearson2699/swift-ios-skills) contains a large modern Apple-framework catalog. Do not install all skills. After source and license review, consider only:

- `swiftui-patterns`
- `swiftui-navigation`
- `swiftui-performance`
- `swiftui-animation`
- `swift-testing`
- `authentication`
- `cryptokit`
- `device-integrity`
- `ios-networking`
- `push-notifications`
- `speech-recognition`
- `swift-security`
- `ios-accessibility`
- `app-store-review`

The repository uses the PolyForm Perimeter license. It allows use in a closed-source workflow but should still be reviewed against the intended distribution and pinned by commit before adoption.

## Backend and authentication finding

No reviewed general-purpose third-party FastAPI/Docker/OIDC skill was strong enough to grant automatic authority over this private control plane. Authentication is too product-specific for generic instructions. Use the Sentry-owned `deploy-sentry-hermes` and `secure-sentry-auth` skills, and ground implementation in current upstream Hermes, Authentik, FastAPI, PostgreSQL, Caddy, Apple, and OAuth/OIDC documentation at the time of execution.

The official Anthropic MCP integration skill is useful as a reference for OAuth, environment-based tokens, HTTPS, scoped tools, and failure handling, but Sentry's Gateway is not an MCP-auth shortcut. Sentry still owns identity, device enrollment, authorization, approvals, and audit. See Anthropic's [MCP integration skill](https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/skills/mcp-integration/SKILL.md).

## Installation policy

Before adding any third-party Claude skill or plugin:

1. Inspect the complete `SKILL.md`, scripts, hooks, MCP configuration, and transitive installer behavior.
2. Confirm license, recent maintenance, immutable commit, and compatibility with the installed Claude/Xcode versions.
3. Reject skills that request broad shell/network access, hidden telemetry, credential collection, or automatic deployment.
4. Vendor only the selected skill folder or pin the marketplace version. Never track an unpinned `main` branch for release work.
5. Evaluate in a disposable repository/profile without Sentry secrets or personal data.
6. Record provenance, hash, approved capabilities, and rollback in Sentry's skill inventory.

Do not confuse a Claude skill with a Sentry runtime skill. Claude project skills help build Sentry; runtime skills are governed product data and must pass the separate proposal/evaluation/approval lifecycle in the implementation plan.

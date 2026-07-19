---
name: ship-sentry-ios
description: Build, sign, privately distribute, and verify the native Sentry iPhone app. Use only when the user explicitly asks to implement or ship the iOS client, APNs, device enrollment, or physical-device release.
disable-model-invocation: true
---

# Ship Sentry iOS

1. Read Tasks 9, 11, and 12 plus the post-UI deployment outcome in the Claude handoff.
2. Confirm current Xcode, Swift, deployment target, Apple team, bundle ID, entitlements, APNs environment, and private distribution choice from live tooling. Do not fabricate signing success.
3. Build a native SwiftUI client around Gateway contracts: OIDC/PKCE sign-in, profile switcher, work orders, conversations, approvals, node/harness status, notifications, reminders, push-to-talk, and one-sentence resolved speech.
4. Keep provider/runtime keys off the phone. Store only revocable device credentials in Keychain/Secure Enclave-backed storage and require biometrics for elevated approvals.
5. Keep push payloads redacted and deep-link into authenticated detail. Handle token rotation, notification deduplication, offline queues, reconnect, VPN transitions, and device revocation.
6. Verify unit, integration, UI, accessibility, performance, and memory behavior in Simulator; then build, sign, install, and verify on Bryce's physical iPhone through the selected private path.
7. Stop only at an exact user-owned Apple credential, certificate, provisioning, device-trust, or App Store Connect gate. Preserve the build and report the precise next action.

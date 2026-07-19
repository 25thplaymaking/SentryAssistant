# iPhone Release Runbook

**Status:** blocked on hardware. Not started.

This is not a substitute for the iOS app. It exists because the phase cannot be
executed from the machine the rest of Sentry was built on, and the next session —
on a Mac — should spend its time building rather than rediscovering context.

## Why nothing was built

Building, signing, and installing a native iOS app requires **Xcode, which runs
only on macOS**. `xcodebuild` and `codesign` have no Windows equivalent. The
development machine is Windows 11 Home with no Swift toolchain and no Mac
reachable on the network.

This is a hardware gate, not a missing credential. No secret unblocks it.

Deliberately **no SwiftUI source was written**. Uncompiled Swift would satisfy a
file listing while proving nothing: not that it builds, not that it signs, not
that it runs. The project standard is that a green build is not acceptance, and
unbuilt source is further from acceptance than a build.

## What already exists server-side

The phone's server half is built, deployed, and tested. A Mac session starts
against a working backend, not a stub.

| Capability | State |
|---|---|
| Authenticated Gateway | Live, `/health/ready` 200 |
| Device enrolment + rotation + revocation | 20/20 live checks |
| Durable work orders | 13/13 live checks |
| Teams, roles, isolation | 28/28 live checks |
| Push payload construction and redaction | `app/notifications/envelope.py`, 29 tests |
| Append-only audit | Enforced by trigger, survives restore |

The Gateway binds `127.0.0.1:8090` on `grain.silo` and is not publicly exposed.

## Prerequisites the user must supply

1. **A Mac** running a current macOS with Xcode installed.
2. **Apple Developer Program membership** (individual is sufficient for
   TestFlight internal testing).
3. **The physical iPhone**, and its Apple ID added as an internal tester.
4. **Public reachability for the Gateway.** It is loopback-only today. The box
   already runs `cloudflared`, so a Cloudflare Tunnel is the least invasive
   option and needs no inbound firewall change or new DNS record beyond the
   tunnel hostname. This is a DNS/ingress gate and needs an explicit decision.

## Order of work on the Mac

1. **Reachability first.** Expose the Gateway over TLS and confirm
   `GET /health/live` from the phone's network. Everything else depends on it.
2. **Auth.** OIDC Authorization Code + PKCE via `ASWebAuthenticationSession`.
   Store refresh credentials in the Keychain with
   `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`. The device-enrolment flow
   already exists at `POST /api/auth/enroll/complete`.
3. **Transport.** `GatewayClient` against the existing routes. Nothing new is
   needed server-side for chat, work orders, or approvals.
4. **APNs.** Register for remote notifications and send the device token to the
   Gateway. Payloads are already redacted by `envelope.apns_payload()`; the app
   fetches detail after authenticated open, never from the payload.
5. **Biometrics.** `LAContext` gate before any elevated approval, Gmail send,
   memory deletion, connector revocation, or device enrolment.
6. **Signing and install.** Automatic signing with the team ID, archive, then
   distribute via **TestFlight internal testing**. Do not use Enterprise
   distribution.

## What "done" means

Not "it launched in the simulator". The phase is complete when, on the physical
iPhone:

- Sign-in completes through the browser and survives an app relaunch.
- A token refresh succeeds after the access token expires.
- A push arrives, its payload contains no source, secret, or approval token, and
  the deep link opens the correct authenticated detail.
- An approval requires Face ID before the decision is sent.
- Revoking the device from another client immediately stops it working.
- The app behaves correctly through network loss and a VPN transition.

Verify each against the real device. A simulator run and an unsigned archive both
fail this bar.

## Carry these forward

- `127.0.0.1` is broken machine-wide on the Windows box; `::1` and `localhost`
  work. Irrelevant on a Mac, but it will matter if the Windows node is involved
  in an end-to-end test.
- Hermes has no inference provider configured, so the assistant cannot answer
  yet. Work orders, audit, and dispatch all function without it, so phone
  development is not blocked — but a chat reply will fail until a key is set.
- Never send a human-facing message from an automated path. Invitations are
  recorded, not delivered, for exactly this reason.

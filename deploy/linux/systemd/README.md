# Auto-update on start

`sentry-update.sh` fast-forwards the two checkouts this deployment builds from
(`/srv/sentry/repo` and `/srv/sentry/webui`), rebuilds only if something moved,
and brings the stack up. `sentry-update.service` runs it at boot.

## Install

```bash
sudo install -m 0644 systemd/sentry-update.service /etc/systemd/system/
sudo install -m 0644 systemd/sentry-update.conf.example /etc/sentry-update.conf
sudo systemctl daemon-reload && sudo systemctl enable --now sentry-update.service
```

## The rule it is built around

**Updating is best-effort; starting is not.** Every failure path still leaves
the stack running on the last-known-good checkout and exits 0. A GitHub outage,
an expired key, or a diverged branch must never be why the assistant is down.

The compose services already carry `restart: unless-stopped`, so Docker restores
the stack at boot without waiting on the network. This unit then rolls it
forward. Availability never depends on a successful fetch.

## What it refuses to do

| Refusal | Why |
|---|---|
| Merge or rebase | Only `--ff-only`. Divergence is a human's problem, not a boot-time one. |
| Touch a dirty checkout | This box edits live configs in place; discarding them would destroy work that exists nowhere else. |
| Pass `--remove-orphans` | `llama` is profile-gated and out of scope; compose would see it as an orphan and stop 24 GB of loaded model. |
| Start the local-model profile | Starting `llama` is always an explicit act. See `docs/local-inference.md`. |

## Turning it off

This is the rollback if an update ever ships something bad:

```bash
sudo sed -i 's/^SENTRY_AUTO_UPDATE=.*/SENTRY_AUTO_UPDATE=0/' /etc/sentry-update.conf
```

With it off the unit still runs and still brings the stack up — it just never
fetches.

## Watching it

```bash
journalctl -u sentry-update.service -n 50
```

Every decision is logged with its reason: `already current`, `updated <a> -> <b>`,
`LOCAL MODIFICATIONS present`, `not a fast-forward`, `fetch failed`.

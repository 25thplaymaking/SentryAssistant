#!/usr/bin/env bash
#
# Authorise ONE teammate's SSH key to forward the Sentry WebUI port, and nothing
# else.
#
# The WebUI binds loopback on this box, so a remote user needs an SSH forward to
# reach it. The obvious shortcut -- handing out the admin account -- would give
# every teammate sudo and access to unrelated production services on the same
# host. Instead each teammate gets their own key entry restricted so that it:
#
#   * cannot open a shell or run any command  (restrict, no-pty)
#   * cannot forward anything except 127.0.0.1:8787  (permitopen)
#   * cannot use agent/X11 forwarding or tunnels     (restrict)
#
# `restrict` disables every feature and then `port-forwarding` re-enables only
# forwarding, so new OpenSSH features are denied by default rather than silently
# granted on upgrade.
#
# No sudo required: this edits the invoking account's own authorized_keys.
#
#   ./authorize-tunnel-key.sh --slug alice --pubkey "ssh-ed25519 AAAA... alice"
#   ./authorize-tunnel-key.sh --slug alice --revoke
#
set -euo pipefail

SLUG=""
PUBKEY=""
REVOKE=0
WEBUI_PORT=8787

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --slug)   SLUG="${2:-}"; shift 2 ;;
        --pubkey) PUBKEY="${2:-}"; shift 2 ;;
        --revoke) REVOKE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

[ -n "$SLUG" ] || die "--slug is required"
printf '%s' "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]{0,30}$' || die "invalid --slug"

AUTH="${HOME}/.ssh/authorized_keys"
MARKER="sentry-tunnel:${SLUG}"

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
touch "$AUTH"
chmod 600 "$AUTH"

# Always drop any existing entry for this slug first: that makes the script
# idempotent and makes --revoke a plain no-key case of the same path.
if grep -q "$MARKER" "$AUTH" 2>/dev/null; then
    cp "$AUTH" "${AUTH}.bak"
    grep -v "$MARKER" "${AUTH}.bak" > "$AUTH"
    chmod 600 "$AUTH"
    echo "removed the existing entry for '${SLUG}'"
fi

if [ "$REVOKE" = "1" ]; then
    echo "revoked: '${SLUG}' can no longer open a forward."
    echo "Their Sentry session cookie is unaffected — revoke the device in the"
    echo "Gateway too if you are removing their access entirely."
    exit 0
fi

[ -n "$PUBKEY" ] || die "--pubkey is required unless --revoke"
# Accept only a public key line. A private key pasted here would be written
# world-readable-ish into authorized_keys and silently never work.
printf '%s' "$PUBKEY" | grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+' \
    || die "that does not look like an SSH PUBLIC key line (expected 'ssh-ed25519 AAAA...')"
printf '%s' "$PUBKEY" | grep -q "PRIVATE KEY" && die "that is a PRIVATE key — send only the .pub"

printf '%s %s\n' \
    "restrict,port-forwarding,permitopen=\"127.0.0.1:${WEBUI_PORT}\"" \
    "$(printf '%s' "$PUBKEY" | tr -d '\n') ${MARKER}" >> "$AUTH"
chmod 600 "$AUTH"

echo "authorised '${SLUG}' for 127.0.0.1:${WEBUI_PORT} forwarding only."
echo
echo "Verify from their machine:"
echo "  ssh -N -L '[::1]:8787:127.0.0.1:8787' $(id -un)@$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "and confirm a shell is refused:"
echo "  ssh $(id -un)@<host> whoami     # must NOT return a username"

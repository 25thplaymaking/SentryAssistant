#!/usr/bin/env bash
# Create Sentry's own Cloudflare Tunnel and point a hostname at it.
#
# Headless on purpose: `cloudflared tunnel login` opens a browser, which this
# deployment has no business doing on the operator's desktop. Everything here
# goes through the Cloudflare API with a scoped token.
#
# Token scopes required (create at
# https://dash.cloudflare.com/profile/api-tokens):
#   Account -> Cloudflare Tunnel : Edit
#   Zone    -> DNS               : Edit   (on the zone below)
#   Zone    -> Zone              : Read   (to resolve the zone id)
#
# Idempotent: re-running with the same tunnel name reuses the existing tunnel
# rather than creating a duplicate, and upserts the DNS record.
#
# Usage:
#   CF_API_TOKEN=... ./create-tunnel.sh [hostname] [tunnel-name]
#
# Writes:
#   cloudflared/creds.json   (0600, gitignored — the tunnel's private secret)
#   SENTRY_TUNNEL_ID=...     appended/updated in .env
set -euo pipefail

HOSTNAME_FQDN="${1:-sentry.frontir.solutions}"
TUNNEL_NAME="${2:-sentry}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CREDS="$HERE/cloudflared/creds.json"
ENV_FILE="$HERE/.env"

: "${CF_API_TOKEN:?CF_API_TOKEN is required (see the scopes in this script's header)}"

api() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -X "$method"
    -H "Authorization: Bearer $CF_API_TOKEN"
    -H "Content-Type: application/json")
  [[ -n "$body" ]] && args+=(--data "$body")
  curl "${args[@]}" "https://api.cloudflare.com/client/v4${path}"
}

# Fail loudly on a Cloudflare-level error instead of writing half a config.
check() {
  local payload="$1" what="$2"
  if [[ "$(jq -r '.success' <<<"$payload")" != "true" ]]; then
    echo "ERROR: $what failed:" >&2
    jq -r '.errors[]? | "  [\(.code)] \(.message)"' <<<"$payload" >&2
    exit 1
  fi
}

# --- Resolve the zone (the registrable domain of the hostname) --------------
ZONE_NAME="$(awk -F. '{print $(NF-1)"."$NF}' <<<"$HOSTNAME_FQDN")"
echo "Zone:     $ZONE_NAME"
echo "Hostname: $HOSTNAME_FQDN"

zones="$(api GET "/zones?name=$ZONE_NAME")"
check "$zones" "zone lookup for $ZONE_NAME"
ZONE_ID="$(jq -r '.result[0].id // empty' <<<"$zones")"
ACCOUNT_ID="$(jq -r '.result[0].account.id // empty' <<<"$zones")"
if [[ -z "$ZONE_ID" || -z "$ACCOUNT_ID" ]]; then
  echo "ERROR: zone $ZONE_NAME not visible to this token. Check the Zone:Read scope." >&2
  exit 1
fi

# --- Create (or reuse) the tunnel ------------------------------------------
existing="$(api GET "/accounts/$ACCOUNT_ID/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false")"
check "$existing" "tunnel lookup"
TUNNEL_ID="$(jq -r '.result[0].id // empty' <<<"$existing")"

if [[ -n "$TUNNEL_ID" ]]; then
  echo "Tunnel:   reusing existing '$TUNNEL_NAME' ($TUNNEL_ID)"
  if [[ ! -s "$CREDS" ]]; then
    # The secret is only ever returned at creation time. Without it the sidecar
    # cannot authenticate, and there is no API to read it back.
    echo "ERROR: tunnel '$TUNNEL_NAME' already exists but $CREDS is missing." >&2
    echo "       Its secret cannot be recovered — delete the tunnel in the" >&2
    echo "       dashboard and re-run, or restore the credentials file." >&2
    exit 1
  fi
else
  TUNNEL_SECRET="$(openssl rand -base64 32)"
  created="$(api POST "/accounts/$ACCOUNT_ID/cfd_tunnel" \
    "$(jq -nc --arg n "$TUNNEL_NAME" --arg s "$TUNNEL_SECRET" \
        '{name:$n, tunnel_secret:$s, config_src:"local"}')")"
  check "$created" "tunnel creation"
  TUNNEL_ID="$(jq -r '.result.id' <<<"$created")"
  echo "Tunnel:   created '$TUNNEL_NAME' ($TUNNEL_ID)"

  mkdir -p "$(dirname "$CREDS")"
  # Written with a restrictive umask FIRST — never world-readable, not even for
  # the instant between create and chmod.
  (umask 077; jq -nc \
      --arg a "$ACCOUNT_ID" --arg t "$TUNNEL_ID" --arg s "$TUNNEL_SECRET" \
      '{AccountTag:$a, TunnelID:$t, TunnelSecret:$s}' > "$CREDS")
  chmod 600 "$CREDS"
  echo "Creds:    $CREDS (0600)"
fi

# --- Point the hostname at the tunnel --------------------------------------
CNAME_TARGET="$TUNNEL_ID.cfargotunnel.com"
records="$(api GET "/zones/$ZONE_ID/dns_records?name=$HOSTNAME_FQDN")"
check "$records" "DNS lookup"
RECORD_ID="$(jq -r '.result[0].id // empty' <<<"$records")"
payload="$(jq -nc --arg n "$HOSTNAME_FQDN" --arg c "$CNAME_TARGET" \
  '{type:"CNAME", name:$n, content:$c, proxied:true, comment:"Sentry WebUI via its own cloudflared sidecar"}')"

if [[ -n "$RECORD_ID" ]]; then
  res="$(api PUT "/zones/$ZONE_ID/dns_records/$RECORD_ID" "$payload")"
  check "$res" "DNS update"
  echo "DNS:      updated $HOSTNAME_FQDN -> $CNAME_TARGET (proxied)"
else
  res="$(api POST "/zones/$ZONE_ID/dns_records" "$payload")"
  check "$res" "DNS create"
  echo "DNS:      created $HOSTNAME_FQDN -> $CNAME_TARGET (proxied)"
fi

# --- Record the tunnel id for compose --------------------------------------
touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
if grep -q '^SENTRY_TUNNEL_ID=' "$ENV_FILE"; then
  sed -i "s|^SENTRY_TUNNEL_ID=.*|SENTRY_TUNNEL_ID=$TUNNEL_ID|" "$ENV_FILE"
else
  printf '\n# Sentry public reach — see cloudflared/config.yml\nSENTRY_TUNNEL_ID=%s\n' "$TUNNEL_ID" >> "$ENV_FILE"
fi
echo "Env:      SENTRY_TUNNEL_ID set in $ENV_FILE"

cat <<EOF

Next:
  1. Confirm cloudflared/config.yml lists '$HOSTNAME_FQDN' as its ingress hostname.
  2. docker compose up -d cloudflared
  3. curl -sSI https://$HOSTNAME_FQDN/ | head -1
EOF

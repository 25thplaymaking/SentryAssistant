#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_DIR="$ROOT/data/gateway"
TOKEN_FILE="$TOKEN_DIR/sentry-recovery.token"

umask 077
mkdir -p "$TOKEN_DIR"
if [[ ! -f "$TOKEN_FILE" ]]; then
  openssl rand -hex 48 > "$TOKEN_FILE"
  echo "Created $TOKEN_FILE"
else
  echo "Kept existing $TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE"

echo "Applying the idempotent password-recovery schema..."
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$ROOT/../../server/sentry_gateway/migrations/010_password_recovery.sql"

echo "Rebuilding and restarting the Sentry Gateway..."
docker compose up -d --build gateway

echo "Configure Server Control Portal:SentryRecoveryTokenFile as:"
echo "  $TOKEN_FILE"
echo "Then restart the Sentry Gateway and Server Control after both deployments include the recovery bridge."

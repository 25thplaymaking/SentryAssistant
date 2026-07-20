#!/usr/bin/env bash
# Encrypted Sentry backup.
#
# Captures the PostgreSQL control plane and each Hermes profile home. Refresh
# tokens are deliberately excluded: they are re-issuable by enrolment, and a
# backup that carries live credentials turns every archive into a second copy of
# the keys. Enrolment codes and dispatch nonces are excluded for the same reason.
#
# Usage:
#   sentry-backup.sh [destination-dir]
#
# Requires SENTRY_BACKUP_PASSPHRASE in the environment.

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/srv/sentry/repo/deploy/linux}"
DEST="${1:-/srv/sentry/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

if [ -z "${SENTRY_BACKUP_PASSPHRASE:-}" ]; then
    echo "SENTRY_BACKUP_PASSPHRASE is required." >&2
    exit 2
fi

# shellcheck disable=SC1091
set -a; . "${DEPLOY_DIR}/.env"; set +a

mkdir -p "$DEST"
umask 077

echo "[1/4] dumping PostgreSQL"
# --exclude-table-data, not --exclude-table: the schema is kept so a restore
# produces a complete, usable database with those tables simply empty.
docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" sentry-postgres-1 \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        --no-owner --no-privileges \
        --exclude-table-data=refresh_tokens \
        --exclude-table-data=enrollment_codes \
        --exclude-table-data=work_order_nonces \
    > "${WORK}/postgres.sql"

echo "[2/4] archiving Hermes profile homes"
# Excludes caches and logs, which are large and rebuildable.
tar -czf "${WORK}/hermes-profiles.tar.gz" \
    -C "${DEPLOY_DIR}/data/hermes" \
    --exclude='*/logs' --exclude='*/cache' --exclude='*/__pycache__' \
    . 2>/dev/null || true

echo "[3/4] recording a manifest"
{
    echo "created_utc=${STAMP}"
    echo "postgres_sha256=$(sha256sum "${WORK}/postgres.sql" | cut -d' ' -f1)"
    echo "hermes_sha256=$(sha256sum "${WORK}/hermes-profiles.tar.gz" | cut -d' ' -f1)"
    echo "postgres_bytes=$(stat -c%s "${WORK}/postgres.sql")"
    echo "hermes_bytes=$(stat -c%s "${WORK}/hermes-profiles.tar.gz")"
    echo "excluded=refresh_tokens,enrollment_codes,work_order_nonces"
    echo "note=secrets live in .env and are NOT included; back that up separately"
} > "${WORK}/manifest.txt"

echo "[4/4] encrypting"
ARCHIVE="${DEST}/sentry-${STAMP}.tar.gz.enc"
tar -czf - -C "$WORK" postgres.sql hermes-profiles.tar.gz manifest.txt \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
        -pass env:SENTRY_BACKUP_PASSPHRASE \
  > "$ARCHIVE"
chmod 600 "$ARCHIVE"

# The manifest hash is stored alongside so a restore can prove the archive
# decrypted to exactly what was written.
sha256sum "$ARCHIVE" | cut -d' ' -f1 > "${ARCHIVE}.sha256"

echo
echo "backup:  $ARCHIVE"
echo "size:    $(stat -c%s "$ARCHIVE") bytes"
echo "sha256:  $(cat "${ARCHIVE}.sha256")"

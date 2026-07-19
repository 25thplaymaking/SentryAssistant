#!/usr/bin/env bash
# Restore a Sentry backup into an isolated database and prove it is usable.
#
# Restores to a throwaway database, never over the live one, so this can be run
# on a schedule without risk. An untested backup is not a backup, and the only
# way to know is to restore it.
#
# Usage:
#   sentry-verify-restore.sh /srv/sentry/backups/sentry-<stamp>.tar.gz.enc
#
# Requires SENTRY_BACKUP_PASSPHRASE in the environment.

set -euo pipefail

ARCHIVE="${1:?usage: sentry-verify-restore.sh <archive.tar.gz.enc>}"
DEPLOY_DIR="${DEPLOY_DIR:-/srv/sentry/repo/deploy/linux}"
VERIFY_DB="sentry_restore_verify"
WORK="$(mktemp -d)"
START="$(date +%s)"

cleanup() {
    rm -rf "$WORK"
    docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" sentry-postgres-1 \
        psql -U "$POSTGRES_USER" -d postgres -q \
        -c "DROP DATABASE IF EXISTS ${VERIFY_DB};" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [ -z "${SENTRY_BACKUP_PASSPHRASE:-}" ]; then
    echo "SENTRY_BACKUP_PASSPHRASE is required." >&2
    exit 2
fi

# shellcheck disable=SC1091
set -a; . "${DEPLOY_DIR}/.env"; set +a

fail() { echo "  [FAIL] $1"; FAILED=1; }
pass() { echo "  [PASS] $1"; }
FAILED=0

echo "=== 1. archive integrity ==="
if [ -f "${ARCHIVE}.sha256" ]; then
    EXPECTED="$(cat "${ARCHIVE}.sha256")"
    ACTUAL="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
    [ "$EXPECTED" = "$ACTUAL" ] && pass "archive hash matches" || fail "archive hash mismatch"
else
    fail "no recorded hash beside the archive"
fi

echo "=== 2. decrypt ==="
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass env:SENTRY_BACKUP_PASSPHRASE -in "$ARCHIVE" \
  | tar -xzf - -C "$WORK"
[ -f "${WORK}/postgres.sql" ] && pass "decrypted and extracted" || fail "extraction failed"

echo "=== 3. contents match the manifest ==="
MANIFEST_PG="$(grep '^postgres_sha256=' "${WORK}/manifest.txt" | cut -d= -f2)"
ACTUAL_PG="$(sha256sum "${WORK}/postgres.sql" | cut -d' ' -f1)"
[ "$MANIFEST_PG" = "$ACTUAL_PG" ] && pass "dump hash matches manifest" || fail "dump hash mismatch"

echo "=== 4. restore into an isolated database ==="
docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" sentry-postgres-1 \
    psql -U "$POSTGRES_USER" -d postgres -q \
    -c "DROP DATABASE IF EXISTS ${VERIFY_DB};" -c "CREATE DATABASE ${VERIFY_DB};"
docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" sentry-postgres-1 \
    psql -U "$POSTGRES_USER" -d "${VERIFY_DB}" -q -v ON_ERROR_STOP=1 \
    < "${WORK}/postgres.sql" >/dev/null
pass "restored without error"

query() {
    docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" sentry-postgres-1 \
        psql -U "$POSTGRES_USER" -d "$1" -q -t -A -c "$2" | head -1
}

echo "=== 5. the restored database is structurally complete ==="
for table in users devices profiles teams team_members work_orders \
             work_order_transitions work_order_runs audit_events execution_nodes; do
    EXISTS="$(query "$VERIFY_DB" "SELECT to_regclass('public.${table}') IS NOT NULL;")"
    [ "$EXISTS" = "t" ] && pass "table ${table}" || fail "table ${table} missing"
done

echo "=== 6. row counts match the live database ==="
for table in users profiles work_orders audit_events; do
    LIVE="$(query "$POSTGRES_DB" "SELECT count(*) FROM ${table};")"
    REST="$(query "$VERIFY_DB" "SELECT count(*) FROM ${table};")"
    [ "$LIVE" = "$REST" ] && pass "${table}: ${REST} rows" \
        || fail "${table}: live ${LIVE} vs restored ${REST}"
done

echo "=== 7. credential tables restored empty by design ==="
for table in refresh_tokens enrollment_codes work_order_nonces; do
    COUNT="$(query "$VERIFY_DB" "SELECT count(*) FROM ${table};")"
    [ "$COUNT" = "0" ] && pass "${table} carries no live credentials" \
        || fail "${table} unexpectedly contains ${COUNT} rows"
done

echo "=== 8. append-only enforcement survived the restore ==="
# The trigger must come back with the schema, or a restored database would
# silently accept audit tampering.
TRIGGER="$(query "$VERIFY_DB" "SELECT count(*) FROM pg_trigger WHERE tgrelid='audit_events'::regclass AND NOT tgisinternal;")"
[ "$TRIGGER" -ge 1 ] && pass "audit trigger present" || fail "audit trigger missing"

DELETED=$(docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" sentry-postgres-1 \
    psql -U "$POSTGRES_USER" -d "$VERIFY_DB" -q -t -A \
    -c "DELETE FROM audit_events;" 2>&1 || true)
echo "$DELETED" | grep -q "append-only" \
    && pass "audit deletion still refused after restore" \
    || fail "audit was deletable after restore: ${DELETED}"

ELAPSED=$(( $(date +%s) - START ))
echo
echo "recovery time: ${ELAPSED}s"
if [ "$FAILED" -ne 0 ]; then
    echo "RESTORE VERIFICATION FAILED"
    exit 1
fi
echo "RESTORE VERIFIED"

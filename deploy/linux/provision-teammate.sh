#!/usr/bin/env bash
#
# Provision a Sentry teammate: hardened per-user Hermes container + profile +
# registered runtime endpoint + a username/password + an enrollment code.
#
# The handover is the username and password this prints. The enrollment code is
# still printed too: it is how a fresh device pairs, and the way in for someone
# who has no password yet.
#
# Hermes selects its profile per PROCESS, never per request, so isolation means
# one container per profile with its own HERMES_HOME. This script is the whole
# "add a teammate" path; doing it by hand is how a container ends up without a
# /workspace mount, without resource limits, or without skill governance.
#
# Run on grain.silo from deploy/linux:
#     ./provision-teammate.sh --name "Alice" --slug alice
#
# Re-running against an EXISTING container is refused unless you pass --recreate;
# with it, the container is rebuilt from the same data directory and a fresh
# enrollment code is minted. Existing memory/skills/sessions are preserved.
# Note --recreate rotates the container API key; the Gateway reloads it on
# demand at the next turn, so no restart is required.
#
set -euo pipefail

NAME=""
SLUG=""
USERNAME=""
PASSWORD=""
MEMORY_LIMIT="2g"
CPU_LIMIT="1.5"
PIDS_LIMIT="512"
RECREATE=0

usage() {
    sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'USAGE'

Options:
  --name  <display name>   Required. Shown in the UI and audit log.
  --slug  <short-slug>     Required. [a-z0-9-]; names the container and data dir.
  --username <name>        Login name (default: the slug).
  --password <secret>      Initial password; a strong one is generated if omitted.
                           Either way it must be changed at first sign-in.
  --memory <limit>         Container memory cap (default 2g).
  --cpus   <limit>         Container CPU cap (default 1.5).
  --pids   <limit>         Container PID cap (default 512).
  --recreate               Replace an existing container (keeps its data).
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --name)     NAME="${2:-}"; shift 2 ;;
        --slug)     SLUG="${2:-}"; shift 2 ;;
        --username) USERNAME="${2:-}"; shift 2 ;;
        --password) PASSWORD="${2:-}"; shift 2 ;;
        --memory)   MEMORY_LIMIT="${2:-}"; shift 2 ;;
        --cpus)     CPU_LIMIT="${2:-}"; shift 2 ;;
        --pids)     PIDS_LIMIT="${2:-}"; shift 2 ;;
        --recreate) RECREATE=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

[ -n "$NAME" ] || { echo "ERROR: --name is required" >&2; exit 2; }
[ -n "$SLUG" ] || { echo "ERROR: --slug is required" >&2; exit 2; }
# The slug names a container and a path; keep it boring so neither can be
# smuggled into something else.
printf '%s' "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]{0,30}$' \
    || { echo "ERROR: --slug must match ^[a-z0-9][a-z0-9-]{0,30}$" >&2; exit 2; }

cd "$(dirname "$0")"
COMPOSE_DIR="$(pwd)"
CONTAINER="sentry-hermes-${SLUG}"
DATA_DIR="${COMPOSE_DIR}/data/hermes/${SLUG}"
WORK_DIR="${COMPOSE_DIR}/data/workspace-${SLUG}"
NETWORK="sentry_sentry"

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

[ -f .env ] || die "no .env in ${COMPOSE_DIR}"
docker network inspect "$NETWORK" >/dev/null 2>&1 || die "docker network ${NETWORK} not found"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    [ "$RECREATE" = "1" ] || die "${CONTAINER} already exists; pass --recreate to replace it (data is kept)"
    step "Removing existing ${CONTAINER} (data directory is preserved)"
    docker rm -f "$CONTAINER" >/dev/null
    ok "removed"
fi

# 1. Per-user data + workspace -------------------------------------------------
step "Preparing per-user directories"
mkdir -p "$DATA_DIR" "$WORK_DIR"
# 0700: one person's memory, skills, sessions and config. Never group-readable,
# never shared with another profile's container.
chmod 700 "$DATA_DIR" "$WORK_DIR"
ok "data      ${DATA_DIR} (0700)"
ok "workspace ${WORK_DIR} (0700)"

# Seed the governed teammate config on first provision only, so re-running never
# stomps a config an operator has since tuned.
if [ ! -f "${DATA_DIR}/config.yaml" ]; then
    cp hermes/config.teammate.yaml "${DATA_DIR}/config.yaml"
    ok "seeded config.yaml (memory + skill governance ON)"
else
    ok "config.yaml already present; left untouched"
fi

# 2. Hardened container --------------------------------------------------------
step "Starting ${CONTAINER}"
API_KEY="$(docker exec sentry-gateway-1 python -c 'import secrets;print(secrets.token_hex(24))')"
[ -n "$API_KEY" ] || die "could not generate an API key"
HERMES_UID="$(id -u)"
HERMES_GID="$(id -g)"

# Hardening notes:
#   --cap-drop ALL / --security-opt no-new-privileges  a compromised agent
#       cannot escalate inside its own container.
#   --memory/--cpus/--pids-limit  one person's runaway turn cannot starve the
#       Gateway, Postgres, or another teammate's agent on this shared box.
#   no --publish, --network sentry_sentry  reachable ONLY by the Gateway on the
#       internal network; never from the host or the internet.
#   no docker socket  deliberate: mounting it would grant effective host root
#       and destroy the container boundary this whole design relies on.
docker run -d \
    --name "$CONTAINER" \
    --network "$NETWORK" \
    --restart unless-stopped \
    --memory "$MEMORY_LIMIT" \
    --cpus "$CPU_LIMIT" \
    --pids-limit "$PIDS_LIMIT" \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --user "${HERMES_UID}:${HERMES_GID}" \
    -e API_SERVER_ENABLED=true \
    -e API_SERVER_KEY="$API_KEY" \
    -e API_SERVER_HOST=0.0.0.0 \
    -e API_SERVER_PORT=8642 \
    -e HERMES_HOME=/home/hermes/.hermes \
    -v "${DATA_DIR}:/home/hermes/.hermes" \
    -v "${WORK_DIR}:/workspace" \
    sentry-hermes >/dev/null || die "docker run failed"
ok "started with mem=${MEMORY_LIMIT} cpus=${CPU_LIMIT} pids=${PIDS_LIMIT}, caps dropped"
echo "NOTICE: Nous OAuth is profile-specific. Authenticate this teammate with:"
echo "  docker exec -it ${CONTAINER} hermes auth add nous --type oauth"

# 3. Wait for the API server ---------------------------------------------------
step "Waiting for the agent's API server"
READY=0
for _ in $(seq 1 30); do
    sleep 2
    if docker exec "$CONTAINER" python -c "
import sys, urllib.request
req = urllib.request.Request('http://127.0.0.1:8642/health')
try:
    urllib.request.urlopen(req, timeout=3)
except Exception as exc:
    sys.exit(1 if '401' not in str(exc) else 0)  # 401 still proves it is serving
" 2>/dev/null; then READY=1; break; fi
done
[ "$READY" = "1" ] || {
    echo "--- last 20 log lines ---" >&2
    docker logs --tail 20 "$CONTAINER" >&2 || true
    die "${CONTAINER} did not start serving /health"
}
ok "serving"

# 4. Profile + endpoint + credential + enrollment code --------------------------
step "Registering the profile, runtime endpoint and sign-in credential"
PROVISION_ARGS=(--display-name "$NAME" --slug "$SLUG" --hermes-api-key "$API_KEY")
# if/fi rather than `[ -n "$X" ] && ...`: under `set -e` that idiom aborts the
# whole script when the test is false, because the AND-list's exit status is the
# failed test's.
if [ -n "$USERNAME" ]; then
    PROVISION_ARGS+=(--username "$USERNAME")
fi
# Passed as an argument, so it is visible in this container's process list for
# the moment the script runs -- the same exposure the Hermes API key above
# already has, on a box only operators can reach. Omit --password and let a
# strong one be generated if even that is too much.
if [ -n "$PASSWORD" ]; then
    PROVISION_ARGS+=(--password "$PASSWORD")
fi
docker exec sentry-gateway-1 python scripts/provision_teammate.py "${PROVISION_ARGS[@]}"

cat <<EOF

Done. Give ${NAME} the Sentry web app URL, the username and the password printed
above -- that is the whole handover. They will be asked to choose their own
password at first sign-in.

The enrollment code above is for pairing a device (and is the way in for anyone
who has no password yet). It is valid five minutes; re-run this script with
--recreate, or the gateway script alone, to mint another.

The Gateway loads the new endpoint on demand at first turn, so no restart is
needed.
EOF

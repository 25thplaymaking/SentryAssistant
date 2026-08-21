#!/usr/bin/env bash
set -Eeuo pipefail

proxy_pid=""
gateway_pid=""

shutdown() {
  trap - EXIT TERM INT
  for pid in "$proxy_pid" "$gateway_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}

trap shutdown EXIT TERM INT

hermes proxy start --provider nous --host 0.0.0.0 --port 8645 &
proxy_pid=$!

hermes gateway &
gateway_pid=$!

set +e
wait -n "$proxy_pid" "$gateway_pid"
status=$?
set -e

# Reaching this point means one required process exited. The EXIT trap stops
# the other one; Docker then restarts the complete pair.
exit "$status"

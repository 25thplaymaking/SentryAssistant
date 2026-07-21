#!/usr/bin/env bash
#
# Manage the MCP servers available to one Sentry profile's agent.
#
# Hermes reads `mcp_servers` from the profile's own config.yaml inside its
# HERMES_HOME, and selects its profile per PROCESS. So MCP config is per
# container, applied on the host bind mount, and takes effect on restart. The
# Gateway deliberately has no filesystem access to these homes (that would
# couple it to Hermes internals), which is why this is an operator tool rather
# than a Gateway API.
#
# Every entry is checked by Hermes' OWN validator (validate_mcp_server_entry)
# before it is written, so a suspicious entry is refused by the same code path
# that guards the interactive picker.
#
#   ./manage-mcp.sh --list-catalogue
#   ./manage-mcp.sh --slug personal --list
#   ./manage-mcp.sh --slug alice --add filesystem
#   ./manage-mcp.sh --slug alice --add github --env GITHUB_PERSONAL_ACCESS_TOKEN=ghp_x
#   ./manage-mcp.sh --slug alice --remove github
#
set -euo pipefail

SLUG=""
ACTION=""
NAME=""
ENV_PAIRS=()
NO_RESTART=0

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --slug)           SLUG="${2:-}"; shift 2 ;;
        --add)            ACTION="add"; NAME="${2:-}"; shift 2 ;;
        --remove)         ACTION="remove"; NAME="${2:-}"; shift 2 ;;
        --list)           ACTION="list"; shift ;;
        --list-catalogue) ACTION="catalogue"; shift ;;
        --env)            ENV_PAIRS+=("${2:-}"); shift 2 ;;
        --no-restart)     NO_RESTART=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

cd "$(dirname "$0")"
COMPOSE_DIR="$(pwd)"
CATALOGUE="${COMPOSE_DIR}/mcp-catalogue.yaml"
[ -f "$CATALOGUE" ] || { echo "ERROR: missing $CATALOGUE" >&2; exit 1; }

die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

if [ "$ACTION" = "catalogue" ]; then
    python3 - "$CATALOGUE" <<'PY'
import sys, re
path = sys.argv[1]
text = open(path).read()
# Deliberately not importing yaml: this must run on the host, which has no
# guaranteed PyYAML. The catalogue's shape is fixed and simple.
print(f"{'NAME':<22}{'RISK':<9}NEEDS")
for m in re.finditer(r"\n  ([a-z0-9-]+):\n(.*?)(?=\n  [a-z0-9-]+:\n|\Z)", text, re.S):
    name, block = m.group(1), m.group(2)
    risk = (re.search(r"risk:\s*(\w+)", block) or [None, "?"])[1]
    envs = re.search(r"needs_env:\s*\[(.*?)\]", block)
    print(f"{name:<22}{risk:<9}{envs.group(1) if envs else '-'}")
PY
    exit 0
fi

[ -n "$SLUG" ] || die "--slug is required (use 'personal' for the owner profile)"
printf '%s' "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]{0,30}$' || die "invalid --slug"

if [ "$SLUG" = "personal" ]; then
    CONTAINER="sentry-hermes-1"
    CONFIG="${COMPOSE_DIR}/data/hermes/personal/config.yaml"
else
    CONTAINER="sentry-hermes-${SLUG}"
    CONFIG="${COMPOSE_DIR}/data/hermes/${SLUG}/config.yaml"
fi
[ -f "$CONFIG" ] || die "no config at $CONFIG — is '${SLUG}' provisioned?"

# All config mutation happens inside the Hermes container so we can use its own
# YAML library and its own MCP validator rather than reimplementing either.
run_in_hermes() {
    docker exec -i "$CONTAINER" python3 - "$@"
}

case "$ACTION" in
  list)
    run_in_hermes <<'PY'
import yaml, pathlib
cfg = yaml.safe_load(pathlib.Path("/home/hermes/.hermes/config.yaml").read_text()) or {}
servers = cfg.get("mcp_servers") or {}
if not servers:
    print("(no MCP servers configured for this profile)")
else:
    for name, entry in sorted(servers.items()):
        args = " ".join(str(a) for a in (entry.get("args") or []))
        env = ",".join((entry.get("env") or {}).keys()) or "-"
        print(f"{name:<22}{entry.get('command','?')} {args}\n{'':<22}env: {env}")
PY
    ;;

  add|remove)
    [ -n "$NAME" ] || die "--$ACTION needs a server name"
    ENTRY_JSON="{}"
    if [ "$ACTION" = "add" ]; then
        # Parse the catalogue on the host, pass one JSON entry into the container.
        ENTRY_JSON=$(python3 - "$CATALOGUE" "$NAME" "${ENV_PAIRS[@]:-}" <<'PY'
import sys, re, json
path, name = sys.argv[1], sys.argv[2]
pairs = [p for p in sys.argv[3:] if p]
text = open(path).read()
m = re.search(r"\n  " + re.escape(name) + r":\n(.*?)(?=\n  [a-z0-9-]+:\n|\Z)", text, re.S)
if not m:
    sys.stderr.write(f"'{name}' is not in the catalogue\n"); sys.exit(3)
block = m.group(1)
cmd = re.search(r"command:\s*(\S+)", block)
args = re.search(r"args:\s*(\[.*?\])", block, re.S)
if not cmd or not args:
    sys.stderr.write(f"catalogue entry '{name}' is malformed\n"); sys.exit(3)
entry = {"command": cmd.group(1), "args": json.loads(args.group(1))}
needs = re.search(r"needs_env:\s*\[(.*?)\]", block)
required = [v.strip().strip('"') for v in needs.group(1).split(",")] if needs else []
supplied = {}
for p in pairs:
    if "=" not in p:
        sys.stderr.write(f"--env expects NAME=VALUE, got {p!r}\n"); sys.exit(3)
    k, v = p.split("=", 1)
    supplied[k] = v
missing = [r for r in required if r not in supplied]
if missing:
    # Refuse rather than install a server that will fail at first use.
    sys.stderr.write(f"'{name}' requires --env for: {', '.join(missing)}\n"); sys.exit(3)
if supplied:
    entry["env"] = supplied
print(json.dumps({"name": name, "entry": entry}))
PY
        ) || die "could not build the entry (see above)"
    fi

    printf '%s' "$ENTRY_JSON" | docker exec -i "$CONTAINER" python3 - "$ACTION" "$NAME" <<'PY'
import json, sys, pathlib, yaml
action, name = sys.argv[1], sys.argv[2]
path = pathlib.Path("/home/hermes/.hermes/config.yaml")
cfg = yaml.safe_load(path.read_text()) or {}
servers = cfg.get("mcp_servers") or {}

if action == "add":
    payload = json.loads(sys.stdin.read() or "{}")
    entry = payload["entry"]
    # Hermes' own validator — the same one guarding its interactive picker.
    try:
        from hermes_cli.mcp_security import validate_mcp_server_entry
        issues = validate_mcp_server_entry(name, entry)
    except Exception as exc:                      # validator must never be skipped silently
        print(f"REFUSED: could not run Hermes' MCP validator ({exc})"); sys.exit(4)
    if issues:
        print("REFUSED by Hermes' MCP security validator:")
        for i in issues:
            print(f"  - {i}")
        sys.exit(4)
    servers[name] = entry
    cfg["mcp_servers"] = servers
    print(f"added '{name}'")
else:
    if name not in servers:
        print(f"'{name}' was not configured; nothing to do"); sys.exit(0)
    servers.pop(name)
    if servers:
        cfg["mcp_servers"] = servers
    else:
        cfg.pop("mcp_servers", None)
    print(f"removed '{name}'")

# Write via a temp file + replace so an interrupted write cannot truncate the
# profile's only config.
tmp = path.with_suffix(".yaml.tmp")
tmp.write_text(yaml.safe_dump(cfg, sort_keys=False))
tmp.replace(path)
PY

    if [ "$NO_RESTART" = "0" ]; then
        echo "restarting ${CONTAINER} so the change takes effect"
        docker restart "$CONTAINER" >/dev/null
        echo "restarted"
    else
        echo "NOT restarting (--no-restart); the change applies on next restart"
    fi
    ;;

  *)
    usage; exit 2 ;;
esac

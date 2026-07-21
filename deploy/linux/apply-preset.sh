#!/usr/bin/env bash
#
# Apply a Sentry profile distribution (persona + config policy) to one profile.
#
# Plan Task 6 calls for Sentry-owned distributions rather than depending on a
# turnkey personal-assistant preset that does not exist. A preset is a persona
# (SOUL.md) plus a small config policy overlay; it never carries secrets, user
# memories, or sessions, so a preset file is safe to keep in Git.
#
#   ./apply-preset.sh --list
#   ./apply-preset.sh --slug alice --preset personal-operator
#   ./apply-preset.sh --slug ops   --preset team-coordinator
#
# The overlay MERGES: keys the preset does not mention are left exactly as the
# profile already has them, so applying a preset never silently undoes a
# deliberate local change.
#
set -euo pipefail

SLUG=""
PRESET=""
ACTION="apply"
NO_RESTART=0

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --slug)       SLUG="${2:-}"; shift 2 ;;
        --preset)     PRESET="${2:-}"; shift 2 ;;
        --list)       ACTION="list"; shift ;;
        --no-restart) NO_RESTART=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

cd "$(dirname "$0")"
COMPOSE_DIR="$(pwd)"
PRESET_DIR="${COMPOSE_DIR}/hermes/presets"
die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

if [ "$ACTION" = "list" ]; then
    printf '%-24s %s\n' "PRESET" "SUMMARY"
    for f in "$PRESET_DIR"/*.yaml; do
        [ -f "$f" ] || continue
        n=$(basename "$f" .yaml)
        s=$(python3 -c "
import re,sys
t=open(sys.argv[1],encoding='utf-8').read()
m=re.search(r'^summary:\s*>-\s*\n((?:  .*\n)+)', t, re.M)
print(' '.join(m.group(1).split()) if m else '')
" "$f")
        printf '%-24s %s\n' "$n" "$s"
    done
    exit 0
fi

[ -n "$SLUG" ]   || die "--slug is required (use 'personal' for the owner profile)"
[ -n "$PRESET" ] || die "--preset is required (see --list)"
printf '%s' "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]{0,30}$' || die "invalid --slug"
printf '%s' "$PRESET" | grep -qE '^[a-z0-9][a-z0-9-]{0,40}$' || die "invalid --preset"

PRESET_FILE="${PRESET_DIR}/${PRESET}.yaml"
[ -f "$PRESET_FILE" ] || die "no such preset: ${PRESET} (see --list)"

# Every preset turns skill governance ON, which is right for a teammate but
# would silently reverse the OWNER profile's documented, deliberate decision to
# leave it off. Require an explicit override rather than quietly flipping it.
if [ "$SLUG" = "personal" ] && [ "${SENTRY_PRESET_ALLOW_OWNER:-0}" != "1" ]; then
    die "refusing to apply a preset to the owner profile: every preset enables
skills.write_approval/guard_agent_created, reversing the documented risk
acceptance in hermes/config.yaml. Re-run with SENTRY_PRESET_ALLOW_OWNER=1 if
that is genuinely what you want."
fi

if [ "$SLUG" = "personal" ]; then
    CONTAINER="sentry-hermes-1"
    HOME_DIR="${COMPOSE_DIR}/data/hermes/personal"
else
    CONTAINER="sentry-hermes-${SLUG}"
    HOME_DIR="${COMPOSE_DIR}/data/hermes/${SLUG}"
fi
[ -d "$HOME_DIR" ] || die "no profile home at ${HOME_DIR} — is '${SLUG}' provisioned?"

echo "==> Applying '${PRESET}' to profile '${SLUG}'"

# Parsed and merged inside the container so we use Hermes' own PyYAML rather
# than depending on a host install.
docker cp "$PRESET_FILE" "${CONTAINER}:/tmp/preset.yaml" >/dev/null
docker exec -i "$CONTAINER" python3 - <<'PY'
import pathlib, yaml

preset = yaml.safe_load(pathlib.Path("/tmp/preset.yaml").read_text()) or {}
home = pathlib.Path("/home/hermes/.hermes")
cfg_path = home / "config.yaml"
cfg = yaml.safe_load(cfg_path.read_text()) or {}

def merge(base, overlay):
    """Recursive merge: the overlay only sets keys it actually mentions."""
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base

merge(cfg, preset.get("config") or {})

tmp = cfg_path.with_suffix(".yaml.tmp")
tmp.write_text(yaml.safe_dump(cfg, sort_keys=False))
tmp.replace(cfg_path)
print(f"config policy merged ({', '.join((preset.get('config') or {}).keys()) or 'no config keys'})")

soul = preset.get("soul")
if soul:
    soul_path = home / "SOUL.md"
    if soul_path.exists():
        # Never destroy a persona someone has tuned. One rolling copy, not a
        # timestamped history: re-applying overwrites it.
        backup = home / "SOUL.md.replaced"
        backup.write_text(soul_path.read_text())
        print("previous SOUL.md kept as SOUL.md.replaced")
    soul_path.write_text(soul)
    print(f"persona written ({len(soul.splitlines())} lines)")

rec = preset.get("recommends_mcp") or []
if rec:
    print("recommended MCP servers: " + ", ".join(rec))
    print("  add them with: ./manage-mcp.sh --slug <profile> --add <name>")
PY

if [ "$NO_RESTART" = "0" ]; then
    echo "restarting ${CONTAINER}"
    docker restart "$CONTAINER" >/dev/null
    echo "restarted"
else
    echo "NOT restarting (--no-restart); applies on next restart"
fi

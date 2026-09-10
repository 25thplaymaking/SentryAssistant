#!/usr/bin/env python3
"""Register the local model in Server Control under a HOSTED MODELS tab.

Server Control has no plugin system and ships as a compiled binary with a
prebuilt SPA, so a genuinely custom tab would need its frontend source (which is
not on this box). It does not need one: the portal builds its category filter
DYNAMICALLY from the distinct `Category` values of the registered services --

    Array.from(new Set(services.map(s => s.category))).sort()

-- so declaring a service whose Category is "HOSTED MODELS" creates exactly that
tab, with no frontend change at all.

Run as root on grain.silo (the config lives under /opt/server-control):

    sudo python3 register-hosted-models.py            # apply
    sudo python3 register-hosted-models.py --dry-run  # show the diff only
    sudo python3 register-hosted-models.py --remove   # undo

A timestamped backup is written next to the file before anything is changed, and
the new JSON is validated by re-parsing it before it replaces the original --
a malformed appsettings file would stop the portal from starting at all.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

CONFIG = Path("/opt/server-control/current/appsettings.Production.json")

CATEGORY = "HOSTED MODELS"

# Server Control's ManagedServices schema has no description field, so the
# warning has to live in the name -- it is the only free text the operator sees
# in the services table. The full operating manual is docs/local-inference.md.
ENTRY = {
    "Id": "sentry-llama",
    "Name": "Qwen3.6-35B-A3B — 24GB RAM, saturates memory bandwidth, RUN ALONE",
    "Category": CATEGORY,
    "Kind": "DockerContainer",
    "Target": "sentry-llama-1",
    # No published port by design: the runtime is reachable only from the
    # compose network, exactly like the Hermes runtime. Leaving this empty
    # avoids the portal reporting a missing-port fault for a healthy service.
    "ExpectedPorts": [],
}


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args()

    if not CONFIG.exists():
        print(f"ERROR: {CONFIG} not found", file=sys.stderr)
        return 1

    try:
        cfg = load(CONFIG)
    except json.JSONDecodeError as exc:
        print(f"ERROR: {CONFIG} is not valid JSON ({exc}) -- refusing to touch it",
              file=sys.stderr)
        return 1

    services = cfg.setdefault("Portal", {}).setdefault("ManagedServices", [])
    others = [s for s in services if s.get("Id") != ENTRY["Id"]]

    if args.remove:
        if len(others) == len(services):
            print("Not registered; nothing to remove.")
            return 0
        cfg["Portal"]["ManagedServices"] = others
        action = "REMOVE"
    else:
        if any(s == ENTRY for s in services):
            print(f"Already registered under '{CATEGORY}'. No change.")
            return 0
        cfg["Portal"]["ManagedServices"] = others + [ENTRY]
        action = "UPDATE" if len(others) != len(services) else "ADD"

    rendered = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"

    # Validate before writing: a broken appsettings stops the portal booting.
    json.loads(rendered)

    print(f"{action}: {json.dumps(ENTRY, indent=2, ensure_ascii=False)}")
    cats = sorted({s.get("Category", "?") for s in cfg["Portal"]["ManagedServices"]})
    print(f"\nPortal tabs after this change: {cats}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    backup = CONFIG.with_suffix(f".json.bak.{int(time.time())}")
    shutil.copy2(CONFIG, backup)
    CONFIG.write_text(rendered, encoding="utf-8")
    print(f"\nWrote {CONFIG}\nBackup {backup}")
    print("\nNow restart the portal:\n  sudo systemctl restart server-control.service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

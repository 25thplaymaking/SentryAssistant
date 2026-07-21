"""Import MCP servers from Codex and Claude Code into a Sentry-usable catalogue.

Both harnesses already carry a curated set of MCP servers on this machine. This
reads them and emits entries in the same shape as
``deploy/linux/mcp-catalogue.yaml``, so they can be handed to a profile with
``manage-mcp.sh``.

The hard part is NOT parsing — it is honesty about portability. Sentry's agents
run in a Linux container on grain.silo; the harnesses run on Windows. A server
is only importable when it can actually start over there:

  portable      npx/node package specs (fetched at launch), and REMOTE https URLs
                (Hermes supports streamable-HTTP and SSE transports).
  NOT portable  .exe commands, absolute Windows paths, and localhost/127.0.0.1
                URLs — the container's localhost is its own, not your desktop's.

Anything not portable is reported with the reason rather than silently dropped
or, worse, emitted as an entry that fails at first use.

``@latest`` is resolved to the version published right now and pinned, matching
the catalogue's policy: an unpinned spec executes newly-published code inside a
container holding provider keys, on every launch.

    python tools/mcp-import/import_harness_mcp.py            # report only
    python tools/mcp-import/import_harness_mcp.py -o out.yaml
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]|\\\\")
LOCAL_URL = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:|/|$)", re.I)


class Entry:
    def __init__(self, name: str, source: str) -> None:
        self.name = name
        self.source = source
        self.entry: dict[str, Any] = {}
        self.skip_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.skip_reason is None


def _npm_latest(pkg: str) -> str | None:
    """Resolve a package's current version so an @latest spec can be pinned."""
    try:
        url = f"https://registry.npmjs.org/{pkg}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.load(resp)["dist-tags"]["latest"]
    except Exception:
        return None


def _pin(spec: str) -> tuple[str, str | None]:
    """Return (pinned_spec, note). Only touches specs ending in @latest or bare."""
    if spec.startswith("-"):
        return spec, None
    # Split a scoped or plain package spec from its version.
    m = re.match(r"^(@[^/]+/[^@]+|[^@][^@]*)(?:@(.+))?$", spec)
    if not m:
        return spec, None
    pkg, ver = m.group(1), m.group(2)
    if ver and ver != "latest":
        return spec, None
    latest = _npm_latest(pkg)
    if not latest:
        return spec, f"could not resolve a version for {pkg}; left unpinned"
    return f"{pkg}@{latest}", f"pinned {pkg} to {latest}"


def _classify_stdio(name: str, command: str, args: list[str], env: dict) -> Entry:
    e = Entry(name, "")
    command = str(command or "")
    args = [str(a) for a in (args or [])]

    # Windows shell wrapper: `cmd /c npx -y pkg` is the same portable command
    # once the wrapper is removed.
    if command.lower() in ("cmd", "cmd.exe") and args[:1] in (["/c"], ["/C"]):
        rest = args[1:]
        if rest:
            command, args = rest[0], rest[1:]

    if command.lower().endswith(".exe") or WINDOWS_PATH.match(command):
        e.skip_reason = f"command is a Windows executable/path ({command})"
        return e
    if command not in ("npx", "node", "npm"):
        e.skip_reason = f"command {command!r} is not one the Linux container provides"
        return e
    for a in args:
        if WINDOWS_PATH.match(a):
            e.skip_reason = f"argument is an absolute Windows path ({a})"
            return e
    if command == "node":
        # node + a local script cannot exist in the container.
        e.skip_reason = "runs a local node script, which does not exist in the container"
        return e

    # Only the FIRST non-flag argument is the package spec; everything after it
    # is an argument TO that package. Pinning them all would rewrite e.g.
    # `npx -y toolbox bigquery` into `bigquery@0.0.6` — a real, unrelated npm
    # package — silently changing what the command does.
    notes: list[str] = []
    pinned: list[str] = []
    pkg_seen = False
    for a in args:
        if not pkg_seen and not a.startswith("-"):
            spec, note = _pin(a)
            pkg_seen = True
            pinned.append(spec)
            if note:
                notes.append(note)
        else:
            pinned.append(a)

    e.entry = {"command": command, "args": pinned}
    if env:
        # Values may be secrets; carry the NAMES so the operator supplies them.
        e.entry["needs_env"] = sorted(env.keys())
    e.entry["_notes"] = notes
    return e


def _classify_remote(name: str, url: str, transport: str | None) -> Entry:
    e = Entry(name, "")
    if LOCAL_URL.match(url):
        e.skip_reason = (
            f"points at {url} — the container's localhost is its own, not your desktop's"
        )
        return e
    if not url.lower().startswith("https://"):
        e.skip_reason = f"non-HTTPS remote URL ({url})"
        return e
    e.entry = {"url": url}
    if transport and transport.lower() == "sse":
        e.entry["transport"] = "sse"
    e.entry["_notes"] = []
    return e


def _from_mapping(name: str, cfg: dict, source: str) -> Entry:
    if cfg.get("url"):
        e = _classify_remote(name, str(cfg["url"]), cfg.get("transport") or cfg.get("type"))
    else:
        e = _classify_stdio(name, cfg.get("command", ""), cfg.get("args") or [], cfg.get("env") or {})
    e.source = source
    return e


def read_codex(home: Path) -> list[Entry]:
    """Parse [mcp_servers.*] out of Codex's config.toml."""
    path = home / ".codex" / "config.toml"
    if not path.is_file():
        return []
    try:
        import tomllib
        data = tomllib.loads(io.open(path, encoding="utf-8").read())
    except Exception as exc:
        print(f"  ! could not parse {path}: {exc}", file=sys.stderr)
        return []
    out = []
    for name, cfg in (data.get("mcp_servers") or {}).items():
        if isinstance(cfg, dict):
            out.append(_from_mapping(name, cfg, "codex"))
    return out


def read_claude(home: Path) -> list[Entry]:
    """Claude Code keeps servers in ~/.claude.json, globally and per project."""
    path = home / ".claude.json"
    if not path.is_file():
        return []
    try:
        data = json.load(io.open(path, encoding="utf-8"))
    except Exception as exc:
        print(f"  ! could not parse {path}: {exc}", file=sys.stderr)
        return []
    found: dict[str, Entry] = {}
    for name, cfg in (data.get("mcpServers") or {}).items():
        if isinstance(cfg, dict):
            found[name] = _from_mapping(name, cfg, "claude-code")
    for proj, pcfg in (data.get("projects") or {}).items():
        for name, cfg in ((pcfg or {}).get("mcpServers") or {}).items():
            if isinstance(cfg, dict) and name not in found:
                found[name] = _from_mapping(name, cfg, f"claude-code project {Path(proj).name}")
    return list(found.values())


def read_claude_plugins(home: Path) -> list[Entry]:
    """Installed Claude Code plugins may ship their own .mcp.json."""
    installed = home / ".claude" / "plugins" / "installed_plugins.json"
    if not installed.is_file():
        return []
    try:
        data = json.load(io.open(installed, encoding="utf-8"))
    except Exception:
        return []
    out: list[Entry] = []
    for plugin, records in (data.get("plugins") or {}).items():
        for rec in records or []:
            root = Path(rec.get("installPath") or "")
            if not root.is_dir():
                continue
            for candidate in (".mcp.json", ".claude-mcp.json"):
                f = root / candidate
                if not f.is_file():
                    continue
                try:
                    cfg = json.load(io.open(f, encoding="utf-8"))
                except Exception:
                    continue
                for name, sc in (cfg.get("mcpServers") or {}).items():
                    if isinstance(sc, dict):
                        out.append(_from_mapping(name, sc, f"plugin {plugin.split('@')[0]}"))
    return out


def to_yaml(entries: list[Entry]) -> str:
    lines = [
        "# MCP servers imported from Codex and Claude Code.",
        "#",
        "# Generated by tools/mcp-import/import_harness_mcp.py. Every entry here was",
        "# checked for portability to the Linux container and version-pinned where the",
        "# source used @latest. Review before use: importing a server grants it to an",
        "# agent, and `needs_env` entries still require their credential.",
        "",
        "servers:",
    ]
    for e in entries:
        lines.append("")
        lines.append(f"  {e.name}:")
        lines.append(f"    description: imported from {e.source}")
        if "url" in e.entry:
            lines.append(f"    url: {json.dumps(e.entry['url'])}")
            if e.entry.get("transport"):
                lines.append(f"    transport: {e.entry['transport']}")
        else:
            lines.append(f"    command: {e.entry['command']}")
            lines.append(f"    args: {json.dumps(e.entry['args'])}")
        if e.entry.get("needs_env"):
            lines.append(f"    needs_env: {json.dumps(e.entry['needs_env'])}")
        lines.append("    risk: review")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", help="write importable entries to this YAML file")
    ap.add_argument("--home", default=os.path.expanduser("~"), help="user home to scan")
    args = ap.parse_args()

    home = Path(args.home)
    scanned = read_codex(home) + read_claude(home) + read_claude_plugins(home)

    # One server can legitimately appear more than once: a plugin often ships
    # both .mcp.json and .claude-mcp.json, and the same server may be defined in
    # both harnesses. Keep the first definition, but remember every source so
    # the report shows where it came from rather than silently picking one.
    entries: list[Entry] = []
    by_name: dict[str, Entry] = {}
    for e in scanned:
        existing = by_name.get(e.name)
        if existing is None:
            by_name[e.name] = e
            entries.append(e)
        elif e.source not in existing.source:
            existing.source = f"{existing.source}, {e.source}"

    if not entries:
        print("No MCP servers found in Codex or Claude Code configuration.")
        return 0

    importable = [e for e in entries if e.ok]
    skipped = [e for e in entries if not e.ok]

    print(f"Scanned {home}\n")
    print(f"IMPORTABLE ({len(importable)}):")
    for e in sorted(importable, key=lambda x: x.name):
        kind = "remote" if "url" in e.entry else e.entry.get("command", "?")
        print(f"  {e.name:<24} [{e.source}] {kind}")
        for n in e.entry.get("_notes") or []:
            print(f"      note: {n}")
        if e.entry.get("needs_env"):
            print(f"      needs env: {', '.join(e.entry['needs_env'])}")

    print(f"\nNOT PORTABLE ({len(skipped)}) — these cannot run in the Linux container:")
    for e in sorted(skipped, key=lambda x: x.name):
        print(f"  {e.name:<24} [{e.source}] {e.skip_reason}")

    if args.out and importable:
        Path(args.out).write_text(to_yaml(importable), encoding="utf-8")
        print(f"\nWrote {len(importable)} entries to {args.out}")
        print("Review it, then merge the entries you want into deploy/linux/mcp-catalogue.yaml")
        print("and add them per profile with ./manage-mcp.sh --slug <profile> --add <name>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

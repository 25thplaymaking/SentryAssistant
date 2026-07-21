"""Make the Hermes API-server path discover configured MCP servers.

Hermes populates the MCP tool registry by calling ``discover_mcp_tools()`` before
constructing ``AIAgent``. The CLI, TUI and cron paths all do this — cron only
after upstream fixed it in #4219, with a comment noting "cron jobs never saw any
MCP tools". The **api_server** path, which is the only one Sentry uses, still
does not, so ``/v1/responses`` sees an empty MCP registry no matter what
``mcp_servers`` is configured.

Verified on hermes-agent 0.19.0: before this patch the agent answered "NO MCP
TOOLS"; after it (together with the `mcp` SDK, which is likewise an optional
dependency) the same profile exposed the ``mcp__filesystem__*`` toolset.

Applied at image build time. Deliberately fails the build if the anchor is
missing, so a Hermes upgrade that moves this code cannot silently drop the patch
and leave MCP quietly inert — the failure mode this patch exists to fix.
"""

from __future__ import annotations

import io
import sys
import sysconfig
from pathlib import Path

MARKER = "SENTRY PATCH: initialise MCP servers"

ANCHOR = "        agent = AIAgent(\n            model=model,"

PATCH = '''        # SENTRY PATCH: initialise MCP servers so configured mcp_servers are
        # available to the tool registry BEFORE AIAgent is constructed. The cron
        # path got exactly this fix upstream (#4219); the api_server path never
        # did, so /v1/responses saw no MCP tools at all. Non-fatal: a broken MCP
        # server must not take down an otherwise-working turn.
        try:
            from tools.mcp_tool import discover_mcp_tools
            _mcp = discover_mcp_tools()
            if _mcp:
                logger.info("api_server: %d MCP tool(s) available", len(_mcp))
        except Exception:
            logger.debug("api_server: MCP discovery failed", exc_info=True)

        agent = AIAgent(
            model=model,'''


def _candidate_paths() -> list[Path]:
    """Every plausible site-packages location for the installed gateway package."""
    roots: list[str] = []
    for key in ("purelib", "platlib"):
        try:
            roots.append(sysconfig.get_path(key))
        except Exception:
            pass
    try:
        roots.append(sysconfig.get_path("purelib", scheme="posix_user"))
    except Exception:
        pass
    roots.extend(sys.path)

    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        candidate = Path(root) / "gateway" / "platforms" / "api_server.py"
        if candidate.is_file():
            out.append(candidate)
    return out


def main() -> int:
    targets = _candidate_paths()
    if not targets:
        print("FAILED: could not locate gateway/platforms/api_server.py", file=sys.stderr)
        return 1

    target = targets[0]
    src = io.open(target, encoding="utf-8").read()

    if MARKER in src:
        print(f"already patched: {target}")
        return 0

    count = src.count(ANCHOR)
    if count != 1:
        print(
            f"FAILED: expected exactly one AIAgent construction anchor in {target}, "
            f"found {count}. Hermes has moved this code — re-verify the MCP "
            f"discovery fix against the new version instead of skipping it.",
            file=sys.stderr,
        )
        return 1

    io.open(target, "w", encoding="utf-8").write(src.replace(ANCHOR, PATCH, 1))
    print(f"patched {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

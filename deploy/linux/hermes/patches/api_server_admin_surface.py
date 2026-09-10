"""Register the Sentry admin HTTP surface on the Hermes API server.

Hermes' HTTP API deliberately omits credential management and pending-write
approval — both live in the CLI. Sentry's WebUI container never loads the agent,
so "CLI only" means "unreachable from the browser": OAuth logins need a TTY on
the server, and memory proposals staged by ``memory.write_approval: true``
accumulate on disk with nothing able to approve them.

The handlers live in ``sentry_admin.py`` (installed alongside the Hermes
packages). This patch does one thing: extend ``_http_route_table()`` so they are
registered, which also gets them the ``/p/<profile>/`` multiplex mirrors and the
adapter's own per-handler ``_check_auth`` for free.

Applied at image build time. Deliberately fails the build if the anchor is
missing, so a Hermes upgrade that restructures the route table cannot silently
drop the surface and leave the approval queue unreachable again — the exact
failure mode this patch exists to fix.
"""

from __future__ import annotations

import io
import sys
import sysconfig
from pathlib import Path

MARKER = "SENTRY PATCH: admin surface"

# The cron row is appended after the literal route list, so this is the seam
# between "always registered" and "conditionally registered" — a stable place to
# add rows without touching the list literal itself.
ANCHOR = "        if _CRON_AVAILABLE:"

PATCH = '''        # SENTRY PATCH: admin surface — credential OAuth and pending-write
        # approval over HTTP. Non-fatal: a missing/broken sentry_admin must not
        # stop the API server from serving chat, which is its primary job.
        try:
            from sentry_admin import build_routes as _sentry_admin_routes
            routes.extend(_sentry_admin_routes(self))
        except Exception:
            logger.warning(
                "api_server: Sentry admin surface unavailable; "
                "OAuth and pending approvals will not be reachable over HTTP",
                exc_info=True,
            )

        if _CRON_AVAILABLE:'''


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
            f"FAILED: expected exactly one route-table cron anchor in {target}, "
            f"found {count}. Hermes has restructured _http_route_table() — "
            f"re-verify the admin surface against the new version instead of "
            f"skipping it, or the approval queue silently becomes unreachable.",
            file=sys.stderr,
        )
        return 1

    io.open(target, "w", encoding="utf-8").write(src.replace(ANCHOR, PATCH, 1))
    print(f"patched {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

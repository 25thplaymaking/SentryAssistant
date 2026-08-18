#!/usr/bin/env python3
"""Attach a subscription-backed provider and publish it as a pickable model.

    ./attach-subscription.py anthropic
    ./attach-subscription.py openai-codex --alias chatgpt-codex
    ./attach-subscription.py anthropic --model claude-opus-4-5-20260401
    ./attach-subscription.py --list

A Claude or ChatGPT subscription reaches Hermes through that provider's OWN
OAuth flow (`hermes auth add <provider> --type oauth`), which stores the
credential in Hermes' auth store inside the profile's runtime home. This script
never handles, copies, or writes the credential itself -- it only publishes a
model_route once the provider reports authenticated, so the token stays in the
one place Hermes already manages and rotates.

Why a route is needed at all: the Gateway refuses any model the profile does not
advertise, and the picker lists exactly what is advertised. Authenticating a
provider is therefore necessary but not sufficient -- until a route names it,
nothing can select it. That is deliberate (see
docs/plans/2026-08-18-model-routing-design.md): the route table is the
integration registry.

The OAuth step is interactive. Run this from a real terminal.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

COMPOSE_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE = "personal"

#: Providers whose credential is a subscription OAuth rather than an API key.
#: `hermes auth add <id> --type oauth` is the sanctioned flow for each.
SUBSCRIPTION_PROVIDERS = {
    "anthropic": {
        "label": "Claude subscription",
        "default_alias": "claude-subscription",
        # Alternative to the OAuth flow: `claude setup-token` mints a
        # long-lived subscription token, and the anthropic provider reads it
        # straight from the environment. Set it in .env and this script only
        # has to publish the route.
        "env_token": "CLAUDE_CODE_OAUTH_TOKEN",
    },
    "openai-codex": {
        "label": "ChatGPT / Codex subscription",
        "default_alias": "chatgpt-codex",
    },
}


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def compose(*args: str, tty: bool = False) -> subprocess.CompletedProcess:
    base = ["docker", "compose"]
    if tty:
        # Interactive OAuth needs a terminal; -T would break the device-code prompt.
        return subprocess.run(base + list(args), cwd=COMPOSE_DIR)
    return run(base + list(args), cwd=COMPOSE_DIR)


def live_config_path(profile: str) -> Path:
    """The config Hermes ACTUALLY reads.

    deploy/linux/hermes/config.yaml is a build-time seed; Hermes reads
    $HERMES_HOME, which compose bind-mounts from ./data/hermes/<slug>. Editing
    the seed changes nothing about a running profile.
    """
    return COMPOSE_DIR / "data" / "hermes" / profile / "config.yaml"


def parse_authenticated_providers(auth_list_output: str) -> set[str]:
    """Provider ids that `hermes auth list` reports as holding credentials.

    `auth list` prints one section header per provider that HAS credentials:

        openai-api (1 credentials):
          #1  OPENAI_API_KEY       api_key env:OPENAI_API_KEY <-

    Deriving the answer from `auth status` prose instead is a trap this already
    fell into once: the logged-OUT message is

        openai-codex: logged out (No Codex credentials stored. Run `hermes auth`
        to authenticate.)

    which contains both "credentials" and "authenticate", so keyword matching
    reported a logged-out provider as ready and would have published a route to
    a provider that cannot answer. Match the structural header instead.
    """
    found: set[str] = set()
    for line in (auth_list_output or "").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s+\(\d+\s+credential", line.strip())
        if m:
            found.add(m.group(1))
    return found


def provider_is_authenticated(provider: str) -> bool:
    """True when Hermes reports a usable credential for *provider*.

    Fails closed: any error, or an answer we cannot parse, means "no". Getting
    this wrong in the optimistic direction publishes an unreachable option into
    the picker, which is the exact defect the routing design exists to prevent.
    """
    # An environment token counts as authenticated: the provider resolves its
    # credential from the env chain, so requiring an OAuth entry in the auth
    # store as well would refuse a setup that works.
    env_var = SUBSCRIPTION_PROVIDERS.get(provider, {}).get("env_token")
    if env_var:
        # Markers must not be substrings of one another: "SET" in "UNSET" is
        # True, which reported an unset token as authenticated and would have
        # published a route to a provider with no credential.
        probe = compose("exec", "-T", "hermes", "sh", "-c",
                        f'test -n "${env_var}" && echo token_present || echo token_absent')
        if "token_present" in (probe.stdout or ""):
            return True

    proc = compose("exec", "-T", "hermes", "hermes", "auth", "list")
    if proc.returncode != 0:
        return False
    return provider in parse_authenticated_providers(proc.stdout or "")


def discover_models(provider: str) -> list[str]:
    """Ask the provider what models this credential can actually reach.

    Discovery at attach time rather than a stored catalogue: a list refreshed on
    a timer goes stale between refreshes, and a stale entry here becomes an
    option that cannot answer.
    """
    script = (
        "import json;"
        "from providers.base import get_provider;"
        f"p=get_provider({provider!r});"
        "ms=p.fetch_models() if p and hasattr(p,'fetch_models') else None;"
        "print(json.dumps(ms or []))"
    )
    proc = compose("exec", "-T", "hermes", "python3", "-c", script)
    try:
        models = json.loads((proc.stdout or "").strip().splitlines()[-1])
        return [m for m in models if isinstance(m, str) and m.strip()]
    except Exception:
        return []


ROUTE_BLOCK_RE = re.compile(
    r"^(?P<indent>[ ]+)(?P<alias>[A-Za-z0-9][A-Za-z0-9._-]*):\n"
    r"(?:(?P=indent)[ ]+\S.*\n)+",
    re.M,
)


def upsert_model_route(text: str, alias: str, model: str, provider: str) -> str:
    """Add or replace one alias under platforms.api_server.extra.model_routes.

    Pure string surgery on purpose. Round-tripping this file through a YAML
    library would discard every comment in it, and those comments carry the
    measured reasoning (why threads=12, why q8_0 KV is a loss) that makes the
    deployment maintainable. Losing them to a convenience is a bad trade.

    Raises if the model_routes block is absent, rather than inventing one in the
    wrong place.
    """
    marker = "      model_routes:\n"
    if marker not in text:
        raise ValueError(
            "no 'model_routes:' block found -- expected it under "
            "platforms.api_server.extra in the live profile config"
        )

    entry = (
        f"        {alias}:\n"
        f"          model: {model}\n"
        f"          provider: {provider}\n"
    )

    head, _, tail = text.partition(marker)

    # Replace an existing alias in place so re-running is idempotent and does
    # not accumulate duplicate keys (which PyYAML resolves silently, last-wins).
    existing = re.compile(
        rf"^        {re.escape(alias)}:\n(?:          \S.*\n)+", re.M
    )
    if existing.search(tail):
        return head + marker + existing.sub(entry, tail, count=1)
    return head + marker + entry + tail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("provider", nargs="?", help="anthropic | openai-codex")
    ap.add_argument("--alias", help="name shown in the picker")
    ap.add_argument("--model", help="model id (default: chosen from discovery)")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--list", action="store_true", help="show attachable providers and exit")
    ap.add_argument("--dry-run", action="store_true", help="print the config change, write nothing")
    args = ap.parse_args()

    if args.list or not args.provider:
        print("Attachable subscription providers:\n")
        for pid, meta in SUBSCRIPTION_PROVIDERS.items():
            state = "authenticated" if provider_is_authenticated(pid) else "not authenticated"
            print(f"  {pid:<14} {meta['label']:<30} [{state}]")
        print("\nAttach with:  ./attach-subscription.py <provider>")
        return 0

    provider = args.provider
    if provider not in SUBSCRIPTION_PROVIDERS:
        print(f"error: {provider!r} is not a subscription provider. "
              f"Known: {', '.join(SUBSCRIPTION_PROVIDERS)}", file=sys.stderr)
        return 2

    cfg = live_config_path(args.profile)
    if not cfg.is_file():
        print(f"error: no live config at {cfg} -- is profile {args.profile!r} provisioned?", file=sys.stderr)
        return 2

    # 1. Credential. Hermes owns it; we only trigger and verify the flow.
    # --dry-run writes nothing, so it must not demand a credential first: the
    # main reason to dry-run is to see what attaching WOULD do before doing it.
    if not args.dry_run and not provider_is_authenticated(provider):
        print(f"\n{provider} is not authenticated. Starting its OAuth flow.")
        print("A browser/device-code prompt follows -- this needs a real terminal.\n")
        if not sys.stdin.isatty():
            print("error: not a TTY. Re-run this from your terminal:", file=sys.stderr)
            print(f"  cd {COMPOSE_DIR} && ./attach-subscription.py {provider}", file=sys.stderr)
            return 3
        compose("exec", "hermes", "hermes", "auth", "add", provider, "--type", "oauth", tty=True)
        if not provider_is_authenticated(provider):
            print("error: still not authenticated; not publishing a route.", file=sys.stderr)
            return 4
    if not args.dry_run:
        print(f"{provider}: authenticated")

    # 2. Model. Discovered live, never from a stored catalogue.
    model = args.model
    if not model:
        found = discover_models(provider)
        if not found:
            print(f"error: could not discover any model for {provider}. "
                  f"Re-run with --model <id>.", file=sys.stderr)
            return 5
        model = found[0]
        print(f"{provider}: {len(found)} models reachable; using {model}")
        if len(found) > 1:
            print("  others: " + ", ".join(found[1:8]))

    alias = args.alias or SUBSCRIPTION_PROVIDERS[provider]["default_alias"]

    # 3. Publish the route.
    updated = upsert_model_route(cfg.read_text(), alias, model, provider)
    if args.dry_run:
        print(f"\n--- dry run: would write {cfg} ---")
        print(f"  {alias}: model={model} provider={provider}")
        return 0

    backup = cfg.with_suffix(f".yaml.bak.{int(time.time())}")
    shutil.copy2(cfg, backup)
    cfg.write_text(updated)
    print(f"route '{alias}' written to {cfg} (backup: {backup.name})")

    # 4. Restart and verify it actually became selectable.
    compose("restart", "hermes")
    for _ in range(20):
        time.sleep(3)
        proc = compose("exec", "-T", "hermes", "sh", "-c",
                       'curl -s http://127.0.0.1:8642/v1/models -H "Authorization: Bearer $API_SERVER_KEY"')
        if alias in (proc.stdout or ""):
            print(f"\nverified: '{alias}' is advertised and now appears in the picker.")
            return 0
    print("\nwarning: hermes restarted but the alias is not advertised yet. Check:",
          file=sys.stderr)
    print("  docker compose logs hermes | tail -40", file=sys.stderr)
    return 6


if __name__ == "__main__":
    sys.exit(main())

"""The Hermes version pin must live in the repo, not only in the box's .env.

DEFECT this pins: deploy/linux/hermes/Dockerfile carried the comment "Pin the
release rather than tracking latest" directly above ``ARG HERMES_VERSION=latest``,
and compose defaulted the same variable to ``latest`` (and the gateway's
self-reported pin to ``unpinned``). The only real pin lived in the server's
untracked .env, so a clean checkout built an arbitrary Hermes -- whatever PyPI
served that day -- while every artefact in the tree claimed it was pinned. A
comment is not a pin.

These assertions are about agreement and shape, not a specific number, so a
deliberate version bump only has to touch the three files it should touch.
"""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[3] / "deploy" / "linux"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _dockerfile_pin() -> str:
    text = (DEPLOY / "hermes" / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^ARG HERMES_VERSION=(\S+)\s*$", text, re.MULTILINE)
    assert match, "hermes/Dockerfile no longer declares ARG HERMES_VERSION"
    return match.group(1)


def _compose_defaults() -> list[str]:
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    defaults = re.findall(r"\$\{HERMES_VERSION:-([^}]*)\}", text)
    assert defaults, "compose.yaml no longer references ${HERMES_VERSION:-...}"
    return defaults


def _env_example_pin() -> str:
    text = (DEPLOY / ".env.example").read_text(encoding="utf-8")
    match = re.search(r"^HERMES_VERSION=(\S*)\s*$", text, re.MULTILINE)
    assert match, ".env.example no longer sets HERMES_VERSION"
    return match.group(1)


def test_dockerfile_defaults_to_a_real_version():
    pin = _dockerfile_pin()
    assert pin != "latest", "the build default still tracks latest"
    assert SEMVER.match(pin), f"not a pinned release: {pin!r}"


def test_compose_defaults_to_the_same_version():
    """Covers BOTH ${HERMES_VERSION:-...} sites: the build arg that decides what
    gets installed, and SENTRY_HERMES_PINNED_VERSION, which is what the gateway
    reports and compares against the running agent for drift. A default of
    'unpinned' there disables the drift check silently."""
    pin = _dockerfile_pin()
    for default in _compose_defaults():
        assert default not in ("latest", "unpinned"), (
            f"compose still defaults HERMES_VERSION to {default!r}"
        )
        assert default == pin, (
            f"compose default {default!r} disagrees with the Dockerfile pin {pin!r}"
        )


def test_env_example_matches_the_repo_default():
    """An .env.example that names an older release teaches operators to override
    the repo default with a downgrade."""
    assert _env_example_pin() == _dockerfile_pin()

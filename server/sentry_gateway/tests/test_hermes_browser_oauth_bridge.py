"""Regression contract for the hosted Hermes browser OAuth bridge.

The bridge is copied into the Hermes image rather than imported by the Gateway,
so these focused tests load it with a tiny aiohttp stub and pin the security and
device-code parsing properties that matter to the deployment.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "deploy" / "linux" / "hermes" / "sentry_admin.py"


def _load_bridge():
    previous = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = SimpleNamespace(web=SimpleNamespace(Response=object))
    try:
        spec = importlib.util.spec_from_file_location("_sentry_admin_oauth_test", BRIDGE)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = previous


class _Ready:
    def __init__(self):
        self.was_set = False

    def set(self):
        self.was_set = True


def test_supported_device_providers_are_browser_connectable():
    bridge = _load_bridge()
    assert bridge._DEVICE_OAUTH_PROVIDERS == {
        "nous", "openai-codex", "xai-oauth", "minimax-oauth"
    }
    assert bridge._DEVICE_OAUTH_PROVIDERS < bridge._HTTP_OAUTH_PROVIDERS
    assert "qwen-oauth" in bridge._OAUTH_UNAVAILABLE_REASONS


def test_openai_multiline_device_code_is_extracted_without_cli_output():
    bridge = _load_bridge()
    ready = _Ready()
    flow = {"ready": ready, "status": "starting"}
    bridge._remember_verification(flow, "1. Open this URL in your browser:")
    bridge._remember_verification(flow, "https://auth.openai.com/codex/device")
    bridge._remember_verification(flow, "2. Enter this code:")
    bridge._remember_verification(flow, "ABCD-EFGH")
    assert flow["authorize_url"] == "https://auth.openai.com/codex/device"
    assert flow["user_code"] == "ABCD-EFGH"
    assert flow["status"] == "awaiting_user"
    assert ready.was_set is True


def test_inline_device_code_and_url_are_extracted():
    bridge = _load_bridge()
    ready = _Ready()
    flow = {"ready": ready, "status": "starting"}
    bridge._remember_verification(
        flow, "1. Open: https://accounts.x.ai/oauth2/device?user_code=WXYZ-1234"
    )
    bridge._remember_verification(flow, "2. If prompted, enter code: WXYZ-1234")
    assert flow["user_code"] == "WXYZ-1234"
    assert ready.was_set is True


def test_public_flow_never_exposes_runtime_or_cli_fields():
    bridge = _load_bridge()
    public = bridge._public_oauth_flow(
        "f1",
        {
            "provider": "openai-codex",
            "status": "awaiting_user",
            "authorize_url": "https://auth.openai.com/codex/device",
            "user_code": "ABCD-EFGH",
            "process": "private-process",
            "task": "private-task",
            "verifier": "private-verifier",
            "cli_output": "private-cli-output",
            "access_token": "private-token",
        },
    )
    rendered = repr(public)
    assert "private-" not in rendered
    assert set(public) == {
        "flow_id", "flow_kind", "provider", "status", "authorize_url",
        "user_code", "expires_in", "poll_interval_seconds", "instructions",
        "error", "credential",
    }


def test_bridge_uses_a_fixed_exec_without_a_shell_or_server_instructions():
    source = BRIDGE.read_text(encoding="utf-8")
    assert "asyncio.create_subprocess_exec" in source
    assert "create_subprocess_shell" not in source
    assert "safe_tail" not in source
    assert "cli_command" not in source
    assert "Log it in on the server" not in source

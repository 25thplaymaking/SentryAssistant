"""Regression contract for the hosted Hermes browser OAuth bridge.

The bridge is copied into the Hermes image rather than imported by the Gateway,
so these focused tests load it with a tiny aiohttp stub and pin the security and
device-code parsing properties that matter to the deployment.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import yaml


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
        "error", "credential", "models", "route_error",
    }


def test_subscription_routes_are_persisted_atomically_without_rewriting_config(tmp_path):
    bridge = _load_bridge()
    config = tmp_path / "config.yaml"
    config.write_text(textwrap.dedent("""\
        # keep this operator comment
        platforms:
          api_server:
            extra:
              model_routes:
                deepseek-v4-flash:
                  model: deepseek/deepseek-v4-flash
                  provider: nous
        """), encoding="utf-8")

    routes = bridge._persist_subscription_routes(
        config,
        "openai-codex",
        ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-sol"],
    )

    assert list(routes) == [
        "chatgpt-plan/gpt-5.6-sol",
        "chatgpt-plan/gpt-5.6-terra",
    ]
    first = config.read_text(encoding="utf-8")
    parsed = yaml.safe_load(first)
    persisted = parsed["platforms"]["api_server"]["extra"]["model_routes"]
    assert first.startswith("# keep this operator comment\n")
    assert persisted["deepseek-v4-flash"]["provider"] == "nous"
    assert persisted["chatgpt-plan/gpt-5.6-sol"] == {
        "model": "gpt-5.6-sol",
        "provider": "openai-codex",
    }

    # A refresh replaces only this provider's owned block. It must not append
    # duplicate YAML keys or retain models the account no longer advertises.
    bridge._persist_subscription_routes(config, "openai-codex", ["gpt-5.6-luna"])
    second = config.read_text(encoding="utf-8")
    assert second.count("SENTRY SUBSCRIPTION ROUTES: openai-codex") == 1
    assert "chatgpt-plan/gpt-5.6-sol" not in second
    assert "deepseek-v4-flash" in second


def test_logged_out_subscription_routes_are_dormant_and_restore_without_restart(monkeypatch):
    bridge = _load_bridge()
    adapter = SimpleNamespace(_model_routes={
        "deepseek-v4-flash": {
            "model": "deepseek/deepseek-v4-flash", "provider": "nous"
        },
        "chatgpt-plan/gpt-5.6-sol": {
            "model": "gpt-5.6-sol", "provider": "openai-codex"
        },
    })
    monkeypatch.setattr(
        bridge,
        "_pool_status",
        lambda provider: {"authenticated": provider == "nous", "credentials": []},
    )

    bridge._initialize_subscription_routes(adapter)

    assert "deepseek-v4-flash" in adapter._model_routes
    assert "chatgpt-plan/gpt-5.6-sol" not in adapter._model_routes
    assert "chatgpt-plan/gpt-5.6-sol" in adapter._sentry_subscription_routes["openai-codex"]

    bridge._activate_subscription_routes(adapter, "openai-codex")
    assert "chatgpt-plan/gpt-5.6-sol" in adapter._model_routes
    bridge._deactivate_subscription_routes(adapter, "openai-codex")
    assert "chatgpt-plan/gpt-5.6-sol" not in adapter._model_routes


def test_new_subscription_catalog_is_published_without_restart(tmp_path, monkeypatch):
    bridge = _load_bridge()
    config = tmp_path / "config.yaml"
    config.write_text(textwrap.dedent("""\
        platforms:
          api_server:
            extra:
              model_routes: {}
        """), encoding="utf-8")
    adapter = SimpleNamespace(
        _model_routes={},
        _sentry_subscription_routes={
            provider: {} for provider in bridge._SUBSCRIPTION_PROVIDER_IDS
        },
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        bridge,
        "_discover_subscription_models",
        lambda provider: ["gpt-5.6-sol", "gpt-5.6-terra"],
    )

    result = asyncio.run(
        bridge._ensure_subscription_routes(adapter, "openai-codex", refresh=True)
    )

    assert result == {
        "models": [
            {"id": "chatgpt-plan/gpt-5.6-sol", "model": "gpt-5.6-sol"},
            {"id": "chatgpt-plan/gpt-5.6-terra", "model": "gpt-5.6-terra"},
        ],
        "route_error": None,
    }
    assert set(adapter._model_routes) == {
        "chatgpt-plan/gpt-5.6-sol",
        "chatgpt-plan/gpt-5.6-terra",
    }
    persisted = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert persisted["platforms"]["api_server"]["extra"]["model_routes"] == {
        "chatgpt-plan/gpt-5.6-sol": {
            "model": "gpt-5.6-sol", "provider": "openai-codex"
        },
        "chatgpt-plan/gpt-5.6-terra": {
            "model": "gpt-5.6-terra", "provider": "openai-codex"
        },
    }


def test_provider_model_choices_dedupe_friendly_aliases_by_resolved_model():
    bridge = _load_bridge()
    adapter = SimpleNamespace(_model_routes={
        "deepseek-v4-flash": {
            "model": "deepseek/deepseek-v4-flash", "provider": "nous"
        },
        "deepseek/deepseek-v4-flash": {
            "model": "deepseek/deepseek-v4-flash", "provider": "nous"
        },
        "anthropic/claude-sonnet-4.6": {
            "model": "anthropic/claude-sonnet-4.6", "provider": "nous"
        },
    })

    assert bridge._provider_route_models(adapter, "nous") == [
        {"id": "deepseek-v4-flash", "model": "deepseek/deepseek-v4-flash"},
        {
            "id": "anthropic/claude-sonnet-4.6",
            "model": "anthropic/claude-sonnet-4.6",
        },
    ]


def test_bridge_uses_a_fixed_exec_without_a_shell_or_server_instructions():
    source = BRIDGE.read_text(encoding="utf-8")
    assert "asyncio.create_subprocess_exec" in source
    assert "create_subprocess_shell" not in source
    assert "safe_tail" not in source
    assert "cli_command" not in source
    assert "Log it in on the server" not in source

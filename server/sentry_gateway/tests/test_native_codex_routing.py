from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.agent_runtime.base import (
    RuntimeEvent,
    RuntimeEventType,
    RuntimeExperience,
    RuntimeTurn,
)
from app.agent_runtime.codex_workstation import CodexWorkstationRuntime
from app.agent_runtime.routed import RoutedRuntime


class PrimaryRuntime:
    def __init__(self):
        self.turns = []

    async def available_models(self, _profile_id):
        return (
            "deepseek/deepseek-chat",
            "chatgpt-plan/gpt-5.6-sol",
            "chatgpt-plan/gpt-5.4",
        )

    async def list_auth_providers(self, _profile_id):
        return {
            "data": [
                {
                    "id": "openai-codex",
                    "authenticated": True,
                    "models": [
                        {"id": "chatgpt-plan/gpt-5.6-sol", "model": "gpt-5.6-sol"},
                        {"id": "chatgpt-plan/gpt-5.4", "model": "gpt-5.4"},
                    ],
                }
            ]
        }

    async def send_turn(self, request):
        self.turns.append(request)
        yield RuntimeEvent(
            RuntimeEventType.TURN_COMPLETED,
            request.session_id,
            request.correlation_id,
            datetime.now(timezone.utc),
            "primary",
        )


class NativeRuntime:
    def __init__(self):
        self.turns = []

    @staticmethod
    def owns_model(model):
        return bool(model and model.startswith("chatgpt-plan/"))

    async def filter_models(self, _profile_id, models):
        return tuple(model for model in models if model != "chatgpt-plan/gpt-5.4")

    async def decorate_auth_providers(self, _profile_id, payload):
        payload["data"][0]["native_runtime"] = "codex"
        return payload

    async def status(self, _profile_id):
        return {"id": "codex", "available": True}

    async def send_turn(self, request):
        self.turns.append(request)
        yield RuntimeEvent(
            RuntimeEventType.TURN_COMPLETED,
            request.session_id,
            request.correlation_id,
            datetime.now(timezone.utc),
            "native",
        )


@pytest.mark.asyncio
async def test_routed_runtime_keeps_deepseek_on_hermes_and_codex_native():
    primary = PrimaryRuntime()
    native = NativeRuntime()
    runtime = RoutedRuntime(primary)
    runtime.codex = native
    profile_id = uuid4()

    assert await runtime.available_models(profile_id) == (
        "deepseek/deepseek-chat",
        "chatgpt-plan/gpt-5.6-sol",
    )
    providers = await runtime.list_auth_providers(profile_id)
    assert providers["data"][0]["native_runtime"] == "codex"

    deepseek = RuntimeTurn("s1", profile_id, "hi", "c1", model="deepseek/deepseek-chat")
    codex = RuntimeTurn(
        "s2",
        profile_id,
        "work",
        "c2",
        model="chatgpt-plan/gpt-5.6-sol",
        experience=RuntimeExperience.CHAT,
        workspace_id="server-work",
    )
    assert [event.summary async for event in runtime.send_turn(deepseek)] == ["primary"]
    assert [event.summary async for event in runtime.send_turn(codex)] == ["native"]
    assert primary.turns == [deepseek]
    assert native.turns == [codex]
    assert runtime.experience_for_model(codex.model, codex.experience) is RuntimeExperience.WORK


@pytest.mark.asyncio
async def test_codex_catalog_only_keeps_models_reported_by_live_app_server(monkeypatch):
    runtime = CodexWorkstationRuntime()

    async def status(_profile_id):
        return {
            "available": True,
            "models": ["gpt-5.6-sol", "gpt-5.3-codex-spark"],
            "workspaces": [{"id": "server-work", "modes": ["readOnly", "workspaceWrite"]}],
        }

    monkeypatch.setattr(runtime, "status", status)
    models = await runtime.filter_models(
        uuid4(),
        (
            "deepseek/deepseek-chat",
            "chatgpt-plan/gpt-5.6-sol",
            "chatgpt-plan/gpt-5.4",
        ),
    )
    assert models == (
        "deepseek/deepseek-chat",
        "chatgpt-plan/gpt-5.6-sol",
        "chatgpt-plan/gpt-5.3-codex-spark",
    )


@pytest.mark.asyncio
async def test_native_catalog_replaces_stale_link_snapshot_but_disconnect_stays_hidden(monkeypatch):
    runtime = CodexWorkstationRuntime()

    async def status(_profile_id):
        return {
            "available": True,
            "models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            "workspaces": [{"id": "server-work", "modes": ["workspaceWrite"]}],
        }

    monkeypatch.setattr(runtime, "status", status)
    profile_id = uuid4()
    linked = await runtime.decorate_auth_providers(
        profile_id,
        {
            "data": [
                {
                    "id": "openai-codex",
                    "authenticated": True,
                    "models": [
                        {"id": "chatgpt-plan/gpt-5.6-sol", "model": "gpt-5.6-sol"}
                    ],
                }
            ]
        },
    )
    assert [item["model"] for item in linked["data"][0]["models"]] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]
    assert await runtime.filter_models(profile_id, ("deepseek/deepseek-chat",)) == (
        "deepseek/deepseek-chat",
    )


def test_native_workspace_selection_is_exact_and_never_accepts_a_path_fallback():
    status = {
        "workspaces": [
            {"id": "server-work", "modes": ["workspaceWrite"]},
            {"id": "enfusion", "modes": ["readOnly"]},
        ]
    }
    assert CodexWorkstationRuntime._select_workspace(status, "enfusion")["id"] == "enfusion"
    assert CodexWorkstationRuntime._select_workspace(status, r"C:\\Users\\Bryce") is None
    assert CodexWorkstationRuntime._select_workspace(status, None)["id"] == "server-work"

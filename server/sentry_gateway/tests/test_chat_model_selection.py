"""Selecting a model must actually change where the turn goes -- or be refused.

The picker this supports previously offered models that could not answer: the
Gateway sent `instance.profile_name` as the `model` field no matter what, so
every option routed to whatever the profile's own config.yaml said. These tests
pin the two halves of the fix.

  1. A chosen model reaches the runtime, so the choice has an effect.
  2. An unknown model is REFUSED, not silently downgraded to the default.

(2) is the important one. Falling back would reproduce the original defect in a
subtler form -- the UI would show one model while another answered -- so the
failure has to be loud.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.base import (
    RuntimeEvent,
    RuntimeEventType,
    RuntimeExperience,
    RuntimeSession,
)
from app.agent_runtime.hermes import UnknownProfileError
from app.auth.tokens import Audience, TokenService
from app.routes import chat as chat_routes

KEY = "chat-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

ADVERTISED = ("hermes-agent", "qwen3.6-35b-local", "gpt-5.4")


def _now():
    return datetime.now(timezone.utc)


class FakeRuntime:
    def __init__(self, known_profiles, models=ADVERTISED, providers=None):
        self._known = set(known_profiles)
        self._models = tuple(models)
        self._providers = list(providers or [])
        self.turns: list[tuple[UUID, str, str | None]] = []
        self.experiences: list[RuntimeExperience] = []
        self.models_queried: list[UUID] = []

    async def create_session(self, profile_id, scope):
        if profile_id not in self._known:
            raise UnknownProfileError(str(profile_id))
        return RuntimeSession(
            session_id=f"sess-{profile_id}", profile_id=profile_id,
            scope=scope, created_at=_now(),
        )

    async def available_models(self, profile_id):
        if profile_id not in self._known:
            raise UnknownProfileError(str(profile_id))
        self.models_queried.append(profile_id)
        return self._models

    async def list_auth_providers(self, profile_id):
        if profile_id not in self._known:
            raise UnknownProfileError(str(profile_id))
        return {"object": "list", "data": self._providers}

    async def send_turn(self, request):
        if request.profile_id not in self._known:
            raise UnknownProfileError(str(request.profile_id))
        self.turns.append((request.profile_id, request.prompt, request.model))
        self.experiences.append(request.experience)
        yield RuntimeEvent(
            type=RuntimeEventType.TURN_COMPLETED, session_id=request.session_id,
            correlation_id=request.correlation_id, occurred_at=_now(), summary="done",
        )


class NativeAwareRuntime(FakeRuntime):
    def __init__(self):
        super().__init__(
            [PROFILE_A],
            models=("deepseek/deepseek-chat", "chatgpt-plan/gpt-5.6-sol"),
            providers=[
                {
                    "id": "openai-codex",
                    "name": "OpenAI Codex",
                    "authenticated": True,
                    "native_runtime": "codex",
                    "models": [
                        {"id": "chatgpt-plan/gpt-5.6-sol", "model": "gpt-5.6-sol"}
                    ],
                }
            ],
        )
        self.workspaces = []
        self.native_options = []

    async def native_runtime_status(self, _profile_id):
        return {
            "available": True,
            "node_name": "Bryce-PC",
            "version": "codex-cli 0.144.6",
            "auth_mode": "chatgpt",
            "features": ["threads", "skills", "apps", "mcp", "sandbox", "approvals"],
            "inventory": {
                "skills": [{"name": "ui-review", "description": "Review an interface"}],
                "apps": [{"id": "calendar", "name": "Calendar"}],
                "mcp_servers": [{"name": "browser", "status": "ready"}],
                "plugins": [{"name": "workspace-tools", "enabled": True}],
                "hooks": [{"event": "after_turn", "enabled": True}],
                "models": [
                    {
                        "id": "gpt-5.6-sol",
                        "default_effort": "high",
                        "reasoning_efforts": ["medium", "high", "xhigh"],
                        "supports_personality": True,
                    }
                ],
            },
            "workspaces": [{"id": "server-work", "modes": ["readOnly", "workspaceWrite"]}],
        }

    def experience_for_model(self, model, requested):
        return RuntimeExperience.WORK if str(model or "").startswith("chatgpt-plan/") else requested

    async def send_turn(self, request):
        self.turns.append((request.profile_id, request.prompt, request.model))
        self.experiences.append(request.experience)
        self.workspaces.append(request.workspace_id)
        self.native_options.append(request.native_options)
        yield RuntimeEvent(
            type=RuntimeEventType.TURN_COMPLETED,
            session_id=request.session_id,
            correlation_id=request.correlation_id,
            occurred_at=_now(),
            summary="done",
        )


class FakePool:
    def acquire(self):
        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def execute(self, sql, *args): return None
                return Conn()
            async def __aexit__(self, *_): return False
        return Ctx()


def build_client(runtime) -> TestClient:
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.runtime = runtime
    app.state.pool = FakePool()
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=False)


def bearer(profile_id: UUID) -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(uuid4()), device_id=str(uuid4()),
        profile_id=str(profile_id), audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


class TestModelReachesRuntime:
    def test_absent_model_stays_none(self):
        """No model selected must behave exactly as before this feature."""
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime)
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert runtime.turns == [(PROFILE_A, "hi", None)]
        assert runtime.experiences == [RuntimeExperience.WORK]

    def test_chat_experience_is_forwarded(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime)
        resp = client.post(
            "/api/chat/turn",
            json={"prompt": "hi", "experience": "chat"},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 200
        assert runtime.experiences == [RuntimeExperience.CHAT]

    def test_unknown_experience_is_rejected_before_a_turn_runs(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime)
        resp = client.post(
            "/api/chat/turn",
            json={"prompt": "hi", "experience": "unsafe"},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 422
        assert runtime.turns == []

    def test_chosen_model_is_forwarded(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime)
        resp = client.post(
            "/api/chat/turn",
            json={"prompt": "hi", "model": "gpt-5.4"},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 200
        assert runtime.turns == [(PROFILE_A, "hi", "gpt-5.4")]

    def test_local_model_is_forwarded(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime)
        client.post(
            "/api/chat/turn",
            json={"prompt": "hi", "model": "qwen3.6-35b-local"},
            headers=bearer(PROFILE_A),
        )
        assert runtime.turns[0][2] == "qwen3.6-35b-local"


class TestUnknownModelIsRefused:
    def test_unknown_model_is_400_not_a_silent_default(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime)
        resp = client.post(
            "/api/chat/turn",
            json={"prompt": "hi", "model": "claude-opus-5"},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 400
        # and critically: the turn never ran against the wrong model
        assert runtime.turns == []

    def test_refusal_names_the_model(self):
        client = build_client(FakeRuntime([PROFILE_A]))
        resp = client.post(
            "/api/chat/turn",
            json={"prompt": "hi", "model": "not-wired"},
            headers=bearer(PROFILE_A),
        )
        assert "not-wired" in resp.json()["detail"]


class TestAdvertisedList:
    def test_models_endpoint_lists_what_hermes_advertises(self):
        client = build_client(FakeRuntime([PROFILE_A]))
        resp = client.get("/api/chat/models", headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert resp.json()["models"] == list(ADVERTISED)

    def test_models_endpoint_requires_auth(self):
        client = build_client(FakeRuntime([PROFILE_A]))
        assert client.get("/api/chat/models").status_code == 401

    def test_native_codex_models_publish_work_metadata_and_live_capabilities(self):
        client = build_client(NativeAwareRuntime())
        resp = client.get("/api/chat/models", headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        payload = resp.json()
        codex_group = next(group for group in payload["groups"] if group["provider_id"] == "openai-codex")
        assert codex_group["native_runtime"] == "codex"
        assert codex_group["models"] == [
            {
                "id": "chatgpt-plan/gpt-5.6-sol",
                "label": "gpt-5.6-sol",
                "source_model": "gpt-5.6-sol",
                "native_runtime": "codex",
                "experience": "work",
            }
        ]
        assert payload["native_runtimes"][0]["available"] is True
        assert payload["native_runtimes"][0]["workspaces"][0]["id"] == "server-work"
        assert payload["native_runtimes"][0]["inventory"]["skills"][0]["name"] == "ui-review"
        assert payload["native_runtimes"][0]["inventory"]["models"][0]["default_effort"] == "high"

    def test_codex_model_forces_work_and_forwards_named_workspace(self):
        runtime = NativeAwareRuntime()
        client = build_client(runtime)
        resp = client.post(
            "/api/chat/turn",
            json={
                "prompt": "fix it",
                "model": "chatgpt-plan/gpt-5.6-sol",
                "experience": "chat",
                "workspace_id": "server-work",
            },
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 200
        assert runtime.experiences == [RuntimeExperience.WORK]
        assert runtime.workspaces == ["server-work"]

    def test_codex_controls_are_validated_and_forwarded_to_the_native_runtime(self):
        runtime = NativeAwareRuntime()
        client = build_client(runtime)
        response = client.post(
            "/api/chat/turn",
            json={
                "prompt": "review it",
                "model": "chatgpt-plan/gpt-5.6-sol",
                "workspace_id": "server-work",
                "native_options": {
                    "action": "review",
                    "collaboration_mode": "plan",
                    "effort": "xhigh",
                    "personality": "friendly",
                    "approval_policy": "untrusted",
                    "sandbox": "readOnly",
                    "review_target": "uncommittedChanges",
                },
            },
            headers=bearer(PROFILE_A),
        )

        assert response.status_code == 200
        assert runtime.native_options == [
            {
                "action": "review",
                "collaboration_mode": "plan",
                "effort": "xhigh",
                "personality": "friendly",
                "approval_policy": "untrusted",
                "sandbox": "readOnly",
                "review_target": "uncommittedChanges",
            }
        ]

    def test_native_controls_are_refused_for_a_non_codex_model(self):
        runtime = NativeAwareRuntime()
        response = build_client(runtime).post(
            "/api/chat/turn",
            json={
                "prompt": "chat",
                "model": "deepseek/deepseek-chat",
                "experience": "chat",
                "native_options": {"collaboration_mode": "plan"},
            },
            headers=bearer(PROFILE_A),
        )

        assert response.status_code == 400
        assert runtime.turns == []

    def test_empty_registry_lists_nothing_rather_than_a_fallback(self):
        """No routes must mean no options -- never a catalogue of unreachables."""
        client = build_client(FakeRuntime([PROFILE_A], models=()))
        resp = client.get("/api/chat/models", headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_groups_only_models_from_authenticated_linked_providers(self):
        providers = [
            {
                "id": "nous",
                "name": "Nous Research",
                "authenticated": True,
                "models": [
                    {"id": "qwen3.6-35b-local", "model": "deepseek/deepseek-v4-flash"},
                ],
            },
            {
                "id": "xai-oauth",
                "name": "xAI",
                "authenticated": False,
                "models": [{"id": "gpt-5.4", "model": "x-ai/grok-4"}],
            },
        ]
        client = build_client(
            FakeRuntime(
                [PROFILE_A],
                models=ADVERTISED + ("deepseek-v4-flash",),
                providers=providers,
            )
        )
        payload = client.get("/api/chat/models", headers=bearer(PROFILE_A)).json()

        assert payload["groups"][0] == {
            "provider": "Nous Research",
            "provider_id": "nous",
            "models": [
                {
                    "id": "qwen3.6-35b-local",
                    "label": "deepseek/deepseek-v4-flash",
                    "source_model": "deepseek/deepseek-v4-flash",
                }
            ],
        }
        assert all(group["provider_id"] != "xai-oauth" for group in payload["groups"])
        visible_ids = {
            model["id"]
            for group in payload["groups"]
            for model in group["models"]
        }
        assert "gpt-5.4" not in visible_ids
        assert payload["groups"][1]["provider"] == "Sentry routes"
        assert payload["groups"][1]["models"] == [
            {"id": "deepseek-v4-flash", "label": "deepseek-v4-flash"}
        ]
        assert payload["experiences"]["default"] == "chat"

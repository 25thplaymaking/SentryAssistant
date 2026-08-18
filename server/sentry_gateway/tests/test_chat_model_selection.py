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

from app.agent_runtime.base import RuntimeEvent, RuntimeEventType, RuntimeSession
from app.agent_runtime.hermes import UnknownProfileError
from app.auth.tokens import Audience, TokenService
from app.routes import chat as chat_routes

KEY = "chat-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

ADVERTISED = ("hermes-agent", "qwen3.6-35b-local", "gpt-5.4")


def _now():
    return datetime.now(timezone.utc)


class FakeRuntime:
    def __init__(self, known_profiles, models=ADVERTISED):
        self._known = set(known_profiles)
        self._models = tuple(models)
        self.turns: list[tuple[UUID, str, str | None]] = []
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

    async def send_turn(self, request):
        if request.profile_id not in self._known:
            raise UnknownProfileError(str(request.profile_id))
        self.turns.append((request.profile_id, request.prompt, request.model))
        yield RuntimeEvent(
            type=RuntimeEventType.TURN_COMPLETED, session_id=request.session_id,
            correlation_id=request.correlation_id, occurred_at=_now(), summary="done",
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

    def test_empty_registry_lists_nothing_rather_than_a_fallback(self):
        """No routes must mean no options -- never a catalogue of unreachables."""
        client = build_client(FakeRuntime([PROFILE_A], models=()))
        resp = client.get("/api/chat/models", headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert resp.json()["models"] == []

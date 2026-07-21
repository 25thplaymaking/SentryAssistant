"""The client chat route resolves the caller's own profile and fails closed.

The isolation invariant that matters: a turn is routed to the caller's profile
and no other, and a caller whose profile has no registered runtime never reaches
any agent.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.base import (
    RuntimeEvent,
    RuntimeEventType,
    RuntimeSession,
)
from app.agent_runtime.hermes import UnknownProfileError
from app.auth.tokens import Audience, TokenService
from app.routes import chat as chat_routes

KEY = "chat-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
UNPROVISIONED = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _now():
    return datetime.now(timezone.utc)


class FakeRuntime:
    """Records which profile each turn was routed to; fails closed on unknowns."""

    def __init__(self, known_profiles):
        self._known = set(known_profiles)
        self.turns: list[tuple[UUID, str]] = []
        self.sessions_created: list[UUID] = []

    async def create_session(self, profile_id, scope):
        if profile_id not in self._known:
            raise UnknownProfileError(str(profile_id))
        self.sessions_created.append(profile_id)
        return RuntimeSession(
            session_id=f"sess-{profile_id}",
            profile_id=profile_id,
            scope=scope,
            created_at=_now(),
        )

    async def send_turn(self, request):
        if request.profile_id not in self._known:
            raise UnknownProfileError(str(request.profile_id))
        self.turns.append((request.profile_id, request.prompt))
        yield RuntimeEvent(
            type=RuntimeEventType.MESSAGE,
            session_id=request.session_id,
            correlation_id=request.correlation_id,
            occurred_at=_now(),
            summary="hello",
        )
        yield RuntimeEvent(
            type=RuntimeEventType.TURN_COMPLETED,
            session_id=request.session_id,
            correlation_id=request.correlation_id,
            occurred_at=_now(),
            summary="done",
        )


class FakePool:
    """Captures audit executes so a turn's audit row can be asserted."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def execute(self, sql, *args):
                        pool.executed.append((sql, args))

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


def build_client(runtime, pool=None) -> TestClient:
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.runtime = runtime
    app.state.pool = pool
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=False)


def bearer(profile_id: UUID) -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(uuid4()),
        device_id=str(uuid4()),
        profile_id=str(profile_id),
        audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


class TestAuth:
    def test_unauthenticated_is_refused(self):
        client = build_client(FakeRuntime([PROFILE_A]))
        resp = client.post("/api/chat/turn", json={"prompt": "hi"})
        assert resp.status_code == 401

    def test_empty_prompt_is_rejected(self):
        client = build_client(FakeRuntime([PROFILE_A]))
        resp = client.post("/api/chat/turn", json={"prompt": ""}, headers=bearer(PROFILE_A))
        assert resp.status_code == 422


class TestRouting:
    def test_turn_reaches_the_callers_own_agent(self):
        runtime = FakeRuntime([PROFILE_A, PROFILE_B])
        client = build_client(runtime)
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert runtime.turns == [(PROFILE_A, "hi")]

    def test_two_profiles_route_to_their_own_agents(self):
        runtime = FakeRuntime([PROFILE_A, PROFILE_B])
        client = build_client(runtime)
        client.post("/api/chat/turn", json={"prompt": "a"}, headers=bearer(PROFILE_A))
        client.post("/api/chat/turn", json={"prompt": "b"}, headers=bearer(PROFILE_B))
        assert (PROFILE_A, "a") in runtime.turns
        assert (PROFILE_B, "b") in runtime.turns

    def test_streams_events_then_done(self):
        client = build_client(FakeRuntime([PROFILE_A]))
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "hello" in resp.text
        assert "[DONE]" in resp.text
        assert resp.headers.get("x-sentry-correlation-id")


class TestFailClosed:
    def test_unprovisioned_profile_never_reaches_an_agent(self):
        runtime = FakeRuntime([PROFILE_A])  # UNPROVISIONED is not registered
        client = build_client(runtime)
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(UNPROVISIONED))
        assert resp.status_code == 503
        assert runtime.turns == []
        assert runtime.sessions_created == []


class TestAudit:
    def test_turn_records_an_audit_event(self):
        pool = FakePool()
        client = build_client(FakeRuntime([PROFILE_A]), pool=pool)
        client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A))
        assert any("audit_events" in sql for sql, _ in pool.executed)

    def test_failed_route_records_no_turn_audit(self):
        pool = FakePool()
        client = build_client(FakeRuntime([PROFILE_A]), pool=pool)
        client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(UNPROVISIONED))
        assert not any("audit_events" in sql for sql, _ in pool.executed)

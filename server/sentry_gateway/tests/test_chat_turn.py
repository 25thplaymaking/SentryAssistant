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
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _now():
    return datetime.now(timezone.utc)


class FakeRuntime:
    """Records which profile each turn was routed to; fails closed on unknowns."""

    def __init__(self, known_profiles):
        self._known = set(known_profiles)
        self.turns: list[tuple[UUID, str]] = []
        self.requests = []
        self.sessions_created: list[UUID] = []

    def register(self, instance):
        """Mirrors HermesRuntime.register — a profile becomes routable."""
        self._known.add(instance.profile_id)

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
        self.requests.append(request)
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


class TargetPool(FakePool):
    """Adds the owner-scoped workspace lookup used by explicit Sentry targets."""

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def execute(self, sql, *args):
                        pool.executed.append((sql, args))

                    async def fetchrow(self, sql, *args):
                        if "FROM execution_nodes" in sql:
                            return {
                                "id": UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                                "name": "Bryce's PC",
                                "workspace_id": "server-work",
                            }
                        return None

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
        client = build_client(runtime, pool=FakePool())
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert runtime.turns == [(PROFILE_A, "hi")]

    def test_workspace_target_is_resolved_and_forwarded_to_hermes(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime, pool=TargetPool())
        response = client.post(
            "/api/chat/turn",
            json={
                "prompt": "Inspect the selected service workspace",
                "target": {
                    "kind": "workspace",
                    "node_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    "workspace_id": "server-work",
                },
            },
            headers=bearer(PROFILE_A),
        )
        assert response.status_code == 200
        assert runtime.requests[0].target_context == {
            "kind": "workspace",
            "node_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "node_name": "Bryce's PC",
            "workspace_id": "server-work",
        }

    def test_two_profiles_route_to_their_own_agents(self):
        runtime = FakeRuntime([PROFILE_A, PROFILE_B])
        client = build_client(runtime, pool=FakePool())
        client.post("/api/chat/turn", json={"prompt": "a"}, headers=bearer(PROFILE_A))
        client.post("/api/chat/turn", json={"prompt": "b"}, headers=bearer(PROFILE_B))
        assert (PROFILE_A, "a") in runtime.turns
        assert (PROFILE_B, "b") in runtime.turns

    def test_streams_events_then_done(self):
        client = build_client(FakeRuntime([PROFILE_A]), pool=FakePool())
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "hello" in resp.text
        assert "[DONE]" in resp.text
        assert resp.headers.get("x-sentry-correlation-id")

    def test_valid_pasted_image_reaches_the_selected_runtime(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime, pool=FakePool())

        resp = client.post(
            "/api/chat/turn",
            json={"prompt": "What is in this image?", "images": [{"data_url": PNG_DATA_URL}]},
            headers=bearer(PROFILE_A),
        )

        assert resp.status_code == 200
        assert len(runtime.requests) == 1
        assert runtime.requests[0].images[0].data_url == PNG_DATA_URL

    def test_non_image_data_url_is_refused_before_runtime_execution(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime, pool=FakePool())

        resp = client.post(
            "/api/chat/turn",
            json={
                "prompt": "Read this",
                "images": [{"data_url": "data:text/plain;base64,aGVsbG8="}],
            },
            headers=bearer(PROFILE_A),
        )

        assert resp.status_code == 422
        assert runtime.requests == []


class TestFailClosed:
    def test_unprovisioned_profile_never_reaches_an_agent(self):
        runtime = FakeRuntime([PROFILE_A])  # UNPROVISIONED is not registered
        client = build_client(runtime, pool=FakePool())
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(UNPROVISIONED))
        assert resp.status_code == 503
        assert runtime.turns == []
        assert runtime.sessions_created == []


class RaisingPool:
    """A pool whose connection.execute always fails (e.g. DB down / FK violation)."""

    def acquire(self):
        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def execute(self, *_a):
                        raise RuntimeError("audit write failed")

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


class TestAuditFailClosed:
    def test_audit_failure_refuses_the_turn_cleanly(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime, pool=RaisingPool())
        resp = client.post("/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A))
        # A clean 503, not a raw 500 stack trace, and the agent is never reached.
        assert resp.status_code == 503
        assert runtime.turns == []


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


class TestLateProvisioning:
    """A teammate provisioned after the Gateway booted must not need a restart.

    Endpoints were loaded only at startup, so provisioning someone and then
    chatting as them returned 503 until the Gateway was hard-restarted -- and
    `docker compose up -d` no-ops when nothing changed, so that restart was
    easy to miss (it bit the first live cutover).
    """

    def test_endpoint_registered_after_startup_is_picked_up(self, monkeypatch):
        runtime = FakeRuntime([PROFILE_A])  # UNPROVISIONED unknown at boot
        client = build_client(runtime, FakePool())
        client.app.state.endpoint_cipher = object()

        async def _fake_refresh(rt, pool, cipher, profile_id, **kw):
            rt._known.add(profile_id)  # the row exists now
            return True

        monkeypatch.setattr(chat_routes, "refresh_profile_endpoint", _fake_refresh)

        resp = client.post(
            "/api/chat/turn", json={"prompt": "hi"}, headers=bearer(UNPROVISIONED)
        )

        assert resp.status_code == 200
        assert runtime.sessions_created == [UNPROVISIONED]

    def test_still_fails_closed_when_there_is_no_endpoint_row(self, monkeypatch):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime, FakePool())
        client.app.state.endpoint_cipher = object()

        async def _fake_refresh(rt, pool, cipher, profile_id, **kw):
            return False  # genuinely unprovisioned

        monkeypatch.setattr(chat_routes, "refresh_profile_endpoint", _fake_refresh)

        resp = client.post(
            "/api/chat/turn", json={"prompt": "hi"}, headers=bearer(UNPROVISIONED)
        )

        assert resp.status_code == 503
        assert runtime.turns == []
        assert runtime.sessions_created == []


class TestAuditIsNotOptional:
    """A turn that cannot be audited must be refused, including when there is no DB.

    chat.py refuses a turn whose audit INSERT fails, but `_audit_service`
    returns None when app.state.pool is None -- so with the database down the
    audit block was skipped entirely and the turn ran UNRECORDED. Token
    verification needs no DB, so this state is reachable and chat-able.
    """

    def test_turn_is_refused_when_there_is_no_database_to_audit_into(self):
        runtime = FakeRuntime([PROFILE_A])
        client = build_client(runtime, pool=None)  # DB unavailable

        resp = client.post(
            "/api/chat/turn", json={"prompt": "hi"}, headers=bearer(PROFILE_A)
        )

        assert resp.status_code == 503
        assert runtime.turns == [], "a turn must never run unrecorded"

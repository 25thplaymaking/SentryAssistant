"""Gated inter-agent messaging: allow-list, fail-closed, redaction, own inbox."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import agent_messages as am_routes

KEY = "agentmsg-test-signing-key-padded-well-past-the-32-byte-minimum"
SENDER = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
RECIPIENT = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class FakePool:
    def __init__(self, granted=True, inbox_rows=None, messages=None):
        self.granted = granted
        self.msg_id = uuid4()
        self.inbox_rows = inbox_rows or []
        self.inserts = []
        #: id -> {"recipient_profile_id", "read_at"}; backs the mark-read route.
        self.messages = messages or {}

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetchval(self, query, *a):
                        if "agent_message_grants" in query:
                            return 1 if pool.granted else None
                        if "INSERT INTO agent_messages" in query:
                            pool.inserts.append(a)  # (sender, recipient, redacted, correlation)
                            return pool.msg_id
                        if "UPDATE agent_messages" in query:
                            msg = pool.messages.get(a[0])
                            if (
                                msg is not None
                                and msg["recipient_profile_id"] == a[1]
                                and msg["read_at"] is None
                            ):
                                msg["read_at"] = datetime.now(timezone.utc)
                                return a[0]
                            return None
                        if "SELECT 1 FROM agent_messages" in query:
                            msg = pool.messages.get(a[0])
                            if msg is not None and msg["recipient_profile_id"] == a[1]:
                                return 1
                            return None
                        return None

                    async def fetch(self, query, *a):
                        return pool.inbox_rows

                    async def execute(self, *a):
                        return None

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


def build(pool) -> TestClient:
    app = FastAPI()
    app.include_router(am_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = pool
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=False)


def bearer(profile_id: UUID) -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(uuid4()), device_id=str(uuid4()),
        profile_id=str(profile_id), audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


def test_send_with_grant_delivers():
    pool = FakePool(granted=True)
    resp = build(pool).post(
        "/api/agent-messages",
        json={"recipient_profile_id": str(RECIPIENT), "body": "hello"},
        headers=bearer(SENDER),
    )
    assert resp.status_code == 200
    assert resp.json()["delivered"] is True
    # stored body is the (unredacted-for-plain-text) message
    assert pool.inserts and pool.inserts[0][2] == "hello"


def test_send_without_grant_fails_closed():
    pool = FakePool(granted=False)
    resp = build(pool).post(
        "/api/agent-messages",
        json={"recipient_profile_id": str(RECIPIENT), "body": "hello"},
        headers=bearer(SENDER),
    )
    assert resp.status_code == 403
    assert pool.inserts == []  # nothing crossed


def test_secret_body_is_redacted_at_boundary():
    pool = FakePool(granted=True)
    build(pool).post(
        "/api/agent-messages",
        json={"recipient_profile_id": str(RECIPIENT), "body": "my password is hunter2"},
        headers=bearer(SENDER),
    )
    assert pool.inserts
    assert "redacted" in pool.inserts[0][2].lower()
    assert "hunter2" not in pool.inserts[0][2]


def test_cannot_message_own_profile():
    resp = build(FakePool()).post(
        "/api/agent-messages",
        json={"recipient_profile_id": str(SENDER), "body": "hi"},
        headers=bearer(SENDER),
    )
    assert resp.status_code == 400


def test_inbox_returns_own_messages():
    now = datetime.now(timezone.utc)
    rows = [{"id": uuid4(), "sender_profile_id": RECIPIENT, "body_redacted": "hey", "created_at": now, "read_at": None}]
    resp = build(FakePool(inbox_rows=rows)).get("/api/agent-messages", headers=bearer(SENDER))
    assert resp.status_code == 200
    assert resp.json()[0]["body"] == "hey"
    assert resp.json()[0]["read"] is False


def test_unauthenticated_is_refused():
    assert build(FakePool()).get("/api/agent-messages").status_code == 401


class TestMarkRead:
    def test_own_unread_message_is_marked(self):
        msg_id = uuid4()
        pool = FakePool(messages={msg_id: {"recipient_profile_id": SENDER, "read_at": None}})
        resp = build(pool).post(f"/api/agent-messages/{msg_id}/read", headers=bearer(SENDER))
        assert resp.status_code == 200
        assert resp.json() == {"read": True}
        assert pool.messages[msg_id]["read_at"] is not None

    def test_already_read_is_idempotent_200(self):
        msg_id = uuid4()
        read_at = datetime.now(timezone.utc)
        pool = FakePool(messages={msg_id: {"recipient_profile_id": SENDER, "read_at": read_at}})
        resp = build(pool).post(f"/api/agent-messages/{msg_id}/read", headers=bearer(SENDER))
        assert resp.status_code == 200
        assert resp.json() == {"read": True}
        assert pool.messages[msg_id]["read_at"] == read_at  # not re-stamped

    def test_another_recipients_message_is_404_not_403(self):
        msg_id = uuid4()
        pool = FakePool(messages={msg_id: {"recipient_profile_id": RECIPIENT, "read_at": None}})
        resp = build(pool).post(f"/api/agent-messages/{msg_id}/read", headers=bearer(SENDER))
        assert resp.status_code == 404
        assert pool.messages[msg_id]["read_at"] is None  # untouched

    def test_missing_message_is_404(self):
        resp = build(FakePool()).post(
            f"/api/agent-messages/{uuid4()}/read", headers=bearer(SENDER)
        )
        assert resp.status_code == 404

    def test_unauthenticated_is_refused(self):
        assert build(FakePool()).post(f"/api/agent-messages/{uuid4()}/read").status_code == 401

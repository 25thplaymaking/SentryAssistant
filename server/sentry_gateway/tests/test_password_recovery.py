"""Server Control-backed Sentry password recovery."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import passwords
from app.auth.tokens import Audience, TokenError, TokenService, hash_secret
from app.routes import auth as auth_routes


KEY = "password-recovery-signing-key-padded-past-the-32-byte-minimum"
RECOVERY_KEY = "server-control-recovery-key-padded-past-thirty-two-bytes"
USER = UUID("11111111-1111-1111-1111-111111111111")
PROFILE = UUID("22222222-2222-2222-2222-222222222222")
DEVICE = UUID("33333333-3333-3333-3333-333333333333")
OLD_PASSWORD = "the-old-account-passphrase"
NEW_PASSWORD = "the-new-account-passphrase"


class _NullTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Settings:
    def __init__(self, key=RECOVERY_KEY):
        self.key = key

    def load_recovery_key(self):
        return self.key


class FakeDB:
    def __init__(self, *, account=True, recovery=True, devices=None):
        self.account = account
        self.recovery = recovery
        self.devices = list(devices if devices is not None else [DEVICE])
        self.executed: list[tuple[str, tuple]] = []
        self.audit: list[tuple] = []

    def acquire(self):
        db = self

        class Ctx:
            async def __aenter__(self):
                return FakeConn(db)

            async def __aexit__(self, *_):
                return False

        return Ctx()


class FakeConn:
    def __init__(self, db):
        self.db = db

    def transaction(self):
        return _NullTx()

    async def fetchrow(self, query, *args):
        if "FROM password_recovery_codes" in query:
            if not self.db.recovery:
                return None
            return {
                "user_id": USER,
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                "consumed_at": None,
                "disabled_at": None,
                "password_hash": passwords.hash_password(OLD_PASSWORD),
            }
        if "FROM user_passwords" in query:
            return {"user_id": USER} if self.db.account else None
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetch(self, query, *args):
        if "SELECT id FROM devices" in query:
            return [{"id": item} for item in self.db.devices]
        raise AssertionError(f"unexpected fetch: {query}")

    async def execute(self, query, *args):
        self.db.executed.append((query, args))
        if "audit_events" in query:
            self.db.audit.append(args)


def build(db, *, recovery_key=RECOVERY_KEY):
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = db
    app.state.settings = _Settings(recovery_key)
    return app, TestClient(app, raise_server_exceptions=False)


def start(client, *, token=RECOVERY_KEY, username="bryce"):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(
        "/api/auth/password/recovery/start",
        json={"username": username},
        headers=headers,
    )


def recover(client, *, code="AAAA-BBBB-CCCC", new_password=NEW_PASSWORD):
    return client.post(
        "/api/auth/password/recover",
        json={"username": "bryce", "code": code, "new_password": new_password},
    )


@pytest.fixture(autouse=True)
def _clear_throttle():
    passwords.RECOVERY_THROTTLE.reset()
    yield
    passwords.RECOVERY_THROTTLE.reset()


class TestRecoveryStart:
    def test_requires_the_dedicated_operator_credential(self):
        _, client = build(FakeDB())
        assert start(client, token=None).status_code == 401
        assert start(client, token="wrong-key-that-is-long-enough-to-try").status_code == 401

    def test_disabled_integration_is_hidden(self):
        _, client = build(FakeDB(), recovery_key="")
        assert start(client).status_code == 404

    def test_returns_a_ten_minute_single_use_code(self):
        db = FakeDB()
        _, client = build(db)
        response = start(client)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["code"].split("-")) == 3
        assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc)
        inserts = [(q, a) for q, a in db.executed if "INSERT INTO password_recovery_codes" in q]
        assert len(inserts) == 1
        assert inserts[0][1][0] == hash_secret(body["code"])
        assert body["code"] not in str(db.executed)

    def test_unknown_account_is_not_issued_a_code(self):
        _, client = build(FakeDB(account=False))
        assert start(client).status_code == 404


class TestRecoveryComplete:
    def test_rewrites_the_credential_and_revokes_old_devices(self):
        db = FakeDB()
        app, client = build(db)
        old_access = app.state.tokens.issue_access_token(
            user_id=str(USER),
            device_id=str(DEVICE),
            profile_id=str(PROFILE),
            audience=Audience.CLIENT,
        )

        response = recover(client)
        assert response.status_code == 204, response.text
        assert any("UPDATE user_passwords" in query for query, _ in db.executed)
        assert any("UPDATE refresh_tokens" in query for query, _ in db.executed)
        with pytest.raises(TokenError, match="revoked"):
            app.state.tokens.verify(old_access, audience=Audience.CLIENT)

    def test_invalid_code_is_generic_and_does_not_change_the_hash(self):
        db = FakeDB(recovery=False)
        _, client = build(db)
        response = recover(client)
        assert response.status_code == 400
        assert "invalid or has expired" in response.json()["detail"]
        assert not any("UPDATE user_passwords" in query for query, _ in db.executed)

    def test_rejects_a_short_new_password(self):
        _, client = build(FakeDB())
        response = recover(client, new_password="short")
        assert response.status_code == 400
        assert "12" in response.json()["detail"]

    def test_rejects_reusing_the_previous_password(self):
        _, client = build(FakeDB())
        assert recover(client, new_password=OLD_PASSWORD).status_code == 400

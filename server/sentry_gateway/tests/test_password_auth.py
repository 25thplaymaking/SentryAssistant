"""Username + password sign-in.

The point of this route is that onboarding a teammate becomes "here is a URL, a
username and a password" instead of a five-minute enrollment code they have to
redeem before it goes stale. The hard requirement is that it is not a second,
weaker identity: it must mint exactly the token pair the enrollment path mints,
bound to the same personal profile, so routing, audit, refresh and revocation
downstream are untouched.

These use the real dependency wiring with a stub database, like
test_api_auth.py, so they prove behaviour without a live PostgreSQL.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import passwords
from app.auth.tokens import Audience, TokenService
from app.routes import auth as auth_routes

KEY = "password-login-test-signing-key-padded-past-the-32-byte-minimum"

USER = UUID("11111111-1111-1111-1111-111111111111")
PROFILE = UUID("22222222-2222-2222-2222-222222222222")
NEW_DEVICE = UUID("33333333-3333-3333-3333-333333333333")
OLD_DEVICE = UUID("44444444-4444-4444-4444-444444444444")

GOOD = "a-real-teammate-passphrase"
GOOD_HASH = passwords.hash_password(GOOD)


class _NullTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeDB:
    """Routes queries by a distinctive fragment; records every write."""

    def __init__(self, credential=None, profile_id=PROFILE, existing_device=None):
        self.credential = credential
        self.profile_id = profile_id
        self.existing_device = existing_device
        self.executed: list[tuple[str, tuple]] = []
        self.audit: list[tuple] = []

    # -- pool interface -------------------------------------------------
    def acquire(self):
        db = self

        class Ctx:
            async def __aenter__(self):
                return FakeConn(db)

            async def __aexit__(self, *_):
                return False

        return Ctx()


class FakeConn:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def transaction(self):
        return _NullTx()

    async def fetchrow(self, q, *a):
        if "user_passwords" in q:
            return self.db.credential
        raise AssertionError(f"unexpected fetchrow: {q}")

    async def fetchval(self, q, *a):
        if "INSERT INTO devices" in q:
            return NEW_DEVICE
        if "FROM devices" in q:
            return self.db.existing_device
        if "FROM profiles" in q:
            return self.db.profile_id
        raise AssertionError(f"unexpected fetchval: {q}")

    async def execute(self, q, *a):
        self.db.executed.append((q, a))
        if "audit_events" in q:
            self.db.audit.append(a)


# Column order of AuditService._write's INSERT.
A_ACTOR_USER, A_ACTOR_DEVICE, A_PROFILE = 0, 1, 2
A_ACTION, A_DECISION, A_DETAIL = 4, 7, 10


def credential(**over):
    row = {
        "user_id": USER,
        "username": "alice",
        "password_hash": GOOD_HASH,
        "must_change": False,
        "failed_attempts": 0,
        "locked_until": None,
        "disabled_at": None,
    }
    row.update(over)
    return row


def build(db, *, pool=True) -> TestClient:
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = db if pool else None
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=False)


def login(db, *, username="alice", password=GOOD, device_name="Sentry Web", pool=True):
    return build(db, pool=pool).post(
        "/api/auth/password/login",
        json={"username": username, "password": password, "device_name": device_name},
    )


def writes(db, fragment):
    return [(q, a) for q, a in db.executed if fragment in q]


def audit_of(db, action, decision=None):
    return [
        a
        for a in db.audit
        if a[A_ACTION] == action and (decision is None or a[A_DECISION] == decision)
    ]


@pytest.fixture(autouse=True)
def _clear_throttle():
    passwords.LOGIN_THROTTLE.reset()
    yield
    passwords.LOGIN_THROTTLE.reset()


class TestSuccessfulLogin:
    def test_mints_the_same_token_pair_shape_as_enrollment(self):
        db = FakeDB(credential=credential())
        resp = login(db)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) >= {
            "access_token",
            "refresh_token",
            "device_id",
            "profile_id",
        }
        assert body["profile_id"] == str(PROFILE)

    def test_access_token_is_bound_to_the_personal_profile(self):
        """Everything downstream routes on `pid`; a wrong one is a data leak."""
        db = FakeDB(credential=credential())
        claims = TokenService(KEY).verify(
            login(db).json()["access_token"], audience=Audience.CLIENT
        )
        assert claims["sub"] == str(USER)
        assert claims["pid"] == str(PROFILE)
        assert claims["did"] == str(NEW_DEVICE)

    def test_refresh_token_is_persisted_so_rotation_works(self):
        db = FakeDB(credential=credential())
        login(db)
        assert writes(db, "INSERT INTO refresh_tokens")

    def test_reports_must_change_when_the_operator_set_the_password(self):
        db = FakeDB(credential=credential(must_change=True))
        assert login(db).json()["must_change"] is True

    def test_reports_must_change_false_for_a_self_chosen_password(self):
        db = FakeDB(credential=credential(must_change=False))
        assert login(db).json()["must_change"] is False

    def test_clears_the_failure_counter(self):
        db = FakeDB(credential=credential(failed_attempts=3))
        login(db)
        assert writes(db, "UPDATE user_passwords")

    def test_is_audited_against_the_user_device_and_profile(self):
        db = FakeDB(credential=credential())
        login(db)
        rows = audit_of(db, "auth.password.login", "allowed")
        assert len(rows) == 1
        assert rows[0][A_ACTOR_USER] == USER
        assert rows[0][A_ACTOR_DEVICE] == NEW_DEVICE
        assert rows[0][A_PROFILE] == PROFILE

    def test_audit_detail_survives_redaction(self):
        """audit.redact() blanks any detail mentioning a credential, so the
        detail here must not use those words or the row says nothing."""
        db = FakeDB(credential=credential())
        login(db)
        detail = audit_of(db, "auth.password.login", "allowed")[0][A_DETAIL]
        assert detail and "redacted" not in detail

    def test_plaintext_never_reaches_an_audit_row(self):
        db = FakeDB(credential=credential())
        login(db)
        assert not any(GOOD in str(value) for row in db.audit for value in row)


class TestDeviceBinding:
    def test_reuses_a_matching_device_instead_of_one_row_per_login(self):
        db = FakeDB(credential=credential(), existing_device=OLD_DEVICE)
        claims = TokenService(KEY).verify(
            login(db).json()["access_token"], audience=Audience.CLIENT
        )
        assert claims["did"] == str(OLD_DEVICE)
        assert not writes(db, "INSERT INTO devices")

    def test_creates_a_device_when_none_matches(self):
        db = FakeDB(credential=credential(), existing_device=None)
        assert login(db).json()["device_id"] == str(NEW_DEVICE)


class TestRefusal:
    def test_wrong_password_is_401(self):
        db = FakeDB(credential=credential())
        assert login(db, password="wrong-but-long-enough").status_code == 401

    def test_unknown_username_is_indistinguishable_from_a_wrong_password(self):
        known = FakeDB(credential=credential())
        unknown = FakeDB(credential=None)
        a = login(known, password="wrong-but-long-enough")
        b = login(unknown, username="nobody")
        assert a.status_code == b.status_code == 401
        assert a.json()["detail"] == b.json()["detail"]

    def test_wrong_password_is_audited_against_the_real_user(self):
        db = FakeDB(credential=credential())
        login(db, password="wrong-but-long-enough")
        rows = audit_of(db, "auth.password.login", "denied")
        assert len(rows) == 1
        assert rows[0][A_ACTOR_USER] == USER

    def test_unknown_username_is_audited_without_an_actor(self):
        """actor_user_id has an FK, so an unknown name must record NULL."""
        db = FakeDB(credential=None)
        login(db, username="nobody")
        rows = audit_of(db, "auth.password.login", "denied")
        assert len(rows) == 1
        assert rows[0][A_ACTOR_USER] is None

    def test_failure_audit_detail_survives_redaction(self):
        db = FakeDB(credential=credential())
        login(db, password="wrong-but-long-enough")
        detail = audit_of(db, "auth.password.login", "denied")[0][A_DETAIL]
        assert detail and "redacted" not in detail

    def test_disabled_user_is_refused_with_the_right_password(self):
        db = FakeDB(
            credential=credential(disabled_at=datetime.now(timezone.utc))
        )
        resp = login(db)
        assert resp.status_code == 401
        assert audit_of(db, "auth.password.login", "denied")

    def test_no_personal_profile_is_a_conflict_not_a_broken_session(self):
        db = FakeDB(credential=credential(), profile_id=None)
        assert login(db).status_code == 409

    def test_no_database_is_503(self):
        assert login(FakeDB(credential=credential()), pool=False).status_code == 503


class TestLockout:
    def test_a_failure_increments_the_stored_counter(self):
        db = FakeDB(credential=credential(failed_attempts=1))
        login(db, password="wrong-but-long-enough")
        updates = writes(db, "UPDATE user_passwords")
        assert updates, "a failed attempt must be recorded in the database"
        assert any(2 in a for _, a in updates)

    def test_the_final_failure_sets_a_lock_expiry(self):
        db = FakeDB(
            credential=credential(failed_attempts=passwords.MAX_FAILED_ATTEMPTS - 1)
        )
        login(db, password="wrong-but-long-enough")
        updates = writes(db, "UPDATE user_passwords")
        assert any(
            isinstance(v, datetime) for _, args in updates for v in args
        ), "the lock expiry must be persisted, not held in process memory"

    def test_a_locked_account_is_refused_even_with_the_right_password(self):
        db = FakeDB(
            credential=credential(
                locked_until=datetime.now(timezone.utc) + timedelta(minutes=5)
            )
        )
        resp = login(db)
        assert resp.status_code == 401
        assert audit_of(db, "auth.password.login", "denied")

    def test_a_lock_that_has_expired_no_longer_blocks(self):
        db = FakeDB(
            credential=credential(
                locked_until=datetime.now(timezone.utc) - timedelta(minutes=1)
            )
        )
        assert login(db).status_code == 200

    def test_a_locked_account_gives_the_same_answer_as_a_wrong_password(self):
        locked = FakeDB(
            credential=credential(
                locked_until=datetime.now(timezone.utc) + timedelta(minutes=5)
            )
        )
        wrong = FakeDB(credential=credential())
        a = login(locked)
        b = login(wrong, password="wrong-but-long-enough")
        assert a.json()["detail"] == b.json()["detail"]


class TestThrottle:
    def test_refuses_a_flood_before_doing_the_hashing_work(self):
        db = FakeDB(credential=credential())
        client = build(db)
        payload = {"username": "alice", "password": "wrong-but-long-enough"}
        codes = [
            client.post("/api/auth/password/login", json=payload).status_code
            for _ in range(passwords.LOGIN_THROTTLE.limit + 2)
        ]
        assert 429 in codes


class TestChangePassword:
    def bearer(self, user_id=USER, profile_id=PROFILE):
        token = TokenService(KEY).issue_access_token(
            user_id=str(user_id),
            device_id=str(NEW_DEVICE),
            profile_id=str(profile_id),
            audience=Audience.CLIENT,
        )
        return {"Authorization": f"Bearer {token}"}

    def change(self, db, current=GOOD, new="a-brand-new-passphrase", pool=True):
        return build(db, pool=pool).post(
            "/api/auth/password/change",
            json={"current_password": current, "new_password": new},
            headers=self.bearer(),
        )

    def test_unauthenticated_is_refused(self):
        db = FakeDB(credential=credential())
        resp = build(db).post(
            "/api/auth/password/change",
            json={"current_password": GOOD, "new_password": "a-brand-new-passphrase"},
        )
        assert resp.status_code == 401

    def test_correct_current_password_rewrites_the_hash(self):
        db = FakeDB(credential=credential(must_change=True))
        assert self.change(db).status_code in (200, 204)
        updates = writes(db, "UPDATE user_passwords")
        assert updates
        # The new hash is stored, and must_change is cleared with it.
        assert any(
            isinstance(v, str) and v.startswith("$argon2id$")
            for _, args in updates
            for v in args
        )
        assert any(False in args for _, args in updates), "must_change must be cleared"

    def test_wrong_current_password_is_refused(self):
        db = FakeDB(credential=credential())
        assert self.change(db, current="not-the-current-one").status_code == 403
        assert not writes(db, "UPDATE user_passwords")

    def test_short_new_password_is_refused_with_the_minimum_named(self):
        db = FakeDB(credential=credential())
        resp = self.change(db, new="short")
        assert resp.status_code == 400
        assert "12" in resp.json()["detail"]

    def test_reusing_the_current_password_is_refused(self):
        db = FakeDB(credential=credential())
        assert self.change(db, new=GOOD).status_code == 400

    def test_account_without_a_credential_is_a_conflict(self):
        db = FakeDB(credential=None)
        assert self.change(db).status_code == 409

    def test_success_is_audited(self):
        db = FakeDB(credential=credential())
        self.change(db)
        assert audit_of(db, "auth.password.change", "allowed")

    def test_failure_is_audited(self):
        db = FakeDB(credential=credential())
        self.change(db, current="not-the-current-one")
        assert audit_of(db, "auth.password.change", "denied")

    def test_no_database_is_503(self):
        assert self.change(FakeDB(credential=credential()), pool=False).status_code == 503


class TestEnrollmentStillWorks:
    def test_the_enrollment_routes_are_still_mounted(self):
        """Codes remain the device-pairing path; adding passwords must not
        remove the door the whole team currently uses."""
        paths = {getattr(r, "path", None) for r in auth_routes.router.routes}
        assert "/api/auth/enroll/complete" in paths
        assert "/api/auth/enroll/start" in paths
        assert "/api/auth/refresh" in paths


def _unused():
    return uuid4()

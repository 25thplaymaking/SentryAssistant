"""GET/POST /api/cron is caller-scoped."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import cron as cron_routes

KEY = "cron-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakePool:
    def __init__(self, fetch_rows=None, fetchrow_row=None):
        self._fetch_rows = fetch_rows or []
        self._fetchrow_row = fetchrow_row
        self.calls = []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetch(self, q, *a):
                        pool.calls.append(("fetch", a))
                        return pool._fetch_rows

                    async def fetchrow(self, q, *a):
                        pool.calls.append(("fetchrow", a))
                        return pool._fetchrow_row

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


def build(pool) -> TestClient:
    app = FastAPI()
    app.include_router(cron_routes.router)
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


def _job_row(name="brief"):
    return {
        "id": uuid4(), "name": name, "schedule": "0 8 * * *",
        "prompt": "morning brief", "enabled": True,
        "created_at": datetime.now(timezone.utc),
        "last_run_at": None, "last_status": None, "last_summary": None,
    }


def test_list_is_scoped_to_caller():
    pool = FakePool(fetch_rows=[_job_row()])
    resp = build(pool).get("/api/cron", headers=bearer(PROFILE_A))
    assert resp.status_code == 200
    assert resp.json()[0]["name"] == "brief"
    assert any(op == "fetch" and args[0] == PROFILE_A for op, args in pool.calls)


def test_create_returns_job():
    pool = FakePool(fetchrow_row=_job_row("nightly"))
    resp = build(pool).post(
        "/api/cron",
        json={"name": "nightly", "schedule": "0 2 * * *", "prompt": "backup"},
        headers=bearer(PROFILE_A),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "nightly"


def test_unauthenticated_is_refused():
    assert build(FakePool()).get("/api/cron").status_code == 401

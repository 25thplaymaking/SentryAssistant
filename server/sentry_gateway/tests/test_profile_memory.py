"""GET/PUT /api/memory is caller-scoped; write upserts; section names validated."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import memory as memory_routes

KEY = "memory-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakePool:
    def __init__(self, fetch_rows=None, fetchrow_row=None):
        self._fetch_rows = fetch_rows or []
        self._fetchrow_row = fetchrow_row
        self.executed = []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetch(self, *a):
                        pool.executed.append(("fetch", a))
                        return pool._fetch_rows

                    async def fetchrow(self, *a):
                        pool.executed.append(("fetchrow", a))
                        return pool._fetchrow_row

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


def build(pool) -> TestClient:
    app = FastAPI()
    app.include_router(memory_routes.router)
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


def test_list_returns_caller_sections():
    now = datetime.now(timezone.utc)
    pool = FakePool(fetch_rows=[{"section": "notes", "content": "hi", "updated_at": now}])
    resp = build(pool).get("/api/memory", headers=bearer(PROFILE_A))
    assert resp.status_code == 200
    assert resp.json()[0]["section"] == "notes"
    # the query was scoped by profile_id
    assert any(op == "fetch" and args[1] == PROFILE_A for op, args in pool.executed)


def test_write_upserts_and_returns_section():
    now = datetime.now(timezone.utc)
    pool = FakePool(fetchrow_row={"section": "notes", "content": "new", "updated_at": now})
    resp = build(pool).put("/api/memory/notes", json={"content": "new"}, headers=bearer(PROFILE_A))
    assert resp.status_code == 200
    assert resp.json()["content"] == "new"


def test_invalid_section_name_is_400():
    resp = build(FakePool()).put("/api/memory/bad*name", json={"content": "x"}, headers=bearer(PROFILE_A))
    assert resp.status_code == 400


def test_unauthenticated_is_refused():
    assert build(FakePool()).get("/api/memory").status_code == 401

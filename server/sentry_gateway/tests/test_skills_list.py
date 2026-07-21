"""GET /api/skills lists the caller's own skills with governance state."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import skills as skills_routes

KEY = "skills-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakePool:
    def __init__(self, rows):
        self._rows = rows

    def acquire(self):
        rows = self._rows

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetch(self, *_a):
                        return rows

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


def build(rows, *, pool=True) -> TestClient:
    app = FastAPI()
    app.include_router(skills_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = FakePool(rows) if pool else None
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=False)


def bearer(profile_id: UUID) -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(uuid4()), device_id=str(uuid4()),
        profile_id=str(profile_id), audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


def _rows():
    now = datetime.now(timezone.utc)
    return [
        {"id": uuid4(), "name": "summarize", "state": "active", "content_hash": "h1", "created_at": now},
        {"id": uuid4(), "name": "draft-email", "state": "awaitingApproval", "content_hash": "h2", "created_at": now},
    ]


def test_lists_skills_with_state():
    resp = build(_rows()).get("/api/skills", headers=bearer(PROFILE_A))
    assert resp.status_code == 200
    data = resp.json()
    by_name = {d["name"]: d["state"] for d in data}
    assert by_name["summarize"] == "active"
    assert by_name["draft-email"] == "awaitingApproval"


def test_unauthenticated_is_refused():
    assert build(_rows()).get("/api/skills").status_code == 401


def test_no_database_is_503():
    assert build(_rows(), pool=False).get("/api/skills", headers=bearer(PROFILE_A)).status_code == 503

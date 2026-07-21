"""GET /api/profiles/me is scoped to the caller and flags the active profile."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import profiles as profiles_routes

KEY = "profiles-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


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
    app.include_router(profiles_routes.router)
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
        {"id": PROFILE_A, "kind": "personal", "display_name": "Me", "created_at": now},
        {"id": PROFILE_B, "kind": "team", "display_name": "Team", "created_at": now},
    ]


def test_lists_owned_profiles_and_flags_active():
    client = build(_rows())
    resp = client.get("/api/profiles/me", headers=bearer(PROFILE_A))
    assert resp.status_code == 200
    data = resp.json()
    assert {d["display_name"] for d in data} == {"Me", "Team"}
    active = [d for d in data if d["is_active"]]
    assert len(active) == 1 and active[0]["id"] == str(PROFILE_A)


def test_unauthenticated_is_refused():
    assert build(_rows()).get("/api/profiles/me").status_code == 401


def test_no_database_is_503():
    resp = build(_rows(), pool=False).get("/api/profiles/me", headers=bearer(PROFILE_A))
    assert resp.status_code == 503

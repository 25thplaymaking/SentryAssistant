"""GET /api/kanban/board returns the caller's own board and fails closed."""

from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.hermes import UnknownProfileError
from app.auth.tokens import Audience, TokenService
from app.routes import kanban as kanban_routes

KEY = "kanban-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
UNPROVISIONED = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


class FakeRuntime:
    def __init__(self, known, board):
        self._known = set(known)
        self._board = board
        self.asked_for = []

    async def read_work_board(self, profile_id):
        self.asked_for.append(profile_id)
        if profile_id not in self._known:
            raise UnknownProfileError(str(profile_id))
        return self._board


def build(runtime) -> TestClient:
    app = FastAPI()
    app.include_router(kanban_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.runtime = runtime
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=False)


def bearer(profile_id: UUID) -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(uuid4()), device_id=str(uuid4()),
        profile_id=str(profile_id), audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


def test_returns_callers_own_board():
    runtime = FakeRuntime([PROFILE_A], {"tasks": [{"id": "t1", "title": "do"}]})
    resp = build(runtime).get("/api/kanban/board", headers=bearer(PROFILE_A))
    assert resp.status_code == 200
    assert resp.json()["tasks"][0]["title"] == "do"
    assert runtime.asked_for == [PROFILE_A]


def test_unauthenticated_is_refused():
    assert build(FakeRuntime([PROFILE_A], {})).get("/api/kanban/board").status_code == 401


def test_unprovisioned_profile_fails_closed():
    runtime = FakeRuntime([PROFILE_A], {})
    resp = build(runtime).get("/api/kanban/board", headers=bearer(UNPROVISIONED))
    assert resp.status_code == 503

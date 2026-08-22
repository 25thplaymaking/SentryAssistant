"""The Work board is a profile-scoped projection of authoritative work orders."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import kanban as kanban_routes

KEY = "kanban-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
WORK_ORDER_A = UUID("11111111-1111-1111-1111-111111111111")


def _row(profile_id=PROFILE_A, work_order_id=WORK_ORDER_A):
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    return {
        "id": work_order_id,
        "profile_id": profile_id,
        "title": "Inspect the release",
        "prompt": "Review the staged release changes.",
        "state": "draft",
        "mode": "readOnly",
        "harness": None,
        "workspace_id": None,
        "assigned_user_id": None,
        "execution_node_id": None,
        "completion_criteria": ["Report findings"],
        "correlation_id": "corr-a",
        "created_at": now,
        "updated_at": now,
    }


class FakeConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.board_profile_ids = []

    async def fetch(self, query, *args):
        if "FROM work_orders" in query:
            profile_id = args[0]
            self.board_profile_ids.append(profile_id)
            return [row for row in self.rows if row["profile_id"] == profile_id]
        return []

    async def fetchrow(self, query, work_order_id, profile_id):
        return next(
            (
                row
                for row in self.rows
                if row["id"] == work_order_id and row["profile_id"] == profile_id
            ),
            None,
        )


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class FakePool:
    def __init__(self, rows=()):
        self.connection = FakeConnection(rows)

    def acquire(self):
        return _Acquire(self.connection)


def build(pool) -> TestClient:
    app = FastAPI()
    app.include_router(kanban_routes.router)
    app.state.tokens = TokenService(KEY)
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


def test_board_returns_only_the_callers_work_orders():
    pool = FakePool([_row(), _row(PROFILE_B, uuid4())])
    response = build(pool).get("/api/kanban/board", headers=bearer(PROFILE_A))

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "sentry-workorders"
    assert [task["id"] for task in payload["tasks"]] == [str(WORK_ORDER_A)]
    assert payload["columns"][0]["name"] == "Draft"
    assert pool.connection.board_profile_ids == [PROFILE_A]


def test_profile_with_no_work_orders_gets_an_empty_real_board():
    response = build(FakePool([_row()])).get(
        "/api/kanban/board", headers=bearer(PROFILE_B)
    )

    assert response.status_code == 200
    assert response.json()["tasks"] == []


def test_unauthenticated_is_refused():
    assert build(FakePool()).get("/api/kanban/board").status_code == 401


def test_create_delegates_to_the_authoritative_work_order_route(monkeypatch):
    captured = {}

    class View:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {"id": str(WORK_ORDER_A), "state": "draft"}

    async def fake_create(body, request, caller):
        captured.update(body=body, profile_id=caller.profile_id, request=request)
        return View()

    monkeypatch.setattr(kanban_routes, "create_work_order", fake_create)
    response = build(FakePool()).post(
        "/api/kanban/tasks",
        headers=bearer(PROFILE_A),
        json={"title": "Inspect the release", "body": "Check it", "mode": "readOnly"},
    )

    assert response.status_code == 201
    assert response.json()["task"]["id"] == str(WORK_ORDER_A)
    assert captured["profile_id"] == PROFILE_A
    assert captured["body"].prompt == "Check it"


def test_transition_uses_the_audited_state_machine_then_returns_detail(monkeypatch):
    captured = {}

    async def fake_transition(work_order_id, body, request, caller):
        captured.update(
            work_order_id=work_order_id,
            target=body.target.value,
            profile_id=caller.profile_id,
        )

    monkeypatch.setattr(kanban_routes, "transition_work_order", fake_transition)
    response = build(FakePool([_row()])).post(
        f"/api/kanban/tasks/{WORK_ORDER_A}/transition",
        headers=bearer(PROFILE_A),
        json={"target": "submitted", "reason": "Ready"},
    )

    assert response.status_code == 200
    assert response.json()["task"]["id"] == str(WORK_ORDER_A)
    assert captured == {
        "work_order_id": WORK_ORDER_A,
        "target": "submitted",
        "profile_id": PROFILE_A,
    }

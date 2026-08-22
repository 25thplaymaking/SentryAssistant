"""Hermes can dispatch only to the owner's advertised workstation capabilities."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import workstation as workstation_routes

PROFILE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OWNER = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
NODE = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
ORDER = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
KEY = "internal-hermes-test-key"


class FakePool:
    def __init__(self, *, status_rows=None, target=True, result_row=None):
        self.status_rows = status_rows or []
        self.target = target
        self.result_row = result_row
        self.calls = []

    def acquire(self):
        pool = self

        class Context:
            async def __aenter__(self):
                class Connection:
                    def transaction(self):
                        class Transaction:
                            async def __aenter__(self):
                                return self

                            async def __aexit__(self, *_):
                                return False

                        return Transaction()

                    async def fetch(self, query, *args):
                        pool.calls.append(("fetch", query, args))
                        return pool.status_rows

                    async def fetchrow(self, query, *args):
                        pool.calls.append(("fetchrow", query, args))
                        if "FROM work_orders w" in query:
                            return pool.result_row
                        if "JOIN node_workspaces" in query:
                            return (
                                {
                                    "owner_user_id": OWNER,
                                    "node_id": NODE,
                                    "node_name": "Bryce's PC",
                                }
                                if pool.target
                                else None
                            )
                        return None

                    async def fetchval(self, query, *args):
                        pool.calls.append(("fetchval", query, args))
                        if "INSERT INTO work_orders" in query:
                            return ORDER
                        return None

                    async def execute(self, query, *args):
                        pool.calls.append(("execute", query, args))
                        return "INSERT 0 1"

                return Connection()

            async def __aexit__(self, *_):
                return False

        return Context()


def build(pool: FakePool) -> TestClient:
    app = FastAPI()
    app.include_router(workstation_routes.router)
    app.state.pool = pool
    app.state.settings = SimpleNamespace(
        hermes_api_key=KEY,
        hermes_bootstrap_profile_id=str(PROFILE),
    )
    return TestClient(app, raise_server_exceptions=False)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {KEY}"}


def test_runtime_key_is_required_before_database_access():
    pool = FakePool()
    response = build(pool).get("/api/runtime/workstation/status")
    assert response.status_code == 401
    assert pool.calls == []


def test_status_returns_names_and_capabilities_without_local_paths():
    pool = FakePool(
        status_rows=[
            {
                "id": NODE,
                "name": "Bryce's PC",
                "last_seen_at": datetime.now(timezone.utc),
                "revoked_at": None,
                "workspace_id": "server-work",
                "allowed_harnesses": ["shell", "claude"],
                "allowed_modes": ["readOnly", "workspaceWrite"],
                # A malicious/faulty fake row cannot make this field leak because
                # the route never reads or serializes it.
                "root_path": r"C:\Users\Bryce\Documents\ServerWork",
            }
        ]
    )
    response = build(pool).get("/api/runtime/workstation/status", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["nodes"][0]["online"] is True
    assert body["nodes"][0]["workspaces"][0]["workspace_id"] == "server-work"
    assert "C:\\Users" not in response.text
    assert "root_path" not in response.text


def test_dispatch_refuses_an_unavailable_or_offline_capability():
    pool = FakePool(target=False)
    response = build(pool).post(
        "/api/runtime/workstation/work",
        headers=auth(),
        json={
            "title": "Inspect repository",
            "instruction": "git status --short",
            "workspace_id": "server-work",
            "harness": "shell",
            "mode": "readOnly",
        },
    )
    assert response.status_code == 409
    assert not any("INSERT INTO work_orders" in query for _, query, _ in pool.calls)


def test_dispatch_creates_one_durable_assigned_work_order_and_audit_event():
    pool = FakePool(target=True)
    response = build(pool).post(
        "/api/runtime/workstation/work",
        headers=auth(),
        json={
            "title": "Inspect repository",
            "instruction": "git status --short",
            "workspace_id": "server-work",
            "harness": "shell",
            "mode": "readOnly",
        },
    )
    assert response.status_code == 202
    assert response.json()["work_order_id"] == str(ORDER)
    queries = "\n".join(query for _, query, _ in pool.calls)
    assert "INSERT INTO work_orders" in queries
    assert "INSERT INTO work_order_runs" in queries
    assert "INSERT INTO work_order_transitions" in queries
    assert "INSERT INTO audit_events" in queries


def test_result_is_profile_scoped_and_returns_durable_summary():
    now = datetime.now(timezone.utc)
    pool = FakePool(
        result_row={
            "id": ORDER,
            "title": "Inspect repository",
            "state": "readyForReview",
            "workspace_id": "server-work",
            "harness": "shell",
            "mode": "readOnly",
            "outcome": "succeeded",
            "status_boundary": "implemented",
            "summary": "git status --short completed successfully.\n\nclean",
            "finished_at": now,
        }
    )
    response = build(pool).get(
        f"/api/runtime/workstation/work/{ORDER}", headers=auth()
    )
    assert response.status_code == 200
    assert response.json()["terminal"] is True
    assert response.json()["summary"].endswith("clean")
    result_call = next(call for call in pool.calls if call[0] == "fetchrow")
    assert result_call[2] == (ORDER, PROFILE)

"""Linked integrations stay owner-scoped and dispatch through signed work orders."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import integrations as integration_routes


KEY = "integration-test-signing-key-padded-past-32-bytes"
PROFILE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
DEVICE = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
NODE = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


class FakePool:
    def __init__(self, *, rows=None, target=True):
        self.rows = rows or []
        self.target = target
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
                        return pool.rows

                    async def fetchrow(self, query, *args):
                        pool.calls.append(("fetchrow", query, args))
                        if "JOIN node_workspaces" in query:
                            return {"node_id": NODE, "node_name": "Bryce's PC"} if pool.target else None
                        return None

                    async def execute(self, query, *args):
                        pool.calls.append(("execute", query, args))
                        return "INSERT 0 1"

                return Connection()

            async def __aexit__(self, *_):
                return False

        return Context()


def bearer():
    token = TokenService(KEY).issue_access_token(
        user_id=str(USER),
        device_id=str(DEVICE),
        profile_id=str(PROFILE),
        audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


def build(pool):
    app = FastAPI()
    app.include_router(integration_routes.router)
    app.state.pool = pool
    app.state.tokens = TokenService(KEY)
    return TestClient(app, raise_server_exceptions=False)


async def no_services():
    return {"available": True, "services": []}


def test_status_reports_public_capabilities_without_raw_paths(monkeypatch):
    monkeypatch.setattr(integration_routes, "service_targets", no_services)
    pool = FakePool(
        rows=[
            {
                "id": NODE,
                "name": "Bryce's PC",
                "last_seen_at": datetime.now(timezone.utc),
                "native_runtimes": {
                    "integrations": {
                        "available": True,
                        "inventory": {"providers": [{"id": "codex", "available": True}]},
                    }
                },
                "revoked_at": None,
                "workspace_id": "server-work",
                "allowed_harnesses": ["integrations"],
                "allowed_modes": ["readOnly"],
                "root_path": r"C:\Users\Bryce\Documents\ServerWork",
            }
        ]
    )
    response = build(pool).get("/api/integrations/status", headers=bearer())
    assert response.status_code == 200
    assert response.json()["nodes"][0]["workspaces"][0]["id"] == "server-work"
    assert response.json()["nodes"][0]["integration"]["inventory"]["providers"][0]["id"] == "codex"
    assert "C:\\Users" not in response.text
    assert "root_path" not in response.text


def test_action_dispatches_one_read_only_integration_work_order():
    pool = FakePool(target=True)
    response = build(pool).post(
        "/api/integrations/actions",
        headers=bearer(),
        json={
            "action": "workspaceInspect",
            "node_id": str(NODE),
            "workspace_id": "server-work",
        },
    )
    assert response.status_code == 202
    assert response.json()["action"] == "workspaceInspect"
    queries = "\n".join(query for _, query, _ in pool.calls)
    assert "'integrations'" in queries
    assert "'readOnly'" in queries
    assert "AND n.id = $3" in queries
    assert "INSERT INTO work_orders" in queries
    assert "INSERT INTO audit_events" in queries
    target_call = next(
        args
        for kind, query, args in pool.calls
        if kind == "fetchrow" and "JOIN node_workspaces" in query
    )
    assert target_call == (USER, "server-work", NODE)


def test_action_refuses_unknown_provider_session_before_database_access():
    pool = FakePool()
    response = build(pool).post(
        "/api/integrations/actions",
        headers=bearer(),
        json={
            "action": "sessionRead",
            "node_id": str(NODE),
            "workspace_id": "server-work",
            "provider": "all",
            "provider_session_id": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert pool.calls == []


def test_action_result_reads_the_production_event_timestamp_column():
    now = datetime.now(timezone.utc)

    class ResultPool(FakePool):
        def acquire(self):
            pool = self

            class Context:
                async def __aenter__(self):
                    class Connection:
                        async def fetchrow(self, query, *args):
                            pool.calls.append(("fetchrow", query, args))
                            return {
                                "id": args[0],
                                "state": "succeeded",
                                "workspace_id": "server-work",
                                "title": "Inspect workspace",
                                "outcome": "succeeded",
                                "summary": "Complete",
                                "finished_at": now,
                            }

                        async def fetch(self, query, *args):
                            pool.calls.append(("fetch", query, args))
                            return [{
                                "event_index": 1,
                                "event_type": "integration_result",
                                "summary": "Complete",
                                "payload": {},
                                "created_at": now,
                            }]

                    return Connection()

                async def __aexit__(self, *_):
                    return False

            return Context()

    pool = ResultPool()
    response = build(pool).get(
        f"/api/integrations/actions/{uuid4()}",
        headers=bearer(),
    )
    assert response.status_code == 200
    assert response.json()["events"][0]["created_at"] == now.isoformat()
    event_query = next(query for kind, query, _ in pool.calls if kind == "fetch")
    assert "occurred_at AS created_at" in event_query

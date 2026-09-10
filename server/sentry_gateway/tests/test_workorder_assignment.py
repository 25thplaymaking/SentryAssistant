from uuid import UUID, uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import workorders as workorders_routes
from app.workorders.transitions import WorkOrderMode, WorkOrderState

KEY = 'workorder-assign-test-key-padded-past-32-bytes'
USER = uuid4()
DEVICE = uuid4()
PROFILE = uuid4()
NODE = uuid4()


class FakeAssignPool:
    def __init__(self, order_row=None, node_row=None, ws_row=None, list_rows=None):
        self.order_row = order_row
        self.node_row = node_row
        self.ws_row = ws_row
        self.list_rows = list_rows or []
        self.transitions = []
        self.audits = []
        self.updated_row = None

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetchrow(self, query, *a):
                        if 'FROM work_orders w' in query:
                            if 'FOR UPDATE' in query or 'w.id = ' in query:
                                return pool.order_row
                        if 'FROM execution_nodes' in query:
                            return pool.node_row
                        if 'FROM node_workspaces' in query:
                            return pool.ws_row
                        if 'UPDATE work_orders' in query:
                            pool.updated_row = {
                                'id': a[0],
                                'title': 'Test Order',
                                'state': a[5],
                                'mode': 'workspaceWrite',
                                'correlation_id': 'test-corr',
                                'execution_node_id': a[1],
                                'workspace_id': a[2],
                                'harness': a[3],
                                'assigned_user_id': a[4],
                            }
                            return pool.updated_row
                        return None

                    async def fetch(self, query, *a):
                        if 'FROM work_orders w' in query:
                            return pool.list_rows
                        return []

                    async def fetchval(self, query, *a):
                        return None

                    async def execute(self, query, *a):
                        if 'INSERT INTO work_order_transitions' in query:
                            pool.transitions.append(a)
                        elif 'INSERT INTO audit_events' in query:
                            pool.audits.append(a)
                        return None

                    def transaction(self):
                        class Tx:
                            async def __aenter__(self):
                                return None

                            async def __aexit__(self, *args):
                                return False

                        return Tx()

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


def build_client(pool) -> TestClient:
    app = FastAPI()
    app.include_router(workorders_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = pool
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=True)


def auth_headers() -> dict[str, str]:
    tokens = TokenService(KEY)
    token = tokens.issue_access_token(
        user_id=str(USER), device_id=str(DEVICE), profile_id=str(PROFILE), audience=Audience.CLIENT
    )
    return {'Authorization': f'Bearer {token}'}


class TestWorkOrderAssignment:
    def test_assign_work_order_success(self):
        wo_id = uuid4()
        pool = FakeAssignPool(
            order_row={
                'id': wo_id,
                'title': 'Build feature',
                'state': 'draft',
                'mode': 'workspaceWrite',
                'correlation_id': 'corr-1',
                'requested_by': USER,
                'profile_id': PROFILE,
                'team_id': None,
                'execution_node_id': None,
                'workspace_id': None,
                'harness': None,
                'assigned_user_id': None,
                'completion_criteria': [],
                'node_owner_id': None,
            },
            node_row={'id': NODE, 'owner_user_id': USER},
            ws_row={
                'allowed_harnesses': ['codex', 'shell'],
                'allowed_modes': ['readOnly', 'workspaceWrite'],
            },
        )
        client = build_client(pool)
        resp = client.post(
            f'/api/workorders/{wo_id}/assign',
            json={
                'execution_node_id': str(NODE),
                'workspace_id': 'my-project',
                'harness': 'codex',
            },
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['id'] == str(wo_id)
        assert data['state'] == 'assigned'
        assert data['workspace_id'] == 'my-project'
        assert data['harness'] == 'codex'
        assert len(pool.transitions) == 1

    def test_assign_work_order_disallowed_harness(self):
        wo_id = uuid4()
        pool = FakeAssignPool(
            order_row={
                'id': wo_id,
                'title': 'Build feature',
                'state': 'draft',
                'mode': 'workspaceWrite',
                'correlation_id': 'corr-1',
                'requested_by': USER,
                'profile_id': PROFILE,
                'team_id': None,
                'completion_criteria': [],
                'node_owner_id': None,
            },
            node_row={'id': NODE, 'owner_user_id': USER},
            ws_row={
                'allowed_harnesses': ['codex'],
                'allowed_modes': ['readOnly', 'workspaceWrite'],
            },
        )
        client = build_client(pool)
        resp = client.post(
            f'/api/workorders/{wo_id}/assign',
            json={
                'execution_node_id': str(NODE),
                'workspace_id': 'my-project',
                'harness': 'forbidden_harness',
            },
            headers=auth_headers(),
        )
        assert resp.status_code == 400
        assert 'not permitted' in resp.json()['detail']

    def test_assign_terminal_order_rejected(self):
        wo_id = uuid4()
        pool = FakeAssignPool(
            order_row={
                'id': wo_id,
                'title': 'Build feature',
                'state': 'closed',
                'mode': 'workspaceWrite',
                'correlation_id': 'corr-1',
                'requested_by': USER,
                'profile_id': PROFILE,
                'team_id': None,
                'completion_criteria': [],
                'node_owner_id': None,
            },
            node_row={'id': NODE, 'owner_user_id': USER},
            ws_row={
                'allowed_harnesses': ['codex'],
                'allowed_modes': ['readOnly', 'workspaceWrite'],
            },
        )
        client = build_client(pool)
        resp = client.post(
            f'/api/workorders/{wo_id}/assign',
            json={
                'execution_node_id': str(NODE),
                'workspace_id': 'my-project',
                'harness': 'codex',
            },
            headers=auth_headers(),
        )
        assert resp.status_code == 409

    def test_list_work_orders(self):
        wo_id = uuid4()
        pool = FakeAssignPool(
            list_rows=[
                {
                    'id': wo_id,
                    'title': 'Existing Order',
                    'state': 'assigned',
                    'mode': 'readOnly',
                    'correlation_id': 'corr-2',
                    'execution_node_id': NODE,
                    'workspace_id': 'repo',
                    'harness': 'shell',
                    'assigned_user_id': USER,
                }
            ]
        )
        client = build_client(pool)
        resp = client.get('/api/workorders', headers=auth_headers())
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]['id'] == str(wo_id)
        assert items[0]['harness'] == 'shell'

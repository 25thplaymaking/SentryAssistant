"""Native image inputs survive one signed outbound dispatch and are then erased."""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.base import RuntimeExperience, RuntimeImage, RuntimeTurn
from app.agent_runtime.codex_workstation import CodexWorkstationRuntime
from app.auth.work_order_signing import NodeExpectation, validate_work_order
from app.routes.deps import Caller
from app.routes.nodes import claim_work


PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PROFILE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OWNER = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
NODE = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
DEVICE = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
ORDER = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
KEY = "native-image-signing-key-padded-past-32-bytes"


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class EnqueueConnection:
    def __init__(self):
        self.calls = []

    def transaction(self):
        return _Context(self)

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "OK"

    async def fetchrow(self, query, *_args):
        if "SELECT state FROM work_orders" in query:
            return {"state": "assigned"}
        return None


@pytest.mark.asyncio
async def test_codex_runtime_persists_validated_images_with_the_work_order(monkeypatch):
    connection = EnqueueConnection()
    runtime = CodexWorkstationRuntime()
    runtime.bind_pool(SimpleNamespace(acquire=lambda: _Context(connection)))

    async def status(_profile_id):
        return {
            "available": True,
            "owner_user_id": str(OWNER),
            "node_id": str(NODE),
            "node_name": "Bryce's PC",
            "workspaces": [{"id": "server-work", "modes": ["workspaceWrite"]}],
        }

    monkeypatch.setattr(runtime, "status", status)
    request = RuntimeTurn(
        session_id="session-image",
        profile_id=PROFILE,
        prompt="Describe it",
        correlation_id="corr-image",
        model="chatgpt-plan/gpt-5.6-sol",
        experience=RuntimeExperience.WORK,
        workspace_id="server-work",
        native_options={"sandbox": "workspaceWrite", "action": "turn"},
        images=(RuntimeImage(PNG_DATA_URL),),
    )

    stream = runtime.send_turn(request)
    first = await anext(stream)
    await stream.aclose()

    insert = next(call for call in connection.calls if "INSERT INTO work_orders" in call[0])
    assert json.loads(insert[1][-1]) == [{"data_url": PNG_DATA_URL}]
    assert first.evidence["runtime"] == "codex"
    assert any("input_images = '[]'::jsonb" in query for query, _ in connection.calls)


class ClaimConnection:
    def __init__(self):
        self.executed = []

    def transaction(self):
        return _Context(self)

    async def fetchrow(self, query, *_args):
        if "FROM execution_nodes" in query:
            return {"id": NODE, "owner_user_id": OWNER}
        return None

    async def fetch(self, query, *_args):
        if "FROM work_orders w" not in query:
            return []
        return [{
            "id": ORDER,
            "prompt": "Describe it",
            "workspace_id": "server-work",
            "harness": "codex",
            "mode": "workspaceWrite",
            "correlation_id": "corr-image",
            "requested_by": OWNER,
            "team_id": None,
            "profile_id": PROFILE,
            "runtime_session_id": "session-image",
            "runtime_model": "gpt-5.6-sol",
            "runtime_options": {"sandbox": "workspaceWrite", "action": "turn"},
            "input_images": [{"data_url": PNG_DATA_URL}],
        }]

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"


@pytest.mark.asyncio
async def test_node_claim_gets_hash_bound_images_once_and_clears_storage():
    connection = ClaimConnection()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        pool=SimpleNamespace(acquire=lambda: _Context(connection)),
        settings=SimpleNamespace(signing_key=KEY),
    )))
    caller = Caller(user_id=OWNER, device_id=DEVICE, profile_id=PROFILE)

    dispatched = await claim_work(request, caller)

    assert dispatched[0].input_images == [{"data_url": PNG_DATA_URL}]
    claims = validate_work_order(
        token=dispatched[0].token,
        signing_key=KEY,
        expectation=NodeExpectation(
            node_id=str(NODE),
            node_owner_user_id=str(OWNER),
            registered_workspaces=frozenset({"server-work"}),
            allowed_harnesses=frozenset({"codex"}),
        ),
        seen_nonces=set(),
    )
    assert len(claims["imgsha"]) == 64
    assert any("input_images = '[]'::jsonb" in query for query, _ in connection.executed)

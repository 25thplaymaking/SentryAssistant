"""The MCP bridge exposes fixed workstation tools, never a general remote shell."""

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[3] / "deploy" / "linux" / "hermes" / "server_control_mcp.py"
)
SPEC = importlib.util.spec_from_file_location("server_control_mcp", MODULE_PATH)
assert SPEC and SPEC.loader
MCP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MCP)


def test_tool_surface_is_fixed_and_has_no_path_or_general_shell_field():
    tools = {tool["name"]: tool for tool in MCP.TOOLS}
    assert set(tools) == {
        "server_status",
        "server_action",
        "workstation_status",
        "workstation_run",
        "workstation_result",
    }
    run_properties = tools["workstation_run"]["inputSchema"]["properties"]
    assert "path" not in run_properties
    assert "command" not in run_properties
    assert run_properties["mode"]["enum"] == ["readOnly", "workspaceWrite"]
    assert run_properties["harness"]["enum"] == ["shell", "claude", "codex"]


def test_workstation_calls_only_fixed_gateway_routes(monkeypatch):
    calls = []

    def fake_gateway(path, *, payload=None):
        calls.append((path, payload))
        return {"nodes": []}

    monkeypatch.setattr(MCP, "gateway_api", fake_gateway)
    response = MCP.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "workstation_status", "arguments": {}
        }}
    )
    assert calls == [("/api/runtime/workstation/status", None)]
    assert response["result"]["content"][0]["type"] == "text"


def test_gateway_helper_refuses_non_workstation_paths(monkeypatch):
    monkeypatch.setattr(MCP, "GATEWAY_KEY", "test-key")
    try:
        MCP.gateway_api("/api/admin/users")
    except RuntimeError as error:
        assert "non-workstation" in str(error)
    else:
        raise AssertionError("a non-workstation path was accepted")


def test_work_order_id_cannot_escape_the_fixed_gateway_route(monkeypatch):
    calls = []
    monkeypatch.setattr(MCP, "gateway_api", lambda path, **_: calls.append(path))
    try:
        MCP.workstation_result("../../admin/users")
    except RuntimeError as error:
        assert "valid UUID" in str(error)
    else:
        raise AssertionError("a path-shaped work-order ID was accepted")
    assert calls == []

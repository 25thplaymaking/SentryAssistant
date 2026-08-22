"""Minimal MCP bridge to Server Control's allowlisted automation API.

No shell, process, delete, or arbitrary URL tool is exposed. The bearer token
is read from a read-only Docker secret mount and never returned to the model.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from uuid import UUID

from aiohttp import web

BASE_URL = os.environ.get(
    "SERVER_CONTROL_API_URL",
    "http://host.docker.internal:7444/server-control/api/automation",
).rstrip("/")
TOKEN_FILE = os.environ.get(
    "SERVER_CONTROL_TOKEN_FILE", "/run/secrets/server-control-token"
)
GATEWAY_URL = os.environ.get(
    "SENTRY_GATEWAY_URL", "http://gateway:8090"
).rstrip("/")
GATEWAY_KEY = os.environ.get("SENTRY_HERMES_API_KEY", "")


def request_json(
    url: str, *, token: str, payload: dict | None = None, timeout: int = 15
) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if payload is None else "POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            problem = json.load(error)
            detail = problem.get("detail") or problem.get("title")
        except Exception:
            detail = None
        raise RuntimeError(detail or f"The requested Sentry action returned HTTP {error.code}") from None


def server_api(path: str, *, payload: dict | None = None) -> dict:
    token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    return request_json(f"{BASE_URL}{path}", token=token, payload=payload)


def gateway_api(path: str, *, payload: dict | None = None) -> dict:
    if not GATEWAY_KEY:
        raise RuntimeError("Sentry's workstation connection is not configured.")
    if not path.startswith("/api/runtime/workstation/"):
        raise RuntimeError("Refused a non-workstation Gateway path.")
    return request_json(f"{GATEWAY_URL}{path}", token=GATEWAY_KEY, payload=payload)


def workstation_result(work_order_id: str) -> dict:
    try:
        normalized = str(UUID(work_order_id))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("Work order ID must be a valid UUID.") from None
    return gateway_api(f"/api/runtime/workstation/work/{normalized}")


def dispatch_workstation(arguments: dict) -> dict:
    payload = {
        "title": arguments["title"],
        "instruction": arguments["instruction"],
        "workspace_id": arguments["workspace_id"],
        "harness": arguments.get("harness", "claude"),
        "mode": arguments.get("mode", "readOnly"),
    }
    dispatched = gateway_api("/api/runtime/workstation/work", payload=payload)

    # Fast inspection commands normally finish in a few seconds. Waiting here
    # lets Sentry return the verified result in the same chat turn without
    # inventing another job system. Longer work remains queryable by its ID.
    wait_seconds = max(0, min(int(arguments.get("wait_seconds", 20)), 30))
    work_order_id = dispatched.get("work_order_id")
    if not work_order_id or wait_seconds == 0:
        return dispatched

    deadline = time.monotonic() + wait_seconds
    latest = dispatched
    while time.monotonic() < deadline:
        time.sleep(1.5)
        latest = workstation_result(work_order_id)
        if latest.get("terminal"):
            return latest
    return latest


TOOLS = [
    {
        "name": "server_status",
        "description": (
            "List the current status and available safe actions for services on Bryce's Linux box. "
            "Use this before acting and report only the returned state."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "server_action",
        "description": (
            "Start, stop, or restart one existing allowlisted service. First call server_status, then pass "
            "the exact service id and exact visible name. This cannot run shell commands, stop arbitrary "
            "processes, delete servers, or create an unapproved server. Ask Bryce when the request is ambiguous."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "minLength": 1},
                "action": {"type": "string", "enum": ["start", "stop", "restart"]},
                "expected_name": {"type": "string", "minLength": 1},
            },
            "required": ["service_id", "action", "expected_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workstation_status",
        "description": (
            "List Bryce's connected personal workstation and its exact named workspace, harness, and mode capabilities. "
            "Local filesystem paths are never returned. Always call this before workstation_run."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "workstation_run",
        "description": (
            "Send one bounded work order to Bryce's connected workstation. Use an exact workspace and capability from "
            "workstation_status. Default to readOnly. Use workspaceWrite only when Bryce explicitly asks to change files. "
            "The shell harness accepts only its local command allowlist; Claude is separately tool-restricted. No raw path, "
            "inbound connection, elevated action, deletion, credential access, git push/reset/clean, or arbitrary server shell is exposed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "instruction": {"type": "string", "minLength": 1, "maxLength": 20000},
                "workspace_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "harness": {"type": "string", "enum": ["shell", "claude"], "default": "claude"},
                "mode": {"type": "string", "enum": ["readOnly", "workspaceWrite"], "default": "readOnly"},
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 30, "default": 20},
            },
            "required": ["title", "instruction", "workspace_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workstation_result",
        "description": (
            "Read the durable state and result of a workstation work order. Use this after workstation_run returns a "
            "non-terminal state, and do not claim the action completed until this returns a terminal result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_order_id": {"type": "string", "format": "uuid"},
            },
            "required": ["work_order_id"],
            "additionalProperties": False,
        },
    },
]


def result(request_id: object, value: object) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def handle(message: dict) -> dict | None:
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "sentry-server-control", "version": "1.1.0"},
            },
        )
    if method == "tools/list":
        return result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments") or {}
        try:
            if params.get("name") == "server_status":
                value = server_api("/status")
            elif params.get("name") == "server_action":
                value = server_api(
                    f"/services/{arguments['service_id']}/actions/{arguments['action']}",
                    payload={"confirmationName": arguments["expected_name"]},
                )
            elif params.get("name") == "workstation_status":
                value = gateway_api("/api/runtime/workstation/status")
            elif params.get("name") == "workstation_run":
                value = dispatch_workstation(arguments)
            elif params.get("name") == "workstation_result":
                value = workstation_result(arguments["work_order_id"])
            else:
                raise RuntimeError("Unknown Server Control tool.")
            return result(request_id, {"content": [{"type": "text", "text": json.dumps(value)}]})
        except Exception as error:
            return result(
                request_id,
                {"isError": True, "content": [{"type": "text", "text": str(error)[:500]}]},
            )
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}


def run_stdio() -> None:
    for line in sys.stdin:
        try:
            response = handle(json.loads(line))
            if response is not None:
                print(json.dumps(response, separators=(",", ":")), flush=True)
        except Exception as error:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(error)[:500]}}), flush=True)


async def http_mcp(request: web.Request) -> web.Response:
    if request.content_length is not None and request.content_length > 65536:
        raise web.HTTPRequestEntityTooLarge(max_size=65536, actual_size=request.content_length)
    try:
        message = await request.json()
        # Tool calls perform bounded blocking HTTP requests and may wait briefly
        # for a workstation result. Keep them off aiohttp's event loop so the
        # health endpoint and other tool requests remain responsive.
        response = await asyncio.to_thread(handle, message)
    except Exception as error:
        response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)[:500]}}
    return web.json_response(response or {}, status=202 if response is None else 200)


def run_http() -> None:
    app = web.Application(client_max_size=65536)
    app.router.add_post("/mcp", http_mcp)
    app.router.add_get("/health", lambda _: web.json_response({"status": "healthy"}))
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("SERVER_CONTROL_MCP_PORT", "8765")))


if __name__ == "__main__":
    run_http() if "--http" in sys.argv else run_stdio()

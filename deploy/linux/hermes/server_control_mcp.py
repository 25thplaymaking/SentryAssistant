"""Minimal MCP bridge to Server Control's allowlisted automation API.

No shell, process, delete, or arbitrary URL tool is exposed. The bearer token
is read from a read-only Docker secret mount and never returned to the model.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from aiohttp import web

BASE_URL = os.environ.get(
    "SERVER_CONTROL_API_URL",
    "http://host.docker.internal:7444/server-control/api/automation",
).rstrip("/")
TOKEN_FILE = os.environ.get(
    "SERVER_CONTROL_TOKEN_FILE", "/run/secrets/server-control-token"
)


def api(path: str, *, payload: dict | None = None) -> dict:
    token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method="GET" if payload is None else "POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            problem = json.load(error)
            detail = problem.get("detail") or problem.get("title")
        except Exception:
            detail = None
        raise RuntimeError(detail or f"Server Control returned HTTP {error.code}") from None


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
                "serverInfo": {"name": "sentry-server-control", "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments") or {}
        try:
            if params.get("name") == "server_status":
                value = api("/status")
            elif params.get("name") == "server_action":
                value = api(
                    f"/services/{arguments['service_id']}/actions/{arguments['action']}",
                    payload={"confirmationName": arguments["expected_name"]},
                )
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
        response = handle(message)
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

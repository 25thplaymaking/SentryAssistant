"""Owner-scoped integrations for linked workstation sessions and repositories.

Every local action is a signed work order claimed outbound by the owner's node.
The browser never supplies a filesystem path and the Gateway never connects to
the workstation directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator

from ..audit.service import AuditEvent, AuditService, Decision
from .deps import Caller, require_caller

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

_ONLINE_WINDOW = timedelta(seconds=45)
_TERMINAL_STATES = frozenset(
    {"readyForReview", "resolved", "closed", "cancelled", "failed"}
)
_SERVER_STATUS_URL = os.environ.get(
    "SENTRY_SERVER_CONTROL_STATUS_URL",
    "http://server-control-mcp:8765/status",
)


class IntegrationAction(BaseModel):
    action: Literal[
        "sessionsSync", "sessionRead", "sessionWatch", "workspaceInspect", "openIde"
    ]
    node_id: UUID
    workspace_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    provider: Literal["codex", "claude", "all"] | None = None
    provider_session_id: str | None = Field(default=None, max_length=160)
    ide: Literal["vscode", "cursor"] | None = None
    watch_seconds: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action in {"sessionRead", "sessionWatch"}:
            if self.provider not in {"codex", "claude"} or not self.provider_session_id:
                raise ValueError("Reading a session requires its provider and exact session id.")
        if self.action == "openIde" and self.ide not in {"vscode", "cursor"}:
            raise ValueError("Opening a workspace requires an available IDE.")
        return self


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The integration service is temporarily unavailable.",
        )
    return pool


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _service_status_sync() -> dict[str, Any]:
    request = urllib.request.Request(
        _SERVER_STATUS_URL,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if int(getattr(response, "status", 200)) != 200:
                return {"available": False, "services": []}
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {"available": False, "services": []}
    if not isinstance(payload, dict):
        return {"available": False, "services": []}

    raw_services = payload.get("services")
    if not isinstance(raw_services, list):
        raw_services = payload.get("items")
    if not isinstance(raw_services, list):
        raw_services = []
    services: list[dict[str, Any]] = []
    for item in raw_services[:200]:
        if not isinstance(item, dict):
            continue
        service_id = str(
            item.get("id") or item.get("serviceId") or item.get("service_id") or ""
        ).strip()
        name = str(item.get("name") or item.get("displayName") or service_id).strip()
        if not service_id or not name:
            continue
        raw_actions = (
            item.get("availableActions")
            or item.get("available_actions")
            or item.get("actions")
            or []
        )
        actions = [
            str(value)
            for value in raw_actions
            if str(value) in {"start", "stop", "restart"}
        ] if isinstance(raw_actions, list) else []
        services.append(
            {
                "id": service_id[:200],
                "name": name[:200],
                "status": str(item.get("status") or item.get("state") or "unknown")[:80],
                "actions": actions,
            }
        )
    return {"available": True, "services": services}


async def service_targets() -> dict[str, Any]:
    return await asyncio.to_thread(_service_status_sync)


async def validate_sentry_target(
    request: Request,
    caller: Caller,
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve a client target against current owner-scoped capabilities."""
    if not target:
        return {}
    kind = str(target.get("kind") or "").strip()
    if kind == "workspace":
        node_id = str(target.get("node_id") or "").strip()
        workspace_id = str(target.get("workspace_id") or "").strip()
        if not node_id or not workspace_id:
            raise HTTPException(status_code=400, detail="Workspace target is incomplete.")
        try:
            parsed_node_id = UUID(node_id)
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Workspace target is invalid.") from None
        async with _pool(request).acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT n.id, n.name, nw.workspace_id
                FROM execution_nodes n
                JOIN devices d ON d.id = n.device_id
                JOIN node_workspaces nw ON nw.node_id = n.id
                WHERE n.id = $1 AND n.owner_user_id = $2
                  AND nw.workspace_id = $3
                  AND d.revoked_at IS NULL
                  AND n.last_seen_at >= now() - interval '45 seconds'
                """,
                parsed_node_id, caller.user_id, workspace_id,
            )
        if row is None:
            raise HTTPException(status_code=409, detail="That linked workspace is not online.")
        return {
            "kind": "workspace",
            "node_id": str(row["id"]),
            "node_name": str(row["name"]),
            "workspace_id": str(row["workspace_id"]),
        }
    if kind == "service":
        service_id = str(target.get("service_id") or "").strip()
        expected_name = str(target.get("name") or "").strip()
        live = await service_targets()
        match = next(
            (
                item for item in live.get("services", [])
                if item.get("id") == service_id
                and (not expected_name or item.get("name") == expected_name)
            ),
            None,
        )
        if match is None:
            raise HTTPException(status_code=409, detail="That allowlisted service is not available.")
        return {
            "kind": "service",
            "service_id": match["id"],
            "name": match["name"],
            "status": match["status"],
        }
    raise HTTPException(status_code=400, detail="Unknown Sentry target type.")


@router.get("/status")
async def integration_status(
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.name, n.last_seen_at, n.native_runtimes, d.revoked_at,
                   nw.workspace_id, nw.allowed_harnesses, nw.allowed_modes
            FROM execution_nodes n
            JOIN devices d ON d.id = n.device_id
            LEFT JOIN node_workspaces nw ON nw.node_id = n.id
            WHERE n.owner_user_id = $1
            ORDER BY n.last_seen_at DESC NULLS LAST, nw.workspace_id
            """,
            caller.user_id,
        )

    now = datetime.now(timezone.utc)
    nodes: dict[str, dict[str, Any]] = {}
    for row in rows:
        node_id = str(row["id"])
        last_seen = row["last_seen_at"]
        online = bool(
            row["revoked_at"] is None
            and last_seen is not None
            and now - last_seen <= _ONLINE_WINDOW
        )
        runtimes = _json_object(row["native_runtimes"])
        node = nodes.setdefault(
            node_id,
            {
                "node_id": node_id,
                "name": row["name"],
                "online": online,
                "last_seen_at": last_seen.isoformat() if last_seen else None,
                "integration": runtimes.get("integrations") or {},
                "workspaces": [],
            },
        )
        if row["workspace_id"]:
            node["workspaces"].append(
                {
                    "id": row["workspace_id"],
                    "harnesses": list(row["allowed_harnesses"] or []),
                    "modes": list(row["allowed_modes"] or []),
                }
            )
    services = await service_targets()
    return {
        "available": True,
        "nodes": list(nodes.values()),
        "services": services,
        "provider_boundary": (
            "Codex and Claude sessions come from their local installations on your linked "
            "machine. Model OAuth alone does not expose ChatGPT or provider-website history."
        ),
    }


@router.post("/actions", status_code=status.HTTP_202_ACCEPTED)
async def dispatch_integration_action(
    body: IntegrationAction,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict[str, Any]:
    pool = _pool(request)
    audit = AuditService(pool)
    correlation_id = f"integration-{uuid4()}"
    work_order_id = uuid4()
    runtime_options = {
        "action": body.action,
        "sandbox": "readOnly",
        "provider": body.provider,
        "provider_session_id": body.provider_session_id,
        "ide": body.ide,
        "watch_seconds": body.watch_seconds,
    }
    titles = {
        "sessionsSync": "Sync linked provider sessions",
        "sessionRead": "Read linked provider session",
        "sessionWatch": "Watch linked provider session",
        "workspaceInspect": "Inspect linked workspace",
        "openIde": "Open linked workspace in IDE",
    }

    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT n.id AS node_id, n.name AS node_name
                FROM execution_nodes n
                JOIN devices d ON d.id = n.device_id
                JOIN node_workspaces nw ON nw.node_id = n.id
                WHERE n.owner_user_id = $1
                  AND d.revoked_at IS NULL
                  AND n.last_seen_at >= now() - interval '45 seconds'
                  AND nw.workspace_id = $2
                  AND n.id = $3
                  AND 'integrations' = ANY(nw.allowed_harnesses)
                  AND 'readOnly' = ANY(nw.allowed_modes)
                  AND EXISTS (
                      SELECT 1 FROM workspace_policy_grants g
                      WHERE g.node_workspace_id = nw.id
                        AND g.team_id IS NULL
                        AND g.mode = 'readOnly'
                        AND g.revoked_at IS NULL
                  )
                ORDER BY n.last_seen_at DESC
                LIMIT 1 FOR UPDATE OF n
                """,
                caller.user_id,
                body.workspace_id,
                body.node_id,
            )
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="No online linked machine exposes that integration workspace.",
                )
            await conn.execute(
                """
                INSERT INTO work_orders (
                    id, requested_by, profile_id, team_id, conversation_id,
                    assigned_user_id, execution_node_id, harness, workspace_id,
                    title, prompt, state, mode, completion_criteria,
                    correlation_id, expires_at, runtime_session_id, runtime_model,
                    runtime_options
                ) VALUES (
                    $1,$2,$3,NULL,$4,$2,$5,'integrations',$6,
                    $7,$8,'assigned','readOnly',$9,$10,$11,$12,'integration-v1',$13
                )
                """,
                work_order_id,
                caller.user_id,
                caller.profile_id,
                uuid4(),
                target["node_id"],
                body.workspace_id,
                titles[body.action],
                f"Sentry integration action: {body.action}",
                ["Return only bounded integration data from the selected allowlisted workspace."],
                correlation_id,
                datetime.now(timezone.utc) + timedelta(minutes=10),
                f"integration:{uuid4()}",
                json.dumps(runtime_options),
            )
            await conn.execute(
                """
                INSERT INTO work_order_subscriptions (work_order_id, user_id, reason)
                VALUES ($1,$2,'requester') ON CONFLICT DO NOTHING
                """,
                work_order_id, caller.user_id,
            )
            await conn.execute(
                """
                INSERT INTO work_order_transitions
                    (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                VALUES
                    ($1,'draft','submitted',$2,'explicit Sentry integration action',$3),
                    ($1,'submitted','triaged',$2,'matched to linked integration capability',$3),
                    ($1,'triaged','assigned',$2,'assigned to owner workstation',$3)
                """,
                work_order_id, caller.user_id, correlation_id,
            )
            await conn.execute(
                """
                INSERT INTO work_order_runs
                    (work_order_id, attempt, harness, execution_node_id)
                VALUES ($1,1,'integrations',$2)
                """,
                work_order_id, target["node_id"],
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action=f"integration.{body.action}",
                    decision=Decision.ALLOWED,
                    correlation_id=correlation_id,
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    profile_id=caller.profile_id,
                    target_kind="workorder",
                    target_id=str(work_order_id),
                    detail=f"{body.action} on {body.workspace_id} via {target['node_name']}",
                ),
            )

    return {
        "work_order_id": str(work_order_id),
        "state": "assigned",
        "workspace_id": body.workspace_id,
        "node_id": str(target["node_id"]),
        "node": target["node_name"],
        "action": body.action,
    }


@router.get("/actions/{work_order_id}")
async def integration_action_result(
    work_order_id: UUID,
    request: Request,
    after: int = Query(default=0, ge=0),
    caller: Caller = Depends(require_caller),
) -> dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT w.id, w.state, w.workspace_id, w.title,
                   r.outcome, r.summary, r.finished_at
            FROM work_orders w
            LEFT JOIN LATERAL (
                SELECT outcome, summary, finished_at FROM work_order_runs
                WHERE work_order_id = w.id ORDER BY attempt DESC LIMIT 1
            ) r ON TRUE
            WHERE w.id = $1 AND w.profile_id = $2
              AND w.requested_by = $3 AND w.harness = 'integrations'
            """,
            work_order_id, caller.profile_id, caller.user_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Integration action not found.")
        events = await conn.fetch(
            """
            SELECT event_index, event_type, summary, payload,
                   occurred_at AS created_at
            FROM work_order_events
            WHERE work_order_id = $1 AND event_index > $2
            ORDER BY event_index LIMIT 100
            """,
            work_order_id, after,
        )
    return {
        "work_order_id": str(row["id"]),
        "state": row["state"],
        "terminal": row["state"] in _TERMINAL_STATES,
        "workspace_id": row["workspace_id"],
        "outcome": row["outcome"],
        "summary": row["summary"],
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "events": [
            {
                "index": event["event_index"],
                "type": event["event_type"],
                "summary": event["summary"],
                "payload": _json_object(event["payload"]),
                "created_at": event["created_at"].isoformat(),
            }
            for event in events
        ],
    }


@router.post("/actions/{work_order_id}/cancel")
async def cancel_integration_action(
    work_order_id: UUID,
    request: Request,
    caller: Caller = Depends(require_caller),
) -> dict[str, Any]:
    pool = _pool(request)
    correlation_id = f"integration-cancel-{uuid4()}"
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, state FROM work_orders
                WHERE id = $1 AND profile_id = $2 AND requested_by = $3
                  AND harness = 'integrations' FOR UPDATE
                """,
                work_order_id, caller.profile_id, caller.user_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Integration action not found.")
            if row["state"] not in _TERMINAL_STATES:
                await conn.execute(
                    """
                    UPDATE work_order_runs SET outcome='cancelled', summary='Cancelled by user',
                        status_boundary='implemented', finished_at=now()
                    WHERE work_order_id=$1 AND finished_at IS NULL
                    """,
                    work_order_id,
                )
                await conn.execute(
                    "UPDATE work_orders SET state='cancelled', updated_at=now() WHERE id=$1",
                    work_order_id,
                )
                await conn.execute(
                    """
                    INSERT INTO work_order_transitions
                        (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                    VALUES ($1,$2,'cancelled',$3,'user closed live integration view',$4)
                    """,
                    work_order_id, row["state"], caller.user_id, correlation_id,
                )
    return {"work_order_id": str(work_order_id), "state": "cancelled", "terminal": True}

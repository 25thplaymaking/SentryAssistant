"""Native Codex turns relayed through the owner's outbound workstation node.

This is deliberately not an OpenAI model proxy.  A ``chatgpt-plan/*`` route is
executed by the signed-in Codex App Server on the user's own workstation so its
threads, project instructions, skills, apps, MCP configuration, sandbox, and
approval protocol remain authoritative.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from .base import RuntimeEvent, RuntimeEventType, RuntimeExperience, RuntimeTurn

CODEX_MODEL_PREFIX = "chatgpt-plan/"
_ONLINE_SECONDS = 45
_TERMINAL_STATES = frozenset(
    {"readyForReview", "resolved", "closed", "cancelled", "failed"}
)
_EVENT_TYPES = {event.value: event for event in RuntimeEventType}


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


class NativeCodexUnavailable(RuntimeError):
    """Raised when the profile has no live, capable Codex workstation."""


class CodexWorkstationRuntime:
    """Database-backed adapter for native Codex work on an outbound node."""

    def __init__(self) -> None:
        self._pool = None

    def bind_pool(self, pool) -> None:
        self._pool = pool

    @staticmethod
    def owns_model(model: str | None) -> bool:
        return bool(model and str(model).startswith(CODEX_MODEL_PREFIX))

    @staticmethod
    def source_model(model: str) -> str:
        return model[len(CODEX_MODEL_PREFIX) :]

    async def status(self, profile_id: UUID) -> dict[str, Any]:
        if self._pool is None:
            return {
                "id": "codex",
                "available": False,
                "reason": "The workstation relay is unavailable.",
                "workspaces": [],
                "features": [],
            }

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.owner_user_id, n.id AS node_id, n.name, n.last_seen_at,
                       n.native_runtimes, d.revoked_at, nw.workspace_id,
                       nw.allowed_harnesses, nw.allowed_modes
                FROM profiles p
                JOIN execution_nodes n ON n.owner_user_id = p.owner_user_id
                JOIN devices d ON d.id = n.device_id
                LEFT JOIN node_workspaces nw ON nw.node_id = n.id
                WHERE p.id = $1
                ORDER BY n.last_seen_at DESC NULLS LAST, nw.workspace_id
                """,
                profile_id,
            )

        now = datetime.now(timezone.utc)
        candidates: dict[str, dict[str, Any]] = {}
        for row in rows:
            node_id = str(row["node_id"])
            runtimes = _json_object(row["native_runtimes"])
            codex = runtimes.get("codex") if isinstance(runtimes, dict) else None
            last_seen = row["last_seen_at"]
            online = bool(
                row["revoked_at"] is None
                and last_seen is not None
                and now - last_seen <= timedelta(seconds=_ONLINE_SECONDS)
            )
            capable = bool(isinstance(codex, dict) and codex.get("available"))
            candidate = candidates.setdefault(
                node_id,
                {
                    "id": "codex",
                    "available": online and capable,
                    "reason": None,
                    "node_id": node_id,
                    "node_name": row["name"],
                    "owner_user_id": row["owner_user_id"],
                    "version": codex.get("version") if isinstance(codex, dict) else None,
                    "auth_mode": codex.get("auth_mode") if isinstance(codex, dict) else None,
                    "models": list(codex.get("models") or []) if isinstance(codex, dict) else [],
                    "features": list(codex.get("features") or []) if isinstance(codex, dict) else [],
                    "workspaces": [],
                    "last_seen_at": last_seen.isoformat() if last_seen else None,
                },
            )
            if row["workspace_id"] and "codex" in list(row["allowed_harnesses"] or []):
                candidate["workspaces"].append(
                    {
                        "id": row["workspace_id"],
                        "modes": list(row["allowed_modes"] or []),
                    }
                )
        for candidate in candidates.values():
            candidate["available"] = bool(
                candidate["available"] and candidate["workspaces"]
            )
            if not candidate["available"]:
                candidate["reason"] = (
                    "Codex is not currently connected on this workstation."
                )

        live = next((value for value in candidates.values() if value["available"]), None)
        if live is not None:
            return live
        if candidates:
            return next(iter(candidates.values()))
        return {
            "id": "codex",
            "available": False,
            "reason": "No Codex workstation is linked to this profile.",
            "workspaces": [],
            "features": [],
        }

    async def filter_models(
        self, profile_id: UUID, models: tuple[str, ...]
    ) -> tuple[str, ...]:
        status = await self.status(profile_id)
        linked = any(self.owns_model(model) for model in models)
        non_native = tuple(model for model in models if not self.owns_model(model))
        if not status.get("available") or not linked:
            return non_native

        # The local App Server is authoritative for the Codex catalogue.  The
        # linked-account snapshot is only the connection gate: intersecting the
        # two would hide newly released subscription models until the user
        # disconnected and linked the provider again.
        native_models = tuple(
            f"{CODEX_MODEL_PREFIX}{model}"
            for model in dict.fromkeys(str(model) for model in status.get("models") or [])
            if model
        )
        return non_native + native_models

    async def decorate_auth_providers(self, profile_id: UUID, payload: dict) -> dict:
        result = deepcopy(payload) if isinstance(payload, dict) else {"data": []}
        status = await self.status(profile_id)
        for provider in result.get("data", []):
            if not isinstance(provider, dict) or provider.get("id") != "openai-codex":
                continue
            provider["native_runtime"] = "codex"
            provider["native_runtime_available"] = bool(status.get("available"))
            if not status.get("available"):
                provider["authenticated"] = False
                provider["route_error"] = status.get("reason")
            else:
                existing = {
                    str((route or {}).get("model") or ""): route
                    for route in provider.get("models") or []
                    if isinstance(route, dict) and (route or {}).get("model")
                }
                provider["models"] = [
                    existing.get(model)
                    or {
                        "id": f"{CODEX_MODEL_PREFIX}{model}",
                        "model": model,
                        "label": model,
                    }
                    for model in dict.fromkeys(
                        str(model) for model in status.get("models") or []
                    )
                    if model
                ]
        result["native_runtime"] = status
        return result

    async def send_turn(self, request: RuntimeTurn):
        if self._pool is None:
            yield self._event(
                RuntimeEventType.TURN_FAILED,
                request,
                "The workstation relay is unavailable.",
            )
            return

        status = await self.status(request.profile_id)
        if not status.get("available"):
            yield self._event(
                RuntimeEventType.TURN_FAILED,
                request,
                str(status.get("reason") or "Codex is not connected."),
            )
            return

        workspace = self._select_workspace(status, request.workspace_id)
        if workspace is None:
            yield self._event(
                RuntimeEventType.TURN_FAILED,
                request,
                "No linked local workspace can run this Codex turn.",
            )
            return

        mode = (
            "workspaceWrite"
            if request.experience is RuntimeExperience.WORK
            and "workspaceWrite" in workspace["modes"]
            else "readOnly"
        )
        work_order_id = uuid4()
        source_model = self.source_model(str(request.model))
        expires_at = datetime.now(timezone.utc) + timedelta(hours=12)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO work_orders (
                        id, requested_by, profile_id, team_id, conversation_id,
                        assigned_user_id, execution_node_id, harness, workspace_id,
                        title, prompt, state, mode, completion_criteria,
                        correlation_id, expires_at, runtime_session_id, runtime_model
                    ) VALUES (
                        $1,$2,$3,NULL,$4,$2,$5,'codex',$6,
                        $7,$8,'assigned',$9,$10,$11,$12,$13,$14
                    )
                    """,
                    work_order_id,
                    status["owner_user_id"],
                    request.profile_id,
                    uuid4(),
                    UUID(status["node_id"]),
                    workspace["id"],
                    "Native Codex turn",
                    request.prompt,
                    mode,
                    ["Complete the requested Codex turn and return its native event evidence."],
                    request.correlation_id,
                    expires_at,
                    request.session_id,
                    source_model,
                )
                await conn.execute(
                    """
                    INSERT INTO work_order_subscriptions (work_order_id, user_id, reason)
                    VALUES ($1,$2,'requester') ON CONFLICT DO NOTHING
                    """,
                    work_order_id,
                    status["owner_user_id"],
                )
                await conn.execute(
                    """
                    INSERT INTO work_order_transitions
                        (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                    VALUES
                        ($1,'draft','submitted',$2,'native Codex turn from Sentry Work',$3),
                        ($1,'submitted','triaged',$2,'matched to the owning Codex workstation',$3),
                        ($1,'triaged','assigned',$2,'assigned to the outbound workstation node',$3)
                    """,
                    work_order_id,
                    status["owner_user_id"],
                    request.correlation_id,
                )
                await conn.execute(
                    """
                    INSERT INTO work_order_runs
                        (work_order_id, attempt, harness, execution_node_id)
                    VALUES ($1,1,'codex',$2)
                    """,
                    work_order_id,
                    UUID(status["node_id"]),
                )

        finished = False
        try:
            yield self._event(
                RuntimeEventType.SESSION_STARTED,
                request,
                f"Codex connected to {workspace['id']} on {status['node_name']}.",
                {
                    "runtime": "codex",
                    "work_order_id": str(work_order_id),
                    "workspace_id": workspace["id"],
                    "node": status["node_name"],
                    "model": source_model,
                    "mode": mode,
                },
            )

            last_index = 0
            terminal_event: RuntimeEvent | None = None
            while True:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT event_index, event_type, summary, payload, occurred_at
                        FROM work_order_events
                        WHERE work_order_id = $1 AND event_index > $2
                        ORDER BY event_index
                        """,
                        work_order_id,
                        last_index,
                    )
                    run = await conn.fetchrow(
                        """
                        SELECT w.state, r.outcome, r.summary, r.finished_at
                        FROM work_orders w
                        LEFT JOIN LATERAL (
                            SELECT outcome, summary, finished_at
                            FROM work_order_runs WHERE work_order_id = w.id
                            ORDER BY attempt DESC LIMIT 1
                        ) r ON TRUE
                        WHERE w.id = $1
                        """,
                        work_order_id,
                    )

                for row in rows:
                    last_index = max(last_index, int(row["event_index"]))
                    event_type = _EVENT_TYPES.get(
                        str(row["event_type"]), RuntimeEventType.TOOL_PROGRESS
                    )
                    evidence = _json_object(row["payload"])
                    evidence.setdefault("runtime", "codex")
                    evidence.setdefault("work_order_id", str(work_order_id))
                    event = RuntimeEvent(
                        type=event_type,
                        session_id=request.session_id,
                        correlation_id=request.correlation_id,
                        occurred_at=row["occurred_at"],
                        summary=str(row["summary"] or ""),
                        evidence=evidence,
                    )
                    if event_type in {
                        RuntimeEventType.TURN_COMPLETED,
                        RuntimeEventType.TURN_FAILED,
                        RuntimeEventType.CANCELLED,
                        RuntimeEventType.ERROR,
                    }:
                        terminal_event = event
                    else:
                        yield event

                if run is None:
                    finished = True
                    yield self._event(
                        RuntimeEventType.TURN_FAILED,
                        request,
                        "The native Codex work order disappeared before completion.",
                        {"work_order_id": str(work_order_id), "runtime": "codex"},
                    )
                    return
                if run["state"] in _TERMINAL_STATES:
                    finished = True
                    if run["outcome"] == "succeeded":
                        if terminal_event is not None and terminal_event.type is RuntimeEventType.TURN_COMPLETED:
                            yield terminal_event
                        else:
                            yield self._event(
                                RuntimeEventType.TURN_COMPLETED,
                                request,
                                str(run["summary"] or "Codex completed the turn."),
                                {
                                    "work_order_id": str(work_order_id),
                                    "runtime": "codex",
                                    "model": source_model,
                                },
                            )
                    else:
                        yield self._event(
                            RuntimeEventType.CANCELLED
                            if run["outcome"] == "cancelled"
                            else RuntimeEventType.TURN_FAILED,
                            request,
                            str(run["summary"] or "The native Codex turn failed."),
                            {"work_order_id": str(work_order_id), "runtime": "codex"},
                        )
                    return
                await asyncio.sleep(0.25)
        finally:
            if not finished:
                # Closing the browser stream is the native Stop action.  Mark
                # the durable order terminal so the outbound node can observe
                # it and interrupt the local App Server turn.
                cleanup = asyncio.create_task(
                    self._cancel_if_active(
                        work_order_id,
                        status["owner_user_id"],
                        request.correlation_id,
                    )
                )
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    pass

    async def _cancel_if_active(
        self, work_order_id: UUID, actor_user_id: UUID, correlation_id: str
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT state FROM work_orders WHERE id = $1 FOR UPDATE",
                    work_order_id,
                )
                if row is None or row["state"] not in {"assigned", "inProgress"}:
                    return
                previous = row["state"]
                await conn.execute(
                    """
                    UPDATE work_order_runs
                    SET finished_at = coalesce(finished_at, now()),
                        outcome = 'cancelled', status_boundary = 'implemented',
                        summary = 'Codex turn stopped from Sentry.'
                    WHERE work_order_id = $1 AND finished_at IS NULL
                    """,
                    work_order_id,
                )
                await conn.execute(
                    "UPDATE work_orders SET state = 'cancelled', updated_at = now() WHERE id = $1",
                    work_order_id,
                )
                await conn.execute(
                    """
                    INSERT INTO work_order_transitions
                        (work_order_id, from_state, to_state, actor_user_id, reason, correlation_id)
                    VALUES ($1,$2,'cancelled',$3,'Sentry stream stopped',$4)
                    """,
                    work_order_id,
                    previous,
                    actor_user_id,
                    correlation_id,
                )

    @staticmethod
    def _select_workspace(status: dict, requested: str | None) -> dict | None:
        workspaces = list(status.get("workspaces") or [])
        if requested:
            exact = next((item for item in workspaces if item.get("id") == requested), None)
            return exact
        return workspaces[0] if workspaces else None

    @staticmethod
    def _event(
        event_type: RuntimeEventType,
        request: RuntimeTurn,
        summary: str,
        evidence: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            type=event_type,
            session_id=request.session_id,
            correlation_id=request.correlation_id,
            occurred_at=datetime.now(timezone.utc),
            summary=summary,
            evidence=evidence or {},
        )

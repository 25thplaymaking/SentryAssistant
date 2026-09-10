"""Enforce Sentry's request-scoped Chat capability boundary in Hermes.

Hermes 0.19.0 resolves API-server toolsets only from its process-wide platform
configuration. Sentry needs two lanes without a second agent process: Work keeps
that configured surface, while Chat supplies a deliberately smaller toolset for
one request. The Gateway sends ``sentry_enabled_toolsets`` over the authenticated,
container-private API. This patch validates the field and threads it to AIAgent.

Applied at image build time. It is idempotent and deliberately fails if any
verified anchor moves, so a Hermes upgrade cannot silently turn the Chat/Work
switch into cosmetic UI.
"""

from __future__ import annotations

import io
import sys
import sysconfig
from pathlib import Path

MARKER = "SENTRY PATCH: request-scoped toolsets"


def _candidate_paths() -> list[Path]:
    roots: list[str] = []
    for key in ("purelib", "platlib"):
        try:
            roots.append(sysconfig.get_path(key))
        except Exception:
            pass
    try:
        roots.append(sysconfig.get_path("purelib", scheme="posix_user"))
    except Exception:
        pass
    roots.extend(sys.path)

    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        candidate = Path(root) / "gateway" / "platforms" / "api_server.py"
        if candidate.is_file():
            out.append(candidate)
    return out


def _replace_once(src: str, label: str, anchor: str, replacement: str) -> str:
    count = src.count(anchor)
    if count != 1:
        raise RuntimeError(f"{label}: expected one verified anchor, found {count}")
    return src.replace(anchor, replacement, 1)


def _patched_source(src: str) -> str:
    src = _replace_once(
        src,
        "_create_agent signature",
        '''        gateway_session_key: Optional[str] = None,
        route: Optional[Dict[str, Any]] = None,
    ) -> Any:
''',
        '''        gateway_session_key: Optional[str] = None,
        route: Optional[Dict[str, Any]] = None,
        enabled_toolsets_override: Optional[List[str]] = None,
    ) -> Any:
''',
    )
    src = _replace_once(
        src,
        "configured toolsets",
        '''        user_config = _load_gateway_config()
        enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))
''',
        '''        user_config = _load_gateway_config()
        configured_toolsets = sorted(_get_platform_tools(user_config, "api_server"))
        # SENTRY PATCH: request-scoped toolsets. None preserves the configured
        # Work surface. An explicit list can only subtract from that surface;
        # it can never re-enable a toolset the operator disabled.
        enabled_toolsets = (
            [
                name
                for name in enabled_toolsets_override
                if name in configured_toolsets
            ]
            if enabled_toolsets_override is not None
            else configured_toolsets
        )
''',
    )
    src = _replace_once(
        src,
        "Responses request validation",
        '''        raw_input = body.get("input")
        if raw_input is None:
            return web.json_response(_openai_error("Missing 'input' field"), status=400)

        instructions = body.get("instructions")
        previous_response_id = body.get("previous_response_id")
''',
        '''        raw_input = body.get("input")
        if raw_input is None:
            return web.json_response(_openai_error("Missing 'input' field"), status=400)

        instructions = body.get("instructions")

        # SENTRY PATCH: validate the container-private Gateway extension before
        # it can influence AIAgent construction. Toolset names are identifiers,
        # not arbitrary config or command text.
        raw_toolsets_override = body.get("sentry_enabled_toolsets")
        enabled_toolsets_override = None
        if raw_toolsets_override is not None:
            if not isinstance(raw_toolsets_override, list) or len(raw_toolsets_override) > 32:
                return web.json_response(
                    _openai_error(
                        "'sentry_enabled_toolsets' must be an array of at most 32 toolset names",
                        param="sentry_enabled_toolsets",
                    ),
                    status=400,
                )
            normalized_toolsets = []
            for index, item in enumerate(raw_toolsets_override):
                if not isinstance(item, str):
                    return web.json_response(
                        _openai_error(
                            f"sentry_enabled_toolsets[{index}] must be a string",
                            param="sentry_enabled_toolsets",
                        ),
                        status=400,
                    )
                name = item.strip()
                if (
                    not name
                    or len(name) > 64
                    or not all(ch.isalnum() or ch in "_-" for ch in name)
                ):
                    return web.json_response(
                        _openai_error(
                            f"sentry_enabled_toolsets[{index}] is not a valid toolset name",
                            param="sentry_enabled_toolsets",
                        ),
                        status=400,
                    )
                if name not in normalized_toolsets:
                    normalized_toolsets.append(name)
            enabled_toolsets_override = normalized_toolsets

        previous_response_id = body.get("previous_response_id")
''',
    )
    src = _replace_once(
        src,
        "streaming Responses run",
        '''                ephemeral_system_prompt=instructions,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                tool_progress_callback=_on_tool_progress,
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                agent_ref=agent_ref,
                gateway_session_key=gateway_session_key,
                route=route,
            ))
''',
        '''                ephemeral_system_prompt=instructions,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                tool_progress_callback=_on_tool_progress,
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                agent_ref=agent_ref,
                gateway_session_key=gateway_session_key,
                route=route,
                enabled_toolsets_override=enabled_toolsets_override,
            ))
''',
    )
    src = _replace_once(
        src,
        "non-streaming Responses run",
        '''        async def _compute_response():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=instructions,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
                route=route,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
''',
        '''        async def _compute_response():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=instructions,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
                route=route,
                enabled_toolsets_override=enabled_toolsets_override,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
''',
    )
    src = _replace_once(
        src,
        "Responses idempotency fingerprint",
        '''                keys=["input", "instructions", "previous_response_id", "conversation", "model", "tools"],
''',
        '''                keys=["input", "instructions", "previous_response_id", "conversation", "model", "tools", "sentry_enabled_toolsets"],
''',
    )
    src = _replace_once(
        src,
        "_run_agent signature",
        '''    async def _run_agent(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        agent_ref: Optional[list] = None,
        gateway_session_key: Optional[str] = None,
        route: Optional[Dict[str, Any]] = None,
    ) -> tuple:
''',
        '''    async def _run_agent(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        ephemeral_system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
        stream_delta_callback=None,
        tool_progress_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        agent_ref: Optional[list] = None,
        gateway_session_key: Optional[str] = None,
        route: Optional[Dict[str, Any]] = None,
        enabled_toolsets_override: Optional[List[str]] = None,
    ) -> tuple:
''',
    )
    src = _replace_once(
        src,
        "AIAgent construction",
        '''                        tool_complete_callback=tool_complete_callback,
                        gateway_session_key=gateway_session_key,
                        route=route,
                    )
''',
        '''                        tool_complete_callback=tool_complete_callback,
                        gateway_session_key=gateway_session_key,
                        route=route,
                        enabled_toolsets_override=enabled_toolsets_override,
                    )
''',
    )
    return src


def main() -> int:
    targets = _candidate_paths()
    if not targets:
        print("FAILED: could not locate gateway/platforms/api_server.py", file=sys.stderr)
        return 1

    target = targets[0]
    src = io.open(target, encoding="utf-8").read()
    if MARKER in src:
        print(f"already patched: {target}")
        return 0

    try:
        patched = _patched_source(src)
    except RuntimeError as exc:
        print(
            f"FAILED: {exc}. Hermes has moved the API-server path; re-verify "
            "the Chat capability boundary instead of skipping it.",
            file=sys.stderr,
        )
        return 1

    io.open(target, "w", encoding="utf-8").write(patched)
    print(f"patched {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

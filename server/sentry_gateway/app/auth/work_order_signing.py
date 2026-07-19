"""Signed work orders.

The Gateway signs each dispatched work order. The Node re-validates everything
before executing: signature, audience, expiry, single-use nonce, requesting user,
profile, team membership, node ownership, workspace ID, harness, and mode. A Node
must never execute on the strength of a signature alone.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from ..workorders.transitions import WorkOrderMode
from .tokens import Audience, ISSUER, TokenError

#: Work orders are deliberately short lived. A queued order that outlives this
#: must be re-signed rather than silently executed later.
DEFAULT_WORK_ORDER_TTL = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SignedWorkOrder:
    token: str
    nonce: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NodeExpectation:
    """What the Node knows locally and will insist the order matches."""

    node_id: str
    node_owner_user_id: str
    #: Workspace IDs this Node has registered to fixed local paths. A raw remote
    #: filesystem path is never accepted.
    registered_workspaces: frozenset[str]
    allowed_harnesses: frozenset[str]
    team_members: frozenset[str] = frozenset()


def sign_work_order(
    *,
    signing_key: str,
    work_order_id: str,
    requesting_user_id: str,
    profile_id: str,
    team_id: str | None,
    execution_node_id: str,
    workspace_id: str,
    harness: str,
    mode: WorkOrderMode,
    correlation_id: str,
    ttl: timedelta = DEFAULT_WORK_ORDER_TTL,
    algorithm: str = "HS256",
) -> SignedWorkOrder:
    now = _now()
    expires_at = now + ttl
    nonce = secrets.token_urlsafe(24)
    token = jwt.encode(
        {
            "iss": ISSUER,
            "aud": Audience.WORK_ORDER.value,
            "sub": requesting_user_id,
            "wid": work_order_id,
            "pid": profile_id,
            "tid": team_id,
            "nid": execution_node_id,
            "wsp": workspace_id,
            "hns": harness,
            "mode": mode.value,
            "cid": correlation_id,
            "nonce": nonce,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        signing_key,
        algorithm=algorithm,
    )
    return SignedWorkOrder(token=token, nonce=nonce, expires_at=expires_at)


def validate_work_order(
    *,
    token: str,
    signing_key: str,
    expectation: NodeExpectation,
    seen_nonces: set[str],
    algorithm: str = "HS256",
) -> dict[str, Any]:
    """Full Node-side validation. Raises TokenError on any mismatch."""
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=[algorithm],
            audience=Audience.WORK_ORDER.value,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Work order has expired.") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenError("Work order audience is not accepted by this node.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Work order signature is invalid: {exc}") from exc

    nonce = claims.get("nonce")
    if not nonce:
        raise TokenError("Work order carries no nonce.")
    if nonce in seen_nonces:
        raise TokenError("Work order nonce has already been used.")

    # One person must never be able to select another person's private node.
    if claims.get("nid") != expectation.node_id:
        raise TokenError("Work order targets a different execution node.")

    workspace = claims.get("wsp")
    if workspace not in expectation.registered_workspaces:
        raise TokenError(
            "Workspace is not registered on this node. Raw remote paths are refused."
        )

    harness = claims.get("hns")
    if harness not in expectation.allowed_harnesses:
        raise TokenError(f"Harness {harness!r} is not enabled on this node.")

    try:
        mode = WorkOrderMode(claims.get("mode", ""))
    except ValueError as exc:
        raise TokenError("Work order mode is unknown.") from exc

    # Elevated work is only ever valid for the node's own owner, regardless of
    # what the Gateway signed.
    if mode is WorkOrderMode.APPROVED_ELEVATED:
        if claims.get("sub") != expectation.node_owner_user_id:
            raise TokenError(
                "Elevated work orders are only accepted from the node owner."
            )

    # Team work is only accepted from a current member of that team.
    team_id = claims.get("tid")
    if team_id and claims.get("sub") not in expectation.team_members:
        raise TokenError("Requesting user is not a current member of this team.")

    seen_nonces.add(nonce)
    return claims

"""Authoritative work-order state machine.

The Gateway's PostgreSQL record is the authority for workflow and authorization.
Runtime work boards (Hermes Kanban and friends) are rebuildable projections and
must never be able to move a work order on their own, so every transition is
decided here.

This module is deliberately pure: no database, no network, no runtime. That keeps
the rules testable and keeps the authorization decision in one reviewable place.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkOrderState(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    NEEDS_CLARIFICATION = "needsClarification"
    TRIAGED = "triaged"
    ASSIGNED = "assigned"
    IN_PROGRESS = "inProgress"
    NEEDS_INPUT = "needsInput"
    READY_FOR_REVIEW = "readyForReview"
    CHANGES_REQUESTED = "changesRequested"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class WorkOrderMode(StrEnum):
    READ_ONLY = "readOnly"
    WORKSPACE_WRITE = "workspaceWrite"
    APPROVED_ELEVATED = "approvedElevated"


class TeamRole(StrEnum):
    OWNER = "owner"
    COLLABORATOR = "collaborator"
    OBSERVER = "observer"


TERMINAL_STATES: frozenset[WorkOrderState] = frozenset(
    {
        WorkOrderState.RESOLVED,
        WorkOrderState.CLOSED,
        WorkOrderState.CANCELLED,
    }
)

#: States where execution assignment (assignee, node, harness, workspace) is required.
EXECUTION_STATES: frozenset[WorkOrderState] = frozenset(
    {WorkOrderState.ASSIGNED, WorkOrderState.IN_PROGRESS}
)

#: Transitions that begin a fresh immutable run. Earlier runs are always preserved.
RUN_STARTING_STATES: frozenset[WorkOrderState] = frozenset(
    {WorkOrderState.ASSIGNED, WorkOrderState.CHANGES_REQUESTED}
)


class TransitionError(Exception):
    """Raised when a transition is structurally invalid."""


class AuthorizationError(Exception):
    """Raised when the actor is not permitted to make an otherwise valid transition."""


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is attempting the transition, and how they relate to the work order."""

    user_id: str
    role: TeamRole
    is_requester: bool = False
    is_assignee: bool = False
    #: True when the actor owns the execution node the order is assigned to.
    is_node_owner: bool = False


@dataclass(frozen=True, slots=True)
class WorkOrderSnapshot:
    """The fields a transition decision depends on."""

    state: WorkOrderState
    mode: WorkOrderMode
    assignee_user_id: str | None = None
    execution_node_id: str | None = None
    harness: str | None = None
    workspace_id: str | None = None
    #: Set when the node owner has pre-approved this team/workspace/mode combination.
    has_policy_grant: bool = False


# Structurally legal moves. Authorization is a separate, stricter gate applied on top.
_ALLOWED: dict[WorkOrderState, frozenset[WorkOrderState]] = {
    WorkOrderState.DRAFT: frozenset(
        {WorkOrderState.SUBMITTED, WorkOrderState.CANCELLED}
    ),
    WorkOrderState.SUBMITTED: frozenset(
        {
            WorkOrderState.NEEDS_CLARIFICATION,
            WorkOrderState.TRIAGED,
            WorkOrderState.CANCELLED,
        }
    ),
    WorkOrderState.NEEDS_CLARIFICATION: frozenset(
        {WorkOrderState.SUBMITTED, WorkOrderState.CANCELLED}
    ),
    WorkOrderState.TRIAGED: frozenset(
        {WorkOrderState.ASSIGNED, WorkOrderState.CANCELLED}
    ),
    WorkOrderState.ASSIGNED: frozenset(
        {
            WorkOrderState.IN_PROGRESS,
            WorkOrderState.NEEDS_INPUT,
            WorkOrderState.FAILED,
            WorkOrderState.CANCELLED,
        }
    ),
    WorkOrderState.IN_PROGRESS: frozenset(
        {
            WorkOrderState.NEEDS_INPUT,
            WorkOrderState.READY_FOR_REVIEW,
            WorkOrderState.FAILED,
            WorkOrderState.CANCELLED,
        }
    ),
    WorkOrderState.NEEDS_INPUT: frozenset(
        {
            WorkOrderState.IN_PROGRESS,
            WorkOrderState.FAILED,
            WorkOrderState.CANCELLED,
        }
    ),
    WorkOrderState.READY_FOR_REVIEW: frozenset(
        {
            WorkOrderState.RESOLVED,
            WorkOrderState.CHANGES_REQUESTED,
            WorkOrderState.CANCELLED,
        }
    ),
    WorkOrderState.CHANGES_REQUESTED: frozenset(
        {
            WorkOrderState.IN_PROGRESS,
            WorkOrderState.ASSIGNED,
            WorkOrderState.CANCELLED,
        }
    ),
    # A failed run may be retried by reassignment, but never silently resurrected.
    WorkOrderState.FAILED: frozenset(
        {WorkOrderState.ASSIGNED, WorkOrderState.CANCELLED, WorkOrderState.CLOSED}
    ),
    WorkOrderState.RESOLVED: frozenset({WorkOrderState.CLOSED}),
    WorkOrderState.CLOSED: frozenset(),
    WorkOrderState.CANCELLED: frozenset(),
}


def allowed_targets(state: WorkOrderState) -> frozenset[WorkOrderState]:
    return _ALLOWED[state]


def is_terminal(state: WorkOrderState) -> bool:
    return state in TERMINAL_STATES


def starts_new_run(target: WorkOrderState) -> bool:
    """Whether entering this state must create a fresh immutable run."""
    return target in RUN_STARTING_STATES


def _require_execution_fields(order: WorkOrderSnapshot, target: WorkOrderState) -> None:
    if target not in EXECUTION_STATES:
        return
    missing = [
        name
        for name, value in (
            ("assignee_user_id", order.assignee_user_id),
            ("execution_node_id", order.execution_node_id),
            ("harness", order.harness),
            ("workspace_id", order.workspace_id),
        )
        if not value
    ]
    if missing:
        raise TransitionError(
            f"{target} requires execution assignment; missing: {', '.join(missing)}"
        )


def _authorize(actor: Actor, order: WorkOrderSnapshot, target: WorkOrderState) -> None:
    # Observers may read shared results but never dispatch, approve, or decide.
    if actor.role is TeamRole.OBSERVER:
        raise AuthorizationError("Observers cannot change work-order state.")

    if target is WorkOrderState.SUBMITTED and order.state is WorkOrderState.DRAFT:
        # Only the person who wrote the draft may submit it on their own behalf.
        if not actor.is_requester:
            raise AuthorizationError("Only the requester can submit their draft.")

    if target is WorkOrderState.TRIAGED or target is WorkOrderState.ASSIGNED:
        if actor.role is not TeamRole.OWNER and not actor.is_requester:
            raise AuthorizationError("Triage and assignment require owner permission.")

    if target is WorkOrderState.RESOLVED:
        # Accepting a result is the requester's or an owner's decision, never the
        # implementer's — otherwise a run could approve its own work.
        if not (actor.is_requester or actor.role is TeamRole.OWNER):
            raise AuthorizationError(
                "Only the requester or an owner can accept a result."
            )

    if target is WorkOrderState.CHANGES_REQUESTED:
        if not (actor.is_requester or actor.role is TeamRole.OWNER):
            raise AuthorizationError(
                "Only the requester or an owner can request changes."
            )

    # A colleague's request must never silently authorize writes on someone
    # else's machine. Elevated work is never granted by stored policy.
    if target in EXECUTION_STATES:
        if order.mode is WorkOrderMode.APPROVED_ELEVATED:
            if not actor.is_node_owner:
                raise AuthorizationError(
                    "Elevated execution requires the node owner's explicit approval."
                )
        elif order.mode is WorkOrderMode.WORKSPACE_WRITE:
            if not (actor.is_node_owner or order.has_policy_grant):
                raise AuthorizationError(
                    "Workspace-write execution requires node-owner approval or a "
                    "stored policy grant for this team, workspace, and mode."
                )


def validate_transition(
    actor: Actor, order: WorkOrderSnapshot, target: WorkOrderState
) -> None:
    """Raise if `actor` may not move `order` to `target`. Returns None when allowed."""
    if order.state is target:
        raise TransitionError(f"Work order is already {target}.")

    if is_terminal(order.state) and order.state is not WorkOrderState.RESOLVED:
        raise TransitionError(f"{order.state} is terminal and cannot transition.")

    if target not in _ALLOWED[order.state]:
        raise TransitionError(f"Cannot move from {order.state} to {target}.")

    _require_execution_fields(order, target)
    _authorize(actor, order, target)


def can_transition(
    actor: Actor, order: WorkOrderSnapshot, target: WorkOrderState
) -> bool:
    try:
        validate_transition(actor, order, target)
    except (TransitionError, AuthorizationError):
        return False
    return True

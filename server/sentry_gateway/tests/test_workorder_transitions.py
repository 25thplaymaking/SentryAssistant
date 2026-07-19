import pytest

from app.workorders.transitions import (
    Actor,
    AuthorizationError,
    TeamRole,
    TransitionError,
    WorkOrderMode,
    WorkOrderSnapshot,
    WorkOrderState,
    allowed_targets,
    can_transition,
    is_terminal,
    starts_new_run,
    validate_transition,
)

REQUESTER = Actor("u-req", TeamRole.COLLABORATOR, is_requester=True)
OWNER = Actor("u-own", TeamRole.OWNER, is_node_owner=True)
COLLABORATOR = Actor("u-col", TeamRole.COLLABORATOR)
OBSERVER = Actor("u-obs", TeamRole.OBSERVER)


def assigned_order(
    state=WorkOrderState.TRIAGED,
    mode=WorkOrderMode.READ_ONLY,
    **kwargs,
) -> WorkOrderSnapshot:
    base = dict(
        assignee_user_id="u-col",
        execution_node_id="node-1",
        harness="codex",
        workspace_id="ws-1",
    )
    base.update(kwargs)
    return WorkOrderSnapshot(state=state, mode=mode, **base)


class TestStructure:
    def test_every_state_has_a_transition_entry(self):
        for state in WorkOrderState:
            assert allowed_targets(state) is not None

    def test_closed_and_cancelled_are_dead_ends(self):
        assert allowed_targets(WorkOrderState.CLOSED) == frozenset()
        assert allowed_targets(WorkOrderState.CANCELLED) == frozenset()

    def test_terminal_states(self):
        assert is_terminal(WorkOrderState.RESOLVED)
        assert is_terminal(WorkOrderState.CLOSED)
        assert is_terminal(WorkOrderState.CANCELLED)
        assert not is_terminal(WorkOrderState.IN_PROGRESS)

    def test_cancelled_cannot_be_revived(self):
        order = WorkOrderSnapshot(WorkOrderState.CANCELLED, WorkOrderMode.READ_ONLY)
        with pytest.raises(TransitionError):
            validate_transition(OWNER, order, WorkOrderState.IN_PROGRESS)

    def test_resolved_may_only_close(self):
        order = WorkOrderSnapshot(WorkOrderState.RESOLVED, WorkOrderMode.READ_ONLY)
        assert can_transition(OWNER, order, WorkOrderState.CLOSED)
        assert not can_transition(OWNER, order, WorkOrderState.IN_PROGRESS)

    def test_transition_to_same_state_is_rejected(self):
        order = WorkOrderSnapshot(WorkOrderState.DRAFT, WorkOrderMode.READ_ONLY)
        with pytest.raises(TransitionError):
            validate_transition(REQUESTER, order, WorkOrderState.DRAFT)

    def test_skipping_the_pipeline_is_rejected(self):
        order = WorkOrderSnapshot(WorkOrderState.DRAFT, WorkOrderMode.READ_ONLY)
        with pytest.raises(TransitionError):
            validate_transition(OWNER, order, WorkOrderState.RESOLVED)


class TestExecutionFields:
    """Drafts may omit execution assignment; Assigned/InProgress may not."""

    def test_assignment_requires_full_execution_identity(self):
        order = WorkOrderSnapshot(
            WorkOrderState.TRIAGED, WorkOrderMode.READ_ONLY, assignee_user_id="u-col"
        )
        with pytest.raises(TransitionError) as exc:
            validate_transition(OWNER, order, WorkOrderState.ASSIGNED)
        assert "execution_node_id" in str(exc.value)
        assert "harness" in str(exc.value)
        assert "workspace_id" in str(exc.value)

    def test_assignment_succeeds_with_full_identity(self):
        validate_transition(OWNER, assigned_order(), WorkOrderState.ASSIGNED)

    def test_in_progress_requires_execution_identity(self):
        order = WorkOrderSnapshot(WorkOrderState.ASSIGNED, WorkOrderMode.READ_ONLY)
        with pytest.raises(TransitionError):
            validate_transition(OWNER, order, WorkOrderState.IN_PROGRESS)


class TestAuthorization:
    def test_only_requester_may_submit_their_draft(self):
        order = WorkOrderSnapshot(WorkOrderState.DRAFT, WorkOrderMode.READ_ONLY)
        validate_transition(REQUESTER, order, WorkOrderState.SUBMITTED)
        with pytest.raises(AuthorizationError):
            validate_transition(COLLABORATOR, order, WorkOrderState.SUBMITTED)

    def test_observers_can_never_change_state(self):
        for state, target in [
            (WorkOrderState.DRAFT, WorkOrderState.SUBMITTED),
            (WorkOrderState.SUBMITTED, WorkOrderState.TRIAGED),
            (WorkOrderState.READY_FOR_REVIEW, WorkOrderState.RESOLVED),
        ]:
            order = WorkOrderSnapshot(state, WorkOrderMode.READ_ONLY)
            with pytest.raises(AuthorizationError):
                validate_transition(OBSERVER, order, target)

    def test_plain_collaborator_cannot_triage(self):
        order = WorkOrderSnapshot(WorkOrderState.SUBMITTED, WorkOrderMode.READ_ONLY)
        with pytest.raises(AuthorizationError):
            validate_transition(COLLABORATOR, order, WorkOrderState.TRIAGED)

    def test_implementer_cannot_accept_their_own_work(self):
        """The assignee must not be able to approve the result they produced."""
        order = WorkOrderSnapshot(
            WorkOrderState.READY_FOR_REVIEW, WorkOrderMode.READ_ONLY
        )
        assignee = Actor("u-col", TeamRole.COLLABORATOR, is_assignee=True)
        with pytest.raises(AuthorizationError):
            validate_transition(assignee, order, WorkOrderState.RESOLVED)

    def test_requester_and_owner_may_accept(self):
        order = WorkOrderSnapshot(
            WorkOrderState.READY_FOR_REVIEW, WorkOrderMode.READ_ONLY
        )
        validate_transition(REQUESTER, order, WorkOrderState.RESOLVED)
        validate_transition(OWNER, order, WorkOrderState.RESOLVED)

    def test_only_requester_or_owner_may_request_changes(self):
        order = WorkOrderSnapshot(
            WorkOrderState.READY_FOR_REVIEW, WorkOrderMode.READ_ONLY
        )
        validate_transition(REQUESTER, order, WorkOrderState.CHANGES_REQUESTED)
        with pytest.raises(AuthorizationError):
            validate_transition(COLLABORATOR, order, WorkOrderState.CHANGES_REQUESTED)


class TestExecutionSafety:
    """A colleague's request must never silently authorize writes on Bryce's machine."""

    def test_workspace_write_needs_node_owner_or_stored_grant(self):
        order = assigned_order(mode=WorkOrderMode.WORKSPACE_WRITE)
        non_owner = Actor("u-req", TeamRole.OWNER, is_requester=True, is_node_owner=False)
        with pytest.raises(AuthorizationError):
            validate_transition(non_owner, order, WorkOrderState.ASSIGNED)

    def test_workspace_write_allowed_with_stored_policy_grant(self):
        order = assigned_order(
            mode=WorkOrderMode.WORKSPACE_WRITE, has_policy_grant=True
        )
        non_owner = Actor("u-req", TeamRole.OWNER, is_requester=True, is_node_owner=False)
        validate_transition(non_owner, order, WorkOrderState.ASSIGNED)

    def test_workspace_write_allowed_for_node_owner(self):
        order = assigned_order(mode=WorkOrderMode.WORKSPACE_WRITE)
        validate_transition(OWNER, order, WorkOrderState.ASSIGNED)

    def test_elevated_always_requires_node_owner_even_with_grant(self):
        """A stored grant must never be enough to authorize elevated actions."""
        order = assigned_order(
            mode=WorkOrderMode.APPROVED_ELEVATED, has_policy_grant=True
        )
        non_owner = Actor("u-req", TeamRole.OWNER, is_requester=True, is_node_owner=False)
        with pytest.raises(AuthorizationError):
            validate_transition(non_owner, order, WorkOrderState.ASSIGNED)

    def test_read_only_execution_needs_no_node_ownership(self):
        order = assigned_order(mode=WorkOrderMode.READ_ONLY)
        requester_owner = Actor(
            "u-req", TeamRole.OWNER, is_requester=True, is_node_owner=False
        )
        validate_transition(requester_owner, order, WorkOrderState.ASSIGNED)


class TestRunAccounting:
    def test_assignment_and_changes_requested_start_fresh_runs(self):
        assert starts_new_run(WorkOrderState.ASSIGNED)
        assert starts_new_run(WorkOrderState.CHANGES_REQUESTED)

    def test_ordinary_progress_does_not_start_a_run(self):
        assert not starts_new_run(WorkOrderState.IN_PROGRESS)
        assert not starts_new_run(WorkOrderState.READY_FOR_REVIEW)
        assert not starts_new_run(WorkOrderState.RESOLVED)

    def test_changes_requested_returns_to_execution(self):
        order = WorkOrderSnapshot(
            WorkOrderState.CHANGES_REQUESTED, WorkOrderMode.READ_ONLY
        )
        assert WorkOrderState.IN_PROGRESS in allowed_targets(order.state)

    def test_failed_may_be_reassigned_but_not_resolved_directly(self):
        order = assigned_order(state=WorkOrderState.FAILED)
        validate_transition(OWNER, order, WorkOrderState.ASSIGNED)
        with pytest.raises(TransitionError):
            validate_transition(OWNER, order, WorkOrderState.RESOLVED)

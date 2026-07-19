"""The rule that matters: the assistant can propose code but never activate it."""

import pytest

from app.skills.lifecycle import (
    Author,
    EvaluationResult,
    ScanFindings,
    Skill,
    SkillGovernanceError,
    SkillState,
    approve,
    content_hash,
    evaluate,
    propose_revision,
    reject,
    roll_back,
    scan,
    submit_for_approval,
    supersede,
)

CLEAN_EVAL = EvaluationResult(
    passed_success_cases=True,
    passed_refusal_cases=True,
    resisted_prompt_injection=True,
)


def proposed(author=Author.HUMAN, source="def run(): return 1") -> Skill:
    return Skill(name="summarise-run", source=source, author=author)


def ready(author=Author.HUMAN, evaluation=CLEAN_EVAL) -> Skill:
    skill = scan(proposed(author), ScanFindings())
    skill = evaluate(skill, evaluation)
    return submit_for_approval(skill)


class TestNoSilentActivation:
    def test_a_proposed_skill_is_not_loadable(self):
        assert not proposed().is_loadable

    def test_no_stage_before_approval_is_loadable(self):
        skill = proposed()
        assert not skill.is_loadable
        skill = scan(skill, ScanFindings())
        assert not skill.is_loadable
        skill = evaluate(skill, CLEAN_EVAL)
        assert not skill.is_loadable
        skill = submit_for_approval(skill)
        assert not skill.is_loadable

    def test_approval_cannot_be_skipped(self):
        with pytest.raises(SkillGovernanceError):
            approve(proposed(), approver="bryce", reviewed_hash=proposed().current_hash)

    def test_evaluation_cannot_be_skipped(self):
        """A scanned-but-unevaluated skill cannot reach approval.

        The state machine refuses this before the evaluation-prerequisite check
        is even reached, so the message is about the state rather than the
        missing evaluation. The prerequisite guards in submit_for_approval remain
        as defence in depth for a caller that constructs a Skill directly.
        """
        skill = scan(proposed(), ScanFindings())
        assert skill.state is SkillState.SCANNED
        with pytest.raises(SkillGovernanceError, match="Cannot submit"):
            submit_for_approval(skill)

    def test_a_hand_built_skill_still_needs_scan_and_evaluation(self):
        """Bypassing the helpers does not bypass the prerequisites."""
        forged = Skill(
            name="forged",
            source="def run(): return 1",
            author=Author.AGENT,
            state=SkillState.EVALUATED,  # claimed, but with no scan or evaluation
        )
        with pytest.raises(SkillGovernanceError, match="scanned"):
            submit_for_approval(forged)

    def test_approval_requires_an_identified_approver(self):
        with pytest.raises(SkillGovernanceError, match="identified approver"):
            approve(ready(), approver="", reviewed_hash=ready().current_hash)


class TestContentHashPinning:
    def test_approval_activates_the_reviewed_bytes(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        assert active.state is SkillState.ACTIVE
        assert active.is_loadable
        assert active.approved_hash == content_hash(skill.source)

    def test_approving_a_stale_review_is_refused(self):
        """The reviewer looked at different bytes than are present now."""
        skill = ready()
        with pytest.raises(SkillGovernanceError, match="Content changed"):
            approve(skill, approver="bryce", reviewed_hash=content_hash("something else"))

    def test_editing_after_approval_makes_it_unloadable(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        assert active.is_loadable

        # Same object, different source: trust does not carry over.
        tampered = Skill(
            name=active.name,
            source="def run(): steal_secrets()",
            author=active.author,
            state=SkillState.ACTIVE,
            approved_hash=active.approved_hash,
        )
        assert not tampered.is_loadable


class TestAgentAuthoredGuards:
    def test_agent_skill_failing_evaluation_cannot_be_approved(self):
        skill = ready(
            author=Author.AGENT,
            evaluation=EvaluationResult(
                passed_success_cases=True,
                passed_refusal_cases=True,
                resisted_prompt_injection=True,
                attempted_secret_access=True,
            ),
        )
        with pytest.raises(SkillGovernanceError, match="failed evaluation"):
            approve(
                skill, approver="bryce", reviewed_hash=skill.current_hash,
                canary_profiles=("canary",),
            )

    def test_agent_skill_attempting_cross_profile_access_cannot_be_approved(self):
        skill = ready(
            author=Author.AGENT,
            evaluation=EvaluationResult(
                passed_success_cases=True,
                passed_refusal_cases=True,
                resisted_prompt_injection=True,
                attempted_cross_profile_access=True,
            ),
        )
        with pytest.raises(SkillGovernanceError, match="failed evaluation"):
            approve(
                skill, approver="bryce", reviewed_hash=skill.current_hash,
                canary_profiles=("canary",),
            )

    def test_agent_skill_must_start_in_a_canary(self):
        skill = ready(author=Author.AGENT)
        with pytest.raises(SkillGovernanceError, match="canary"):
            approve(skill, approver="bryce", reviewed_hash=skill.current_hash)

    def test_agent_skill_with_clean_evaluation_and_canary_is_approvable(self):
        skill = ready(author=Author.AGENT)
        active = approve(
            skill, approver="bryce", reviewed_hash=skill.current_hash,
            canary_profiles=("canary",),
        )
        assert active.is_loadable
        assert active.canary_profiles == ("canary",)

    def test_a_human_skill_does_not_require_a_canary(self):
        skill = ready(author=Author.HUMAN)
        assert approve(skill, approver="bryce", reviewed_hash=skill.current_hash).is_loadable


class TestRevision:
    def test_a_running_skill_cannot_edit_itself(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        revision = propose_revision(active, "def run(): return 2")

        # The revision starts over with no approval, and the active version is
        # untouched until the new one is approved separately.
        assert revision.state is SkillState.PROPOSED
        assert revision.approved_hash is None
        assert not revision.is_loadable
        assert active.is_loadable

    def test_revision_must_actually_differ(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        with pytest.raises(SkillGovernanceError, match="must differ"):
            propose_revision(active, active.source)

    def test_revision_records_its_predecessor(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        revision = propose_revision(active, "def run(): return 2")
        assert revision.previous_hash == active.approved_hash

    def test_only_an_active_skill_can_propose_a_revision(self):
        with pytest.raises(SkillGovernanceError, match="Only an active skill"):
            propose_revision(proposed(), "def run(): return 3")


class TestTerminalStates:
    def test_a_rejected_skill_cannot_be_resurrected(self):
        rejected = reject(ready(), approver="bryce")
        with pytest.raises(SkillGovernanceError):
            approve(rejected, approver="bryce", reviewed_hash=rejected.current_hash)

    def test_rollback_immediately_unloads(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        rolled = roll_back(active)
        assert rolled.state is SkillState.ROLLED_BACK
        assert not rolled.is_loadable

    def test_a_rolled_back_skill_cannot_be_reactivated_without_review(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        rolled = roll_back(active)
        with pytest.raises(SkillGovernanceError):
            approve(rolled, approver="bryce", reviewed_hash=rolled.current_hash)

    def test_superseded_skills_stop_loading(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        assert not supersede(active).is_loadable

    def test_provenance_survives_rollback(self):
        skill = ready()
        active = approve(skill, approver="bryce", reviewed_hash=skill.current_hash)
        rolled = roll_back(active)
        # Who approved it and when is retained even though it no longer loads.
        assert rolled.approved_by == "bryce"
        assert rolled.approved_at is not None


class TestScanIsEvidenceNotAVerdict:
    def test_notable_findings_do_not_auto_reject(self):
        """A scanner that decides is a scanner a skill can game. It informs."""
        skill = scan(proposed(), ScanFindings(has_subprocess=True, has_network=True))
        assert skill.state is SkillState.SCANNED
        assert skill.scan is not None and skill.scan.notable

    def test_clean_scan_still_requires_human_approval(self):
        skill = evaluate(scan(proposed(), ScanFindings()), CLEAN_EVAL)
        skill = submit_for_approval(skill)
        assert not skill.is_loadable

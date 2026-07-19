"""Skill governance.

The assistant may propose reusable procedures, but it may never activate its own
code. Every version travels the same path — proposed, scanned, evaluated,
awaiting approval, then active — and only a human owner moves it across the
approval boundary.

Activation is by content hash. Approving a proposal approves *exactly* the bytes
that were reviewed; if the content changes afterwards the approval no longer
applies and the skill will not load.

This module is pure policy. The scanner and evaluator produce evidence for a
reviewer; neither is treated as a security boundary, because a scanner that a
skill can predict is a scanner a skill can evade.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum


class SkillState(StrEnum):
    PROPOSED = "proposed"
    SCANNED = "scanned"
    EVALUATED = "evaluated"
    AWAITING_APPROVAL = "awaitingApproval"
    REJECTED = "rejected"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolledBack"


class Author(StrEnum):
    """Who wrote it. Agent-authored skills carry extra guards."""

    HUMAN = "human"
    AGENT = "agent"
    IMPORTED = "imported"


#: States from which a skill can never reach production without starting over.
TERMINAL: frozenset[SkillState] = frozenset(
    {SkillState.REJECTED, SkillState.ROLLED_BACK, SkillState.SUPERSEDED}
)

_ALLOWED: dict[SkillState, frozenset[SkillState]] = {
    SkillState.PROPOSED: frozenset({SkillState.SCANNED, SkillState.REJECTED}),
    SkillState.SCANNED: frozenset({SkillState.EVALUATED, SkillState.REJECTED}),
    SkillState.EVALUATED: frozenset({SkillState.AWAITING_APPROVAL, SkillState.REJECTED}),
    SkillState.AWAITING_APPROVAL: frozenset({SkillState.ACTIVE, SkillState.REJECTED}),
    SkillState.ACTIVE: frozenset({SkillState.SUPERSEDED, SkillState.ROLLED_BACK}),
    SkillState.REJECTED: frozenset(),
    SkillState.SUPERSEDED: frozenset(),
    SkillState.ROLLED_BACK: frozenset(),
}


class SkillGovernanceError(Exception):
    """Raised when a transition would violate the governance rules."""


def content_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ScanFindings:
    """Evidence, not a verdict. Recorded for a reviewer to read."""

    has_subprocess: bool = False
    has_network: bool = False
    has_credential_access: bool = False
    has_encoded_payload: bool = False
    has_install_time_code: bool = False
    undeclared_imports: tuple[str, ...] = ()

    @property
    def notable(self) -> bool:
        return any(
            [
                self.has_subprocess,
                self.has_network,
                self.has_credential_access,
                self.has_encoded_payload,
                self.has_install_time_code,
                bool(self.undeclared_imports),
            ]
        )


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    passed_success_cases: bool = False
    passed_refusal_cases: bool = False
    resisted_prompt_injection: bool = False
    attempted_secret_access: bool = False
    attempted_cross_profile_access: bool = False

    @property
    def clean(self) -> bool:
        return (
            self.passed_success_cases
            and self.passed_refusal_cases
            and self.resisted_prompt_injection
            and not self.attempted_secret_access
            and not self.attempted_cross_profile_access
        )


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    source: str
    author: Author
    state: SkillState = SkillState.PROPOSED
    approved_hash: str | None = None
    scan: ScanFindings | None = None
    evaluation: EvaluationResult | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    previous_hash: str | None = None
    #: Canary profiles receive an activation before it reaches the rest.
    canary_profiles: tuple[str, ...] = field(default=())

    @property
    def current_hash(self) -> str:
        return content_hash(self.source)

    @property
    def is_loadable(self) -> bool:
        """Whether a runtime may load this skill right now.

        Active *and* byte-identical to what was approved. If the source changed
        after approval this is false, so an edited skill fails closed instead of
        inheriting its predecessor's trust.
        """
        return (
            self.state is SkillState.ACTIVE
            and self.approved_hash is not None
            and self.approved_hash == self.current_hash
        )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SkillGovernanceError(message)


def scan(skill: Skill, findings: ScanFindings) -> Skill:
    _require(
        SkillState.SCANNED in _ALLOWED[skill.state],
        f"Cannot scan a skill in state {skill.state}.",
    )
    return replace(skill, state=SkillState.SCANNED, scan=findings)


def evaluate(skill: Skill, result: EvaluationResult) -> Skill:
    _require(
        SkillState.EVALUATED in _ALLOWED[skill.state],
        f"Cannot evaluate a skill in state {skill.state}.",
    )
    return replace(skill, state=SkillState.EVALUATED, evaluation=result)


def submit_for_approval(skill: Skill) -> Skill:
    _require(
        SkillState.AWAITING_APPROVAL in _ALLOWED[skill.state],
        f"Cannot submit a skill in state {skill.state} for approval.",
    )
    _require(skill.scan is not None, "A skill must be scanned before approval.")
    _require(skill.evaluation is not None, "A skill must be evaluated before approval.")
    return replace(skill, state=SkillState.AWAITING_APPROVAL)


def approve(
    skill: Skill,
    *,
    approver: str,
    reviewed_hash: str,
    canary_profiles: tuple[str, ...] = (),
    at: datetime | None = None,
) -> Skill:
    """Activate exactly the reviewed bytes.

    `reviewed_hash` is what the human actually looked at. If the source has moved
    since, approval is refused rather than silently applied to new content.
    """
    _require(
        SkillState.ACTIVE in _ALLOWED[skill.state],
        f"Cannot approve a skill in state {skill.state}.",
    )
    _require(bool(approver), "Approval requires an identified approver.")
    _require(
        reviewed_hash == skill.current_hash,
        "Content changed since review; approval refused.",
    )

    # An agent-authored skill that tried to reach secrets or another profile
    # during evaluation is never approvable, whatever the reviewer clicks.
    if skill.author is Author.AGENT:
        _require(skill.evaluation is not None, "Agent-authored skills require evaluation.")
        _require(
            skill.evaluation.clean,
            "Agent-authored skill failed evaluation and cannot be approved.",
        )
        _require(
            bool(canary_profiles),
            "Agent-authored skills must activate into a canary profile first.",
        )

    return replace(
        skill,
        state=SkillState.ACTIVE,
        approved_hash=skill.current_hash,
        approved_by=approver,
        approved_at=at or datetime.now(timezone.utc),
        canary_profiles=canary_profiles,
    )


def reject(skill: Skill, *, approver: str) -> Skill:
    _require(
        SkillState.REJECTED in _ALLOWED[skill.state],
        f"Cannot reject a skill in state {skill.state}.",
    )
    _require(bool(approver), "Rejection requires an identified reviewer.")
    return replace(skill, state=SkillState.REJECTED)


def propose_revision(skill: Skill, new_source: str) -> Skill:
    """A running skill may propose a successor, never edit itself.

    The revision starts at the beginning of the lifecycle with no approval, and
    the active version is untouched until the new one is approved separately.
    """
    _require(
        skill.state is SkillState.ACTIVE,
        "Only an active skill can propose a revision.",
    )
    _require(
        content_hash(new_source) != skill.current_hash,
        "A revision must differ from the active version.",
    )
    return Skill(
        name=skill.name,
        source=new_source,
        author=skill.author,
        state=SkillState.PROPOSED,
        approved_hash=None,
        previous_hash=skill.approved_hash,
    )


def roll_back(skill: Skill) -> Skill:
    """Deactivate immediately, preserving provenance for the audit trail."""
    _require(
        SkillState.ROLLED_BACK in _ALLOWED[skill.state],
        f"Cannot roll back a skill in state {skill.state}.",
    )
    return replace(skill, state=SkillState.ROLLED_BACK, approved_hash=None)


def supersede(skill: Skill) -> Skill:
    _require(
        SkillState.SUPERSEDED in _ALLOWED[skill.state],
        f"Cannot supersede a skill in state {skill.state}.",
    )
    return replace(skill, state=SkillState.SUPERSEDED, approved_hash=None)

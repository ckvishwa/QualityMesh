"""Deterministic, offline evaluation of evidence completeness against a release contract.

Implements `PRD.md` FR-6. For each `RequiredEvidenceItem` in a contract, completeness
evaluation answers: does enough correctly-bound, fresh evidence exist to satisfy it?

This module does not parse evidence payloads, re-verify binding, authenticate anything,
or decide a release outcome. It consumes the outputs of earlier stages —
:class:`~qualitymesh.schemas.evidence.EvidenceEnvelope` (Task 1.3) and
:class:`~qualitymesh.binding.verifier.BindingVerificationResult` (Task 1.4) — bundled
into an :class:`EvidenceCandidate`, and produces a structured, per-requirement judgement.
A candidate whose binding is `NOT_BOUND` or `INSUFFICIENT_INFORMATION` never counts,
regardless of anything else about it: only a `BOUND` candidate is even considered for
kind/subject/freshness checks.

`CompletenessOutcome.SATISFIED` is not `Decision.PASS`. Complete evidence is necessary
but not sufficient for a release to pass — a release with fully satisfied evidence can
still `BLOCK` on a rule this module never evaluates (an exposed secret, a critical
vulnerability, a failed critical journey). `CompletenessOutcome.INSUFFICIENT_EVIDENCE` is
intended to feed `Decision.INSUFFICIENT_EVIDENCE` directly in the future policy evaluator
(FR-7); `SATISFIED` feeds into further evaluation, not directly into `PASS`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import field_validator

from qualitymesh.binding.verifier import BindingStatus, BindingVerificationResult
from qualitymesh.schemas.contract import (
    PositiveStrictInt,
    ReleaseContract,
    RequiredEvidenceItem,
    Slug,
)
from qualitymesh.schemas.evidence import EvidenceEnvelope, StrictEvidenceModel


class EvidenceCandidate(StrictEvidenceModel):
    """One piece of evidence, already parsed and binding-checked, offered to satisfy a
    required evidence item.

    Bundles exactly what completeness evaluation needs and nothing this repository does
    not already define elsewhere: the envelope (kind, subject, payload digest — Task
    1.3), the binding result (Task 1.4), which requirement it is offered against, and
    when it was observed. No component in the repository computes `observed_at`
    automatically — nothing here calls a clock. It must be supplied already
    timezone-aware; a naive value is rejected at construction, not silently assumed to
    be UTC or local time.
    """

    requirement_id: Slug
    envelope: EvidenceEnvelope
    binding_result: BindingVerificationResult
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware, not naive")
        return value


class CandidateRejectionReason(StrEnum):
    """Why one candidate did not qualify toward its requirement's `minimumItems`."""

    UNKNOWN_REQUIREMENT = "unknown-requirement"
    EVIDENCE_KIND_MISMATCH = "evidence-kind-mismatch"
    EVIDENCE_SUBJECT_MISMATCH = "evidence-subject-mismatch"
    NOT_BOUND = "not-bound"
    INSUFFICIENT_BINDING_INFORMATION = "insufficient-binding-information"
    FUTURE_DATED = "future-dated"
    STALE = "stale"
    DUPLICATE_OF_ANOTHER_CANDIDATE = "duplicate-of-another-candidate"


class CandidateEvaluation(StrictEvidenceModel):
    """The per-candidate verdict: whether it counted, and why not if it didn't.

    Used both for candidates associated with a real requirement and for candidates
    referencing a `requirement_id` the contract does not declare — every supplied
    candidate produces exactly one of these, so none can silently disappear.
    """

    envelope_digest: str
    requirement_id: Slug
    qualifies: bool
    reason: CandidateRejectionReason | None


class RequirementCompletenessResult(StrictEvidenceModel):
    """Whether one `RequiredEvidenceItem` is satisfied, and the evidence behind that
    judgement."""

    requirement_id: Slug
    minimum_items: PositiveStrictInt
    qualifying_count: int
    total_candidate_count: int
    satisfied: bool
    candidate_evaluations: tuple[CandidateEvaluation, ...]


class CompletenessOutcome(StrEnum):
    """The overall result of one `evaluate_completeness` call.

    Deliberately not `Decision`: `SATISFIED` is not `Decision.PASS` — complete evidence
    is necessary but not sufficient for a release to pass policy evaluation (FR-7).
    `INSUFFICIENT_EVIDENCE` shares its name and meaning with `Decision.INSUFFICIENT_EVIDENCE`
    because it is meant to feed that outcome directly once the policy evaluator exists.
    """

    SATISFIED = "satisfied"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"


class CompletenessResult(StrictEvidenceModel):
    """The full result of evaluating a contract's required evidence against a set of
    candidates as of a specific instant."""

    outcome: CompletenessOutcome
    as_of: datetime
    requirement_results: tuple[RequirementCompletenessResult, ...]
    unmatched_candidates: tuple[CandidateEvaluation, ...]


def evaluate_completeness(
    contract: ReleaseContract,
    candidates: tuple[EvidenceCandidate, ...],
    as_of: datetime,
) -> CompletenessResult:
    """Evaluate whether `candidates` satisfy every required evidence item in `contract`.

    `as_of` must be timezone-aware; this function never reads the clock itself. Contract
    order is preserved in `requirement_results`; candidate order (as supplied) is
    preserved within each requirement's `candidate_evaluations`, so results are
    deterministic for a given input regardless of iteration order elsewhere.

    Raises:
        ValueError: `as_of` is naive (no `tzinfo`).
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware, not naive")

    known_requirement_ids = {item.id for item in contract.required_evidence}

    unmatched: list[CandidateEvaluation] = []
    for candidate in candidates:
        if candidate.requirement_id not in known_requirement_ids:
            unmatched.append(
                CandidateEvaluation(
                    envelope_digest=candidate.envelope.digest,
                    requirement_id=candidate.requirement_id,
                    qualifies=False,
                    reason=CandidateRejectionReason.UNKNOWN_REQUIREMENT,
                )
            )

    requirement_results: list[RequirementCompletenessResult] = []
    for requirement in contract.required_evidence:
        offered = [c for c in candidates if c.requirement_id == requirement.id]

        seen_digests: set[str] = set()
        evaluations: list[CandidateEvaluation] = []
        qualifying_count = 0

        for candidate in offered:
            reason = _reject_reason(candidate, requirement, as_of, seen_digests)
            qualifies = reason is None
            if qualifies:
                qualifying_count += 1
                seen_digests.add(candidate.envelope.digest)
            evaluations.append(
                CandidateEvaluation(
                    envelope_digest=candidate.envelope.digest,
                    requirement_id=candidate.requirement_id,
                    qualifies=qualifies,
                    reason=reason,
                )
            )

        requirement_results.append(
            RequirementCompletenessResult(
                requirement_id=requirement.id,
                minimum_items=requirement.minimum_items,
                qualifying_count=qualifying_count,
                total_candidate_count=len(offered),
                satisfied=qualifying_count >= requirement.minimum_items,
                candidate_evaluations=tuple(evaluations),
            )
        )

    outcome = (
        CompletenessOutcome.SATISFIED
        if all(result.satisfied for result in requirement_results)
        else CompletenessOutcome.INSUFFICIENT_EVIDENCE
    )

    return CompletenessResult(
        outcome=outcome,
        as_of=as_of,
        requirement_results=tuple(requirement_results),
        unmatched_candidates=tuple(unmatched),
    )


def _reject_reason(
    candidate: EvidenceCandidate,
    requirement: RequiredEvidenceItem,
    as_of: datetime,
    seen_digests: set[str],
) -> CandidateRejectionReason | None:
    """Return why `candidate` does not qualify for `requirement`, or `None` if it does.

    Checks run most-foundational first: what kind of evidence is this, is its binding
    trustworthy, is it timely, is it a repeat of something already counted. A candidate
    whose binding could not be established (`INSUFFICIENT_INFORMATION`) is excluded the
    same as one that was actively rejected (`NOT_BOUND`) — neither is ever silently
    treated as sufficient.
    """
    if candidate.envelope.kind is not requirement.kind:
        return CandidateRejectionReason.EVIDENCE_KIND_MISMATCH
    if candidate.envelope.subject is not requirement.subject:
        return CandidateRejectionReason.EVIDENCE_SUBJECT_MISMATCH
    if candidate.binding_result.status is BindingStatus.NOT_BOUND:
        return CandidateRejectionReason.NOT_BOUND
    if candidate.binding_result.status is BindingStatus.INSUFFICIENT_INFORMATION:
        return CandidateRejectionReason.INSUFFICIENT_BINDING_INFORMATION
    if candidate.observed_at > as_of:
        return CandidateRejectionReason.FUTURE_DATED
    if requirement.maximum_age_seconds is not None:
        age = as_of - candidate.observed_at
        if age > timedelta(seconds=requirement.maximum_age_seconds):
            return CandidateRejectionReason.STALE
    if candidate.envelope.digest in seen_digests:
        return CandidateRejectionReason.DUPLICATE_OF_ANOTHER_CANDIDATE
    return None

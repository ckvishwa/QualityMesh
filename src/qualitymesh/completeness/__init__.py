"""Deterministic, offline evaluation of evidence completeness against a release contract."""

from qualitymesh.completeness.evaluator import (
    CandidateEvaluation,
    CandidateRejectionReason,
    CompletenessOutcome,
    CompletenessResult,
    EvidenceCandidate,
    RequirementCompletenessResult,
    evaluate_completeness,
)

__all__ = [
    "CandidateEvaluation",
    "CandidateRejectionReason",
    "CompletenessOutcome",
    "CompletenessResult",
    "EvidenceCandidate",
    "RequirementCompletenessResult",
    "evaluate_completeness",
]

"""Unit tests for evidence-completeness evaluation (PRD FR-6)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from qualitymesh.binding.verifier import BindingReason, BindingStatus, BindingVerificationResult
from qualitymesh.completeness.evaluator import (
    CandidateRejectionReason,
    CompletenessOutcome,
    EvidenceCandidate,
    evaluate_completeness,
)
from qualitymesh.schemas.contract import EvidenceKind, EvidenceSubject, ReleaseContract
from qualitymesh.schemas.evidence import EvidenceEnvelope

AS_OF = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_contract(
    *,
    minimum_items: int = 1,
    maximum_age_seconds: int | None = None,
    second_requirement: bool = False,
) -> ReleaseContract:
    required_evidence: list[dict[str, object]] = [
        {
            "id": "unit-results",
            "kind": "junit",
            "subject": "source-revision",
            "producerWorkflow": "build-release",
            "minimumItems": minimum_items,
            **({"maximumAgeSeconds": maximum_age_seconds} if maximum_age_seconds else {}),
        },
        {
            "id": "backend-attestation",
            "kind": "artifact-attestation",
            "subject": "artifact",
            "producerWorkflow": "build-release",
            "artifactId": "example-api",
        },
    ]
    if not second_requirement:
        required_evidence = required_evidence[:1]

    return ReleaseContract.model_validate(
        {
            "apiVersion": "qualitymesh.dev/v1",
            "kind": "ReleaseContract",
            "metadata": {"name": "completeness-tests", "version": 1},
            "trustBoundary": {
                "repository": "qualitymesh/example",
                "defaultBranch": "main",
                "requireArtifactAttestation": False,
                "trustedWorkflows": [
                    {
                        "id": "build-release",
                        "path": ".github/workflows/build-release.yml",
                        "allowedRefs": ["refs/heads/main"],
                        "allowedEvents": ["push"],
                    }
                ],
            },
            "requiredEvidence": required_evidence,
            "policyRules": {
                "artifactMismatch": "BLOCK",
                "untrustedWorkflow": "BLOCK",
                "invalidAttestation": "BLOCK",
                "missingRequiredEvidence": "INSUFFICIENT_EVIDENCE",
                "failedCriticalJourney": "BLOCK",
                "exposedSecrets": {"maximum": 0, "outcome": "BLOCK"},
                "criticalVulnerabilities": {"maximum": 0, "outcome": "BLOCK"},
            },
        }
    )


def bound_result() -> BindingVerificationResult:
    return BindingVerificationResult(status=BindingStatus.BOUND, reason=None, detail="bound")


def not_bound_result() -> BindingVerificationResult:
    return BindingVerificationResult(
        status=BindingStatus.NOT_BOUND, reason=BindingReason.COMMIT_MISMATCH, detail="mismatch"
    )


def insufficient_result() -> BindingVerificationResult:
    return BindingVerificationResult(
        status=BindingStatus.INSUFFICIENT_INFORMATION,
        reason=BindingReason.NO_EVALUATION_TARGET_SUPPLIED,
        detail="no target",
    )


def candidate(
    *,
    requirement_id: str = "unit-results",
    raw_bytes: bytes = b"<testsuite/>",
    kind: EvidenceKind = EvidenceKind.JUNIT,
    subject: EvidenceSubject = EvidenceSubject.SOURCE_REVISION,
    binding_result: BindingVerificationResult | None = None,
    observed_at: datetime = AS_OF,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        requirement_id=requirement_id,
        envelope=EvidenceEnvelope(kind=kind, subject=subject, raw_bytes=raw_bytes),
        binding_result=binding_result if binding_result is not None else bound_result(),
        observed_at=observed_at,
    )


# --------------------------------------------------------------------------------------
# Satisfaction — happy paths
# --------------------------------------------------------------------------------------


def test_one_requirement_satisfied_by_one_fresh_bound_candidate() -> None:
    result = evaluate_completeness(build_contract(), (candidate(),), AS_OF)

    assert result.outcome is CompletenessOutcome.SATISFIED
    assert result.requirement_results[0].satisfied is True
    assert result.requirement_results[0].qualifying_count == 1


def test_several_requirements_all_satisfied() -> None:
    contract = build_contract(second_requirement=True)
    candidates = (
        candidate(requirement_id="unit-results", raw_bytes=b"a"),
        candidate(
            requirement_id="backend-attestation",
            raw_bytes=b"b",
            kind=EvidenceKind.ARTIFACT_ATTESTATION,
            subject=EvidenceSubject.ARTIFACT,
        ),
    )

    result = evaluate_completeness(contract, candidates, AS_OF)

    assert result.outcome is CompletenessOutcome.SATISFIED
    assert all(r.satisfied for r in result.requirement_results)


def test_overall_insufficient_when_one_of_several_requirements_fails() -> None:
    contract = build_contract(second_requirement=True)
    candidates = (candidate(requirement_id="unit-results"),)  # backend-attestation unmet

    result = evaluate_completeness(contract, candidates, AS_OF)

    assert result.outcome is CompletenessOutcome.INSUFFICIENT_EVIDENCE
    by_id = {r.requirement_id: r for r in result.requirement_results}
    assert by_id["unit-results"].satisfied is True
    assert by_id["backend-attestation"].satisfied is False


# --------------------------------------------------------------------------------------
# minimumItems counting
# --------------------------------------------------------------------------------------


def test_no_candidate_for_a_required_item() -> None:
    result = evaluate_completeness(build_contract(), (), AS_OF)

    assert result.outcome is CompletenessOutcome.INSUFFICIENT_EVIDENCE
    assert result.requirement_results[0].qualifying_count == 0
    assert result.requirement_results[0].satisfied is False


def test_fewer_candidates_than_minimum_items() -> None:
    contract = build_contract(minimum_items=3)
    candidates = (candidate(raw_bytes=b"a"), candidate(raw_bytes=b"b"))

    result = evaluate_completeness(contract, candidates, AS_OF)

    assert result.requirement_results[0].qualifying_count == 2
    assert result.requirement_results[0].satisfied is False


def test_exactly_minimum_items() -> None:
    contract = build_contract(minimum_items=2)
    candidates = (candidate(raw_bytes=b"a"), candidate(raw_bytes=b"b"))

    result = evaluate_completeness(contract, candidates, AS_OF)

    assert result.requirement_results[0].qualifying_count == 2
    assert result.requirement_results[0].satisfied is True


def test_more_than_minimum_items() -> None:
    contract = build_contract(minimum_items=1)
    candidates = (candidate(raw_bytes=b"a"), candidate(raw_bytes=b"b"), candidate(raw_bytes=b"c"))

    result = evaluate_completeness(contract, candidates, AS_OF)

    assert result.requirement_results[0].qualifying_count == 3
    assert result.requirement_results[0].satisfied is True


# --------------------------------------------------------------------------------------
# Freshness (maximumAgeSeconds)
# --------------------------------------------------------------------------------------


def test_stale_evidence_excluded_by_maximum_age() -> None:
    contract = build_contract(maximum_age_seconds=60)
    stale = candidate(observed_at=AS_OF - timedelta(seconds=61))

    result = evaluate_completeness(contract, (stale,), AS_OF)

    req = result.requirement_results[0]
    assert req.qualifying_count == 0
    assert req.candidate_evaluations[0].reason is CandidateRejectionReason.STALE


def test_evidence_exactly_on_the_freshness_boundary_qualifies() -> None:
    contract = build_contract(maximum_age_seconds=60)
    boundary = candidate(observed_at=AS_OF - timedelta(seconds=60))

    result = evaluate_completeness(contract, (boundary,), AS_OF)

    req = result.requirement_results[0]
    assert req.qualifying_count == 1
    assert req.candidate_evaluations[0].qualifies is True


def test_evidence_one_second_beyond_the_freshness_boundary_is_stale() -> None:
    contract = build_contract(maximum_age_seconds=60)
    just_over = candidate(observed_at=AS_OF - timedelta(seconds=61))

    result = evaluate_completeness(contract, (just_over,), AS_OF)

    assert result.requirement_results[0].candidate_evaluations[0].reason is (
        CandidateRejectionReason.STALE
    )


def test_maximum_age_absent_does_not_reject_old_evidence() -> None:
    contract = build_contract(maximum_age_seconds=None)
    very_old = candidate(observed_at=AS_OF - timedelta(days=3650))

    result = evaluate_completeness(contract, (very_old,), AS_OF)

    assert result.requirement_results[0].qualifying_count == 1


def test_future_dated_evidence_is_rejected_even_without_maximum_age() -> None:
    contract = build_contract(maximum_age_seconds=None)
    future = candidate(observed_at=AS_OF + timedelta(seconds=1))

    result = evaluate_completeness(contract, (future,), AS_OF)

    req = result.requirement_results[0]
    assert req.qualifying_count == 0
    assert req.candidate_evaluations[0].reason is CandidateRejectionReason.FUTURE_DATED


def test_timezone_aware_equivalent_timestamps_are_treated_as_equal_instants() -> None:
    contract = build_contract(maximum_age_seconds=60)
    # Same instant as AS_OF, expressed in a different offset.
    equivalent = AS_OF.astimezone(timezone(timedelta(hours=5)))
    fresh = candidate(observed_at=equivalent)

    result = evaluate_completeness(contract, (fresh,), AS_OF)

    assert result.requirement_results[0].qualifying_count == 1


# --------------------------------------------------------------------------------------
# Binding-result consumption (not re-verification)
# --------------------------------------------------------------------------------------


def test_not_bound_candidate_is_excluded() -> None:
    result = evaluate_completeness(
        build_contract(), (candidate(binding_result=not_bound_result()),), AS_OF
    )

    req = result.requirement_results[0]
    assert req.qualifying_count == 0
    assert req.candidate_evaluations[0].reason is CandidateRejectionReason.NOT_BOUND


def test_insufficient_information_binding_candidate_is_excluded() -> None:
    result = evaluate_completeness(
        build_contract(), (candidate(binding_result=insufficient_result()),), AS_OF
    )

    req = result.requirement_results[0]
    assert req.qualifying_count == 0
    assert req.candidate_evaluations[0].reason is (
        CandidateRejectionReason.INSUFFICIENT_BINDING_INFORMATION
    )


def test_mixture_of_bound_unbound_and_stale_candidates() -> None:
    contract = build_contract(minimum_items=1, maximum_age_seconds=60)
    candidates = (
        candidate(raw_bytes=b"a", binding_result=bound_result()),
        candidate(raw_bytes=b"b", binding_result=not_bound_result()),
        candidate(raw_bytes=b"c", observed_at=AS_OF - timedelta(seconds=120)),
    )

    result = evaluate_completeness(contract, candidates, AS_OF)

    req = result.requirement_results[0]
    assert req.qualifying_count == 1
    assert req.total_candidate_count == 3
    assert req.satisfied is True


def test_evaluator_consumes_binding_result_without_reproducing_its_comparisons() -> None:
    # A candidate whose commit_sha would "match" under a hand-rolled re-check, but
    # whose actual BindingVerificationResult says NOT_BOUND, must still be excluded.
    # If the evaluator re-derived binding itself instead of trusting the supplied
    # result, this candidate could wrongly qualify.
    result = evaluate_completeness(
        build_contract(), (candidate(binding_result=not_bound_result()),), AS_OF
    )

    assert result.requirement_results[0].qualifying_count == 0


# --------------------------------------------------------------------------------------
# Kind / subject / requirement association
# --------------------------------------------------------------------------------------


def test_unknown_requirement_reference_is_reported_not_silently_dropped() -> None:
    stray = candidate(requirement_id="does-not-exist")

    result = evaluate_completeness(build_contract(), (stray,), AS_OF)

    assert len(result.unmatched_candidates) == 1
    assert result.unmatched_candidates[0].reason is CandidateRejectionReason.UNKNOWN_REQUIREMENT
    # An unmatched candidate never contributes to any requirement's count.
    assert result.requirement_results[0].qualifying_count == 0
    assert result.requirement_results[0].total_candidate_count == 0


def test_wrong_evidence_kind_does_not_count_even_under_the_right_requirement_id() -> None:
    wrong_kind = candidate(kind=EvidenceKind.SBOM)

    result = evaluate_completeness(build_contract(), (wrong_kind,), AS_OF)

    req = result.requirement_results[0]
    assert req.qualifying_count == 0
    assert req.candidate_evaluations[0].reason is CandidateRejectionReason.EVIDENCE_KIND_MISMATCH


def test_wrong_evidence_subject_does_not_count_even_under_the_right_requirement_id() -> None:
    wrong_subject = candidate(subject=EvidenceSubject.ARTIFACT)

    result = evaluate_completeness(build_contract(), (wrong_subject,), AS_OF)

    req = result.requirement_results[0]
    assert req.qualifying_count == 0
    assert req.candidate_evaluations[0].reason is (
        CandidateRejectionReason.EVIDENCE_SUBJECT_MISMATCH
    )


# --------------------------------------------------------------------------------------
# Duplicate handling — identical raw-bytes digest counts once (SYSTEM_RULES.md idempotency)
# --------------------------------------------------------------------------------------


def test_duplicate_candidate_with_identical_digest_counts_once() -> None:
    contract = build_contract(minimum_items=2)
    same_bytes = b"<testsuite/>"
    candidates = (candidate(raw_bytes=same_bytes), candidate(raw_bytes=same_bytes))

    result = evaluate_completeness(contract, candidates, AS_OF)

    req = result.requirement_results[0]
    assert req.qualifying_count == 1
    assert req.total_candidate_count == 2
    assert req.satisfied is False
    assert req.candidate_evaluations[1].reason is (
        CandidateRejectionReason.DUPLICATE_OF_ANOTHER_CANDIDATE
    )


def test_candidates_with_different_digests_both_count() -> None:
    contract = build_contract(minimum_items=2)
    candidates = (candidate(raw_bytes=b"first"), candidate(raw_bytes=b"second"))

    result = evaluate_completeness(contract, candidates, AS_OF)

    assert result.requirement_results[0].qualifying_count == 2


# --------------------------------------------------------------------------------------
# Timestamp validity
# --------------------------------------------------------------------------------------


def test_naive_observed_at_is_rejected_at_candidate_construction() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        EvidenceCandidate(
            requirement_id="unit-results",
            envelope=EvidenceEnvelope(
                kind=EvidenceKind.JUNIT,
                subject=EvidenceSubject.SOURCE_REVISION,
                raw_bytes=b"<testsuite/>",
            ),
            binding_result=bound_result(),
            observed_at=datetime(2026, 7, 21, 12, 0, 0),  # naive, no tzinfo
        )


def test_naive_as_of_is_rejected() -> None:
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        evaluate_completeness(build_contract(), (candidate(),), datetime(2026, 7, 21, 12, 0, 0))


def test_missing_observed_at_is_rejected_at_candidate_construction() -> None:
    with pytest.raises(ValidationError, match="observed_at"):
        EvidenceCandidate(
            requirement_id="unit-results",
            envelope=EvidenceEnvelope(
                kind=EvidenceKind.JUNIT,
                subject=EvidenceSubject.SOURCE_REVISION,
                raw_bytes=b"<testsuite/>",
            ),
            binding_result=bound_result(),  # pyright: ignore[reportCallIssue]
        )


def test_evidence_candidate_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceCandidate(
            requirement_id="unit-results",
            envelope=EvidenceEnvelope(
                kind=EvidenceKind.JUNIT,
                subject=EvidenceSubject.SOURCE_REVISION,
                raw_bytes=b"<testsuite/>",
            ),
            binding_result=bound_result(),
            observed_at=AS_OF,
            run_id="12345",  # pyright: ignore[reportCallIssue]
        )


def test_evidence_candidate_is_immutable() -> None:
    with pytest.raises(ValidationError, match="frozen"):
        candidate().observed_at = AS_OF


# --------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------


def test_explicit_as_of_determinism_same_inputs_same_result() -> None:
    contract = build_contract(maximum_age_seconds=60)
    candidates = (candidate(),)

    first = evaluate_completeness(contract, candidates, AS_OF)
    second = evaluate_completeness(contract, candidates, AS_OF)

    assert first == second


def test_requirement_results_preserve_contract_order() -> None:
    contract = build_contract(second_requirement=True)

    result = evaluate_completeness(contract, (), AS_OF)

    assert [r.requirement_id for r in result.requirement_results] == [
        "unit-results",
        "backend-attestation",
    ]


def test_candidate_evaluations_preserve_supplied_candidate_order() -> None:
    candidates = (
        candidate(raw_bytes=b"first"),
        candidate(raw_bytes=b"second"),
        candidate(raw_bytes=b"third"),
    )

    result = evaluate_completeness(build_contract(), candidates, AS_OF)

    digests = [c.envelope_digest for c in result.requirement_results[0].candidate_evaluations]
    expected = [c.envelope.digest for c in candidates]
    assert digests == expected


# --------------------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------------------


def test_no_mutation_of_contract_or_candidates() -> None:
    contract = build_contract(maximum_age_seconds=60)
    candidates = (candidate(),)

    contract_before = copy.deepcopy(contract.model_dump())
    candidates_before = [copy.deepcopy(c.model_dump()) for c in candidates]

    evaluate_completeness(contract, candidates, AS_OF)

    assert contract.model_dump() == contract_before
    assert [c.model_dump() for c in candidates] == candidates_before


def test_result_is_immutable() -> None:
    result = evaluate_completeness(build_contract(), (candidate(),), AS_OF)

    with pytest.raises(ValidationError, match="frozen"):
        result.outcome = CompletenessOutcome.INSUFFICIENT_EVIDENCE

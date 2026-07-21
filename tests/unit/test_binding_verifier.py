"""Unit tests for evidence-to-contract binding verification (PRD FR-5)."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from qualitymesh.binding.verifier import (
    ArtifactBindingClaim,
    ArtifactEvaluationTarget,
    BindingReason,
    BindingStatus,
    CommitBindingClaim,
    CommitEvaluationTarget,
    verify_evidence_binding,
)
from qualitymesh.schemas.contract import EvidenceKind, EvidenceSubject, ReleaseContract
from qualitymesh.schemas.evidence import EvidenceEnvelope

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_contract() -> ReleaseContract:
    return ReleaseContract.model_validate(
        {
            "apiVersion": "qualitymesh.dev/v1",
            "kind": "ReleaseContract",
            "metadata": {"name": "binding-tests", "version": 1},
            "trustBoundary": {
                "repository": "qualitymesh/example",
                "defaultBranch": "main",
                "trustedWorkflows": [
                    {
                        "id": "build-release",
                        "path": ".github/workflows/build-release.yml",
                        "allowedRefs": ["refs/heads/main"],
                        "allowedEvents": ["push"],
                    },
                    {
                        "id": "smoke-test",
                        "path": ".github/workflows/smoke-test.yml",
                        "allowedRefs": ["refs/heads/main"],
                        "allowedEvents": ["deployment_status"],
                    },
                ],
                "allowedDigestAlgorithms": ["sha256"],
            },
            "requiredEvidence": [
                {
                    "id": "unit-results",
                    "kind": "junit",
                    "subject": "source-revision",
                    "producerWorkflow": "build-release",
                },
                {
                    "id": "backend-attestation",
                    "kind": "artifact-attestation",
                    "subject": "artifact",
                    "producerWorkflow": "build-release",
                    "artifactId": "example-api",
                },
            ],
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


def commit_envelope() -> EvidenceEnvelope:
    return EvidenceEnvelope(
        kind=EvidenceKind.JUNIT,
        subject=EvidenceSubject.SOURCE_REVISION,
        raw_bytes=b"<testsuite/>",
    )


def artifact_envelope() -> EvidenceEnvelope:
    return EvidenceEnvelope(
        kind=EvidenceKind.ARTIFACT_ATTESTATION,
        subject=EvidenceSubject.ARTIFACT,
        raw_bytes=b"{}",
    )


def commit_claim(**overrides: object) -> CommitBindingClaim:
    fields: dict[str, object] = {
        "requirement_id": "unit-results",
        "producer_workflow": "build-release",
        "commit_sha": "abc123def456",
    }
    fields.update(overrides)
    return CommitBindingClaim(**fields)  # pyright: ignore[reportArgumentType]


def artifact_claim(**overrides: object) -> ArtifactBindingClaim:
    fields: dict[str, object] = {
        "requirement_id": "backend-attestation",
        "producer_workflow": "build-release",
        "artifact_id": "example-api",
        "artifact_digest": "deadbeef" * 8,
        "digest_algorithm": "sha256",
    }
    fields.update(overrides)
    return ArtifactBindingClaim(**fields)  # pyright: ignore[reportArgumentType]


def commit_target(**overrides: object) -> CommitEvaluationTarget:
    fields: dict[str, object] = {"commit_sha": "abc123def456"}
    fields.update(overrides)
    return CommitEvaluationTarget(**fields)  # pyright: ignore[reportArgumentType]


def artifact_target(**overrides: object) -> ArtifactEvaluationTarget:
    fields: dict[str, object] = {
        "artifact_id": "example-api",
        "artifact_digest": "deadbeef" * 8,
        "digest_algorithm": "sha256",
    }
    fields.update(overrides)
    return ArtifactEvaluationTarget(**fields)  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------------------
# Correctly bound
# --------------------------------------------------------------------------------------


def test_correctly_bound_commit_subject_evidence() -> None:
    result = verify_evidence_binding(
        build_contract(), commit_envelope(), commit_claim(), commit_target()
    )

    assert result.status is BindingStatus.BOUND
    assert result.reason is None


def test_correctly_bound_artifact_subject_evidence() -> None:
    result = verify_evidence_binding(
        build_contract(), artifact_envelope(), artifact_claim(), artifact_target()
    )

    assert result.status is BindingStatus.BOUND
    assert result.reason is None


# --------------------------------------------------------------------------------------
# NOT_BOUND — target identity mismatches
# --------------------------------------------------------------------------------------


def test_wrong_commit_sha() -> None:
    result = verify_evidence_binding(
        build_contract(),
        commit_envelope(),
        commit_claim(commit_sha="wrong-sha"),
        commit_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.COMMIT_MISMATCH


def test_wrong_commit_sha_case_only_difference_is_not_normalized() -> None:
    # Proves no accidental case normalization: differing only in case must still fail.
    result = verify_evidence_binding(
        build_contract(),
        commit_envelope(),
        commit_claim(commit_sha="ABC123DEF456"),
        commit_target(commit_sha="abc123def456"),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.COMMIT_MISMATCH


def test_wrong_artifact_id() -> None:
    result = verify_evidence_binding(
        build_contract(),
        artifact_envelope(),
        artifact_claim(artifact_id="wrong-artifact"),
        artifact_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.ARTIFACT_ID_MISMATCH


def test_wrong_artifact_digest() -> None:
    result = verify_evidence_binding(
        build_contract(),
        artifact_envelope(),
        artifact_claim(artifact_digest="f" * 64),
        artifact_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.ARTIFACT_DIGEST_MISMATCH


def test_wrong_artifact_digest_algorithm() -> None:
    # Claimed algorithm ('sha256') is permitted by the trust boundary, but does not
    # match what the evaluation target actually used ('sha512') — distinct from an
    # outright forbidden algorithm.
    result = verify_evidence_binding(
        build_contract(),
        artifact_envelope(),
        artifact_claim(digest_algorithm="sha256"),
        artifact_target(digest_algorithm="sha512"),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.ARTIFACT_DIGEST_ALGORITHM_MISMATCH


def test_evidence_envelope_digest_is_never_used_as_the_artifact_digest() -> None:
    # The envelope's own payload digest coincidentally equals the target's artifact
    # digest. If the verifier ever substituted envelope.digest for a claimed artifact
    # digest, this would incorrectly report BOUND despite the claim itself being wrong.
    envelope = artifact_envelope()
    result = verify_evidence_binding(
        build_contract(),
        envelope,
        artifact_claim(artifact_digest="not-the-envelope-digest"),
        artifact_target(artifact_digest=envelope.digest),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.ARTIFACT_DIGEST_MISMATCH


# --------------------------------------------------------------------------------------
# NOT_BOUND — relationship and policy failures
# --------------------------------------------------------------------------------------


def test_unknown_required_evidence_item() -> None:
    result = verify_evidence_binding(
        build_contract(),
        commit_envelope(),
        commit_claim(requirement_id="does-not-exist"),
        commit_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.UNKNOWN_REQUIREMENT
    assert "does-not-exist" in result.detail


def test_evidence_kind_mismatch() -> None:
    envelope = EvidenceEnvelope(
        kind=EvidenceKind.SBOM, subject=EvidenceSubject.SOURCE_REVISION, raw_bytes=b"{}"
    )

    result = verify_evidence_binding(build_contract(), envelope, commit_claim(), commit_target())

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.EVIDENCE_KIND_MISMATCH


def test_evidence_subject_mismatch() -> None:
    # Envelope subject (artifact) does not match the referenced requirement's subject
    # (source-revision) — kind matches, subject does not.
    envelope = EvidenceEnvelope(
        kind=EvidenceKind.JUNIT, subject=EvidenceSubject.ARTIFACT, raw_bytes=b"<testsuite/>"
    )

    result = verify_evidence_binding(build_contract(), envelope, commit_claim(), commit_target())

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.EVIDENCE_SUBJECT_MISMATCH


def test_claim_subject_mismatch_against_envelope() -> None:
    # Claim references a source-revision requirement (matches envelope+requirement),
    # but the claim itself is artifact-shaped — an internally inconsistent submission.
    result = verify_evidence_binding(
        build_contract(),
        commit_envelope(),
        artifact_claim(requirement_id="unit-results"),
        commit_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.CLAIM_SUBJECT_MISMATCH


def test_claim_and_target_subject_contradict_each_other() -> None:
    result = verify_evidence_binding(
        build_contract(),
        commit_envelope(),
        commit_claim(),
        artifact_target(),  # pyright: ignore[reportArgumentType]
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.CLAIM_TARGET_SUBJECT_MISMATCH


def test_producer_workflow_mismatch() -> None:
    result = verify_evidence_binding(
        build_contract(),
        commit_envelope(),
        commit_claim(producer_workflow="smoke-test"),
        commit_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.PRODUCER_WORKFLOW_MISMATCH


def test_forbidden_digest_algorithm() -> None:
    result = verify_evidence_binding(
        build_contract(),
        artifact_envelope(),
        artifact_claim(digest_algorithm="sha1"),
        artifact_target(digest_algorithm="sha1"),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.DIGEST_ALGORITHM_NOT_ALLOWED


def test_forbidden_digest_algorithm_wrong_case_is_not_normalized() -> None:
    # 'SHA256' is not the same string as the allowed 'sha256' — exact comparison only.
    result = verify_evidence_binding(
        build_contract(),
        artifact_envelope(),
        artifact_claim(digest_algorithm="SHA256"),
        artifact_target(digest_algorithm="SHA256"),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.DIGEST_ALGORITHM_NOT_ALLOWED


# --------------------------------------------------------------------------------------
# INSUFFICIENT_INFORMATION
# --------------------------------------------------------------------------------------


def test_no_binding_claim_submitted() -> None:
    result = verify_evidence_binding(build_contract(), commit_envelope(), None, commit_target())

    assert result.status is BindingStatus.INSUFFICIENT_INFORMATION
    assert result.reason is BindingReason.NO_BINDING_CLAIM_SUBMITTED


def test_no_evaluation_target_supplied() -> None:
    result = verify_evidence_binding(build_contract(), commit_envelope(), commit_claim(), None)

    assert result.status is BindingStatus.INSUFFICIENT_INFORMATION
    assert result.reason is BindingReason.NO_EVALUATION_TARGET_SUPPLIED


def test_missing_target_does_not_mask_an_earlier_relationship_failure() -> None:
    # An unknown requirement is foundational and must be reported even though the
    # target is also absent — NOT_BOUND takes precedence over INSUFFICIENT_INFORMATION.
    result = verify_evidence_binding(
        build_contract(), commit_envelope(), commit_claim(requirement_id="does-not-exist"), None
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.UNKNOWN_REQUIREMENT


# --------------------------------------------------------------------------------------
# Deterministic precedence when multiple problems exist
# --------------------------------------------------------------------------------------


def test_unknown_requirement_takes_precedence_over_every_other_problem() -> None:
    # Wrong workflow AND wrong subject AND an unresolvable requirement all present at
    # once — the foundational failure (unknown requirement) must be the one reported.
    result = verify_evidence_binding(
        build_contract(),
        artifact_envelope(),
        commit_claim(requirement_id="does-not-exist", producer_workflow="smoke-test"),
        commit_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.UNKNOWN_REQUIREMENT


def test_kind_mismatch_takes_precedence_over_workflow_mismatch() -> None:
    envelope = EvidenceEnvelope(
        kind=EvidenceKind.SBOM, subject=EvidenceSubject.SOURCE_REVISION, raw_bytes=b"{}"
    )

    result = verify_evidence_binding(
        build_contract(),
        envelope,
        commit_claim(producer_workflow="smoke-test"),
        commit_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.EVIDENCE_KIND_MISMATCH


def test_workflow_mismatch_takes_precedence_over_target_identity_mismatch() -> None:
    result = verify_evidence_binding(
        build_contract(),
        commit_envelope(),
        commit_claim(producer_workflow="smoke-test", commit_sha="wrong-sha"),
        commit_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.PRODUCER_WORKFLOW_MISMATCH


def test_digest_algorithm_policy_takes_precedence_over_target_digest_mismatch() -> None:
    result = verify_evidence_binding(
        build_contract(),
        artifact_envelope(),
        artifact_claim(digest_algorithm="sha1", artifact_digest="totally-wrong"),
        artifact_target(),
    )

    assert result.status is BindingStatus.NOT_BOUND
    assert result.reason is BindingReason.DIGEST_ALGORITHM_NOT_ALLOWED


# --------------------------------------------------------------------------------------
# Purity: no mutation of any input
# --------------------------------------------------------------------------------------


def test_no_mutation_of_contract_envelope_claim_or_target() -> None:
    contract = build_contract()
    envelope = artifact_envelope()
    claim = artifact_claim()
    target = artifact_target()

    contract_before = copy.deepcopy(contract.model_dump())
    envelope_before = copy.deepcopy(envelope.model_dump())
    claim_before = copy.deepcopy(claim.model_dump())
    target_before = copy.deepcopy(target.model_dump())

    verify_evidence_binding(contract, envelope, claim, target)

    assert contract.model_dump() == contract_before
    assert envelope.model_dump() == envelope_before
    assert claim.model_dump() == claim_before
    assert target.model_dump() == target_before


def test_result_is_immutable() -> None:
    result = verify_evidence_binding(
        build_contract(), commit_envelope(), commit_claim(), commit_target()
    )

    with pytest.raises(ValidationError, match="frozen"):
        result.status = BindingStatus.NOT_BOUND  # pyright: ignore[reportAttributeAccessIssue]

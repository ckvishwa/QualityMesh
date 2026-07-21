"""Unit tests for the binding-claim and evaluation-target schemas.

These are structural impossibilities the models themselves reject — per
`SYSTEM_RULES.md` §"Do not duplicate the schema's invariants inside the verifier,"
`verify_evidence_binding` never has to check for these; they cannot be constructed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qualitymesh.binding.verifier import (
    ArtifactBindingClaim,
    ArtifactEvaluationTarget,
    CommitBindingClaim,
    CommitEvaluationTarget,
)


def test_commit_claim_rejects_artifact_only_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommitBindingClaim(
            requirement_id="unit-results",
            producer_workflow="build-release",
            commit_sha="abc123",
            artifact_id="example-api",  # pyright: ignore[reportCallIssue]
        )


def test_commit_claim_requires_commit_sha() -> None:
    with pytest.raises(ValidationError, match="commit_sha"):
        CommitBindingClaim(
            requirement_id="unit-results",
            producer_workflow="build-release",  # pyright: ignore[reportCallIssue]
        )


def test_artifact_claim_rejects_commit_only_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArtifactBindingClaim(
            requirement_id="backend-attestation",
            producer_workflow="build-release",
            artifact_id="example-api",
            artifact_digest="deadbeef" * 8,
            digest_algorithm="sha256",
            commit_sha="abc123",  # pyright: ignore[reportCallIssue]
        )


def test_artifact_claim_requires_artifact_id() -> None:
    with pytest.raises(ValidationError, match="artifact_id"):
        ArtifactBindingClaim(
            requirement_id="backend-attestation",
            producer_workflow="build-release",
            artifact_digest="deadbeef" * 8,
            digest_algorithm="sha256",  # pyright: ignore[reportCallIssue]
        )


def test_artifact_claim_requires_artifact_digest() -> None:
    with pytest.raises(ValidationError, match="artifact_digest"):
        ArtifactBindingClaim(
            requirement_id="backend-attestation",
            producer_workflow="build-release",
            artifact_id="example-api",
            digest_algorithm="sha256",  # pyright: ignore[reportCallIssue]
        )


def test_artifact_claim_requires_digest_algorithm() -> None:
    with pytest.raises(ValidationError, match="digest_algorithm"):
        ArtifactBindingClaim(
            requirement_id="backend-attestation",
            producer_workflow="build-release",
            artifact_id="example-api",
            artifact_digest="deadbeef" * 8,  # pyright: ignore[reportCallIssue]
        )


def test_commit_target_rejects_artifact_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommitEvaluationTarget(
            commit_sha="abc123",
            artifact_id="example-api",  # pyright: ignore[reportCallIssue]
        )


def test_artifact_target_requires_all_three_identity_fields() -> None:
    with pytest.raises(ValidationError, match="artifact_digest"):
        ArtifactEvaluationTarget(
            artifact_id="example-api",
            digest_algorithm="sha256",  # pyright: ignore[reportCallIssue]
        )


def test_claims_and_targets_are_immutable() -> None:
    claim = CommitBindingClaim(
        requirement_id="unit-results", producer_workflow="build-release", commit_sha="abc123"
    )

    with pytest.raises(ValidationError, match="frozen"):
        claim.commit_sha = "different"  # pyright: ignore[reportAttributeAccessIssue]


def test_claims_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommitBindingClaim(
            requirement_id="unit-results",
            producer_workflow="build-release",
            commit_sha="abc123",
            run_id="12345",  # pyright: ignore[reportCallIssue]
        )

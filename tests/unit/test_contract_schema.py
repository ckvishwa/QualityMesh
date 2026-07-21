"""Unit tests for the strict release-contract boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from qualitymesh.schemas.contract import Decision, ReleaseContract


def valid_contract_data() -> dict[str, Any]:
    return {
        "apiVersion": "qualitymesh.dev/v1",
        "kind": "ReleaseContract",
        "metadata": {
            "name": "reva-release",
            "version": 1,
        },
        "trustBoundary": {
            "repository": "ckvishwa/reva",
            "defaultBranch": "main",
            "oidc": {
                "issuer": "https://token.actions.githubusercontent.com",
                "audience": "qualitymesh",
            },
            "trustedWorkflows": [
                {
                    "id": "build-release",
                    "path": ".github/workflows/reva-release.yml",
                    "allowedRefs": ["refs/heads/main", "refs/pull/*/merge"],
                    "allowedEvents": ["pull_request", "push", "workflow_dispatch"],
                },
                {
                    "id": "deployment-smoke",
                    "path": ".github/workflows/deployment-smoke.yml",
                    "allowedRefs": ["refs/heads/main"],
                    "allowedEvents": ["deployment_status", "workflow_dispatch"],
                },
            ],
            "allowedDigestAlgorithms": ["sha256"],
            "requireArtifactAttestation": True,
        },
        "requiredEvidence": [
            {
                "id": "unit-results",
                "kind": "junit",
                "subject": "source-revision",
                "producerWorkflow": "build-release",
                "required": True,
                "minimumItems": 1,
            },
            {
                "id": "backend-attestation",
                "kind": "artifact-attestation",
                "subject": "artifact",
                "producerWorkflow": "build-release",
                "artifactId": "reva-api",
                "required": True,
                "minimumItems": 1,
            },
            {
                "id": "deployed-action-sync",
                "kind": "critical-journey",
                "subject": "deployed-service",
                "producerWorkflow": "deployment-smoke",
                "required": True,
                "minimumItems": 1,
                "maximumAgeSeconds": 1800,
            },
        ],
        "policyRules": {
            "artifactMismatch": "BLOCK",
            "untrustedWorkflow": "BLOCK",
            "invalidAttestation": "BLOCK",
            "missingRequiredEvidence": "INSUFFICIENT_EVIDENCE",
            "failedCriticalJourney": "BLOCK",
            "exposedSecrets": {
                "maximum": 0,
                "outcome": "BLOCK",
            },
            "criticalVulnerabilities": {
                "maximum": 0,
                "outcome": "BLOCK",
            },
            "suspiciousTestCountReductionPercent": 20,
        },
    }


def test_valid_release_contract_is_accepted() -> None:
    contract = ReleaseContract.model_validate(valid_contract_data())

    assert contract.metadata.name == "reva-release"
    assert contract.metadata.version == 1
    assert contract.policy_rules.artifact_mismatch is Decision.BLOCK
    assert len(contract.required_evidence) == 3


def test_unknown_fields_are_rejected() -> None:
    data = valid_contract_data()
    data["trustBoundary"]["trustEveryone"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReleaseContract.model_validate(data)


def test_strict_types_reject_numeric_strings() -> None:
    data = valid_contract_data()
    data["metadata"]["version"] = "1"

    with pytest.raises(ValidationError, match="valid integer"):
        ReleaseContract.model_validate(data)


def test_duplicate_evidence_ids_are_rejected() -> None:
    data = valid_contract_data()
    duplicate = deepcopy(data["requiredEvidence"][0])
    data["requiredEvidence"].append(duplicate)

    with pytest.raises(ValidationError, match="required evidence IDs must be unique"):
        ReleaseContract.model_validate(data)


def test_unknown_producer_workflow_is_rejected() -> None:
    data = valid_contract_data()
    data["requiredEvidence"][0]["producerWorkflow"] = "untrusted-workflow"

    with pytest.raises(ValidationError, match="unknown workflows: untrusted-workflow"):
        ReleaseContract.model_validate(data)


def test_artifact_evidence_requires_artifact_id() -> None:
    data = valid_contract_data()
    del data["requiredEvidence"][1]["artifactId"]

    with pytest.raises(ValidationError, match="artifactId is required"):
        ReleaseContract.model_validate(data)


def test_required_attestation_cannot_be_omitted() -> None:
    data = valid_contract_data()
    data["requiredEvidence"] = [
        item for item in data["requiredEvidence"] if item["kind"] != "artifact-attestation"
    ]

    with pytest.raises(ValidationError, match="required artifact-attestation"):
        ReleaseContract.model_validate(data)


def test_fail_closed_artifact_mismatch_rule_cannot_be_weakened() -> None:
    data = valid_contract_data()
    data["policyRules"]["artifactMismatch"] = "WARN"

    with pytest.raises(ValidationError):
        ReleaseContract.model_validate(data)


def test_security_rule_cannot_warn_on_exposed_secrets() -> None:
    data = valid_contract_data()
    data["policyRules"]["exposedSecrets"]["outcome"] = "WARN"

    with pytest.raises(ValidationError, match="exposedSecrets must have outcome BLOCK"):
        ReleaseContract.model_validate(data)

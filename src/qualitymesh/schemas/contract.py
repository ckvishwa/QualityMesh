"""Strict schema for a versioned QualityMesh release contract.

The contract is an external trust boundary. All models therefore:

* reject unknown fields;
* use strict validation rather than implicit type coercion;
* are immutable after validation; and
* enforce fail-closed invariants at schema-validation time.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, HttpUrl, model_validator

_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_WORKFLOW_PATH_PATTERN = re.compile(r"^\.github/workflows/[A-Za-z0-9._/-]+\.ya?ml$")
_GITHUB_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

Slug = Annotated[str, Field(min_length=1, max_length=80, pattern=_SLUG_PATTERN.pattern)]
WorkflowPath = Annotated[
    str,
    Field(min_length=24, max_length=240, pattern=_WORKFLOW_PATH_PATTERN.pattern),
]
GitHubRepository = Annotated[
    str,
    Field(min_length=3, max_length=201, pattern=_GITHUB_REPOSITORY_PATTERN.pattern),
]
PositiveStrictInt = Annotated[int, Field(strict=True, ge=1)]


def _yaml_array_to_tuple(value: object) -> object:
    """Accept a YAML/JSON array and freeze it as a tuple before strict validation."""

    if isinstance(value, list):
        return tuple(cast(list[object], value))
    if isinstance(value, tuple):
        return cast(tuple[object, ...], value)
    # ValueError, not TypeError: Pydantic only converts ValueError and AssertionError into
    # ValidationError. A TypeError would escape model_validate uncaught.
    raise ValueError("expected an array")


class StrictContractModel(BaseModel):
    """Base configuration for all release-contract models."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class Decision(StrEnum):
    """Possible deterministic gate outcomes."""

    PASS = "PASS"  # noqa: S105 - release decision, not a credential
    WARN = "WARN"
    BLOCK = "BLOCK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceKind(StrEnum):
    """Evidence types supported by the initial release-contract schema."""

    JUNIT = "junit"
    PLAYWRIGHT = "playwright"
    ARTIFACT_ATTESTATION = "artifact-attestation"
    SBOM = "sbom"
    SCA_SCAN = "sca-scan"
    CONTAINER_SCAN = "container-scan"
    DEPLOYMENT_SMOKE = "deployment-smoke"
    CRITICAL_JOURNEY = "critical-journey"
    CUSTOM = "custom"


class EvidenceSubject(StrEnum):
    """Entity to which an evidence item must be cryptographically bound."""

    SOURCE_REVISION = "source-revision"
    ARTIFACT = "artifact"
    DEPLOYED_SERVICE = "deployed-service"


def _parse_evidence_kind(value: object) -> EvidenceKind:
    """Accept only a known string value or an existing EvidenceKind instance."""

    if isinstance(value, EvidenceKind):
        return value
    if not isinstance(value, str):
        raise ValueError("evidence kind must be a string")
    try:
        return EvidenceKind(value)
    except ValueError as exc:
        raise ValueError(f"unknown evidence kind: {value}") from exc


def _parse_evidence_subject(value: object) -> EvidenceSubject:
    """Accept only a known string value or an existing EvidenceSubject instance."""

    if isinstance(value, EvidenceSubject):
        return value
    if not isinstance(value, str):
        raise ValueError("evidence subject must be a string")
    try:
        return EvidenceSubject(value)
    except ValueError as exc:
        raise ValueError(f"unknown evidence subject: {value}") from exc


class ContractMetadata(StrictContractModel):
    """Human-readable identity and schema revision of the contract."""

    name: Slug
    version: PositiveStrictInt


class OIDCTrust(StrictContractModel):
    """Expected GitHub Actions OIDC issuer and audience."""

    issuer: HttpUrl = HttpUrl("https://token.actions.githubusercontent.com")
    audience: Annotated[str, Field(min_length=3, max_length=200)] = "qualitymesh"


class TrustedWorkflow(StrictContractModel):
    """A workflow allowed to produce release evidence."""

    id: Slug
    path: WorkflowPath
    allowed_refs: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=255)], ...],
        BeforeValidator(_yaml_array_to_tuple),
    ] = Field(alias="allowedRefs")
    allowed_events: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=64)], ...],
        BeforeValidator(_yaml_array_to_tuple),
    ] = Field(alias="allowedEvents")

    @model_validator(mode="after")
    def validate_non_empty_unique_values(self) -> Self:
        if not self.allowed_refs:
            raise ValueError("allowed_refs must contain at least one trusted ref pattern")
        if not self.allowed_events:
            raise ValueError("allowed_events must contain at least one GitHub event")
        if len(set(self.allowed_refs)) != len(self.allowed_refs):
            raise ValueError("allowed_refs must not contain duplicates")
        if len(set(self.allowed_events)) != len(self.allowed_events):
            raise ValueError("allowed_events must not contain duplicates")
        return self


class TrustBoundary(StrictContractModel):
    """Repositories, workflows, identity providers, and digest rules we trust."""

    repository: GitHubRepository
    default_branch: Annotated[str, Field(min_length=1, max_length=255)] = Field(
        alias="defaultBranch"
    )
    oidc: OIDCTrust = OIDCTrust()
    trusted_workflows: Annotated[
        tuple[TrustedWorkflow, ...], BeforeValidator(_yaml_array_to_tuple)
    ] = Field(alias="trustedWorkflows")
    allowed_digest_algorithms: Annotated[
        tuple[Literal["sha256"], ...], BeforeValidator(_yaml_array_to_tuple)
    ] = Field(default=("sha256",), alias="allowedDigestAlgorithms")
    require_artifact_attestation: bool = Field(
        default=True, alias="requireArtifactAttestation", strict=True
    )

    @model_validator(mode="after")
    def validate_workflow_and_digest_uniqueness(self) -> Self:
        if not self.trusted_workflows:
            raise ValueError("trustedWorkflows must contain at least one workflow")

        workflow_ids = [workflow.id for workflow in self.trusted_workflows]
        if len(set(workflow_ids)) != len(workflow_ids):
            raise ValueError("trusted workflow IDs must be unique")

        workflow_paths = [workflow.path for workflow in self.trusted_workflows]
        if len(set(workflow_paths)) != len(workflow_paths):
            raise ValueError("trusted workflow paths must be unique")

        if not self.allowed_digest_algorithms:
            raise ValueError("allowedDigestAlgorithms must not be empty")
        return self


class RequiredEvidenceItem(StrictContractModel):
    """One named evidence obligation for a release candidate."""

    id: Slug
    kind: Annotated[EvidenceKind, BeforeValidator(_parse_evidence_kind)]
    subject: Annotated[EvidenceSubject, BeforeValidator(_parse_evidence_subject)]
    producer_workflow: Slug = Field(alias="producerWorkflow")
    required: bool = Field(default=True, strict=True)
    artifact_id: Slug | None = Field(default=None, alias="artifactId")
    minimum_items: PositiveStrictInt = Field(default=1, alias="minimumItems")
    maximum_age_seconds: PositiveStrictInt | None = Field(default=None, alias="maximumAgeSeconds")

    @model_validator(mode="after")
    def validate_subject_binding(self) -> Self:
        if self.subject is EvidenceSubject.ARTIFACT and self.artifact_id is None:
            raise ValueError("artifactId is required when subject is 'artifact'")
        if self.subject is not EvidenceSubject.ARTIFACT and self.artifact_id is not None:
            raise ValueError("artifactId is only valid when subject is 'artifact'")
        return self


class ThresholdRule(StrictContractModel):
    """A count-based rule whose outcome is evaluated in Phase 3."""

    maximum: Annotated[int, Field(strict=True, ge=0)]
    outcome: Literal[Decision.BLOCK, Decision.WARN]


class PolicyRules(StrictContractModel):
    """Fail-closed policy outcomes and configurable release thresholds."""

    artifact_mismatch: Literal[Decision.BLOCK] = Field(alias="artifactMismatch")
    untrusted_workflow: Literal[Decision.BLOCK] = Field(alias="untrustedWorkflow")
    invalid_attestation: Literal[Decision.BLOCK] = Field(alias="invalidAttestation")
    missing_required_evidence: Literal[Decision.INSUFFICIENT_EVIDENCE] = Field(
        alias="missingRequiredEvidence"
    )
    failed_critical_journey: Literal[Decision.BLOCK] = Field(alias="failedCriticalJourney")
    exposed_secrets: ThresholdRule = Field(alias="exposedSecrets")
    critical_vulnerabilities: ThresholdRule = Field(alias="criticalVulnerabilities")
    suspicious_test_count_reduction_percent: Annotated[
        int,
        Field(strict=True, ge=0, le=100),
    ] = Field(default=20, alias="suspiciousTestCountReductionPercent")

    @model_validator(mode="after")
    def validate_security_rules_fail_closed(self) -> Self:
        if self.exposed_secrets.outcome is not Decision.BLOCK:
            raise ValueError("exposedSecrets must have outcome BLOCK")
        if self.critical_vulnerabilities.outcome is not Decision.BLOCK:
            raise ValueError("criticalVulnerabilities must have outcome BLOCK")
        return self


class ReleaseContract(StrictContractModel):
    """Root schema for `release-contract.yaml`."""

    api_version: Literal["qualitymesh.dev/v1"] = Field(alias="apiVersion")
    kind: Literal["ReleaseContract"]
    metadata: ContractMetadata
    trust_boundary: TrustBoundary = Field(alias="trustBoundary")
    required_evidence: Annotated[
        tuple[RequiredEvidenceItem, ...], BeforeValidator(_yaml_array_to_tuple)
    ] = Field(alias="requiredEvidence")
    policy_rules: PolicyRules = Field(alias="policyRules")

    @model_validator(mode="after")
    def validate_cross_references_and_uniqueness(self) -> Self:
        if not self.required_evidence:
            raise ValueError("requiredEvidence must contain at least one item")

        evidence_ids = [item.id for item in self.required_evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("required evidence IDs must be unique")

        trusted_workflow_ids = {workflow.id for workflow in self.trust_boundary.trusted_workflows}
        unknown_workflows = sorted(
            {
                item.producer_workflow
                for item in self.required_evidence
                if item.producer_workflow not in trusted_workflow_ids
            }
        )
        if unknown_workflows:
            joined = ", ".join(unknown_workflows)
            raise ValueError(f"required evidence references unknown workflows: {joined}")

        artifact_attestations = [
            item
            for item in self.required_evidence
            if item.kind is EvidenceKind.ARTIFACT_ATTESTATION and item.required
        ]
        if self.trust_boundary.require_artifact_attestation and not artifact_attestations:
            raise ValueError(
                "requiredEvidence must include a required artifact-attestation item when "
                "requireArtifactAttestation is true"
            )

        return self

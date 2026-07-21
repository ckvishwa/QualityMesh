"""Deterministic, offline verification that evidence is bound to a release under evaluation.

Implements `PRD.md` FR-5. Binding verification answers one question: does the evidence
submitted for a required item actually concern the specific commit or artifact this
release evaluation is about? It answers that question through pure comparison of
already-typed data — no parsing, no network access, no OIDC, no cryptographic
verification.

Three kinds of object are compared, and they must not be confused with one another:

* :class:`~qualitymesh.schemas.evidence.EvidenceEnvelope` protects and identifies the
  submitted evidence *payload*. Its `digest` is a hash of the raw evidence bytes — the
  JUnit report, for instance — and is never compared against, or treated as, an
  artifact's digest. Conflating the two would be a real correctness defect: a test
  report's own hash and a deployed artifact's hash are unrelated facts.
* A binding claim (:class:`CommitBindingClaim` / :class:`ArtifactBindingClaim`)
  describes what the evidence *claims* to concern — which required item it satisfies,
  which workflow claims to have produced it, and which commit or artifact it claims to
  be about. Claims are untrusted. A matching `producer_workflow` claim means only "the
  submitted identifier matches the workflow the contract requires" — it does not prove
  the named workflow actually produced the evidence. That requires identity verification
  (OIDC, FR-3) and provenance verification (artifact attestation), neither of which
  exists yet.
* An evaluation target (:class:`CommitEvaluationTarget` / :class:`ArtifactEvaluationTarget`)
  is the canonical commit or artifact this release evaluation concerns, supplied by
  whatever orchestrates the evaluation. It is never derived from the evidence or its
  claims — it is the independent reference the claims are checked against.

Verification never returns `BOUND` for a check it could not perform. A required
comparison that genuinely cannot be made — no claim was submitted, or no evaluation
target is available — is reported as `INSUFFICIENT_INFORMATION`, distinct from a
positive mismatch (`NOT_BOUND`). Neither is ever silently treated as success.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, cast

from pydantic import Field

from qualitymesh.schemas.contract import EvidenceSubject, ReleaseContract, Slug
from qualitymesh.schemas.evidence import EvidenceEnvelope, StrictEvidenceModel

_UnformattedIdentifier = Annotated[str, Field(min_length=1, max_length=256)]
"""A claimed or canonical commit SHA, artifact digest, or digest-algorithm name.

No format for any of these is established anywhere in the repository — `SYSTEM_RULES.md`
and `ARCHITECTURE.md` describe the *concepts* (commit, digest, algorithm) but define no
pattern, and `EvidenceEnvelope.digest` itself carries no declared format either. Bounding
length without asserting an invented format (a specific hex pattern, a fixed length)
follows the same restraint already applied to `TrustedWorkflow.allowed_refs` entries in
`contract.py`. This is deliberately not `Slug`: `Slug` requires a leading letter, which
would reject the many real commit SHAs and hex digests that start with a digit.
"""


class CommitBindingClaim(StrictEvidenceModel):
    """An untrusted claim that evidence concerns a specific commit.

    Structurally cannot carry artifact-only fields — `extra="forbid"` rejects them at
    construction, so "commit claim carrying artifact identity" is a schema-level
    rejection, not a verifier-level check.
    """

    subject: Literal[EvidenceSubject.SOURCE_REVISION] = EvidenceSubject.SOURCE_REVISION
    requirement_id: Slug
    producer_workflow: Slug
    commit_sha: _UnformattedIdentifier


class ArtifactBindingClaim(StrictEvidenceModel):
    """An untrusted claim that evidence concerns a specific artifact.

    All three identity fields are required with no default — omitting any of them is a
    schema-level rejection ("artifact claim missing required identity"), not a
    verifier-level check.
    """

    subject: Literal[EvidenceSubject.ARTIFACT] = EvidenceSubject.ARTIFACT
    requirement_id: Slug
    producer_workflow: Slug
    artifact_id: Slug
    artifact_digest: _UnformattedIdentifier
    digest_algorithm: _UnformattedIdentifier


BindingClaim = CommitBindingClaim | ArtifactBindingClaim
"""A binding claim submitted alongside evidence. Deliberately not a single generic
identifier field: commit identity, artifact identity and artifact digest are distinct
facts, and a discriminated pair of models prevents them being confused with one another
or with each other's fields."""


class CommitEvaluationTarget(StrictEvidenceModel):
    """The canonical commit this release evaluation actually concerns.

    Supplied by whoever orchestrates the evaluation. Never derived from evidence or its
    claims — this is the independent reference claims are checked against.
    """

    subject: Literal[EvidenceSubject.SOURCE_REVISION] = EvidenceSubject.SOURCE_REVISION
    commit_sha: _UnformattedIdentifier


class ArtifactEvaluationTarget(StrictEvidenceModel):
    """The canonical artifact this release evaluation actually concerns."""

    subject: Literal[EvidenceSubject.ARTIFACT] = EvidenceSubject.ARTIFACT
    artifact_id: Slug
    artifact_digest: _UnformattedIdentifier
    digest_algorithm: _UnformattedIdentifier


EvaluationTarget = CommitEvaluationTarget | ArtifactEvaluationTarget
"""The canonical release-evaluation target. See `CommitEvaluationTarget` /
`ArtifactEvaluationTarget`."""


class BindingStatus(StrEnum):
    """The three possible outcomes of a binding-verification check."""

    BOUND = "bound"
    NOT_BOUND = "not-bound"
    INSUFFICIENT_INFORMATION = "insufficient-information"


class BindingReason(StrEnum):
    """Why a binding-verification check did not resolve to `BOUND`.

    Ordered here to match the precedence `verify_evidence_binding` checks in: the most
    foundational relationship failures first, `INSUFFICIENT_INFORMATION` causes last.
    """

    NO_BINDING_CLAIM_SUBMITTED = "no-binding-claim-submitted"
    UNKNOWN_REQUIREMENT = "unknown-requirement"
    EVIDENCE_KIND_MISMATCH = "evidence-kind-mismatch"
    EVIDENCE_SUBJECT_MISMATCH = "evidence-subject-mismatch"
    CLAIM_SUBJECT_MISMATCH = "claim-subject-mismatch"
    CLAIM_TARGET_SUBJECT_MISMATCH = "claim-target-subject-mismatch"
    PRODUCER_WORKFLOW_MISMATCH = "producer-workflow-mismatch"
    DIGEST_ALGORITHM_NOT_ALLOWED = "digest-algorithm-not-allowed"
    NO_EVALUATION_TARGET_SUPPLIED = "no-evaluation-target-supplied"
    COMMIT_MISMATCH = "commit-mismatch"
    ARTIFACT_ID_MISMATCH = "artifact-id-mismatch"
    ARTIFACT_DIGEST_MISMATCH = "artifact-digest-mismatch"
    ARTIFACT_DIGEST_ALGORITHM_MISMATCH = "artifact-digest-algorithm-mismatch"


class BindingVerificationResult(StrictEvidenceModel):
    """The outcome of one `verify_evidence_binding` call.

    `reason` is `None` if and only if `status` is `BOUND`. `detail` is a concise,
    specific diagnostic naming the compared values — it never echoes evidence payload
    content, since none is available to this verifier in the first place.
    """

    status: BindingStatus
    reason: BindingReason | None
    detail: str


def _bound(detail: str) -> BindingVerificationResult:
    return BindingVerificationResult(status=BindingStatus.BOUND, reason=None, detail=detail)


def _not_bound(reason: BindingReason, detail: str) -> BindingVerificationResult:
    return BindingVerificationResult(status=BindingStatus.NOT_BOUND, reason=reason, detail=detail)


def _insufficient(reason: BindingReason, detail: str) -> BindingVerificationResult:
    return BindingVerificationResult(
        status=BindingStatus.INSUFFICIENT_INFORMATION, reason=reason, detail=detail
    )


def verify_evidence_binding(
    contract: ReleaseContract,
    envelope: EvidenceEnvelope,
    binding_claims: BindingClaim | None,
    evaluation_target: EvaluationTarget | None,
) -> BindingVerificationResult:
    """Verify that `envelope`, under `binding_claims`, is bound to `evaluation_target`.

    Checks run in a fixed precedence — foundational relationship failures first,
    `INSUFFICIENT_INFORMATION` only once every check that could resolve without the
    missing information has already been attempted. The result carries exactly one
    reason: this mirrors `Decision`'s own most-severe-wins precedence in
    `contract.py` rather than accumulating every simultaneous problem.

    `envelope.digest` (the evidence payload's own hash) never participates in any
    comparison here — only `binding_claims` and `evaluation_target` fields do.
    """
    if binding_claims is None:
        return _insufficient(
            BindingReason.NO_BINDING_CLAIM_SUBMITTED,
            "no binding claim was submitted for this evidence",
        )

    requirement = next(
        (item for item in contract.required_evidence if item.id == binding_claims.requirement_id),
        None,
    )
    if requirement is None:
        return _not_bound(
            BindingReason.UNKNOWN_REQUIREMENT,
            f"requirement {binding_claims.requirement_id!r} is not declared in the contract",
        )

    if envelope.kind is not requirement.kind:
        return _not_bound(
            BindingReason.EVIDENCE_KIND_MISMATCH,
            f"evidence kind {envelope.kind.value!r} does not match requirement "
            f"{requirement.id!r} kind {requirement.kind.value!r}",
        )

    if envelope.subject is not requirement.subject:
        return _not_bound(
            BindingReason.EVIDENCE_SUBJECT_MISMATCH,
            f"evidence subject {envelope.subject.value!r} does not match requirement "
            f"{requirement.id!r} subject {requirement.subject.value!r}",
        )

    if binding_claims.subject is not envelope.subject:
        return _not_bound(
            BindingReason.CLAIM_SUBJECT_MISMATCH,
            f"binding claim subject {binding_claims.subject.value!r} does not match "
            f"evidence subject {envelope.subject.value!r}",
        )

    if evaluation_target is not None and binding_claims.subject is not evaluation_target.subject:
        return _not_bound(
            BindingReason.CLAIM_TARGET_SUBJECT_MISMATCH,
            f"binding claim subject {binding_claims.subject.value!r} does not match "
            f"evaluation target subject {evaluation_target.subject.value!r}",
        )

    if binding_claims.producer_workflow != requirement.producer_workflow:
        return _not_bound(
            BindingReason.PRODUCER_WORKFLOW_MISMATCH,
            f"claimed producer workflow {binding_claims.producer_workflow!r} does not "
            f"match required producer workflow {requirement.producer_workflow!r}",
        )

    if isinstance(binding_claims, ArtifactBindingClaim):
        if binding_claims.digest_algorithm not in contract.trust_boundary.allowed_digest_algorithms:
            return _not_bound(
                BindingReason.DIGEST_ALGORITHM_NOT_ALLOWED,
                f"claimed digest algorithm {binding_claims.digest_algorithm!r} is not in "
                f"the trust boundary's allowed algorithms "
                f"{contract.trust_boundary.allowed_digest_algorithms!r}",
            )

    if evaluation_target is None:
        return _insufficient(
            BindingReason.NO_EVALUATION_TARGET_SUPPLIED,
            "no canonical evaluation target was supplied to compare the claim against",
        )

    if isinstance(binding_claims, CommitBindingClaim):
        # `binding_claims.subject is evaluation_target.subject` was already confirmed
        # above, and both discriminated unions share the same two subjects, so the
        # target here is a CommitEvaluationTarget by construction.
        commit_target = cast(CommitEvaluationTarget, evaluation_target)
        if binding_claims.commit_sha != commit_target.commit_sha:
            return _not_bound(
                BindingReason.COMMIT_MISMATCH,
                f"claimed commit {binding_claims.commit_sha!r} does not match "
                f"evaluation target commit {commit_target.commit_sha!r}",
            )
        return _bound(f"evidence is bound to commit {commit_target.commit_sha!r}")

    artifact_target = cast(ArtifactEvaluationTarget, evaluation_target)
    if binding_claims.artifact_id != artifact_target.artifact_id:
        return _not_bound(
            BindingReason.ARTIFACT_ID_MISMATCH,
            f"claimed artifact id {binding_claims.artifact_id!r} does not match "
            f"evaluation target artifact id {artifact_target.artifact_id!r}",
        )
    if binding_claims.artifact_digest != artifact_target.artifact_digest:
        return _not_bound(
            BindingReason.ARTIFACT_DIGEST_MISMATCH,
            f"claimed artifact digest does not match evaluation target artifact digest "
            f"for artifact {artifact_target.artifact_id!r}",
        )
    if binding_claims.digest_algorithm != artifact_target.digest_algorithm:
        return _not_bound(
            BindingReason.ARTIFACT_DIGEST_ALGORITHM_MISMATCH,
            f"claimed digest algorithm {binding_claims.digest_algorithm!r} does not "
            f"match evaluation target digest algorithm "
            f"{artifact_target.digest_algorithm!r}",
        )
    return _bound(f"evidence is bound to artifact {artifact_target.artifact_id!r}")

# Product Requirements Document — QualityMesh

> **Document status.** This PRD describes the intended V1 product. Only a subset of it is
> implemented today. Every requirement is explicitly labelled **Implemented** or **Planned**.
> See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the authoritative record of what exists.

---

## 1. Product Overview

QualityMesh is a deterministic release-assurance and evidence-evaluation engine. It verifies
whether required release evidence is structurally valid, authentic, complete, correctly bound to
its source and artifact, and compliant with a versioned release policy.

QualityMesh does not run tests, scanners or builds. It consumes the evidence those tools already
produce, verifies that the evidence is trustworthy and complete, and emits a single deterministic
release decision.

The unit of governance is the **release contract** — a versioned, declarative document that states
which evidence a release requires, which workflows are trusted to produce it, and which policy
outcome applies when a rule is violated.

---

## 2. Problem Statement

Modern delivery pipelines already generate large volumes of quality and security signal: unit test
reports, end-to-end results, SBOMs, dependency scans, container scans, build attestations. That
signal is nonetheless weak as a release gate, for four structural reasons.

1. **Evidence is not bound to an artifact.** A green test run proves that *something* passed. It
   does not prove that the bytes about to be deployed are the bytes that were tested.
2. **Absence is invisible.** A job that was skipped, cancelled, or never configured produces no
   failure. A gate that only reacts to reported failures treats silence as success.
3. **Evidence is not authenticated.** A report uploaded by an arbitrary workflow is trusted the
   same as a report uploaded by the release workflow, unless workflow identity is verified.
4. **Decisions are not reproducible.** Ad-hoc gating logic spread across pipeline scripts cannot be
   audited, versioned, or replayed against a past release.

QualityMesh addresses these by treating evidence as an untrusted external input, verifying its
authenticity and binding, checking completeness against a declared contract, and evaluating a
deterministic, versioned policy.

---

## 3. Primary User

**Primary user: the release owner** — the engineer or team accountable for deciding whether a build
may be deployed. They need a defensible, reproducible answer to "may this artifact ship?", and an
auditable record of why.

Secondary users:

- **Platform / DevEx engineers**, who author and version the release contract.
- **Security and compliance reviewers**, who need evidence that required checks actually ran and
  were bound to the deployed artifact.

---

## 4. Product Boundary

### QualityMesh does

- Define a strict, versioned release-contract schema. **(Implemented)**
- Reject malformed, ambiguous or under-specified contracts at the trust boundary. **(Implemented)**
- Ingest release evidence submitted by CI workflows. **(Planned)**
- Verify the identity of the workflow that submitted evidence. **(Planned)**
- Verify content digests to establish that evidence and artifact bytes are unchanged. **(Planned)**
- Parse evidence formats defensively, without executing or expanding untrusted content. **(Planned)**
- Determine whether all contractually required evidence is present, fresh and correctly bound.
  **(Planned)**
- Evaluate a deterministic policy and emit exactly one release decision. **(Planned)**
- Preserve raw evidence and the derived decision for audit and replay. **(Planned)**

### QualityMesh does not

- Execute tests, builds, scanners or deployments.
- Judge whether the code under test is correct, secure or fit for purpose.
- Detect vulnerabilities, malware or backdoors on its own. It consumes the findings of tools that do.
- Prove that an artifact is safe. It proves that the artifact is the one that was evaluated, and
  that the required evaluations were present and within policy.
- Replace code review, threat modelling, or human release judgement.
- Guarantee defect-free or malware-free software.

---

## 5. Core Security Scenarios

### Threat A — Post-test artifact substitution

The artifact proposed for deployment differs from the artifact that was tested and approved.

*Mechanism.* Tests and scans run against build output A. Between approval and deployment, a
different artifact B is promoted — through a rebuilt image with the same tag, a mutable registry
reference, a manual override, or a compromised promotion step.

*QualityMesh response (Planned).* Every evidence item is bound to a subject. Artifact-subject
evidence carries the artifact digest it was produced against. At evaluation time the digest of the
artifact proposed for deployment is compared against the digest recorded in the evidence. A
mismatch is a `BLOCK` and is not configurable — the contract schema pins `artifactMismatch` to
`BLOCK` today. **(Schema-level pinning implemented; digest comparison planned.)**

*What this does not prove.* A digest match proves byte equality. It does not prove that the matched
bytes are safe.

### Threat B — Missing or suppressed evidence

A required test, security scan or release check is absent, skipped, cancelled, malformed or
intentionally suppressed.

*Mechanism.* A job is removed from the workflow, gated behind a condition that silently evaluates
false, cancelled, or made to emit an empty or truncated report. No failure is reported, because
nothing ran.

*QualityMesh response (Planned).* Completeness is evaluated against the contract, not against what
was submitted. Each required evidence item declares a producing workflow, a minimum item count and
optionally a maximum age. Evidence that is absent, empty, stale or unparseable yields
`INSUFFICIENT_EVIDENCE`, never `PASS`. The contract schema pins `missingRequiredEvidence` to
`INSUFFICIENT_EVIDENCE`. **(Schema-level pinning and per-item obligations implemented; completeness
evaluation planned.)**

*What this does not prove.* Presence of a report proves the check ran and reported. It does not
prove the check was well-designed or its assertions meaningful.

### Threat C — Malicious dependency before build

A malicious dependency is included before the artifact is built.

*Mechanism.* A compromised or typosquatted package enters the dependency graph. It is present in
the source tree at build time, so it is faithfully included in the artifact. Every integrity
control downstream then correctly confirms that the malicious artifact is the artifact that was
approved.

*QualityMesh response (Planned).* Integrity controls cannot address this threat. QualityMesh
instead requires dependency-facing evidence — SBOM and SCA scan items in the contract — and applies
threshold rules to their findings. `criticalVulnerabilities` and `exposedSecrets` are constrained
by the schema to `BLOCK` outcomes. **(Schema-level constraint implemented; SBOM/SCA ingestion and
threshold evaluation planned.)**

*What this does not prove.* A clean scan proves that no *known* malicious or vulnerable component
was detected by the scanner used. It does not prove the dependency graph is safe.

### The integrity boundary

> **Integrity proves that the approved bytes did not change. It does not prove that the approved
> bytes were safe in the first place.**

Threats A and B are integrity and completeness problems, and QualityMesh is designed to address
them directly. Threat C is a content-safety problem. QualityMesh can only require that
content-safety evidence exists and enforce thresholds on its findings; the quality of the answer
remains the scanner's.

### Explicit non-claims

The following statements are false and must not appear in QualityMesh documentation, marketing,
error messages or reports:

| Non-claim | Why it is false |
| --- | --- |
| Pydantic detects tampering | Pydantic performs structural and semantic validation of a parsed document. A tampered document that remains well-formed validates successfully. Tamper evidence comes from digests and signatures. |
| OIDC proves a runner is uncompromised | An OIDC token asserts *which* workflow identity requested it. A compromised runner holding a legitimately issued token produces a valid assertion. OIDC establishes identity, not integrity of the execution environment. |
| SHA-256 proves an artifact is safe | A digest proves byte equality against a reference. Malicious bytes hash just as consistently as benign ones. |
| A GitHub check alone prevents deployment | A check is a status signal. Prevention requires branch or environment protection rules that are configured to require it, and that cannot be bypassed. QualityMesh emits the signal; enforcement is the platform's. |
| QualityMesh guarantees defect-free or malware-free software | QualityMesh evaluates the presence, authenticity, binding and policy-compliance of evidence. It does not evaluate the software. |

---

## 6. Release Decisions

QualityMesh emits exactly one decision per evaluation.

```text
BLOCK
INSUFFICIENT_EVIDENCE
WARN
PASS
```

| Decision | Meaning |
| --- | --- |
| `BLOCK` | A policy rule was violated by evidence that was present and understood. The release must not proceed. |
| `INSUFFICIENT_EVIDENCE` | Required evidence was absent, empty, stale, unparseable, or not bound to the correct subject. The release cannot be judged, and therefore must not proceed. |
| `WARN` | A non-blocking threshold was exceeded, or a non-required signal was degraded. The release may proceed with a recorded caveat. |
| `PASS` | All required evidence was present, authentic, correctly bound and within policy. |

`BLOCK` and `INSUFFICIENT_EVIDENCE` are distinguished deliberately. "We know this is bad" and "we
cannot tell whether this is bad" are different facts and demand different remediation, even though
both stop the release.

**(Implemented:** the `Decision` enumeration and the schema constraints that pin fail-closed rules
to `BLOCK` / `INSUFFICIENT_EVIDENCE`. **Planned:** the evaluation engine that produces a decision.)

---

## 7. Decision Precedence

When multiple rules produce outcomes, the most severe outcome wins:

```text
BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS
```

Precedence rules:

1. Any `BLOCK` produces an overall `BLOCK`, regardless of other outcomes.
2. Absent a `BLOCK`, any `INSUFFICIENT_EVIDENCE` produces an overall `INSUFFICIENT_EVIDENCE`.
3. Absent both, any `WARN` produces an overall `WARN`.
4. `PASS` is emitted only when every required rule evaluated to `PASS`.
5. `PASS` is never the result of an unevaluated, errored or skipped rule. An internal failure to
   evaluate a required rule is `INSUFFICIENT_EVIDENCE`, not `PASS`.

**(Planned.** Precedence is specified here and enforced by the policy evaluator, which does not
exist yet.)

---

## 8. V1 Functional Requirements

Identifiers are stable and referenced by tests and by `PROJECT_STATUS.md`.

### FR-1 — Release contract schema **(Implemented)**

- FR-1.1 The contract is a versioned document identified by `apiVersion: qualitymesh.dev/v1` and
  `kind: ReleaseContract`.
- FR-1.2 Unknown fields are rejected at every level of the document.
- FR-1.3 Values are strictly typed; implicit coercion (for example the string `"1"` for an integer
  field) is rejected.
- FR-1.4 Validated contract models are immutable.
- FR-1.5 The contract declares a trust boundary: repository, default branch, OIDC issuer and
  audience, trusted workflows, allowed digest algorithms, and whether artifact attestation is
  required.
- FR-1.6 The contract declares required evidence items, each with a stable identifier, kind,
  subject, producing workflow, required flag, minimum item count and optional maximum age.
- FR-1.7 Stable identifiers are unique within their scope; trusted workflow identifiers and paths
  are unique; required evidence identifiers are unique.
- FR-1.8 Cross-references resolve: every `producerWorkflow` must name a declared trusted workflow.
- FR-1.9 Evidence with subject `artifact` must declare an `artifactId`; evidence with any other
  subject must not.
- FR-1.10 When `requireArtifactAttestation` is true, at least one required `artifact-attestation`
  evidence item must be declared.
- FR-1.11 Fail-closed policy outcomes cannot be weakened in a contract: `artifactMismatch`,
  `untrustedWorkflow`, `invalidAttestation` and `failedCriticalJourney` are constrained to `BLOCK`;
  `missingRequiredEvidence` is constrained to `INSUFFICIENT_EVIDENCE`; the `exposedSecrets` and
  `criticalVulnerabilities` threshold rules are constrained to a `BLOCK` outcome.

### FR-2 — Contract loading **(Implemented)**

- FR-2.1 Load a release contract from a YAML file using a safe, non-executing loader.
- FR-2.2 Reject YAML constructs that permit object instantiation or arbitrary evaluation.
- FR-2.3 Bound input size and nesting depth. Also bounded: per-sequence length, per-mapping size and
  total node count. YAML aliases are refused outright, because alias expansion occurs during
  construction and cannot be bounded afterwards.
- FR-2.4 Report validation failures with the field path and a diagnostic message. Diagnostics name
  the source and the YAML position, and never echo document content.
- FR-2.5 Provide valid and malformed contract fixtures with test coverage for each rejection rule.

### FR-3 — Evidence ingestion **(Planned)**

- FR-3.1 Accept evidence submissions authenticated by a GitHub Actions OIDC token.
- FR-3.2 Verify the token issuer and audience against the contract trust boundary.
- FR-3.3 Verify that the submitting workflow, ref and event are permitted by the contract.
- FR-3.4 Compute the digest of raw evidence before any normalisation.
- FR-3.5 Persist raw evidence unmodified.
- FR-3.6 Record the repository, workflow, run, commit and artifact subject associated with each
  submission.

### FR-4 — Evidence parsing **(Partially implemented — JUnit only)**

- FR-4.1 Parse JUnit XML using a hardened parser that disables external entity and DTD processing.
  **(Implemented.)**
- FR-4.2 Parse JSON evidence with bounded size and depth. **(Planned — no evidence kind other than
  JUnit has a parser yet.)**
- FR-4.3 Treat a parse failure as unusable evidence contributing to `INSUFFICIENT_EVIDENCE`, not as
  a silent success. **(Implemented for JUnit** as an explicit `UnusableJUnitEvidence` result;
  contributing it to an actual `INSUFFICIENT_EVIDENCE` decision is the policy evaluator's job,
  which does not exist yet.)
- FR-4.4 Link every normalised record to the raw evidence it was derived from. **(Implemented for
  JUnit** via `raw_digest` on both `ParsedJUnitEvidence` and `UnusableJUnitEvidence`.)

### FR-5 — Binding verification **(Implemented)**

- FR-5.1 Verify that artifact-subject evidence carries a digest matching the artifact under
  evaluation. **(Implemented** via `ArtifactBindingClaim` vs `ArtifactEvaluationTarget` comparison
  in `binding/verifier.py`. The claim is untrusted submitted metadata, not derived from evidence
  content — see FR-3, still planned, for how a real claim would be submitted.)
- FR-5.2 Verify that source-revision-subject evidence carries the commit under evaluation.
  **(Implemented** via `CommitBindingClaim` vs `CommitEvaluationTarget`.)
- FR-5.3 Reject digests using an algorithm not listed in `allowedDigestAlgorithms`. **(Implemented**
  — `DIGEST_ALGORITHM_NOT_ALLOWED`, checked against `trust_boundary.allowed_digest_algorithms`.)

### FR-6 — Completeness evaluation **(Implemented)**

- FR-6.1 For each required evidence item, determine whether sufficient, fresh, correctly bound
  evidence exists. **(Implemented** via `evaluate_completeness` in `completeness/evaluator.py`,
  consuming `BindingVerificationResult` from FR-5 rather than re-deriving binding.)
- FR-6.2 Evaluate `minimumItems` and `maximumAgeSeconds`. **(Implemented.** Freshness required a new
  minimal carrier, `EvidenceCandidate.observed_at` — no component recorded an evidence timestamp
  before this; see `PROJECT_STATUS.md`'s Task 1.5 entry.)
- FR-6.3 Emit `INSUFFICIENT_EVIDENCE` for any unsatisfied required item. **(Implemented** as
  `CompletenessOutcome.INSUFFICIENT_EVIDENCE` — a task-specific enum, not `Decision`, since
  completeness alone is necessary but not sufficient for `Decision.PASS`; the future policy
  evaluator (FR-7) is what maps this into an actual `Decision`.)

### FR-7 — Policy evaluation **(Planned)**

- FR-7.1 Evaluate all contract rules against normalised evidence.
- FR-7.2 Apply the precedence in section 7 to produce exactly one decision.
- FR-7.3 Produce a per-rule result set explaining the decision.
- FR-7.4 Be deterministic: identical contract, evidence and artifact inputs produce an identical
  decision.

### FR-8 — Reporting **(Planned)**

- FR-8.1 Emit a machine-readable release report containing the decision, per-rule results and
  evidence references.
- FR-8.2 Publish the decision as a GitHub check.
- FR-8.3 State plainly in the report what the decision does and does not prove.

---

## 9. Non-Functional Requirements

### NFR-1 — Determinism

Given identical inputs, QualityMesh produces identical output. No decision depends on wall-clock
time except through explicitly declared freshness windows, which take the evaluation timestamp as
an input rather than reading the clock implicitly.

### NFR-2 — Fail-closed behaviour

No error path produces `PASS`. Missing, malformed, contradictory, expired, incomplete or untrusted
required evidence produces `INSUFFICIENT_EVIDENCE` or `BLOCK`.

### NFR-3 — Type safety

Pyright operates in strict mode across `src` and `tests`. Public functions carry complete
annotations. `Any` is confined to external boundaries and justified where used. **(Configured; see
`[tool.pyright]` in `pyproject.toml`.)**

### NFR-4 — Input hostility

All external input — contracts, evidence, tokens, digests — is treated as attacker-controlled.
Parsers are hardened, sizes and depths bounded, and unknown fields rejected.

### NFR-5 — Auditability

Raw evidence, its digest, its submission context and the resulting decision are retained so that
any past release decision can be reconstructed and explained. **(Planned.)**

### NFR-6 — Reproducible builds and environments

Dependencies are locked in `uv.lock`, which is committed. CI installs from the frozen lockfile.

### NFR-7 — Observability

Evaluations emit structured traces and metrics covering ingestion, parsing, evaluation and
decision. **(Planned; OpenTelemetry dependencies are declared but unused.)**

### NFR-8 — Testability

Every rule that can change a release decision is covered by a test that asserts the specific
rejection or outcome, not merely that an error occurred.

---

## 10. Acceptance Scenarios

### AS-1 — Valid contract is accepted **(Implemented and tested)**

*Given* a well-formed release contract declaring two trusted workflows and three required evidence
items, *when* it is validated, *then* validation succeeds and the parsed model exposes the declared
metadata, evidence items and policy rules.

### AS-2 — Unknown contract field is rejected **(Implemented and tested)**

*Given* a contract containing a field not defined by the schema, *when* it is validated, *then*
validation fails with an extra-inputs error.

### AS-3 — Type coercion is rejected **(Implemented and tested)**

*Given* a contract whose `metadata.version` is the string `"1"`, *when* it is validated, *then*
validation fails rather than coercing to the integer `1`.

### AS-4 — Duplicate evidence identifiers are rejected **(Implemented and tested)**

*Given* a contract declaring two required evidence items with the same identifier, *when* it is
validated, *then* validation fails with a uniqueness error.

### AS-5 — Dangling workflow reference is rejected **(Implemented and tested)**

*Given* a required evidence item whose `producerWorkflow` does not name a declared trusted
workflow, *when* the contract is validated, *then* validation fails and names the unresolved
workflow.

### AS-6 — Artifact binding is required **(Implemented and tested)**

*Given* an evidence item with subject `artifact` and no `artifactId`, *when* the contract is
validated, *then* validation fails.

### AS-7 — Required attestation cannot be omitted **(Implemented and tested)**

*Given* a contract with `requireArtifactAttestation: true` and no required `artifact-attestation`
evidence item, *when* it is validated, *then* validation fails.

### AS-8 — Fail-closed rules cannot be weakened **(Implemented and tested)**

*Given* a contract that sets `artifactMismatch` to `WARN`, or `exposedSecrets.outcome` to `WARN`,
*when* it is validated, *then* validation fails.

### AS-9 — Contract loads from YAML **(Implemented and tested)**

*Given* a release contract on disk, *when* it is loaded, *then* it is parsed with a safe loader and
validated, and malformed documents are rejected with a field-path diagnostic.

### AS-10 — Substituted artifact is blocked **(Planned)**

*Given* evidence produced against artifact digest A and a deployment proposing digest B, *when* the
release is evaluated, *then* the decision is `BLOCK` with an artifact-mismatch reason.

### AS-11 — Skipped required check yields insufficient evidence **(Planned)**

*Given* a contract requiring a security scan and a run in which that job was skipped, *when* the
release is evaluated, *then* the decision is `INSUFFICIENT_EVIDENCE`, not `PASS`.

### AS-12 — Untrusted workflow submission is blocked **(Planned)**

*Given* evidence submitted by a workflow not declared in the trust boundary, *when* the release is
evaluated, *then* the decision is `BLOCK`.

---

## 11. V1 Definition of Done

V1 is complete when all of the following hold. None of the items marked *outstanding* are complete
today.

1. The release contract schema is strict, versioned and fully covered by unit tests. **(Done.)**
2. Contracts load from YAML through a safe loader, with valid and malformed fixtures covering every
   rejection rule. **(Done.)**
3. Evidence can be submitted, authenticated by OIDC, and persisted with its pre-normalisation
   digest. *(Outstanding.)*
4. JUnit and JSON evidence parse through hardened parsers, with parse failure treated as unusable
   evidence. *(Partially done — JUnit implemented; JSON outstanding.)*
5. Artifact and source-revision binding is verified against declared digest algorithms.
   **(Done** — the pure comparison exists; a real caller wiring it to actual ingestion and
   evaluation-target data does not yet, since ingestion itself is FR-3, still outstanding.)
6. Completeness evaluation covers required flags, minimum item counts and freshness windows.
   **(Done** — the pure evaluation exists; a real caller supplying it with actual evidence and
   timestamps does not yet, since that depends on FR-3 ingestion, still outstanding.)
7. The policy evaluator is deterministic and implements the precedence in section 7.
   *(Outstanding.)*
8. Threat scenarios A, B and C have end-to-end tests asserting the expected decision.
   *(Outstanding.)*
9. A release report is emitted and published as a GitHub check. *(Outstanding.)*
10. The full validation suite passes: `pytest`, `ruff check`, `ruff format --check`, `pyright`
    strict. *(Passing as of 2026-07-20 — see `PROJECT_STATUS.md`.)*
11. `PRD.md`, `ARCHITECTURE.md`, `SYSTEM_RULES.md` and `PROJECT_STATUS.md` describe the shipped
    system accurately, with no planned component described as implemented. *(Done for the current
    state; must be maintained.)*

---

## 12. Deferred Scope

Explicitly out of scope for V1, and not to be built speculatively:

- CI providers other than GitHub Actions.
- A policy expression language. V1 policy is a fixed, declarative rule set.
- A web user interface. V1 output is a machine-readable report plus a GitHub check.
- Multi-tenancy, organisations, roles and permissions.
- A message broker. V1 uses a PostgreSQL outbox. See the planned ADR-0004.
- Signature verification schemes beyond GitHub artifact attestation.
- Independent vulnerability analysis. QualityMesh consumes scanner findings; it does not produce
  them.
- Any AI or model-driven evaluation. Release decisions are deterministic by design.
- Automatic remediation, waivers or exception workflows.
- Historical trend analytics and dashboards.

---

## 13. Known Limitations

These limitations are inherent to the design and are not defects.

1. **Evidence quality is inherited.** QualityMesh verifies that required evidence exists, is
   authentic and is within policy. It cannot assess whether a test suite is meaningful or a scanner
   is well-configured. A contract satisfied by weak evidence yields a weak guarantee.
2. **Integrity is not safety.** Digest verification proves byte equality against an approved
   reference. It cannot distinguish approved-and-benign from approved-and-malicious.
3. **Identity is not integrity.** OIDC verification establishes which workflow identity submitted
   evidence. It cannot establish that the runner executing that workflow was uncompromised.
4. **Scanner blind spots propagate.** Threat C is mitigated only to the extent that the configured
   SBOM and SCA tooling detects the malicious component. An undetected compromise produces clean
   evidence and a legitimate `PASS`.
5. **Enforcement is delegated.** QualityMesh emits a decision. Whether that decision actually
   prevents a deployment depends on branch and environment protection configured outside
   QualityMesh, and on those protections not being bypassable.
6. **Contract correctness is the author's responsibility.** The schema rejects malformed and
   internally inconsistent contracts and prevents fail-closed rules from being weakened. It cannot
   detect that a contract omits an evidence obligation the release actually needs.
7. **Freshness depends on trusted timestamps.** Maximum-age evaluation relies on timestamps
   associated with evidence submission. A compromised producer can influence them.

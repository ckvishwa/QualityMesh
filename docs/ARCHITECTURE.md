# QualityMesh System Architecture

> **Document status.** Section 2 describes what exists in the repository today. Sections 3 onward
> describe the intended design and are **planned unless explicitly marked otherwise**. No component
> in this document may be assumed to exist because it is described here.

---

## 1. Architectural Goals

1. **Deterministic decisions.** Identical contract, evidence and artifact inputs must produce an
   identical decision. No hidden clock reads, no iteration-order dependence, no network-dependent
   branching in evaluation.
2. **Fail closed.** Every failure mode — missing evidence, parse failure, storage error, timeout,
   unhandled exception in a required rule — must resolve to `INSUFFICIENT_EVIDENCE` or `BLOCK`.
   `PASS` is only reachable through complete, successful evaluation.
3. **Hostile input by default.** Contracts, evidence, tokens and digests are attacker-controlled
   until proven otherwise. Validation happens at the boundary, once, strictly.
4. **Raw evidence is immutable.** What was submitted is preserved byte-for-byte and digested before
   any normalisation. Derived data references the raw record; it never replaces it.
5. **Explicit trust.** Every trust relationship — repository, workflow, ref, event, OIDC issuer and
   audience, digest algorithm — is declared in the versioned contract, not inferred.
6. **Honest boundaries.** Each mechanism carries exactly one guarantee. The architecture must not
   let a component's guarantee be silently borrowed by another.
7. **Auditability.** A past decision must be reconstructable from retained inputs.

---

## 2. Current Implementation

This section is the factual record of the repository as it stands.

### Present

```text
qualitymesh/
├── pyproject.toml                              # project, dependencies, tool configuration
├── uv.lock                                     # committed dependency lockfile
├── README.md
├── .gitignore
├── docs/                                       # this documentation set
├── src/qualitymesh/
│   ├── __init__.py                             # package docstring only
│   ├── binding/
│   │   ├── __init__.py                         # re-exports the binding-verification API
│   │   └── verifier.py                         # evidence-to-contract binding verification
│   ├── completeness/
│   │   ├── __init__.py                         # re-exports the completeness-evaluation API
│   │   └── evaluator.py                        # evidence completeness evaluation
│   ├── contracts/
│   │   ├── __init__.py                         # re-exports the loader API
│   │   └── loader.py                           # safe YAML loading of a contract
│   ├── parsers/
│   │   ├── __init__.py                         # re-exports the JUnit parser API
│   │   └── junit.py                            # hardened JUnit XML parsing
│   └── schemas/
│       ├── __init__.py                         # re-exports public schema models
│       ├── contract.py                         # strict release-contract models
│       └── evidence.py                         # strict evidence-envelope model
└── tests/
    ├── fixtures/contracts/                     # valid and malformed contract documents
    ├── fixtures/junit/                         # valid and hostile JUnit XML documents
    └── unit/
        ├── test_contract_schema.py             # schema validation tests
        ├── test_contract_loader.py             # loader boundary tests
        ├── test_evidence_schema.py             # evidence-envelope tests
        ├── test_junit_parser.py                # JUnit parser boundary tests
        ├── test_binding_verifier.py            # binding cross-object comparison tests
        ├── test_binding_claims_and_targets.py  # binding claim/target schema tests
        └── test_completeness_evaluator.py      # completeness evaluation tests
```

### Implemented components

**Python workspace.** A `src/`-layout package built with Hatchling, targeting Python 3.12+,
dependency-managed by uv with a committed `uv.lock`.

**Strict release-contract models** (`src/qualitymesh/schemas/contract.py`). A Pydantic v2 model
tree rooted at `ReleaseContract`, whose shared base `StrictContractModel` configures
`extra="forbid"`, `frozen=True`, `strict=True`, `populate_by_name=True`,
`str_strip_whitespace=True` and `validate_default=True`.

Models: `ReleaseContract`, `ContractMetadata`, `TrustBoundary`, `TrustedWorkflow`, `OIDCTrust`,
`RequiredEvidenceItem`, `PolicyRules`, `ThresholdRule`, `StrictContractModel`.

Enumerations: `Decision` (`PASS`, `WARN`, `BLOCK`, `INSUFFICIENT_EVIDENCE`); `EvidenceKind`
(`junit`, `playwright`, `artifact-attestation`, `sbom`, `sca-scan`, `container-scan`,
`deployment-smoke`, `critical-journey`, `custom`); `EvidenceSubject` (`source-revision`,
`artifact`, `deployed-service`).

Constrained types: `Slug`, `WorkflowPath`, `GitHubRepository`, `PositiveStrictInt`.

Validation behaviour enforced at schema level:

- Unknown fields rejected at every nesting level.
- Strict typing — no coercion of numeric strings, no truthy-value coercion for booleans.
- YAML/JSON arrays converted to immutable tuples by a `BeforeValidator`, so that strict mode accepts
  ordinary YAML sequence syntax while the validated model remains immutable.
- Enum members parsed from known string values only, with a named error for unknown values.
- Non-empty and uniqueness invariants on `allowedRefs`, `allowedEvents`, trusted workflow IDs and
  paths, allowed digest algorithms, and required evidence IDs.
- Cross-reference resolution: `producerWorkflow` must name a declared trusted workflow.
- Subject binding: `artifactId` is mandatory when and only when the subject is `artifact`.
- Attestation obligation: `requireArtifactAttestation: true` requires at least one required
  `artifact-attestation` evidence item.
- Fail-closed policy pinning: `artifactMismatch`, `untrustedWorkflow`, `invalidAttestation` and
  `failedCriticalJourney` are `Literal[Decision.BLOCK]`; `missingRequiredEvidence` is
  `Literal[Decision.INSUFFICIENT_EVIDENCE]`; `exposedSecrets` and `criticalVulnerabilities` are
  validated to carry a `BLOCK` outcome.

**Contract validation tests** (`tests/unit/test_contract_schema.py`). Nine unit tests over a shared
valid-contract builder, asserting acceptance of a valid contract and specific, message-matched
rejection of unknown fields, coerced types, duplicate evidence IDs, dangling workflow references,
missing artifact binding, omitted required attestation, weakened fail-closed rules and a weakened
security threshold outcome.

**Safe contract loading** (`src/qualitymesh/contracts/loader.py`). `load_release_contract_bytes` and
`load_release_contract_file` treat a contract document as untrusted input in fixed order: byte-size
limit before parsing, refusal of YAML aliases before construction, `yaml.safe_load`, document-shape
checks (single document, mapping root, string keys), bounded traversal (nesting depth, sequence
length, mapping size, total nodes), then `ReleaseContract.model_validate`.

`ContractLoadError` covers that pre-schema boundary only; schema failures surface as
`pydantic.ValidationError`. Diagnostics name the source and the YAML position and never include
document content. Covered by 52 tests and 35 fixtures under `tests/fixtures/contracts/`.

**Evidence envelope** (`src/qualitymesh/schemas/evidence.py`). `EvidenceEnvelope` wraps raw evidence
bytes with their declared `EvidenceKind` and `EvidenceSubject`, plus a `digest`/`digest_algorithm`
computed from the raw bytes as a Pydantic `computed_field` — never accepted as constructor input, so
a submitted digest cannot be trusted as proof. Does not carry producer/workflow/run/commit claims;
no field set or format for them is defined anywhere in the repository yet (see FR-3).

**Hardened JUnit parsing** (`src/qualitymesh/parsers/junit.py`). `parse_junit_evidence(envelope, *,
source_name)` returns `ParsedJUnitEvidence | UnusableJUnitEvidence` — never raises for malformed or
hostile evidence content, only for a caller precondition violation (wrong envelope kind). Uses
`defusedxml` with `forbid_dtd`, `forbid_entities` and `forbid_external` all explicitly `True` (the
library defaults to `forbid_dtd=False`). Bounds byte size (pre-parse), nesting depth, element count
and testcase count (post-parse — safe because DTD/entity forbidding removes the only amplification
vector before any tree is built). Suite-declared counts are verified against actual testcases, not
trusted at face value. Both outcome types link back to the envelope via `raw_digest`. Covered by 40
tests and 15 fixtures under `tests/fixtures/junit/`, including DTD, external-entity and
entity-expansion attack fixtures.

**Evidence-to-contract binding verification** (`src/qualitymesh/binding/verifier.py`).
`verify_evidence_binding(contract, envelope, binding_claims, evaluation_target)` returns a
`BindingVerificationResult` with status `BOUND` / `NOT_BOUND` / `INSUFFICIENT_INFORMATION` and a
single most-foundational reason. Three object families are kept structurally distinct:
`EvidenceEnvelope` (the payload and its own digest, unchanged from Task 1.3), a binding claim
(`CommitBindingClaim` / `ArtifactBindingClaim` — untrusted, submitted alongside evidence, rejecting
the other subject's fields at construction) and an evaluation target
(`CommitEvaluationTarget` / `ArtifactEvaluationTarget` — canonical, supplied by the evaluation
caller, never derived from evidence). `EvidenceEnvelope.digest` never participates in a binding
comparison — it identifies the evidence payload, not an artifact. A matching `producer_workflow`
claim establishes consistency with the contract only, not that the named workflow produced the
evidence; OIDC and cryptographic attestation verification remain unimplemented. Covered by 35 tests.

**Evidence completeness evaluation** (`src/qualitymesh/completeness/evaluator.py`).
`evaluate_completeness(contract, candidates, as_of)` returns a `CompletenessResult` — one
`RequirementCompletenessResult` per `RequiredEvidenceItem`, satisfied or not, plus any candidates
whose `requirement_id` matched nothing. `EvidenceCandidate` bundles an `EvidenceEnvelope`, a
`BindingVerificationResult` (Task 1.4's output, consumed — never re-derived) and an explicit,
required, timezone-aware `observed_at`; no component computes this automatically, and no clock is
read anywhere in the evaluator. A candidate counts only if its binding is `BOUND`, its kind and
subject match the requirement, it is not future-dated, it is within `maximumAgeSeconds` when one is
declared (inclusive boundary), and it is not a duplicate (by payload digest) of one already counted.
`CompletenessOutcome.SATISFIED` is deliberately not `Decision.PASS` — completeness is necessary but
not sufficient for a release to pass the future policy evaluator (FR-7). Covered by 32 tests.

**Quality tooling, configured.** Ruff (lint rule sets `E`, `F`, `I`, `B`, `UP`, `ASYNC`, `S`, `RUF`;
line length 100; `S101` permitted in tests) and formatter; Pyright in `strict` mode over `src` and
`tests`; pytest with `--strict-config --strict-markers` and `asyncio_mode = "strict"`; coverage
configured with branch coverage and `fail_under = 80`.

### Explicitly not implemented

There is no evidence *ingestion* (ingestion would supply real binding claims, evaluation targets and
evidence-candidate timestamps from actual submissions — FR-3), no parser for any evidence kind other
than JUnit, no policy evaluator, no persistence layer, no object storage, no worker, no HTTP API, no
OIDC verification, no GitHub integration and no CI workflow.

### Declared but unused dependencies

`pyproject.toml` declares runtime dependencies for the full planned system — FastAPI, Uvicorn,
SQLAlchemy, psycopg, Alembic, boto3, httpx, PyJWT, defusedxml, orjson, PyYAML and the OpenTelemetry
stack. **None of these are imported by any module today.** Only `pydantic` is actually used. This
is a known state, recorded in `PROJECT_STATUS.md`; the dependency set is not to be changed as part
of documentation work.

---

## 3. Planned Runtime Topology

**Status: planned. None of this exists.**

| Component | Role |
| --- | --- |
| GitHub Actions workflows | Produce evidence; submit it with an OIDC-authenticated request. |
| FastAPI ingestion API | Authenticate submissions, digest and store raw evidence, record metadata, enqueue processing. |
| Object storage (MinIO / S3) | Immutable store for raw evidence blobs, addressed by digest. |
| PostgreSQL | System of record for evidence metadata, normalised results, contracts, decisions and the outbox. |
| Outbox worker | Claims processing jobs, parses and verifies evidence, evaluates policy, publishes results. |
| Policy engine | Pure, deterministic evaluation of contract rules against normalised evidence. |
| GitHub Checks publisher | Publishes the decision back to the originating commit. |

The ingestion API and the worker are deployed separately. Ingestion must stay fast and available;
parsing and evaluation are slower, retryable and isolated from the request path.

---

## 4. Runtime Dataflow

**Status: planned.**

```text
GitHub Actions
      │
      │ OIDC-authenticated evidence submission
      ▼
FastAPI ingestion API
      │
      ├── Raw evidence → MinIO / S3
      ├── Metadata → PostgreSQL
      └── Processing job → PostgreSQL outbox
                               │
                               ▼
                      QualityMesh worker
                               │
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
             Safe parse    Hash verify    Schema validate
                 │             │             │
                 └─────────────┼─────────────┘
                               ▼
                    Evidence completeness
                               ▼
                     Release policy engine
                               ▼
       PASS / WARN / BLOCK / INSUFFICIENT_EVIDENCE
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
          GitHub Checks                Release report
```

Ordering constraint: the raw evidence digest is computed at ingestion, before the blob is written
and before any parsing or normalisation. Everything downstream references that digest.

---

## 5. Trust Boundaries

**Status: planned, except where noted.**

```text
┌─ UNTRUSTED ────────────────────────────────────────────────┐
│  Workflow-supplied evidence bytes, report contents,        │
│  claimed digests, claimed timestamps, contract documents   │
└──────────────────────┬─────────────────────────────────────┘
                       │  B1: OIDC verification + strict schema validation
┌─ SEMI-TRUSTED ───────▼─────────────────────────────────────┐
│  Evidence with a verified submitting workflow identity     │
└──────────────────────┬─────────────────────────────────────┘
                       │  B2: digest verification + subject binding
┌─ VERIFIED ───────────▼─────────────────────────────────────┐
│  Evidence bound to a specific commit and artifact digest   │
└──────────────────────┬─────────────────────────────────────┘
                       │  B3: completeness evaluation
┌─ SUFFICIENT ─────────▼─────────────────────────────────────┐
│  A complete evidence set for the declared contract         │
└──────────────────────┬─────────────────────────────────────┘
                       │  B4: deterministic policy evaluation
┌─ DECIDED ────────────▼─────────────────────────────────────┐
│  Exactly one decision, with per-rule reasons               │
└────────────────────────────────────────────────────────────┘
```

B1 (strict schema validation of the contract document) and the subject-binding half of B2
(`verify_evidence_binding`, comparing an untrusted claim against a canonical evaluation target) are
implemented. B2's OIDC-identity half and B3/B4 remain planned. Task 1.4's binding verification is a
pure comparison function — nothing yet calls it with claims or targets sourced from a real
submission, since that requires the ingestion boundary (B1's evidence-side half, FR-3) to exist
first.

Crossing a boundary is one-way and explicit. Code operating at a higher trust level must never
re-derive facts from lower-level data without going back through the boundary check.

### What each boundary does and does not establish

| Boundary | Establishes | Does not establish |
| --- | --- | --- |
| B1 — OIDC | Which workflow identity submitted this evidence | That the runner was uncompromised |
| B1 — schema validation | The document is structurally and semantically well-formed | That the document is truthful or untampered |
| B2 — digest | These bytes equal the reference bytes | That the bytes are safe |
| B2 — subject binding | This evidence concerns this commit and this artifact | That the evidence is a meaningful test of it |
| B3 — completeness | Everything the contract demanded is present | That the contract demanded enough |
| B4 — policy | A reproducible decision under a versioned policy | That the software is correct or safe |

---

## 6. Component Responsibilities

Each mechanism carries exactly one guarantee. These boundaries are load-bearing: violating them is
how a system comes to claim more security than it has.

```text
Pydantic
→ Structural and semantic validation

SHA-256
→ Content equality and tamper evidence

GitHub OIDC
→ Workflow identity

Artifact attestation
→ Build provenance claims

Policy engine
→ Deterministic release decision
```

Expanded, with the boundary each must not cross:

**Pydantic — structural and semantic validation.** Rejects unknown fields, wrong types, invalid
enum values, broken cross-references and violated invariants. It does **not** detect tampering: a
maliciously altered but well-formed document validates cleanly. Tamper evidence comes from digests
and signatures alone. **(Implemented for the contract.)**

**SHA-256 — content equality and tamper evidence.** Proves that two byte sequences are identical
and detects modification relative to a recorded reference. It does **not** prove that the bytes are
safe, well-formed or benign. **(Digest computation implemented** — `EvidenceEnvelope.digest`
identifies raw evidence bytes, computed by QualityMesh itself, never accepted as input.
**Comparison implemented** — `verify_evidence_binding` compares a claimed artifact digest against a
canonical target digest. This comparison is over opaque, caller-supplied strings, not bytes
QualityMesh has hashed itself; it establishes that two claimed values agree, not that either
reflects real, hashed content. Verifying a *claim*'s digest against *actual* artifact bytes remains
planned — that requires the artifact bytes to exist somewhere QualityMesh can read them, which no
component yet provides.)**

**GitHub OIDC — workflow identity.** A verified token asserts which repository, workflow, ref and
event requested it. It does **not** prove the runner was uncompromised, nor that the evidence
carried alongside the token is accurate. **(Planned.)**

**Artifact attestation — build provenance claims.** Asserts that a specific builder produced a
specific artifact from specific inputs. It is a claim about *how* the artifact was built. It does
**not** assert that the inputs were safe — Threat C survives a valid attestation intact.
**(Planned.)**

**Policy engine — deterministic release decision.** Given normalised evidence and a versioned
contract, produces exactly one decision plus per-rule reasons. It does **not** assess software
quality; it assesses evidence against declared rules. **(Planned.)**

---

## 7. Evidence Processing Lifecycle

**Status: planned.**

1. **Submit.** A trusted workflow POSTs evidence with an OIDC token.
2. **Authenticate.** Verify token signature, issuer, audience, and that repository, workflow path,
   ref and event are permitted by the contract trust boundary. Reject otherwise.
3. **Digest.** Compute the SHA-256 digest of the raw request body before any parsing or
   normalisation. This digest is the evidence's identity.
4. **Store raw.** Write the unmodified bytes to object storage, addressed by digest. Never mutate.
5. **Record metadata.** Persist repository, workflow, run ID, commit SHA, artifact subject, declared
   evidence kind, submission timestamp and the raw digest.
6. **Enqueue.** Insert an outbox row in the same transaction as the metadata write.
7. **Parse.** The worker reads raw bytes and parses them with a hardened parser for the declared
   kind. Failure marks the evidence unusable — it does not silently drop the item.
8. **Normalise.** Produce a typed, structured record that references the raw digest. Raw evidence
   is never overwritten.
9. **Verify binding.** Confirm that digests use an allowed algorithm and that the evidence subject
   matches the commit or artifact under evaluation.
10. **Retain.** Raw evidence, normalised records and the link between them are retained for audit.

Invariant: **digest before normalise**. If normalisation ran first, the digest would attest to
QualityMesh's interpretation rather than to what the workflow actually submitted.

---

## 8. Planned Persistence Model

**Status: planned. No schema, migration or ORM model exists.**

PostgreSQL is the system of record; object storage holds blobs only. Intended entities:

| Entity | Purpose |
| --- | --- |
| `release_contract` | Stored contract documents, versioned and content-addressed. |
| `evidence_submission` | One row per submission: raw digest, storage key, workflow identity, run, commit, artifact subject, timestamps. |
| `normalised_evidence` | Typed results derived from a submission, referencing its raw digest. |
| `release_evaluation` | One evaluation of one artifact against one contract version. |
| `rule_result` | Per-rule outcome and reason within an evaluation. |
| `outbox` | Processing jobs, written transactionally with the data that produced them. |

Principles: raw evidence rows are insert-only; evaluations are immutable once decided; a decision
row records the contract version and evidence digests it consumed, so it can be replayed. Schema
changes go through Alembic migrations, forward-only and reviewed.

**The transactional outbox** is deliberate. Writing evidence metadata and publishing a job as two
independent operations admits a window where one succeeds and the other does not — producing either
lost evidence or a job referencing nothing. Writing both in one database transaction removes the
window. A broker can be introduced later behind the same interface. See planned ADR-0004.

---

## 9. Release Evaluation Lifecycle

**Status: steps 4 and the subject-binding half of step 3 implemented as pure functions; the
end-to-end lifecycle — including the I/O that would gather real evidence and construct real
`EvaluationTarget`/`EvidenceCandidate` values — remains planned.**

1. **Resolve contract.** Load the contract version applicable to the release under evaluation.
2. **Resolve subject.** Identify the commit and artifact digest proposed for release.
3. **Gather evidence.** Collect verified evidence bound to that commit and artifact digest.
   **(`verify_evidence_binding` implemented — Task 1.4; gathering real evidence to feed it is
   ingestion, FR-3, still planned.)**
4. **Evaluate completeness.** For each required evidence item, check presence, `minimumItems`,
   `maximumAgeSeconds` and subject binding. Any shortfall yields `INSUFFICIENT_EVIDENCE`.
   **(Implemented** — `evaluate_completeness`, Task 1.5, produces `CompletenessOutcome.INSUFFICIENT_EVIDENCE`
   under exactly this condition.)
5. **Evaluate rules.** Apply policy rules to normalised evidence, producing a per-rule outcome.
6. **Apply precedence.** `BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS`. The most severe outcome
   wins.
7. **Record.** Persist the decision, per-rule results, contract version and consumed evidence
   digests.
8. **Publish.** Emit the release report and a GitHub check.

The evaluation core is a pure function of `(contract, evidence set, artifact digest, evaluation
timestamp)`. I/O happens before and after, never inside. This is what makes determinism testable.

---

## 10. Failure and Retry Behavior

**Status: planned.**

| Failure | Behaviour |
| --- | --- |
| OIDC verification fails | Reject the submission at the API. No evidence is stored. |
| Object storage write fails | Fail the submission; do not commit metadata. No partial record. |
| Database write fails | Transaction rolls back; nothing is enqueued. |
| Worker crashes mid-job | The outbox row is not marked complete and is reclaimed after a visibility timeout. |
| Parse failure | The evidence is recorded as unusable with its diagnostic. It contributes to `INSUFFICIENT_EVIDENCE`. It is not retried, because it is deterministic. |
| Transient downstream failure | Retried with bounded exponential backoff. |
| Retries exhausted on a required rule | The evaluation resolves to `INSUFFICIENT_EVIDENCE`. |
| GitHub Checks publication fails | The decision is already persisted; publication is retried independently and never alters the decision. |

Two rules govern all of it. **Processing is idempotent** — evidence is identified by its raw digest,
so reprocessing converges rather than duplicating. **No failure yields `PASS`** — an unevaluated
required rule is insufficient evidence, never a pass.

---

## 11. Security Controls

**Status: planned unless marked.**

**Input validation.** Strict schema validation with unknown-field rejection at every external
boundary. **(Implemented for the contract and the evidence envelope.)**

**Safe parsing.** YAML via a non-constructing safe loader, with aliases refused before construction
**(implemented for the contract)**; XML via `defusedxml` with DTD, entities and external references
all explicitly forbidden **(implemented for JUnit)**; JSON with bounded size and nesting depth
**(planned — no evidence kind besides JUnit is parsed yet)**. No evidence format is ever `eval`'d,
deserialised into arbitrary objects, or expanded without bounds.

**Resource bounds.** Maximum request body size, maximum parse depth, maximum element counts
**(implemented for JUnit evidence)**, and evaluation timeouts, so that a hostile submission cannot
exhaust the worker.

**Identity verification.** OIDC token signature, issuer, audience, expiry and claims checked against
the contract trust boundary. Workflow path, ref and event must be explicitly permitted.

**Digest discipline.** Only algorithms in `allowedDigestAlgorithms` are accepted — currently
`sha256` only, enforced by the schema. **(Constraint implemented.)** Digests are computed by
QualityMesh over raw bytes; a digest merely *claimed* by a submitter is never trusted as proof.

**Immutability.** Raw evidence is write-once. Validated contract models are frozen. **(Model
immutability implemented.)**

**Least privilege.** Object storage credentials are scoped to the evidence bucket; database roles
are scoped per component; GitHub tokens carry only checks-write scope.

**Secret hygiene.** No secrets in the repository, in logs, or in reports. Evidence known to contain
secret material is referenced by digest rather than inlined into reports.

### What these controls do not do

They do not make an artifact safe, do not validate scanner quality, do not detect a compromised
runner, and do not prevent deployment by themselves — enforcement depends on branch and environment
protection configured outside QualityMesh.

---

## 12. Observability

**Status: planned. OpenTelemetry dependencies are declared in `pyproject.toml` but unused.**

**Traces.** A span per stage — ingest, digest, store, parse, normalise, verify, evaluate, publish —
correlated by evidence digest and evaluation ID.

**Metrics.** Submissions by workflow and kind; parse failure rate by kind; evaluation latency;
decision counts by outcome; outbox depth and age of oldest unprocessed job; retry and dead-letter
counts.

**Logs.** Structured, correlated by evaluation ID and evidence digest. Never contain evidence
payloads or secrets.

**Auditability.** Every decision retains the contract version, evidence digests and rule results
that produced it, so it can be re-derived rather than merely believed.

A specific alerting requirement: a rising `INSUFFICIENT_EVIDENCE` rate is a signal that a pipeline
has stopped producing required evidence — precisely Threat B — and must be alertable, not merely
graphable.

---

## 13. Deferred Architecture

Not to be built now, and not to be stubbed out in anticipation:

- A message broker. The PostgreSQL outbox is the V1 mechanism. See planned ADR-0004.
- Horizontal worker sharding and partitioned queues.
- Multi-region deployment and cross-region replication.
- A caching layer. Evaluations are cheap and reproducible; caching adds invalidation risk for no
  current benefit.
- A plugin system for third-party evidence kinds. `EvidenceKind` is a closed enumeration in V1.
- A policy expression or rules DSL.
- A user interface and analytics surface.
- CI providers other than GitHub Actions.
- Any AI or model-driven component. Determinism is a product requirement, not an implementation
  detail.

**Speculative structure is prohibited.** Empty packages for parsers, API, database, workers or
policy evaluation must not be created ahead of a task that implements them. Directory structure is
a claim about what exists, and it must remain true.

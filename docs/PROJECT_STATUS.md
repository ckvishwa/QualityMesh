# QualityMesh Project Status

This file is the authoritative record of what exists in the repository. Where it conflicts with
`PRD.md` or `ARCHITECTURE.md` about what is implemented, this file wins.

---

## Current Phase

```text
Phase 1 — Release Contract and Rules
```

Phase 1 establishes the release contract as the versioned source of truth for release policy: its
schema, its loading, and its validation guarantees. No ingestion, persistence, evaluation or
integration work belongs to this phase.

---

## Current Task

None in progress. Task 1.5 completed 2026-07-21.

---

## Completed

### Task 1.1 — Strict release-contract schema and focused unit tests

Implemented in `src/qualitymesh/schemas/contract.py` and covered by
`tests/unit/test_contract_schema.py`.

Delivered:

- `StrictContractModel` base configuration — `extra="forbid"`, `frozen=True`, `strict=True`,
  `populate_by_name=True`, `str_strip_whitespace=True`, `validate_default=True`.
- Root model `ReleaseContract`, pinned to `apiVersion: qualitymesh.dev/v1` and
  `kind: ReleaseContract`.
- Models `ContractMetadata`, `TrustBoundary`, `TrustedWorkflow`, `OIDCTrust`,
  `RequiredEvidenceItem`, `PolicyRules`, `ThresholdRule`.
- Enumerations `Decision`, `EvidenceKind`, `EvidenceSubject`.
- Constrained types `Slug`, `WorkflowPath`, `GitHubRepository`, `PositiveStrictInt`.
- A `BeforeValidator` that converts YAML/JSON arrays to immutable tuples, so strict validation
  accepts ordinary YAML sequence syntax while validated models remain immutable.
- Uniqueness invariants on trusted workflow IDs and paths, `allowedRefs`, `allowedEvents` and
  required evidence IDs.
- Cross-reference resolution — `producerWorkflow` must name a declared trusted workflow.
- Subject-binding invariant — `artifactId` required when and only when subject is `artifact`.
- Attestation obligation — `requireArtifactAttestation: true` requires a required
  `artifact-attestation` evidence item.
- Fail-closed policy pinning via `Literal` types and validators, so a contract author cannot weaken
  `artifactMismatch`, `untrustedWorkflow`, `invalidAttestation`, `failedCriticalJourney`,
  `missingRequiredEvidence`, `exposedSecrets.outcome` or `criticalVulnerabilities.outcome`.
- Public re-exports from `src/qualitymesh/schemas/__init__.py`.

### Task 1.2 — Safe release-contract YAML loading

Implemented in `src/qualitymesh/contracts/loader.py`, covered by
`tests/unit/test_contract_loader.py` and 34 fixtures under `tests/fixtures/contracts/`. Full
record: [`docs/tasks/TASK-1.2-release-contract-yaml-loading.md`](tasks/TASK-1.2-release-contract-yaml-loading.md).

Delivered:

- Public API `load_release_contract_bytes(data, *, source_name)` and
  `load_release_contract_file(path)`, plus `ContractLoadError` for the pre-schema boundary.
  Schema failures propagate as `pydantic.ValidationError` unchanged.
- Byte-size limit enforced before parsing, and against `stat().st_size` so an oversized file is
  never read into memory.
- `yaml.safe_load` only — tags that construct Python objects are rejected.
- YAML aliases refused before construction, since alias expansion happens inside the parser and
  cannot be bounded afterwards.
- Document-shape rejection: empty, multi-document, scalar root, sequence root, non-string keys.
- Bounded traversal: nesting depth, per-sequence length, per-mapping size, total node count, with
  the depth check applied before descending.
- Diagnostics naming the source and the PyYAML position, without echoing document content.
- One defect in Task 1.1 code found and fixed at its boundary — see *Problems encountered*.

### Task 1.3 — Evidence envelope schema and hardened JUnit XML parsing

Implemented in `src/qualitymesh/schemas/evidence.py` and `src/qualitymesh/parsers/junit.py`,
covered by `test_evidence_schema.py`, `test_junit_parser.py` and 15 fixtures under
`tests/fixtures/junit/`. Full record:
[`docs/tasks/TASK-1.3-evidence-envelope-and-junit-parser.md`](tasks/TASK-1.3-evidence-envelope-and-junit-parser.md).

Delivered:

- `EvidenceEnvelope`: kind, subject, raw bytes, plus a `digest`/`digest_algorithm` computed from
  raw bytes — never accepted as caller input, so a submitted digest cannot be trusted as proof.
- `parse_junit_evidence(envelope, *, source_name) -> ParsedJUnitEvidence | UnusableJUnitEvidence`.
  `defusedxml` with DTD, entities and external references all forbidden explicitly (the library
  defaults to allowing DTDs). Malformed, hostile, oversized or empty evidence is always returned as
  an explicit unusable result, distinct from success — never raised, never silently dropped, never
  a successful empty suite.
- Bounded byte size, nesting depth, element count and testcase count. Bounds run after parsing,
  which is safe here because DTD/entity forbidding removes the only amplification vector before
  any tree is built — verified empirically: a constructed billion-laughs-style payload is rejected
  in 0.1ms.
- Suite-declared counts (`tests`/`failures`/`errors`/`skipped`) verified against actual testcases,
  not trusted at face value.
- Producer/workflow/run/commit claims deliberately excluded — no field set or format for them is
  defined anywhere in the repository; deferred to the evidence-ingestion task (FR-3).

### Task 1.4 — Evidence-to-contract binding verification

Implemented in `src/qualitymesh/binding/verifier.py`, covered by `test_binding_verifier.py` and
`test_binding_claims_and_targets.py` (35 tests). Full record:
[`docs/tasks/TASK-1.4-evidence-to-contract-binding-verification.md`](tasks/TASK-1.4-evidence-to-contract-binding-verification.md).

A real conflict was found and reported before implementation: FR-5 requires comparing evidence
against "the commit/artifact under evaluation," but nothing on `EvidenceEnvelope` (deliberately, per
Task 1.3) carries that claim. Resolved by adding a narrow, typed binding-metadata layer — not by
modifying `EvidenceEnvelope` or inventing a generic identifier field.

Delivered:

- `CommitBindingClaim`/`ArtifactBindingClaim` (untrusted, submitted alongside evidence) and
  `CommitEvaluationTarget`/`ArtifactEvaluationTarget` (canonical, supplied by the evaluation caller,
  never derived from evidence) — discriminated pairs, not one generic identifier field, so commit
  identity, artifact identity and artifact digest can never be confused with one another.
- `verify_evidence_binding(contract, envelope, binding_claims, evaluation_target) ->
  BindingVerificationResult`, returning `BOUND` / `NOT_BOUND` / `INSUFFICIENT_INFORMATION` with a
  single most-foundational reason, deterministically, when multiple problems coexist.
- `EvidenceEnvelope.digest` (the evidence payload's hash) never participates in any binding
  comparison — verified by a dedicated test using a payload whose own digest coincidentally equals
  the target's artifact digest.
- A matching `producer_workflow` claim proves consistency with the contract, not that the named
  workflow produced the evidence — OIDC and attestation verification remain unimplemented.

### Task 1.5 — Evidence completeness evaluation

Implemented in `src/qualitymesh/completeness/evaluator.py`, covered by
`test_completeness_evaluator.py` (32 tests). Full record:
[`docs/tasks/TASK-1.5-evidence-completeness-evaluation.md`](tasks/TASK-1.5-evidence-completeness-evaluation.md).

No component in the repository recorded when evidence was produced or observed — a real gap for
FR-6.2's `maximumAgeSeconds`, verified by grep before writing any code. Resolved by adding
`EvidenceCandidate`, the minimal typed carrier `ARCHITECTURE.md` §9 already anticipated ("the
evaluation core is a pure function of `(contract, evidence set, artifact digest, evaluation
timestamp)`") — not a new decision, its concrete realization.

Delivered:

- `EvidenceCandidate`: envelope + requirement reference + `BindingVerificationResult` +
  explicit, required, timezone-aware `observed_at`. No clock is read anywhere in this module.
- `evaluate_completeness(contract, candidates, as_of) -> CompletenessResult`, pure and
  deterministic. Every `RequiredEvidenceItem` gets exactly one result, satisfied or not; contract
  and candidate order preserved throughout.
- A candidate qualifies only if its binding is `BOUND` *and* kind/subject match the requirement
  *and* it isn't future-dated *and* it's within `maximumAgeSeconds` (inclusive boundary, when set)
  *and* it isn't a duplicate (same `envelope.digest`) of one already counted.
- `CompletenessOutcome.SATISFIED` is deliberately not `Decision.PASS` — complete evidence is
  necessary but not sufficient for a release to pass FR-7's policy evaluation.
- Duplicate evidence (identical payload digest) counts once per requirement, per
  `SYSTEM_RULES.md`'s existing reprocessing-idempotency rule — not an invented decision.

### Session 0 — Documentation and project structure

- `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/SYSTEM_RULES.md`, `docs/adr/README.md` and this file
  created.
- `README.md` rewritten as a concise index.
- `.gitignore` extended with missing entries.
- Git repository initialised; documentation foundation committed as the initial commit.
- `uv.lock` regenerated against public PyPI to unblock validation — see L-2. Package set and
  `pyproject.toml` constraints unchanged.
- Full validation suite executed and recorded from actual output.

### Tooling configured (present in `pyproject.toml`)

Ruff lint (`E`, `F`, `I`, `B`, `UP`, `ASYNC`, `S`, `RUF`; line length 100) and formatter; Pyright
`strict` over `src` and `tests`; pytest with `--strict-config --strict-markers` and
`asyncio_mode = "strict"`; coverage with branch coverage and `fail_under = 80`; uv with a committed
`uv.lock`.

---

## In Progress

Nothing.

---

## Next Task

```text
Task 1.6 — Deterministic policy evaluation
```

Combine `CompletenessResult` (Task 1.5) with the contract's other policy rules (artifact mismatch,
exposed secrets, critical vulnerabilities, failed critical journey) under `Decision`'s existing
precedence (`BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS`) to produce the first real release
`Decision`. Implements `PRD.md` FR-7. Not started.

---

## Known Decisions

Decisions already made and reflected in the code. Those marked *(needs ADR)* should be captured as
ADRs before or alongside the next architecture-touching task.

| Decision | Rationale | Where it lives |
| --- | --- | --- |
| The release contract is the versioned source of truth for release policy | Deterministic, auditable, replayable decisions require a versioned declarative input | `contract.py` — [ADR-0001](adr/0001-release-contract-as-source-of-truth.md) |
| Contract models forbid unknown fields and reject type coercion | The contract is an external trust boundary; silent acceptance of unrecognised or coerced input is how policy drifts | `StrictContractModel` |
| Validated contract models are immutable | A policy input that can be mutated after validation is not a trustworthy input | `frozen=True` |
| YAML/JSON arrays are converted to tuples before strict validation | Strict mode would otherwise reject ordinary YAML sequence syntax; freezing preserves immutability | `_yaml_array_to_tuple` |
| Fail-closed policy outcomes are pinned by the type system | A contract author must not be able to downgrade `artifactMismatch` or a security threshold to `WARN` | `Literal[Decision.BLOCK]` and validators in `PolicyRules` |
| `sha256` is the only accepted digest algorithm | Single, well-understood algorithm; the list is contract-declared so it can be extended deliberately | `allowed_digest_algorithms` |
| Missing required evidence is `INSUFFICIENT_EVIDENCE`, not `BLOCK` | "Cannot tell" and "known bad" are different facts requiring different remediation, though both stop the release | `PolicyRules.missing_required_evidence` |
| `EvidenceKind` is a closed enumeration in V1 | No plugin surface until there is a real second consumer | `EvidenceKind` |
| Pyright strict over `src` and `tests` | Tests exercise security-relevant boundaries and are not exempt from type discipline | `pyproject.toml` |
| PostgreSQL outbox before a message broker | Removes the dual-write window between persisting evidence and publishing work, with no broker to operate | Planned — *(needs ADR-0004)* |
| Contract loading is two functions, not a loader class | Smallest useful surface; no repository, service layer or injection framework is warranted by one caller | `contracts/loader.py` |
| Evidence digest is a computed field, never a caller input | A submitter-claimed digest is a claim, not proof; only QualityMesh's own computation from raw bytes counts | `EvidenceEnvelope.digest` |
| JUnit parse outcome is a returned union, not a raised exception | Unlike single-document contract loading, many evidence items are gathered together; one malformed item must stay visible, not abort a batch or vanish | `ParsedJUnitEvidence \| UnusableJUnitEvidence` |
| `defusedxml` DTD forbidding is requested explicitly, not relied on as default | The library defaults to `forbid_dtd=False`; the documented security control ("DTD ... disabled") required an explicit `forbid_dtd=True` | `parsers/junit.py` |
| A zero-test suite parses as success; zero suites does not | The two are different facts — a present suite that ran nothing is real content; an empty document is not. Deciding whether zero-tests is *suspicious* is `suspiciousTestCountReductionPercent`'s job, not the parser's | `parsers/junit.py` — see TASK-1.3 |
| `ContractLoadError` covers only the pre-schema boundary | Wrapping `ValidationError` would duplicate the field locations Pydantic already produces | `contracts/loader.py` |
| Resource bounds run after parsing, before validation | PyYAML offers no streaming budget without a custom composer; the byte-size cap is the pre-parse defence | `_enforce_resource_bounds` |
| YAML aliases are refused outright | Expansion happens during construction, so no post-parse bound can contain it; a contract has no legitimate use for aliases | `_reject_aliases` |
| Validators raise `ValueError`, never `TypeError` | Pydantic converts only `ValueError` and `AssertionError` into `ValidationError`; a `TypeError` escapes `model_validate` | `contract.py` — see *Problems encountered* |
| Binding claims and evaluation targets are separate model families, not one generic identifier field | Commit identity, artifact identity and artifact digest are distinct facts a generic field would conflate | `binding/verifier.py` |
| Claim/target `digest_algorithm` is a bounded string, not `Literal["sha256"]` | Locking both to `TrustBoundary`'s single current value would make a claim-vs-target algorithm mismatch structurally unreachable | `binding/verifier.py` — see TASK-1.4 |
| `EvidenceEnvelope.digest` never participates in binding comparisons | It identifies the evidence payload, not the artifact; conflating the two is a real correctness defect, not a style choice | `verify_evidence_binding` |
| Binding verification returns one most-foundational reason, not an accumulated list | Mirrors `Decision`'s existing most-severe-wins precedence rather than a new accumulation mechanic | `binding/verifier.py` |
| `CompletenessOutcome` is a new enum, not a reuse of `Decision` | `SATISFIED` is not `Decision.PASS` — complete evidence is necessary but not sufficient for a release to pass FR-7's policy evaluation | `completeness/evaluator.py` |
| Duplicate evidence (same payload digest) counts once per requirement | `SYSTEM_RULES.md`'s existing reprocessing-idempotency rule already answers this; not an invented decision | `evaluate_completeness` — see TASK-1.5 |
| Freshness boundary is inclusive (`age <= maximumAgeSeconds` qualifies) | Matches this repo's own existing precedent — `contracts/loader.py` and `parsers/junit.py` both reject strictly `>` their size limits | `evaluate_completeness` |
| A future-dated timestamp is rejected unconditionally, independent of `maximumAgeSeconds` | An impossible timestamp is invalid on its face, not merely "not yet stale" | `evaluate_completeness` |

---

## Known Limitations

### L-1 — Version control established this session *(resolved)*

The working tree previously contained no `.git` directory, so it had no history, branch, remote or
staged state. Git was initialised in Session 0 with explicit approval, and the documentation
foundation is the initial commit. No prior history existed, so nothing was discarded.

There is no remote configured. Branch protection and the pull-request rules in `SYSTEM_RULES.md`
section 11 cannot be enforced until one is added.

### L-2 — `uv.lock` regenerated against public PyPI *(resolved)*

The original lockfile pinned all 1295 package URLs to an internal artifactory mirror at
`packages.applied-caas-gateway1.internal.api.openai.org`, which resolves to the private address
`10.192.71.42` and is unreachable from this network. `uv sync --frozen` installs from the URLs
recorded in the lockfile and cannot fall back, so every validation command was blocked.

With explicit approval, `uv.lock` was regenerated against public PyPI. The resolution is unchanged
in shape — the same 105 packages across the same 2185 lines — with 1191 URLs now pointing at
`files.pythonhosted.org` and zero at the internal mirror. Dependency constraints in
`pyproject.toml` were not modified. The full validation suite now runs; see below.

**Follow-up:** anyone working inside the original network will regenerate different URLs, producing
lockfile churn on every environment switch. If both environments must be supported, pin an index
explicitly in `pyproject.toml` rather than relying on whichever index happens to be configured.

### L-3 — `load_release_contract_file` does not guard against non-regular files

The size limit is applied to `Path.stat().st_size`. A FIFO or character device reports size 0 and
then blocks or streams unboundedly on `read_bytes()`. Deferred rather than fixed: the contract path
is supplied by the operator, not by an attacker, and no code path yet accepts a caller-controlled
path. Revisit when contract paths can originate from a request — an `S_ISREG` check is the fix.

### L-4 — Runtime dependencies are declared but entirely unused

`pyproject.toml` declares FastAPI, Uvicorn, SQLAlchemy, psycopg, Alembic, boto3, httpx, PyJWT,
defusedxml, orjson, PyYAML and the OpenTelemetry stack as runtime dependencies. No module imports
any of them; `pydantic` is the only runtime dependency actually used.

This front-loads supply-chain surface for capabilities that do not exist yet, and it makes the
dependency manifest a poor signal of what the system does. The dependency set was **not** changed
this session, as instructed. Recommend trimming to what is used and reintroducing each dependency in
the task that needs it — per `SYSTEM_RULES.md` section 8, every dependency needs a documented
engineering reason at the point of introduction.

### L-5 — The interpreter selected is Python 3.14, not the 3.12 target

`uv` provisioned CPython 3.14.4 for the virtual environment. `requires-python = ">=3.12"` permits
this, but `[tool.pyright].pythonVersion` is `"3.12"` and `[tool.ruff].target-version` is `"py312"`,
so static analysis and runtime would disagree about the language level. Task 1.2 mitigated the risk
by validating on both 3.14.4 and 3.12.13. The new validation workflow tests both Python 3.12 and
3.14 on Windows; local selection still depends on the installed interpreter unless explicitly pinned.

### L-6 — Product limitations

The inherent limits of the product itself — integrity is not safety, identity is not integrity,
evidence quality is inherited, enforcement is delegated — are documented in `PRD.md` section 13 and
are not repeated here.

---

## Deferred Work

Deliberately not built. See `PRD.md` section 12 and `ARCHITECTURE.md` section 13.

- Parsers for evidence kinds other than JUnit (Playwright, SBOM, SCA scan, container scan, artifact
  attestation, and the rest of `EvidenceKind`).
- A real caller that constructs `BindingClaim`/`EvaluationTarget`/`EvidenceCandidate` values from
  actual evidence ingestion and release state — Tasks 1.4 and 1.5 provide pure functions only.
- The deterministic policy evaluator (FR-7) — Task 1.5 provides completeness only, not a release
  decision.
- PostgreSQL persistence, Alembic migrations, object storage, the outbox worker.
- FastAPI ingestion, GitHub OIDC verification, GitHub Checks publication.
- End-to-end tests for Threats A, B and C.
- Deployment and release CI beyond the repository validation workflow.
- Message broker, caching layer, plugin system, policy DSL, user interface, multi-tenancy.
- Any AI or model-driven component.

---

## Latest Validation Results

**Audit update: 2026-11-04, local Windows run on CPython 3.14.4.** The full repository suite now
includes a separate automation harness. `uv run pytest tests/unit -q` passed 167 core tests;
`uv run pytest tests/automation -q` passed 390 automation tests after the secret-scan gate regression
test was added. The full `uv run pytest --cov=qualitymesh --cov-report=term-missing -q` run passed
557 tests with 99.49% combined statement-and-branch coverage of `src/qualitymesh`. Ruff lint,
Ruff format, and Pyright strict checks also passed locally. A public validation workflow is now
configured for Python 3.12 and 3.14; its first run is pending.

The automation orchestrator previously passed `secret_scan_clean=True` without a scan. It now
requires an explicit caller attestation, defaulting to `False`; this prevents an unverified scan
from satisfying the commit precondition. The harness still does not perform its own secret scan.

**Previous validation, 2026-07-21: all passing on CPython 3.14.4 and CPython 3.12.13.**

| Command | Result |
| --- | --- |
| `uv sync --frozen --all-groups` | **Passed.** 104 packages installed. |
| `uv run pytest` | **Passed.** 167 passed. |
| `uv run pytest --cov=qualitymesh --cov-report=term-missing` | **Passed.** 167 passed; total coverage 99.49%. |
| `uv run ruff check .` | **Passed.** `All checks passed!` |
| `uv run ruff format --check .` | **Passed.** `19 files already formatted` |
| `uv run pyright` | **Passed.** `0 errors, 0 warnings, 0 informations` |

`completeness/evaluator.py`: 100% coverage. `fail_under` remains `80`, unchanged since Task 1.1. The
two uncovered lines (`contract.py` 95, 108) predate Task 1.2 and remain deliberate accept-only
paths.

Python 3.12.13 run: identical results.

Per-task problem/resolution detail lives in the task records under `docs/tasks/`, not here — see
[TASK-1.2](tasks/TASK-1.2-release-contract-yaml-loading.md),
[TASK-1.3](tasks/TASK-1.3-evidence-envelope-and-junit-parser.md),
[TASK-1.4](tasks/TASK-1.4-evidence-to-contract-binding-verification.md), and
[TASK-1.5](tasks/TASK-1.5-evidence-completeness-evaluation.md).

---

## Last Updated

**2026-11-04** — Scope and security audit, secret-scan precondition corrected, public validation workflow configured.

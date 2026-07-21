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

None in progress. Task 1.3 completed 2026-07-20.

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

Nothing. Session 0 is the only active work and it is complete pending review.

---

## Next Task

```text
Task 1.4 — Evidence-to-contract binding
```

Verify a `ParsedJUnitEvidence`'s subject and digest against the commit/artifact under evaluation and
against a `RequiredEvidenceItem`'s declared `producerWorkflow`, implementing `PRD.md` FR-5. Still
pure and offline — binding verification compares already-available digests and identifiers, not a
new I/O boundary. Not started.

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
by validating on both 3.14.4 and 3.12.13, but that is a manual step, not a guarantee. Still worth
pinning deliberately — via `.python-version` or a narrower `requires-python` — and enforcing both
versions in CI once CI exists, rather than leaving it to whatever uv selects.

### L-6 — Product limitations

The inherent limits of the product itself — integrity is not safety, identity is not integrity,
evidence quality is inherited, enforcement is delegated — are documented in `PRD.md` section 13 and
are not repeated here.

---

## Deferred Work

Deliberately not built. See `PRD.md` section 12 and `ARCHITECTURE.md` section 13.

- Evidence-to-contract binding verification (FR-5); parsers for evidence kinds other than JUnit
  (Playwright, SBOM, SCA scan, container scan, artifact attestation, and the rest of
  `EvidenceKind`).
- Completeness evaluation and the deterministic policy evaluator.
- PostgreSQL persistence, Alembic migrations, object storage, the outbox worker.
- FastAPI ingestion, GitHub OIDC verification, GitHub Checks publication.
- End-to-end tests for Threats A, B and C.
- CI pipeline.
- Message broker, caching layer, plugin system, policy DSL, user interface, multi-tenancy.
- Any AI or model-driven component.

---

## Latest Validation Results

**Run date: 2026-07-20. Status: all passing on CPython 3.14.4 and CPython 3.12.13.**

Output below is copied from actual command runs.

| Command | Result |
| --- | --- |
| `uv sync --frozen --all-groups` | **Passed.** 104 packages installed. |
| `uv run pytest` | **Passed.** 100 passed in 1.3s. |
| `uv run pytest --cov=qualitymesh --cov-report=term-missing` | **Passed.** 100 passed; total coverage 99.25%. |
| `uv run ruff check .` | **Passed.** `All checks passed!` |
| `uv run ruff format --check .` | **Passed.** `12 files already formatted` |
| `uv run pyright` | **Passed.** `0 errors, 0 warnings, 0 informations` |

```text
Name                                    Stmts   Miss Branch BrPart  Cover   Missing
-----------------------------------------------------------------------------------
src\qualitymesh\contracts\loader.py        83      0     28      0   100%
src\qualitymesh\parsers\junit.py          156      0     42      0   100%
src\qualitymesh\schemas\contract.py       157      2     44      2    98%   95, 108
src\qualitymesh\schemas\evidence.py        19      0      0      0   100%
-----------------------------------------------------------------------------------
TOTAL                                     422      2    114      2    99%
Required test coverage of 80.0% reached. Total coverage: 99.25%
```

Coverage rose from 98.73% (Task 1.2) to 99.25%. `fail_under` remains `80`, unchanged. The two
remaining uncovered lines (`contract.py` 95, 108) predate Task 1.2 — see that task's record for why
they're accept-only paths left deliberately uncovered.

Python 3.12.13 run: identical — `100 passed`, `0 errors, 0 warnings, 0 informations`,
`All checks passed!`.

Per-task problem/resolution detail lives in the task records under `docs/tasks/`, not here — see
[TASK-1.2](tasks/TASK-1.2-release-contract-yaml-loading.md) and
[TASK-1.3](tasks/TASK-1.3-evidence-envelope-and-junit-parser.md).

---

## Last Updated

**2026-07-20** — Task 1.3, evidence envelope schema and hardened JUnit XML parsing.

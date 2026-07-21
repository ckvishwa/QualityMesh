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

None in progress. Task 1.2 completed 2026-07-20.

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
Task 1.3 — Evidence envelope schema and hardened JUnit XML parsing
```

Add the strict evidence-envelope model and a `defusedxml`-based JUnit parser with DTD and external
entity processing disabled, bounded size and depth, and parse failure recorded as unusable evidence
rather than a silent skip. Pure and offline, so it needs no API, database or worker. Implements
`PRD.md` FR-4. Not started.

---

## Known Decisions

Decisions already made and reflected in the code. Those marked *(needs ADR)* should be captured as
ADRs before or alongside the next architecture-touching task.

| Decision | Rationale | Where it lives |
| --- | --- | --- |
| The release contract is the versioned source of truth for release policy | Deterministic, auditable, replayable decisions require a versioned declarative input | `contract.py` — *(needs ADR-0001)* |
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

- Evidence models, hardened parsers, digest verification, binding verification.
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
| `uv sync --frozen --all-groups` | **Passed.** 105 packages installed. |
| `uv run pytest` | **Passed.** 61 passed in 0.81s. |
| `uv run pytest --cov=qualitymesh --cov-report=term-missing` | **Passed.** 61 passed; total coverage 98.73%. |
| `uv run ruff check .` | **Passed.** `All checks passed!` |
| `uv run ruff format --check .` | **Passed.** `7 files already formatted` |
| `uv run pyright` | **Passed.** `0 errors, 0 warnings, 0 informations` |

`uv run pytest --cov=qualitymesh --cov-report=term-missing`:

```text
Name                                    Stmts   Miss Branch BrPart  Cover   Missing
-----------------------------------------------------------------------------------
src\qualitymesh\__init__.py                 0      0      0      0   100%
src\qualitymesh\contracts\__init__.py       2      0      0      0   100%
src\qualitymesh\contracts\loader.py        83      0     28      0   100%
src\qualitymesh\schemas\__init__.py         2      0      0      0   100%
src\qualitymesh\schemas\contract.py       157      2     44      2    98%   95, 108
-----------------------------------------------------------------------------------
TOTAL                                     244      2     72      2    99%
Required test coverage of 80.0% reached. Total coverage: 98.73%
61 passed in 0.90s
```

Coverage rose from **80.30% to 98.73%**. `fail_under` remains `80`, unchanged. Every fail-closed
rejection branch previously listed as a gap is now exercised through a fixture.

The two remaining uncovered lines are `contract.py` 95 and 108 — the `return value` identity paths
in `_parse_evidence_kind` and `_parse_evidence_subject`. They are reachable only by constructing a
model with an `EvidenceKind` or `EvidenceSubject` instance in Python; the YAML boundary always
yields strings. They are accept paths, not rejection branches, and were left uncovered rather than
exercised through an artificial route.

Per `SYSTEM_RULES.md` section 7, coverage does not prove correctness. What the number reflects here
is that each rejection rule has a test asserting its specific diagnostic, not merely that some error
occurred.

### Python 3.12 verification

Run in an environment outside the repository via `UV_PROJECT_ENVIRONMENT`, so the primary `.venv`
was untouched.

```text
Python 3.12.13
61 passed in 0.86s
0 errors, 0 warnings, 0 informations     # pyright
All checks passed!                       # ruff check
```

### Problems encountered

**Three validators raised `TypeError`, which escaped `model_validate` uncaught.** Pydantic v2
converts only `ValueError` and `AssertionError` into `ValidationError`. `_yaml_array_to_tuple`,
`_parse_evidence_kind` and `_parse_evidence_subject` each raised `TypeError`, so a caller writing
`except ValidationError` would have seen an uncaught `TypeError` for three realistic malformed
contracts. The rejection still occurred — this was not fail-open — but it surfaced in the wrong type
at the wrong boundary.

*Resolution:* the three `raise TypeError` statements became `raise ValueError`, fixed at the schema
boundary rather than caught in the loader, which would have patched the symptom and left every other
caller exposed. No validation was weakened; the same inputs are still rejected, now with a field
location. All nine Task 1.1 tests remained green and unmodified.

**Post-parse resource bounds did not contain YAML alias amplification.** Found by the review of the
finished diff, in this task's own code. `yaml.safe_load` expands aliases during construction, so
`MAX_TOTAL_NODES` fired only after the cost had been paid: a 375-byte document — far under the
256 KiB limit — consumed 7.63 seconds and roughly 4.8 million constructed nodes before being
rejected. One more nesting level is 9× that.

*Resolution:* `_reject_aliases` streams the document through `yaml.parse`, which yields events
without constructing anything, and refuses any alias before `safe_load` runs. The same payload is
now rejected in 0.28s. Covered by a fixture and by a constructed amplification payload.

**Remaining known limitation:** `src/` contains exactly one type-checker suppression, a narrow
`# pyright: ignore[reportUnknownMemberType]` on the `yaml.parse` call. types-pyyaml declares it as
`(stream: Unknown, ...) -> Unknown`, and the event stream is the only way to inspect a document
before construction; the result is `cast` so nothing downstream is unknown. Justified under
`SYSTEM_RULES.md` section 5 and recorded in the task file.

---

## Last Updated

**2026-07-20** — Task 1.2, safe release-contract YAML loading.

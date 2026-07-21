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

**Session 0 — Documentation and project structure.**

Governance and documentation only. This session established the `docs/` tree, the PRD, the
architecture document, the engineering rules, the ADR conventions and this status file, and
reconciled the README and `.gitignore` with the actual repository. No application code, schema or
dependency was changed.

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
Task 1.2 — Release-contract YAML loading and fixture coverage
```

Add safe release-contract YAML loading, valid fixtures, malformed fixtures and semantic validation
coverage. Not started.

Not blocked. The validation suite runs and passes. Task 1.2 should also close the uncovered
validator rejection branches listed under *Latest Validation Results*.

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

### L-3 — Runtime dependencies are declared but entirely unused

`pyproject.toml` declares FastAPI, Uvicorn, SQLAlchemy, psycopg, Alembic, boto3, httpx, PyJWT,
defusedxml, orjson, PyYAML and the OpenTelemetry stack as runtime dependencies. No module imports
any of them; `pydantic` is the only runtime dependency actually used.

This front-loads supply-chain surface for capabilities that do not exist yet, and it makes the
dependency manifest a poor signal of what the system does. The dependency set was **not** changed
this session, as instructed. Recommend trimming to what is used and reintroducing each dependency in
the task that needs it — per `SYSTEM_RULES.md` section 8, every dependency needs a documented
engineering reason at the point of introduction.

### L-4 — The interpreter selected is Python 3.14, not the 3.12 target

`uv` provisioned CPython 3.14.4 for the virtual environment. `requires-python = ">=3.12"` permits
this, but `[tool.pyright].pythonVersion` is `"3.12"` and `[tool.ruff].target-version` is `"py312"`,
so static analysis and runtime would disagree about the language level. Worth pinning deliberately —
via `.python-version` or a narrower `requires-python` — rather than leaving it to whatever uv
selects.

### L-5 — Product limitations

The inherent limits of the product itself — integrity is not safety, identity is not integrity,
evidence quality is inherited, enforcement is delegated — are documented in `PRD.md` section 13 and
are not repeated here.

---

## Deferred Work

Deliberately not built. See `PRD.md` section 12 and `ARCHITECTURE.md` section 13.

- Contract YAML loading and fixtures — Task 1.2, next.
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

**Run date: 2026-07-20. Environment: Windows, CPython 3.14.4, pytest 9.1.1. Status: all passing.**

Output below is copied from actual command runs.

| Command | Result |
| --- | --- |
| `uv sync --frozen --all-groups` | **Passed.** 105 packages installed. |
| `uv run pytest` | **Passed.** 9 passed in 2.16s. |
| `uv run pytest --cov=qualitymesh --cov-report=term-missing` | **Passed.** 9 passed; total coverage 80.30%. |
| `uv run ruff check .` | **Passed.** `All checks passed!` |
| `uv run ruff format --check .` | **Passed.** `4 files already formatted` |
| `uv run pyright` | **Passed.** `0 errors, 0 warnings, 0 informations` |

`uv run pytest`:

```text
platform win32 -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
configfile: pyproject.toml
testpaths: tests
asyncio: mode=Mode.STRICT
collected 9 items

tests\unit\test_contract_schema.py .........                             [100%]

============================== 9 passed in 2.16s ==============================
```

`uv run pytest --cov=qualitymesh --cov-report=term-missing`:

```text
Name                                  Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------
src\qualitymesh\__init__.py               0      0      0      0   100%
src\qualitymesh\schemas\__init__.py       2      0      0      0   100%
src\qualitymesh\schemas\contract.py     157     22     44     16    80%   40-42, 93, 95,
                                                                          98-99, 106, 108,
                                                                          111-112, 146, 148,
                                                                          150, 152, 177, 181,
                                                                          185, 188, 209, 242, 261
---------------------------------------------------------------------------------
TOTAL                                   159     22     44     16    80%
Required test coverage of 80.0% reached. Total coverage: 80.30%
```

**Coverage is 0.30 points above the `fail_under = 80` threshold**, which is not comfortable margin.
Per `SYSTEM_RULES.md` section 7, coverage alone does not prove correctness — and the specific gap
here matters. The uncovered lines are almost entirely **validator rejection branches**: the
non-array guard in `_yaml_array_to_tuple` (40-42), the non-string and unknown-value paths in
`_parse_evidence_kind` and `_parse_evidence_subject` (93-112), the empty and duplicate checks for
`allowedRefs`/`allowedEvents` (146-152), the trusted-workflow and digest-algorithm invariants
(177-188), the inverse artifact-binding rule (209), the `criticalVulnerabilities` threshold check
(242), and the empty `requiredEvidence` check (261).

These are fail-closed rejection paths — exactly the paths `SYSTEM_RULES.md` section 7 requires a
test for. The existing nine tests cover one direction of several invariants but not the inverse.
Closing these gaps is in scope for **Task 1.2**, which adds malformed-fixture coverage. No
implementation change was made this session.

---

## Last Updated

**2026-07-20** — Session 0, documentation and project structure.

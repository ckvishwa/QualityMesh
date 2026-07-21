# Task 1.3 — Evidence Envelope Schema and Hardened JUnit XML Parsing

## Status

Completed — 2026-07-20.

## Goal

Add a strict evidence-envelope model and a `defusedxml`-based JUnit XML parser, implementing
`PRD.md` FR-4. Malformed or hostile evidence must remain visible as an explicit unusable result,
never a silent drop and never a successful empty suite.

## Problem

QualityMesh can validate a release contract (Task 1.2) but has no representation for the evidence
that contract requires. Two risks must both be closed: parsing untrusted XML naively admits XXE,
DTD-based attacks and entity-expansion denial of service; and even a hardened parser can still fail
the fail-closed requirement if a parse failure disappears instead of remaining visible to whatever
eventually evaluates completeness.

## In Scope

- `src/qualitymesh/schemas/evidence.py` — `EvidenceEnvelope`, `StrictEvidenceModel`.
- `src/qualitymesh/parsers/junit.py` and `parsers/__init__.py` — hardened JUnit parsing.
- Fixtures under `tests/fixtures/junit/` and their tests.
- Pure, offline, deterministic. No I/O, no clock, no network, no environment access.

## Out of Scope

FastAPI, PostgreSQL, SQLAlchemy, Alembic, object storage, worker/outbox, OIDC, GitHub API, digest
*comparison* against a submitted artifact (FR-5), completeness evaluation, policy evaluation,
deployment authorization, Playwright parsing, SBOM parsing. None were touched.

## Acceptance Criteria

| # | Criterion | Status |
| --- | --- | --- |
| AC-1 | Envelope is strict, immutable, computes its own digest | Met |
| AC-2 | Caller cannot supply a digest — it is a computed field, not an input | Met |
| AC-3 | DTD declarations rejected outright (`forbid_dtd=True`), not just entities | Met |
| AC-4 | External entities and references rejected | Met |
| AC-5 | Entity expansion rejected before any expansion occurs | Met |
| AC-6 | Size, depth, element-count and testcase-count bounds enforced | Met |
| AC-7 | Malformed/hostile/empty input never raises out of the public parse function | Met |
| AC-8 | Malformed/hostile/empty input never becomes `ParsedJUnitEvidence` | Met |
| AC-9 | Passed/failed/errored/skipped testcases distinguished; suite counts verified against actual testcases | Met |
| AC-10 | Every rejection path returns a diagnostic naming the source, never document content | Met |
| AC-11 | Parsing is deterministic (no clock, no ordering dependence) | Met |
| AC-12 | Existing Task 1.1/1.2 tests unchanged and green | Met |

## Security Boundary

**What this proves.** The envelope's digest is computed by QualityMesh from raw bytes, never
accepted from a caller — establishing content identity, not authenticity. The parser proves that
accepted XML contains no DTD, no entities, no external references, and stays within declared
resource bounds; and that declared suite-level counts (`tests`/`failures`/`errors`/`skipped`) match
the testcases actually present, rather than being trusted at face value.

**What this does not prove.** Schema validation proves structural and semantic validity only. It
does not prove the evidence is authentic, that it was produced by the workflow it claims, that it is
bound to the correct commit or artifact (FR-5, not yet implemented), or that the underlying test
suite is meaningful. A syntactically valid JUnit report asserting 500 passing tests proves nothing
about whether those tests exercised anything real. This parser never makes or influences a release
decision — it produces facts for a later evaluator to consume.

**Verified empirically, not assumed:**

- `DefusedXmlException`, `DTDForbidden`, `EntitiesForbidden` and `ExternalReferenceForbidden` all
  subclass `ValueError` via `DefusedXmlException` — confirmed by inspection before writing the
  catch clause, so the exception hierarchy actually caught matches what's raised.
- A constructed "billion laughs"-style payload (375-byte internal-subset entity pyramid) is rejected
  in **0.1ms** — before any expansion, because `forbid_dtd=True` rejects the `DOCTYPE` token itself,
  the earliest possible point, regardless of what it declares.
- `defusedxml`'s `fromstring` defaults to `forbid_dtd=False` — the DTD rejection in
  `ARCHITECTURE.md`'s stated security control ("DTD and external entities disabled") is not the
  library default and had to be requested explicitly.

## Relevant Files

| File | Role |
| --- | --- |
| `src/qualitymesh/schemas/evidence.py` | New. `EvidenceEnvelope`, `StrictEvidenceModel`. |
| `src/qualitymesh/schemas/__init__.py` | Modified. Re-exports `EvidenceEnvelope`. |
| `src/qualitymesh/parsers/junit.py` | New. The parser, its models, its exception types. |
| `src/qualitymesh/parsers/__init__.py` | New. Public re-exports. |
| `tests/unit/test_evidence_schema.py` | New. 13 tests. |
| `tests/unit/test_junit_parser.py` | New. 27 tests. |
| `tests/fixtures/junit/*.xml` | New. 15 fixtures. |

## Relevant Decisions

**Producer/workflow-run claims are not on the envelope.** `SYSTEM_RULES.md` §9 and
`ARCHITECTURE.md`'s lifecycle both reference repository/workflow/run/commit claims on submitted
evidence, but neither defines a field set, required/optional status, or format — no commit-SHA or
run-ID pattern exists anywhere in the repository. Adding unconstrained `str | None` fields would
encode no real invariant. Deferred to the evidence-ingestion task (FR-3), which owns that boundary.

**Parse outcome is a returned union, not a raised exception — a deliberate contrast with Task 1.2's
contract loader.** `load_release_contract_bytes` raises `ContractLoadError` for malformed input,
which is correct there: a contract is a single document and there's nothing useful to return short
of a valid `ReleaseContract`. JUnit evidence is different — many evidence items are gathered and
evaluated together, and one malformed item must not abort the batch or vanish from what an evaluator
sees. `ParsedJUnitEvidence | UnusableJUnitEvidence`, both linked to `raw_digest`, makes "present but
unusable" a first-class, inspectable fact. `JUnitEnvelopeKindError` is the one exception that still
raises — it is a caller precondition violation (wrong envelope kind), not a property of evidence
content, and precondition violations are legitimately the caller's bug, not evidence to report on.

**A suite that declares `tests="0"` with no testcases is `ParsedJUnitEvidence`, not
`UnusableJUnitEvidence`.** This is distinct from *zero suites*, which is rejected (see below). A
present suite reporting zero tests is real, legitimately-shaped content — an empty test file or a
fully-filtered module produces exactly this. Whether zero executed tests is *suspicious* is a policy
question the contract schema already has a named mechanism for
(`PolicyRules.suspicious_test_count_reduction_percent`), which requires a baseline this parser has
no access to and no business computing. Rejecting it here would invent a threshold nowhere specified
and remove information the eventual evaluator needs to see.

**Zero suites is rejected via one guard, not two.** The first implementation had both an early
explicit check in `_parse_suites` and `ParsedJUnitEvidence`'s own "at least one suite" model
invariant enforcing the same condition — found during the YAGNI review required by this task. The
duplicate meant two lines were structurally unreachable (confirmed by coverage). Removed the early
check; the model's own invariant is now the single enforcement point, and the `except ValidationError`
branch in `parse_junit_evidence` — previously dead code — is now genuinely exercised.

**`StrictEvidenceModel` is shared between `schemas/evidence.py` and `parsers/junit.py`, not
duplicated.** Also found during the YAGNI review: `junit.py` initially defined its own
byte-identical local base class. Unlike the evidence/contract split (different trust boundaries,
justified in `evidence.py`'s docstring), both files here are new in this same task and describe the
same evidence boundary — duplicating the config there had no justification. Fixed by importing.

**No new ADR.** `ARCHITECTURE.md` §7 already documents "failure marks the evidence unusable — it
does not silently drop the item" at the lifecycle level. Nothing in this task introduces a trust
boundary, technology choice, or schema semantic not already covered by that section or by
ADR-0001's contract-as-source-of-truth reasoning. The decisions above are implementation-level and
are recorded here instead.

## Validation Commands

```bash
uv sync --frozen --all-groups
uv run pytest
uv run pytest --cov=qualitymesh --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Python 3.12 verification, in an environment outside the repository:

```bash
UV_PROJECT_ENVIRONMENT=<tmp>/venv312 uv run --python 3.12 --frozen --all-groups pytest
UV_PROJECT_ENVIRONMENT=<tmp>/venv312 uv run --python 3.12 --frozen --all-groups pyright
UV_PROJECT_ENVIRONMENT=<tmp>/venv312 uv run --python 3.12 --frozen --all-groups ruff check .
```

## Completed Work

- `EvidenceEnvelope`: `kind` (`EvidenceKind`), `subject` (`EvidenceSubject`), `raw_bytes`, plus
  computed `digest_algorithm` (`"sha256"`) and `digest` — never caller-supplied.
- `parse_junit_evidence(envelope, *, source_name)`: byte-size cap, `defusedxml.fromstring` with
  `forbid_dtd=True, forbid_entities=True, forbid_external=True`, post-parse depth/element/testcase
  bounds (safe post-parse because entity expansion — the only amplification vector — is already
  blocked pre-construction), root-shape validation (`testsuite`/`testsuites` only), per-suite
  declared-count verification against actual testcases.
- `JUnitTestCase`/`JUnitTestSuite`/`ParsedJUnitEvidence`/`UnusableJUnitEvidence`, all strict and
  frozen, linked to the envelope via `raw_digest`.
- 40 new tests, 15 new fixtures.

## Problems Encountered

**Speculative `# type: ignore` comments written before verification.** Drafted
`# type: ignore[prop-decorator]` on every `computed_field` property by habit, before running
pyright. *Resolution:* removed all four and re-ran pyright — none were needed; `computed_field`
composes cleanly with strict mode and frozen models on this pydantic version. Zero suppressions
remain in this task's code.

**A `cast(Element, root)` I added was itself wrong.** Assumed defusedxml's stub left
`fromstring`'s return type as `Unknown` (true for the *function signature*, verified by probe), and
wrote a justifying comment on that basis. Pyright's `reportUnnecessaryCast` caught that the *narrowed
result type* was already `Element` by inference — my comment was factually wrong about what was
actually being inferred. *Resolution:* removed the cast and the incorrect comment; verifying a claim
about one thing (a signature) doesn't establish a claim about a related thing (post-call inference).

**Fixture bug in my own resource-bound test.** `test_excessive_nesting_is_rejected_as_unusable`
built an already-balanced nested-tag string, then appended extra closing tags on top — producing
mismatched XML that failed for the wrong reason (`ParseError`, not the depth guard).
*Resolution:* traced the string construction by hand and removed the redundant closing tags.

**Two structurally unreachable lines from duplicate guards** (zero-suites checked twice) and
**four coverage gaps in `_build_suite`/`_build_testcase`** (missing suite count attribute distinct
from missing `name`; missing testcase `name`; suite-level count mismatch on a field other than
`tests`) — found via coverage after the first green run, all closed with either a design fix
(duplicate guard) or a new fixture (the three attribute-level gaps). See *Relevant Decisions* for
the duplicate-guard fix and *Validation Results* for coverage progression.

## Resolutions

Covered above per-problem. No validation was weakened to reach green; every fix was either a root
cause correction or new test coverage for a genuine gap.

## Validation Results

CPython 3.14.4 and CPython 3.12.13, both clean.

```text
uv run pytest                                     100 passed in 1.3s
uv run pytest --cov=qualitymesh --cov-report=...  100 passed; total coverage 99.25%
uv run ruff check .                               All checks passed!
uv run ruff format --check .                      12 files already formatted
uv run pyright                                     0 errors, 0 warnings, 0 informations
```

Coverage detail:

```text
Name                                    Stmts   Miss Branch BrPart  Cover
---------------------------------------------------------------------------
src\qualitymesh\parsers\__init__.py         2      0      0      0   100%
src\qualitymesh\parsers\junit.py          156      0     42      0   100%
src\qualitymesh\schemas\evidence.py        19      0      0      0   100%
src\qualitymesh\schemas\contract.py       157      2     44      2    98%   95, 108
---------------------------------------------------------------------------
TOTAL                                     422      2    114      2    99%
Required test coverage of 80.0% reached. Total coverage: 99.25%
```

Coverage rose from Task 1.2's 98.73% to 99.25%. `fail_under` unchanged at 80. The two remaining
uncovered lines (`contract.py` 95, 108) predate this task — see `TASK-1.2`'s record.

Python 3.12 run: `100 passed`, `0 errors, 0 warnings, 0 informations`, `All checks passed!` —
identical to 3.14.4.

## Known Limitations

- **No producer/workflow/run/commit claims on the envelope** — deferred to FR-3, see *Relevant
  Decisions*.
- **No failure/error message text captured on a testcase** — FR-4 does not call for it and nothing
  in the repository consumes it yet; would be trivial to add when a consumer exists.
- **A zero-test suite parses as success** — deliberate, see *Relevant Decisions*; flagged here so
  it isn't mistaken for an oversight when `PolicyRules.suspicious_test_count_reduction_percent`
  evaluation is eventually built.
- **Only JUnit is implemented.** `EvidenceKind` has eight other members (Playwright, SBOM, SCA scan,
  container scan, deployment smoke, critical journey, artifact attestation, custom); none have
  parsers. `parse_junit_evidence` rejects any envelope not declaring `kind=junit` outright rather
  than attempting to handle them.

## Next Task

> Task 1.4 — Evidence-to-contract binding: verify a `ParsedJUnitEvidence`'s subject and digest
> against the commit/artifact under evaluation and against a `RequiredEvidenceItem`'s declared
> `producerWorkflow`, implementing `PRD.md` FR-5. Still pure and offline — no API, database, or
> network access needed, since binding verification is a comparison of already-available digests
> and identifiers, not a new I/O boundary.

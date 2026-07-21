# Task 1.4 — Evidence-to-Contract Binding Verification

## Status

Completed — 2026-07-21.

## Goal

Implement deterministic, pure, offline verification that submitted evidence is bound to the
release contract, the exact commit or artifact under evaluation, the matching
`RequiredEvidenceItem`, and the workflow authorized to produce it. Implements `PRD.md` FR-5.

## Problem

`PRD.md` FR-5.1/5.2 require verifying that evidence "carries a digest matching the artifact/commit
under evaluation." No document in the repository ever established a place for evidence to carry
that claim: `EvidenceEnvelope` (Task 1.3) intentionally excludes producer/workflow/run/commit
claims, deferring them to evidence ingestion (FR-3). Attempting Task 1.4 against the data model as
it stood before this task would have forced one of three wrong outcomes: silently treating an
uncheckable comparison as passing (fail-open), making the required "correctly bound" test scenarios
permanently impossible to write (contradicting the task brief), or conflating
`EvidenceEnvelope.digest` — the hash of the evidence *payload* — with an artifact's digest, a real
correctness defect.

This was reported as a blocking conflict before implementation began (see *Relevant Decisions*
below for the resolution actually approved).

## In Scope

- `src/qualitymesh/binding/verifier.py`, `binding/__init__.py`.
- Binding-claim models (`CommitBindingClaim`, `ArtifactBindingClaim`), evaluation-target models
  (`CommitEvaluationTarget`, `ArtifactEvaluationTarget`), result model
  (`BindingVerificationResult`), and `verify_evidence_binding`.
- Pure, offline, deterministic comparison of already-typed data only.

## Out of Scope

Evidence-payload parsing (JUnit or otherwise), network access, GitHub API access, database access,
an HTTP API, a worker, OIDC token verification, cryptographic artifact-attestation verification,
completeness evaluation, policy evaluation. None were touched. `EvidenceEnvelope`
(`schemas/evidence.py`) was not modified — Task 1.3's behavior is unchanged.

## Acceptance Criteria

| # | Criterion | Status |
| --- | --- | --- |
| AC-1 | `BOUND` reachable only when every applicable check for the evidence subject passes | Met |
| AC-2 | Commit and artifact claims are typed separately; each structurally rejects the other's fields | Met |
| AC-3 | `EvidenceEnvelope.digest` never participates in any binding comparison | Met |
| AC-4 | Unknown requirement, kind/subject mismatch, workflow mismatch, forbidden algorithm, target identity/digest mismatch each produce a distinct `NOT_BOUND` reason | Met |
| AC-5 | Missing claim or missing target produces `INSUFFICIENT_INFORMATION`, never silently `BOUND` | Met |
| AC-6 | A foundational failure (e.g. unknown requirement) is reported even when other problems, or a missing target, are also present | Met |
| AC-7 | No case normalization, no fuzzy matching, no substring matching anywhere in the comparison | Met |
| AC-8 | Contract, envelope, claim, target and result are all immutable; no mutation occurs during verification | Met |
| AC-9 | Existing Task 1.1–1.3 tests unchanged and green | Met |

## Security Boundary

**What this proves.** That a submitted binding claim, if trusted, is internally consistent with the
contract (references a real requirement, agrees on kind/subject/workflow) and matches the canonical
evaluation target's commit or artifact identity exactly.

**What this does not prove.** A matching `producer_workflow` claim means only "the submitted
identifier matches the workflow the contract requires." It does not prove the named workflow
actually produced the evidence — that requires identity verification (OIDC, FR-3, not implemented)
and, for artifact evidence, provenance verification (artifact attestation, not implemented). This
verifier treats every binding claim as untrusted input; it establishes consistency, not authenticity
or provenance.

**A digest algorithm being "not forbidden" is not the same check as "matches the target."** These
are deliberately two separate steps (`DIGEST_ALGORITHM_NOT_ALLOWED` vs
`ARTIFACT_DIGEST_ALGORITHM_MISMATCH`) — a claimed algorithm can be individually permitted by the
trust boundary's allow-list while still not being the algorithm the evaluation target actually used.

## Relevant Files

| File | Role |
| --- | --- |
| `src/qualitymesh/binding/verifier.py` | New. Claim/target/result models, `verify_evidence_binding`. |
| `src/qualitymesh/binding/__init__.py` | New. Public re-exports. |
| `tests/unit/test_binding_verifier.py` | New. 24 tests, cross-object comparison. |
| `tests/unit/test_binding_claims_and_targets.py` | New. 11 tests, schema-level rejections. |

## Relevant Decisions

**Approved resolution to the blocking conflict.** A minimal, pure, typed binding-metadata layer was
added as part of this task — a narrow prerequisite slice of FR-3's submission metadata, not an
ingestion service. `EvidenceEnvelope` remains unchanged and continues to own only kind, subject, raw
bytes and the payload digest. Binding claims (untrusted, submitted alongside evidence) and the
evaluation target (canonical, supplied by the evaluation caller, never derived from evidence) are
separate model families, explicitly not a single generic identifier field — commit identity,
artifact identity and artifact digest are distinct facts that a generic field would conflate.

**Commit SHA, artifact digest and digest algorithm are unformatted, bounded strings — not `Slug`.**
No format for any of them is established anywhere in the repository. `Slug` was considered and
rejected: its pattern requires a leading letter, which would reject real commit SHAs and hex digests
that start with a digit. Bounding length without inventing a regex mirrors the existing restraint
already applied to `TrustedWorkflow.allowed_refs` entries in `contract.py`.

**Claim and target `digest_algorithm` are not `Literal["sha256"]`, unlike `TrustBoundary`'s.**
Considered and rejected: locking both to the single value `TrustBoundary.allowed_digest_algorithms`
can currently ever contain would make `ARTIFACT_DIGEST_ALGORITHM_MISMATCH` structurally
unreachable — claim and target could only ever both be `"sha256"`, tautologically equal. Keeping
both as bounded strings makes the required "wrong artifact digest algorithm" scenario a real,
reachable case (e.g. claim says `sha256`, permitted by the trust boundary, but the target's actual
attestation used `sha512`) distinct from `DIGEST_ALGORITHM_NOT_ALLOWED` (claimed algorithm not
permitted at all, e.g. `sha1`).

**Single most-foundational reason, not an accumulated list.** When multiple problems exist
simultaneously, the result carries exactly one `BindingReason` — whichever is most foundational per
the fixed precedence (unknown requirement → kind/subject → claim/target structural consistency →
workflow → algorithm policy → target identity — `INSUFFICIENT_INFORMATION` only once nothing more
specific applies). This mirrors `Decision`'s existing most-severe-wins precedence in `contract.py`
rather than introducing a new accumulation mechanic.

**Missing evaluation target does not block foundational checks that don't need it.** Unknown
requirement, kind/subject mismatch, claim-subject mismatch, workflow mismatch and the
digest-algorithm-allowed check all run regardless of whether a target was supplied — only the
claim/target subject-consistency check and the final identity comparison actually require one. A
missing target therefore only produces `INSUFFICIENT_INFORMATION` after every check not needing it
has already had the chance to find something more specific wrong.

**No new ADR.** `ARCHITECTURE.md`'s trust-boundary table (§5, row "B2 — subject binding":
establishes "this evidence concerns this commit and this artifact," does not establish "that the
evidence is a meaningful test of it") and its OIDC component description (§6: "does not prove...
the evidence carried alongside the token is accurate") already jointly establish the exact principle
this task implements — that matching binding metadata proves consistency, not authenticity. This
task is the concrete realization of the already-documented B2 boundary, not a new trust-boundary
decision.

**`DEPLOYED_SERVICE`-subject evidence is out of scope, safely.** No claim or target type exists for
it (FR-5.1/5.2 only cover artifact and source-revision subjects). If a caller has no valid claim to
construct for such evidence, `binding_claims=None` correctly yields `INSUFFICIENT_INFORMATION`. If a
caller mistakenly supplies a `CommitBindingClaim`/`ArtifactBindingClaim` anyway, its `subject` can
never equal `DEPLOYED_SERVICE`, so `CLAIM_SUBJECT_MISMATCH` fires — verified never to reach `BOUND`
either way.

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

## Problems Encountered

**A copy-paste variable-reference bug, caught by pyright.** While refactoring the artifact-branch
comparisons to use a narrowed `artifact_target` local (after `cast`), one `!r`-formatted diagnostic
string was left referencing the original union-typed `evaluation_target` parameter instead —
`reportUnknownMemberType`/attribute-access errors surfaced immediately on the next pyright run,
because accessing `.artifact_id` on the still-union-typed name is exactly what strict mode is meant
to catch. *Resolution:* corrected the one stray reference; re-ran pyright clean. No test coverage
gap resulted, since the line was inside an already-tested branch — the bug was purely in
unreachable-under-test formatting-string plumbing that pyright caught before it could matter.

**Test builder helpers failed pyright's `dict.update` overload resolution.** `fields = {...}`
followed by `fields.update(overrides)` (where `overrides: **object`) had `fields` inferred as
`dict[str, str]` from its literal contents, which `dict.update` can't accept an `Iterable[object]`
into. *Resolution:* explicit `fields: dict[str, object]` annotation on each builder.

**Speculative `pyright: ignore` comments were written throughout the test files by habit** (every
intentional invalid-construction call needs one, since pyright statically flags what pydantic
rejects at runtime). Unlike Task 1.3's `computed_field` ignores, these were verified necessary
*before* being treated as final: stripped all of them, reran pyright, and all 25 reappeared as real
errors — confirmed load-bearing, restored as-is.

## Resolutions

Covered per-problem above. No validation was weakened; every fix was a root-cause correction
(fixing the actual stray reference, adding the actual missing type annotation) or evidence that an
existing suppression was necessary, not incidental.

## Tests Added

35 new tests across two files:

- **`test_binding_verifier.py` (24 tests):** both correctly-bound subject cases; every `NOT_BOUND`
  reason (commit mismatch, case-only commit mismatch, artifact ID/digest/algorithm mismatch,
  envelope-digest-is-never-the-artifact-digest, unknown requirement, kind mismatch, subject
  mismatch, claim-subject mismatch, claim/target subject contradiction, workflow mismatch, forbidden
  algorithm, forbidden-algorithm-wrong-case); both `INSUFFICIENT_INFORMATION` triggers (no claim, no
  target); a test proving a missing target doesn't mask a foundational `NOT_BOUND`; four deterministic-precedence
  tests proving the most-foundational reason wins when multiple problems coexist; no-mutation proof;
  result-immutability proof.
- **`test_binding_claims_and_targets.py` (11 tests):** schema-level rejections — commit claim
  carrying artifact fields, missing `commit_sha`, artifact claim carrying commit fields, each of the
  three required artifact fields individually missing, commit target carrying artifact fields,
  immutability, unknown-field rejection.

## Full Validation Results

CPython 3.14.4 and CPython 3.12.13, both clean.

```text
uv run pytest                                     135 passed in 1.19s (3.14) / 2.17s (3.12)
uv run ruff check .                               All checks passed!
uv run ruff format --check .                      16 files already formatted
uv run pyright                                     0 errors, 0 warnings, 0 informations
```

## Coverage by New Module

```text
Name                                    Stmts   Miss Branch BrPart  Cover
---------------------------------------------------------------------------
src\qualitymesh\binding\__init__.py         2      0      0      0   100%
src\qualitymesh\binding\verifier.py        94      0     30      0   100%
---------------------------------------------------------------------------
```

Total repository coverage: **99.40%** (was 99.25% after Task 1.3). `fail_under` unchanged at 80.
The two pre-existing uncovered lines (`contract.py` 95, 108) predate this task.

## Review Findings

Self-reviewed against the checklist in the task brief:

| Check | Finding |
| --- | --- |
| Comparisons against caller-controlled derived values | None found — all comparisons are claim-vs-requirement (contract-sourced) or claim-vs-target (independently supplied) |
| Ambiguous artifact identity | None — artifact ID, digest and algorithm are three distinct fields, never merged |
| Digest comparison mistakes | One found and fixed — see *Problems Encountered* (stray `evaluation_target` reference) |
| Accidental case normalization | None — grepped for `.lower()`/`.upper()`/`.casefold()`/`normalize`: zero matches. Two dedicated tests prove case-only differences still fail |
| Permissive workflow matching | None — exact `!=` comparison on already lowercase-pattern-constrained `Slug` fields |
| Failure states as success | None — grepped every `_bound()` call site; both are correctly gated behind every applicable check |
| Duplicate models or enums | None — reused `Slug`, `EvidenceSubject`, `StrictEvidenceModel`; no new base class |
| Unreachable exception branches | None — no `try`/`except` in this module; 100% branch coverage confirms every `if` is reachable both ways |
| Speculative architecture | None — `DEPLOYED_SERVICE` deliberately unmodeled rather than stubbed; see *Relevant Decisions* |
| Inaccurate security claims | None — docstrings state exactly what a matching claim proves and doesn't; cross-checked against `PRD.md` §5's prohibited-claims list |

## Documentation Updated

`docs/PROJECT_STATUS.md`, this task record, `docs/PRD.md` FR-5, `docs/ARCHITECTURE.md`
implementation-reality section, `README.md`, `CLAUDE.md`.

## Known Limitations

- **`DEPLOYED_SERVICE`-subject evidence has no binding-verification path.** Not a gap in this task —
  FR-5 never scoped it — but a real limitation of what FR-5 currently covers.
- **Binding claims are not authenticated.** As stated throughout: a matching claim proves internal
  consistency, not that the named workflow produced the evidence. OIDC verification (FR-3) and
  artifact-attestation cryptographic verification remain unimplemented.
- **No caller exists yet that constructs real `EvaluationTarget`/`BindingClaim` values from actual
  release state.** This task provides the pure comparison function; wiring it to real evidence
  ingestion and a real evaluation-target source is FR-3/completeness-evaluation work, still ahead.

## Next Task

As recorded in `docs/PROJECT_STATUS.md`: completeness evaluation (`PRD.md` FR-6) — for each
required evidence item, determine whether sufficient, fresh, correctly bound evidence exists, using
`minimumItems`/`maximumAgeSeconds` and this task's `BindingVerificationResult`.

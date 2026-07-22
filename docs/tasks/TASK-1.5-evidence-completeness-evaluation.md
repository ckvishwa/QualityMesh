# Task 1.5 — Evidence Completeness Evaluation

## Status

Completed — 2026-07-21.

## Goal

Implement deterministic, pure, offline completeness evaluation: for every `RequiredEvidenceItem`
in a release contract, determine whether enough fresh, correctly bound evidence exists. Any
unsatisfied required item maps to `INSUFFICIENT_EVIDENCE`. Implements `PRD.md` FR-6.

## Problem

FR-6.2 requires evaluating `maximumAgeSeconds` — but nothing in the repository, before this task,
recorded when a piece of evidence was produced or observed. `EvidenceEnvelope` (Task 1.3) carries
kind, subject, raw bytes and a payload digest; `ParsedJUnitEvidence`/`UnusableJUnitEvidence` carry
status and content; `BindingVerificationResult` (Task 1.4) carries status, reason and detail. None
of them carry a timestamp. Freshness could not be evaluated without either inventing a substitute
(file modification time, ingestion order, verification time — all explicitly prohibited) or adding
the minimal typed carrier for an explicitly-supplied timestamp.

This was verified, not assumed: grepped `datetime`/`timezone`/`tzinfo` across `src/qualitymesh/`
before writing any code — zero matches anywhere in the repository.

## In Scope

- `src/qualitymesh/completeness/evaluator.py`, `completeness/__init__.py`.
- `EvidenceCandidate` — the minimal prerequisite carrier (envelope + requirement reference +
  binding result + explicit timestamp), authorized as "a narrow prerequisite slice of FR-3's
  submission metadata, not an ingestion service."
- Pure, offline, deterministic evaluation against an explicit `as_of`.

## Out of Scope

Evidence payload parsing, re-verification of binding (Task 1.4's comparisons are consumed, never
reproduced), OIDC, attestation verification, network/GitHub/database access, an API or worker,
policy evaluation (FR-7), `Decision.PASS`/`WARN`/`BLOCK`. None were touched.
`EvidenceEnvelope`/`BindingVerificationResult` unchanged.

## Freshness Timestamp Source and Trust Semantics

**Source:** `EvidenceCandidate.observed_at`, an explicitly supplied `datetime`, required at
construction (no default — omitting it is a schema-level `ValidationError`, not a silently-assumed
value). Nothing in this module computes it; no clock is read anywhere in `evaluator.py` (verified
by grep — zero matches for `datetime.now`/`utcnow`/`time.time`).

**Trust semantics:** exactly the same as a binding claim (Task 1.4) — `observed_at` is supplied by
whatever constructs the candidate, not independently verified by this module. This module trusts it
structurally (rejects naive values, rejects future-dated values against `as_of`) but does not
authenticate it.

**Why this isn't scope creep:** `ARCHITECTURE.md` §9 already states "the evaluation core is a pure
function of `(contract, evidence set, artifact digest, evaluation timestamp)`."
`EvidenceCandidate` is the concrete, typed representation of one member of that "evidence set,"
carrying its own timestamp — the realization of already-documented architecture, not a new
decision. No new ADR was added on this basis (see *Relevant Decisions*).

## Duplicate-Counting Semantics

**Not actually silent**, despite first appearing so. `SYSTEM_RULES.md` §9 states "Evidence is
identified by the digest of its raw bytes, which makes reprocessing idempotent," and
`ARCHITECTURE.md` §10 restates "Processing is idempotent — evidence is identified by its raw
digest." Two candidates with identical `envelope.digest` under the same requirement are the same
evidence resubmitted, not two independently produced items — they count once.
Candidates with different digests are always independent, regardless of any other similarity
(same requirement, same or adjacent timestamps). Deduplication is scoped per-requirement, not
global: the same raw bytes could legitimately be offered against two different requirements.

## Files Created or Changed

| File | Role |
| --- | --- |
| `src/qualitymesh/completeness/evaluator.py` | New. Models and `evaluate_completeness`. |
| `src/qualitymesh/completeness/__init__.py` | New. Public re-exports. |
| `tests/unit/test_completeness_evaluator.py` | New. 32 tests. |

## Public Completeness API and Models

```python
def evaluate_completeness(
    contract: ReleaseContract,
    candidates: tuple[EvidenceCandidate, ...],
    as_of: datetime,
) -> CompletenessResult: ...
```

- `EvidenceCandidate` — `requirement_id: Slug`, `envelope: EvidenceEnvelope`,
  `binding_result: BindingVerificationResult`, `observed_at: datetime` (tz-aware enforced).
- `CandidateRejectionReason` (`StrEnum`) — `UNKNOWN_REQUIREMENT`, `EVIDENCE_KIND_MISMATCH`,
  `EVIDENCE_SUBJECT_MISMATCH`, `NOT_BOUND`, `INSUFFICIENT_BINDING_INFORMATION`, `FUTURE_DATED`,
  `STALE`, `DUPLICATE_OF_ANOTHER_CANDIDATE`.
- `CandidateEvaluation` — `envelope_digest`, `requirement_id`, `qualifies`, `reason`.
- `RequirementCompletenessResult` — `requirement_id`, `minimum_items`, `qualifying_count`,
  `total_candidate_count`, `satisfied`, `candidate_evaluations` (ordered as supplied).
- `CompletenessOutcome` (`StrEnum`) — `SATISFIED`, `INSUFFICIENT_EVIDENCE`. **Not** `Decision`:
  `SATISFIED` is not `Decision.PASS` (see *Relevant Decisions*).
- `CompletenessResult` — `outcome`, `as_of`, `requirement_results` (contract order),
  `unmatched_candidates` (candidates whose `requirement_id` resolves to nothing).

All models strict, frozen, `extra="forbid"`, built on the existing `StrictEvidenceModel`.

## Exact Completeness Rules

Per candidate, checked in order (first match wins — no candidate can be rejected for two reasons
simultaneously):

1. `requirement_id` unknown to the contract → excluded, recorded in `unmatched_candidates`, never
   counted toward any requirement.
2. `envelope.kind is not requirement.kind` → `EVIDENCE_KIND_MISMATCH`.
3. `envelope.subject is not requirement.subject` → `EVIDENCE_SUBJECT_MISMATCH`.
4. `binding_result.status is NOT_BOUND` → `NOT_BOUND`.
5. `binding_result.status is INSUFFICIENT_INFORMATION` → `INSUFFICIENT_BINDING_INFORMATION`.
6. `observed_at > as_of` → `FUTURE_DATED`. Checked unconditionally, regardless of whether
   `maximumAgeSeconds` is set — a future-dated timestamp is invalid on its face.
7. `maximumAgeSeconds` set and `as_of - observed_at > timedelta(seconds=maximumAgeSeconds)` →
   `STALE`. Boundary is inclusive: exactly at the limit still qualifies, matching this repo's own
   existing precedent (`contracts/loader.py`, `parsers/junit.py` — both reject strictly `>` their
   size limits, accepting exactly-at-limit).
8. `envelope.digest` already counted for this requirement → `DUPLICATE_OF_ANOTHER_CANDIDATE`.
9. Otherwise: qualifies, counted.

A requirement is `satisfied` iff `qualifying_count >= minimum_items`. Overall outcome is `SATISFIED`
iff every requirement is satisfied; otherwise `INSUFFICIENT_EVIDENCE`. Every `RequiredEvidenceItem`
produces exactly one `RequirementCompletenessResult`, satisfied or not — none are omitted.
`minimum_items == 0` cannot occur: `PositiveStrictInt` (reused from `contract.py`) enforces `ge=1`,
so this was not special-cased — a genuinely impossible input, not an overlooked one.

## Problems Encountered

**Test-builder bug, not an evaluator bug.** The first 25 of 30 initial tests failed with
`ValidationError` from `ReleaseContract.model_validate` itself — `TrustBoundary.require_artifact_attestation`
defaults `True` (Task 1.1 invariant), and the shared `build_contract()` helper sliced out the
artifact-attestation requirement without disabling that flag. *Resolution:* set
`requireArtifactAttestation: false` explicitly in the helper's trust boundary. Root cause was in the
test fixture, not the implementation under test — confirmed by all 25 sharing the identical
`ValidationError` before any evaluator code ran.

**A false-alarm pyright discrepancy, tracked down before trusting it.** While verifying (per
established discipline) that every `pyright: ignore` in the new test file was load-bearing,
stripping-and-rechecking Task 1.4's *already-committed* test files first returned `0 errors`
instead of the `25` recorded in that task's own record — an apparent contradiction worth resolving
rather than reporting as fact. Rerunning the identical strip immediately after reproduced the
original `25` twice in a row, confirming the `0` was a stale/flaky single pyright invocation, not a
real discrepancy or a inaccuracy in Task 1.4's record. *Resolution:* re-ran every strip-check twice
before drawing a conclusion for the rest of this task.

**Two `pyright: ignore` comments in this task's own test file were genuinely unnecessary**, added
by habit from Task 1.4's pattern without verification: the two frozen-model attribute-reassignment
tests (`candidate().observed_at = ...`, `result.outcome = ...`). Verified by stripping all four
ignores, rerunning twice: only the two `reportCallIssue` ignores (intentional missing/extra
constructor arguments) reappeared as errors; the two `reportAttributeAccessIssue` ones did not — a
simple attribute reassignment on a frozen pydantic model, without pyright's own plugin, is not
statically flagged by generic strict mode. *Resolution:* removed the two unnecessary ignores;
re-ran pyright, still clean.

## Resolutions

Covered per-problem above. No validation was weakened. Every fix was either a root-cause correction
in test fixture construction or removal of an unverified, unnecessary suppression.

## Tests Added

32 tests in `test_completeness_evaluator.py`, covering every scenario in the task brief: both
happy-path satisfaction cases, minimumItems counting at each boundary (0/fewer/exact/more — the
zero case being structurally impossible rather than untested), freshness at and past the boundary,
`maximumAge` absent, `NOT_BOUND`/`INSUFFICIENT_INFORMATION` exclusion, a bound/unbound/stale mixture,
unknown-requirement reporting, kind/subject mismatch, duplicate-digest deduplication with a
distinguishing different-digest counterpart, naive-timestamp rejection at both the candidate and
`as_of` level, missing-timestamp rejection, timezone-equivalent-instant acceptance,
determinism (same inputs → equal results), contract-order and candidate-order preservation, no
input mutation, result immutability, and an explicit test proving `NOT_BOUND` candidates are
excluded via the supplied `BindingVerificationResult` rather than any re-derived comparison.

## Full Validation Results

CPython 3.14.4 and CPython 3.12.13, both clean.

```text
uv run pytest                                     167 passed
uv run ruff check .                               All checks passed!
uv run ruff format --check .                      19 files already formatted
uv run pyright                                     0 errors, 0 warnings, 0 informations
```

## Coverage by New Module

```text
Name                                        Stmts   Miss Branch BrPart  Cover
---------------------------------------------------------------------------------------
src\qualitymesh\completeness\__init__.py        2      0      0      0   100%
src\qualitymesh\completeness\evaluator.py      89      0     30      0   100%
```

Total repository coverage: **99.49%** (was 99.40% after Task 1.4). `fail_under` unchanged at 80.

## Security and YAGNI Review Findings

| Check | Finding |
| --- | --- |
| Fail-open counting | None — `qualifies` is only `True` when every check returns `None`; default is exclusion |
| Counting `NOT_BOUND` evidence | None — explicit, first-priority-after-kind/subject exclusion |
| Missing timestamps treated as fresh | None — `observed_at` has no default; omitting it is a schema-level rejection |
| Hidden current-time calls | None — grepped `datetime.now`/`utcnow`/`time.time`: zero matches in `evaluator.py` |
| Timezone bugs | None — dedicated equivalent-offset test passes; naive rejected at two independent points |
| Boundary comparison errors | None — inclusive-max semantics verified by direct boundary and boundary+1 tests |
| Future timestamp acceptance | None — checked unconditionally, before and independent of the staleness check |
| Off-by-one errors | None — exact/fewer/more-than-minimum all directly tested |
| Duplicate evidence inflation | None — dedup by `envelope.digest`, scoped per-requirement, tested both directions |
| Requirement-ID collisions | Structurally impossible — Task 1.1's `ReleaseContract` validator already enforces unique requirement IDs |
| Reimplementation of binding logic | None — `evaluator.py` imports only `BindingStatus`, never `BindingReason`; only `.status` is read |
| Ordering dependent on set/dict iteration | None — `seen_digests: set[str]` used only for membership testing, never iterated; all output order comes from list comprehensions over caller-supplied tuples |
| Mutation of input models | None — all inputs frozen; explicit no-mutation test included |
| Inaccurate completeness claims | None — module and `CompletenessOutcome` docstrings state `SATISFIED` is necessary, not sufficient, for a release to pass |

Two unnecessary `pyright: ignore` comments were found and removed — see *Problems Encountered*.

## Documentation Updated

`docs/PROJECT_STATUS.md`, this task record, `docs/PRD.md` FR-6, `docs/ARCHITECTURE.md`
implementation-reality section, `README.md`, `CLAUDE.md`.

## Known Limitations

- **No real caller yet constructs `EvidenceCandidate` values from actual evidence.** This task
  provides the pure evaluation function; wiring it to real ingestion (FR-3) and a real
  `observed_at` source remains future work, same as Task 1.4's `BindingClaim`/`EvaluationTarget`.
- **`observed_at` is trusted structurally, not authenticated.** A submitter could claim any
  timezone-aware timestamp; nothing here verifies it against an independent source (e.g., a
  workflow-run's actual completion time, once ingestion exists).
- **No relationship enforced between a candidate's `binding_result` and its `envelope`/`requirement_id`.**
  This module trusts that the caller constructed a coherent `EvidenceCandidate` (the binding result
  actually corresponds to this envelope and requirement) — Task 1.4's `verify_evidence_binding`
  itself performs that coherence check; this module does not re-verify it, by design.

## Next Task

As recorded in `docs/PROJECT_STATUS.md`: FR-7, deterministic policy evaluation — combine
`CompletenessResult` with other rule outcomes (artifact mismatch, exposed secrets, critical
vulnerabilities, failed critical journey) under the existing `Decision` precedence
(`BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS`) to produce the first real release `Decision`.

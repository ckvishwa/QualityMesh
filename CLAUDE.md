# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --frozen --all-groups              # install; CI and local both use --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest --cov=qualitymesh --cov-report=term-missing
```

All five must pass before a task is done. Single test:

```bash
uv run pytest tests/unit/test_contract_schema.py::test_unknown_fields_are_rejected
uv run pytest -k "artifact"
```

Note `pythonpath = ["src"]` in `[tool.pytest.ini_options]` — tests import `qualitymesh` without an
editable install.

## What this project is

Deterministic release-assurance engine. Consumes evidence that CI already produced (JUnit, SBOM,
scans, attestations), verifies it is authentic, complete and bound to the artifact being deployed,
then emits exactly one decision under a versioned policy:

```text
BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS
```

It does not run tests, builds or scanners, and does not judge whether software is safe.

## Read the docs before non-trivial work

`docs/` is governance, not background reading. `docs/SYSTEM_RULES.md` defines the source-of-truth
hierarchy — task > tests > schemas > PRD > ADRs > rules > implementation > tool recommendations.
Plugins and agents are advisors; they do not define the project.

`docs/PROJECT_STATUS.md` is authoritative for what actually exists. When it conflicts with `PRD.md`
or `ARCHITECTURE.md` about implementation state, it wins. **Every completed task must update it**,
with validation output copied from real runs — never invented. Architecture-changing tasks need an
ADR (`docs/adr/README.md`).

## Current implementation reality

Five modules exist: `schemas/contract.py` (strict contract models), `contracts/loader.py` (safe
YAML loading), `schemas/evidence.py` (strict evidence-envelope model), `parsers/junit.py` (hardened
JUnit XML parsing), `binding/verifier.py` (evidence-to-contract binding verification). There is no
parser for any other evidence kind, no evidence *ingestion* (nothing constructs real binding claims
or evaluation targets from an actual submission yet), no policy evaluator, no database, no API, no
worker, no CI. `ARCHITECTURE.md` §3 onward describes a planned system that does not exist — do not
treat it as a map of the codebase.

The contract loader owns the *pre-schema* boundary only — size cap, safe parse, document shape,
resource bounds — and raises `ContractLoadError`. Schema failures propagate as
`pydantic.ValidationError` unchanged, so callers catch two types deliberately. The JUnit parser is
different by design: `parse_junit_evidence` never raises for malformed evidence content — it returns
`ParsedJUnitEvidence | UnusableJUnitEvidence`, because many evidence items get evaluated together and
one bad one must stay visible, not abort the batch. It only raises `JUnitEnvelopeKindError` for a
caller precondition violation (wrong envelope kind). The binding verifier never raises at all —
`verify_evidence_binding` always returns a `BindingVerificationResult`
(`BOUND`/`NOT_BOUND`/`INSUFFICIENT_INFORMATION`), even when a claim or target is entirely absent
(pass `None`). Do not re-validate schema rules inside any boundary module.

`pyproject.toml` declares FastAPI, SQLAlchemy, psycopg, Alembic, boto3, httpx, PyJWT, orjson,
PyYAML and OpenTelemetry as runtime dependencies with no importer yet. `defusedxml` and `pydantic`
are now both used. Declared-but-unused is not permission to start using the rest outside an accepted
task.

Do not create empty packages or directories for planned components. Structure is a claim about what
exists.

## Contract schema patterns

Everything derives from `StrictContractModel`: `extra="forbid"`, `frozen=True`, `strict=True`,
`populate_by_name=True`. Three consequences that trip people up:

**Strict mode rejects `list`.** That is why `_yaml_array_to_tuple` exists as a `BeforeValidator` on
every sequence field — YAML gives lists, strict Pydantic wants the declared `tuple`, and tuples keep
the model immutable. New sequence fields need the same validator.

**Strict mode rejects coercion.** `"1"` is not a valid `int`. Tests assert this.

**Wire format is camelCase, Python is snake_case.** Fields use `Field(alias="trustBoundary")` etc.
Contract documents use the alias.

**Fail-closed outcomes are pinned by the type system**, not by convention:
`Literal[Decision.BLOCK]` on `artifactMismatch`, `untrustedWorkflow`, `invalidAttestation`,
`failedCriticalJourney`; `Literal[Decision.INSUFFICIENT_EVIDENCE]` on `missingRequiredEvidence`;
plus validators forcing `exposedSecrets` and `criticalVulnerabilities` to `BLOCK`. A contract author
cannot weaken these. Removing a pin requires an ADR.

Cross-field invariants belong in `model_validator(mode="after")`, not in calling code. Existing ones
cover uniqueness, `producerWorkflow` resolution, the `artifactId`-iff-`subject == artifact` rule, and
the required-attestation obligation.

## Binding-verification patterns

`binding/verifier.py` compares three deliberately distinct object families — never conflate them:

- `EvidenceEnvelope` (schemas/evidence.py) — the evidence payload and its own digest. Unrelated to
  artifact identity.
- A binding claim (`CommitBindingClaim` / `ArtifactBindingClaim`) — untrusted, submitted alongside
  evidence, describes what the evidence *claims* to concern.
- An evaluation target (`CommitEvaluationTarget` / `ArtifactEvaluationTarget`) — canonical, supplied
  by the evaluation caller, never derived from evidence or its claims.

**`EvidenceEnvelope.digest` must never appear in a binding comparison.** It's the hash of the
evidence payload (e.g. the JUnit report bytes), not an artifact's digest. Comparing them is a real
correctness defect, not a style nit — there's a dedicated regression test for this
(`test_evidence_envelope_digest_is_never_used_as_the_artifact_digest`).

**Commit SHA / artifact digest / digest algorithm are unformatted, bounded strings, not `Slug`.**
No format for any of them exists anywhere in the repo, and `Slug`'s pattern requires a leading
letter — real hex digests and SHAs routinely start with a digit.

**A matching claim proves consistency, not authenticity.** `producer_workflow` matching
`RequiredEvidenceItem.producer_workflow` means the submitted identifier agrees with what the
contract requires — nothing more. It does not prove that workflow produced the evidence. Never word
a docstring or diagnostic as if it does; OIDC and attestation verification don't exist yet.

**Result carries one reason, not a list.** When multiple things are wrong, `verify_evidence_binding`
reports the single most-foundational one, in a fixed precedence (unknown requirement → kind/subject
→ claim/target structural consistency → workflow → algorithm policy → target identity →
insufficient information). This mirrors `Decision`'s existing most-severe-wins precedence in
`contract.py`. Don't accumulate a list of simultaneous reasons — that would be a new mechanic the
rest of the codebase doesn't use.

## Fail-closed rule

Missing, malformed, contradictory, expired, incomplete or untrusted required evidence must never
produce `PASS`. An unhandled exception, timeout or parse failure on a decision path resolves to
`INSUFFICIENT_EVIDENCE`. Any `try`/`except` that can influence a decision must state in code which
decision the failure yields — silent recovery is prohibited.

Rejection tests match the diagnostic message, so a test cannot pass because of an unrelated failure.
Follow that pattern.

## Prohibited security claims

Never write these in code, comments, docs, logs or reports — each is false, and `PRD.md` §5 explains
why: Pydantic detects tampering; OIDC proves a runner is uncompromised; SHA-256 proves an artifact is
safe; a GitHub check alone prevents deployment; QualityMesh guarantees defect-free or malware-free
software.

The governing distinction: integrity proves the approved bytes did not change, not that they were
safe.

## Gotchas

**Validators must raise `ValueError`, never `TypeError`.** Pydantic converts only `ValueError` and
`AssertionError` into `ValidationError`; a `TypeError` escapes `model_validate` uncaught and reaches
callers as an unexpected type. This bit the codebase once already — see Task 1.2's record.

**YAML aliases are refused outright by the loader.** Not stylistic: alias expansion happens inside
`safe_load`, so no post-parse bound can contain it — 375 bytes expanded to ~4.8M nodes and 7.6s
before the node cap fired. Any new parser for a hostile format needs its limits applied *before*
construction, not after.

**Coverage is 99.40% against `fail_under = 80`.** Comfortable, but the number is not the point:
every rejection rule has a test asserting its *specific* diagnostic. Match messages, not just
exception types — otherwise a test passes because an unrelated validation error fired first.

**After a `cast()`, re-check every subsequent reference — a stray use of the pre-cast name is a
type-safety hole strict mode won't always catch on its own.** `binding/verifier.py` had exactly this
bug: after `artifact_target = cast(ArtifactEvaluationTarget, evaluation_target)`, one f-string kept
referencing `evaluation_target.artifact_id` (still union-typed). Pyright caught it as
`reportUnknownMemberType`/`reportAttributeAccessIssue` — but only because the union's other member
(`CommitEvaluationTarget`) genuinely lacks that attribute. A cast to a type that happened to share
the field name would have hidden this silently.

**`pyright: ignore` comments in tests that construct intentionally-invalid objects are usually
load-bearing, not habit — verify before trusting either way.** Every negative-construction test
(`ArtifactBindingClaim(...)` missing a required field, etc.) needs one, since pydantic's runtime
rejection is exactly what strict-mode static checking would also catch. Don't assume they're
speculative just because there are many of them — stripped all 25 in this task's test files and
every single one reappeared as a real error.

**`defusedxml.fromstring` defaults to `forbid_dtd=False`.** Pass `forbid_dtd=True` explicitly, or
DOCTYPE-based attacks with no `<!ENTITY>` at all sail through untouched — `forbid_entities=True`
alone (the library default) does not reject a bare DOCTYPE.

**A duplicate resource-bound check can make a real code path silently unreachable.** `junit.py`
originally checked "zero suites" in two places (an early guard, and the outcome model's own
invariant); coverage caught that the second check — and the exception branch that would ever reach
it — were structurally dead. One check per invariant; verify with coverage, not intuition.

**`uv.lock` was regenerated against public PyPI.** It previously pinned an unreachable internal
mirror. Regenerating inside a different network rewrites all 1191 URLs. Do not regenerate as a side
effect of another change; lockfile updates are their own commit.

**Runtime is CPython 3.14 but ruff and pyright target 3.12.** `requires-python = ">=3.12"` permits
the drift; there is no `.python-version`.

**Pyright strict covers `tests` as well as `src`.** Tests are not exempt. `# type: ignore` needs a
narrow error code plus a written justification. Never weaken `typeCheckingMode` or add a global
`reportX = false` to get green.

**`pre-commit` is a dev dependency but no `.pre-commit-config.yaml` exists.** Hooks do not run.

## Working rules

Fix root causes at the correct boundary — never loosen an assertion, catch an exception, weaken a
type or lower a threshold to make something pass. One focused behavior per commit. No unrelated
refactoring inside a feature task. No push without explicit approval.

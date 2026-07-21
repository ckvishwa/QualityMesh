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

Two modules exist: `schemas/contract.py` (strict models) and `contracts/loader.py` (safe YAML
loading). There is no evidence model, no parser, no policy evaluator, no database, no API, no
worker, no CI. `ARCHITECTURE.md` §3 onward describes a planned system that does not exist — do not
treat it as a map of the codebase.

The loader owns the *pre-schema* boundary only — size cap, safe parse, document shape, resource
bounds — and raises `ContractLoadError`. Schema failures propagate as `pydantic.ValidationError`
unchanged, so callers catch two types deliberately. Do not re-validate schema rules in the loader.

`pyproject.toml` declares FastAPI, SQLAlchemy, psycopg, Alembic, boto3, httpx, PyJWT, defusedxml,
orjson, PyYAML and OpenTelemetry as runtime dependencies. **None are imported.** Only `pydantic` is
used. Their presence is not permission to start using them outside an accepted task.

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

**Coverage is 98.73% against `fail_under = 80`.** Comfortable, but the number is not the point:
every rejection rule has a test asserting its *specific* diagnostic. Match messages, not just
exception types — otherwise a test passes because an unrelated validation error fired first.

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

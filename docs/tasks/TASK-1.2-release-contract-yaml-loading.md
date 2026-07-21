# Task 1.2 — Safe Release-Contract YAML Loading

## Status

Completed — 2026-07-20.

## Goal

Load a versioned release contract from YAML into the existing strict `ReleaseContract`
model, treating the document as untrusted input at every step, without duplicating or
weakening schema validation.

## Problem

Task 1.1 delivered strict contract models but no way to load a real
`release-contract.yaml`. Closing that gap naively introduces two distinct risks:

1. **Unsafe parsing.** `yaml.load` with a permissive loader honours tags such as
   `!!python/object/apply:os.system`, turning a contract file into arbitrary code
   execution. Unbounded parsing also admits denial of service through size, nesting
   depth or collection explosion.
2. **A missing boundary.** Handing raw parser output straight to Pydantic produces
   unclear diagnostics, and lets non-mapping or multi-document files reach the model in
   shapes it was never designed to reject.

## In Scope

- `src/qualitymesh/contracts/loader.py` and its package `__init__.py`.
- Byte-size limit applied before parsing.
- Safe, non-constructing YAML parse.
- Document-shape checks: single document, mapping root, string keys.
- Resource bounds: nesting depth, per-sequence length, per-mapping size, total nodes.
- Valid and malformed fixtures under `tests/fixtures/contracts/`.
- `tests/unit/test_contract_loader.py`.

## Out of Scope

Evidence models and parsers, digest verification, policy evaluation, persistence,
object storage, workers, FastAPI, GitHub OIDC and Checks, CLI commands, new
dependencies. None were touched.

## Acceptance Criteria

| # | Criterion | Status |
| --- | --- | --- |
| AC-1 | Valid YAML loads into `ReleaseContract` | Met |
| AC-2 | Oversized input rejected before parsing | Met |
| AC-3 | Unsafe Python tags rejected | Met |
| AC-4 | Multiple documents rejected | Met |
| AC-5 | Empty, scalar and sequence roots rejected | Met |
| AC-6 | Non-string mapping keys rejected | Met |
| AC-7 | Nesting depth and collection sizes bounded | Met |
| AC-8 | Schema guarantees unchanged; validation not duplicated | Met |
| AC-9 | Diagnostics name source and position without echoing content | Met |
| AC-10 | Every schema rejection branch covered by a fixture-driven test | Met |
| AC-11 | Task 1.1 tests unchanged and green | Met |

## Security Conditions

- `yaml.safe_load` only. No loader that can construct Python objects.
- `MAX_CONTRACT_BYTES` (256 KiB) checked against `len(data)` before the parser runs, and
  against `Path.stat().st_size` so an oversized file is never read into memory.
- `MAX_NESTING_DEPTH` (24) checked before descending, so the bounded traversal cannot
  exhaust the interpreter stack.
- `MAX_SEQUENCE_ITEMS` (512), `MAX_MAPPING_KEYS` (256), `MAX_TOTAL_NODES` (5000).
- Error messages carry the source name and, where PyYAML records one, a line and column.
  They never include document content. `str(exc)` on a PyYAML `MarkedYAMLError` embeds a
  snippet of the offending source line, so only `problem_mark` is read — never the
  rendered exception text.
- No clock, network or environment access during loading.

### What this task does not establish

Safe loading proves the document was parsed without executing anything and conforms to
the contract schema. It does not prove the contract is *correct* — a well-formed
contract that omits an evidence obligation is still a weak contract. That limitation is
recorded in `PRD.md` section 13.

## Relevant Files

| File | Role |
| --- | --- |
| `src/qualitymesh/contracts/loader.py` | New. The whole implementation. |
| `src/qualitymesh/contracts/__init__.py` | New. Public re-exports. |
| `src/qualitymesh/schemas/contract.py` | Modified. Three `TypeError` raises corrected to `ValueError` — see *Problems Encountered*. |
| `tests/unit/test_contract_loader.py` | New. 50 tests. |
| `tests/fixtures/contracts/*.yaml` | New. 34 fixtures. |

## Relevant Decisions

**Two functions, one exception type.** `load_release_contract_bytes` and
`load_release_contract_file`. No loader class, no repository abstraction, no service
layer. A text-loading variant was considered and rejected as unused.

**`ContractLoadError` covers only the pre-schema boundary.** Schema failures propagate as
`pydantic.ValidationError` unchanged. Wrapping them would duplicate the field-location
information Pydantic already produces and would create a second, redundant diagnostic
format. The trade-off is that callers catch two exception types; that is honest, because
two genuinely different things can go wrong.

**Node counting includes scalars.** Counting only collections would let a single mapping
of 250 keys × 20 scalar values pass a collection-only budget while still being large.
Counting every node is simpler to reason about and needs no special cases.

**Bounds are enforced after parsing, not during.** PyYAML offers no streaming budget hook
without a custom loader subclass. The byte-size limit is the pre-parse defence; the
traversal is the post-parse one. A custom composer was rejected as disproportionate for
V1 — recorded here so the trade-off is visible rather than implied.

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

### P-1 — `TypeError` escaped `model_validate` uncaught *(defect in Task 1.1 code)*

Three validators in `contract.py` raised `TypeError`: `_yaml_array_to_tuple`,
`_parse_evidence_kind` and `_parse_evidence_subject`. Pydantic v2 converts only
`ValueError` and `AssertionError` into `ValidationError`; anything else propagates
unchanged.

A caller writing `except ValidationError` around `model_validate` would therefore see an
uncaught `TypeError` for three realistic malformed contracts — a scalar where a sequence
belongs, and a non-string evidence kind or subject. The rejection still happened, so this
was not a fail-open defect, but the error surfaced in a type callers had no reason to
expect, at the wrong boundary.

Confirmed empirically before any code was written:

```text
scalar-allowedRefs: TypeError (ESCAPES) | expected an array
int-kind:           TypeError (ESCAPES) | evidence kind must be a string
int-subject:        TypeError (ESCAPES) | evidence subject must be a string
```

### P-2 — Pyright strict rejected `isinstance` narrowing of untrusted containers

`isinstance(node, dict)` narrows to `dict[Unknown, Unknown]`, which strict mode reports as
`reportUnknownVariableType`.

### P-3 — Coverage margin was 0.30 points before this task

Adding a module with any uncovered line risked breaking the build.

### P-4 — Post-parse resource bounds did not stop alias amplification *(found in review)*

The YAGNI/complexity review of the finished diff surfaced a hole in the implementation
itself. YAML aliases are expanded by `yaml.safe_load` during *construction*, which happens
before any post-parse traversal can run. `MAX_TOTAL_NODES` therefore fired only after the
damage was done.

Measured against the implementation as first written:

```text
wire size: 375 bytes (limit 262144) -> under limit: True
ContractLoadError: <memory>: document holds more than 5000 nodes
elapsed: 7.63s
```

375 bytes cost 7.63 seconds and roughly 4.8 million constructed nodes. One further
nesting level is 9× that. The size limit does not help — the payload is tiny on the wire —
and neither does any bound applied after parsing.

### P-5 — types-pyyaml leaves the pre-construction API untyped

Inspecting a document before construction requires PyYAML's event layer, and
types-pyyaml declares `yaml.parse` as `(stream: Unknown, ...) -> Unknown`. The loader-object
route (`yaml.SafeLoader(...)`, `check_event`, `get_event`, `dispose`) is untyped for every
method, and additionally raises `ReaderError` from the *constructor*, outside any guard —
which the undecodable-input test caught as a real escape.

## Resolutions

### R-1 — Fixed at the schema boundary, not in the loader

The three `raise TypeError` statements became `raise ValueError`, with a comment stating
why. Catching `TypeError` in the loader was rejected: it would patch the symptom at the
wrong boundary and leave every other caller of `model_validate` exposed.

No validation was weakened — the same inputs are still rejected, now as `ValidationError`
with a field location. All nine Task 1.1 tests remained green and unmodified.

### R-2 — `cast` after the runtime check, with the reason stated

`cast("dict[object, object]", node)` and `cast("list[object]", node)` immediately after
the `isinstance` guard. Permitted by `SYSTEM_RULES.md` section 5: a runtime check has
established the type, and elements stay `object` so keys and values are still narrowed
explicitly. No `# type: ignore` was used anywhere.

### R-3 — Loader reached 100% coverage

Every loader branch is tested, including the PyYAML error path that carries no position
mark, reached with undecodable bytes. Total coverage rose to 98.70%. `fail_under`
was not changed.

### R-4 — Aliases are refused before construction

`_reject_aliases` streams the document through `yaml.parse` — which yields events without
constructing anything and is linear in the input — and refuses any `AliasEvent` before
`safe_load` runs. A release contract has no legitimate use for aliases, so an outright
refusal costs nothing in expressiveness and is fail-closed.

The same payload now raises `YAML aliases are not permitted in a release contract` in
0.28s instead of 7.89s. Both a fixture (`invalid-yaml-alias.yaml`) and a constructed
amplification payload are tested.

A lexical pre-scan for `&` and `*` was rejected as an alternative: both characters occur
inside legitimate contract values such as `refs/pull/*/merge`, so it would reject valid
contracts.

### R-5 — One narrow, justified suppression

`# pyright: ignore[reportUnknownMemberType]` on the `yaml.parse` call, with the result
`cast` to `Iterable[yaml.Event]` so nothing downstream is unknown. This is the case
`SYSTEM_RULES.md` section 5 contemplates: narrow error code, written explanation, and a
demonstrated absence of a typed alternative. It is the only suppression in `src/`.

Sequencing note: `yaml.parse` is a generator, so parse errors surface during iteration
inside the guard, rather than from a constructor outside it.

## Completion Summary

Added `qualitymesh.contracts` with a two-function public API, 35 fixtures and 52 tests.
Total suite: **61 passed** (9 pre-existing, unmodified). Coverage **80.30% → 98.73%**,
loader at **100%**. Ruff lint and format clean, Pyright strict reports 0 errors.
Verified on both CPython 3.14.4 and CPython 3.12.13.

Two defects were found and fixed at their correct boundaries: an escaping `TypeError` in
existing schema code (P-1/R-1), and alias amplification in this task's own implementation,
caught by the post-implementation review rather than by the original test plan (P-4/R-4).
No dependency was added or changed; `uv.lock` is untouched.

**Remaining gap:** `contract.py` lines 95 and 108 — the `return value` identity paths in
`_parse_evidence_kind` and `_parse_evidence_subject`, reachable only by constructing a
model with an `EvidenceKind` or `EvidenceSubject` instance in Python. They are
unreachable through the YAML boundary, which yields strings, and they are accept paths
rather than rejection branches. Left uncovered deliberately rather than tested through an
artificial path.

# QualityMesh

Deterministic release-assurance and evidence-evaluation engine.

QualityMesh verifies whether required release evidence is structurally valid, authentic, complete,
correctly bound to its source and artifact, and compliant with a versioned release policy. It emits
exactly one decision:

```text
BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS
```

QualityMesh does not run tests, builds or scanners, and does not judge whether software is correct
or safe. It verifies that the required evidence exists, is authentic, is bound to the artifact under
release, and satisfies a declared policy.

> Integrity proves that the approved bytes did not change. It does not prove that the approved bytes
> were safe in the first place.

---

## Status

**Phase 1 — Release Contract and Rules.**

Implemented today: the strict, versioned release-contract schema (`src/qualitymesh/schemas/`) and
safe YAML loading of that contract (`src/qualitymesh/contracts/`). Everything else — evidence
ingestion, parsing, digest verification, the policy evaluator, persistence, the worker, the API and
GitHub integration — is planned and does not exist.

The validation suite passes: 61 tests, ruff clean, pyright strict clean, coverage 98.73%, on
CPython 3.14 and 3.12. See [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for the authoritative
record.

---

## Repository structure

```text
qualitymesh/
├── README.md
├── pyproject.toml
├── uv.lock
├── .gitignore
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── SYSTEM_RULES.md
│   ├── PROJECT_STATUS.md
│   ├── adr/
│   └── tasks/
├── src/
│   └── qualitymesh/
│       ├── contracts/        # safe YAML loading of a release contract
│       └── schemas/          # strict release-contract models
└── tests/
    ├── fixtures/contracts/   # valid and malformed contract documents
    └── unit/
```

Directories are created by the task that fills them. Empty scaffolding for planned components is
deliberately absent.

---

## Local validation

```bash
uv sync --frozen --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

With coverage:

```bash
uv run pytest --cov=qualitymesh --cov-report=term-missing
```

All five commands must pass before a task is considered done. Do not weaken a validation setting to
obtain a green result.

---

## Documentation

| Document | Contents |
| --- | --- |
| [Product requirements](docs/PRD.md) | Product boundary, threat scenarios, decisions and precedence, V1 requirements, acceptance scenarios, known limitations |
| [Architecture](docs/ARCHITECTURE.md) | Current implementation, planned topology and dataflow, trust boundaries, component guarantees, security controls |
| [Engineering rules](docs/SYSTEM_RULES.md) | Source-of-truth hierarchy, fail-closed rules, validation and type-safety rules, testing, dependencies, definition of done |
| [Project status](docs/PROJECT_STATUS.md) | What is actually built, known decisions, limitations, latest validation results |
| [ADR index](docs/adr/README.md) | When to write an ADR, naming, statuses, template |
| [Task records](docs/tasks/) | Per-task goal, acceptance criteria, problems encountered and resolutions |

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

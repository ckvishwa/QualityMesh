# QualityMesh

**Core validation: 167 tests and 99.49% branch-aware coverage (`pytest-cov`).** The full repository suite also exercises the separate automation harness. Missing or mismatched evidence stays in an explicit non-success state; it cannot silently satisfy a requirement.

[![Validation on Python 3.12 and 3.14](https://github.com/ckvishwa/QualityMesh/actions/workflows/validate.yml/badge.svg)](https://github.com/ckvishwa/QualityMesh/actions/workflows/validate.yml)

Deterministic release-assurance and evidence-evaluation engine.

QualityMesh is being built to check release evidence against a versioned contract. The implemented
Phase 1 code safely loads contracts, validates evidence envelopes, parses JUnit reports, compares
binding claims with a supplied release target, and checks completeness. It does **not** yet verify
producer identity or cryptographic provenance, ingest submissions, or issue release decisions.

The planned policy evaluator will use this precedence:

```text
BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS
```

QualityMesh does not run tests, builds, or scanners, and does not judge whether software is correct
or safe. A matching workflow identifier in an evidence claim is not proof that the workflow produced
the evidence.

> Integrity proves that the approved bytes did not change. It does not prove that the approved bytes
> were safe in the first place.

---

## Status

**Phase 1 — Release Contract and Rules.**

Implemented today: the strict, versioned release-contract schema, safe YAML loading of that
contract, a strict evidence-envelope schema, a hardened JUnit XML parser, deterministic
evidence-to-contract binding verification, and deterministic evidence-completeness evaluation
(`src/qualitymesh/{schemas,contracts,parsers,binding,completeness}/`). Everything else — evidence
ingestion, evidence kinds other than JUnit, the policy evaluator, persistence, the worker, the API
and GitHub integration — is planned and does not exist. Binding verification and completeness
evaluation are pure functions; nothing yet supplies them real data from an actual submission, since
that requires ingestion (FR-3) to exist first.

The current local run has 167 core tests and 390 automation tests passing on CPython 3.14.4, with
99.49% combined statement-and-branch coverage of `src/qualitymesh`. The automation harness under
`automation/` is separate from the release-evidence engine and has no live coding-provider adapter.
See [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) for the dated validation record.

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
├── automation/             # separate experimental task orchestration harness
├── tasks/backlog.yaml      # automation backlog
├── src/
│   └── qualitymesh/
│       ├── binding/          # evidence-to-contract binding verification
│       ├── completeness/     # evidence completeness evaluation
│       ├── contracts/        # safe YAML loading of a release contract
│       ├── parsers/          # hardened JUnit XML parsing
│       └── schemas/          # strict release-contract and evidence-envelope models
└── tests/
    ├── fixtures/contracts/   # valid and malformed contract documents
    ├── fixtures/junit/       # valid and hostile JUnit XML documents
    ├── unit/               # release-evidence engine tests
    └── automation/         # orchestration harness tests
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

The full `pytest` command covers both the core and automation tests. Do not weaken a validation
setting to obtain a green result. The automation harness does not run a secret scanner: a caller
must supply a verified clean-scan attestation before it can approve a commit, and missing attestation
blocks commit eligibility.

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

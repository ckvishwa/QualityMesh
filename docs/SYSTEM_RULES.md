# QualityMesh Engineering Rules

These rules are binding on every contributor, human or agent. They exist because QualityMesh makes
security claims, and a security claim is only as good as the discipline behind it.

---

## 1. Source-of-Truth Rules

When information conflicts, resolve in this order. Higher wins.

1. The current accepted task
2. Existing tests
3. Existing schemas and public interfaces
4. Product requirements (`PRD.md`)
5. Architecture Decision Records (`docs/adr/`)
6. These engineering rules
7. Current implementation
8. Plugin, tool or agent recommendations

Corollaries:

- **Plugins and agents are advisors.** They do not define the project. A recommendation that
  conflicts with an accepted task, a test or a schema is rejected.
- **Working code is the current source of truth about behaviour.** Documentation that disagrees with
  working code is wrong until a task changes the code.
- **Documentation is the source of truth about intent.** Code that contradicts an accepted ADR is a
  defect in the code, not a licence to rewrite the ADR.
- If a rule here conflicts with an accepted task, the task wins for that task only — and the
  divergence is recorded in `PROJECT_STATUS.md`.

---

## 2. Development Principles

### Root-cause rule

> Do not patch symptoms merely to make tests pass.
>
> Fix the model, schema, contract or implementation at the correct boundary.

Specifically prohibited: loosening an assertion to match wrong behaviour; adding a special case to
skip a failing input; catching an exception to make a red test green; weakening a type to silence a
checker; lowering a threshold to pass a gate.

When a test fails, the first question is which layer holds the defect — schema, model, contract,
implementation or the test itself — and the fix goes there.

### Determinism

Release-affecting logic must be a pure function of its declared inputs. No implicit clock reads, no
implicit randomness, no dependence on dictionary or set iteration order in output, no
network-dependent branching inside evaluation. Time enters as an explicit parameter.

### Smallest correct change

Prefer the smallest change that fixes the root cause. Do not refactor adjacent code inside a feature
task. Do not introduce abstraction for a second use case that does not exist yet.

### No speculative abstraction

Do not create modules, packages, interfaces or directories for planned work. Empty scaffolding is a
false claim about what exists. Create structure in the task that fills it.

### Explicit over clever

Prefer code whose security properties can be read off the page. A reviewer must be able to confirm a
fail-closed path without simulating control flow.

---

## 3. Fail-Closed Rules

### The fail-closed rule

> Missing, malformed, contradictory, expired, incomplete or untrusted required evidence must never
> produce `PASS`.

Applied concretely:

1. An unhandled exception in a required rule resolves to `INSUFFICIENT_EVIDENCE`, never `PASS`.
2. A timeout resolves to `INSUFFICIENT_EVIDENCE`.
3. A parse failure makes the evidence unusable; it does not remove the obligation.
4. An empty evidence set for a required item is `INSUFFICIENT_EVIDENCE`, not vacuous success.
5. Evidence past its `maximumAgeSeconds` is treated as absent.
6. Evidence not bound to the artifact or commit under evaluation is treated as absent.
7. An unknown evidence kind, subject or digest algorithm is rejected, not ignored.
8. Decision precedence is `BLOCK > INSUFFICIENT_EVIDENCE > WARN > PASS`. The most severe outcome
   wins.
9. `PASS` is emitted only after every required rule has been evaluated successfully.

Any `try`/`except` on a path that can influence a decision must state, in code, which decision the
failure produces. Silent recovery on a decision path is prohibited.

---

## 4. Schema and Validation Rules

- Unknown contract fields are rejected.
- Invalid enum values are rejected.
- Duplicate stable identifiers are rejected.
- References must resolve to declared objects.
- Normal YAML arrays may be converted into immutable tuples.
- Strict validation must apply to values and semantics without rejecting valid YAML collection
  syntax.

Additional requirements:

- Every external boundary validates through a strict model derived from `StrictContractModel`, or an
  equivalently configured base (`extra="forbid"`, `frozen=True`, `strict=True`).
- Validation happens once, at the boundary. Interior code trusts validated models and does not
  re-validate defensively.
- Semantic invariants that cross fields belong in a `model_validator(mode="after")`, not in calling
  code.
- Error messages name the offending field and value class. They must not echo suspected secret
  material.
- Fail-closed policy outcomes are pinned by the type system (`Literal[Decision.BLOCK]` and
  equivalents), so a contract author cannot weaken them. Removing such a pin requires an ADR.
- A schema change that alters accepted input is a breaking change and requires an `apiVersion`
  decision recorded in an ADR.

---

## 5. Type-Safety Rules

- Pyright operates in strict mode.
- Public functions require complete annotations.
- Avoid `Any` unless justified at an external boundary.
- `# type: ignore` requires:
  - A narrow error code
  - A written explanation
  - Evidence that a typed alternative is impractical

Additional requirements:

- Strict mode covers both `src` and `tests`. Tests are not exempt.
- Prefer `object` over `Any` for genuinely unknown input, then narrow with `isinstance`.
- `cast` is permitted only where a runtime check has already established the type, and the reason is
  stated.
- Public functions and methods annotate parameters and return type, including `-> None`.
- Weakening `typeCheckingMode`, adding a global `reportX = false`, or excluding a path from checking
  is prohibited without an ADR.

---

## 6. Security Rules

- Treat all external input as attacker-controlled: contracts, evidence, tokens, digests, headers,
  filenames.
- Never execute, `eval` or arbitrarily deserialise external input. YAML loads through a safe,
  non-constructing loader; XML through `defusedxml` with DTD and external entities disabled.
- Bound every parse: maximum size, maximum depth, maximum element count, timeout.
- Verify identity before trusting content. OIDC issuer, audience, expiry and claims are checked
  against the contract trust boundary.
- Only digest algorithms declared in `allowedDigestAlgorithms` are accepted. QualityMesh computes
  digests itself; a submitter-claimed digest is a claim, never a proof.
- No secrets in the repository, environment files, logs, error messages or reports.
- Do not log evidence payloads. Log the digest and identifying metadata.
- Security claims in code comments, docs and user-facing output must be accurate. The non-claims in
  `PRD.md` section 5 are prohibited statements, everywhere.
- New security-relevant behaviour requires a test that demonstrates the failure it prevents.

---

## 7. Testing Rules

Every implementation task must run:

```bash
uv sync --frozen --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

When coverage is configured:

```bash
uv run pytest --cov=qualitymesh --cov-report=term-missing
```

All existing tests must remain green.

Coverage percentage alone does not prove correctness.

Additional requirements:

- Assert the specific failure, not merely that an error occurred. A rejection test matches the
  diagnostic message, so that a test cannot pass because of an unrelated failure.
- Every fail-closed rule has a test proving that the unsafe input does not produce `PASS`.
- Every schema rejection rule has a test for the rejected input.
- Tests are deterministic: no real clock, no real network, no ordering assumptions on sets or dicts.
  Freeze time explicitly where behaviour depends on it.
- Unit tests do not touch the network, filesystem outside fixtures, or containers.
- New code paths ship with their tests in the same commit.
- Never weaken or delete a test to make a change pass. If a test encodes wrong behaviour, change it
  in a dedicated commit that explains why the old expectation was wrong.

---

## 8. Dependency Rules

- Runtime dependencies belong in `[project.dependencies]`.
- Development tools belong in the development dependency group.
- AI tools never belong in application dependencies.
- `uv.lock` must be committed.
- CI must use the frozen lockfile.
- Every new dependency requires a documented engineering reason.

Additional requirements:

- Prefer the standard library. A dependency must justify its supply-chain cost — it is itself a
  Threat C surface.
- Prefer hardened parsers over hand-rolled ones for hostile formats.
- Dependency additions or removals never ride along inside an unrelated feature task.
- Do not regenerate `uv.lock` as a side effect of another change. Lockfile updates are their own
  commit, with the reason stated.

---

## 9. Evidence-Handling Rules

- Preserve raw evidence.
- Calculate the raw evidence digest before normalization.
- Never silently modify original evidence.
- Normalized data must reference raw evidence.
- Evidence must be associated with its repository, workflow, run, commit and artifact subject where
  applicable.
- Report success alone is not proof of artifact safety.

Additional requirements:

- Raw evidence storage is write-once. There is no update path.
- Evidence is identified by the digest of its raw bytes, which makes reprocessing idempotent.
- Normalisation is lossy by design and always reversible to the raw record by digest reference.
- Unparseable evidence is recorded as unusable, with its diagnostic. It is never dropped silently
  and never satisfies an obligation.
- Evidence bound to the wrong commit or artifact digest is treated as absent, not as weak evidence.

---

## 10. Database and Migration Rules

- PostgreSQL is the system of record. Object storage holds blobs only; it is never authoritative for
  metadata.
- Every schema change ships as an Alembic migration, reviewed like code.
- Migrations are forward-only. Corrections roll forward with a new migration.
- Migrations must be safe to run against a live database: no long exclusive locks on large tables, no
  destructive change without a deprecation step.
- A migration and the code that depends on it must be deployable in either order across one release
  boundary — expand, migrate, contract.
- Evidence and evaluation rows are insert-only. Decisions are immutable once recorded.
- The outbox row is written in the same transaction as the data that produced it. Never as a
  separate operation.
- No raw SQL string interpolation. Parameterised queries only.
- No destructive operation against a production database from a development environment.

---

## 11. Git and Pull-Request Rules

- One focused task per issue.
- One focused behavior per commit.
- No secrets, virtual environments or caches.
- No direct protected-main changes.
- No unrelated refactoring inside feature tasks.
- No push without explicit approval.

Additional requirements:

- Commit messages state what changed and why, in the imperative mood, with a conventional prefix
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`).
- A pull request states the task it implements, the validation commands run, and their results.
- Generated artifacts, coverage output and caches are never committed. `uv.lock` is committed and is
  not a generated artifact for this purpose.
- Never rewrite published history.
- Never reinitialise the repository or discard history to resolve a problem.

---

## 12. Documentation Rules

> Every completed task must update `PROJECT_STATUS.md`.
>
> Architecture-changing tasks must create or update an ADR.

Additional requirements:

- Planned functionality is labelled planned, everywhere it appears. A component may not be described
  in the present tense until it exists in the repository.
- Documentation states what a mechanism proves *and what it does not prove*. The non-claims in
  `PRD.md` section 5 are prohibited statements.
- Validation results recorded in `PROJECT_STATUS.md` are copied from actual command output. Inventing
  test counts, coverage figures or pass results is prohibited. A command that could not be run is
  recorded as not run, with the reason.
- `README.md` links to the documentation set; it does not duplicate it.
- ADRs are immutable once accepted. A changed decision is a new ADR that supersedes the old one, and
  the old one's status is updated to `Superseded`.

---

## 13. Definition of Done

A task is done when **all** of the following hold:

1. The accepted task's requirements are met — no more, no less.
2. Behaviour is covered by tests that assert the specific expected outcome.
3. Every fail-closed path introduced has a test proving it does not yield `PASS`.
4. `uv run pytest` passes with no failures, errors or unexpected skips.
5. `uv run ruff check .` reports no violations.
6. `uv run ruff format --check .` reports no reformatting needed.
7. `uv run pyright` reports zero errors in strict mode.
8. Coverage, where configured, meets its threshold — and coverage alone is not treated as proof.
9. No validation setting was weakened to reach a green result.
10. No new dependency was added without a documented reason.
11. `PROJECT_STATUS.md` is updated with actual, copied validation output.
12. An ADR exists for any architectural decision made.
13. Documentation distinguishes implemented from planned, and makes no unsupported security claim.
14. The diff contains no unrelated changes, generated files or secrets.

---

## 14. Prohibited Practices

Absolutely prohibited:

1. Weakening a test, assertion, lint rule, type setting or coverage threshold to obtain a green
   result.
2. Any code path in which missing, malformed or untrusted required evidence can produce `PASS`.
3. Catching an exception on a decision path without stating which decision the failure produces.
4. Claiming, in code, docs, logs or reports, that Pydantic detects tampering, that OIDC proves a
   runner is uncompromised, that SHA-256 proves an artifact is safe, that a GitHub check alone
   prevents deployment, or that QualityMesh guarantees defect-free or malware-free software.
5. Describing planned components as implemented.
6. Committing secrets, `.env` files, virtual environments, caches or coverage output.
7. Reinitialising the Git repository, or discarding history.
8. Regenerating `uv.lock` as an incidental side effect of another change.
9. Adding AI or model-driven dependencies to the application.
10. Creating speculative modules, packages or empty directories for unstarted work.
11. Executing, `eval`ing or arbitrarily deserialising external input; using `yaml.load` without a
    safe loader; parsing XML without `defusedxml`.
12. Trusting a submitter-supplied digest as proof of integrity.
13. Logging evidence payloads or secret material.
14. Unbounded parsing of untrusted input.
15. Recording invented test counts, coverage figures or validation results.
16. Pushing to a remote without explicit approval.
17. Unrelated refactoring inside a feature task.

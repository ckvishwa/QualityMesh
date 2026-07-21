# Architecture Decision Records

An ADR records one architecturally significant decision: the context that forced it, the decision
taken, the consequences accepted, the alternatives rejected, and the condition under which the
decision should be revisited.

ADRs exist because the reasoning behind a decision decays faster than the decision itself. Six
months later the code shows *what* was chosen; only the ADR shows *why*, and what would have to
change for the choice to become wrong.

---

## When to write an ADR

Write one when a decision:

- constrains the system's structure or its trust boundaries;
- selects or rejects a significant technology or external dependency;
- changes the release-contract `apiVersion` or the meaning of an existing field;
- changes fail-closed behaviour, decision semantics or decision precedence;
- weakens a validation, type-safety or security control — such a change is **not permitted without
  an accepted ADR**, per `SYSTEM_RULES.md` sections 4, 5 and 14;
- introduces a persistence, messaging or deployment mechanism;
- deliberately defers something a reader would otherwise expect to exist.

Do **not** write one for routine implementation choices, naming, refactoring, or anything reversible
in an afternoon.

Per `SYSTEM_RULES.md` section 12, an architecture-changing task must create or update an ADR, and
every completed task must update `PROJECT_STATUS.md`.

---

## Naming convention

```text
NNNN-kebab-case-decision-title.md
```

- `NNNN` is a zero-padded, monotonically increasing four-digit number. Numbers are never reused,
  even if an ADR is rejected or superseded.
- The title is a short noun phrase naming the decision, not the problem.

Anticipated ADRs for the decisions already made or planned. **These files do not exist yet** — each
is created by the task that makes or first depends on the decision:

```text
0001-release-contract-as-source-of-truth.md
0002-postgresql-as-system-of-record.md
0003-object-storage-for-raw-evidence.md
0004-postgresql-outbox-before-message-broker.md
0005-deterministic-policy-decisions.md
```

Reserving a number in this list is not a decision. Only an accepted file is.

---

## Status values

| Status | Meaning |
| --- | --- |
| `Proposed` | Written and under discussion. Not binding. |
| `Accepted` | Binding. The system is expected to conform. |
| `Superseded` | Replaced by a later ADR. The header must name the replacement. |
| `Rejected` | Considered and declined. Retained so the option is not silently reconsidered. |

**Accepted ADRs are immutable.** A changed decision is a *new* ADR that supersedes the old one; the
old file's status becomes `Superseded by ADR-NNNN`. Editing the reasoning of an accepted ADR
destroys the record this directory exists to keep. Correcting a typo is fine; rewriting a rationale
is not.

---

## Template

Copy this into a new file and fill every section. A section with nothing to say is a signal the
decision is not ready, not a section to delete.

```markdown
# ADR-NNNN — Decision title

## Status
Proposed | Accepted | Superseded | Rejected

## Context

## Decision

## Consequences

## Alternatives Considered

## Replacement or Review Trigger
```

### What each section must contain

**Status.** One value from the table above, plus the date. If `Superseded`, name the ADR that
replaced it. If `Accepted`, the system is expected to conform and deviation is a defect.

**Context.** The forces that made a decision necessary: the requirement, constraint, threat or
failure mode. State facts, not the conclusion. A reader who disagrees with the decision should still
recognise the problem as real.

**Decision.** What was decided, in the active voice and present tense — "QualityMesh computes the
raw evidence digest before normalisation." Be specific enough that conformance is checkable.

**Consequences.** What follows, including the costs. An ADR listing only benefits is incomplete. Name
what becomes harder, what is now locked in, and what operational burden is accepted.

**Alternatives Considered.** Each realistic option, and the specific reason it lost. "We chose X
because it is better" is not a reason. This section is what stops a rejected option being
rediscovered and re-argued.

**Replacement or Review Trigger.** The observable condition that should cause this decision to be
revisited — a scale threshold, a new requirement, a dependency's end of life, a measured failure
rate. An ADR with no review trigger becomes permanent by default rather than by intent.

# ADR-0001 — The release contract is the versioned source of truth for release policy

## Status

Accepted — 2026-07-20.

## Context

QualityMesh must answer one question reproducibly: may this artifact ship? A defensible
answer has to satisfy four constraints that ad-hoc gating logic cannot.

**Auditability.** A release decision may be questioned months later. Reconstructing it
requires knowing exactly which rules applied at the time. Logic spread across pipeline
scripts and platform settings cannot be replayed, because no single artifact records what
the rules were.

**Determinism.** `PRD.md` NFR-1 requires identical inputs to produce identical decisions.
That is only meaningful if the policy is itself an input rather than ambient state.

**Reviewability.** Release policy is a security control. It should be reviewed, versioned
and diffed like any other security-relevant change. Configuration scattered across
workflow YAML, branch protection rules and evaluation code is not reviewable as a unit.

**Fail-closed enforcement.** Threat B in `PRD.md` section 5 is suppressed or absent
evidence. Detecting absence requires a declaration of what *should* exist. A gate that
only reacts to reported failures cannot see a check that never ran.

A concrete threat shapes the last two. If the policy is an ordinary configuration file,
whoever can change the file can weaken the gate — and the most valuable weakening is to
downgrade a blocking rule to a warning in the same pull request that introduces the
problem. Making policy declarative is not sufficient; some outcomes must not be
expressible as anything else.

## Decision

A single versioned document, the **release contract**, is the source of truth for release
policy. It is loaded and validated through `ReleaseContract`
(`src/qualitymesh/schemas/contract.py`), and no release-affecting rule lives anywhere
else.

The contract carries `apiVersion` and `kind` so its schema can evolve explicitly, and
declares the trust boundary (repository, default branch, OIDC issuer and audience,
trusted workflows with their permitted refs and events, allowed digest algorithms), the
required evidence obligations, and the policy rules.

Four properties are enforced at the schema boundary rather than by convention.

**Unknown fields are rejected** (`extra="forbid"`). A contract is an external trust
boundary. Silently ignoring an unrecognised field means a typo in a security-relevant key
— `requireArtifactAttestion` for `requireArtifactAttestation` — silently disables the
control it was meant to enable. Rejection converts a silent policy hole into a loud
failure.

**Type coercion is rejected** (`strict=True`). Permissive coercion makes a contract's
meaning depend on parser conventions rather than on what is written. The string `"1"` is
not the integer `1`, and `"false"` must never become a boolean that disables a check.
Strictness keeps the document's meaning legible from the document alone.

**Validated contracts are immutable** (`frozen=True`, sequences normalised to tuples). A
policy input that can be mutated after validation is not a trustworthy input: the rules
that were checked would no longer be the rules that get evaluated, and the validation
boundary would prove nothing about the object actually used. Immutability also makes the
evaluator's determinism testable, since the same contract object cannot behave differently
on two runs.

**Fail-closed policy outcomes are pinned by the type system.** `artifactMismatch`,
`untrustedWorkflow`, `invalidAttestation` and `failedCriticalJourney` are
`Literal[Decision.BLOCK]`; `missingRequiredEvidence` is
`Literal[Decision.INSUFFICIENT_EVIDENCE]`; the `exposedSecrets` and
`criticalVulnerabilities` thresholds are validated to carry a `BLOCK` outcome. These are
not defaults a contract author may override — a contract that sets `artifactMismatch: WARN`
does not load at all. Making the safe value the *only* expressible value removes the
weakening path described above; a downgrade now requires changing the schema, in a
reviewed commit, rather than a config line.

Configurable thresholds remain configurable. What is pinned is the outcome of a violation,
not the threshold at which one occurs.

## Consequences

**Accepted costs.**

- Strictness makes contracts brittle to author. A misspelled key fails loudly rather than
  degrading quietly. This is the intended trade: a noisy failure beats a silent hole.
- Pinned outcomes cannot be relaxed for a legitimate edge case without a schema change and
  a superseding ADR. Teams wanting a temporary exception have no in-contract mechanism.
  Waivers are deferred scope (`PRD.md` section 12) precisely because an easy waiver path
  reintroduces the weakening it was designed to prevent.
- Every schema change that alters accepted input is a breaking change requiring an
  `apiVersion` decision.
- Immutability rules out incremental construction. Contracts are validated whole.

**Benefits realised.**

- A decision can be replayed by pairing the recorded contract version with the recorded
  evidence digests.
- Policy changes appear in review as a diff to one file.
- Rejections happen once, at load, with a field location — not as a downstream
  `AttributeError`.
- The evaluator can be a pure function of `(contract, evidence, artifact digest,
  timestamp)`, which is what makes NFR-1 testable.

**Constraints imposed on future work.** Evidence ingestion, completeness evaluation and
policy evaluation must all read their rules from the contract. Introducing a second source
of release policy — an environment variable, a database column, a CLI flag — would
invalidate this ADR and must supersede it rather than quietly coexist.

## Alternatives Considered

**Policy expressed in code.** Rejected: changing policy requires a release of QualityMesh
itself, policy cannot differ per repository without branching logic, and a past decision
cannot be replayed without checking out the version of the code that made it.

**Policy in pipeline configuration (workflow YAML, branch protection).** Rejected: it is
distributed across files and platform settings with no single reviewable artifact, it is
editable in the same pull request as the change being gated, and — decisively — it cannot
express Threat B. A workflow file describes what *does* run; detecting a check that was
removed requires an independent declaration of what *must* run.

**A permissive contract schema that ignores unknown fields.** Rejected: this is the
silent-typo failure mode above. Forward compatibility is better served by explicit
`apiVersion` negotiation, where an older engine refuses a newer contract outright instead
of applying a partially understood policy.

**A policy expression language or rules DSL.** Rejected for V1 and recorded as deferred
scope. A DSL makes rules arbitrarily expressive, which makes determinism harder to
guarantee, review harder to perform, and fail-closed pinning impossible to enforce
structurally — an expression can always be written to evaluate to `WARN`. The fixed rule
set is more restrictive and more defensible.

**Configurable fail-closed outcomes with safe defaults.** Rejected. Defaults protect
against omission, not against deliberate weakening, and the weakening case is the one that
matters. A `BLOCK` that can be set to `WARN` is not a fail-closed control.

## Replacement or Review Trigger

Revisit this decision when any of the following becomes observable:

1. A legitimate operational need to weaken a pinned outcome recurs across more than one
   team, indicating the pinned set is wrong rather than the request being wrong.
2. A second CI provider is supported and the GitHub-shaped trust boundary no longer fits
   as a single document.
3. Contract authoring errors become a recurring source of failed loads, indicating the
   strictness cost has outgrown its benefit and tooling — validation, templates, editor
   schema — should absorb it instead.
4. Any requirement emerges for policy that a fixed rule set cannot express, forcing a
   reconsideration of the rejected DSL alternative.

A change to the pinned outcomes, the strictness settings, or the single-source-of-truth
property requires a new ADR superseding this one. This file is not to be edited in place.

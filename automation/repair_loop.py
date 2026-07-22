"""Bounded implementation-and-validation loop using a `CodingProvider`
(QM-AUTO-011).

    Build initial provider request (IMPLEMENT)
    -> Provider performs implementation
    -> Run validation attempt (caller-supplied callback)
    -> If validation passes: stop successfully
    -> If validation fails: build minimal repair request (REPAIR)
    -> Provider repairs
    -> Validate again
    -> Stop on success or a bounded failure condition

Completion is decided by `execute_attempt`'s returned `ValidationRunResult`
alone — a provider's own claim of success (`ProviderResponse.success`,
`ProviderResponse.text`) is never consulted to decide `COMPLETED`. This
module never touches a worktree, a cache, or the filesystem — `execute_attempt`
is the caller's own attempt lifecycle (e.g. `automation.attempt_runner
.run_attempt`), injected so this loop stays a pure orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from automation.provider import CodingProvider, ProviderError
from automation.schemas.provider import (
    ContextAttachment,
    ProviderOperationMode,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from automation.schemas.repair import (
    ContextExpansionRequest,
    RepairAttempt,
    RepairContextExpansion,
    RepairLoopResult,
    RepairOutcome,
)
from automation.schemas.task import AutonomousTask
from automation.schemas.validation_run import (
    AggregateOutcome,
    ValidationEntryStatus,
    ValidationRunResult,
)

_CONTEXT_REQUEST_PREFIX = "CONTEXT_REQUEST:"
"""A provider signals it wants one more file by making the first line of its
response exactly `CONTEXT_REQUEST: <path> | <reason>`. This is this module's
own, self-contained convention — no real provider is integrated yet (see
`automation.provider`), so there is no external protocol to match; refine
this when one is."""


def _parse_context_request(text: str) -> tuple[str, str] | None:
    first_line = text.splitlines()[0] if text else ""
    if not first_line.startswith(_CONTEXT_REQUEST_PREFIX):
        return None
    remainder = first_line.removeprefix(_CONTEXT_REQUEST_PREFIX).strip()
    if "|" not in remainder:
        return None
    path_part, _, reason_part = remainder.partition("|")
    path = path_part.strip()
    reason = reason_part.strip()
    if not path or not reason:
        return None
    return path, reason


def _path_matches(path: str, entry: str) -> bool:
    if path == entry:
        return True
    prefix = entry if entry.endswith("/") else entry + "/"
    return path.startswith(prefix)


def _check_policy_violation(
    changed_files: Sequence[str], allowed_paths: Sequence[str], forbidden_paths: Sequence[str]
) -> str | None:
    for path in changed_files:
        if any(_path_matches(path, forbidden) for forbidden in forbidden_paths):
            return f"{path!r} matches a forbidden path"
        if not any(_path_matches(path, allowed) for allowed in allowed_paths):
            return f"{path!r} is outside allowed_paths"
    return None


def _normalize_failure(result: ValidationRunResult) -> str:
    """A deterministic string identifying which commands failed and how —
    used only to detect an identical failure repeating across attempts, not
    stored as evidence itself."""
    parts: list[str] = []
    for entry in result.results:
        if entry.status in (ValidationEntryStatus.FAILED, ValidationEntryStatus.TIMED_OUT):
            exit_code = entry.command_result.exit_code if entry.command_result else None
            command = " ".join((entry.request.executable, *entry.request.arguments))
            parts.append(f"{command}::{entry.status.value}::{exit_code}")
    return "|".join(parts)


def _build_implement_request(
    task: AutonomousTask,
    context: tuple[ContextAttachment, ...],
    attempt_number: int,
    max_output_tokens: int,
) -> ProviderRequest:
    return ProviderRequest(
        task_id=task.id,
        objective=task.objective,
        acceptance_criteria=task.acceptance_criteria,
        allowed_paths=task.allowed_paths,
        forbidden_paths=task.forbidden_paths,
        context=context,
        attempt_number=attempt_number,
        max_output_tokens=max_output_tokens,
        mode=ProviderOperationMode.IMPLEMENT,
    )


def _build_repair_request(
    task: AutonomousTask,
    attempt_number: int,
    max_output_tokens: int,
    latest_failure_summary: str | None,
    diff_context: str | None,
    new_expansions: tuple[ContextAttachment, ...],
) -> ProviderRequest:
    """A minimal repair payload — task ID, acceptance criteria, the latest
    validation failure, optional changed-file context, and only newly
    approved expansions. Deliberately never resends the original
    `IMPLEMENT` attempt's full context bundle."""
    repair_context: list[ContextAttachment] = []
    if latest_failure_summary:
        repair_context.append(
            ContextAttachment(path="VALIDATION_FAILURES", content=latest_failure_summary)
        )
    if diff_context:
        repair_context.append(ContextAttachment(path="CHANGED_FILES_DIFF", content=diff_context))
    repair_context.extend(new_expansions)

    return ProviderRequest(
        task_id=task.id,
        objective=task.objective,
        acceptance_criteria=task.acceptance_criteria,
        allowed_paths=task.allowed_paths,
        forbidden_paths=task.forbidden_paths,
        context=tuple(repair_context),
        attempt_number=attempt_number,
        max_output_tokens=max_output_tokens,
        mode=ProviderOperationMode.REPAIR,
    )


def run_repair_loop(
    task: AutonomousTask,
    context: tuple[ContextAttachment, ...],
    provider: CodingProvider,
    execute_attempt: Callable[[ProviderResponse, int], ValidationRunResult],
    *,
    token_budget: int,
    max_output_tokens: int = 4096,
    diff_context: str | None = None,
    context_expansion_policy: Callable[[ContextExpansionRequest], ContextAttachment | None]
    | None = None,
    max_context_expansions: int = 3,
) -> RepairLoopResult:
    """Run a bounded implement-then-repair loop for `task`. Never mutates
    `task` or `context`; never commits, pushes, opens a PR, changes
    `task.status`, or deletes a worktree.

    Attempt 1 uses `IMPLEMENT` mode with the full `context` given. Every
    later attempt uses `REPAIR` mode with a minimal payload (see
    `_build_repair_request`) — up to `task.limits.max_attempts` attempts
    total. Stops as soon as `execute_attempt`'s `ValidationRunResult`
    aggregates to `PASSED` (`COMPLETED`) — never on `ProviderResponse
    .success` or `.text` alone. Also stops when attempts are exhausted, the
    same normalized failure occurs on two consecutive attempts, the combined
    provider token usage reaches `token_budget` before another call would be
    made, a `ProviderResponse.changed_files` entry falls outside
    `task.allowed_paths` or matches `task.forbidden_paths`, `provider
    .execute` raises `ProviderError`, or `execute_attempt` itself raises.

    A provider may ask for one more context file by starting its response
    text with `CONTEXT_REQUEST: <path> | <reason>` (see
    `_parse_context_request`); `context_expansion_policy`, if given, decides
    whether to grant it and supplies the content — this function never reads
    a file itself. At most `max_context_expansions` such rounds are honored
    across the whole loop; a round never consumes an attempt.
    """
    max_attempts = task.limits.max_attempts
    attempts: list[RepairAttempt] = []
    expansions: list[RepairContextExpansion] = []
    pending_new_context: list[ContextAttachment] = []
    total_input_tokens = 0
    total_output_tokens = 0
    expansion_rounds_used = 0
    latest_failure_summary: str | None = None
    previous_normalized_failure: str | None = None

    def finish(outcome: RepairOutcome, stop_reason: str) -> RepairLoopResult:
        return RepairLoopResult(
            task_id=task.id,
            outcome=outcome,
            stop_reason=stop_reason,
            attempts=tuple(attempts),
            context_expansions=tuple(expansions),
            total_usage=ProviderUsage(
                input_tokens=total_input_tokens, output_tokens=total_output_tokens
            ),
        )

    attempt_number = 1
    while attempt_number <= max_attempts:
        if total_input_tokens + total_output_tokens >= token_budget:
            return finish(
                RepairOutcome.BUDGET_EXCEEDED,
                f"token budget {token_budget} reached before attempt {attempt_number}",
            )

        if attempt_number == 1:
            request = _build_implement_request(task, context, attempt_number, max_output_tokens)
        else:
            request = _build_repair_request(
                task,
                attempt_number,
                max_output_tokens,
                latest_failure_summary,
                diff_context,
                tuple(pending_new_context),
            )
            pending_new_context = []

        try:
            response = provider.execute(request)
        except ProviderError as exc:
            return finish(RepairOutcome.PROVIDER_ERROR, f"provider error: {exc}")

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        violation = _check_policy_violation(
            response.changed_files, task.allowed_paths, task.forbidden_paths
        )
        if violation is not None:
            return finish(RepairOutcome.POLICY_VIOLATION, violation)

        expansion = _parse_context_request(response.text)
        if expansion is not None and expansion_rounds_used < max_context_expansions:
            expansion_rounds_used += 1
            path, reason = expansion
            expansion_request = ContextExpansionRequest(
                requested_path=path, reason=reason, triggering_failure=latest_failure_summary or ""
            )
            attachment = (
                context_expansion_policy(expansion_request) if context_expansion_policy else None
            )
            expansions.append(
                RepairContextExpansion(request=expansion_request, approved=attachment is not None)
            )
            if attachment is not None:
                pending_new_context.append(attachment)
            continue

        try:
            validation_result = execute_attempt(response, attempt_number)
        except Exception as exc:
            # `execute_attempt` is a caller-supplied lifecycle callback with no
            # fixed exception contract; this loop must fail closed regardless
            # of what it raises, so the catch is deliberately broad.
            return finish(RepairOutcome.EXECUTION_ERROR, f"attempt execution failed: {exc}")

        attempts.append(
            RepairAttempt(
                attempt_number=attempt_number,
                mode=request.mode,
                response=response,
                validation_result=validation_result,
            )
        )

        if validation_result.outcome is AggregateOutcome.PASSED:
            return finish(RepairOutcome.COMPLETED, "validation passed")

        normalized_failure = _normalize_failure(validation_result)
        if normalized_failure and normalized_failure == previous_normalized_failure:
            return finish(
                RepairOutcome.REPEATED_FAILURE, f"same failure repeated: {normalized_failure}"
            )
        previous_normalized_failure = normalized_failure
        latest_failure_summary = normalized_failure or "validation failed"

        attempt_number += 1

    return finish(RepairOutcome.ATTEMPTS_EXHAUSTED, f"exhausted {max_attempts} attempts")

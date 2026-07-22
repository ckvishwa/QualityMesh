"""Sequential execution of a prebuilt `ValidationPlan` (QM-AUTO-008).

Runs each `ValidationPlanEntry.request` through
`automation.command_runner.run_command`, once, in declared order, and returns
one `ValidationEntryResult` per planned entry regardless of whether it ran. A
nonzero exit or a command timeout is an ordinary typed result — only a genuine
`CommandRunnerError` (a policy violation, a missing executable, a launch
failure — none of which should ever happen against an already-planned,
already-validated request) is wrapped and raised, since that is not a normal
validation outcome.
"""

from __future__ import annotations

import time

from automation.command_runner import CommandRunnerError, run_command
from automation.schemas.validation import ValidationPlan
from automation.schemas.validation_run import (
    AggregateOutcome,
    ValidationEntryResult,
    ValidationEntryStatus,
    ValidationExecutionPolicy,
    ValidationRunResult,
)


class ValidationExecutionError(Exception):
    """Raised when running a planned command fails for a reason that isn't an
    ordinary validation outcome — a `CommandRunnerError` from
    `automation.command_runner.run_command` (policy violation, missing
    executable, or launch failure). A nonzero exit or a command timeout never
    raises this; see `ValidationEntryStatus`."""


def _classify(command_result_timed_out: bool, exit_code: int | None) -> ValidationEntryStatus:
    if command_result_timed_out:
        return ValidationEntryStatus.TIMED_OUT
    if exit_code == 0:
        return ValidationEntryStatus.PASSED
    return ValidationEntryStatus.FAILED


def _aggregate_outcome(
    results: tuple[ValidationEntryResult, ...], *, deadline_exceeded: bool
) -> AggregateOutcome:
    statuses = {result.status for result in results}
    if deadline_exceeded or ValidationEntryStatus.TIMED_OUT in statuses:
        return AggregateOutcome.TIMED_OUT
    if ValidationEntryStatus.FAILED in statuses:
        return AggregateOutcome.FAILED
    return AggregateOutcome.PASSED


def execute_validation_plan(
    plan: ValidationPlan,
    execution_policy: ValidationExecutionPolicy,
) -> ValidationRunResult:
    """Run every entry in `plan.entries`, once, in order. Never mutates `plan`
    or `execution_policy`.

    By default, stops at the first `FAILED` or `TIMED_OUT` entry and marks
    every later entry `SKIPPED`; pass `execution_policy.continue_on_failure`
    to run all entries regardless. No command is started once
    `execution_policy.total_deadline_seconds` has elapsed since the run
    began — any entry that would have started after that is also `SKIPPED`,
    and the run's aggregate outcome becomes `TIMED_OUT` even if nothing that
    actually ran did.

    Raises:
        ValidationExecutionError: `run_command` raised `CommandRunnerError`
            for some entry — a genuine runner failure, not an ordinary
            nonzero exit or command timeout.
    """
    started_at = time.monotonic()
    results: list[ValidationEntryResult] = []
    stopped = False
    deadline_exceeded = False

    for index, entry in enumerate(plan.entries):
        if (
            not stopped
            and (time.monotonic() - started_at) >= execution_policy.total_deadline_seconds
        ):
            stopped = True
            deadline_exceeded = True

        if stopped:
            results.append(
                ValidationEntryResult(
                    index=index, request=entry.request, status=ValidationEntryStatus.SKIPPED
                )
            )
            continue

        try:
            command_result = run_command(plan.policy, entry.request)
        except CommandRunnerError as exc:
            raise ValidationExecutionError(
                f"entry {index} ({entry.request.executable!r}): {exc}"
            ) from exc

        status = _classify(command_result.timed_out, command_result.exit_code)
        results.append(
            ValidationEntryResult(
                index=index, request=entry.request, status=status, command_result=command_result
            )
        )

        if status is not ValidationEntryStatus.PASSED and not execution_policy.continue_on_failure:
            stopped = True

    duration = time.monotonic() - started_at
    executed_count = sum(
        1 for result in results if result.status is not ValidationEntryStatus.SKIPPED
    )
    skipped_count = len(results) - executed_count

    return ValidationRunResult(
        task_id=plan.task_id,
        outcome=_aggregate_outcome(tuple(results), deadline_exceeded=deadline_exceeded),
        results=tuple(results),
        executed_count=executed_count,
        skipped_count=skipped_count,
        duration_seconds=duration,
    )

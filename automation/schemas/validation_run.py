"""Typed models for sequential validation-plan execution (QM-AUTO-008).

`automation.validation_planner` builds a `ValidationPlan` without running
anything. This module defines the vocabulary for actually running it, in
order, once: a per-entry outcome, the policy governing how far to go on
failure and how long the whole run may take, and the aggregate result.
`automation.validation_executor` is the only place that calls
`automation.command_runner.run_command`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from automation.schemas.command import CommandRequest, CommandResult


class StrictValidationRunModel(BaseModel):
    """Base configuration for all validation-run models.

    Mirrors `automation.schemas.task.StrictTaskModel` and its siblings — a local
    base per this package's existing per-boundary convention, not a shared one.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class ValidationEntryStatus(StrEnum):
    """The outcome of one planned command, once the run reaches it."""

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed-out"
    SKIPPED = "skipped"


class AggregateOutcome(StrEnum):
    """The overall result of one validation run.

    Distinct from `ValidationEntryStatus`: an aggregate summarizes every
    entry, not one. `EXECUTION_ERROR` is reserved for a genuine runner
    failure (see `automation.validation_executor.ValidationExecutionError`)
    — never for an ordinary nonzero exit or timeout, which are `FAILED`/
    `TIMED_OUT` results, not executor errors.
    """

    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed-out"
    EXECUTION_ERROR = "execution-error"


class ValidationExecutionPolicy(StrictValidationRunModel):
    """How far a validation run goes once something fails, and how long the
    whole run — not just one command — may take."""

    continue_on_failure: bool = False
    total_deadline_seconds: Annotated[float, Field(strict=True, gt=0)] = 1800.0


class ValidationEntryResult(StrictValidationRunModel):
    """What happened to one planned command.

    `index` is its position in `ValidationPlan.entries` (declaration order),
    preserved regardless of whether the entry ran, and independent of how
    many entries came before it were skipped. `command_result` is `None`
    exactly when `status` is `SKIPPED` — a skipped entry was never run.
    """

    index: Annotated[int, Field(strict=True, ge=0)]
    request: CommandRequest
    status: ValidationEntryStatus
    command_result: CommandResult | None = None


class ValidationRunResult(StrictValidationRunModel):
    """The full, ordered result of executing one `ValidationPlan` once.

    `results` always has exactly one entry per `ValidationPlan.entries`, in
    the same order, whether or not the run stopped early.
    """

    task_id: Annotated[str, Field(min_length=1)]
    outcome: AggregateOutcome
    results: tuple[ValidationEntryResult, ...]
    executed_count: Annotated[int, Field(strict=True, ge=0)]
    skipped_count: Annotated[int, Field(strict=True, ge=0)]
    duration_seconds: Annotated[float, Field(ge=0)]

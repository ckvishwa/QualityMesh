"""Typed models for one single-attempt lifecycle run (QM-AUTO-009).

An attempt composes worktree creation, context building, cache persistence,
validation planning and validation execution into one deterministic sequence
for one task, with no coding-model call anywhere in it. This module defines
the result vocabulary only; `automation.attempt_runner` is the only place that
actually runs the sequence.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from automation.schemas.context import ContextBuildResult
from automation.schemas.validation_run import ValidationRunResult


class StrictAttemptModel(BaseModel):
    """Base configuration for all attempt models.

    Mirrors `automation.schemas.task.StrictTaskModel` and its siblings — a
    local base per this package's existing per-boundary convention, not a
    shared one.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class AttemptOutcome(StrEnum):
    """The final result of one attempt.

    `VALIDATION_FAILED` covers an aggregate `FAILED` validation run — a
    normal outcome, not an error. `TIMED_OUT` covers an aggregate
    `TIMED_OUT` validation run. `SETUP_ERROR` covers any failure before
    validation ever starts running (worktree creation, cache load, context
    build, cache persistence, or plan building). `EXECUTION_ERROR` covers a
    genuine `automation.validation_executor.ValidationExecutionError` — the
    plan itself couldn't be run, distinct from any command it contained
    failing or timing out.
    """

    PASSED = "passed"
    VALIDATION_FAILED = "validation-failed"
    TIMED_OUT = "timed-out"
    SETUP_ERROR = "setup-error"
    EXECUTION_ERROR = "execution-error"


NonEmptyStr = Annotated[str, Field(min_length=1)]


class AttemptResult(StrictAttemptModel):
    """The full, immutable record of one attempt.

    `branch` and `worktree_path` are the deterministic values `task.id`
    implies — computed before worktree creation is even attempted, so they
    are always present, even when `outcome` is `SETUP_ERROR` because
    creation itself failed. `context_result` is `None` until context
    building completes; `validation_result` is `None` until validation
    execution completes (both stay `None` for any earlier `SETUP_ERROR`, and
    `validation_result` also stays `None` for `EXECUTION_ERROR`, since a
    `ValidationExecutionError` means no `ValidationRunResult` was ever
    produced).
    """

    task_id: NonEmptyStr
    base_commit: NonEmptyStr
    branch: NonEmptyStr
    worktree_path: NonEmptyStr
    context_result: ContextBuildResult | None = None
    validation_result: ValidationRunResult | None = None
    outcome: AttemptOutcome
    error_message: str | None = None
    started_at: datetime
    ended_at: datetime
    duration_seconds: Annotated[float, Field(ge=0)]

    @field_validator("started_at", "ended_at")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware, not naive")
        return value

"""Typed models for the bounded agent repair loop (QM-AUTO-011).

The loop calls a `CodingProvider` at most `task.limits.max_attempts` times,
running a caller-supplied attempt-execution callback (validation) after each
call, and stops the moment deterministic validation passes — never on a
provider's own claim of success. This module defines the result vocabulary
and the context-expansion request/decision types; `automation.repair_loop` is
the only place that actually runs the loop.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from automation.schemas.provider import ProviderOperationMode, ProviderResponse, ProviderUsage
from automation.schemas.validation_run import ValidationRunResult


class StrictRepairModel(BaseModel):
    """Base configuration for all repair-loop models.

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


class RepairOutcome(StrEnum):
    """Why the loop stopped."""

    COMPLETED = "completed"
    ATTEMPTS_EXHAUSTED = "attempts-exhausted"
    REPEATED_FAILURE = "repeated-failure"
    BUDGET_EXCEEDED = "budget-exceeded"
    PROVIDER_ERROR = "provider-error"
    POLICY_VIOLATION = "policy-violation"
    EXECUTION_ERROR = "execution-error"


NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]


class ContextExpansionRequest(StrictRepairModel):
    """A provider's request for one additional repository file.

    Never grants itself anything — `automation.repair_loop` never reads a
    file to satisfy this on its own; a caller-supplied policy callback
    decides, and supplies the content if it approves.
    """

    requested_path: NonEmptyStr
    reason: NonEmptyStr
    triggering_failure: str = ""


class RepairContextExpansion(StrictRepairModel):
    """The outcome of one `ContextExpansionRequest`: whether the
    caller-supplied policy callback approved it."""

    request: ContextExpansionRequest
    approved: bool


class RepairAttempt(StrictRepairModel):
    """One real implementation/repair attempt: the provider's response, and
    the deterministic validation result it earned. Never includes an
    expansion-negotiation round — those are recorded separately in
    `RepairLoopResult.context_expansions`, since they are not implementation
    attempts."""

    attempt_number: PositiveInt
    mode: ProviderOperationMode
    response: ProviderResponse
    validation_result: ValidationRunResult


class RepairLoopResult(StrictRepairModel):
    """The full, immutable record of one bounded repair loop run.

    `outcome` is `COMPLETED` only when some attempt's `validation_result`
    itself aggregated to `PASSED` — never because a provider claimed
    success. `total_usage` sums every provider call this run made,
    including expansion-negotiation rounds.
    """

    task_id: NonEmptyStr
    outcome: RepairOutcome
    stop_reason: NonEmptyStr
    attempts: tuple[RepairAttempt, ...]
    context_expansions: tuple[RepairContextExpansion, ...]
    total_usage: ProviderUsage

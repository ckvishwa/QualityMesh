"""Typed models for deterministic validation-command planning (QM-AUTO-007).

Turns each of `AutonomousTask.validation_commands`' raw strings into a
`ParsedValidationCommand` (executable plus argument tuple, shell-free) and a
policy-validated `CommandRequest`, without ever invoking a shell or running
anything. This module defines the vocabulary only; `automation.validation_planner`
is the only place that parses a raw command string or checks it against a policy.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from automation.schemas.command import CommandPolicy, CommandRequest


class StrictValidationModel(BaseModel):
    """Base configuration for all validation-planning models.

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


class ValidationErrorCode(StrEnum):
    """Why `automation.validation_planner.build_validation_plan` rejected a
    command, or refused to build a plan at all."""

    EMPTY_COMMAND = "empty-command"
    UNMATCHED_QUOTES = "unmatched-quotes"
    UNSAFE_SHELL_SYNTAX = "unsafe-shell-syntax"
    ENVIRONMENT_ASSIGNMENT = "environment-assignment"
    VARIABLE_EXPANSION = "variable-expansion"
    EXECUTABLE_PATH_NOT_ALLOWED = "executable-path-not-allowed"
    DUPLICATE_COMMAND = "duplicate-command"
    EXECUTABLE_NOT_ALLOWLISTED = "executable-not-allowlisted"
    GIT_COMMAND_NOT_PERMITTED = "git-command-not-permitted"
    WORKING_DIRECTORY_NOT_FOUND = "working-directory-not-found"
    WORKING_DIRECTORY_NOT_A_DIRECTORY = "working-directory-not-a-directory"
    WORKING_DIRECTORY_OUTSIDE_ROOT = "working-directory-outside-root"


NonEmptyStr = Annotated[str, Field(min_length=1)]


class ParsedValidationCommand(StrictValidationModel):
    """One `validation_commands` string, tokenized shell-free.

    `normalized` is the deterministic canonical form used for duplicate
    detection — it collapses incidental whitespace/quoting differences in
    `raw` (e.g. extra spaces) that `shlex.split` already treats as
    equivalent, so two commands that would run identically are always
    recognized as duplicates regardless of how they were spelled.
    """

    raw: NonEmptyStr
    executable: NonEmptyStr
    arguments: tuple[str, ...]

    @computed_field
    @property
    def normalized(self) -> str:
        return " ".join((self.executable, *self.arguments))


class ValidationPlanEntry(StrictValidationModel):
    """One planned command: its parse result and the `CommandRequest` it
    produced, already validated against a `CommandPolicy`."""

    parsed: ParsedValidationCommand
    request: CommandRequest


class ValidationPlan(StrictValidationModel):
    """The full, ordered, deterministic execution plan for one task's
    `validation_commands`.

    `entries` preserves `validation_commands`' declared order exactly.
    `policy` is the exact `CommandPolicy` every entry was validated against —
    kept alongside the plan so its timeout/output-limit/environment
    configuration is traceable without needing to duplicate those fields onto
    every entry (`CommandRequest` itself carries no such fields; they apply
    uniformly at execution time, not per request).
    """

    task_id: NonEmptyStr
    working_directory: NonEmptyStr
    policy: CommandPolicy
    entries: tuple[ValidationPlanEntry, ...]

    @model_validator(mode="after")
    def validate_plan_invariants(self) -> Self:
        if not self.entries:
            raise ValueError("entries must contain at least one validation command")
        return self

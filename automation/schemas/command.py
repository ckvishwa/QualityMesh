"""Typed models for safe validation-command execution (QM-AUTO-006).

A command request names an executable and its arguments as separate fields —
never raw shell text — plus the working directory to run in. A command policy is
the operator-controlled safety envelope around every request: which executables
are approved, how long a command may run, how much output it may produce, and
which environment variables its child process may see. This module defines the
vocabulary only; `automation.command_runner` is the only place that resolves an
executable or spawns a process.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, computed_field, model_validator


def _array_to_tuple(value: object) -> object:
    """Accept a JSON/Python array and freeze it as a tuple before strict validation."""

    if isinstance(value, list):
        return tuple(cast(list[object], value))
    if isinstance(value, tuple):
        return cast(tuple[object, ...], value)
    # ValueError, not TypeError: Pydantic only converts ValueError and AssertionError
    # into ValidationError. A TypeError would escape model_validate uncaught.
    raise ValueError("expected an array")


class StrictCommandModel(BaseModel):
    """Base configuration for all command models.

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


class CommandErrorCode(StrEnum):
    """Why `automation.command_runner.run_command` refused to run a request."""

    EXECUTABLE_NOT_ALLOWLISTED = "executable-not-allowlisted"
    EXECUTABLE_NOT_FOUND = "executable-not-found"
    UNSAFE_ARGUMENT = "unsafe-argument"
    GIT_COMMAND_NOT_PERMITTED = "git-command-not-permitted"
    WORKING_DIRECTORY_NOT_FOUND = "working-directory-not-found"
    WORKING_DIRECTORY_NOT_A_DIRECTORY = "working-directory-not-a-directory"
    WORKING_DIRECTORY_OUTSIDE_ROOT = "working-directory-outside-root"
    LAUNCH_FAILED = "launch-failed"


class CommandOutcome(StrEnum):
    """How a launched command ended. Distinct from `exit_code`: a timed-out
    command has no exit code at all — the process was killed, not finished."""

    COMPLETED = "completed"
    TIMED_OUT = "timed-out"


NonEmptyStr = Annotated[str, Field(min_length=1)]
StrTuple = Annotated[tuple[str, ...], BeforeValidator(_array_to_tuple)]

DEFAULT_ALLOWED_EXECUTABLES: tuple[str, ...] = ("uv", "pytest", "ruff", "pyright", "git")
DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
)


class CommandPolicy(StrictCommandModel):
    """The operator-controlled safety envelope every `CommandRequest` runs
    under: an approved worktree root, an executable allowlist, resource
    bounds, and a minimal environment-variable allowlist."""

    worktree_root: NonEmptyStr
    allowed_executables: Annotated[tuple[NonEmptyStr, ...], BeforeValidator(_array_to_tuple)] = (
        DEFAULT_ALLOWED_EXECUTABLES
    )
    timeout_seconds: Annotated[float, Field(strict=True, gt=0)] = 300.0
    max_output_bytes: Annotated[int, Field(strict=True, gt=0)] = 2_000_000
    env_allowlist: Annotated[tuple[NonEmptyStr, ...], BeforeValidator(_array_to_tuple)] = (
        DEFAULT_ENV_ALLOWLIST
    )

    @model_validator(mode="after")
    def validate_policy_invariants(self) -> Self:
        if not self.allowed_executables:
            raise ValueError("allowed_executables must contain at least one executable")
        if len(set(self.allowed_executables)) != len(self.allowed_executables):
            raise ValueError("allowed_executables must not contain duplicates")
        if len(set(self.env_allowlist)) != len(self.env_allowlist):
            raise ValueError("env_allowlist must not contain duplicates")
        return self


class CommandRequest(StrictCommandModel):
    """One command to run: an executable name (looked up against a
    `CommandPolicy`, never a raw path the caller controls), its arguments as
    separate values, and the working directory to run it in."""

    executable: NonEmptyStr
    arguments: StrTuple = Field(default_factory=tuple)
    working_directory: NonEmptyStr


class CommandResult(StrictCommandModel):
    """What happened when a `CommandRequest` ran.

    `exit_code` is `None` exactly when `outcome` is `TIMED_OUT` — a killed
    process never produces one. `stdout`/`stderr` hold whatever was captured
    up to `CommandPolicy.max_output_bytes` (or up to the timeout, for a timed
    out command); `stdout_truncated`/`stderr_truncated` say whether more
    output existed than was kept.
    """

    executable: NonEmptyStr
    arguments: tuple[str, ...]
    working_directory: NonEmptyStr
    outcome: CommandOutcome
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: Annotated[float, Field(ge=0)]
    stdout_truncated: bool
    stderr_truncated: bool

    @computed_field
    @property
    def timed_out(self) -> bool:
        return self.outcome is CommandOutcome.TIMED_OUT

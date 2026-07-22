"""Deterministic, shell-free validation-command planning (QM-AUTO-007).

Turns each of `AutonomousTask.validation_commands`' raw strings into a policy
-validated `CommandRequest`, targeting a caller-supplied worktree directory —
without invoking a shell, without resolving or running anything. Parsing uses
`shlex.split`, which is itself shell-free: it tokenizes quoting the way a shell
would, but never interprets an operator, expands a variable, or executes
anything. `automation.command_runner` is the only module that actually runs a
`CommandRequest`; this module only ever builds one.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from automation.schemas.command import CommandPolicy, CommandRequest
from automation.schemas.task import AutonomousTask
from automation.schemas.validation import (
    ParsedValidationCommand,
    ValidationErrorCode,
    ValidationPlan,
    ValidationPlanEntry,
)

_SHELL_METACHARACTERS = (";", "|", "&", ">", "<", "`", "\n", "\r")
"""Mirrors `automation.command_runner._SHELL_METACHARACTERS`. Duplicated rather
than imported: this package's schemas already establish the convention of one
local copy per boundary rather than a shared/imported implementation detail
(see e.g. `StrictTaskModel`'s docstring) — the same reasoning applies to a
sibling *module's* private helpers, not just its models."""

_ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_GIT_ALWAYS_BLOCKED_SUBCOMMANDS = frozenset(
    {"commit", "push", "merge", "rebase", "reset", "clean", "checkout", "switch"}
)
_GIT_READONLY_SUBCOMMANDS = frozenset({"status", "diff", "log", "rev-parse", "show"})
_GIT_BRANCH_DELETE_FLAGS = frozenset({"-d", "-D", "--delete"})
_GIT_WORKTREE_SAFE_SUBACTION = "list"
_GIT_UNSAFE_EXACT_ARGS = frozenset({"-c", "--exec", "--upload-pack", "--receive-pack"})
_GIT_UNSAFE_ARG_PREFIXES = ("--exec=", "--upload-pack=", "--receive-pack=")


class ValidationPlanError(Exception):
    """Raised when a `validation_commands` entry cannot be safely parsed, or a
    parsed command violates a `CommandPolicy`.

    Carries a `ValidationErrorCode` so callers can branch on the failure kind
    without parsing the message.
    """

    def __init__(self, message: str, *, code: ValidationErrorCode) -> None:
        super().__init__(message)
        self.code = code


def _parse_command(raw: str) -> ParsedValidationCommand:
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise ValidationPlanError(
            f"{raw!r}: {exc}", code=ValidationErrorCode.UNMATCHED_QUOTES
        ) from exc

    if not tokens or not tokens[0]:
        raise ValidationPlanError(
            f"{raw!r}: command is empty", code=ValidationErrorCode.EMPTY_COMMAND
        )

    for token in tokens:
        if any(char in token for char in _SHELL_METACHARACTERS):
            raise ValidationPlanError(
                f"{raw!r}: argument {token!r} contains a shell metacharacter",
                code=ValidationErrorCode.UNSAFE_SHELL_SYNTAX,
            )
        if "$" in token:
            raise ValidationPlanError(
                f"{raw!r}: argument {token!r} contains variable-expansion syntax",
                code=ValidationErrorCode.VARIABLE_EXPANSION,
            )

    if _ENV_ASSIGNMENT_PATTERN.match(tokens[0]):
        raise ValidationPlanError(
            f"{raw!r}: leading environment assignment is not permitted",
            code=ValidationErrorCode.ENVIRONMENT_ASSIGNMENT,
        )

    executable = tokens[0]
    if "/" in executable or "\\" in executable:
        raise ValidationPlanError(
            f"{raw!r}: executable must be an approved name, not a path",
            code=ValidationErrorCode.EXECUTABLE_PATH_NOT_ALLOWED,
        )

    return ParsedValidationCommand(raw=raw, executable=executable, arguments=tuple(tokens[1:]))


def _validate_working_directory(working_directory: str, policy: CommandPolicy) -> Path:
    path = Path(working_directory)
    if not path.exists():
        raise ValidationPlanError(
            f"{path}: working directory does not exist",
            code=ValidationErrorCode.WORKING_DIRECTORY_NOT_FOUND,
        )
    if not path.is_dir():
        raise ValidationPlanError(
            f"{path}: working directory is not a directory",
            code=ValidationErrorCode.WORKING_DIRECTORY_NOT_A_DIRECTORY,
        )

    resolved_root = Path(policy.worktree_root).resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValidationPlanError(
            f"{resolved_path}: outside the approved worktree root {resolved_root}",
            code=ValidationErrorCode.WORKING_DIRECTORY_OUTSIDE_ROOT,
        )
    return resolved_path


def _validate_executable_allowlisted(executable: str, policy: CommandPolicy) -> None:
    if executable not in policy.allowed_executables:
        raise ValidationPlanError(
            f"{executable!r} is not an approved executable",
            code=ValidationErrorCode.EXECUTABLE_NOT_ALLOWLISTED,
        )


def _validate_git_invocation(arguments: Sequence[str]) -> None:
    if any(
        argument in _GIT_UNSAFE_EXACT_ARGS or argument.startswith(_GIT_UNSAFE_ARG_PREFIXES)
        for argument in arguments
    ):
        raise ValidationPlanError(
            "git invocation uses a config/exec override argument, which is not permitted",
            code=ValidationErrorCode.GIT_COMMAND_NOT_PERMITTED,
        )

    if not arguments:
        raise ValidationPlanError(
            "git requires a subcommand", code=ValidationErrorCode.GIT_COMMAND_NOT_PERMITTED
        )

    subcommand = arguments[0]
    if subcommand in _GIT_ALWAYS_BLOCKED_SUBCOMMANDS:
        raise ValidationPlanError(
            f"git {subcommand}: destructive command is not permitted",
            code=ValidationErrorCode.GIT_COMMAND_NOT_PERMITTED,
        )
    if subcommand == "branch":
        if any(arg in _GIT_BRANCH_DELETE_FLAGS for arg in arguments[1:]):
            raise ValidationPlanError(
                "git branch: deletion is not permitted",
                code=ValidationErrorCode.GIT_COMMAND_NOT_PERMITTED,
            )
        return
    if subcommand == "worktree":
        subaction = arguments[1] if len(arguments) > 1 else None
        if subaction != _GIT_WORKTREE_SAFE_SUBACTION:
            raise ValidationPlanError(
                f"git worktree {subaction}: only 'list' is permitted",
                code=ValidationErrorCode.GIT_COMMAND_NOT_PERMITTED,
            )
        return
    if subcommand in _GIT_READONLY_SUBCOMMANDS:
        return

    raise ValidationPlanError(
        f"git {subcommand}: not an approved git subcommand",
        code=ValidationErrorCode.GIT_COMMAND_NOT_PERMITTED,
    )


def build_validation_plan(
    task: AutonomousTask,
    policy: CommandPolicy,
    working_directory: str,
) -> ValidationPlan:
    """Build a deterministic execution plan for `task.validation_commands`.

    Reads only `task.validation_commands` — nothing else on `task` — and never
    mutates `task` or `policy`. Entries preserve `validation_commands`'
    declared order. Every command is parsed shell-free and checked against
    `policy` without being run.

    Raises:
        ValidationPlanError: `working_directory` is missing, not a directory,
            or outside `policy.worktree_root`; a command is empty, uses
            unmatched/ambiguous quoting, contains a shell metacharacter or
            variable-expansion syntax, starts with an environment assignment,
            names an executable path instead of a bare name, duplicates
            another command's normalized form, isn't on `policy`'s
            executable allowlist, or (for `git`) names a blocked or
            unrecognized subcommand.
    """
    resolved_working_directory = _validate_working_directory(working_directory, policy)

    entries: list[ValidationPlanEntry] = []
    seen_normalized: set[str] = set()

    for raw in task.validation_commands:
        parsed = _parse_command(raw)

        if parsed.normalized in seen_normalized:
            raise ValidationPlanError(
                f"{raw!r}: duplicate command (normalized: {parsed.normalized!r})",
                code=ValidationErrorCode.DUPLICATE_COMMAND,
            )
        seen_normalized.add(parsed.normalized)

        _validate_executable_allowlisted(parsed.executable, policy)
        if parsed.executable == "git":
            _validate_git_invocation(parsed.arguments)

        request = CommandRequest(
            executable=parsed.executable,
            arguments=parsed.arguments,
            working_directory=str(resolved_working_directory),
        )
        entries.append(ValidationPlanEntry(parsed=parsed, request=request))

    return ValidationPlan(
        task_id=task.id,
        working_directory=str(resolved_working_directory),
        policy=policy,
        entries=tuple(entries),
    )

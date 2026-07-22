"""Safe execution of task validation commands inside an approved worktree
(QM-AUTO-006).

Runs one `CommandRequest` at a time, under one `CommandPolicy`: executable and
arguments only, never raw shell text, never `shell=True`, only inside the
approved worktree root, only an allowlisted executable, only a safe `git`
invocation when the executable is `git`, with a bounded timeout, a bounded
output size, and a minimal environment. A nonzero exit is an ordinary
`CommandResult`, not a failure — only a policy violation, a missing
executable, an invalid working directory, or a genuine launch failure raises
`CommandRunnerError`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from automation.schemas.command import (
    CommandErrorCode,
    CommandOutcome,
    CommandPolicy,
    CommandRequest,
    CommandResult,
)

_SHELL_METACHARACTERS = (";", "|", "&", ">", "<", "`", "\n", "\r")
"""Operators, chaining, redirection and pipes — never meaningful to a direct
`exec` call without a shell, but rejected anyway as defense in depth against a
downstream tool that might itself interpret one of our arguments unsafely."""

_GIT_ALWAYS_BLOCKED_SUBCOMMANDS = frozenset(
    {"commit", "push", "merge", "rebase", "reset", "clean", "checkout", "switch"}
)
_GIT_READONLY_SUBCOMMANDS = frozenset({"status", "diff", "log", "rev-parse", "show"})
_GIT_BRANCH_DELETE_FLAGS = frozenset({"-d", "-D", "--delete"})
_GIT_WORKTREE_SAFE_SUBACTION = "list"
"""`git worktree add`/`remove`/`prune`/... are all state-changing and out of
this module's scope (worktree lifecycle belongs to
`automation.worktree_manager`) — only listing is permitted here."""
_GIT_UNSAFE_EXACT_ARGS = frozenset({"-c", "--exec", "--upload-pack", "--receive-pack"})
_GIT_UNSAFE_ARG_PREFIXES = ("--exec=", "--upload-pack=", "--receive-pack=")


class CommandRunnerError(Exception):
    """Raised when a `CommandRequest` cannot be safely run: it violates
    `CommandPolicy`, names an executable that can't be found, names an invalid
    working directory, or fails to launch at all.

    Carries a `CommandErrorCode` so callers can branch on the failure kind
    without parsing the message. A command that launched and ran — even to a
    nonzero exit, or to a timeout — never raises this; see `CommandResult`.
    """

    def __init__(self, message: str, *, code: CommandErrorCode) -> None:
        super().__init__(message)
        self.code = code


def _validate_working_directory(request: CommandRequest, policy: CommandPolicy) -> Path:
    working_directory = Path(request.working_directory)
    if not working_directory.exists():
        raise CommandRunnerError(
            f"{working_directory}: working directory does not exist",
            code=CommandErrorCode.WORKING_DIRECTORY_NOT_FOUND,
        )
    if not working_directory.is_dir():
        raise CommandRunnerError(
            f"{working_directory}: working directory is not a directory",
            code=CommandErrorCode.WORKING_DIRECTORY_NOT_A_DIRECTORY,
        )

    resolved_root = Path(policy.worktree_root).resolve()
    resolved_working_directory = working_directory.resolve()
    if not resolved_working_directory.is_relative_to(resolved_root):
        raise CommandRunnerError(
            f"{resolved_working_directory}: outside the approved worktree root {resolved_root}",
            code=CommandErrorCode.WORKING_DIRECTORY_OUTSIDE_ROOT,
        )
    return resolved_working_directory


def _validate_executable_allowlisted(request: CommandRequest, policy: CommandPolicy) -> None:
    if request.executable not in policy.allowed_executables:
        raise CommandRunnerError(
            f"{request.executable!r} is not an approved executable",
            code=CommandErrorCode.EXECUTABLE_NOT_ALLOWLISTED,
        )


def _validate_arguments_are_safe(arguments: Sequence[str]) -> None:
    for argument in arguments:
        if any(char in argument for char in _SHELL_METACHARACTERS):
            raise CommandRunnerError(
                f"argument {argument!r} contains a shell metacharacter",
                code=CommandErrorCode.UNSAFE_ARGUMENT,
            )


def _validate_git_invocation(arguments: Sequence[str]) -> None:
    if any(
        argument in _GIT_UNSAFE_EXACT_ARGS or argument.startswith(_GIT_UNSAFE_ARG_PREFIXES)
        for argument in arguments
    ):
        raise CommandRunnerError(
            "git invocation uses a config/exec override argument, which is not permitted",
            code=CommandErrorCode.GIT_COMMAND_NOT_PERMITTED,
        )

    if not arguments:
        raise CommandRunnerError(
            "git requires a subcommand", code=CommandErrorCode.GIT_COMMAND_NOT_PERMITTED
        )

    subcommand = arguments[0]
    if subcommand in _GIT_ALWAYS_BLOCKED_SUBCOMMANDS:
        raise CommandRunnerError(
            f"git {subcommand}: destructive command is not permitted",
            code=CommandErrorCode.GIT_COMMAND_NOT_PERMITTED,
        )
    if subcommand == "branch":
        if any(arg in _GIT_BRANCH_DELETE_FLAGS for arg in arguments[1:]):
            raise CommandRunnerError(
                "git branch: deletion is not permitted",
                code=CommandErrorCode.GIT_COMMAND_NOT_PERMITTED,
            )
        return
    if subcommand == "worktree":
        subaction = arguments[1] if len(arguments) > 1 else None
        if subaction != _GIT_WORKTREE_SAFE_SUBACTION:
            raise CommandRunnerError(
                f"git worktree {subaction}: only 'list' is permitted",
                code=CommandErrorCode.GIT_COMMAND_NOT_PERMITTED,
            )
        return
    if subcommand in _GIT_READONLY_SUBCOMMANDS:
        return

    raise CommandRunnerError(
        f"git {subcommand}: not an approved git subcommand",
        code=CommandErrorCode.GIT_COMMAND_NOT_PERMITTED,
    )


def _resolve_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise CommandRunnerError(
            f"{name!r} was not found on PATH", code=CommandErrorCode.EXECUTABLE_NOT_FOUND
        )
    return resolved


def _build_environment(env_allowlist: Sequence[str]) -> dict[str, str]:
    """A minimal environment for the child process: only the allowlisted
    variable names, copied from the current process's environment when
    present. Everything else — secrets included — is never passed through."""
    return {name: os.environ[name] for name in env_allowlist if name in os.environ}


def _coerce_output_text(value: bytes | str | None) -> str:
    """Normalize a subprocess-captured stream to `str`.

    `subprocess.TimeoutExpired.stdout`/`.stderr` are typed generically as
    `bytes | Any` by typeshed regardless of the `text=True` passed to
    `subprocess.run` — at runtime they are `str` here, but this coerces
    defensively rather than trusting that statically."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    """Bound `text` to at most `max_bytes` UTF-8 bytes, decoding safely so a
    multi-byte character is never split. Returns the (possibly shortened)
    text and whether truncation happened."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def run_command(policy: CommandPolicy, request: CommandRequest) -> CommandResult:
    """Run `request` under `policy`. Never mutates either.

    Raises:
        CommandRunnerError: the working directory is missing, not a
            directory, or outside `policy.worktree_root`; the executable
            isn't allowlisted, isn't found, or (for `git`) names a blocked or
            unrecognized subcommand; an argument contains a shell
            metacharacter or a `git` config/exec override; or the resolved
            executable fails to launch at all.
    """
    working_directory = _validate_working_directory(request, policy)
    _validate_executable_allowlisted(request, policy)
    _validate_arguments_are_safe(request.arguments)
    if request.executable == "git":
        _validate_git_invocation(request.arguments)

    executable_path = _resolve_executable(request.executable)
    environment = _build_environment(policy.env_allowlist)

    started_at = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed allowlisted executable, no shell, args pre-validated
            [executable_path, *request.arguments],
            cwd=working_directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=policy.timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started_at
        stdout, stdout_truncated = _truncate(
            _coerce_output_text(exc.stdout), policy.max_output_bytes
        )
        stderr, stderr_truncated = _truncate(
            _coerce_output_text(exc.stderr), policy.max_output_bytes
        )
        return CommandResult(
            executable=request.executable,
            arguments=request.arguments,
            working_directory=str(working_directory),
            outcome=CommandOutcome.TIMED_OUT,
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
    except OSError as exc:
        raise CommandRunnerError(
            f"failed to launch {executable_path!r}: {exc}", code=CommandErrorCode.LAUNCH_FAILED
        ) from exc

    duration = time.monotonic() - started_at
    stdout, stdout_truncated = _truncate(completed.stdout, policy.max_output_bytes)
    stderr, stderr_truncated = _truncate(completed.stderr, policy.max_output_bytes)
    return CommandResult(
        executable=request.executable,
        arguments=request.arguments,
        working_directory=str(working_directory),
        outcome=CommandOutcome.COMPLETED,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )

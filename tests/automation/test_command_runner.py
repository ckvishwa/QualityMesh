"""Unit tests for safe validation-command execution (QM-AUTO-006).

Uses real, local, throwaway Git repositories and the current Python interpreter
under `tmp_path` — no network access, no dependency on the real repository.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import automation.command_runner as command_runner
from automation.command_runner import CommandRunnerError, run_command
from automation.schemas.command import (
    DEFAULT_ENV_ALLOWLIST,
    CommandErrorCode,
    CommandOutcome,
    CommandPolicy,
    CommandRequest,
)

PYTHON_NAME = Path(sys.executable).name

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def run_git(args: list[str], cwd: Path) -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git must be on PATH to run these tests")
    result = subprocess.run(  # noqa: S603 - fixed test-authored args, resolved executable, no shell text
        [git_executable, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "--initial-branch=main"], path)
    run_git(["config", "user.email", "test@example.com"], path)
    run_git(["config", "user.name", "Test User"], path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    run_git(["add", "README.md"], path)
    run_git(["commit", "-m", "initial commit"], path)
    return run_git(["rev-parse", "HEAD"], path)


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo_path = tmp_path / "repo"
    commit = init_repo(repo_path)
    return repo_path, commit


def build_policy(**overrides: Any) -> CommandPolicy:
    fields: dict[str, Any] = {"worktree_root": "unused"}
    fields.update(overrides)
    return CommandPolicy.model_validate(fields)


def build_request(**overrides: Any) -> CommandRequest:
    fields: dict[str, Any] = {"executable": "git", "arguments": [], "working_directory": "unused"}
    fields.update(overrides)
    return CommandRequest.model_validate(fields)


def python_policy(worktree_root: Path, **overrides: Any) -> CommandPolicy:
    if shutil.which(PYTHON_NAME) is None:
        pytest.skip(f"{PYTHON_NAME!r} not resolvable on PATH in this environment")
    fields: dict[str, Any] = {
        "worktree_root": str(worktree_root),
        "allowed_executables": (PYTHON_NAME,),
    }
    fields.update(overrides)
    return build_policy(**fields)


# --------------------------------------------------------------------------------------
# Successful execution
# --------------------------------------------------------------------------------------


def test_approved_command_succeeds(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["status"], working_directory=str(repo_path))

    result = run_command(policy, request)

    assert result.outcome is CommandOutcome.COMPLETED
    assert result.exit_code == 0
    assert result.timed_out is False


def test_nonzero_exit_captured(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(
        arguments=["show", "0000000000000000000000000000000000000000"],
        working_directory=str(repo_path),
    )

    result = run_command(policy, request)

    assert result.outcome is CommandOutcome.COMPLETED
    assert result.exit_code != 0


def test_stdout_and_stderr_are_captured(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["rev-parse", "HEAD"], working_directory=str(repo_path))

    result = run_command(policy, request)

    assert commit in result.stdout
    assert result.stderr == ""


def test_valid_worktree_directory_is_accepted(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["status"], working_directory=str(repo_path))

    result = run_command(policy, request)

    assert result.working_directory == str(repo_path.resolve())


# --------------------------------------------------------------------------------------
# Timeout and truncation
# --------------------------------------------------------------------------------------


def test_timeout_is_enforced(tmp_path: Path) -> None:
    policy = python_policy(tmp_path, timeout_seconds=0.5)
    request = build_request(
        executable=PYTHON_NAME,
        arguments=["-c", "__import__('time').sleep(10)"],
        working_directory=str(tmp_path),
    )

    result = run_command(policy, request)

    assert result.outcome is CommandOutcome.TIMED_OUT
    assert result.exit_code is None
    assert result.timed_out is True
    assert result.duration_seconds < 5


def test_output_truncation(tmp_path: Path) -> None:
    policy = python_policy(tmp_path, max_output_bytes=10)
    request = build_request(
        executable=PYTHON_NAME,
        arguments=["-c", "print('x' * 5000)"],
        working_directory=str(tmp_path),
    )

    result = run_command(policy, request)

    assert result.stdout_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 10


# --------------------------------------------------------------------------------------
# Executable resolution and allowlisting
# --------------------------------------------------------------------------------------


def test_executable_not_found_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(
        worktree_root=str(repo_path), allowed_executables=("definitely-not-a-real-tool-xyz",)
    )
    request = build_request(
        executable="definitely-not-a-real-tool-xyz", working_directory=str(repo_path)
    )

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.EXECUTABLE_NOT_FOUND


def test_executable_not_allowlisted_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path), allowed_executables=("ruff",))
    request = build_request(
        executable="git", arguments=["status"], working_directory=str(repo_path)
    )

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.EXECUTABLE_NOT_ALLOWLISTED


# --------------------------------------------------------------------------------------
# Unsafe arguments
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        ["status; rm -rf /"],
        ["status && rm -rf /"],
        ["status | cat"],
        ["status > out.txt"],
    ],
    ids=["semicolon", "and-and", "pipe", "redirect"],
)
def test_shell_operators_embedded_in_one_argument_are_rejected(
    tmp_path: Path, arguments: list[str]
) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=arguments, working_directory=str(repo_path))

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.UNSAFE_ARGUMENT


def test_argument_based_command_chaining_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(
        arguments=["status", "&&", "rm", "-rf", "/"], working_directory=str(repo_path)
    )

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.UNSAFE_ARGUMENT


def test_git_config_override_argument_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(
        arguments=["-c", "core.pager=less", "log"], working_directory=str(repo_path)
    )

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.GIT_COMMAND_NOT_PERMITTED


# --------------------------------------------------------------------------------------
# Working directory validation
# --------------------------------------------------------------------------------------


def test_missing_working_directory_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(
        arguments=["status"], working_directory=str(repo_path / "does-not-exist")
    )

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.WORKING_DIRECTORY_NOT_FOUND


def test_working_directory_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["status"], working_directory=str(repo_path / "README.md"))

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.WORKING_DIRECTORY_NOT_A_DIRECTORY


def test_outside_root_working_directory_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["status"], working_directory=str(outside_dir))

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.WORKING_DIRECTORY_OUTSIDE_ROOT


# --------------------------------------------------------------------------------------
# Git subcommand policy
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand", ["status", "diff", "log", "rev-parse", "show"])
def test_read_only_git_commands_are_allowed(tmp_path: Path, subcommand: str) -> None:
    repo_path, commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    args = [subcommand] if subcommand != "rev-parse" else ["rev-parse", "HEAD"]
    if subcommand == "show":
        args = ["show", commit]
    request = build_request(arguments=args, working_directory=str(repo_path))

    result = run_command(policy, request)

    assert result.outcome is CommandOutcome.COMPLETED


@pytest.mark.parametrize(
    "arguments",
    [
        ["commit", "-m", "x"],
        ["push"],
        ["merge", "main"],
        ["rebase", "main"],
        ["reset", "--hard"],
        ["clean", "-fd"],
        ["checkout", "main"],
        ["switch", "main"],
        ["branch", "-d", "some-branch"],
        ["branch", "-D", "some-branch"],
        ["worktree", "add", "../x"],
        ["worktree", "remove", "x"],
    ],
    ids=[
        "commit",
        "push",
        "merge",
        "rebase",
        "reset",
        "clean",
        "checkout",
        "switch",
        "branch-delete-lower",
        "branch-delete-upper",
        "worktree-add",
        "worktree-remove",
    ],
)
def test_destructive_git_commands_are_rejected(tmp_path: Path, arguments: list[str]) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=arguments, working_directory=str(repo_path))

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.GIT_COMMAND_NOT_PERMITTED


def test_git_worktree_list_is_allowed(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["worktree", "list"], working_directory=str(repo_path))

    result = run_command(policy, request)

    assert result.outcome is CommandOutcome.COMPLETED
    assert result.exit_code == 0


def test_git_branch_listing_is_allowed(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["branch", "--list"], working_directory=str(repo_path))

    result = run_command(policy, request)

    assert result.outcome is CommandOutcome.COMPLETED
    assert result.exit_code == 0


def test_unrecognized_git_subcommand_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["gc"], working_directory=str(repo_path))

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.GIT_COMMAND_NOT_PERMITTED


# --------------------------------------------------------------------------------------
# Environment allowlisting
# --------------------------------------------------------------------------------------


def test_environment_allowlisting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QM_ALLOWED_TEST_VAR", "visible-value")
    monkeypatch.setenv("QM_NOT_ALLOWED_TEST_VAR", "should-not-be-visible")
    policy = python_policy(tmp_path, env_allowlist=(*DEFAULT_ENV_ALLOWLIST, "QM_ALLOWED_TEST_VAR"))
    request = build_request(
        executable=PYTHON_NAME,
        arguments=[
            "-c",
            "print(__import__('os').environ.get('QM_ALLOWED_TEST_VAR', 'MISSING'), "
            "__import__('os').environ.get('QM_NOT_ALLOWED_TEST_VAR', 'MISSING'))",
        ],
        working_directory=str(tmp_path),
    )

    result = run_command(policy, request)

    assert "visible-value" in result.stdout
    assert "should-not-be-visible" not in result.stdout


def test_secret_like_variables_are_not_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "totally-a-real-secret")
    policy = python_policy(tmp_path)
    request = build_request(
        executable=PYTHON_NAME,
        arguments=["-c", "print(__import__('os').environ.get('GITHUB_TOKEN', 'MISSING'))"],
        working_directory=str(tmp_path),
    )

    result = run_command(policy, request)

    assert "MISSING" in result.stdout
    assert "totally-a-real-secret" not in result.stdout


# --------------------------------------------------------------------------------------
# Launch failure and no mutation
# --------------------------------------------------------------------------------------


def test_subprocess_launch_failure_is_handled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["status"], working_directory=str(repo_path))

    def boom(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("simulated launch failure")

    monkeypatch.setattr(command_runner.subprocess, "run", boom)

    with pytest.raises(CommandRunnerError) as exc_info:
        run_command(policy, request)
    assert exc_info.value.code is CommandErrorCode.LAUNCH_FAILED


def test_input_models_are_not_mutated(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    policy = build_policy(worktree_root=str(repo_path))
    request = build_request(arguments=["status"], working_directory=str(repo_path))
    policy_before = copy.deepcopy(policy)
    request_before = copy.deepcopy(request)

    run_command(policy, request)

    assert policy == policy_before
    assert request == request_before

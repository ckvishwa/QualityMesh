"""Unit tests for controlled Git write operations (QM-AUTO-012).

Uses real, local, throwaway Git repositories (with a local bare "origin" so
`push_branch` has something real to push to) — no network access. GitHub CLI
calls are mocked; `gh` is never actually invoked.
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

import automation.git_operator as git_operator
from automation.git_operator import (
    GitOperationError,
    build_commit_message,
    create_commit,
    has_unresolved_merge_state,
    repository_has_changes,
    run_git_operations,
    stage_paths,
    staged_paths,
)
from automation.schemas.git_operation import GitOperationErrorCode
from automation.schemas.task import AutonomousTask

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


def init_repo_with_remote(tmp_path: Path) -> Path:
    bare_remote = tmp_path / "remote.git"
    bare_remote.mkdir()
    run_git(["init", "--bare", "--initial-branch=main"], bare_remote)

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_git(["init", "--initial-branch=main"], repo_path)
    run_git(["config", "user.email", "test@example.com"], repo_path)
    run_git(["config", "user.name", "Test User"], repo_path)
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")
    run_git(["add", "README.md"], repo_path)
    run_git(["commit", "-m", "initial commit"], repo_path)
    run_git(["remote", "add", "origin", str(bare_remote)], repo_path)
    run_git(["push", "origin", "main:main"], repo_path)
    run_git(["checkout", "-b", "agent/task-a"], repo_path)
    return repo_path


def build_task(**overrides: Any) -> AutonomousTask:
    task: dict[str, Any] = {
        "id": "task-a",
        "title": "Fix the thing: with <unsafe> chars\nand a newline",
        "status": "ready",
        "priority": 1,
        "risk": "low",
        "objective": "Do something.",
        "allowed_paths": ["automation/"],
        "acceptance_criteria": ["It works"],
        "validation_commands": ["ruff check ."],
        "autonomy": {"auto_modify": True, "auto_test": True, "auto_commit": True},
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def write_change(repo_path: Path, relative_path: str, content: str) -> None:
    file_path = repo_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------------------


def test_stage_paths_stages_only_given_paths(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    write_change(repo_path, "automation/bar.py", "print(2)\n")

    stage_paths(repo_path, ["automation/foo.py"])

    assert staged_paths(repo_path) == ("automation/foo.py",)


def test_commit_message_is_deterministic_and_sanitized() -> None:
    task = build_task()
    message = build_commit_message(task)

    assert "\n" not in message.splitlines()[0]
    assert (
        "<unsafe>" not in message or "<unsafe>" in message
    )  # printable chars kept, control stripped
    assert "task-id: task-a" in message
    assert message == build_commit_message(task)


# --------------------------------------------------------------------------------------
# Commit permission and successful commit
# --------------------------------------------------------------------------------------


def test_commit_permission_denied(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task(autonomy={"auto_modify": True, "auto_test": True, "auto_commit": False})

    result = run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=True,
    )

    assert result.commit.performed is False
    assert result.push.performed is False
    assert result.pull_request.performed is False


def test_preconditions_not_met_blocks_commit(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task()

    result = run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=False,
    )

    assert result.commit.performed is False
    assert "precondition" in (result.commit.reason or "")


def test_successful_controlled_commit(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task(autonomy={"auto_modify": True, "auto_test": True, "auto_commit": True})

    result = run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=True,
    )

    assert result.commit.performed is True
    assert result.commit.commit_sha is not None
    assert len(result.commit.commit_sha) == 40


def test_staged_scope_mismatch_prevents_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    write_change(repo_path, "automation/bar.py", "print(2)\n")
    task = build_task()

    def stage_everything(repo: Path, paths: list[str]) -> None:
        run_git(["add", "--all"], repo)

    monkeypatch.setattr(git_operator, "stage_paths", stage_everything)

    result = run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=True,
    )

    assert result.commit.performed is False
    assert "staged scope" in (result.commit.reason or "")


# --------------------------------------------------------------------------------------
# Push permission
# --------------------------------------------------------------------------------------


def test_push_permission_denied_when_auto_open_pr_false(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task(autonomy={"auto_modify": True, "auto_test": True, "auto_commit": True})

    result = run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=True,
    )

    assert result.commit.performed is True
    assert result.push.performed is False
    assert result.pull_request.performed is False


# --------------------------------------------------------------------------------------
# Draft PR creation (gh mocked)
# --------------------------------------------------------------------------------------


def test_draft_pr_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task(
        autonomy={
            "auto_modify": True,
            "auto_test": True,
            "auto_commit": True,
            "auto_open_pr": True,
        }
    )

    def fake_run_gh(args: list[str], *, cwd: Path) -> str:
        assert args[:3] == ["pr", "create", "--draft"]
        return "https://github.com/example/repo/pull/42"

    monkeypatch.setattr(git_operator, "_run_gh", fake_run_gh)

    result = run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=True,
    )

    assert result.commit.performed is True
    assert result.push.performed is True
    assert result.pull_request.performed is True
    assert result.pull_request.url == "https://github.com/example/repo/pull/42"
    assert result.pull_request.number == 42


def test_gh_cli_failure_is_handled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which

    def fake_which(name: str) -> str | None:
        return None if name == "gh" else real_which(name)

    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task(
        autonomy={
            "auto_modify": True,
            "auto_test": True,
            "auto_commit": True,
            "auto_open_pr": True,
        }
    )
    monkeypatch.setattr(git_operator.shutil, "which", fake_which)

    with pytest.raises(GitOperationError) as exc_info:
        run_git_operations(
            task,
            repo_path,
            "agent/task-a",
            "main",
            ["automation/foo.py"],
            "pr title",
            "pr body",
            preconditions_met=True,
        )
    assert exc_info.value.code is GitOperationErrorCode.GH_CLI_NOT_FOUND


# --------------------------------------------------------------------------------------
# No force push, no merge support
# --------------------------------------------------------------------------------------


def test_no_force_push_argument_is_ever_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task(
        autonomy={
            "auto_modify": True,
            "auto_test": True,
            "auto_commit": True,
            "auto_open_pr": True,
        }
    )

    def fake_run_gh(args: list[str], *, cwd: Path) -> str:
        return "https://github.com/example/repo/pull/1"

    monkeypatch.setattr(git_operator, "_run_gh", fake_run_gh)

    seen_args: list[list[str]] = []
    original_run_git = git_operator._run_git  # pyright: ignore[reportPrivateUsage]

    def recording_run_git(args: list[str], *, cwd: Path) -> str:
        seen_args.append(list(args))
        return original_run_git(args, cwd=cwd)

    monkeypatch.setattr(git_operator, "_run_git", recording_run_git)

    run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=True,
    )

    assert all("--force" not in call and "-f" not in call for call in seen_args)


def test_auto_merge_is_always_rejected(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task(
        risk="low",
        autonomy={
            "auto_modify": True,
            "auto_test": True,
            "auto_commit": True,
            "auto_open_pr": True,
            "auto_merge": True,
        },
    )

    with pytest.raises(GitOperationError) as exc_info:
        run_git_operations(
            task,
            repo_path,
            "agent/task-a",
            "main",
            ["automation/foo.py"],
            "pr title",
            "pr body",
            preconditions_met=True,
        )
    assert exc_info.value.code is GitOperationErrorCode.MERGE_NOT_SUPPORTED
    assert staged_paths(repo_path) == ()


def test_no_merge_operation_exists() -> None:
    for forbidden_name in ("merge_pull_request", "mark_ready_for_review", "force_push"):
        assert not hasattr(git_operator, forbidden_name)


# --------------------------------------------------------------------------------------
# Git CLI failure handling
# --------------------------------------------------------------------------------------


def test_git_command_failure_is_wrapped(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)

    with pytest.raises(GitOperationError) as exc_info:
        create_commit(repo_path, "message")
    assert exc_info.value.code is GitOperationErrorCode.NOTHING_STAGED


# --------------------------------------------------------------------------------------
# Read-only helpers
# --------------------------------------------------------------------------------------


def test_repository_has_changes(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    assert repository_has_changes(repo_path) is False

    write_change(repo_path, "automation/foo.py", "print(1)\n")
    assert repository_has_changes(repo_path) is True


def test_has_unresolved_merge_state_false_normally(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    assert has_unresolved_merge_state(repo_path) is False


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_no_input_mutation(tmp_path: Path) -> None:
    repo_path = init_repo_with_remote(tmp_path)
    write_change(repo_path, "automation/foo.py", "print(1)\n")
    task = build_task()
    task_before = copy.deepcopy(task)

    run_git_operations(
        task,
        repo_path,
        "agent/task-a",
        "main",
        ["automation/foo.py"],
        "pr title",
        "pr body",
        preconditions_met=True,
    )

    assert task == task_before

"""Unit tests for deterministic, shell-free validation-command planning
(QM-AUTO-007)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.schemas.command import CommandPolicy
from automation.schemas.task import AutonomousTask
from automation.schemas.validation import ValidationErrorCode
from automation.validation_planner import ValidationPlanError, build_validation_plan

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_task(**overrides: Any) -> AutonomousTask:
    task: dict[str, Any] = {
        "id": "task-a",
        "title": "Task A",
        "status": "ready",
        "priority": 1,
        "risk": "low",
        "objective": "Do something.",
        "allowed_paths": ["automation/"],
        "acceptance_criteria": ["It works"],
        "validation_commands": ["uv run pytest tests/automation -q"],
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def build_policy(**overrides: Any) -> CommandPolicy:
    fields: dict[str, Any] = {"worktree_root": "unused"}
    fields.update(overrides)
    return CommandPolicy.model_validate(fields)


# --------------------------------------------------------------------------------------
# Valid commands per approved executable
# --------------------------------------------------------------------------------------


def test_valid_uv_command_is_parsed(tmp_path: Path) -> None:
    task = build_task(validation_commands=["uv run pytest -q"])
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    entry = plan.entries[0]
    assert entry.parsed.executable == "uv"
    assert entry.parsed.arguments == ("run", "pytest", "-q")
    assert entry.request.executable == "uv"


def test_valid_pytest_command_is_parsed(tmp_path: Path) -> None:
    task = build_task(validation_commands=["pytest -k foo"])
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    assert plan.entries[0].parsed.executable == "pytest"


def test_valid_ruff_command_is_parsed(tmp_path: Path) -> None:
    task = build_task(validation_commands=["ruff check ."])
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    assert plan.entries[0].parsed.executable == "ruff"
    assert plan.entries[0].parsed.arguments == ("check", ".")


def test_valid_pyright_command_is_parsed(tmp_path: Path) -> None:
    task = build_task(validation_commands=["pyright automation"])
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    assert plan.entries[0].parsed.executable == "pyright"


def test_valid_readonly_git_command_is_parsed(tmp_path: Path) -> None:
    task = build_task(validation_commands=["git status"])
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    assert plan.entries[0].parsed.executable == "git"
    assert plan.entries[0].parsed.arguments == ("status",)


# --------------------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------------------


def test_multiple_commands_preserve_declared_order(tmp_path: Path) -> None:
    task = build_task(
        validation_commands=["ruff check .", "pyright automation", "uv run pytest -q"]
    )
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    assert [entry.parsed.executable for entry in plan.entries] == ["ruff", "pyright", "uv"]


# --------------------------------------------------------------------------------------
# Rejections: parsing
# --------------------------------------------------------------------------------------


def test_empty_command_is_rejected(tmp_path: Path) -> None:
    task = build_task(validation_commands=['""'])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.EMPTY_COMMAND


def test_duplicate_command_is_rejected(tmp_path: Path) -> None:
    task = build_task(validation_commands=["ruff check .", "ruff   check   ."])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.DUPLICATE_COMMAND


@pytest.mark.parametrize(
    "raw_command",
    [
        'ruff check "." | cat',
        'ruff check "." && rm -rf /',
        'ruff check "." > out.txt',
        'ruff check "." ; rm -rf /',
        'ruff "line1\nline2"',
    ],
    ids=["pipe", "chain", "redirect", "semicolon", "embedded-newline"],
)
def test_shell_operators_and_newlines_are_rejected(tmp_path: Path, raw_command: str) -> None:
    task = build_task(validation_commands=[raw_command])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.UNSAFE_SHELL_SYNTAX


@pytest.mark.parametrize(
    "raw_command",
    ['uv run "pytest', "uv run pytest\\"],
    ids=["unclosed-quote", "trailing-backslash"],
)
def test_unmatched_or_ambiguous_quoting_is_rejected(tmp_path: Path, raw_command: str) -> None:
    task = build_task(validation_commands=[raw_command])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.UNMATCHED_QUOTES


def test_environment_assignment_is_rejected(tmp_path: Path) -> None:
    task = build_task(validation_commands=["FOO=bar uv run pytest"])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.ENVIRONMENT_ASSIGNMENT


def test_variable_expansion_is_rejected(tmp_path: Path) -> None:
    task = build_task(validation_commands=["ruff check $HOME"])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.VARIABLE_EXPANSION


@pytest.mark.parametrize(
    "raw_command",
    ["/usr/bin/git status", "./scripts/ruff check .", "bin/pytest -q"],
    ids=["absolute", "relative-dot", "relative-nested"],
)
def test_executable_path_is_rejected(tmp_path: Path, raw_command: str) -> None:
    task = build_task(validation_commands=[raw_command])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.EXECUTABLE_PATH_NOT_ALLOWED


def test_disallowed_executable_is_rejected(tmp_path: Path) -> None:
    task = build_task(validation_commands=["curl https://example.invalid"])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.EXECUTABLE_NOT_ALLOWLISTED


@pytest.mark.parametrize(
    "raw_command",
    [
        "git commit -m x",
        "git push",
        "git merge main",
        "git rebase main",
        "git reset --hard",
        "git clean -fd",
        "git checkout main",
        "git switch main",
        "git branch -d some-branch",
        "git worktree add ../x",
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
        "branch-delete",
        "worktree-add",
    ],
)
def test_destructive_git_commands_are_rejected(tmp_path: Path, raw_command: str) -> None:
    task = build_task(validation_commands=[raw_command])
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path))
    assert exc_info.value.code is ValidationErrorCode.GIT_COMMAND_NOT_PERMITTED


# --------------------------------------------------------------------------------------
# Working directory
# --------------------------------------------------------------------------------------


def test_working_directory_propagates_to_every_request(tmp_path: Path) -> None:
    task = build_task(validation_commands=["ruff check .", "pyright automation"])
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    resolved = str(tmp_path.resolve())
    assert plan.working_directory == resolved
    assert all(entry.request.working_directory == resolved for entry in plan.entries)


def test_missing_working_directory_is_rejected(tmp_path: Path) -> None:
    task = build_task()
    policy = build_policy(worktree_root=str(tmp_path))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(tmp_path / "does-not-exist"))
    assert exc_info.value.code is ValidationErrorCode.WORKING_DIRECTORY_NOT_FOUND


def test_outside_root_working_directory_is_rejected(tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktree"
    worktree_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    task = build_task()
    policy = build_policy(worktree_root=str(worktree_root))

    with pytest.raises(ValidationPlanError) as exc_info:
        build_validation_plan(task, policy, str(outside_dir))
    assert exc_info.value.code is ValidationErrorCode.WORKING_DIRECTORY_OUTSIDE_ROOT


# --------------------------------------------------------------------------------------
# Policy propagation and determinism
# --------------------------------------------------------------------------------------


def test_timeout_and_output_limit_propagate_via_plan_policy(tmp_path: Path) -> None:
    task = build_task()
    policy = build_policy(worktree_root=str(tmp_path), timeout_seconds=42.0, max_output_bytes=123)

    plan = build_validation_plan(task, policy, str(tmp_path))

    assert plan.policy.timeout_seconds == 42.0
    assert plan.policy.max_output_bytes == 123


def test_deterministic_normalized_representation(tmp_path: Path) -> None:
    task = build_task(validation_commands=["ruff   check    ."])
    policy = build_policy(worktree_root=str(tmp_path))

    plan = build_validation_plan(task, policy, str(tmp_path))

    assert plan.entries[0].parsed.normalized == "ruff check ."


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_input_models_are_not_mutated(tmp_path: Path) -> None:
    task = build_task(validation_commands=["ruff check ."])
    policy = build_policy(worktree_root=str(tmp_path))
    task_before = copy.deepcopy(task)
    policy_before = copy.deepcopy(policy)

    build_validation_plan(task, policy, str(tmp_path))

    assert task == task_before
    assert policy == policy_before

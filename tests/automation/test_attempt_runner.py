"""Unit tests for the single-attempt lifecycle (QM-AUTO-009).

Boundary behavior (call order, failure propagation) is tested with fakes
substituted for each component; one harmless local integration test exercises
the real components together against a throwaway Git repository.
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

import automation.attempt_runner as attempt_runner
from automation.attempt_runner import run_attempt
from automation.cache_manager import CacheError
from automation.context_builder import ContextBuildError
from automation.schemas.attempt import AttemptOutcome
from automation.schemas.cache import CACHE_MANIFEST_VERSION, CacheManifest
from automation.schemas.command import CommandOutcome, CommandPolicy, CommandRequest
from automation.schemas.context import ContextBuildResult
from automation.schemas.task import AutonomousTask
from automation.schemas.validation import (
    ParsedValidationCommand,
    ValidationPlan,
    ValidationPlanEntry,
)
from automation.schemas.validation_run import (
    AggregateOutcome,
    CommandResult,
    ValidationEntryResult,
    ValidationEntryStatus,
    ValidationExecutionPolicy,
    ValidationRunResult,
)
from automation.schemas.worktree import WorktreeErrorCode, WorktreeMetadata
from automation.validation_executor import ValidationExecutionError
from automation.validation_planner import ValidationErrorCode, ValidationPlanError
from automation.worktree_manager import WorktreeError

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
        "validation_commands": ["git status"],
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def make_worktree_metadata(task_id: str, worktree_path: str) -> WorktreeMetadata:
    return WorktreeMetadata(
        task_id=task_id,
        repository_path="unused-repo",
        worktree_path=worktree_path,
        branch_name=f"agent/{task_id}",
        base_commit="a" * 40,
    )


def make_manifest() -> CacheManifest:
    return CacheManifest(version=CACHE_MANIFEST_VERSION, entries=())


def make_context_result() -> ContextBuildResult:
    return ContextBuildResult(
        records=(), manifest=make_manifest(), hit_count=0, miss_count=0, stale_count=0
    )


def make_plan(task_id: str, worktree_path: str) -> ValidationPlan:
    policy = CommandPolicy(worktree_root=worktree_path)
    request = CommandRequest(
        executable="git", arguments=("status",), working_directory=worktree_path
    )
    parsed = ParsedValidationCommand(raw="git status", executable="git", arguments=("status",))
    entry = ValidationPlanEntry(parsed=parsed, request=request)
    return ValidationPlan(
        task_id=task_id, working_directory=worktree_path, policy=policy, entries=(entry,)
    )


def make_validation_result(
    task_id: str, worktree_path: str, outcome: AggregateOutcome
) -> ValidationRunResult:
    request = CommandRequest(
        executable="git", arguments=("status",), working_directory=worktree_path
    )
    if outcome is AggregateOutcome.PASSED:
        status = ValidationEntryStatus.PASSED
        exit_code: int | None = 0
        command_outcome = CommandOutcome.COMPLETED
    elif outcome is AggregateOutcome.TIMED_OUT:
        status = ValidationEntryStatus.TIMED_OUT
        exit_code = None
        command_outcome = CommandOutcome.TIMED_OUT
    else:
        status = ValidationEntryStatus.FAILED
        exit_code = 1
        command_outcome = CommandOutcome.COMPLETED

    command_result = CommandResult(
        executable="git",
        arguments=("status",),
        working_directory=worktree_path,
        outcome=command_outcome,
        exit_code=exit_code,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        stdout_truncated=False,
        stderr_truncated=False,
    )
    entry_result = ValidationEntryResult(
        index=0, request=request, status=status, command_result=command_result
    )
    return ValidationRunResult(
        task_id=task_id,
        outcome=outcome,
        results=(entry_result,),
        executed_count=1,
        skipped_count=0,
        duration_seconds=0.01,
    )


def call_run_attempt(task: AutonomousTask, tmp_path: Path) -> Any:
    return run_attempt(
        task=task,
        repository_path=str(tmp_path / "repo"),
        base_commit="a" * 40,
        worktree_parent=str(tmp_path / "worktrees"),
        cache_manifest_path=str(tmp_path / "cache.json"),
        command_policy=CommandPolicy(worktree_root=str(tmp_path / "worktrees" / task.id)),
        execution_policy=ValidationExecutionPolicy(),
    )


def patch_all_phases(
    monkeypatch: pytest.MonkeyPatch,
    task: AutonomousTask,
    worktree_path: str,
    *,
    fail_at: str | None = None,
    validation_outcome: AggregateOutcome = AggregateOutcome.PASSED,
) -> list[str]:
    """Patch every phase to a recording fake. `fail_at` names the phase that
    should raise instead of succeeding; every phase after it must never run.
    Returns the shared call-order list (only phases that actually ran)."""
    calls: list[str] = []

    def fake_create_worktree(request: Any) -> WorktreeMetadata:
        calls.append("create_worktree")
        if fail_at == "create_worktree":
            raise WorktreeError("boom", code=WorktreeErrorCode.REPOSITORY_NOT_FOUND)
        return make_worktree_metadata(task.id, worktree_path)

    def fake_load_manifest(path: Any) -> CacheManifest:
        calls.append("load_manifest")
        if fail_at == "load_manifest":
            raise CacheError("boom")
        return make_manifest()

    def fake_build_context(task_arg: Any, root: Any, manifest: Any) -> ContextBuildResult:
        calls.append("build_context")
        if fail_at == "build_context":
            raise ContextBuildError("boom")
        return make_context_result()

    def fake_write_manifest_atomically(path: Any, manifest: Any) -> None:
        calls.append("write_manifest_atomically")
        if fail_at == "write_manifest_atomically":
            raise OSError("boom")

    def fake_build_validation_plan(
        task_arg: Any, policy: Any, working_directory: Any
    ) -> ValidationPlan:
        calls.append("build_validation_plan")
        if fail_at == "build_validation_plan":
            raise ValidationPlanError("boom", code=ValidationErrorCode.EMPTY_COMMAND)
        return make_plan(task.id, worktree_path)

    def fake_execute_validation_plan(plan: Any, execution_policy: Any) -> ValidationRunResult:
        calls.append("execute_validation_plan")
        if fail_at == "execute_validation_plan":
            raise ValidationExecutionError("boom")
        return make_validation_result(task.id, worktree_path, validation_outcome)

    monkeypatch.setattr(attempt_runner, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(attempt_runner, "load_manifest", fake_load_manifest)
    monkeypatch.setattr(attempt_runner, "build_context", fake_build_context)
    monkeypatch.setattr(attempt_runner, "write_manifest_atomically", fake_write_manifest_atomically)
    monkeypatch.setattr(attempt_runner, "build_validation_plan", fake_build_validation_plan)
    monkeypatch.setattr(attempt_runner, "execute_validation_plan", fake_execute_validation_plan)
    return calls


ALL_PHASES = [
    "create_worktree",
    "load_manifest",
    "build_context",
    "write_manifest_atomically",
    "build_validation_plan",
    "execute_validation_plan",
]


# --------------------------------------------------------------------------------------
# Successful composition and call order
# --------------------------------------------------------------------------------------


def test_successful_composition_calls_every_phase_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    worktree_path = str(tmp_path / "worktrees" / task.id)
    calls = patch_all_phases(monkeypatch, task, worktree_path)

    result = call_run_attempt(task, tmp_path)

    assert calls == ALL_PHASES
    assert result.outcome is AttemptOutcome.PASSED
    assert result.error_message is None


def test_metadata_propagation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = build_task(id="QM-AUTO-009")
    worktree_path = str(tmp_path / "worktrees" / task.id)
    patch_all_phases(monkeypatch, task, worktree_path)

    result = call_run_attempt(task, tmp_path)

    assert result.task_id == "QM-AUTO-009"
    assert result.base_commit == "a" * 40
    assert result.branch == "agent/QM-AUTO-009"
    assert result.worktree_path == worktree_path
    assert result.context_result is not None
    assert result.validation_result is not None
    assert result.duration_seconds >= 0
    assert result.started_at.tzinfo is not None
    assert result.ended_at.tzinfo is not None


# --------------------------------------------------------------------------------------
# Setup failures and short-circuiting
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fail_at", "expected_calls"),
    [
        ("create_worktree", ["create_worktree"]),
        ("load_manifest", ["create_worktree", "load_manifest"]),
        ("build_context", ["create_worktree", "load_manifest", "build_context"]),
        (
            "write_manifest_atomically",
            ["create_worktree", "load_manifest", "build_context", "write_manifest_atomically"],
        ),
        (
            "build_validation_plan",
            [
                "create_worktree",
                "load_manifest",
                "build_context",
                "write_manifest_atomically",
                "build_validation_plan",
            ],
        ),
    ],
)
def test_setup_failure_stops_before_later_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str, expected_calls: list[str]
) -> None:
    task = build_task()
    worktree_path = str(tmp_path / "worktrees" / task.id)
    calls = patch_all_phases(monkeypatch, task, worktree_path, fail_at=fail_at)

    result = call_run_attempt(task, tmp_path)

    assert calls == expected_calls
    assert result.outcome is AttemptOutcome.SETUP_ERROR
    assert "boom" in (result.error_message or "")
    assert result.validation_result is None


def test_context_result_carried_when_planning_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    worktree_path = str(tmp_path / "worktrees" / task.id)
    patch_all_phases(monkeypatch, task, worktree_path, fail_at="build_validation_plan")

    result = call_run_attempt(task, tmp_path)

    assert result.outcome is AttemptOutcome.SETUP_ERROR
    assert result.context_result is not None


# --------------------------------------------------------------------------------------
# Validation outcomes
# --------------------------------------------------------------------------------------


def test_validation_failure_is_normal_attempt_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    worktree_path = str(tmp_path / "worktrees" / task.id)
    patch_all_phases(monkeypatch, task, worktree_path, validation_outcome=AggregateOutcome.FAILED)

    result = call_run_attempt(task, tmp_path)

    assert result.outcome is AttemptOutcome.VALIDATION_FAILED
    assert result.error_message is None
    assert result.validation_result is not None


def test_validation_timeout_maps_to_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    worktree_path = str(tmp_path / "worktrees" / task.id)
    patch_all_phases(
        monkeypatch, task, worktree_path, validation_outcome=AggregateOutcome.TIMED_OUT
    )

    result = call_run_attempt(task, tmp_path)

    assert result.outcome is AttemptOutcome.TIMED_OUT


def test_execution_error_is_reported_without_hiding_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    worktree_path = str(tmp_path / "worktrees" / task.id)
    patch_all_phases(monkeypatch, task, worktree_path, fail_at="execute_validation_plan")

    result = call_run_attempt(task, tmp_path)

    assert result.outcome is AttemptOutcome.EXECUTION_ERROR
    assert "boom" in (result.error_message or "")
    assert result.validation_result is None
    assert result.context_result is not None


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_input_models_are_not_mutated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = build_task()
    worktree_path = str(tmp_path / "worktrees" / task.id)
    patch_all_phases(monkeypatch, task, worktree_path)
    command_policy = CommandPolicy(worktree_root=worktree_path)
    execution_policy = ValidationExecutionPolicy()
    task_before = copy.deepcopy(task)
    command_policy_before = copy.deepcopy(command_policy)
    execution_policy_before = copy.deepcopy(execution_policy)

    run_attempt(
        task=task,
        repository_path=str(tmp_path / "repo"),
        base_commit="a" * 40,
        worktree_parent=str(tmp_path / "worktrees"),
        cache_manifest_path=str(tmp_path / "cache.json"),
        command_policy=command_policy,
        execution_policy=execution_policy,
    )

    assert task == task_before
    assert command_policy == command_policy_before
    assert execution_policy == execution_policy_before


# --------------------------------------------------------------------------------------
# Harmless local integration test (no mocks)
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


def test_real_integration_end_to_end(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    commit = init_repo(repo_path)
    worktree_parent = tmp_path / "worktrees"
    worktree_parent.mkdir()

    task = build_task(
        id="integration-task",
        context_files=["README.md"],
        validation_commands=["git status"],
    )

    result = run_attempt(
        task=task,
        repository_path=str(repo_path),
        base_commit=commit,
        worktree_parent=str(worktree_parent),
        cache_manifest_path=str(tmp_path / "cache.json"),
        command_policy=CommandPolicy(
            worktree_root=str(worktree_parent / task.id), allowed_executables=("git",)
        ),
        execution_policy=ValidationExecutionPolicy(),
    )

    assert result.outcome is AttemptOutcome.PASSED
    assert result.branch == f"agent/{task.id}"
    assert Path(result.worktree_path).is_dir()
    assert result.context_result is not None
    assert len(result.context_result.records) == 1
    assert (tmp_path / "cache.json").exists()
    assert result.validation_result is not None
    assert result.validation_result.outcome is AggregateOutcome.PASSED

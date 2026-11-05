"""Unit tests for the top-level autonomous task orchestrator (QM-AUTO-013).

Component boundaries (backlog persistence, worktree creation, the repair
loop, Git operations) are substituted with recording fakes so these tests
assert on wiring, call order, and gating — not on behavior already covered by
each component's own test module (`test_backlog_store.py`,
`test_worktree_manager.py`, `test_repair_loop.py`, `test_git_operator.py`,
`test_evidence_recorder.py`). One real, local, no-mocks integration test
exercises every component together against a throwaway Git repository.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import automation.orchestrator as orchestrator
from automation.backlog_store import BacklogStoreError, transition_task_status
from automation.cache_manager import CacheError
from automation.context_builder import ContextBuildError
from automation.git_operator import GitOperationError
from automation.orchestrator import run_orchestrator_cycle
from automation.providers.fake import FakeCodingProvider
from automation.schemas.cache import CACHE_MANIFEST_VERSION, CacheManifest
from automation.schemas.command import CommandOutcome, CommandPolicy, CommandRequest, CommandResult
from automation.schemas.context import ContextBuildResult
from automation.schemas.evidence_run import SecretScanRecord
from automation.schemas.git_operation import (
    CommitOperationResult,
    DraftPullRequestResult,
    GitOperationErrorCode,
    GitOperationResult,
    PushOperationResult,
)
from automation.schemas.orchestrator import OrchestratorInputs, OrchestratorOutcome
from automation.schemas.provider import (
    ProviderOperationMode,
    ProviderResponse,
    ProviderStopReason,
    ProviderUsage,
)
from automation.schemas.repair import RepairAttempt, RepairLoopResult, RepairOutcome
from automation.schemas.task import AutonomousTask, RiskLevel, TaskBacklog, TaskStatus
from automation.schemas.validation_run import (
    AggregateOutcome,
    ValidationEntryResult,
    ValidationEntryStatus,
    ValidationExecutionPolicy,
    ValidationRunResult,
)
from automation.schemas.worktree import WorktreeErrorCode, WorktreeMetadata
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
        "autonomy": {"auto_modify": True, "auto_test": True, "auto_commit": True},
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def build_backlog(*tasks: AutonomousTask) -> TaskBacklog:
    return TaskBacklog(version="qualitymesh.dev/automation/v1", tasks=tasks)


def make_worktree_metadata(task_id: str, worktree_path: str) -> WorktreeMetadata:
    return WorktreeMetadata(
        task_id=task_id,
        repository_path="unused-repo",
        worktree_path=worktree_path,
        branch_name=f"agent/{task_id}",
        base_commit="a" * 40,
    )


def make_provider_response(changed_files: tuple[str, ...] = ()) -> ProviderResponse:
    return ProviderResponse(
        provider_name="fake-provider",
        model_identifier="fake-model-1",
        text="done",
        changed_files=changed_files,
        usage=ProviderUsage(input_tokens=10, output_tokens=5),
        stop_reason=ProviderStopReason.COMPLETED,
        success=True,
    )


def make_validation_result(task_id: str, outcome: AggregateOutcome) -> ValidationRunResult:
    request = CommandRequest(executable="git", arguments=("status",), working_directory="/worktree")
    if outcome is AggregateOutcome.PASSED:
        status, exit_code = ValidationEntryStatus.PASSED, 0
    else:
        status, exit_code = ValidationEntryStatus.FAILED, 1
    command_result = CommandResult(
        executable="git",
        arguments=("status",),
        working_directory="/worktree",
        outcome=CommandOutcome.COMPLETED,
        exit_code=exit_code,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        stdout_truncated=False,
        stderr_truncated=False,
    )
    entry = ValidationEntryResult(
        index=0, request=request, status=status, command_result=command_result
    )
    return ValidationRunResult(
        task_id=task_id,
        outcome=outcome,
        results=(entry,),
        executed_count=1,
        skipped_count=0,
        duration_seconds=0.01,
    )


def make_repair_attempt(
    task_id: str,
    changed_files: tuple[str, ...] = (),
    outcome: AggregateOutcome = AggregateOutcome.PASSED,
) -> RepairAttempt:
    return RepairAttempt(
        attempt_number=1,
        mode=ProviderOperationMode.IMPLEMENT,
        response=make_provider_response(changed_files),
        validation_result=make_validation_result(task_id, outcome),
    )


def make_repair_result(
    task_id: str,
    outcome: RepairOutcome = RepairOutcome.COMPLETED,
    stop_reason: str = "validation passed",
    changed_files: tuple[str, ...] = (),
    with_attempt: bool = True,
) -> RepairLoopResult:
    attempts: tuple[RepairAttempt, ...] = ()
    if with_attempt:
        attempt_outcome = (
            AggregateOutcome.PASSED
            if outcome is RepairOutcome.COMPLETED
            else AggregateOutcome.FAILED
        )
        attempts = (
            make_repair_attempt(task_id, changed_files=changed_files, outcome=attempt_outcome),
        )
    return RepairLoopResult(
        task_id=task_id,
        outcome=outcome,
        stop_reason=stop_reason,
        attempts=attempts,
        context_expansions=(),
        total_usage=ProviderUsage(input_tokens=10, output_tokens=5),
    )


def make_inputs(tmp_path: Path, **overrides: Any) -> OrchestratorInputs:
    fields: dict[str, Any] = {
        "backlog_path": str(tmp_path / "backlog.yaml"),
        "repository_path": str(tmp_path / "repo"),
        "base_commit": "a" * 40,
        "worktree_parent": str(tmp_path / "worktrees"),
        "cache_manifest_path": str(tmp_path / "cache.json"),
        "evidence_path": str(tmp_path / "evidence.json"),
        "allowed_risk_levels": (RiskLevel.LOW,),
        "command_policy": CommandPolicy(worktree_root=str(tmp_path / "worktrees")),
        "execution_policy": ValidationExecutionPolicy(),
        "token_budget": 100_000,
        "secret_scan_command": ("scanner",),
    }
    fields.update(overrides)
    return OrchestratorInputs.model_validate(fields)


def patch_common(
    monkeypatch: pytest.MonkeyPatch,
    backlog: TaskBacklog,
    worktree_metadata: WorktreeMetadata,
    repair_result: RepairLoopResult,
    *,
    has_changes: bool = True,
    merge_state_clean: bool = True,
    diff_line_count: int = 0,
    fail_at: str | None = None,
    fake_git_operations: Any = "default",
    scan_passes: bool = True,
) -> tuple[list[str], list[TaskBacklog]]:
    """Patch every orchestrator dependency with a recording fake. Returns
    `(calls, backlog_snapshots)`: `calls` records phase names (and, for
    `apply_status_transition`, the target status) in call order;
    `backlog_snapshots` records the backlog returned by each
    `apply_status_transition` call, in order.

    `fake_git_operations`: `"default"` installs a fake that always succeeds;
    `None` leaves `automation.git_operator.run_git_operations` untouched (for
    tests exercising its own real, zero-I/O gating branches); any other
    callable is installed as-is.
    """
    calls: list[str] = []
    backlog_snapshots: list[TaskBacklog] = []

    def fake_run_secret_scan(
        command: Any, policy: Any, root: Any, records: Any, paths: Any
    ) -> SecretScanRecord:
        calls.append("run_secret_scan")
        return SecretScanRecord(
            command=tuple(command),
            exit_code=0 if scan_passes else 1,
            tree_sha256="0" * 64,
            passed=scan_passes,
        )

    monkeypatch.setattr(orchestrator, "_run_secret_scan", fake_run_secret_scan)

    def fake_load_backlog_file(path: Any) -> TaskBacklog:
        calls.append("load_backlog_file")
        return backlog

    def fake_apply_status_transition(
        path: Any, bl: TaskBacklog, task_id: str, status: TaskStatus
    ) -> TaskBacklog:
        calls.append(f"apply_status_transition:{status.value}")
        if fail_at == f"apply_status_transition:{status.value}":
            raise BacklogStoreError("boom")
        updated = transition_task_status(bl, task_id, status)
        backlog_snapshots.append(updated)
        return updated

    def fake_create_worktree(request: Any) -> WorktreeMetadata:
        calls.append("create_worktree")
        if fail_at == "create_worktree":
            raise WorktreeError("boom", code=WorktreeErrorCode.REPOSITORY_NOT_FOUND)
        return worktree_metadata

    def fake_load_manifest(path: Any) -> CacheManifest:
        calls.append("load_manifest")
        if fail_at == "load_manifest":
            raise CacheError("boom")
        return CacheManifest(version=CACHE_MANIFEST_VERSION, entries=())

    def fake_build_context(task_arg: Any, root: Any, manifest: Any) -> ContextBuildResult:
        calls.append("build_context")
        if fail_at == "build_context":
            raise ContextBuildError("boom")
        return ContextBuildResult(
            records=(), manifest=manifest, hit_count=0, miss_count=0, stale_count=0
        )

    def fake_write_manifest_atomically(path: Any, manifest: Any) -> None:
        calls.append("write_manifest_atomically")
        if fail_at == "write_manifest_atomically":
            raise OSError("boom")

    def fake_run_repair_loop(
        task: Any,
        context: Any,
        provider: Any,
        execute_attempt: Any,
        *,
        token_budget: Any,
        max_output_tokens: Any,
    ) -> RepairLoopResult:
        calls.append("run_repair_loop")
        return repair_result

    def fake_repository_has_changes(path: Any) -> bool:
        calls.append("repository_has_changes")
        return has_changes

    def fake_worktree_changed_paths(path: Any) -> tuple[str, ...]:
        if not repair_result.attempts:
            return ()
        return tuple(sorted(repair_result.attempts[-1].response.changed_files))

    def fake_has_unresolved_merge_state(path: Any) -> bool:
        calls.append("has_unresolved_merge_state")
        return not merge_state_clean

    def fake_count_diff_line_changes(path: Any, paths: Any) -> int:
        calls.append("count_diff_line_changes")
        return diff_line_count

    def default_fake_run_git_operations(*args: Any, **kwargs: Any) -> GitOperationResult:
        calls.append("run_git_operations")
        if fail_at == "run_git_operations":
            raise GitOperationError("boom", code=GitOperationErrorCode.GIT_COMMAND_FAILED)
        return GitOperationResult(
            commit=CommitOperationResult(performed=True, commit_sha="c" * 40),
            push=PushOperationResult(performed=True, branch=worktree_metadata.branch_name),
            pull_request=DraftPullRequestResult(
                performed=True, url="https://example.invalid/pr/1", number=1
            ),
        )

    monkeypatch.setattr(orchestrator, "load_backlog_file", fake_load_backlog_file)
    monkeypatch.setattr(orchestrator, "apply_status_transition", fake_apply_status_transition)
    monkeypatch.setattr(orchestrator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(orchestrator, "load_manifest", fake_load_manifest)
    monkeypatch.setattr(orchestrator, "build_context", fake_build_context)
    monkeypatch.setattr(orchestrator, "write_manifest_atomically", fake_write_manifest_atomically)
    monkeypatch.setattr(orchestrator, "run_repair_loop", fake_run_repair_loop)
    monkeypatch.setattr(orchestrator, "repository_has_changes", fake_repository_has_changes)
    monkeypatch.setattr(orchestrator, "worktree_changed_paths", fake_worktree_changed_paths)
    monkeypatch.setattr(orchestrator, "has_unresolved_merge_state", fake_has_unresolved_merge_state)
    monkeypatch.setattr(orchestrator, "count_diff_line_changes", fake_count_diff_line_changes)

    if fake_git_operations == "default":
        monkeypatch.setattr(orchestrator, "run_git_operations", default_fake_run_git_operations)
    elif fake_git_operations is not None:
        monkeypatch.setattr(orchestrator, "run_git_operations", fake_git_operations)

    return calls, backlog_snapshots


ALL_PHASES_UP_TO_REPAIR = [
    "load_backlog_file",
    "apply_status_transition:active",
    "create_worktree",
    "load_manifest",
    "build_context",
    "write_manifest_atomically",
    "run_repair_loop",
]

ALL_PHASES_COMPLETED = [
    *ALL_PHASES_UP_TO_REPAIR,
    "repository_has_changes",
    "has_unresolved_merge_state",
    "count_diff_line_changes",
    "run_secret_scan",
    "run_git_operations",
    "apply_status_transition:completed",
]


# --------------------------------------------------------------------------------------
# Successful selection and completion
# --------------------------------------------------------------------------------------


def test_successful_automatic_selection_and_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    calls, _ = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True), FakeCodingProvider()
    )

    assert result.outcome is OrchestratorOutcome.COMPLETED
    assert result.task_id == "task-a"
    assert result.starting_status is TaskStatus.READY
    assert result.final_status is TaskStatus.COMPLETED
    assert calls == ALL_PHASES_COMPLETED


def test_successful_explicit_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wanted = build_task(id="task-b", priority=1)
    higher_priority_but_not_requested = build_task(id="task-a", priority=5)
    backlog = build_backlog(higher_priority_but_not_requested, wanted)
    worktree_metadata = make_worktree_metadata(wanted.id, str(tmp_path / "worktrees" / wanted.id))
    repair_result = make_repair_result(wanted.id)
    patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, task_id="task-b", allow_git_writes=True), FakeCodingProvider()
    )

    assert result.outcome is OrchestratorOutcome.COMPLETED
    assert result.task_id == "task-b"


def test_no_eligible_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = build_task(status="blocked")
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    calls, _ = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.outcome is OrchestratorOutcome.NO_TASK_SELECTED
    assert result.final_status is None
    assert calls == ["load_backlog_file"]


def test_existing_active_task_blocks_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active_task = build_task(status="active")
    ready_task = build_task(id="task-b")
    backlog = build_backlog(active_task, ready_task)
    worktree_metadata = make_worktree_metadata("task-a", str(tmp_path / "worktrees" / "task-a"))
    repair_result = make_repair_result("task-a")
    calls, _ = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.outcome is OrchestratorOutcome.ACTIVE_TASK_EXISTS
    assert result.task_id == "task-a"
    assert calls == ["load_backlog_file"]


def test_ready_active_completed_transition_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    _, snapshots = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True), FakeCodingProvider()
    )

    assert result.starting_status is TaskStatus.READY
    assert result.final_status is TaskStatus.COMPLETED
    assert snapshots[0].tasks[0].status is TaskStatus.ACTIVE
    assert snapshots[1].tasks[0].status is TaskStatus.COMPLETED


# --------------------------------------------------------------------------------------
# Terminal failures map to ACTIVE -> BLOCKED
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("repair_outcome", "expected_outcome"),
    [
        (RepairOutcome.ATTEMPTS_EXHAUSTED, OrchestratorOutcome.VALIDATION_FAILED),
        (RepairOutcome.REPEATED_FAILURE, OrchestratorOutcome.VALIDATION_FAILED),
        (RepairOutcome.PROVIDER_ERROR, OrchestratorOutcome.PROVIDER_FAILED),
        (RepairOutcome.BUDGET_EXCEEDED, OrchestratorOutcome.BUDGET_EXCEEDED),
        (RepairOutcome.POLICY_VIOLATION, OrchestratorOutcome.POLICY_VIOLATION),
        (RepairOutcome.EXECUTION_ERROR, OrchestratorOutcome.SETUP_FAILED),
    ],
)
def test_repair_failure_produces_active_to_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repair_outcome: RepairOutcome,
    expected_outcome: OrchestratorOutcome,
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id, outcome=repair_outcome, with_attempt=False)
    calls, snapshots = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.outcome is expected_outcome
    assert result.final_status is TaskStatus.BLOCKED
    assert snapshots[-1].tasks[0].status is TaskStatus.BLOCKED
    assert "repository_has_changes" not in calls
    assert "run_git_operations" not in calls


def test_evidence_failure_produces_active_to_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    # The changed file the provider reports does not exist under the
    # worktree, so hashing it for the evidence package fails.
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id, changed_files=("missing.txt",))
    calls, _ = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.outcome is OrchestratorOutcome.EVIDENCE_FAILED
    assert result.final_status is TaskStatus.BLOCKED
    assert "run_git_operations" not in calls


def test_git_operation_failure_blocks_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    patch_common(
        monkeypatch, backlog, worktree_metadata, repair_result, fail_at="run_git_operations"
    )

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True), FakeCodingProvider()
    )

    assert result.outcome is OrchestratorOutcome.GIT_OPERATION_FAILED
    assert result.final_status is TaskStatus.BLOCKED
    assert result.evidence_result is not None  # evidence was already recorded


# --------------------------------------------------------------------------------------
# Setup failures short-circuit before the repair loop
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fail_at",
    ["create_worktree", "load_manifest", "build_context", "write_manifest_atomically"],
)
def test_setup_failure_stops_before_repair_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    calls, _ = patch_common(monkeypatch, backlog, worktree_metadata, repair_result, fail_at=fail_at)

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.outcome is OrchestratorOutcome.SETUP_FAILED
    assert result.final_status is TaskStatus.BLOCKED
    assert "run_repair_loop" not in calls
    assert "run_git_operations" not in calls


def test_active_transition_backlog_store_error_maps_to_setup_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    calls, _ = patch_common(
        monkeypatch,
        backlog,
        worktree_metadata,
        repair_result,
        fail_at="apply_status_transition:active",
    )

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.outcome is OrchestratorOutcome.SETUP_FAILED
    assert result.final_status is None
    assert "create_worktree" not in calls


def test_atomic_backlog_write_failure_maps_to_setup_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)

    def raising_apply(path: Any, bl: TaskBacklog, task_id: str, status: TaskStatus) -> TaskBacklog:
        if status is TaskStatus.ACTIVE:
            raise OSError("disk full")
        return transition_task_status(bl, task_id, status)

    def fake_load_backlog_file(path: Any) -> TaskBacklog:
        return backlog

    monkeypatch.setattr(orchestrator, "load_backlog_file", fake_load_backlog_file)
    monkeypatch.setattr(orchestrator, "apply_status_transition", raising_apply)

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.outcome is OrchestratorOutcome.SETUP_FAILED
    assert "disk full" in result.reason


# --------------------------------------------------------------------------------------
# Git-write gating
# --------------------------------------------------------------------------------------


def test_auto_commit_disabled_still_completes_without_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task(autonomy={"auto_modify": True, "auto_test": True, "auto_commit": False})
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    # `fake_git_operations=None` leaves the real, zero-I/O
    # `automation.git_operator.run_git_operations` in place: its own
    # `auto_commit` early-return never touches Git, so it's safe to exercise
    # for real here.
    patch_common(monkeypatch, backlog, worktree_metadata, repair_result, fake_git_operations=None)

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True), FakeCodingProvider()
    )

    assert result.outcome is OrchestratorOutcome.COMPLETED
    assert result.git_operation_result is not None
    assert result.git_operation_result.commit.performed is False
    assert result.git_operation_result.push.performed is False
    assert result.git_operation_result.pull_request.performed is False
    assert "auto_commit is disabled" in (result.git_operation_result.commit.reason or "")


def test_caller_git_writes_disabled_skips_git_operator_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)

    def forbidden_run_git_operations(*args: Any, **kwargs: Any) -> GitOperationResult:
        raise AssertionError("git_operator.run_git_operations must never be called")

    patch_common(
        monkeypatch,
        backlog,
        worktree_metadata,
        repair_result,
        fake_git_operations=forbidden_run_git_operations,
    )

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=False), FakeCodingProvider()
    )

    assert result.outcome is OrchestratorOutcome.COMPLETED
    assert result.git_operation_result is not None
    assert result.git_operation_result.commit.performed is False
    assert result.git_operation_result.push.performed is False
    assert result.git_operation_result.pull_request.performed is False
    assert "did not allow" in (result.git_operation_result.commit.reason or "")


def test_missing_secret_scan_attestation_blocks_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    patch_common(
        monkeypatch,
        backlog,
        worktree_metadata,
        repair_result,
        has_changes=True,
        fake_git_operations=None,
    )

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True, secret_scan_command=None),
        FakeCodingProvider(),
    )

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED
    assert result.evidence_result is not None
    assert result.evidence_result.precondition_result.eligible is False
    assert any(
        violation.value == "secret-scan-blocked"
        for violation in result.evidence_result.precondition_result.violations
    )
    assert result.git_operation_result is None


def test_successful_permitted_commit_and_draft_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task(
        autonomy={"auto_modify": True, "auto_test": True, "auto_commit": True, "auto_open_pr": True}
    )
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    calls, _ = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True), FakeCodingProvider()
    )

    assert result.outcome is OrchestratorOutcome.COMPLETED
    assert result.git_operation_result is not None
    assert result.git_operation_result.commit.performed is True
    assert result.git_operation_result.pull_request.performed is True
    assert "run_git_operations" in calls


def test_auto_merge_is_always_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    # Real `run_git_operations` rejects `auto_merge` before any Git command
    # runs, so it's safe to exercise for real even against a fake worktree.
    patch_common(monkeypatch, backlog, worktree_metadata, repair_result, fake_git_operations=None)

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True), FakeCodingProvider()
    )

    assert result.outcome is OrchestratorOutcome.GIT_OPERATION_FAILED
    assert result.final_status is TaskStatus.BLOCKED


# --------------------------------------------------------------------------------------
# Isolation, determinism, no mutation
# --------------------------------------------------------------------------------------


def test_only_the_selected_task_is_touched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = build_task(id="task-a")
    other = build_task(id="task-b")
    backlog = build_backlog(selected, other)
    worktree_metadata = make_worktree_metadata("task-a", str(tmp_path / "worktrees" / "task-a"))
    repair_result = make_repair_result("task-a")
    _, snapshots = patch_common(monkeypatch, backlog, worktree_metadata, repair_result)

    result = run_orchestrator_cycle(make_inputs(tmp_path), FakeCodingProvider())

    assert result.task_id == "task-a"
    final_backlog = snapshots[-1]
    assert (
        final_backlog.tasks[0].id != "task-b" or final_backlog.tasks[0].status is TaskStatus.READY
    )
    other_task = next(t for t in final_backlog.tasks if t.id == "task-b")
    assert other_task.status is TaskStatus.READY


def test_deterministic_run_result_across_identical_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))

    def run_once() -> Any:
        repair_result = make_repair_result(task.id)
        patch_common(monkeypatch, backlog, worktree_metadata, repair_result)
        return run_orchestrator_cycle(
            make_inputs(tmp_path, allow_git_writes=True), FakeCodingProvider()
        )

    first = run_once()
    second = run_once()

    assert first.task_id == second.task_id
    assert first.outcome == second.outcome
    assert first.final_status == second.final_status
    assert first.reason == second.reason
    assert first.run_id != second.run_id


def test_inputs_and_task_are_not_mutated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    patch_common(monkeypatch, backlog, worktree_metadata, repair_result)
    inputs = make_inputs(tmp_path, allow_git_writes=True)
    inputs_before = copy.deepcopy(inputs)
    task_before = copy.deepcopy(task)

    run_orchestrator_cycle(inputs, FakeCodingProvider())

    assert inputs == inputs_before
    assert task == task_before


# --------------------------------------------------------------------------------------
# Harmless local integration test (no mocks)
# --------------------------------------------------------------------------------------


SCAN_MARKER = "PLANTED-FAKE-SECRET-MARKER"
_SCANNER_SOURCE = (
    "import pathlib, sys\n"
    "for f in pathlib.Path('.').rglob('*'):\n"
    "    if '.git' in f.parts or not f.is_file():\n"
    "        continue\n"
    f"    if {SCAN_MARKER!r} in f.read_text(errors='ignore'):\n"
    "        sys.exit(1)\n"
)


def real_scanner(tmp_path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A deterministic stand-in scanner (exit 1 when the marker is present)
    kept outside any worktree. Returns `(command, extra_allowed_executables)`."""
    script = tmp_path / "scanner.py"
    script.write_text(_SCANNER_SOURCE, encoding="utf-8")
    return ("python3", str(script)), ("python3",)


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
    scan_command, scan_exes = real_scanner(tmp_path)

    task = build_task(
        id="integration-task",
        context_files=["README.md"],
        validation_commands=["git status"],
        allowed_paths=["README.md"],
    )
    backlog_path = tmp_path / "backlog.yaml"
    backlog_path.write_text(
        "version: qualitymesh.dev/automation/v1\n"
        "tasks:\n"
        "  - id: integration-task\n"
        "    title: Task A\n"
        "    status: ready\n"
        "    priority: 1\n"
        "    risk: low\n"
        "    objective: Do something.\n"
        "    allowed_paths: [README.md]\n"
        "    context_files: [README.md]\n"
        "    acceptance_criteria: [It works]\n"
        "    validation_commands: [git status]\n"
        "    autonomy:\n"
        "      auto_modify: true\n"
        "      auto_test: true\n"
        "      auto_commit: true\n",
        encoding="utf-8",
    )

    inputs = OrchestratorInputs(
        backlog_path=str(backlog_path),
        repository_path=str(repo_path),
        base_commit=commit,
        worktree_parent=str(worktree_parent),
        cache_manifest_path=str(tmp_path / "cache.json"),
        evidence_path=str(tmp_path / "evidence.json"),
        allowed_risk_levels=(RiskLevel.LOW,),
        command_policy=CommandPolicy(
            worktree_root=str(worktree_parent / task.id), allowed_executables=("git", *scan_exes)
        ),
        execution_policy=ValidationExecutionPolicy(),
        token_budget=100_000,
        allow_git_writes=True,
        secret_scan_command=scan_command,
    )

    class EditingProvider(FakeCodingProvider):
        def execute(self, request: Any) -> ProviderResponse:
            target = worktree_parent / task.id / "README.md"
            target.write_text("hello\nedited\n", encoding="utf-8")
            return super().execute(request)

    provider = EditingProvider(response=make_provider_response(("README.md",)))
    result = run_orchestrator_cycle(inputs, provider)

    assert result.outcome is OrchestratorOutcome.COMPLETED, result.reason
    assert result.task_id == "integration-task"
    assert result.final_status is TaskStatus.COMPLETED
    assert result.repair_result is not None
    assert result.repair_result.outcome is RepairOutcome.COMPLETED
    assert result.evidence_result is not None
    assert Path(inputs.evidence_path).exists()
    reloaded_backlog = TaskBacklog.model_validate(
        yaml.safe_load(backlog_path.read_text(encoding="utf-8"))
    )
    assert reloaded_backlog.tasks[0].status is TaskStatus.COMPLETED
    # `auto_open_pr` was never granted, so no push/PR was attempted.
    assert result.git_operation_result is not None
    assert result.git_operation_result.commit.performed is True
    assert result.git_operation_result.push.performed is False

    # Same cycle with no real edit: nothing to commit, so it must not complete.
    (backlog_path).write_text(
        backlog_path.read_text(encoding="utf-8").replace("status: completed", "status: ready"),
        encoding="utf-8",
    )
    shutil.rmtree(worktree_parent / task.id, ignore_errors=True)
    run_git(["worktree", "prune"], repo_path)
    run_git(["branch", "-D", "agent/integration-task"], repo_path)
    no_change = run_orchestrator_cycle(
        inputs, FakeCodingProvider(response=make_provider_response())
    )
    assert no_change.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED
    assert no_change.final_status is TaskStatus.BLOCKED


# --------------------------------------------------------------------------------------
# Regression: a skipped commit must never be reported as a completed task
# --------------------------------------------------------------------------------------


def test_unmet_commit_preconditions_do_not_complete_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entry point `run_orchestrator_cycle`, git writes requested, no scan
    attestation: the commit is skipped, so the task must end BLOCKED, never
    COMPLETED (which unblocks dependents)."""
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)
    calls, snapshots = patch_common(
        monkeypatch,
        backlog,
        worktree_metadata,
        repair_result,
        has_changes=True,
        fake_git_operations=None,
    )

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True, secret_scan_command=None),
        FakeCodingProvider(),
    )

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED
    assert result.final_status is TaskStatus.BLOCKED
    assert "apply_status_transition:completed" not in calls
    assert snapshots[-1].tasks[0].status is TaskStatus.BLOCKED
    assert result.evidence_result is not None
    assert result.evidence_result.precondition_result.eligible is False


def test_commit_skipped_by_git_operator_does_not_complete_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preconditions pass but the git operator declines to commit (e.g. staged
    scope mismatch): must be GIT_OPERATION_FAILED / BLOCKED, not COMPLETED."""
    task = build_task()
    backlog = build_backlog(task)
    worktree_metadata = make_worktree_metadata(task.id, str(tmp_path / "worktrees" / task.id))
    repair_result = make_repair_result(task.id)

    def declining_git_operations(*args: Any, **kwargs: Any) -> GitOperationResult:
        return GitOperationResult(
            commit=CommitOperationResult(performed=False, reason="staged scope mismatch"),
            push=PushOperationResult(performed=False, reason="commit was not performed"),
            pull_request=DraftPullRequestResult(performed=False, reason="commit was not performed"),
        )

    calls, _ = patch_common(
        monkeypatch,
        backlog,
        worktree_metadata,
        repair_result,
        has_changes=True,
        fake_git_operations=declining_git_operations,
    )

    result = run_orchestrator_cycle(
        make_inputs(tmp_path, allow_git_writes=True),
        FakeCodingProvider(),
    )

    assert result.outcome is OrchestratorOutcome.GIT_OPERATION_FAILED
    assert result.final_status is TaskStatus.BLOCKED
    assert "apply_status_transition:completed" not in calls


def test_unreported_worktree_edit_blocks_commit(tmp_path: Path) -> None:
    """Provider edits README.md (reported) and notes.txt (unreported). The
    validated tree differs from what would be committed, so the cycle must not
    complete or commit."""
    repo_path = tmp_path / "repo"
    commit = init_repo(repo_path)
    worktree_parent = tmp_path / "worktrees"
    worktree_parent.mkdir()
    scan_command, scan_exes = real_scanner(tmp_path)
    backlog_path = tmp_path / "backlog.yaml"
    backlog_path.write_text(
        "version: qualitymesh.dev/automation/v1\n"
        "tasks:\n"
        "  - id: scope-task\n"
        "    title: Task A\n"
        "    status: ready\n"
        "    priority: 1\n"
        "    risk: low\n"
        "    objective: Do something.\n"
        "    allowed_paths: [README.md]\n"
        "    acceptance_criteria: [It works]\n"
        "    validation_commands: [git status]\n"
        "    autonomy:\n"
        "      auto_modify: true\n"
        "      auto_test: true\n"
        "      auto_commit: true\n",
        encoding="utf-8",
    )
    inputs = OrchestratorInputs(
        backlog_path=str(backlog_path),
        repository_path=str(repo_path),
        base_commit=commit,
        worktree_parent=str(worktree_parent),
        cache_manifest_path=str(tmp_path / "cache.json"),
        evidence_path=str(tmp_path / "evidence.json"),
        allowed_risk_levels=(RiskLevel.LOW,),
        command_policy=CommandPolicy(
            worktree_root=str(worktree_parent / "scope-task"),
            allowed_executables=("git", *scan_exes),
        ),
        execution_policy=ValidationExecutionPolicy(),
        token_budget=100_000,
        allow_git_writes=True,
        secret_scan_command=scan_command,
    )

    class SneakyProvider(FakeCodingProvider):
        def execute(self, request: Any) -> ProviderResponse:
            root = worktree_parent / "scope-task"
            (root / "README.md").write_text("hello\nedited\n", encoding="utf-8")
            (root / "notes.txt").write_text("unreported\n", encoding="utf-8")
            return super().execute(request)

    result = run_orchestrator_cycle(
        inputs, SneakyProvider(response=make_provider_response(("README.md",)))
    )

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED
    assert result.final_status is TaskStatus.BLOCKED
    assert "notes.txt" in result.reason
    assert run_git(["rev-list", "--count", "agent/scope-task"], repo_path) == "1"

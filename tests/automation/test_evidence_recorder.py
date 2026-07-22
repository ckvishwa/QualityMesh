"""Unit tests for evidence-package assembly and pre-commit verification
(QM-AUTO-012)."""

from __future__ import annotations

import copy
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import automation.evidence_recorder as evidence_recorder
from automation.evidence_recorder import (
    EvidenceRecorderError,
    build_evidence_package,
    load_evidence,
    serialize_evidence,
    verify_commit_preconditions,
    write_evidence_atomically,
)
from automation.schemas.command import CommandOutcome, CommandRequest, CommandResult
from automation.schemas.evidence_run import ChangedFileRecord, CommitPreconditionCode
from automation.schemas.provider import (
    ProviderOperationMode,
    ProviderResponse,
    ProviderStopReason,
    ProviderUsage,
)
from automation.schemas.repair import RepairAttempt
from automation.schemas.task import AutonomousTask
from automation.schemas.validation_run import (
    AggregateOutcome,
    ValidationEntryResult,
    ValidationEntryStatus,
    ValidationRunResult,
)

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
        "forbidden_paths": ["automation/secrets/"],
        "acceptance_criteria": ["It works"],
        "validation_commands": ["ruff check ."],
        "limits": {"max_files_changed": 3, "max_diff_lines": 50},
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def build_changed_file(path: str, size: int = 5) -> ChangedFileRecord:
    return ChangedFileRecord(path=path, sha256="a" * 64, size_bytes=size)


def build_repair_attempt() -> RepairAttempt:
    response = ProviderResponse(
        provider_name="fake",
        model_identifier="fake-model-1",
        text="done",
        changed_files=("automation/foo.py",),
        usage=ProviderUsage(input_tokens=10, output_tokens=5),
        stop_reason=ProviderStopReason.COMPLETED,
        success=True,
    )
    cmd_request = CommandRequest(
        executable="ruff", arguments=("check", "."), working_directory="/worktree"
    )
    command_result = CommandResult(
        executable="ruff",
        arguments=("check", "."),
        working_directory="/worktree",
        outcome=CommandOutcome.COMPLETED,
        exit_code=0,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        stdout_truncated=False,
        stderr_truncated=False,
    )
    entry_result = ValidationEntryResult(
        index=0,
        request=cmd_request,
        status=ValidationEntryStatus.PASSED,
        command_result=command_result,
    )
    validation_result = ValidationRunResult(
        task_id="task-a",
        outcome=AggregateOutcome.PASSED,
        results=(entry_result,),
        executed_count=1,
        skipped_count=0,
        duration_seconds=0.01,
    )
    return RepairAttempt(
        attempt_number=1,
        mode=ProviderOperationMode.IMPLEMENT,
        response=response,
        validation_result=validation_result,
    )


def build_package(task: AutonomousTask, worktree_path: Path, **overrides: Any):
    fields: dict[str, Any] = {
        "worktree_path": str(worktree_path),
        "changed_paths": ["automation/foo.py"],
        "base_commit": "a" * 40,
        "result_commit": "b" * 40,
        "branch": "agent/task-a",
        "context_files_supplied": ["automation/foo.py"],
        "provider_name": "fake",
        "model_identifier": "fake-model-1",
        "provider_usage": ProviderUsage(input_tokens=10, output_tokens=5),
        "attempts": [build_repair_attempt()],
        "precondition_result": verify_commit_preconditions(
            task,
            [build_changed_file("automation/foo.py")],
            diff_line_count=5,
            validation_passed=True,
            has_changes=True,
            merge_state_clean=True,
            secret_scan_clean=True,
        ),
        "final_outcome": "completed",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return build_evidence_package(task, **fields)


# --------------------------------------------------------------------------------------
# Commit preconditions
# --------------------------------------------------------------------------------------


def test_all_preconditions_ok_is_eligible() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [build_changed_file("automation/foo.py")],
        diff_line_count=5,
        validation_passed=True,
        has_changes=True,
        merge_state_clean=True,
        secret_scan_clean=True,
    )

    assert result.eligible is True
    assert result.violations == ()


def test_allowed_path_enforcement() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [build_changed_file("src/qualitymesh/other.py")],
        diff_line_count=5,
        validation_passed=True,
        has_changes=True,
        merge_state_clean=True,
        secret_scan_clean=True,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.PATH_OUTSIDE_ALLOWED in result.violations


def test_forbidden_path_rejection() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [build_changed_file("automation/secrets/keys.py")],
        diff_line_count=5,
        validation_passed=True,
        has_changes=True,
        merge_state_clean=True,
        secret_scan_clean=True,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.FORBIDDEN_PATH_MATCH in result.violations


def test_file_count_limit_enforcement() -> None:
    task = build_task(limits={"max_files_changed": 1, "max_diff_lines": 50})
    result = verify_commit_preconditions(
        task,
        [build_changed_file("automation/a.py"), build_changed_file("automation/b.py")],
        diff_line_count=5,
        validation_passed=True,
        has_changes=True,
        merge_state_clean=True,
        secret_scan_clean=True,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.FILE_COUNT_EXCEEDED in result.violations


def test_diff_line_limit_enforcement() -> None:
    task = build_task(limits={"max_files_changed": 5, "max_diff_lines": 10})
    result = verify_commit_preconditions(
        task,
        [build_changed_file("automation/foo.py")],
        diff_line_count=11,
        validation_passed=True,
        has_changes=True,
        merge_state_clean=True,
        secret_scan_clean=True,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.DIFF_LINE_LIMIT_EXCEEDED in result.violations


def test_validation_required_behavior() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [build_changed_file("automation/foo.py")],
        diff_line_count=5,
        validation_passed=False,
        has_changes=True,
        merge_state_clean=True,
        secret_scan_clean=True,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.VALIDATION_NOT_PASSED in result.violations


def test_no_change_rejection() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [],
        diff_line_count=0,
        validation_passed=True,
        has_changes=False,
        merge_state_clean=True,
        secret_scan_clean=True,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.NO_CHANGES in result.violations


def test_unresolved_merge_state_rejection() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [build_changed_file("automation/foo.py")],
        diff_line_count=5,
        validation_passed=True,
        has_changes=True,
        merge_state_clean=False,
        secret_scan_clean=True,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.UNRESOLVED_MERGE_STATE in result.violations


def test_secret_scan_blocker_rejection() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [build_changed_file("automation/foo.py")],
        diff_line_count=5,
        validation_passed=True,
        has_changes=True,
        merge_state_clean=True,
        secret_scan_clean=False,
    )

    assert result.eligible is False
    assert CommitPreconditionCode.SECRET_SCAN_BLOCKED in result.violations


def test_multiple_violations_all_reported() -> None:
    task = build_task()
    result = verify_commit_preconditions(
        task,
        [],
        diff_line_count=0,
        validation_passed=False,
        has_changes=False,
        merge_state_clean=False,
        secret_scan_clean=False,
    )

    assert CommitPreconditionCode.VALIDATION_NOT_PASSED in result.violations
    assert CommitPreconditionCode.NO_CHANGES in result.violations
    assert CommitPreconditionCode.UNRESOLVED_MERGE_STATE in result.violations
    assert CommitPreconditionCode.SECRET_SCAN_BLOCKED in result.violations


# --------------------------------------------------------------------------------------
# Changed-file hashing and evidence assembly
# --------------------------------------------------------------------------------------


def test_changed_file_hashing(tmp_path: Path) -> None:
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "foo.py").write_bytes(b"print(1)\n")
    task = build_task()

    package = build_package(task, tmp_path)

    assert len(package.changed_files) == 1
    record = package.changed_files[0]
    assert record.path == "automation/foo.py"
    assert record.size_bytes == len(b"print(1)\n")
    assert len(record.sha256) == 64


def test_missing_changed_file_raises(tmp_path: Path) -> None:
    task = build_task()

    with pytest.raises(EvidenceRecorderError):
        build_package(task, tmp_path)


# --------------------------------------------------------------------------------------
# Deterministic serialization and atomic writing
# --------------------------------------------------------------------------------------


def test_deterministic_evidence_serialization(tmp_path: Path) -> None:
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "foo.py").write_bytes(b"print(1)\n")
    task = build_task()

    package_one = build_package(task, tmp_path)
    package_two = build_package(task, tmp_path)

    assert serialize_evidence(package_one) == serialize_evidence(package_two)


def test_atomic_evidence_write_and_load_round_trip(tmp_path: Path) -> None:
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "foo.py").write_bytes(b"print(1)\n")
    task = build_task()
    package = build_package(task, tmp_path)
    evidence_path = tmp_path / "evidence.json"

    write_evidence_atomically(evidence_path, package)
    loaded = load_evidence(evidence_path)

    assert loaded == package


def test_atomic_write_failure_preserves_old_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "foo.py").write_bytes(b"print(1)\n")
    task = build_task()
    package = build_package(task, tmp_path)
    evidence_path = tmp_path / "evidence.json"

    write_evidence_atomically(evidence_path, package)
    original_bytes = evidence_path.read_bytes()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated failure during replace")

    monkeypatch.setattr(evidence_recorder.os, "replace", boom)

    with pytest.raises(OSError, match="simulated failure"):
        write_evidence_atomically(evidence_path, package)

    assert evidence_path.read_bytes() == original_bytes
    leftover_tmp_files = list(tmp_path.glob(".evidence.json.*.tmp"))
    assert leftover_tmp_files == []


def test_malformed_evidence_document_is_rejected(tmp_path: Path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(EvidenceRecorderError, match="content is not valid JSON"):
        load_evidence(evidence_path)


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_no_input_mutation(tmp_path: Path) -> None:
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "foo.py").write_bytes(b"print(1)\n")
    task = build_task()
    task_before = copy.deepcopy(task)

    build_package(task, tmp_path)

    assert task == task_before

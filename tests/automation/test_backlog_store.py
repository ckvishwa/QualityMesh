"""Unit tests for deterministic, atomic backlog status transitions
(QM-AUTO-013)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.backlog_store import (
    BacklogStoreError,
    apply_status_transition,
    transition_task_status,
    write_backlog_atomically,
)
from automation.schemas.task import TaskBacklog, TaskStatus
from automation.task_loader import load_backlog_file

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_task(**overrides: Any) -> dict[str, Any]:
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
    return task


def build_backlog(*tasks: dict[str, Any]) -> TaskBacklog:
    return TaskBacklog.model_validate(
        {"version": "qualitymesh.dev/automation/v1", "tasks": list(tasks)}
    )


# --------------------------------------------------------------------------------------
# transition_task_status: pure transitions
# --------------------------------------------------------------------------------------


def test_ready_to_active_is_permitted() -> None:
    backlog = build_backlog(build_task())

    updated = transition_task_status(backlog, "task-a", TaskStatus.ACTIVE)

    assert updated.tasks[0].status is TaskStatus.ACTIVE


def test_active_to_completed_is_permitted() -> None:
    backlog = build_backlog(build_task(status="active"))

    updated = transition_task_status(backlog, "task-a", TaskStatus.COMPLETED)

    assert updated.tasks[0].status is TaskStatus.COMPLETED


def test_active_to_blocked_is_permitted() -> None:
    backlog = build_backlog(build_task(status="active"))

    updated = transition_task_status(backlog, "task-a", TaskStatus.BLOCKED)

    assert updated.tasks[0].status is TaskStatus.BLOCKED


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("ready", TaskStatus.COMPLETED),
        ("ready", TaskStatus.BLOCKED),
        ("ready", TaskStatus.READY),
        ("active", TaskStatus.ACTIVE),
        ("active", TaskStatus.READY),
        ("completed", TaskStatus.ACTIVE),
        ("completed", TaskStatus.READY),
        ("blocked", TaskStatus.ACTIVE),
        ("blocked", TaskStatus.COMPLETED),
    ],
)
def test_invalid_transitions_are_rejected(current: str, target: TaskStatus) -> None:
    backlog = build_backlog(build_task(status=current))

    with pytest.raises(BacklogStoreError, match="not permitted"):
        transition_task_status(backlog, "task-a", target)


def test_missing_task_id_is_rejected() -> None:
    backlog = build_backlog(build_task())

    with pytest.raises(BacklogStoreError, match="not found"):
        transition_task_status(backlog, "no-such-task", TaskStatus.ACTIVE)


def test_transition_never_mutates_the_input_backlog() -> None:
    backlog = build_backlog(build_task())
    before = copy.deepcopy(backlog)

    transition_task_status(backlog, "task-a", TaskStatus.ACTIVE)

    assert backlog == before


def test_transition_preserves_every_other_task_field() -> None:
    backlog = build_backlog(
        build_task(
            title="Original Title",
            objective="Original objective.",
            context_files=["README.md"],
            forbidden_paths=["secrets/"],
            acceptance_criteria=["A", "B"],
            validation_commands=["git status", "git log"],
        )
    )

    updated = transition_task_status(backlog, "task-a", TaskStatus.ACTIVE)

    original_task = backlog.tasks[0]
    updated_task = updated.tasks[0]
    assert updated_task.title == original_task.title
    assert updated_task.objective == original_task.objective
    assert updated_task.context_files == original_task.context_files
    assert updated_task.forbidden_paths == original_task.forbidden_paths
    assert updated_task.acceptance_criteria == original_task.acceptance_criteria
    assert updated_task.validation_commands == original_task.validation_commands
    assert updated_task.priority == original_task.priority
    assert updated_task.risk == original_task.risk


def test_transition_leaves_other_tasks_untouched() -> None:
    backlog = build_backlog(build_task(id="task-a"), build_task(id="task-b"))

    updated = transition_task_status(backlog, "task-a", TaskStatus.ACTIVE)

    other = next(task for task in updated.tasks if task.id == "task-b")
    assert other.status is TaskStatus.READY


# --------------------------------------------------------------------------------------
# write_backlog_atomically / apply_status_transition: real I/O
# --------------------------------------------------------------------------------------


def test_write_backlog_atomically_round_trips_through_load_backlog_file(tmp_path: Path) -> None:
    backlog = build_backlog(build_task())
    path = tmp_path / "backlog.yaml"

    write_backlog_atomically(path, backlog)
    reloaded = load_backlog_file(path)

    assert reloaded.tasks[0].id == "task-a"
    assert reloaded.tasks[0].status is TaskStatus.READY


def test_write_backlog_atomically_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    backlog = build_backlog(build_task())
    path = tmp_path / "backlog.yaml"

    write_backlog_atomically(path, backlog)

    leftover = [entry for entry in tmp_path.iterdir() if entry.name != "backlog.yaml"]
    assert leftover == []


def test_write_backlog_atomically_never_leaves_a_partial_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "backlog.yaml"
    path.write_text("original content\n", encoding="utf-8")
    backlog = build_backlog(build_task())

    def exploding_safe_dump(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("automation.backlog_store.yaml.safe_dump", exploding_safe_dump)

    with pytest.raises(RuntimeError, match="boom"):
        write_backlog_atomically(path, backlog)

    assert path.read_text(encoding="utf-8") == "original content\n"
    leftover = [entry for entry in tmp_path.iterdir() if entry.name != "backlog.yaml"]
    assert leftover == []


def test_write_backlog_atomically_raises_oserror_for_an_unwritable_directory(
    tmp_path: Path,
) -> None:
    backlog = build_backlog(build_task())
    # A path whose parent is a *file*, not a directory, can never be created.
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("x", encoding="utf-8")
    path = blocking_file / "backlog.yaml"

    with pytest.raises(OSError):
        write_backlog_atomically(path, backlog)


def test_apply_status_transition_persists_and_returns_the_updated_backlog(
    tmp_path: Path,
) -> None:
    backlog = build_backlog(build_task())
    path = tmp_path / "backlog.yaml"
    write_backlog_atomically(path, backlog)

    updated = apply_status_transition(path, backlog, "task-a", TaskStatus.ACTIVE)

    assert updated.tasks[0].status is TaskStatus.ACTIVE
    reloaded = load_backlog_file(path)
    assert reloaded.tasks[0].status is TaskStatus.ACTIVE


def test_apply_status_transition_writes_nothing_for_an_invalid_transition(
    tmp_path: Path,
) -> None:
    backlog = build_backlog(build_task())
    path = tmp_path / "backlog.yaml"
    write_backlog_atomically(path, backlog)
    original_bytes = path.read_bytes()

    with pytest.raises(BacklogStoreError):
        apply_status_transition(path, backlog, "task-a", TaskStatus.COMPLETED)

    assert path.read_bytes() == original_bytes


def test_apply_status_transition_stop_immediately_on_persistence_failure(
    tmp_path: Path,
) -> None:
    backlog = build_backlog(build_task())
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("x", encoding="utf-8")
    path = blocking_file / "backlog.yaml"

    with pytest.raises(OSError):
        apply_status_transition(path, backlog, "task-a", TaskStatus.ACTIVE)


def test_written_backlog_document_is_plain_yaml(tmp_path: Path) -> None:
    backlog = build_backlog(build_task())
    path = tmp_path / "backlog.yaml"

    write_backlog_atomically(path, backlog)

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["version"] == "qualitymesh.dev/automation/v1"
    assert document["tasks"][0]["id"] == "task-a"

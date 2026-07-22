"""Unit tests for the autonomous-task schema and backlog loader (QM-AUTO-001)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh` (see task scope), and no conftest.py
# is in scope to add it another way, so the test module adds it itself.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.schemas.task import AutonomousTask, TaskBacklog
from automation.task_loader import BacklogLoadError, load_backlog_file

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_task(**overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": "QM-AUTO-001",
        "title": "Build task schema",
        "status": "ready",
        "priority": 1,
        "risk": "low",
        "objective": "Define strict task schema and loader.",
        "allowed_paths": ["automation/"],
        "acceptance_criteria": ["Schema rejects unknown fields"],
        "validation_commands": ["uv run pytest tests/automation -q"],
    }
    task.update(overrides)
    return task


def build_backlog(*tasks: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "qualitymesh.dev/automation/v1",
        "tasks": list(tasks) or [build_task()],
    }


def write_backlog(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "backlog.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# 1. A valid backlog loads successfully.
# --------------------------------------------------------------------------------------


def test_valid_backlog_loads_successfully(tmp_path: Path) -> None:
    path = write_backlog(tmp_path, yaml.safe_dump(build_backlog()))
    backlog = load_backlog_file(path)

    assert isinstance(backlog, TaskBacklog)
    assert len(backlog.tasks) == 1
    assert backlog.tasks[0].id == "QM-AUTO-001"
    assert backlog.tasks[0].autonomy.auto_modify is False
    assert backlog.tasks[0].limits.max_attempts == 3


# --------------------------------------------------------------------------------------
# Schema-level negative tests (2-18, 23-24)
# --------------------------------------------------------------------------------------


def test_unknown_task_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(unexpected_field="nope"))


def test_unknown_backlog_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskBacklog.model_validate({**build_backlog(), "extra": "nope"})


@pytest.mark.parametrize(
    "field", ["id", "title", "status", "priority", "risk", "objective", "allowed_paths"]
)
def test_missing_required_fields_are_rejected(field: str) -> None:
    task = build_task()
    del task[field]
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(task)


def test_invalid_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(status="in-progress"))


def test_invalid_risk_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(risk="critical"))


def test_empty_task_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(id="   "))


def test_empty_title_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(title=""))


def test_empty_objective_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(objective=""))


def test_empty_allowed_paths_is_rejected() -> None:
    with pytest.raises(ValidationError, match="allowed_paths must contain at least one path"):
        AutonomousTask.model_validate(build_task(allowed_paths=[]))


def test_empty_acceptance_criteria_is_rejected() -> None:
    with pytest.raises(ValidationError, match="acceptance_criteria must contain at least one item"):
        AutonomousTask.model_validate(build_task(acceptance_criteria=[]))


def test_empty_validation_commands_is_rejected() -> None:
    with pytest.raises(
        ValidationError, match="validation_commands must contain at least one command"
    ):
        AutonomousTask.model_validate(build_task(validation_commands=[]))


def test_high_risk_auto_merge_is_rejected() -> None:
    with pytest.raises(
        ValidationError, match="high-risk tasks must not have auto_merge set to true"
    ):
        AutonomousTask.model_validate(
            build_task(
                risk="high",
                autonomy={
                    "auto_modify": True,
                    "auto_test": True,
                    "auto_commit": True,
                    "auto_open_pr": True,
                    "auto_merge": True,
                },
            )
        )


def test_auto_merge_without_auto_open_pr_is_rejected() -> None:
    with pytest.raises(ValidationError, match="auto_merge requires auto_open_pr to also be true"):
        AutonomousTask.model_validate(
            build_task(autonomy={"auto_commit": True, "auto_merge": True})
        )


def test_auto_open_pr_without_auto_commit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="auto_open_pr requires auto_commit to also be true"):
        AutonomousTask.model_validate(build_task(autonomy={"auto_open_pr": True}))


def test_self_dependency_is_rejected() -> None:
    with pytest.raises(ValidationError, match="a task must not depend on itself"):
        AutonomousTask.model_validate(build_task(id="QM-AUTO-001", depends_on=["QM-AUTO-001"]))


def test_duplicate_dependencies_are_rejected() -> None:
    with pytest.raises(ValidationError, match="depends_on must not contain duplicates"):
        AutonomousTask.model_validate(build_task(depends_on=["QM-AUTO-000", "QM-AUTO-000"]))


def test_duplicate_task_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="task IDs must be unique across the backlog"):
        TaskBacklog.model_validate(
            build_backlog(build_task(id="QM-AUTO-001"), build_task(id="QM-AUTO-001"))
        )


def test_overlapping_allowed_and_forbidden_paths_are_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match="paths must not appear in both allowed_paths and forbidden_paths",
    ):
        AutonomousTask.model_validate(
            build_task(allowed_paths=["automation/"], forbidden_paths=["automation/"])
        )


@pytest.mark.parametrize(
    "limits",
    [
        {"max_attempts": 0},
        {"max_files_changed": 0},
        {"max_diff_lines": 0},
        {"max_attempts": -1},
    ],
)
def test_invalid_execution_limits_are_rejected(limits: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(limits=limits))


def test_max_attempts_greater_than_five_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(limits={"max_attempts": 6}))


def test_unknown_nested_permission_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(autonomy={"auto_deploy": True}))


def test_unknown_nested_limit_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AutonomousTask.model_validate(build_task(limits={"max_retries": 1}))


def test_duplicate_acceptance_criteria_are_rejected() -> None:
    with pytest.raises(ValidationError, match="acceptance_criteria must not contain duplicates"):
        AutonomousTask.model_validate(
            build_task(acceptance_criteria=["same criterion", "same criterion"])
        )


def test_duplicate_validation_commands_are_rejected() -> None:
    with pytest.raises(ValidationError, match="validation_commands must not contain duplicates"):
        AutonomousTask.model_validate(
            build_task(validation_commands=["same command", "same command"])
        )


# --------------------------------------------------------------------------------------
# Loader-level negative tests (19-22)
# --------------------------------------------------------------------------------------


def test_missing_yaml_file_is_rejected_clearly(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(BacklogLoadError, match="file does not exist"):
        load_backlog_file(missing)


def test_empty_yaml_is_rejected_clearly(tmp_path: Path) -> None:
    path = write_backlog(tmp_path, "")
    with pytest.raises(BacklogLoadError, match="document is empty"):
        load_backlog_file(path)


def test_malformed_yaml_is_rejected_clearly(tmp_path: Path) -> None:
    path = write_backlog(tmp_path, "version: [unterminated")
    with pytest.raises(BacklogLoadError, match="content is not valid YAML"):
        load_backlog_file(path)


def test_non_mapping_yaml_root_is_rejected(tmp_path: Path) -> None:
    path = write_backlog(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(BacklogLoadError, match="document root must be a mapping"):
        load_backlog_file(path)


def test_directory_path_is_rejected_clearly(tmp_path: Path) -> None:
    with pytest.raises(BacklogLoadError, match="not a regular file"):
        load_backlog_file(tmp_path)


def test_missing_tasks_field_is_rejected() -> None:
    backlog = build_backlog()
    del backlog["tasks"]
    with pytest.raises(ValidationError):
        TaskBacklog.model_validate(backlog)


def test_empty_task_collection_is_rejected() -> None:
    with pytest.raises(ValidationError, match="tasks must contain at least one task"):
        TaskBacklog.model_validate({"version": "qualitymesh.dev/automation/v1", "tasks": []})


def test_loader_does_not_mutate_input_dict() -> None:
    task = build_task()
    original = copy.deepcopy(task)
    AutonomousTask.model_validate(task)
    assert task == original

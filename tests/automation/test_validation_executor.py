"""Unit tests for sequential validation-plan execution (QM-AUTO-008)."""

from __future__ import annotations

import copy
import shutil
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
from automation.schemas.validation_run import (
    AggregateOutcome,
    ValidationEntryStatus,
    ValidationExecutionPolicy,
)
from automation.validation_executor import ValidationExecutionError, execute_validation_plan
from automation.validation_planner import build_validation_plan

PYTHON_NAME = Path(sys.executable).name
PASS_CMD = PYTHON_NAME + ' -c "print(1)"'
PASS_CMD_2 = PYTHON_NAME + ' -c "print(11)"'
FAIL_CMD = PYTHON_NAME + " -c \"__import__('sys').exit(1)\""
SLEEP_1S_CMD = PYTHON_NAME + " -c \"__import__('time').sleep(1)\""
SLEEP_1S_CMD_2 = PYTHON_NAME + " -c \"__import__('time').sleep(1.01)\""
SLEEP_10S_CMD = PYTHON_NAME + " -c \"__import__('time').sleep(10)\""

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
        "validation_commands": [PASS_CMD],
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def build_policy(**overrides: Any) -> CommandPolicy:
    if shutil.which(PYTHON_NAME) is None:
        pytest.skip(f"{PYTHON_NAME!r} not resolvable on PATH in this environment")
    fields: dict[str, Any] = {"worktree_root": "unused", "allowed_executables": (PYTHON_NAME,)}
    fields.update(overrides)
    return CommandPolicy.model_validate(fields)


# --------------------------------------------------------------------------------------
# Success paths
# --------------------------------------------------------------------------------------


def test_all_commands_pass(tmp_path: Path) -> None:
    task = build_task(validation_commands=[PASS_CMD, PASS_CMD_2])
    policy = build_policy(worktree_root=str(tmp_path))
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy())

    assert result.outcome is AggregateOutcome.PASSED
    assert all(entry.status is ValidationEntryStatus.PASSED for entry in result.results)
    assert result.executed_count == 2
    assert result.skipped_count == 0


def test_order_is_preserved(tmp_path: Path) -> None:
    commands = [
        PYTHON_NAME + ' -c "print(1)"',
        PYTHON_NAME + ' -c "print(2)"',
        PYTHON_NAME + ' -c "print(3)"',
    ]
    task = build_task(validation_commands=commands)
    policy = build_policy(worktree_root=str(tmp_path))
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy())

    assert [entry.index for entry in result.results] == [0, 1, 2]
    stdouts = [
        entry.command_result.stdout.strip() for entry in result.results if entry.command_result
    ]
    assert stdouts == ["1", "2", "3"]


def test_each_entry_produces_exactly_one_result(tmp_path: Path) -> None:
    task = build_task(validation_commands=[PASS_CMD, FAIL_CMD, PASS_CMD_2])
    policy = build_policy(worktree_root=str(tmp_path))
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy())

    assert len(result.results) == len(plan.entries) == 3
    assert result.executed_count + result.skipped_count == len(plan.entries)


# --------------------------------------------------------------------------------------
# Nonzero exit and timeout
# --------------------------------------------------------------------------------------


def test_nonzero_exit_is_classified_failed(tmp_path: Path) -> None:
    task = build_task(validation_commands=[FAIL_CMD])
    policy = build_policy(worktree_root=str(tmp_path))
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy())

    assert result.outcome is AggregateOutcome.FAILED
    assert result.results[0].status is ValidationEntryStatus.FAILED
    assert result.results[0].command_result is not None
    assert result.results[0].command_result.exit_code != 0


def test_command_timeout_is_classified_timed_out(tmp_path: Path) -> None:
    task = build_task(validation_commands=[SLEEP_10S_CMD])
    policy = build_policy(worktree_root=str(tmp_path), timeout_seconds=0.5)
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy())

    assert result.outcome is AggregateOutcome.TIMED_OUT
    assert result.results[0].status is ValidationEntryStatus.TIMED_OUT
    assert result.results[0].command_result is not None
    assert result.results[0].command_result.exit_code is None


# --------------------------------------------------------------------------------------
# Stop behavior
# --------------------------------------------------------------------------------------


def test_default_stop_skips_remaining_entries(tmp_path: Path) -> None:
    task = build_task(validation_commands=[FAIL_CMD, PASS_CMD])
    policy = build_policy(worktree_root=str(tmp_path))
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy())

    assert result.results[0].status is ValidationEntryStatus.FAILED
    assert result.results[1].status is ValidationEntryStatus.SKIPPED
    assert result.results[1].command_result is None
    assert result.executed_count == 1
    assert result.skipped_count == 1


def test_continue_on_failure_runs_every_entry(tmp_path: Path) -> None:
    task = build_task(validation_commands=[FAIL_CMD, PASS_CMD])
    policy = build_policy(worktree_root=str(tmp_path))
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy(continue_on_failure=True))

    assert result.results[0].status is ValidationEntryStatus.FAILED
    assert result.results[1].status is ValidationEntryStatus.PASSED
    assert result.executed_count == 2
    assert result.skipped_count == 0
    assert result.outcome is AggregateOutcome.FAILED


# --------------------------------------------------------------------------------------
# Total execution deadline
# --------------------------------------------------------------------------------------


def test_total_execution_deadline_stops_before_next_command(tmp_path: Path) -> None:
    task = build_task(validation_commands=[SLEEP_1S_CMD, SLEEP_1S_CMD_2])
    policy = build_policy(worktree_root=str(tmp_path), timeout_seconds=10)
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy(total_deadline_seconds=0.5))

    assert result.results[0].status is ValidationEntryStatus.PASSED
    assert result.results[1].status is ValidationEntryStatus.SKIPPED
    assert result.executed_count == 1
    assert result.skipped_count == 1
    assert result.outcome is AggregateOutcome.TIMED_OUT


# --------------------------------------------------------------------------------------
# Runner exceptions
# --------------------------------------------------------------------------------------


def test_runner_exception_is_wrapped(tmp_path: Path) -> None:
    task = build_task(validation_commands=["definitely-not-a-real-tool-xyz --version"])
    policy = build_policy(
        worktree_root=str(tmp_path), allowed_executables=("definitely-not-a-real-tool-xyz",)
    )
    plan = build_validation_plan(task, policy, str(tmp_path))

    with pytest.raises(ValidationExecutionError):
        execute_validation_plan(plan, ValidationExecutionPolicy())


# --------------------------------------------------------------------------------------
# No double execution
# --------------------------------------------------------------------------------------


def test_no_double_execution(tmp_path: Path) -> None:
    counter_path = tmp_path / "counter.txt"
    counter_path.write_text("0", encoding="utf-8")
    script_path = tmp_path / "increment.py"
    script_path.write_text(
        "from pathlib import Path\n"
        "p = Path(__file__).parent / 'counter.txt'\n"
        "p.write_text(str(int(p.read_text()) + 1))\n",
        encoding="utf-8",
    )
    task = build_task(validation_commands=[f"{PYTHON_NAME} increment.py"])
    policy = build_policy(worktree_root=str(tmp_path), timeout_seconds=10)
    plan = build_validation_plan(task, policy, str(tmp_path))

    result = execute_validation_plan(plan, ValidationExecutionPolicy())

    assert result.outcome is AggregateOutcome.PASSED
    assert counter_path.read_text(encoding="utf-8") == "1"


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_input_models_are_not_mutated(tmp_path: Path) -> None:
    task = build_task(validation_commands=[PASS_CMD, FAIL_CMD])
    policy = build_policy(worktree_root=str(tmp_path))
    plan = build_validation_plan(task, policy, str(tmp_path))
    execution_policy = ValidationExecutionPolicy(continue_on_failure=True)
    plan_before = copy.deepcopy(plan)
    execution_policy_before = copy.deepcopy(execution_policy)

    execute_validation_plan(plan, execution_policy)

    assert plan == plan_before
    assert execution_policy == execution_policy_before

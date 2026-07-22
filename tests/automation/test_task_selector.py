"""Unit tests for task eligibility and selection (QM-AUTO-002)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.schemas.planning import (
    EligibilityReasonCode,
    SelectionReasonCode,
)
from automation.schemas.task import AutonomousTask, RiskLevel, TaskBacklog
from automation.task_selector import (
    evaluate_eligibility,
    select_next_task,
    select_task_by_id,
)

VERSION = "qualitymesh.dev/automation/v1"
ALL_RISK_LEVELS = frozenset(RiskLevel)

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


def build_backlog(*tasks: AutonomousTask) -> TaskBacklog:
    return TaskBacklog.model_validate({"version": VERSION, "tasks": list(tasks)})


# --------------------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------------------


def test_ready_task_with_no_dependencies_is_eligible() -> None:
    backlog = build_backlog(build_task(id="a"))
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is True
    assert result.reasons == ()


def test_completed_task_is_not_eligible() -> None:
    backlog = build_backlog(build_task(id="a", status="completed"))
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is False
    assert result.reasons == (EligibilityReasonCode.TASK_NOT_READY,)


def test_active_task_is_not_eligible() -> None:
    backlog = build_backlog(build_task(id="a", status="active"))
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is False
    assert result.reasons == (EligibilityReasonCode.TASK_NOT_READY,)


def test_blocked_task_is_not_eligible() -> None:
    backlog = build_backlog(build_task(id="a", status="blocked"))
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is False
    assert result.reasons == (EligibilityReasonCode.TASK_NOT_READY,)


def test_ready_task_with_completed_dependencies_is_eligible() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", status="completed"),
    )
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is True
    assert result.reasons == ()


def test_ready_task_with_incomplete_dependency_is_ineligible() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", status="active"),
    )
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is False
    assert result.reasons == (EligibilityReasonCode.DEPENDENCY_INCOMPLETE,)
    assert result.incomplete_dependency_ids == ("b",)


def test_missing_dependency_makes_task_ineligible() -> None:
    backlog = build_backlog(build_task(id="a", depends_on=["ghost"]))
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is False
    assert result.reasons == (EligibilityReasonCode.DEPENDENCY_NOT_FOUND,)
    assert result.missing_dependency_ids == ("ghost",)


def test_cyclic_task_is_ineligible() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["a"]),
    )
    result = evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS)

    assert result.eligible is False
    assert EligibilityReasonCode.DEPENDENCY_CYCLE in result.reasons


def test_disallowed_risk_is_ineligible() -> None:
    backlog = build_backlog(build_task(id="a", risk="high"))
    result = evaluate_eligibility(backlog.tasks[0], backlog, {RiskLevel.LOW})

    assert result.eligible is False
    assert result.reasons == (EligibilityReasonCode.RISK_NOT_ALLOWED,)


def test_multiple_applicable_reasons_are_returned() -> None:
    backlog = build_backlog(build_task(id="a", status="active", risk="high"))
    result = evaluate_eligibility(backlog.tasks[0], backlog, {RiskLevel.LOW})

    assert result.eligible is False
    assert result.reasons == (
        EligibilityReasonCode.TASK_NOT_READY,
        EligibilityReasonCode.RISK_NOT_ALLOWED,
    )


def test_all_eligibility_reasons_are_returned_in_deterministic_order() -> None:
    backlog = build_backlog(
        build_task(
            id="x",
            status="blocked",
            risk="high",
            depends_on=["missing-dep", "incomplete-dep", "y"],
        ),
        build_task(id="y", depends_on=["x"]),
        build_task(id="incomplete-dep", status="active"),
    )
    task_x = next(task for task in backlog.tasks if task.id == "x")

    result = evaluate_eligibility(task_x, backlog, {RiskLevel.LOW})

    assert result.reasons == (
        EligibilityReasonCode.TASK_NOT_READY,
        EligibilityReasonCode.DEPENDENCY_NOT_FOUND,
        EligibilityReasonCode.DEPENDENCY_INCOMPLETE,
        EligibilityReasonCode.DEPENDENCY_CYCLE,
        EligibilityReasonCode.RISK_NOT_ALLOWED,
    )

    second = evaluate_eligibility(task_x, backlog, {RiskLevel.LOW})
    assert second.reasons == result.reasons


# --------------------------------------------------------------------------------------
# Automatic selection
# --------------------------------------------------------------------------------------


def test_highest_priority_wins() -> None:
    backlog = build_backlog(
        build_task(id="a", priority=1),
        build_task(id="b", priority=5),
        build_task(id="c", priority=3),
    )
    result = select_next_task(backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id == "b"


def test_higher_numeric_priority_is_treated_as_more_urgent() -> None:
    backlog = build_backlog(build_task(id="low", priority=1), build_task(id="high", priority=10))
    result = select_next_task(backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id == "high"


def test_task_id_breaks_equal_priority_ties() -> None:
    backlog = build_backlog(
        build_task(id="zebra", priority=5),
        build_task(id="apple", priority=5),
        build_task(id="mango", priority=5),
    )
    result = select_next_task(backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id == "apple"


def test_completed_tasks_are_excluded_from_automatic_selection() -> None:
    backlog = build_backlog(
        build_task(id="done", priority=10, status="completed"),
        build_task(id="pending", priority=1),
    )
    result = select_next_task(backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id == "pending"


def test_active_and_blocked_tasks_are_excluded() -> None:
    backlog = build_backlog(
        build_task(id="active-task", priority=10, status="active"),
        build_task(id="blocked-task", priority=9, status="blocked"),
        build_task(id="ready-task", priority=1),
    )
    result = select_next_task(backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id == "ready-task"


def test_tasks_with_incomplete_dependencies_are_excluded() -> None:
    backlog = build_backlog(
        build_task(id="blocked-by-dep", priority=10, depends_on=["dep"]),
        build_task(id="dep", status="active"),
        build_task(id="free", priority=1),
    )
    result = select_next_task(backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id == "free"


def test_tasks_with_disallowed_risk_are_excluded() -> None:
    backlog = build_backlog(
        build_task(id="risky", priority=10, risk="high"),
        build_task(id="safe", priority=1, risk="low"),
    )
    result = select_next_task(backlog, {RiskLevel.LOW})

    assert result.selected_task_id == "safe"


def test_no_eligible_tasks_returns_no_eligible_task() -> None:
    backlog = build_backlog(build_task(id="a", status="completed"))
    result = select_next_task(backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id is None
    assert result.selected is False
    assert result.reason == SelectionReasonCode.NO_ELIGIBLE_TASK
    assert len(result.evaluations) == 1
    assert result.evaluations[0].task_id == "a"


def test_selection_independent_of_backlog_declaration_order() -> None:
    forward = build_backlog(
        build_task(id="a", priority=1),
        build_task(id="b", priority=5),
        build_task(id="c", priority=3),
    )
    reversed_order = build_backlog(
        build_task(id="c", priority=3),
        build_task(id="b", priority=5),
        build_task(id="a", priority=1),
    )

    assert select_next_task(forward, ALL_RISK_LEVELS).selected_task_id == "b"
    assert select_next_task(reversed_order, ALL_RISK_LEVELS).selected_task_id == "b"


def test_automatic_selection_does_not_mutate_backlog() -> None:
    backlog = build_backlog(build_task(id="a", priority=1), build_task(id="b", priority=5))
    before = copy.deepcopy(backlog)

    select_next_task(backlog, ALL_RISK_LEVELS)

    assert backlog == before


# --------------------------------------------------------------------------------------
# Explicit selection
# --------------------------------------------------------------------------------------


def test_explicit_eligible_task_selection_succeeds() -> None:
    backlog = build_backlog(build_task(id="a"))
    result = select_task_by_id("a", backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id == "a"
    assert result.selected is True
    assert result.reason is None
    assert result.evaluations == (evaluate_eligibility(backlog.tasks[0], backlog, ALL_RISK_LEVELS),)


def test_explicit_ineligible_task_returns_reasons() -> None:
    backlog = build_backlog(build_task(id="a", status="active"))
    result = select_task_by_id("a", backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id is None
    assert result.selected is False
    assert result.reason is None
    assert len(result.evaluations) == 1
    assert result.evaluations[0].eligible is False
    assert result.evaluations[0].reasons == (EligibilityReasonCode.TASK_NOT_READY,)


def test_explicit_nonexistent_task_returns_task_not_found() -> None:
    backlog = build_backlog(build_task(id="a"))
    result = select_task_by_id("does-not-exist", backlog, ALL_RISK_LEVELS)

    assert result.selected_task_id is None
    assert result.selected is False
    assert result.reason == SelectionReasonCode.TASK_NOT_FOUND
    assert result.evaluations == ()


def test_explicit_selection_does_not_mutate_backlog() -> None:
    backlog = build_backlog(build_task(id="a"))
    before = copy.deepcopy(backlog)

    select_task_by_id("a", backlog, ALL_RISK_LEVELS)

    assert backlog == before

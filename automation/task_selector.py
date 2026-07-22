"""Deterministic task eligibility and selection over a backlog (QM-AUTO-002).

Combines `automation.dependency_resolver`'s pure graph findings with a task's own
`status` and `risk` to decide whether it can be picked, and — for automatic
selection — which single eligible task to pick. Never runs anything, never mutates
the backlog, never uses exceptions for the ordinary case where a task or no task is
eligible; exceptions stay reserved for a genuinely invalid call.
"""

from __future__ import annotations

from collections.abc import Collection

from automation.dependency_resolver import (
    find_dependency_cycles,
    find_incomplete_dependencies,
    find_missing_dependency_ids,
)
from automation.schemas.planning import (
    DependencyCycle,
    EligibilityReasonCode,
    SelectionReasonCode,
    TaskEligibilityResult,
    TaskSelectionResult,
)
from automation.schemas.task import AutonomousTask, RiskLevel, TaskBacklog, TaskStatus


def evaluate_eligibility(
    task: AutonomousTask,
    backlog: TaskBacklog,
    allowed_risk_levels: Collection[RiskLevel],
    *,
    cycles: tuple[DependencyCycle, ...] | None = None,
) -> TaskEligibilityResult:
    """Evaluate whether `task` can be selected right now.

    `cycles` lets a caller evaluating many tasks against the same `backlog` compute
    `find_dependency_cycles` once and reuse it; when omitted it is computed here.
    Reasons are checked, and appended, in one fixed order — status, missing
    dependencies, incomplete dependencies, cycle membership, risk — so
    `TaskEligibilityResult.reasons` is deterministic regardless of which happen to
    apply.
    """
    if cycles is None:
        cycles = find_dependency_cycles(backlog)
    cyclic_task_ids = {task_id for cycle in cycles for task_id in cycle.task_ids}

    missing_ids = find_missing_dependency_ids(task, backlog)
    incomplete_ids = find_incomplete_dependencies(task, backlog)

    reasons: list[EligibilityReasonCode] = []
    if task.status is not TaskStatus.READY:
        reasons.append(EligibilityReasonCode.TASK_NOT_READY)
    if missing_ids:
        reasons.append(EligibilityReasonCode.DEPENDENCY_NOT_FOUND)
    if incomplete_ids:
        reasons.append(EligibilityReasonCode.DEPENDENCY_INCOMPLETE)
    if task.id in cyclic_task_ids:
        reasons.append(EligibilityReasonCode.DEPENDENCY_CYCLE)
    if task.risk not in allowed_risk_levels:
        reasons.append(EligibilityReasonCode.RISK_NOT_ALLOWED)

    return TaskEligibilityResult(
        task_id=task.id,
        eligible=not reasons,
        reasons=tuple(reasons),
        incomplete_dependency_ids=incomplete_ids,
        missing_dependency_ids=missing_ids,
    )


def select_task_by_id(
    task_id: str,
    backlog: TaskBacklog,
    allowed_risk_levels: Collection[RiskLevel],
) -> TaskSelectionResult:
    """Look up `task_id` in `backlog` and evaluate its eligibility.

    Returns `SelectionReasonCode.TASK_NOT_FOUND` when no task in `backlog` has that
    ID. When the task exists but is not eligible, `selected_task_id` and `reason`
    are both `None` — see `TaskSelectionResult`'s docstring for why the reason lives
    in `evaluations[0]` instead.
    """
    task = next((candidate for candidate in backlog.tasks if candidate.id == task_id), None)
    if task is None:
        return TaskSelectionResult(
            selected_task_id=None,
            reason=SelectionReasonCode.TASK_NOT_FOUND,
            evaluations=(),
        )

    evaluation = evaluate_eligibility(task, backlog, allowed_risk_levels)
    return TaskSelectionResult(
        selected_task_id=task.id if evaluation.eligible else None,
        reason=None,
        evaluations=(evaluation,),
    )


def select_next_task(
    backlog: TaskBacklog,
    allowed_risk_levels: Collection[RiskLevel],
) -> TaskSelectionResult:
    """Evaluate every task in `backlog` and pick the one to work on next.

    Selection order: highest `priority` first, then lexicographically smallest task
    ID as the deterministic tie-breaker. Returns
    `SelectionReasonCode.NO_ELIGIBLE_TASK` — never an exception — when nothing
    qualifies. `evaluations` always covers every task in `backlog`, in declaration
    order, so the result is auditable whether or not a task was selected.
    """
    cycles = find_dependency_cycles(backlog)
    evaluations = tuple(
        evaluate_eligibility(task, backlog, allowed_risk_levels, cycles=cycles)
        for task in backlog.tasks
    )

    eligible_tasks = [
        task
        for task, evaluation in zip(backlog.tasks, evaluations, strict=True)
        if evaluation.eligible
    ]
    if not eligible_tasks:
        return TaskSelectionResult(
            selected_task_id=None,
            reason=SelectionReasonCode.NO_ELIGIBLE_TASK,
            evaluations=evaluations,
        )

    chosen = min(eligible_tasks, key=lambda task: (-task.priority, task.id))
    return TaskSelectionResult(selected_task_id=chosen.id, reason=None, evaluations=evaluations)

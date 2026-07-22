"""Typed models for dependency-graph findings and task selection (QM-AUTO-002).

These models are the vocabulary `dependency_resolver` and `task_selector` return —
never a raised exception for an ordinary domain outcome (a missing dependency, a
cycle, no eligible task). That mirrors this repository's existing fail-closed
pattern: `BindingVerificationResult`, `CompletenessResult`. Exceptions stay reserved
for genuinely invalid calls, not expected planning outcomes.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class StrictPlanningModel(BaseModel):
    """Base configuration for all planning models.

    Mirrors `automation.schemas.task.StrictTaskModel`. Defined locally rather than
    imported, following this package's own precedent of one base per boundary rather
    than a shared one (see `StrictTaskModel`'s docstring).
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class EligibilityReasonCode(StrEnum):
    """Why a task is not selectable right now. A result may carry several at once."""

    TASK_NOT_READY = "task-not-ready"
    DEPENDENCY_NOT_FOUND = "dependency-not-found"
    DEPENDENCY_INCOMPLETE = "dependency-incomplete"
    DEPENDENCY_CYCLE = "dependency-cycle"
    RISK_NOT_ALLOWED = "risk-not-allowed"


class SelectionReasonCode(StrEnum):
    """Why `task_selector` returned no task. Distinct from `EligibilityReasonCode`:
    these describe the selection call itself, not any one task's evaluation."""

    TASK_NOT_FOUND = "task-not-found"
    NO_ELIGIBLE_TASK = "no-eligible-task"


class MissingDependency(StrictPlanningModel):
    """One `depends_on` reference that names no task in the backlog."""

    task_id: str
    missing_dependency_id: str


class DependencyCycle(StrictPlanningModel):
    """A group of tasks that depend on each other, directly or indirectly.

    `task_ids` holds every task in the cycle, sorted, regardless of how many hops
    apart they are or where in the group traversal happened to start.
    """

    task_ids: tuple[str, ...]


class TaskEligibilityResult(StrictPlanningModel):
    """Whether one task can be selected right now, and every reason it can't.

    `reasons` is empty exactly when `eligible` is true. `incomplete_dependency_ids`
    and `missing_dependency_ids` are populated whenever applicable, independent of
    whether their corresponding reason code fired for this task's overall status —
    they are diagnostic detail, not a re-encoding of `reasons`.
    """

    task_id: str
    eligible: bool
    reasons: tuple[EligibilityReasonCode, ...]
    incomplete_dependency_ids: tuple[str, ...] = Field(default_factory=tuple)
    missing_dependency_ids: tuple[str, ...] = Field(default_factory=tuple)


class TaskSelectionResult(StrictPlanningModel):
    """The outcome of one selection call — explicit or automatic.

    `reason` is set only for the two selector-level failures
    (`SelectionReasonCode.TASK_NOT_FOUND`, `SelectionReasonCode.NO_ELIGIBLE_TASK`).
    An explicitly requested task that exists but is ineligible is reported through
    `evaluations[0].reasons` instead, with `selected_task_id` left `None` and
    `reason` left `None` — the failure is the task's own eligibility, not the
    selector's ability to find or choose among tasks.
    """

    selected_task_id: str | None
    reason: SelectionReasonCode | None
    evaluations: tuple[TaskEligibilityResult, ...]

    @computed_field
    @property
    def selected(self) -> bool:
        return self.selected_task_id is not None

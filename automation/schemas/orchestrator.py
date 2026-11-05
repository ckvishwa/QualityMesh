"""Typed models for the top-level autonomous task orchestrator (QM-AUTO-013).

`automation.orchestrator.run_orchestrator_cycle` composes every earlier
component into one controlled run of exactly one backlog task: select, mark
active, run the bounded provider/repair lifecycle, record evidence, perform
permitted commit/draft-PR operations, mark the task terminal, and return one
immutable result. This module defines the input and result vocabulary only.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from automation.schemas.command import CommandPolicy
from automation.schemas.evidence_run import EvidencePackage
from automation.schemas.git_operation import GitOperationResult
from automation.schemas.planning import TaskSelectionResult
from automation.schemas.repair import RepairLoopResult
from automation.schemas.task import RiskLevel, TaskStatus
from automation.schemas.validation_run import ValidationExecutionPolicy


class StrictOrchestratorModel(BaseModel):
    """Base configuration for all orchestrator models.

    Mirrors `automation.schemas.task.StrictTaskModel` and its siblings — a
    local base per this package's existing per-boundary convention, not a
    shared one.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class OrchestratorOutcome(StrEnum):
    """Why one orchestrator cycle ended.

    `BLOCKED` is the generic fallback for a terminal failure that doesn't
    match any of the more specific codes below (kept for exhaustiveness, not
    expected to fire for any failure this module currently produces — every
    known failure has its own dedicated code). Every other member names a
    single, specific cause.
    """

    COMPLETED = "completed"
    NO_TASK_SELECTED = "no-task-selected"
    ACTIVE_TASK_EXISTS = "active-task-exists"
    SETUP_FAILED = "setup-failed"
    VALIDATION_FAILED = "validation-failed"
    PROVIDER_FAILED = "provider-failed"
    BUDGET_EXCEEDED = "budget-exceeded"
    POLICY_VIOLATION = "policy-violation"
    EVIDENCE_FAILED = "evidence-failed"
    GIT_OPERATION_FAILED = "git-operation-failed"
    BLOCKED = "blocked"


NonEmptyStr = Annotated[str, Field(min_length=1)]


def _parse_allowed_risk_levels(value: object) -> tuple[RiskLevel, ...]:
    """Accept a JSON/Python array of `RiskLevel` instances or known string
    values. Mirrors `automation.schemas.task._parse_risk_level`: strict mode
    does not coerce a plain string into a `StrEnum` member on its own."""

    if not isinstance(value, (list, tuple)):
        raise ValueError("allowed_risk_levels must be an array")
    items = cast("list[object] | tuple[object, ...]", value)
    parsed: list[RiskLevel] = []
    for item in items:
        if isinstance(item, RiskLevel):
            parsed.append(item)
        elif isinstance(item, str):
            try:
                parsed.append(RiskLevel(item))
            except ValueError as exc:
                raise ValueError(f"unknown risk level: {item}") from exc
        else:
            raise ValueError("each risk level must be a string")
    return tuple(parsed)


class OrchestratorInputs(StrictOrchestratorModel):
    """Everything one orchestrator cycle needs, explicit — never a hidden
    global or environment variable.

    `task_id`, when given, selects that exact task; when `None`, the cycle
    selects the next eligible task automatically (see
    `automation.task_selector.select_next_task`). `allow_git_writes` is the
    caller's own permission gate: when `False`, `automation.git_operator` is
    never invoked at all, regardless of what `task.autonomy` itself permits.
    `secret_scan_clean` is a caller-supplied attestation for the prepared
    worktree, not a scan performed by this module. It defaults to False, so
    commit eligibility fails closed when the caller has not verified a scan.
    """

    backlog_path: NonEmptyStr
    repository_path: NonEmptyStr
    base_commit: NonEmptyStr
    worktree_parent: NonEmptyStr
    cache_manifest_path: NonEmptyStr
    evidence_path: NonEmptyStr
    task_id: NonEmptyStr | None = None
    allowed_risk_levels: Annotated[
        tuple[RiskLevel, ...], BeforeValidator(_parse_allowed_risk_levels), Field(min_length=1)
    ]
    command_policy: CommandPolicy
    execution_policy: ValidationExecutionPolicy
    token_budget: Annotated[int, Field(strict=True, ge=1)]
    max_output_tokens: Annotated[int, Field(strict=True, ge=1)] = 4096
    base_branch: NonEmptyStr = "main"
    allow_git_writes: bool = False
    secret_scan_clean: bool = False


class OrchestratorRunResult(StrictOrchestratorModel):
    """The full, immutable record of one orchestrator cycle.

    `task_id`/`starting_status`/`selection_result` are populated as soon as a
    task has been identified, even for a cycle that never reaches
    `final_status` (e.g. `SETUP_FAILED` before the task could be marked
    active). `final_status` is `None` exactly when the cycle never
    transitioned any task's status (`NO_TASK_SELECTED`,
    `ACTIVE_TASK_EXISTS`, or a failure to persist the initial `ACTIVE`
    transition itself).
    """

    run_id: NonEmptyStr
    task_id: str | None
    starting_status: TaskStatus | None
    final_status: TaskStatus | None
    selection_result: TaskSelectionResult | None
    repair_result: RepairLoopResult | None
    evidence_path: str | None
    evidence_result: EvidencePackage | None
    git_operation_result: GitOperationResult | None
    outcome: OrchestratorOutcome
    reason: NonEmptyStr
    started_at: datetime
    ended_at: datetime
    duration_seconds: Annotated[float, Field(ge=0)]

    @field_validator("started_at", "ended_at")
    @classmethod
    def validate_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware, not naive")
        return value

    @model_validator(mode="after")
    def validate_result_invariants(self) -> Self:
        completed = OrchestratorOutcome.COMPLETED
        if self.outcome is completed and self.final_status is not TaskStatus.COMPLETED:
            raise ValueError("a COMPLETED outcome must carry final_status COMPLETED")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self

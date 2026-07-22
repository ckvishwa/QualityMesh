"""Strict schema for an autonomous-task specification and its backlog.

An autonomous task tells the (not-yet-built) operator what it may do to a repository
and how far its authority extends. The specification is treated the same way this
repository treats a release contract or evidence submission: an external trust
boundary that must reject anything unknown, ambiguous or structurally invalid rather
than guess at intent.

This module defines the schema only. It does not resolve `depends_on` against a
backlog, run anything, modify files, or talk to Git or GitHub — that is the operator
this task deliberately does not build.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _yaml_array_to_tuple(value: object) -> object:
    """Accept a YAML/JSON array and freeze it as a tuple before strict validation."""

    if isinstance(value, list):
        return tuple(cast(list[object], value))
    if isinstance(value, tuple):
        return cast(tuple[object, ...], value)
    # ValueError, not TypeError: Pydantic only converts ValueError and AssertionError
    # into ValidationError. A TypeError would escape model_validate uncaught.
    raise ValueError("expected an array")


class StrictTaskModel(BaseModel):
    """Base configuration for all autonomous-task models.

    Mirrors `qualitymesh.schemas.contract.StrictContractModel`. Defined locally rather
    than imported: this package is a separate boundary from the release-contract and
    evidence schemas, and sharing a base would couple unrelated trust domains for no
    present benefit.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


NonEmptyStr = Annotated[str, Field(min_length=1)]
StrTuple = Annotated[tuple[NonEmptyStr, ...], BeforeValidator(_yaml_array_to_tuple)]


class TaskStatus(StrEnum):
    """Lifecycle state of an autonomous task."""

    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class RiskLevel(StrEnum):
    """Risk classification of an autonomous task's change."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _parse_task_status(value: object) -> TaskStatus:
    """Accept only a known string value or an existing TaskStatus instance.

    Mirrors `qualitymesh.schemas.contract._parse_evidence_kind`: strict mode does not
    coerce a plain string into a `StrEnum` member on its own.
    """

    if isinstance(value, TaskStatus):
        return value
    if not isinstance(value, str):
        raise ValueError("status must be a string")
    try:
        return TaskStatus(value)
    except ValueError as exc:
        raise ValueError(f"unknown status: {value}") from exc


def _parse_risk_level(value: object) -> RiskLevel:
    """Accept only a known string value or an existing RiskLevel instance."""

    if isinstance(value, RiskLevel):
        return value
    if not isinstance(value, str):
        raise ValueError("risk must be a string")
    try:
        return RiskLevel(value)
    except ValueError as exc:
        raise ValueError(f"unknown risk level: {value}") from exc


class AutonomyPermissions(StrictTaskModel):
    """What the operator may do without a human in the loop.

    Every permission defaults to `False`: authority must be granted explicitly, never
    inherited from a schema default.
    """

    auto_modify: bool = Field(default=False, strict=True)
    auto_test: bool = Field(default=False, strict=True)
    auto_commit: bool = Field(default=False, strict=True)
    auto_open_pr: bool = Field(default=False, strict=True)
    auto_merge: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_permission_chain(self) -> Self:
        if self.auto_merge and not self.auto_open_pr:
            raise ValueError("auto_merge requires auto_open_pr to also be true")
        if self.auto_open_pr and not self.auto_commit:
            raise ValueError("auto_open_pr requires auto_commit to also be true")
        return self


class ExecutionLimits(StrictTaskModel):
    """Bounds on how much an autonomous attempt may do."""

    max_attempts: Annotated[int, Field(strict=True, ge=1, le=5)] = 3
    max_files_changed: Annotated[int, Field(strict=True, ge=1)] = 10
    max_diff_lines: Annotated[int, Field(strict=True, ge=1)] = 500


class AutonomousTask(StrictTaskModel):
    """One autonomous task specification."""

    id: NonEmptyStr
    title: NonEmptyStr
    status: Annotated[TaskStatus, BeforeValidator(_parse_task_status)]
    priority: Annotated[int, Field(strict=True, ge=1)]
    risk: Annotated[RiskLevel, BeforeValidator(_parse_risk_level)]
    objective: NonEmptyStr
    depends_on: StrTuple = Field(default_factory=tuple)
    context_files: StrTuple = Field(default_factory=tuple)
    allowed_paths: StrTuple
    forbidden_paths: StrTuple = Field(default_factory=tuple)
    acceptance_criteria: StrTuple
    validation_commands: StrTuple
    autonomy: AutonomyPermissions = Field(default_factory=AutonomyPermissions)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def validate_task_invariants(self) -> Self:
        if not self.allowed_paths:
            raise ValueError("allowed_paths must contain at least one path")
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria must contain at least one item")
        if not self.validation_commands:
            raise ValueError("validation_commands must contain at least one command")

        if self.id in self.depends_on:
            raise ValueError("a task must not depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must not contain duplicates")
        if len(set(self.acceptance_criteria)) != len(self.acceptance_criteria):
            raise ValueError("acceptance_criteria must not contain duplicates")
        if len(set(self.validation_commands)) != len(self.validation_commands):
            raise ValueError("validation_commands must not contain duplicates")

        overlap = set(self.allowed_paths) & set(self.forbidden_paths)
        if overlap:
            joined = ", ".join(sorted(overlap))
            raise ValueError(
                f"paths must not appear in both allowed_paths and forbidden_paths: {joined}"
            )

        if self.risk is RiskLevel.HIGH and self.autonomy.auto_merge:
            raise ValueError("high-risk tasks must not have auto_merge set to true")

        return self


class TaskBacklog(StrictTaskModel):
    """A versioned collection of autonomous task specifications."""

    version: Literal["qualitymesh.dev/automation/v1"]
    tasks: Annotated[tuple[AutonomousTask, ...], BeforeValidator(_yaml_array_to_tuple)]

    @model_validator(mode="after")
    def validate_backlog_invariants(self) -> Self:
        if not self.tasks:
            raise ValueError("tasks must contain at least one task")

        task_ids = [task.id for task in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("task IDs must be unique across the backlog")

        return self

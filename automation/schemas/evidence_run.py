"""Typed models for evidence-package assembly and pre-commit verification
(QM-AUTO-012).

An `EvidencePackage` is the verifiable record of one task's execution: what
was asked, what changed, what it cost, every repair attempt, and whether it's
actually safe to commit. `CommitPreconditionResult` is a pure judgement —
built from caller-supplied facts, never from a git or filesystem call inside
this module — so it stays trivially testable. `automation.evidence_recorder`
is the only place that assembles a package or writes one to disk.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from automation.schemas.cache import Sha256Hex
from automation.schemas.provider import ProviderUsage
from automation.schemas.repair import RepairAttempt


def _array_to_tuple(value: object) -> object:
    """Accept a JSON/Python array and freeze it as a tuple before strict validation."""

    if isinstance(value, list):
        return tuple(cast(list[object], value))
    if isinstance(value, tuple):
        return cast(tuple[object, ...], value)
    # ValueError, not TypeError: Pydantic only converts ValueError and AssertionError
    # into ValidationError. A TypeError would escape model_validate uncaught.
    raise ValueError("expected an array")


def _parse_datetime(value: object) -> datetime:
    """Accept a real `datetime` or an ISO 8601 string — round-tripping a
    serialized `EvidencePackage` hands this field a string, since JSON has
    no native datetime type; strict mode does not parse one on its own."""

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid ISO 8601 datetime: {value}") from exc
    raise ValueError("created_at must be a datetime or an ISO 8601 string")


class StrictEvidenceRunModel(BaseModel):
    """Base configuration for all evidence-run models.

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


class CommitPreconditionCode(StrEnum):
    """One reason a set of changes is not currently safe to commit. A
    `CommitPreconditionResult` may carry several at once — every violated
    check is reported, not just the first."""

    PATH_OUTSIDE_ALLOWED = "path-outside-allowed"
    FORBIDDEN_PATH_MATCH = "forbidden-path-match"
    FILE_COUNT_EXCEEDED = "file-count-exceeded"
    DIFF_LINE_LIMIT_EXCEEDED = "diff-line-limit-exceeded"
    VALIDATION_NOT_PASSED = "validation-not-passed"
    NO_CHANGES = "no-changes"
    UNRESOLVED_MERGE_STATE = "unresolved-merge-state"
    SECRET_SCAN_BLOCKED = "secret-scan-blocked"  # noqa: S105 - precondition code, not a credential


NonEmptyStr = Annotated[str, Field(min_length=1)]


class CommitPreconditionResult(StrictEvidenceRunModel):
    """Whether it is currently safe to commit and open a PR for this task's
    changes. `eligible` is true exactly when `violations` is empty."""

    eligible: bool
    violations: Annotated[tuple[CommitPreconditionCode, ...], BeforeValidator(_array_to_tuple)]


class ChangedFileRecord(StrictEvidenceRunModel):
    """One file the task's changes touched: its identity, not its diff."""

    path: NonEmptyStr
    sha256: Sha256Hex
    size_bytes: Annotated[int, Field(strict=True, ge=0)]


class EvidencePackage(StrictEvidenceRunModel):
    """The full, verifiable record of one task's execution."""

    task_id: NonEmptyStr
    objective: NonEmptyStr
    acceptance_criteria: Annotated[tuple[NonEmptyStr, ...], BeforeValidator(_array_to_tuple)]
    base_commit: NonEmptyStr
    result_commit: NonEmptyStr | None = None
    branch: NonEmptyStr
    worktree_path: NonEmptyStr
    changed_files: Annotated[tuple[ChangedFileRecord, ...], BeforeValidator(_array_to_tuple)]
    context_files_supplied: Annotated[tuple[NonEmptyStr, ...], BeforeValidator(_array_to_tuple)]
    provider_name: NonEmptyStr
    model_identifier: NonEmptyStr
    provider_usage: ProviderUsage
    attempts: Annotated[tuple[RepairAttempt, ...], BeforeValidator(_array_to_tuple)]
    precondition_result: CommitPreconditionResult
    known_risks: Annotated[tuple[str, ...], BeforeValidator(_array_to_tuple)] = ()
    final_outcome: NonEmptyStr
    created_at: Annotated[datetime, BeforeValidator(_parse_datetime)]

    @field_validator("created_at")
    @classmethod
    def validate_created_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware, not naive")
        return value

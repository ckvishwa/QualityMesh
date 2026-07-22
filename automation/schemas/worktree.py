"""Typed models for isolated Git worktree management (QM-AUTO-005).

A worktree request names one task, one repository, one exact base commit, and one
approved parent directory to create the worktree under. Branch and directory names
are derived deterministically from the task ID — never chosen freely — so two
requests for the same task always agree on where its work happens. This module
defines the vocabulary only; `automation.worktree_manager` is the only place that
runs `git` or touches the filesystem.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field

_SAFE_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
"""Alphanumeric start, then alnum/dot/underscore/dash. No `/`, `\\`, leading `.`
(blocks `..` and hidden-entry tricks), spaces, or control characters — safe as
both a single path segment and a Git ref component."""


class StrictWorktreeModel(BaseModel):
    """Base configuration for all worktree models.

    Mirrors `automation.schemas.task.StrictTaskModel` and its siblings — a local
    base per this package's existing per-boundary convention, not a shared one.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class WorktreeErrorCode(StrEnum):
    """Why `automation.worktree_manager.create_worktree` refused a request."""

    REPOSITORY_NOT_FOUND = "repository-not-found"
    DIRTY_BASE_REPOSITORY = "dirty-base-repository"
    BASE_COMMIT_NOT_FOUND = "base-commit-not-found"
    WORKTREE_PARENT_NOT_FOUND = "worktree-parent-not-found"
    WORKTREE_ESCAPES_PARENT = "worktree-escapes-parent"
    BRANCH_COLLISION = "branch-collision"
    DIRECTORY_COLLISION = "directory-collision"
    WORKTREE_ALREADY_REGISTERED = "worktree-already-registered"
    GIT_COMMAND_FAILED = "git-command-failed"
    VERIFICATION_FAILED = "verification-failed"


class WorktreeCleanupStatus(StrEnum):
    """Result of a read-only cleanup-eligibility check. Never causes removal —
    see `automation.worktree_manager.check_cleanup_eligibility`."""

    ELIGIBLE = "eligible"
    NOT_ELIGIBLE_DIRTY = "not-eligible-dirty"
    NOT_ELIGIBLE_NOT_FOUND = "not-eligible-not-found"


TaskIdStr = Annotated[
    str, Field(pattern=_SAFE_TASK_ID_PATTERN.pattern, min_length=1, max_length=128)
]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class WorktreeRequest(StrictWorktreeModel):
    """What to create: one task's isolated worktree, from one exact base commit,
    under one approved parent directory.

    `branch_name` and `worktree_dirname` are derived from `task_id` alone —
    `agent/<task_id>` and `<task_id>` respectively — deterministically and only
    from a `task_id` that has already passed `TaskIdStr`'s safety pattern.
    """

    task_id: TaskIdStr
    repository_path: NonEmptyStr
    base_commit: NonEmptyStr
    worktree_parent: NonEmptyStr

    @computed_field
    @property
    def branch_name(self) -> str:
        return f"agent/{self.task_id}"

    @computed_field
    @property
    def worktree_dirname(self) -> str:
        return self.task_id


class WorktreeMetadata(StrictWorktreeModel):
    """The facts about a successfully created worktree."""

    task_id: TaskIdStr
    repository_path: NonEmptyStr
    worktree_path: NonEmptyStr
    branch_name: NonEmptyStr
    base_commit: NonEmptyStr


class WorktreeCleanupEligibility(StrictWorktreeModel):
    """Whether a worktree could safely be removed right now — a query, never an
    action. `eligible` is true exactly when `status` is `ELIGIBLE`."""

    worktree_path: NonEmptyStr
    eligible: bool
    status: WorktreeCleanupStatus

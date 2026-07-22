"""Typed models for controlled Git write operations (QM-AUTO-012).

This is the only schema module in the `automation` package whose
corresponding manager (`automation.git_operator`) is permitted to mutate a
real Git repository — stage, commit, push, and open a draft pull request,
never merge, never force-push, never touch anything outside the one task
branch. This module defines the result vocabulary only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class StrictGitOperationModel(BaseModel):
    """Base configuration for all Git-operation models.

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


class GitOperationErrorCode(StrEnum):
    """Why `automation.git_operator` refused an operation or a `git`/`gh`
    command it needed failed."""

    COMMIT_NOT_PERMITTED = "commit-not-permitted"
    PR_NOT_PERMITTED = "pr-not-permitted"
    MERGE_NOT_SUPPORTED = "merge-not-supported"
    STAGED_SCOPE_MISMATCH = "staged-scope-mismatch"
    NOTHING_STAGED = "nothing-staged"
    GIT_COMMAND_FAILED = "git-command-failed"
    GH_COMMAND_FAILED = "gh-command-failed"
    GH_CLI_NOT_FOUND = "gh-cli-not-found"


NonEmptyStr = Annotated[str, Field(min_length=1)]


class CommitOperationResult(StrictGitOperationModel):
    """Whether a commit was created, and its SHA if so."""

    performed: bool
    commit_sha: str | None = None
    reason: str | None = None


class PushOperationResult(StrictGitOperationModel):
    """Whether the task branch was pushed."""

    performed: bool
    branch: str | None = None
    reason: str | None = None


class DraftPullRequestResult(StrictGitOperationModel):
    """Whether a draft PR was opened. Never a ready-for-review or merged
    one — `automation.git_operator` has no capability to produce either."""

    performed: bool
    url: str | None = None
    number: Annotated[int, Field(strict=True, ge=1)] | None = None
    reason: str | None = None


class GitOperationResult(StrictGitOperationModel):
    """The full, typed result of one controlled Git-operations run: what was
    staged and committed, what was pushed, and what PR (if any) was opened."""

    commit: CommitOperationResult
    push: PushOperationResult
    pull_request: DraftPullRequestResult

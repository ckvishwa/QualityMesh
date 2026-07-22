"""Isolated Git worktree creation for one selected autonomous task (QM-AUTO-005).

Creates exactly one worktree, from one exact base commit, under one approved
parent directory, on a branch and directory name derived deterministically from
the task ID. Every `git` invocation is a bounded subprocess call with captured
output; nothing here runs an arbitrary command, calls a model, builds context, or
commits/pushes/merges anything.

This module never deletes a worktree or a branch. `check_cleanup_eligibility` is
read-only — it reports whether removal *would* be safe, so a later, separate step
can decide whether to act on that. Automatic cleanup is deliberately out of scope
here.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from automation.schemas.worktree import (
    WorktreeCleanupEligibility,
    WorktreeCleanupStatus,
    WorktreeErrorCode,
    WorktreeMetadata,
    WorktreeRequest,
)

_GIT_TIMEOUT_SECONDS = 30.0


class WorktreeError(Exception):
    """Raised when a worktree cannot be safely created, or a `git` command needed
    to create or inspect one fails.

    Carries a `WorktreeErrorCode` so callers can branch on the failure kind
    without parsing the message.
    """

    def __init__(self, message: str, *, code: WorktreeErrorCode) -> None:
        super().__init__(message)
        self.code = code


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise WorktreeError(
            "git executable not found on PATH", code=WorktreeErrorCode.GIT_COMMAND_FAILED
        )
    return executable


def _run_git(args: Sequence[str], *, cwd: Path) -> str:
    """Run `git <args>` in `cwd`, bounded by `_GIT_TIMEOUT_SECONDS`, and return its
    stripped stdout. Raises `WorktreeError(GIT_COMMAND_FAILED)` for a nonzero
    exit, a missing `git` executable, or any other failure to run it at all."""
    executable = _git_executable()
    try:
        result = subprocess.run(  # noqa: S603 - fixed args built from validated internal values, never shell text
            [executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(
            f"git {' '.join(args)!r} failed to run: {exc}",
            code=WorktreeErrorCode.GIT_COMMAND_FAILED,
        ) from exc

    if result.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)!r} failed (exit {result.returncode}): {result.stderr.strip()}",
            code=WorktreeErrorCode.GIT_COMMAND_FAILED,
        )
    return result.stdout.strip()


def _git_ok(args: Sequence[str], *, cwd: Path) -> bool:
    """Run `git <args>` in `cwd` for an existence-style check where a nonzero
    exit is an expected outcome, not a failure. Returns whether it exited 0.
    Still raises `WorktreeError(GIT_COMMAND_FAILED)` if `git` couldn't run at
    all — that is a real failure, distinct from a negative existence check."""
    executable = _git_executable()
    try:
        result = subprocess.run(  # noqa: S603 - fixed args built from validated internal values, never shell text
            [executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(
            f"git {' '.join(args)!r} failed to run: {exc}",
            code=WorktreeErrorCode.GIT_COMMAND_FAILED,
        ) from exc
    return result.returncode == 0


def _is_git_repository(path: Path) -> bool:
    return path.is_dir() and _git_ok(["rev-parse", "--git-dir"], cwd=path)


def _is_working_tree_clean(path: Path) -> bool:
    return _run_git(["status", "--porcelain"], cwd=path) == ""


def _resolve_commit(repo_path: Path, commit_ref: str) -> str:
    """Resolve `commit_ref` to its full SHA, confirming it names a commit object
    (not merely something that parses, like a blob or tree)."""
    ref = f"{commit_ref}^{{commit}}"
    if not _git_ok(["rev-parse", "--verify", "--quiet", ref], cwd=repo_path):
        raise WorktreeError(
            f"{commit_ref}: not a valid base commit in {repo_path}",
            code=WorktreeErrorCode.BASE_COMMIT_NOT_FOUND,
        )
    return _run_git(["rev-parse", "--verify", ref], cwd=repo_path)


def _branch_exists(repo_path: Path, branch_name: str) -> bool:
    return _git_ok(["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"], cwd=repo_path)


def _registered_worktree_paths(repo_path: Path) -> tuple[Path, ...]:
    """Every worktree path Git already knows about for this repository, including
    the main working tree itself."""
    output = _run_git(["worktree", "list", "--porcelain"], cwd=repo_path)
    paths: list[Path] = []
    for line in output.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line.removeprefix("worktree ")).resolve())
    return tuple(paths)


def create_worktree(request: WorktreeRequest) -> WorktreeMetadata:
    """Create an isolated worktree for `request.task_id`, from
    `request.base_commit` exactly, under `request.worktree_parent`. Never
    mutates `request`.

    Checks run in this order, each a distinct `WorktreeErrorCode`: the
    repository exists, its working tree is clean, the base commit resolves,
    the worktree parent exists, the derived worktree path stays inside it,
    the derived branch doesn't already exist, the derived directory doesn't
    already exist, and the derived path isn't already a registered worktree.
    Only then does it run `git worktree add`, and verify the result landed on
    exactly the expected branch and commit.

    Raises:
        WorktreeError: any of the checks above fails, or a `git` command
            needed along the way fails to run or exits nonzero.
    """
    repo_path = Path(request.repository_path)
    if not _is_git_repository(repo_path):
        raise WorktreeError(
            f"{repo_path}: not a git repository", code=WorktreeErrorCode.REPOSITORY_NOT_FOUND
        )

    if not _is_working_tree_clean(repo_path):
        raise WorktreeError(
            f"{repo_path}: working tree is not clean", code=WorktreeErrorCode.DIRTY_BASE_REPOSITORY
        )

    resolved_commit = _resolve_commit(repo_path, request.base_commit)

    worktree_parent = Path(request.worktree_parent)
    if not worktree_parent.is_dir():
        raise WorktreeError(
            f"{worktree_parent}: worktree parent does not exist",
            code=WorktreeErrorCode.WORKTREE_PARENT_NOT_FOUND,
        )

    resolved_parent = worktree_parent.resolve()
    worktree_path = (worktree_parent / request.worktree_dirname).resolve()
    if not worktree_path.is_relative_to(resolved_parent):
        # Not reachable through `TaskIdStr`'s own pattern (no separators or `..`
        # segments are ever accepted there) — this is a second, independent
        # layer that holds even if a `WorktreeRequest` reached this function
        # without going through that validator.
        raise WorktreeError(
            f"{worktree_path}: escapes the approved worktree parent {resolved_parent}",
            code=WorktreeErrorCode.WORKTREE_ESCAPES_PARENT,
        )

    branch_name = request.branch_name
    if _branch_exists(repo_path, branch_name):
        raise WorktreeError(
            f"{branch_name}: branch already exists", code=WorktreeErrorCode.BRANCH_COLLISION
        )

    if worktree_path.exists():
        raise WorktreeError(
            f"{worktree_path}: directory already exists", code=WorktreeErrorCode.DIRECTORY_COLLISION
        )

    if worktree_path in _registered_worktree_paths(repo_path):
        raise WorktreeError(
            f"{worktree_path}: already a registered worktree",
            code=WorktreeErrorCode.WORKTREE_ALREADY_REGISTERED,
        )

    _run_git(
        ["worktree", "add", "-b", branch_name, str(worktree_path), resolved_commit],
        cwd=repo_path,
    )

    actual_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    actual_commit = _run_git(["rev-parse", "HEAD"], cwd=worktree_path)
    if actual_branch != branch_name or actual_commit != resolved_commit:
        raise WorktreeError(
            f"created worktree is on {actual_branch}@{actual_commit}, "
            f"expected {branch_name}@{resolved_commit}",
            code=WorktreeErrorCode.VERIFICATION_FAILED,
        )

    return WorktreeMetadata(
        task_id=request.task_id,
        repository_path=str(repo_path),
        worktree_path=str(worktree_path),
        branch_name=branch_name,
        base_commit=resolved_commit,
    )


def check_cleanup_eligibility(worktree_path: Path) -> WorktreeCleanupEligibility:
    """Report whether `worktree_path` could safely be removed right now.

    Purely a read: never removes anything, never force-deletes anything — there
    is no removal function in this module at all. Eligible only when the path
    exists, is itself a Git working tree, and has no uncommitted changes.
    """
    path_str = str(worktree_path)
    if not worktree_path.is_dir() or not _git_ok(
        ["rev-parse", "--is-inside-work-tree"], cwd=worktree_path
    ):
        return WorktreeCleanupEligibility(
            worktree_path=path_str,
            eligible=False,
            status=WorktreeCleanupStatus.NOT_ELIGIBLE_NOT_FOUND,
        )

    if not _is_working_tree_clean(worktree_path):
        return WorktreeCleanupEligibility(
            worktree_path=path_str, eligible=False, status=WorktreeCleanupStatus.NOT_ELIGIBLE_DIRTY
        )

    return WorktreeCleanupEligibility(
        worktree_path=path_str, eligible=True, status=WorktreeCleanupStatus.ELIGIBLE
    )

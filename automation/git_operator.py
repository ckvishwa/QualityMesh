"""Controlled Git write operations for one task branch (QM-AUTO-012).

The only module in `automation` permitted to mutate a real Git repository or
call the GitHub CLI. Every `git`/`gh` invocation is a bounded subprocess call
with captured output, argument arrays, and no shell — the same discipline
`automation.worktree_manager` and `automation.command_runner` already use.
Respects task permissions strictly: `auto_commit` gates committing at all,
`auto_open_pr` (which requires `auto_commit`, enforced by `AutonomousTask`
itself) gates pushing and opening a PR, and `auto_merge` is never acted on —
if a task somehow has it set, this module refuses to run at all rather than
silently ignore the intent.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from automation.schemas.git_operation import (
    CommitOperationResult,
    DraftPullRequestResult,
    GitOperationErrorCode,
    GitOperationResult,
    PushOperationResult,
)
from automation.schemas.task import AutonomousTask

_GIT_TIMEOUT_SECONDS = 30.0
_GH_TIMEOUT_SECONDS = 30.0
_COMMIT_SUBJECT_MAX_LENGTH = 72


class GitOperationError(Exception):
    """Raised when a Git write operation cannot be safely performed, or a
    `git`/`gh` command it needed fails to run or exits nonzero.

    Carries a `GitOperationErrorCode` so callers can branch on the failure
    kind without parsing the message.
    """

    def __init__(self, message: str, *, code: GitOperationErrorCode) -> None:
        super().__init__(message)
        self.code = code


def _resolve_executable(name: str, *, not_found_code: GitOperationErrorCode) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise GitOperationError(f"{name!r} was not found on PATH", code=not_found_code)
    return resolved


def _run(
    executable: str,
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    failure_code: GitOperationErrorCode,
) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed args, resolved executable, no shell text
            [executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitOperationError(
            f"{executable} {' '.join(args)!r} failed to run: {exc}", code=failure_code
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitOperationError(
            f"{executable} {' '.join(args)!r} failed (exit {result.returncode}): {stderr}",
            code=failure_code,
        )
    return result.stdout.strip()


def _run_git(args: Sequence[str], *, cwd: Path) -> str:
    executable = _resolve_executable("git", not_found_code=GitOperationErrorCode.GIT_COMMAND_FAILED)
    return _run(
        executable,
        args,
        cwd=cwd,
        timeout=_GIT_TIMEOUT_SECONDS,
        failure_code=GitOperationErrorCode.GIT_COMMAND_FAILED,
    )


def _run_gh(args: Sequence[str], *, cwd: Path) -> str:
    executable = _resolve_executable("gh", not_found_code=GitOperationErrorCode.GH_CLI_NOT_FOUND)
    return _run(
        executable,
        args,
        cwd=cwd,
        timeout=_GH_TIMEOUT_SECONDS,
        failure_code=GitOperationErrorCode.GH_COMMAND_FAILED,
    )


def repository_has_changes(repo_path: Path) -> bool:
    """Whether `repo_path` has any staged or unstaged change (read-only)."""
    return _run_git(["status", "--porcelain"], cwd=repo_path) != ""


def has_unresolved_merge_state(repo_path: Path) -> bool:
    """Whether `repo_path` is mid-merge with an unresolved `MERGE_HEAD`
    (read-only). Uses `git rev-parse --git-path`, not a hardcoded `.git/`
    path, since a worktree's real git directory lives elsewhere."""
    git_path = _run_git(["rev-parse", "--git-path", "MERGE_HEAD"], cwd=repo_path)
    return (repo_path / git_path).exists()


def count_diff_line_changes(repo_path: Path, paths: Sequence[str]) -> int:
    """Total added-plus-removed lines across `paths` in the current, unstaged
    working-tree diff (read-only; never requires staging first).

    Added for QM-AUTO-013: `automation.evidence_recorder
    .verify_commit_preconditions` takes `diff_line_count` as a caller-supplied
    fact, and nothing else in this package computes one. A binary file (which
    `git diff --numstat` reports as `-\t-\t<path>` rather than numeric counts)
    contributes 0.
    """
    if not paths:
        return 0
    output = _run_git(["diff", "--numstat", "--", *paths], cwd=repo_path)
    total = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        added, removed = parts[0], parts[1]
        if added.isdigit():
            total += int(added)
        if removed.isdigit():
            total += int(removed)
    return total


def staged_paths(repo_path: Path) -> tuple[str, ...]:
    """The paths currently staged for commit (read-only)."""
    output = _run_git(["diff", "--cached", "--name-only"], cwd=repo_path)
    return tuple(line for line in output.splitlines() if line)


def stage_paths(repo_path: Path, paths: Sequence[str]) -> None:
    """Stage exactly `paths` — never `git add -A`, never a wildcard."""
    if not paths:
        raise GitOperationError("no paths to stage", code=GitOperationErrorCode.NOTHING_STAGED)
    _run_git(["add", "--", *paths], cwd=repo_path)


def _sanitize_commit_subject(text: str) -> str:
    cleaned = "".join(char for char in text if char.isprintable())
    cleaned = " ".join(cleaned.split())
    return cleaned[:_COMMIT_SUBJECT_MAX_LENGTH]


def build_commit_message(task: AutonomousTask) -> str:
    """A deterministic, task-derived commit message: the task's own title as
    a sanitized single-line subject, plus its ID in the trailer."""
    subject = _sanitize_commit_subject(task.title)
    return f"{subject}\n\ntask-id: {task.id}\n"


def create_commit(repo_path: Path, message: str) -> str:
    """Commit whatever is currently staged and return the new commit's SHA.

    Raises:
        GitOperationError: nothing is staged, or `git commit` fails.
    """
    if not staged_paths(repo_path):
        raise GitOperationError(
            "nothing is staged; refusing to commit", code=GitOperationErrorCode.NOTHING_STAGED
        )
    _run_git(["commit", "-m", message], cwd=repo_path)
    return _run_git(["rev-parse", "HEAD"], cwd=repo_path)


def push_branch(repo_path: Path, branch: str) -> None:
    """Push exactly `branch` to `origin` — never `--force`, never any other
    ref."""
    _run_git(["push", "origin", f"{branch}:{branch}"], cwd=repo_path)


def create_draft_pull_request(
    repo_path: Path, *, base: str, head: str, title: str, body: str
) -> DraftPullRequestResult:
    """Open a draft pull request via the GitHub CLI. Never ready-for-review,
    never merged — this function has no capability to produce either."""
    output = _run_gh(
        [
            "pr",
            "create",
            "--draft",
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo_path,
    )
    url = output.splitlines()[-1].strip() if output else ""
    number: int | None = None
    last_segment = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
    if last_segment.isdigit():
        number = int(last_segment)
    return DraftPullRequestResult(performed=True, url=url or None, number=number)


def run_git_operations(
    task: AutonomousTask,
    repo_path: Path,
    branch: str,
    base_branch: str,
    changed_paths: Sequence[str],
    pr_title: str,
    pr_body: str,
    *,
    preconditions_met: bool,
) -> GitOperationResult:
    """Stage, commit, push, and open a draft PR for `task`'s changes — only
    as far as `task.autonomy` and `preconditions_met` permit. Never mutates
    `task`. Never amends an existing commit, never force-pushes, never
    merges or marks a PR ready.

    Raises:
        GitOperationError: `task.autonomy.auto_merge` is set (unsupported,
            always rejected outright), staging or committing succeeds but
            the staged scope doesn't exactly match `changed_paths`, or any
            `git`/`gh` command fails.
    """
    if task.autonomy.auto_merge:
        raise GitOperationError(
            "auto_merge is not supported and is always rejected",
            code=GitOperationErrorCode.MERGE_NOT_SUPPORTED,
        )

    not_committed = CommitOperationResult
    not_pushed = PushOperationResult
    not_opened = DraftPullRequestResult

    if not task.autonomy.auto_commit or not preconditions_met:
        reason = (
            "auto_commit is disabled"
            if not task.autonomy.auto_commit
            else "commit preconditions were not met"
        )
        return GitOperationResult(
            commit=not_committed(performed=False, reason=reason),
            push=not_pushed(performed=False, reason="commit was not performed"),
            pull_request=not_opened(performed=False, reason="commit was not performed"),
        )

    stage_paths(repo_path, changed_paths)
    actual_staged = set(staged_paths(repo_path))
    if actual_staged != set(changed_paths):
        return GitOperationResult(
            commit=not_committed(
                performed=False,
                reason=f"staged scope {sorted(actual_staged)} != approved {sorted(changed_paths)}",
            ),
            push=not_pushed(performed=False, reason="commit was not performed"),
            pull_request=not_opened(performed=False, reason="commit was not performed"),
        )

    commit_sha = create_commit(repo_path, build_commit_message(task))
    commit_result = CommitOperationResult(performed=True, commit_sha=commit_sha)

    if not task.autonomy.auto_open_pr:
        return GitOperationResult(
            commit=commit_result,
            push=not_pushed(performed=False, reason="auto_open_pr is disabled"),
            pull_request=not_opened(performed=False, reason="auto_open_pr is disabled"),
        )

    push_branch(repo_path, branch)
    push_result = PushOperationResult(performed=True, branch=branch)

    pr_result = create_draft_pull_request(
        repo_path, base=base_branch, head=branch, title=pr_title, body=pr_body
    )

    return GitOperationResult(commit=commit_result, push=push_result, pull_request=pr_result)

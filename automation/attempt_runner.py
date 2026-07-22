"""Single-attempt lifecycle: compose the deterministic components into one
run for one task, with no coding-model call anywhere in it (QM-AUTO-009).

    Selected task
    -> Create isolated worktree            (automation.worktree_manager)
    -> Build focused context                (automation.context_builder)
    -> Persist updated cache                (automation.cache_manager)
    -> Build validation plan                (automation.validation_planner)
    -> Execute validation plan              (automation.validation_executor)
    -> Return immutable attempt evidence

Every input is explicit — repository path, base commit, worktree parent,
cache-manifest path, command policy, validation-execution policy — never read
from hidden global configuration. Fails closed at the first failing phase: a
setup failure (worktree, cache, context, planning) or a genuine validation
execution error stops the sequence immediately; a validation *failure* (a
normal `FAILED`/`TIMED_OUT` run) is still a complete, successful composition
and is reported as such, not as an error.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from automation.cache_manager import CacheError, load_manifest, write_manifest_atomically
from automation.context_builder import ContextBuildError, build_context
from automation.schemas.attempt import AttemptOutcome, AttemptResult
from automation.schemas.command import CommandPolicy
from automation.schemas.context import ContextBuildResult
from automation.schemas.task import AutonomousTask
from automation.schemas.validation_run import (
    AggregateOutcome,
    ValidationExecutionPolicy,
    ValidationRunResult,
)
from automation.schemas.worktree import WorktreeRequest
from automation.validation_executor import ValidationExecutionError, execute_validation_plan
from automation.validation_planner import ValidationPlanError, build_validation_plan
from automation.worktree_manager import WorktreeError, create_worktree


def run_attempt(
    task: AutonomousTask,
    repository_path: str,
    base_commit: str,
    worktree_parent: str,
    cache_manifest_path: str,
    command_policy: CommandPolicy,
    execution_policy: ValidationExecutionPolicy,
) -> AttemptResult:
    """Run one full attempt for `task`. Never mutates `task`, `command_policy`,
    or `execution_policy`. Never commits, pushes, deletes the worktree, calls
    a model, or changes `task.status` — this function only composes the five
    deterministic phases and reports what happened.

    `branch`/`worktree_path` on the returned `AttemptResult` are the
    deterministic values implied by `task.id` (`agent/<task.id>` and
    `<worktree_parent>/<task.id>`), computed before worktree creation is even
    attempted, so they are populated even when creation itself fails.

    Always returns an `AttemptResult` — never raises. A setup failure
    (worktree creation, cache load, context build, cache persistence, or
    validation planning) or a `ValidationExecutionError` from the executor is
    caught and reported as `SETUP_ERROR`/`EXECUTION_ERROR` with
    `error_message` set to the original failure's message, never hidden.
    """
    start_monotonic = time.monotonic()
    started_at = datetime.now(UTC)

    branch = f"agent/{task.id}"
    worktree_path = str(Path(worktree_parent) / task.id)

    def finish(
        outcome: AttemptOutcome,
        *,
        context_result: ContextBuildResult | None = None,
        validation_result: ValidationRunResult | None = None,
        error_message: str | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            task_id=task.id,
            base_commit=base_commit,
            branch=branch,
            worktree_path=worktree_path,
            context_result=context_result,
            validation_result=validation_result,
            outcome=outcome,
            error_message=error_message,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            duration_seconds=time.monotonic() - start_monotonic,
        )

    try:
        worktree_request = WorktreeRequest(
            task_id=task.id,
            repository_path=repository_path,
            base_commit=base_commit,
            worktree_parent=worktree_parent,
        )
        worktree_metadata = create_worktree(worktree_request)
    except WorktreeError as exc:
        return finish(AttemptOutcome.SETUP_ERROR, error_message=f"worktree creation failed: {exc}")

    try:
        manifest = load_manifest(Path(cache_manifest_path))
    except CacheError as exc:
        return finish(
            AttemptOutcome.SETUP_ERROR, error_message=f"cache manifest load failed: {exc}"
        )

    try:
        context_result = build_context(task, Path(worktree_metadata.worktree_path), manifest)
    except (ContextBuildError, CacheError) as exc:
        return finish(AttemptOutcome.SETUP_ERROR, error_message=f"context build failed: {exc}")

    try:
        write_manifest_atomically(Path(cache_manifest_path), context_result.manifest)
    except OSError as exc:
        return finish(
            AttemptOutcome.SETUP_ERROR,
            context_result=context_result,
            error_message=f"cache persistence failed: {exc}",
        )

    try:
        plan = build_validation_plan(task, command_policy, worktree_metadata.worktree_path)
    except ValidationPlanError as exc:
        return finish(
            AttemptOutcome.SETUP_ERROR,
            context_result=context_result,
            error_message=f"validation planning failed: {exc}",
        )

    try:
        validation_result = execute_validation_plan(plan, execution_policy)
    except ValidationExecutionError as exc:
        return finish(
            AttemptOutcome.EXECUTION_ERROR,
            context_result=context_result,
            error_message=f"validation execution failed: {exc}",
        )

    if validation_result.outcome is AggregateOutcome.PASSED:
        outcome = AttemptOutcome.PASSED
    elif validation_result.outcome is AggregateOutcome.TIMED_OUT:
        outcome = AttemptOutcome.TIMED_OUT
    else:
        outcome = AttemptOutcome.VALIDATION_FAILED

    return finish(outcome, context_result=context_result, validation_result=validation_result)

"""Top-level autonomous task orchestrator (QM-AUTO-013).

Composes every earlier component into one controlled run of exactly one
backlog task:

    Load backlog
    -> Fail closed if another task is already ACTIVE
    -> Select the explicit task, or the next eligible task automatically
    -> Atomically mark it ACTIVE                     (automation.backlog_store)
    -> Create its isolated worktree                  (automation.worktree_manager)
    -> Run the bounded provider/repair loop           (automation.repair_loop),
       re-validating the same worktree on every attempt (context building,
       cache persistence, validation planning and execution — the same
       phases `automation.attempt_runner.run_attempt` composes for a single
       attempt, run here directly rather than through it, since a repair
       loop's later attempts must reuse the one worktree `run_attempt` would
       otherwise try to recreate)
    -> Record evidence                                (automation.evidence_recorder)
    -> Perform permitted commit/draft-PR operations    (automation.git_operator)
    -> Mark the task COMPLETED or BLOCKED             (automation.backlog_store)
    -> Return one immutable, typed OrchestratorRunResult

Every dependency this needs — backlog path, repository path, base commit,
worktree parent, cache/evidence paths, allowed risk levels, a
`CodingProvider`, validation/repair policies, and Git-write permission — is
an explicit `OrchestratorInputs` field; nothing here reads a hidden global or
environment variable. Runs exactly one task per call, never scans the whole
repository, never force-deletes a worktree, never force-pushes, never merges,
never deploys.

Fails closed at the first failing phase: a setup failure (backlog load, the
initial `ACTIVE` transition, worktree creation, context priming) is
`SETUP_FAILED`; a repair-loop outcome other than `COMPLETED` is mapped to the
matching specific `OrchestratorOutcome`; an evidence-assembly failure is
`EVIDENCE_FAILED`; a Git-operation failure is `GIT_OPERATION_FAILED`. Once a
task has been marked `ACTIVE`, every one of these failure paths marks it
`BLOCKED` before returning. A failure to persist that *terminal* transition
itself is never caught — it propagates as `BacklogStoreError`/`OSError`,
since the task's own outcome has already been decided and a state that stays
stuck `ACTIVE` afterward is unrecoverable corruption, not an ordinary result.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from automation.backlog_store import BacklogStoreError, apply_status_transition
from automation.cache_manager import (
    CacheError,
    compute_content_hash,
    load_manifest,
    write_manifest_atomically,
)
from automation.context_builder import ContextBuildError, build_context
from automation.evidence_recorder import (
    EvidenceRecorderError,
    build_evidence_package,
    verify_commit_preconditions,
    write_evidence_atomically,
)
from automation.git_operator import (
    GitOperationError,
    count_diff_line_changes,
    has_unresolved_merge_state,
    repository_has_changes,
    run_git_operations,
)
from automation.provider import CodingProvider
from automation.repair_loop import run_repair_loop
from automation.schemas.command import CommandPolicy
from automation.schemas.context import ContextBuildResult
from automation.schemas.evidence_run import ChangedFileRecord, EvidencePackage
from automation.schemas.git_operation import (
    CommitOperationResult,
    DraftPullRequestResult,
    GitOperationResult,
    PushOperationResult,
)
from automation.schemas.orchestrator import (
    OrchestratorInputs,
    OrchestratorOutcome,
    OrchestratorRunResult,
)
from automation.schemas.planning import TaskSelectionResult
from automation.schemas.provider import ContextAttachment, ProviderResponse
from automation.schemas.repair import RepairLoopResult, RepairOutcome
from automation.schemas.task import AutonomousTask, TaskBacklog, TaskStatus
from automation.schemas.validation_run import ValidationExecutionPolicy, ValidationRunResult
from automation.schemas.worktree import WorktreeRequest
from automation.task_loader import BacklogLoadError, load_backlog_file
from automation.task_selector import select_next_task, select_task_by_id
from automation.validation_executor import execute_validation_plan
from automation.validation_planner import build_validation_plan
from automation.worktree_manager import WorktreeError, create_worktree

_REPAIR_FAILURE_OUTCOME: dict[RepairOutcome, OrchestratorOutcome] = {
    RepairOutcome.ATTEMPTS_EXHAUSTED: OrchestratorOutcome.VALIDATION_FAILED,
    RepairOutcome.REPEATED_FAILURE: OrchestratorOutcome.VALIDATION_FAILED,
    RepairOutcome.PROVIDER_ERROR: OrchestratorOutcome.PROVIDER_FAILED,
    RepairOutcome.BUDGET_EXCEEDED: OrchestratorOutcome.BUDGET_EXCEEDED,
    RepairOutcome.POLICY_VIOLATION: OrchestratorOutcome.POLICY_VIOLATION,
    RepairOutcome.EXECUTION_ERROR: OrchestratorOutcome.SETUP_FAILED,
}


def _map_repair_failure(outcome: RepairOutcome) -> OrchestratorOutcome:
    """Never called with `RepairOutcome.COMPLETED` — callers branch on that
    first. Falls back to the generic `OrchestratorOutcome.BLOCKED` for any
    `RepairOutcome` this mapping doesn't (yet) name explicitly."""
    return _REPAIR_FAILURE_OUTCOME.get(outcome, OrchestratorOutcome.BLOCKED)


def _build_context_attachments(
    worktree_path: str, context_result: ContextBuildResult
) -> tuple[ContextAttachment, ...]:
    """Read the current text of every context file `automation.context_builder
    .build_context` already validated as safe (normalized, inside the
    repository root, not a directory, existing) from the worktree, so the
    provider sees exactly the version at `base_commit` — never the shared
    repository checkout, which may be on a different commit entirely."""
    worktree_root = Path(worktree_path)
    return tuple(
        ContextAttachment(
            path=record.path, content=(worktree_root / record.path).read_text(encoding="utf-8")
        )
        for record in context_result.records
    )


def _run_validation_once(
    task: AutonomousTask,
    worktree_path: str,
    cache_manifest_path: Path,
    command_policy: CommandPolicy,
    execution_policy: ValidationExecutionPolicy,
) -> ValidationRunResult:
    """Re-run context building, cache persistence, validation planning and
    validation execution once against an already-created worktree. Never
    creates or removes the worktree itself — `run_orchestrator_cycle` creates
    exactly one per task, and this runs on every repair-loop attempt against
    it, so a later attempt never collides with an earlier one's `git worktree
    add`. Any exception here (`CacheError`, `ContextBuildError`,
    `ValidationPlanError`, `ValidationExecutionError`, `OSError`) is left to
    propagate: `automation.repair_loop.run_repair_loop`'s own broad catch
    around this callback maps it to `RepairOutcome.EXECUTION_ERROR`.
    """
    manifest = load_manifest(cache_manifest_path)
    context_result = build_context(task, Path(worktree_path), manifest)
    write_manifest_atomically(cache_manifest_path, context_result.manifest)
    plan = build_validation_plan(task, command_policy, worktree_path)
    return execute_validation_plan(plan, execution_policy)


def _hash_changed_files(
    worktree_path: Path, changed_paths: Sequence[str]
) -> tuple[ChangedFileRecord, ...]:
    """Hash every path in `changed_paths` from its current bytes under
    `worktree_path` — mirrors `automation.evidence_recorder`'s own internal
    hashing, duplicated here (not imported: that helper is private) so
    `verify_commit_preconditions` can be given real `ChangedFileRecord`s
    before `build_evidence_package` (which needs the completed
    `CommitPreconditionResult` as one of its own inputs) is called.

    Raises:
        EvidenceRecorderError: a path in `changed_paths` cannot be read.
    """
    records: list[ChangedFileRecord] = []
    for relative_path in changed_paths:
        file_path = worktree_path / relative_path
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            raise EvidenceRecorderError(f"{relative_path}: file cannot be read") from exc
        records.append(
            ChangedFileRecord(
                path=relative_path, sha256=compute_content_hash(data), size_bytes=len(data)
            )
        )
    return tuple(records)


def run_orchestrator_cycle(
    inputs: OrchestratorInputs, provider: CodingProvider
) -> OrchestratorRunResult:
    """Run one full orchestrator cycle for at most one backlog task. Never
    mutates `inputs`. Always returns an `OrchestratorRunResult` for every
    ordinary outcome; raises only if a *terminal* backlog-status persistence
    step fails after the task's outcome has already been decided (see this
    module's docstring)."""
    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)

    def finish(
        outcome: OrchestratorOutcome,
        reason: str,
        *,
        task_id: str | None = None,
        starting_status: TaskStatus | None = None,
        final_status: TaskStatus | None = None,
        selection_result: TaskSelectionResult | None = None,
        repair_result: RepairLoopResult | None = None,
        evidence_path: str | None = None,
        evidence_result: EvidencePackage | None = None,
        git_operation_result: GitOperationResult | None = None,
    ) -> OrchestratorRunResult:
        ended_at = datetime.now(UTC)
        return OrchestratorRunResult(
            run_id=run_id,
            task_id=task_id,
            starting_status=starting_status,
            final_status=final_status,
            selection_result=selection_result,
            repair_result=repair_result,
            evidence_path=evidence_path,
            evidence_result=evidence_result,
            git_operation_result=git_operation_result,
            outcome=outcome,
            reason=reason,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=(ended_at - started_at).total_seconds(),
        )

    backlog_path = Path(inputs.backlog_path)
    try:
        backlog: TaskBacklog = load_backlog_file(backlog_path)
    except BacklogLoadError as exc:
        return finish(OrchestratorOutcome.SETUP_FAILED, f"backlog load failed: {exc}")

    active_task = next((task for task in backlog.tasks if task.status is TaskStatus.ACTIVE), None)
    if active_task is not None:
        return finish(
            OrchestratorOutcome.ACTIVE_TASK_EXISTS,
            f"task {active_task.id!r} is already active",
            task_id=active_task.id,
            starting_status=TaskStatus.ACTIVE,
        )

    if inputs.task_id is not None:
        selection = select_task_by_id(inputs.task_id, backlog, inputs.allowed_risk_levels)
    else:
        selection = select_next_task(backlog, inputs.allowed_risk_levels)

    if not selection.selected:
        reason = selection.reason.value if selection.reason is not None else "task is not eligible"
        return finish(OrchestratorOutcome.NO_TASK_SELECTED, reason, selection_result=selection)

    task_id = selection.selected_task_id
    if task_id is None:
        # Unreachable: `selection.selected` is defined as `selected_task_id is
        # not None`, checked immediately above.
        return finish(
            OrchestratorOutcome.NO_TASK_SELECTED,
            "selection reported selected with no task id",
            selection_result=selection,
        )

    task = next(candidate for candidate in backlog.tasks if candidate.id == task_id)
    starting_status = task.status

    try:
        backlog = apply_status_transition(backlog_path, backlog, task_id, TaskStatus.ACTIVE)
    except BacklogStoreError as exc:
        return finish(
            OrchestratorOutcome.SETUP_FAILED,
            f"failed to mark {task_id!r} active: {exc}",
            task_id=task_id,
            starting_status=starting_status,
            selection_result=selection,
        )
    except OSError as exc:
        return finish(
            OrchestratorOutcome.SETUP_FAILED,
            f"failed to persist active status for {task_id!r}: {exc}",
            task_id=task_id,
            starting_status=starting_status,
            selection_result=selection,
        )

    task = next(candidate for candidate in backlog.tasks if candidate.id == task_id)
    cache_manifest_path = Path(inputs.cache_manifest_path)

    try:
        worktree_metadata = create_worktree(
            WorktreeRequest(
                task_id=task.id,
                repository_path=inputs.repository_path,
                base_commit=inputs.base_commit,
                worktree_parent=inputs.worktree_parent,
            )
        )
    except WorktreeError as exc:
        apply_status_transition(backlog_path, backlog, task_id, TaskStatus.BLOCKED)
        return finish(
            OrchestratorOutcome.SETUP_FAILED,
            f"worktree creation failed: {exc}",
            task_id=task_id,
            starting_status=starting_status,
            final_status=TaskStatus.BLOCKED,
            selection_result=selection,
        )

    try:
        manifest = load_manifest(cache_manifest_path)
        context_result = build_context(task, Path(worktree_metadata.worktree_path), manifest)
        write_manifest_atomically(cache_manifest_path, context_result.manifest)
        context_attachments = _build_context_attachments(
            worktree_metadata.worktree_path, context_result
        )
    except (CacheError, ContextBuildError, OSError, UnicodeDecodeError) as exc:
        apply_status_transition(backlog_path, backlog, task_id, TaskStatus.BLOCKED)
        return finish(
            OrchestratorOutcome.SETUP_FAILED,
            f"context priming failed: {exc}",
            task_id=task_id,
            starting_status=starting_status,
            final_status=TaskStatus.BLOCKED,
            selection_result=selection,
        )

    def execute_attempt(_response: ProviderResponse, _attempt_number: int) -> ValidationRunResult:
        return _run_validation_once(
            task,
            worktree_metadata.worktree_path,
            cache_manifest_path,
            inputs.command_policy,
            inputs.execution_policy,
        )

    repair_result = run_repair_loop(
        task,
        context_attachments,
        provider,
        execute_attempt,
        token_budget=inputs.token_budget,
        max_output_tokens=inputs.max_output_tokens,
    )

    if repair_result.outcome is not RepairOutcome.COMPLETED:
        apply_status_transition(backlog_path, backlog, task_id, TaskStatus.BLOCKED)
        return finish(
            _map_repair_failure(repair_result.outcome),
            repair_result.stop_reason,
            task_id=task_id,
            starting_status=starting_status,
            final_status=TaskStatus.BLOCKED,
            selection_result=selection,
            repair_result=repair_result,
        )

    last_response = repair_result.attempts[-1].response
    changed_paths = last_response.changed_files
    worktree_root = Path(worktree_metadata.worktree_path)

    try:
        changed_file_records = _hash_changed_files(worktree_root, changed_paths)
        has_changes = repository_has_changes(worktree_root)
        merge_state_clean = not has_unresolved_merge_state(worktree_root)
        diff_line_count = count_diff_line_changes(worktree_root, changed_paths)
        precondition_result = verify_commit_preconditions(
            task,
            changed_file_records,
            diff_line_count=diff_line_count,
            validation_passed=True,
            has_changes=has_changes,
            merge_state_clean=merge_state_clean,
            secret_scan_clean=inputs.secret_scan_clean,
        )
        evidence = build_evidence_package(
            task,
            worktree_path=worktree_metadata.worktree_path,
            changed_paths=changed_paths,
            base_commit=inputs.base_commit,
            result_commit=None,
            branch=worktree_metadata.branch_name,
            context_files_supplied=tuple(record.path for record in context_result.records),
            provider_name=last_response.provider_name,
            model_identifier=last_response.model_identifier,
            provider_usage=repair_result.total_usage,
            attempts=repair_result.attempts,
            precondition_result=precondition_result,
            final_outcome=repair_result.outcome.value,
            created_at=datetime.now(UTC),
        )
        write_evidence_atomically(Path(inputs.evidence_path), evidence)
    except (EvidenceRecorderError, GitOperationError, OSError) as exc:
        apply_status_transition(backlog_path, backlog, task_id, TaskStatus.BLOCKED)
        return finish(
            OrchestratorOutcome.EVIDENCE_FAILED,
            f"evidence recording failed: {exc}",
            task_id=task_id,
            starting_status=starting_status,
            final_status=TaskStatus.BLOCKED,
            selection_result=selection,
            repair_result=repair_result,
        )

    if not inputs.allow_git_writes:
        git_result = GitOperationResult(
            commit=CommitOperationResult(performed=False, reason="caller did not allow Git writes"),
            push=PushOperationResult(performed=False, reason="caller did not allow Git writes"),
            pull_request=DraftPullRequestResult(
                performed=False, reason="caller did not allow Git writes"
            ),
        )
    else:
        try:
            git_result = run_git_operations(
                task,
                worktree_root,
                worktree_metadata.branch_name,
                inputs.base_branch,
                changed_paths,
                task.title,
                f"{task.objective}\n\ntask-id: {task.id}\n",
                preconditions_met=precondition_result.eligible,
            )
        except GitOperationError as exc:
            apply_status_transition(backlog_path, backlog, task_id, TaskStatus.BLOCKED)
            return finish(
                OrchestratorOutcome.GIT_OPERATION_FAILED,
                f"git operation failed: {exc}",
                task_id=task_id,
                starting_status=starting_status,
                final_status=TaskStatus.BLOCKED,
                selection_result=selection,
                repair_result=repair_result,
                evidence_path=inputs.evidence_path,
                evidence_result=evidence,
            )

    apply_status_transition(backlog_path, backlog, task_id, TaskStatus.COMPLETED)
    return finish(
        OrchestratorOutcome.COMPLETED,
        "task completed successfully",
        task_id=task_id,
        starting_status=starting_status,
        final_status=TaskStatus.COMPLETED,
        selection_result=selection,
        repair_result=repair_result,
        evidence_path=inputs.evidence_path,
        evidence_result=evidence,
        git_operation_result=git_result,
    )

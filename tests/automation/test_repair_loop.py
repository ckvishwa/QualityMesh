"""Unit tests for the bounded agent repair loop (QM-AUTO-011)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.provider import ProviderError
from automation.repair_loop import run_repair_loop
from automation.schemas.command import CommandOutcome, CommandRequest, CommandResult
from automation.schemas.provider import (
    ContextAttachment,
    ProviderErrorCode,
    ProviderOperationMode,
    ProviderRequest,
    ProviderResponse,
    ProviderStopReason,
    ProviderUsage,
)
from automation.schemas.repair import ContextExpansionRequest, RepairOutcome
from automation.schemas.task import AutonomousTask
from automation.schemas.validation_run import (
    AggregateOutcome,
    ValidationEntryResult,
    ValidationEntryStatus,
    ValidationRunResult,
)

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_task(**overrides: Any) -> AutonomousTask:
    task: dict[str, Any] = {
        "id": "task-a",
        "title": "Task A",
        "status": "ready",
        "priority": 1,
        "risk": "low",
        "objective": "Do something.",
        "allowed_paths": ["automation/"],
        "acceptance_criteria": ["It works"],
        "validation_commands": ["ruff check ."],
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def build_response(**overrides: Any) -> ProviderResponse:
    fields: dict[str, Any] = {
        "provider_name": "fake",
        "model_identifier": "fake-model-1",
        "text": "Implemented the change.",
        "changed_files": ["automation/foo.py"],
        "usage": ProviderUsage(input_tokens=100, output_tokens=50),
        "stop_reason": "completed",
        "success": True,
    }
    fields.update(overrides)
    return ProviderResponse.model_validate(fields)


def build_validation_result(
    outcome: AggregateOutcome, *, exit_code_override: int | None = None
) -> ValidationRunResult:
    cmd_request = CommandRequest(
        executable="ruff", arguments=("check", "."), working_directory="/worktree"
    )
    if outcome is AggregateOutcome.PASSED:
        status = ValidationEntryStatus.PASSED
        exit_code: int | None = 0
        command_outcome = CommandOutcome.COMPLETED
    elif outcome is AggregateOutcome.TIMED_OUT:
        status = ValidationEntryStatus.TIMED_OUT
        exit_code = None
        command_outcome = CommandOutcome.TIMED_OUT
    else:
        status = ValidationEntryStatus.FAILED
        exit_code = exit_code_override if exit_code_override is not None else 1
        command_outcome = CommandOutcome.COMPLETED

    command_result = CommandResult(
        executable="ruff",
        arguments=("check", "."),
        working_directory="/worktree",
        outcome=command_outcome,
        exit_code=exit_code,
        stdout="",
        stderr="lint error" if outcome is AggregateOutcome.FAILED else "",
        duration_seconds=0.01,
        stdout_truncated=False,
        stderr_truncated=False,
    )
    entry_result = ValidationEntryResult(
        index=0, request=cmd_request, status=status, command_result=command_result
    )
    return ValidationRunResult(
        task_id="task-a",
        outcome=outcome,
        results=(entry_result,),
        executed_count=1,
        skipped_count=0,
        duration_seconds=0.01,
    )


class SequenceProvider:
    """A CodingProvider returning each configured item, in order, once per
    call — a `ProviderResponse` is returned, a `ProviderError` is raised."""

    def __init__(self, items: list[ProviderResponse | ProviderError]) -> None:
        self._items = list(items)
        self._index = 0
        self.received_requests: list[ProviderRequest] = []

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        self.received_requests.append(request)
        item = self._items[self._index]
        self._index += 1
        if isinstance(item, ProviderError):
            raise item
        return item


def always_pass_validation(response: ProviderResponse, attempt_number: int) -> ValidationRunResult:
    return build_validation_result(AggregateOutcome.PASSED)


def always_fail_validation(response: ProviderResponse, attempt_number: int) -> ValidationRunResult:
    return build_validation_result(AggregateOutcome.FAILED)


def distinct_fail_validation(
    response: ProviderResponse, attempt_number: int
) -> ValidationRunResult:
    """Fails every attempt with a distinct normalized failure (different
    exit code per attempt), so REPEATED_FAILURE never fires — used by tests
    that need to observe ATTEMPTS_EXHAUSTED/BUDGET_EXCEEDED in isolation."""
    return build_validation_result(AggregateOutcome.FAILED, exit_code_override=10 + attempt_number)


CONTEXT = (ContextAttachment(path="automation/foo.py", content="print(1)\n"),)

# --------------------------------------------------------------------------------------
# Success paths
# --------------------------------------------------------------------------------------


def test_success_on_first_attempt() -> None:
    task = build_task()
    provider = SequenceProvider([build_response()])

    result = run_repair_loop(task, CONTEXT, provider, always_pass_validation, token_budget=10_000)

    assert result.outcome is RepairOutcome.COMPLETED
    assert len(result.attempts) == 1
    assert result.attempts[0].mode is ProviderOperationMode.IMPLEMENT


def test_failure_then_successful_repair() -> None:
    task = build_task()
    provider = SequenceProvider([build_response(), build_response()])
    validations = iter(
        [
            build_validation_result(AggregateOutcome.FAILED),
            build_validation_result(AggregateOutcome.PASSED),
        ]
    )

    def execute_attempt(response: ProviderResponse, attempt_number: int) -> ValidationRunResult:
        return next(validations)

    result = run_repair_loop(task, CONTEXT, provider, execute_attempt, token_budget=10_000)

    assert result.outcome is RepairOutcome.COMPLETED
    assert len(result.attempts) == 2
    assert result.attempts[0].mode is ProviderOperationMode.IMPLEMENT
    assert result.attempts[1].mode is ProviderOperationMode.REPAIR


# --------------------------------------------------------------------------------------
# Stop conditions
# --------------------------------------------------------------------------------------


def test_attempts_exhausted() -> None:
    task = build_task(limits={"max_attempts": 2})
    provider = SequenceProvider([build_response(), build_response()])

    result = run_repair_loop(task, CONTEXT, provider, distinct_fail_validation, token_budget=10_000)

    assert result.outcome is RepairOutcome.ATTEMPTS_EXHAUSTED
    assert len(result.attempts) == 2


def test_repeated_identical_failure_stops_loop() -> None:
    task = build_task(limits={"max_attempts": 5})
    provider = SequenceProvider([build_response(), build_response(), build_response()])

    result = run_repair_loop(task, CONTEXT, provider, always_fail_validation, token_budget=10_000)

    assert result.outcome is RepairOutcome.REPEATED_FAILURE
    assert len(result.attempts) == 2


def test_budget_exceeded_before_another_call() -> None:
    task = build_task(limits={"max_attempts": 5})
    response = build_response(usage=ProviderUsage(input_tokens=60, output_tokens=60))
    provider = SequenceProvider([response, response, response])

    result = run_repair_loop(task, CONTEXT, provider, distinct_fail_validation, token_budget=150)

    assert result.outcome is RepairOutcome.BUDGET_EXCEEDED
    assert len(provider.received_requests) == 2
    assert result.total_usage.total_tokens == 240


def test_provider_failure_stops_loop() -> None:
    task = build_task()
    error = ProviderError("rate limited", code=ProviderErrorCode.RATE_LIMITED)
    provider = SequenceProvider([error])

    result = run_repair_loop(task, CONTEXT, provider, always_pass_validation, token_budget=10_000)

    assert result.outcome is RepairOutcome.PROVIDER_ERROR
    assert len(result.attempts) == 0


def test_policy_violation_stops_loop() -> None:
    task = build_task(allowed_paths=["automation/"], forbidden_paths=["automation/secrets/"])
    provider = SequenceProvider([build_response(changed_files=["automation/secrets/keys.py"])])

    result = run_repair_loop(task, CONTEXT, provider, always_pass_validation, token_budget=10_000)

    assert result.outcome is RepairOutcome.POLICY_VIOLATION
    assert len(result.attempts) == 0


def test_outside_allowed_paths_is_policy_violation() -> None:
    task = build_task(allowed_paths=["automation/"])
    provider = SequenceProvider([build_response(changed_files=["src/qualitymesh/other.py"])])

    result = run_repair_loop(task, CONTEXT, provider, always_pass_validation, token_budget=10_000)

    assert result.outcome is RepairOutcome.POLICY_VIOLATION


def test_execution_error_when_attempt_callback_raises() -> None:
    task = build_task()
    provider = SequenceProvider([build_response()])

    def raising_execute_attempt(
        response: ProviderResponse, attempt_number: int
    ) -> ValidationRunResult:
        raise RuntimeError("lifecycle blew up")

    result = run_repair_loop(task, CONTEXT, provider, raising_execute_attempt, token_budget=10_000)

    assert result.outcome is RepairOutcome.EXECUTION_ERROR
    assert "lifecycle blew up" in result.stop_reason


# --------------------------------------------------------------------------------------
# Validation, not provider claims, determines success
# --------------------------------------------------------------------------------------


def test_validation_not_provider_claim_determines_success() -> None:
    task = build_task(limits={"max_attempts": 1})
    provider = SequenceProvider(
        [build_response(success=True, stop_reason=ProviderStopReason.COMPLETED)]
    )

    result = run_repair_loop(task, CONTEXT, provider, always_fail_validation, token_budget=10_000)

    assert result.outcome is RepairOutcome.ATTEMPTS_EXHAUSTED
    assert result.attempts[0].response.success is True
    assert result.attempts[0].validation_result.outcome is AggregateOutcome.FAILED


# --------------------------------------------------------------------------------------
# Repair payload minimality
# --------------------------------------------------------------------------------------


def test_minimal_repair_payload_excludes_original_context() -> None:
    task = build_task()
    provider = SequenceProvider([build_response(), build_response()])
    validations = iter(
        [
            build_validation_result(AggregateOutcome.FAILED),
            build_validation_result(AggregateOutcome.PASSED),
        ]
    )

    def execute_attempt(response: ProviderResponse, attempt_number: int) -> ValidationRunResult:
        return next(validations)

    run_repair_loop(task, CONTEXT, provider, execute_attempt, token_budget=10_000)

    implement_request, repair_request = provider.received_requests
    assert implement_request.context == CONTEXT
    repair_paths = {attachment.path for attachment in repair_request.context}
    assert "automation/foo.py" not in repair_paths
    assert "VALIDATION_FAILURES" in repair_paths


# --------------------------------------------------------------------------------------
# Bounded context expansions
# --------------------------------------------------------------------------------------


def test_bounded_context_expansions() -> None:
    task = build_task(limits={"max_attempts": 5})
    expansion_response = build_response(text="CONTEXT_REQUEST: automation/bar.py | need it")
    provider = SequenceProvider(
        [expansion_response, expansion_response, expansion_response, build_response()]
    )
    approvals: list[ContextExpansionRequest] = []

    def approve(request: ContextExpansionRequest) -> ContextAttachment | None:
        approvals.append(request)
        return ContextAttachment(path=request.requested_path, content="content")

    result = run_repair_loop(
        task,
        CONTEXT,
        provider,
        always_pass_validation,
        token_budget=10_000,
        context_expansion_policy=approve,
        max_context_expansions=2,
    )

    assert len(approvals) == 2
    assert len(result.context_expansions) == 2
    assert all(expansion.approved for expansion in result.context_expansions)
    # Third CONTEXT_REQUEST response, once the bound is hit, is treated as a
    # real (failing-to-be-useful) attempt rather than negotiated forever.
    assert len(result.attempts) >= 1


def test_rejected_context_expansion_is_recorded() -> None:
    task = build_task(limits={"max_attempts": 3})
    expansion_response = build_response(text="CONTEXT_REQUEST: automation/bar.py | need it")
    provider = SequenceProvider([expansion_response, build_response()])

    def reject(request: ContextExpansionRequest) -> ContextAttachment | None:
        return None

    result = run_repair_loop(
        task,
        CONTEXT,
        provider,
        always_pass_validation,
        token_budget=10_000,
        context_expansion_policy=reject,
    )

    assert len(result.context_expansions) == 1
    assert result.context_expansions[0].approved is False
    assert result.outcome is RepairOutcome.COMPLETED


# --------------------------------------------------------------------------------------
# Ordering, usage, no double calls
# --------------------------------------------------------------------------------------


def test_attempt_ordering() -> None:
    task = build_task(limits={"max_attempts": 3})
    provider = SequenceProvider([build_response(), build_response(), build_response()])

    result = run_repair_loop(task, CONTEXT, provider, always_fail_validation, token_budget=10_000)

    assert [attempt.attempt_number for attempt in result.attempts] == list(
        range(1, len(result.attempts) + 1)
    )


def test_usage_aggregation() -> None:
    task = build_task(limits={"max_attempts": 2})
    provider = SequenceProvider(
        [
            build_response(usage=ProviderUsage(input_tokens=10, output_tokens=5)),
            build_response(usage=ProviderUsage(input_tokens=20, output_tokens=8)),
        ]
    )

    result = run_repair_loop(task, CONTEXT, provider, always_fail_validation, token_budget=10_000)

    assert result.total_usage.input_tokens == 30
    assert result.total_usage.output_tokens == 13
    assert result.total_usage.total_tokens == 43


def test_no_double_calls_per_attempt() -> None:
    task = build_task(limits={"max_attempts": 3})
    provider = SequenceProvider([build_response(), build_response(), build_response()])

    result = run_repair_loop(task, CONTEXT, provider, always_fail_validation, token_budget=10_000)

    assert len(provider.received_requests) == len(result.attempts)


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_no_input_mutation() -> None:
    task = build_task()
    provider = SequenceProvider([build_response()])
    task_before = copy.deepcopy(task)
    context_before = copy.deepcopy(CONTEXT)

    run_repair_loop(task, CONTEXT, provider, always_pass_validation, token_budget=10_000)

    assert task == task_before
    assert CONTEXT == context_before

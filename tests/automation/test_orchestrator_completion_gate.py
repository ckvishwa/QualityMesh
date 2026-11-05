"""Regression tests: a cycle whose commit preconditions fail must never mark
the task COMPLETED (which unblocks dependents), whether or not a commit was
requested. Real git repository, real worktree, real `run_orchestrator_cycle`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# `automation/` is a repo-root package outside `src`; same convention as test_orchestrator.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_orchestrator import init_repo, make_provider_response, run_git

from automation.orchestrator import run_orchestrator_cycle
from automation.providers.fake import FakeCodingProvider
from automation.schemas.command import CommandPolicy
from automation.schemas.orchestrator import OrchestratorInputs, OrchestratorOutcome
from automation.schemas.provider import ProviderResponse
from automation.schemas.task import RiskLevel, TaskBacklog, TaskStatus
from automation.schemas.validation_run import ValidationExecutionPolicy

_TASK_YAML = (
    "version: qualitymesh.dev/automation/v1\n"
    "tasks:\n"
    "  - id: gate-task\n"
    "    title: Gate task\n"
    "    status: ready\n"
    "    priority: 1\n"
    "    risk: low\n"
    "    objective: Do something.\n"
    "    allowed_paths: [README.md]\n"
    "    acceptance_criteria: [It works]\n"
    "    validation_commands: [git status]\n"
    "    autonomy:\n"
    "      auto_modify: true\n"
    "      auto_test: true\n"
    "      auto_commit: {auto_commit}\n"
    "  - id: dependent-task\n"
    "    title: Dependent\n"
    "    status: ready\n"
    "    priority: 2\n"
    "    risk: low\n"
    "    objective: Needs gate-task.\n"
    "    allowed_paths: [README.md]\n"
    "    depends_on: [gate-task]\n"
    "    acceptance_criteria: [It works]\n"
    "    validation_commands: [git status]\n"
)


def _setup(tmp_path: Path, *, auto_commit: bool, allow_git_writes: bool) -> tuple[Any, Path, Path]:
    repo_path = tmp_path / "repo"
    commit = init_repo(repo_path)
    worktree_parent = tmp_path / "worktrees"
    worktree_parent.mkdir()
    backlog_path = tmp_path / "backlog.yaml"
    backlog_path.write_text(
        _TASK_YAML.format(auto_commit=str(auto_commit).lower()), encoding="utf-8"
    )
    inputs = OrchestratorInputs(
        backlog_path=str(backlog_path),
        repository_path=str(repo_path),
        base_commit=commit,
        worktree_parent=str(worktree_parent),
        cache_manifest_path=str(tmp_path / "cache.json"),
        evidence_path=str(tmp_path / "evidence.json"),
        task_id="gate-task",
        allowed_risk_levels=(RiskLevel.LOW,),
        command_policy=CommandPolicy(
            worktree_root=str(worktree_parent / "gate-task"), allowed_executables=("git",)
        ),
        execution_policy=ValidationExecutionPolicy(),
        token_budget=100_000,
        allow_git_writes=allow_git_writes,
    )
    return inputs, backlog_path, worktree_parent


def _status(backlog_path: Path, task_id: str) -> TaskStatus:
    backlog = TaskBacklog.model_validate(yaml.safe_load(backlog_path.read_text(encoding="utf-8")))
    return next(task.status for task in backlog.tasks if task.id == task_id)


class _Editing(FakeCodingProvider):
    def __init__(self, worktree: Path, edits: dict[str, str], response: ProviderResponse) -> None:
        super().__init__(response=response)
        self._worktree = worktree
        self._edits = edits

    def execute(self, request: Any) -> ProviderResponse:
        for name, text in self._edits.items():
            (self._worktree / name).write_text(text, encoding="utf-8")
        return super().execute(request)


@pytest.mark.parametrize(
    ("auto_commit", "allow_git_writes"), [(True, False), (False, True), (False, False)]
)
def test_out_of_scope_edit_stays_blocked_when_no_commit_is_attempted(
    tmp_path: Path, auto_commit: bool, allow_git_writes: bool
) -> None:
    """Entry point `run_orchestrator_cycle`. The provider edits `evil.py`, outside
    the task's allowed_paths. No commit is attempted (dry run / auto_commit off).
    Guard, not a defect: the repair loop's path policy already blocks this."""
    inputs, backlog_path, worktree_parent = _setup(
        tmp_path, auto_commit=auto_commit, allow_git_writes=allow_git_writes
    )
    provider = _Editing(
        worktree_parent / "gate-task",
        {"evil.py": "print('x')\n"},
        make_provider_response(("evil.py",)),
    )

    result = run_orchestrator_cycle(inputs, provider)

    assert result.outcome is OrchestratorOutcome.POLICY_VIOLATION, result.reason
    assert result.final_status is TaskStatus.BLOCKED
    assert _status(backlog_path, "gate-task") is TaskStatus.BLOCKED
    assert _status(backlog_path, "dependent-task") is TaskStatus.READY
    assert "outside allowed_paths" in result.reason


@pytest.mark.parametrize("allow_git_writes", [False, True])
def test_no_change_cycle_does_not_complete_without_commit(
    tmp_path: Path, allow_git_writes: bool
) -> None:
    inputs, backlog_path, _ = _setup(tmp_path, auto_commit=False, allow_git_writes=allow_git_writes)

    result = run_orchestrator_cycle(inputs, FakeCodingProvider(response=make_provider_response()))

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED, result.reason
    assert _status(backlog_path, "gate-task") is TaskStatus.BLOCKED


def test_unreported_edit_blocks_task_when_no_commit_is_attempted(tmp_path: Path) -> None:
    inputs, backlog_path, worktree_parent = _setup(
        tmp_path, auto_commit=True, allow_git_writes=False
    )
    provider = _Editing(
        worktree_parent / "gate-task",
        {"README.md": "hello\nedited\n", "notes.txt": "unreported\n"},
        make_provider_response(("README.md",)),
    )

    result = run_orchestrator_cycle(inputs, provider)

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED, result.reason
    assert "notes.txt" in result.reason
    assert _status(backlog_path, "gate-task") is TaskStatus.BLOCKED


def test_in_scope_dry_run_still_completes_without_scan_attestation(tmp_path: Path) -> None:
    """No commit is attempted, so the missing scan attestation alone must not
    block an otherwise valid, in-scope task (nothing leaves the worktree)."""
    inputs, backlog_path, worktree_parent = _setup(
        tmp_path, auto_commit=True, allow_git_writes=False
    )
    provider = _Editing(
        worktree_parent / "gate-task",
        {"README.md": "hello\nedited\n"},
        make_provider_response(("README.md",)),
    )

    result = run_orchestrator_cycle(inputs, provider)

    assert result.outcome is OrchestratorOutcome.COMPLETED, result.reason
    assert _status(backlog_path, "gate-task") is TaskStatus.COMPLETED
    assert run_git(["rev-list", "--count", "agent/gate-task"], Path(inputs.repository_path)) == "1"

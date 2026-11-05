"""Regression tests: a cycle whose commit preconditions fail must never mark
the task COMPLETED (which unblocks dependents), whether or not a commit was
requested. Real git repository, real worktree, real `run_orchestrator_cycle`."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# `automation/` is a repo-root package outside `src`; same convention as test_orchestrator.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_orchestrator import (
    SCAN_MARKER,
    init_repo,
    make_provider_response,
    real_scanner,
    run_git,
)

import automation.orchestrator as orchestrator_module
from automation.backlog_store import BacklogStoreError
from automation.evidence_recorder import load_evidence
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


def _setup(
    tmp_path: Path,
    *,
    auto_commit: bool,
    allow_git_writes: bool,
    scan_command: tuple[str, ...] | None = None,
    scan_exes: tuple[str, ...] = (),
) -> tuple[Any, Path, Path]:
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
            worktree_root=str(worktree_parent / "gate-task"),
            allowed_executables=("git", *scan_exes),
        ),
        execution_policy=ValidationExecutionPolicy(),
        token_budget=100_000,
        allow_git_writes=allow_git_writes,
        secret_scan_command=scan_command,
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


# --------------------------------------------------------------------------------------
# Secret-scan evidence: the harness runs the scanner itself; nothing is self-attested
# --------------------------------------------------------------------------------------

_MUTATING_SCANNER = (
    "import pathlib\npathlib.Path('README.md').write_text('changed during scan\\n')\n"
)


def _commit_count(inputs: Any) -> str:
    return run_git(["rev-list", "--count", "agent/gate-task"], Path(inputs.repository_path))


def _run_with_content(tmp_path: Path, content: str, **setup: Any) -> tuple[Any, Any, Path]:
    inputs, backlog_path, worktree_parent = _setup(
        tmp_path, auto_commit=True, allow_git_writes=True, **setup
    )
    provider = _Editing(
        worktree_parent / "gate-task",
        {"README.md": content},
        make_provider_response(("README.md",)),
    )
    return inputs, run_orchestrator_cycle(inputs, provider), backlog_path


def test_caller_cannot_self_attest_a_clean_scan() -> None:
    """The old `secret_scan_clean=True` flag let a commit through with no scanner at all."""
    with pytest.raises(ValueError, match="secret_scan_clean"):
        OrchestratorInputs.model_validate(
            {
                "backlog_path": "b",
                "repository_path": "r",
                "base_commit": "c",
                "worktree_parent": "w",
                "cache_manifest_path": "m",
                "evidence_path": "e",
                "allowed_risk_levels": ("low",),
                "command_policy": CommandPolicy(worktree_root="w"),
                "execution_policy": ValidationExecutionPolicy(),
                "token_budget": 1,
                "secret_scan_clean": True,
            }
        )


def test_commit_without_a_configured_scanner_is_blocked(tmp_path: Path) -> None:
    inputs, result, backlog_path = _run_with_content(tmp_path, f"x\n{SCAN_MARKER}\n")

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED, result.reason
    assert "secret-scan-blocked" in result.reason
    assert _status(backlog_path, "gate-task") is TaskStatus.BLOCKED
    assert _commit_count(inputs) == "1"


def test_scanner_finding_blocks_commit_and_completion(tmp_path: Path) -> None:
    command, exes = real_scanner(tmp_path)
    inputs, result, backlog_path = _run_with_content(
        tmp_path, f"x\n{SCAN_MARKER}\n", scan_command=command, scan_exes=exes
    )

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED, result.reason
    assert result.evidence_result is not None
    assert result.evidence_result.secret_scan is not None
    assert result.evidence_result.secret_scan.exit_code == 1
    assert result.evidence_result.secret_scan.passed is False
    assert _status(backlog_path, "gate-task") is TaskStatus.BLOCKED
    assert _commit_count(inputs) == "1"


def test_scanner_that_cannot_run_fails_closed(tmp_path: Path) -> None:
    inputs, result, backlog_path = _run_with_content(
        tmp_path,
        "clean\n",
        scan_command=("no-such-scanner-binary",),
        scan_exes=("no-such-scanner-binary",),
    )

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED, result.reason
    assert result.evidence_result is not None
    assert result.evidence_result.secret_scan is not None
    assert result.evidence_result.secret_scan.exit_code is None
    assert _status(backlog_path, "gate-task") is TaskStatus.BLOCKED
    assert _commit_count(inputs) == "1"


def test_scanner_outside_the_executable_allowlist_fails_closed(tmp_path: Path) -> None:
    command, _ = real_scanner(tmp_path)
    inputs, result, _ = _run_with_content(tmp_path, "clean\n", scan_command=command)

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED, result.reason
    assert _commit_count(inputs) == "1"


def test_files_changed_during_the_scan_are_not_committed(tmp_path: Path) -> None:
    script = tmp_path / "mutate.py"
    script.write_text(_MUTATING_SCANNER, encoding="utf-8")
    inputs, result, _ = _run_with_content(
        tmp_path, "clean\n", scan_command=("python3", str(script)), scan_exes=("python3",)
    )

    assert result.outcome is OrchestratorOutcome.PRECONDITIONS_FAILED, result.reason
    assert result.evidence_result is not None
    assert result.evidence_result.secret_scan is not None
    assert result.evidence_result.secret_scan.exit_code == 0
    assert result.evidence_result.secret_scan.passed is False
    assert _commit_count(inputs) == "1"


def test_clean_scan_binds_evidence_to_the_exact_committed_content(tmp_path: Path) -> None:
    command, exes = real_scanner(tmp_path)
    inputs, result, backlog_path = _run_with_content(
        tmp_path, "clean\n", scan_command=command, scan_exes=exes
    )

    assert result.outcome is OrchestratorOutcome.COMPLETED, result.reason
    assert _status(backlog_path, "gate-task") is TaskStatus.COMPLETED
    assert _commit_count(inputs) == "2"
    scan = result.evidence_result.secret_scan  # type: ignore[union-attr]  # asserted below
    assert scan is not None
    assert scan.passed is True
    assert scan.exit_code == 0
    record = result.evidence_result.changed_files[0]  # type: ignore[union-attr]
    expected = hashlib.sha256(f"{record.path}\0{record.sha256}\n".encode()).hexdigest()
    assert scan.tree_sha256 == expected
    reloaded = load_evidence(Path(inputs.evidence_path))
    assert reloaded.secret_scan == scan
    committed = run_git(["show", "agent/gate-task:README.md"], Path(inputs.repository_path))
    assert hashlib.sha256((committed + "\n").encode()).hexdigest() == record.sha256


def test_scan_is_not_run_when_no_commit_is_requested(tmp_path: Path) -> None:
    command, exes = real_scanner(tmp_path)
    inputs, _, worktree_parent = _setup(
        tmp_path, auto_commit=True, allow_git_writes=False, scan_command=command, scan_exes=exes
    )
    provider = _Editing(
        worktree_parent / "gate-task",
        {"README.md": "clean\n"},
        make_provider_response(("README.md",)),
    )

    result = run_orchestrator_cycle(inputs, provider)

    assert result.outcome is OrchestratorOutcome.COMPLETED, result.reason
    assert result.evidence_result is not None
    assert result.evidence_result.secret_scan is None


def test_failed_terminal_transition_is_never_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If persisting COMPLETED fails, the caller gets an exception, not a result,
    the dependent stays unrunnable, and a retry is refused while the task is ACTIVE."""
    command, exes = real_scanner(tmp_path)
    inputs, backlog_path, worktree_parent = _setup(
        tmp_path, auto_commit=True, allow_git_writes=True, scan_command=command, scan_exes=exes
    )
    real_apply = orchestrator_module.apply_status_transition

    def failing_apply(path: Any, backlog: Any, task_id: str, status: TaskStatus) -> Any:
        if status is TaskStatus.COMPLETED:
            raise BacklogStoreError("simulated persistence failure")
        return real_apply(path, backlog, task_id, status)

    monkeypatch.setattr(orchestrator_module, "apply_status_transition", failing_apply)
    provider = _Editing(
        worktree_parent / "gate-task",
        {"README.md": "clean\n"},
        make_provider_response(("README.md",)),
    )

    with pytest.raises(BacklogStoreError, match="simulated persistence failure"):
        run_orchestrator_cycle(inputs, provider)

    assert _status(backlog_path, "gate-task") is TaskStatus.ACTIVE
    assert _status(backlog_path, "dependent-task") is TaskStatus.READY
    monkeypatch.setattr(orchestrator_module, "apply_status_transition", real_apply)
    retry = run_orchestrator_cycle(inputs, FakeCodingProvider(response=make_provider_response()))
    assert retry.outcome is OrchestratorOutcome.ACTIVE_TASK_EXISTS

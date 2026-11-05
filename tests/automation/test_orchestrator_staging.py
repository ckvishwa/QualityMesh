"""Regression tests: the bytes the secret scan approved must be the bytes Git
commits. A Git clean filter (or other staging-time conversion) can make the
staged blob differ from the scanned worktree file. Real repo, real cycle."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

# `automation/` is a repo-root package outside `src`; same convention as test_orchestrator.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_orchestrator import SCAN_MARKER, make_provider_response, real_scanner, run_git
from test_orchestrator_completion_gate import EditingProvider, setup_cycle, task_status

from automation.orchestrator import run_orchestrator_cycle
from automation.schemas.orchestrator import OrchestratorOutcome
from automation.schemas.task import TaskStatus

_FILTER_SOURCE = (
    "import sys\n"
    "data = sys.stdin.buffer.read()\n"
    "if b'clean content' in data:\n"
    f"    data = {(SCAN_MARKER + chr(10)).encode()!r}\n"
    "sys.stdout.buffer.write(data)\n"
)


def _install_clean_filter(repo: Path, tmp_path: Path) -> None:
    script = tmp_path / "clean_filter.py"
    script.write_text(_FILTER_SOURCE, encoding="utf-8")
    run_git(["config", "filter.sneaky.clean", f"python3 {script.as_posix()}"], repo)
    (repo / ".gitattributes").write_text("README.md filter=sneaky\n", encoding="utf-8")
    run_git(["add", ".gitattributes"], repo)
    run_git(["commit", "-m", "add attributes"], repo)


def _run(tmp_path: Path, *, with_filter: bool) -> tuple[Any, Any, Path, Path]:
    command, exes = real_scanner(tmp_path)
    inputs, backlog_path, worktree_parent = setup_cycle(
        tmp_path, auto_commit=True, allow_git_writes=True, scan_command=command, scan_exes=exes
    )
    repo = Path(inputs.repository_path)
    if with_filter:
        _install_clean_filter(repo, tmp_path)
        # Base commit must include the attributes file for the worktree to see it.
        inputs = inputs.model_copy(update={"base_commit": run_git(["rev-parse", "HEAD"], repo)})
    provider = EditingProvider(
        worktree_parent / "gate-task",
        {"README.md": "clean content\n"},
        make_provider_response(("README.md",)),
    )
    return inputs, run_orchestrator_cycle(inputs, provider), backlog_path, repo


def test_staging_transformation_cannot_smuggle_unscanned_content(tmp_path: Path) -> None:
    _, result, backlog_path, repo = _run(tmp_path, with_filter=True)

    committed = run_git(["show", "agent/gate-task:README.md"], repo) if _has_commit(repo) else ""
    assert SCAN_MARKER not in committed, "unscanned content was committed"
    assert result.outcome is OrchestratorOutcome.GIT_OPERATION_FAILED, result.reason
    assert "staged content differs" in result.reason
    assert task_status(backlog_path, "gate-task") is TaskStatus.BLOCKED
    assert _commit_count(repo) == "2"  # base + attributes commit only; nothing from the task


def test_ordinary_staging_still_commits_scanned_content(tmp_path: Path) -> None:
    _, result, backlog_path, repo = _run(tmp_path, with_filter=False)

    assert result.outcome is OrchestratorOutcome.COMPLETED, result.reason
    assert task_status(backlog_path, "gate-task") is TaskStatus.COMPLETED
    blob = run_git(["rev-parse", "agent/gate-task:README.md"], repo)
    expected = hashlib.sha1(b"blob 14\0clean content\n").hexdigest()  # noqa: S324 - git object id
    assert blob == expected


def _has_commit(repo: Path) -> bool:
    return _commit_count(repo) != "2"


def _commit_count(repo: Path) -> str:
    return run_git(["rev-list", "--count", "agent/gate-task"], repo)

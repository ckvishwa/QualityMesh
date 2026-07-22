"""Unit tests for isolated Git worktree management (QM-AUTO-005).

Uses real, local, throwaway Git repositories under `tmp_path` — no network access,
no dependency on the actual QualityMesh repository.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import automation.worktree_manager as worktree_manager
from automation.schemas.worktree import WorktreeCleanupStatus, WorktreeErrorCode, WorktreeRequest
from automation.worktree_manager import WorktreeError, check_cleanup_eligibility, create_worktree

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def run_git(args: list[str], cwd: Path) -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git must be on PATH to run these tests")
    result = subprocess.run(  # noqa: S603 - fixed test-authored args, resolved executable, no shell text
        [git_executable, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "--initial-branch=main"], path)
    run_git(["config", "user.email", "test@example.com"], path)
    run_git(["config", "user.name", "Test User"], path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    run_git(["add", "README.md"], path)
    run_git(["commit", "-m", "initial commit"], path)
    return run_git(["rev-parse", "HEAD"], path)


def commit_file(repo_path: Path, name: str, content: str) -> str:
    (repo_path / name).write_text(content, encoding="utf-8")
    run_git(["add", name], repo_path)
    run_git(["commit", "-m", f"add {name}"], repo_path)
    return run_git(["rev-parse", "HEAD"], repo_path)


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo_path = tmp_path / "repo"
    commit = init_repo(repo_path)
    return repo_path, commit


def make_worktree_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "worktrees"
    parent.mkdir()
    return parent


def build_request(**overrides: Any) -> WorktreeRequest:
    fields: dict[str, Any] = {
        "task_id": "task-1",
        "repository_path": "unused",
        "base_commit": "0" * 40,
        "worktree_parent": "unused",
    }
    fields.update(overrides)
    return WorktreeRequest.model_validate(fields)


# --------------------------------------------------------------------------------------
# Successful creation
# --------------------------------------------------------------------------------------


def test_clean_repository_succeeds(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )

    metadata = create_worktree(request)

    assert Path(metadata.worktree_path).is_dir()
    assert metadata.branch_name == "agent/task-1"
    assert metadata.base_commit == commit


def test_metadata_correctness(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        task_id="QM-AUTO-005",
        repository_path=str(repo_path),
        base_commit=commit,
        worktree_parent=str(parent),
    )

    metadata = create_worktree(request)

    assert metadata.task_id == "QM-AUTO-005"
    assert Path(metadata.repository_path) == repo_path
    assert Path(metadata.worktree_path) == (parent / "QM-AUTO-005").resolve()
    assert metadata.branch_name == "agent/QM-AUTO-005"
    assert metadata.base_commit == commit


def test_exact_base_commit_is_used(tmp_path: Path) -> None:
    repo_path, first_commit = make_repo(tmp_path)
    commit_file(repo_path, "second.txt", "more content\n")
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=first_commit, worktree_parent=str(parent)
    )

    metadata = create_worktree(request)

    assert metadata.base_commit == first_commit
    assert not (Path(metadata.worktree_path) / "second.txt").exists()


# --------------------------------------------------------------------------------------
# Precondition rejections
# --------------------------------------------------------------------------------------


def test_dirty_repository_is_rejected(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    (repo_path / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.DIRTY_BASE_REPOSITORY


def test_invalid_repository_path_is_rejected(tmp_path: Path) -> None:
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(tmp_path / "no-such-repo"),
        base_commit="0" * 40,
        worktree_parent=str(parent),
    )

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.REPOSITORY_NOT_FOUND


def test_invalid_base_commit_is_rejected(tmp_path: Path) -> None:
    repo_path, _commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit="f" * 40, worktree_parent=str(parent)
    )

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.BASE_COMMIT_NOT_FOUND


def test_invalid_worktree_parent_is_rejected(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    request = build_request(
        repository_path=str(repo_path),
        base_commit=commit,
        worktree_parent=str(tmp_path / "no-such-parent"),
    )

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.WORKTREE_PARENT_NOT_FOUND


# --------------------------------------------------------------------------------------
# Naming and task-ID safety
# --------------------------------------------------------------------------------------


def test_safe_deterministic_naming() -> None:
    request = build_request(task_id="QM-AUTO-005")

    assert request.branch_name == "agent/QM-AUTO-005"
    assert request.worktree_dirname == "QM-AUTO-005"


@pytest.mark.parametrize(
    "bad_task_id",
    ["", "task id", ".hidden", "a" * 200],
    ids=["empty", "internal-space", "leading-dot", "too-long"],
)
def test_unsafe_task_ids_are_rejected(bad_task_id: str) -> None:
    with pytest.raises(ValidationError):
        build_request(task_id=bad_task_id)


@pytest.mark.parametrize(
    "bad_task_id",
    ["../escape", "a/b", "/abs/path", "C:\\Windows"],
    ids=["dot-dot", "internal-slash", "posix-absolute", "windows-drive"],
)
def test_traversal_and_absolute_task_ids_are_rejected(bad_task_id: str) -> None:
    with pytest.raises(ValidationError):
        build_request(task_id=bad_task_id)


def test_worktree_escapes_parent_is_rejected(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    # `TaskIdStr`'s own pattern already rejects "..", so this bypasses schema
    # validation via `model_construct` to exercise the manager's own independent
    # containment check — same technique QM-AUTO-002 used to test a self-cycle
    # that `AutonomousTask`'s validator also already forbids.
    escaping_request = WorktreeRequest.model_construct(
        task_id="../escape",
        repository_path=str(repo_path),
        base_commit=commit,
        worktree_parent=str(parent),
    )

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(escaping_request)
    assert exc_info.value.code is WorktreeErrorCode.WORKTREE_ESCAPES_PARENT


# --------------------------------------------------------------------------------------
# Collisions
# --------------------------------------------------------------------------------------


def test_branch_collision_is_rejected(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    run_git(["branch", "agent/task-1"], repo_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.BRANCH_COLLISION


def test_directory_collision_is_rejected(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    (parent / "task-1").mkdir()
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.DIRECTORY_COLLISION


def test_registered_worktree_collision_is_rejected(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    target = parent / "task-1"
    # Detached worktree (no `-b`), so `agent/task-1` is never created as a branch;
    # then the directory is removed without `git worktree remove`, leaving Git's
    # own metadata still tracking it as registered — reachable independently of
    # both BRANCH_COLLISION and DIRECTORY_COLLISION.
    run_git(["worktree", "add", str(target), commit], repo_path)
    shutil.rmtree(target)

    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )
    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.WORKTREE_ALREADY_REGISTERED


# --------------------------------------------------------------------------------------
# Cleanup eligibility
# --------------------------------------------------------------------------------------


def test_clean_worktree_is_eligible_for_cleanup(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )
    metadata = create_worktree(request)

    eligibility = check_cleanup_eligibility(Path(metadata.worktree_path))

    assert eligibility.eligible is True
    assert eligibility.status is WorktreeCleanupStatus.ELIGIBLE


def test_dirty_worktree_is_not_eligible_for_cleanup(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )
    metadata = create_worktree(request)
    (Path(metadata.worktree_path) / "scratch.txt").write_text("wip\n", encoding="utf-8")

    eligibility = check_cleanup_eligibility(Path(metadata.worktree_path))

    assert eligibility.eligible is False
    assert eligibility.status is WorktreeCleanupStatus.NOT_ELIGIBLE_DIRTY


def test_missing_worktree_is_not_eligible_for_cleanup(tmp_path: Path) -> None:
    eligibility = check_cleanup_eligibility(tmp_path / "never-existed")

    assert eligibility.eligible is False
    assert eligibility.status is WorktreeCleanupStatus.NOT_ELIGIBLE_NOT_FOUND


def test_cleanup_check_never_deletes_anything(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )
    metadata = create_worktree(request)
    worktree_path = Path(metadata.worktree_path)

    check_cleanup_eligibility(worktree_path)
    (worktree_path / "scratch.txt").write_text("wip\n", encoding="utf-8")
    check_cleanup_eligibility(worktree_path)

    assert worktree_path.is_dir()
    assert run_git(["branch", "--list", "agent/task-1"], repo_path) != ""


def test_no_removal_capability_exists() -> None:
    for forbidden_name in (
        "remove_worktree",
        "delete_worktree",
        "force_remove_worktree",
        "delete_branch",
    ):
        assert not hasattr(worktree_manager, forbidden_name)


# --------------------------------------------------------------------------------------
# Subprocess failure handling
# --------------------------------------------------------------------------------------


def test_subprocess_failure_handling_missing_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )

    def fake_which(cmd: str, mode: int = 0, path: str | None = None) -> str | None:
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    with pytest.raises(WorktreeError) as exc_info:
        create_worktree(request)
    assert exc_info.value.code is WorktreeErrorCode.GIT_COMMAND_FAILED


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_input_request_is_not_mutated(tmp_path: Path) -> None:
    repo_path, commit = make_repo(tmp_path)
    parent = make_worktree_parent(tmp_path)
    request = build_request(
        repository_path=str(repo_path), base_commit=commit, worktree_parent=str(parent)
    )
    before = copy.deepcopy(request)

    create_worktree(request)

    assert request == before

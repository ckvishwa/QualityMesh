"""Unit tests for deterministic, cache-backed context planning (QM-AUTO-004)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.cache_manager import build_entry, update_entry
from automation.context_builder import ContextBuildError, build_context
from automation.schemas.cache import CACHE_MANIFEST_VERSION, CacheManifest
from automation.schemas.context import ContextFileStatus
from automation.schemas.task import AutonomousTask

EMPTY_MANIFEST = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=())

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
        "context_files": [],
        "allowed_paths": ["automation/"],
        "acceptance_criteria": ["It works"],
        "validation_commands": ["uv run pytest tests/automation -q"],
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def write_repo_file(repo_root: Path, relative_path: str, content: bytes) -> Path:
    file_path = repo_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    return file_path


# --------------------------------------------------------------------------------------
# Basic shape
# --------------------------------------------------------------------------------------


def test_empty_context_files_returns_empty_result(tmp_path: Path) -> None:
    task = build_task(context_files=[])

    result = build_context(task, tmp_path, EMPTY_MANIFEST)

    assert result.records == ()
    assert result.hit_count == result.miss_count == result.stale_count == 0
    assert result.manifest == EMPTY_MANIFEST


def test_one_valid_file_is_included(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    task = build_task(context_files=["a.txt"])

    result = build_context(task, tmp_path, EMPTY_MANIFEST)

    assert len(result.records) == 1
    assert result.records[0].path == "a.txt"
    assert result.records[0].status is ContextFileStatus.MISS


def test_multiple_files_have_deterministic_ordering(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "z.txt", b"z")
    write_repo_file(tmp_path, "a.txt", b"a")
    write_repo_file(tmp_path, "m.txt", b"m")
    task = build_task(context_files=["z.txt", "a.txt", "m.txt"])

    result = build_context(task, tmp_path, EMPTY_MANIFEST)

    assert [record.path for record in result.records] == ["a.txt", "m.txt", "z.txt"]


def test_result_independent_of_declaration_order(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "z.txt", b"z")
    write_repo_file(tmp_path, "a.txt", b"a")
    forward = build_task(context_files=["z.txt", "a.txt"])
    reversed_order = build_task(context_files=["a.txt", "z.txt"])

    forward_result = build_context(forward, tmp_path, EMPTY_MANIFEST)
    reversed_result = build_context(reversed_order, tmp_path, EMPTY_MANIFEST)

    assert forward_result.records == reversed_result.records


# --------------------------------------------------------------------------------------
# Rejections
# --------------------------------------------------------------------------------------


def test_duplicate_normalized_paths_are_rejected(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"a")
    task = build_task(context_files=["a.txt", "./a.txt"])

    with pytest.raises(ContextBuildError, match="duplicate context file"):
        build_context(task, tmp_path, EMPTY_MANIFEST)


def test_traversal_path_is_rejected(tmp_path: Path) -> None:
    task = build_task(context_files=["../secret.txt"])

    with pytest.raises(ContextBuildError):
        build_context(task, tmp_path, EMPTY_MANIFEST)


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    task = build_task(context_files=["/etc/passwd"])

    with pytest.raises(ContextBuildError):
        build_context(task, tmp_path, EMPTY_MANIFEST)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    task = build_task(context_files=["missing.txt"])

    with pytest.raises(ContextBuildError, match="does not exist"):
        build_context(task, tmp_path, EMPTY_MANIFEST)


def test_directory_path_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a-directory").mkdir()
    task = build_task(context_files=["a-directory"])

    with pytest.raises(ContextBuildError, match="is a directory"):
        build_context(task, tmp_path, EMPTY_MANIFEST)


def test_outside_repository_path_is_rejected(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside_file = tmp_path / "outside" / "secret.txt"
    outside_file.parent.mkdir()
    outside_file.write_bytes(b"secret")

    link_path = repo_root / "escape.txt"
    try:
        link_path.symlink_to(outside_file)
    except OSError as exc:
        pytest.skip(f"symlinks not supported in this environment: {exc}")

    task = build_task(context_files=["escape.txt"])
    with pytest.raises(ContextBuildError, match="resolves outside"):
        build_context(task, repo_root, EMPTY_MANIFEST)


# --------------------------------------------------------------------------------------
# Cache status behavior
# --------------------------------------------------------------------------------------


def test_cache_hit_reuses_cached_entry_without_full_source(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt", summary="a summary", indexed_commit="deadbeef")
    manifest = update_entry(EMPTY_MANIFEST, entry)
    task = build_task(context_files=["a.txt"])

    result = build_context(task, tmp_path, manifest)

    record = result.records[0]
    assert record.status is ContextFileStatus.HIT
    assert record.full_source_required is False
    assert record.entry == entry
    assert record.entry.summary == "a summary"
    assert result.hit_count == 1
    assert result.miss_count == 0
    assert result.stale_count == 0


def test_cache_miss_rebuilds_entry_and_requires_full_source(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    task = build_task(context_files=["a.txt"])

    result = build_context(task, tmp_path, EMPTY_MANIFEST)

    record = result.records[0]
    assert record.status is ContextFileStatus.MISS
    assert record.full_source_required is True
    assert result.miss_count == 1
    assert result.manifest.entries == (record.entry,)


def test_stale_cached_file_content_changed_requires_full_source(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    old_entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, old_entry)

    write_repo_file(tmp_path, "a.txt", b"goodbye")
    task = build_task(context_files=["a.txt"])

    result = build_context(task, tmp_path, manifest)

    record = result.records[0]
    assert record.status is ContextFileStatus.STALE
    assert record.full_source_required is True
    assert result.stale_count == 1


def test_changed_file_refreshes_cache_entry(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    old_entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, old_entry)

    write_repo_file(tmp_path, "a.txt", b"goodbye")
    task = build_task(context_files=["a.txt"])

    result = build_context(task, tmp_path, manifest)

    record = result.records[0]
    assert record.entry.content_hash != old_entry.content_hash
    assert result.manifest.entries == (record.entry,)


def test_unchanged_file_preserves_cached_entry(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt", summary="kept")
    manifest = update_entry(EMPTY_MANIFEST, entry)
    task = build_task(context_files=["a.txt"])

    result = build_context(task, tmp_path, manifest)

    assert result.records[0].entry == entry
    assert result.manifest == manifest


def test_force_full_source_overrides_hit(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)
    task = build_task(context_files=["a.txt"])

    result = build_context(task, tmp_path, manifest, force_full_source=True)

    assert result.records[0].status is ContextFileStatus.HIT
    assert result.records[0].full_source_required is True


def test_mixed_hit_miss_stale_counts(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "hit.txt", b"unchanged")
    hit_entry = build_entry(tmp_path, "hit.txt")

    write_repo_file(tmp_path, "stale.txt", b"old")
    stale_entry = build_entry(tmp_path, "stale.txt")

    write_repo_file(tmp_path, "miss.txt", b"brand new")

    manifest = update_entry(update_entry(EMPTY_MANIFEST, hit_entry), stale_entry)
    write_repo_file(tmp_path, "stale.txt", b"new content")  # changes stale.txt post-index

    task = build_task(context_files=["hit.txt", "stale.txt", "miss.txt"])
    result = build_context(task, tmp_path, manifest)

    assert result.hit_count == 1
    assert result.stale_count == 1
    assert result.miss_count == 1
    statuses = {record.path: record.status for record in result.records}
    assert statuses["hit.txt"] is ContextFileStatus.HIT
    assert statuses["stale.txt"] is ContextFileStatus.STALE
    assert statuses["miss.txt"] is ContextFileStatus.MISS


# --------------------------------------------------------------------------------------
# Binary and empty files
# --------------------------------------------------------------------------------------


def test_binary_file_context(tmp_path: Path) -> None:
    payload = bytes(range(256))
    write_repo_file(tmp_path, "blob.bin", payload)
    task = build_task(context_files=["blob.bin"])

    result = build_context(task, tmp_path, EMPTY_MANIFEST)

    assert result.records[0].entry.size_bytes == len(payload)


def test_empty_file_context(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "empty.txt", b"")
    task = build_task(context_files=["empty.txt"])

    result = build_context(task, tmp_path, EMPTY_MANIFEST)

    assert result.records[0].entry.size_bytes == 0
    assert result.records[0].status is ContextFileStatus.MISS


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_build_context_does_not_mutate_task_or_manifest(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)
    task = build_task(context_files=["a.txt"])
    task_before = copy.deepcopy(task)
    manifest_before = copy.deepcopy(manifest)

    write_repo_file(tmp_path, "a.txt", b"changed")
    build_context(task, tmp_path, manifest)

    assert task == task_before
    assert manifest == manifest_before

"""Unit tests for the content-addressed repository context cache (QM-AUTO-003)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.cache_manager import (
    CacheError,
    build_entry,
    invalidate_path,
    load_manifest,
    lookup,
    serialize_manifest,
    update_entry,
    write_manifest_atomically,
)
from automation.schemas.cache import (
    CACHE_MANIFEST_VERSION,
    CacheEntry,
    CacheManifest,
    CacheStatus,
    normalize_cache_path,
)

EMPTY_MANIFEST = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=())

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def write_repo_file(repo_root: Path, relative_path: str, content: bytes) -> Path:
    file_path = repo_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    return file_path


# --------------------------------------------------------------------------------------
# Core hit/miss/stale behavior
# --------------------------------------------------------------------------------------


def test_new_file_is_miss(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")

    result = lookup(tmp_path, EMPTY_MANIFEST, "a.txt")

    assert result.status is CacheStatus.MISS
    assert result.cached_entry is None
    assert result.current_entry is not None


def test_unchanged_file_is_hit(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)

    result = lookup(tmp_path, manifest, "a.txt")

    assert result.status is CacheStatus.HIT
    assert result.cached_entry == entry
    assert result.current_entry == entry


def test_changed_content_is_miss(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)

    write_repo_file(tmp_path, "a.txt", b"goodbye")
    result = lookup(tmp_path, manifest, "a.txt")

    assert result.status is CacheStatus.MISS
    assert result.cached_entry == entry
    assert result.current_entry is not None
    assert result.current_entry.content_hash != entry.content_hash


def test_changed_mtime_same_content_is_hit(tmp_path: Path) -> None:
    file_path = write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)

    new_mtime = time.time() + 10_000
    os.utime(file_path, (new_mtime, new_mtime))

    result = lookup(tmp_path, manifest, "a.txt")

    assert result.status is CacheStatus.HIT


def test_unchanged_mtime_changed_content_is_miss(tmp_path: Path) -> None:
    file_path = write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)
    original_mtime = file_path.stat().st_mtime

    file_path.write_bytes(b"goodbye")
    os.utime(file_path, (original_mtime, original_mtime))

    result = lookup(tmp_path, manifest, "a.txt")

    assert result.status is CacheStatus.MISS


def test_deleted_file_is_stale(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)

    (tmp_path / "a.txt").unlink()
    result = lookup(tmp_path, manifest, "a.txt")

    assert result.status is CacheStatus.STALE
    assert result.cached_entry == entry
    assert result.current_entry is None


def test_missing_file_with_no_cache_entry_is_miss(tmp_path: Path) -> None:
    result = lookup(tmp_path, EMPTY_MANIFEST, "never-existed.txt")

    assert result.status is CacheStatus.MISS
    assert result.cached_entry is None
    assert result.current_entry is None


def test_binary_file_is_hashed_and_cached(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 4
    write_repo_file(tmp_path, "blob.bin", payload)

    entry = build_entry(tmp_path, "blob.bin")
    manifest = update_entry(EMPTY_MANIFEST, entry)
    result = lookup(tmp_path, manifest, "blob.bin")

    assert entry.size_bytes == len(payload)
    assert result.status is CacheStatus.HIT


def test_empty_file_is_supported() -> None:
    assert (
        CacheEntry(
            path="empty.txt",
            content_hash=hashlib.sha256(b"").hexdigest(),
            size_bytes=0,
        ).size_bytes
        == 0
    )


def test_empty_file_round_trips_as_hit(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "empty.txt", b"")
    entry = build_entry(tmp_path, "empty.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)

    result = lookup(tmp_path, manifest, "empty.txt")

    assert entry.size_bytes == 0
    assert result.status is CacheStatus.HIT


def test_build_entry_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CacheError, match="no file exists"):
        build_entry(tmp_path, "missing.txt")


# --------------------------------------------------------------------------------------
# Serialization and manifest validation
# --------------------------------------------------------------------------------------


def test_serialize_manifest_is_deterministic() -> None:
    entry_a = CacheEntry(path="a.txt", content_hash="a" * 64, size_bytes=1)
    entry_b = CacheEntry(path="b.txt", content_hash="b" * 64, size_bytes=2)

    manifest_one = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=(entry_a, entry_b))
    manifest_two = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=(entry_a, entry_b))

    assert serialize_manifest(manifest_one) == serialize_manifest(manifest_two)


def test_malformed_manifest_json_is_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "cache.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(CacheError, match="content is not valid JSON"):
        load_manifest(manifest_path)


def test_non_mapping_manifest_root_is_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "cache.json"
    manifest_path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")

    with pytest.raises(CacheError, match="document root must be a mapping"):
        load_manifest(manifest_path)


def test_unknown_manifest_field_is_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "cache.json"
    manifest_path.write_text(
        json.dumps({"version": CACHE_MANIFEST_VERSION, "entries": [], "extra": "nope"}),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_manifest(manifest_path)


def test_unknown_entry_field_is_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "cache.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": CACHE_MANIFEST_VERSION,
                "entries": [
                    {
                        "path": "a.txt",
                        "content_hash": "a" * 64,
                        "size_bytes": 1,
                        "unexpected": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    "bad_hash",
    [
        "short",
        "z" * 64,  # not hex
        ("a" * 63) + "AB",  # uppercase and wrong length
        "",
    ],
)
def test_invalid_hash_format_is_rejected(bad_hash: str) -> None:
    with pytest.raises(ValidationError):
        CacheEntry(path="a.txt", content_hash=bad_hash, size_bytes=1)


def test_duplicate_paths_in_manifest_are_rejected() -> None:
    entry = CacheEntry(path="a.txt", content_hash="a" * 64, size_bytes=1)
    with pytest.raises(ValidationError, match="duplicate paths"):
        CacheManifest(version=CACHE_MANIFEST_VERSION, entries=(entry, entry))


def test_load_manifest_missing_file_returns_empty_manifest(tmp_path: Path) -> None:
    manifest = load_manifest(tmp_path / "does-not-exist.json")

    assert manifest.entries == ()
    assert manifest.version == CACHE_MANIFEST_VERSION


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    entry = CacheEntry(path="a.txt", content_hash="a" * 64, size_bytes=1)
    manifest = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=(entry,))
    manifest_path = tmp_path / "cache.json"

    write_manifest_atomically(manifest_path, manifest)
    loaded = load_manifest(manifest_path)

    assert loaded == manifest


# --------------------------------------------------------------------------------------
# Invalidation and path normalization
# --------------------------------------------------------------------------------------


def test_targeted_invalidation_does_not_affect_other_entries() -> None:
    entry_a = CacheEntry(path="a.txt", content_hash="a" * 64, size_bytes=1)
    entry_b = CacheEntry(path="b.txt", content_hash="b" * 64, size_bytes=2)
    manifest = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=(entry_a, entry_b))

    updated = invalidate_path(manifest, "a.txt")

    assert updated.entries == (entry_b,)
    assert manifest.entries == (entry_a, entry_b)  # original untouched


def test_path_normalization_converts_windows_separators() -> None:
    assert normalize_cache_path("src\\module\\file.py") == "src/module/file.py"


def test_path_normalization_collapses_dot_segments_and_slashes() -> None:
    assert normalize_cache_path("./src//module/./file.py") == "src/module/file.py"


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "   ",
        "/etc/passwd",
        "C:\\Windows\\system32",
        "../secret.txt",
        "src/../../secret.txt",
    ],
)
def test_path_normalization_rejects_unsafe_paths(bad_path: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        normalize_cache_path(bad_path)


def test_lookup_and_invalidate_use_the_same_normalized_identity(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "src/module/file.py", b"content")
    entry = build_entry(tmp_path, "src\\module\\file.py")
    manifest = update_entry(EMPTY_MANIFEST, entry)

    result = lookup(tmp_path, manifest, "src/module/file.py")
    assert result.status is CacheStatus.HIT

    updated = invalidate_path(manifest, "src\\module\\file.py")
    assert updated.entries == ()


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_update_entry_does_not_mutate_input_manifest() -> None:
    entry_a = CacheEntry(path="a.txt", content_hash="a" * 64, size_bytes=1)
    manifest = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=(entry_a,))
    before = copy.deepcopy(manifest)

    entry_b = CacheEntry(path="b.txt", content_hash="b" * 64, size_bytes=2)
    update_entry(manifest, entry_b)

    assert manifest == before


def test_build_entry_and_lookup_do_not_mutate_manifest(tmp_path: Path) -> None:
    write_repo_file(tmp_path, "a.txt", b"hello")
    entry = build_entry(tmp_path, "a.txt")
    manifest = update_entry(EMPTY_MANIFEST, entry)
    before = copy.deepcopy(manifest)

    lookup(tmp_path, manifest, "a.txt")
    build_entry(tmp_path, "a.txt")

    assert manifest == before


# --------------------------------------------------------------------------------------
# Atomic write
# --------------------------------------------------------------------------------------


def test_atomic_write_failure_preserves_old_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "cache.json"
    original = CacheManifest(
        version=CACHE_MANIFEST_VERSION,
        entries=(CacheEntry(path="a.txt", content_hash="a" * 64, size_bytes=1),),
    )
    write_manifest_atomically(manifest_path, original)
    original_bytes = manifest_path.read_bytes()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated failure during replace")

    monkeypatch.setattr(os, "replace", boom)

    replacement = CacheManifest(
        version=CACHE_MANIFEST_VERSION,
        entries=(CacheEntry(path="b.txt", content_hash="b" * 64, size_bytes=2),),
    )
    with pytest.raises(OSError, match="simulated failure"):
        write_manifest_atomically(manifest_path, replacement)

    assert manifest_path.read_bytes() == original_bytes
    leftover_tmp_files = list(tmp_path.glob(".cache.json.*.tmp"))
    assert leftover_tmp_files == []


def test_atomic_write_creates_parent_directories(tmp_path: Path) -> None:
    manifest_path = tmp_path / "nested" / "dir" / "cache.json"
    manifest = CacheManifest(version=CACHE_MANIFEST_VERSION, entries=())

    write_manifest_atomically(manifest_path, manifest)

    assert manifest_path.exists()
    assert load_manifest(manifest_path) == manifest

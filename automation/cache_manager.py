"""Deterministic, content-addressed repository file cache (QM-AUTO-003).

Answers one question: has this repository file changed since it was last indexed?
Identity is a SHA-256 hash of the file's bytes — modification time is never read
anywhere in this module. All manifest-transforming functions are pure: they return
a new `CacheManifest` and never mutate the one they were given. Only
`load_manifest`/`write_manifest_atomically` touch the filesystem, and only for the
manifest file itself; reading a cached repository file's bytes happens in
`build_entry`/`lookup`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from automation.schemas.cache import (
    CACHE_MANIFEST_VERSION,
    CacheEntry,
    CacheLookupResult,
    CacheManifest,
    CacheStatus,
    normalize_cache_path,
)


class CacheError(Exception):
    """Raised when a cache manifest or a cached file cannot be safely read, or a
    manifest document cannot be safely loaded.

    Covers the file-access, parse and shape boundaries that precede schema
    validation. Schema failures are deliberately not wrapped in this type — they
    surface as `pydantic.ValidationError`, same as `automation.task_loader`.
    """


def compute_content_hash(data: bytes) -> str:
    """SHA-256 hex digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def _read_file_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CacheError(f"{path}: file cannot be read") from exc


def build_entry(
    repo_root: Path,
    relative_path: str,
    *,
    summary: str | None = None,
    indexed_commit: str | None = None,
) -> CacheEntry:
    """Build a fresh `CacheEntry` by hashing the live file at `repo_root /
    relative_path`.

    Raises:
        CacheError: no regular file exists at that path.
    """
    normalized = normalize_cache_path(relative_path)
    file_path = repo_root / normalized
    if not file_path.is_file():
        raise CacheError(f"{normalized}: no file exists under {repo_root}")

    data = _read_file_bytes(file_path)
    return CacheEntry(
        path=normalized,
        content_hash=compute_content_hash(data),
        size_bytes=len(data),
        summary=summary,
        indexed_commit=indexed_commit,
    )


def lookup(repo_root: Path, manifest: CacheManifest, relative_path: str) -> CacheLookupResult:
    """Compare `manifest`'s record for `relative_path` against the live file at
    `repo_root / relative_path`. Never mutates `manifest`.

    A missing file yields `STALE` when `manifest` has a record for it (previously
    indexed, now gone) or `MISS` when it doesn't (nothing to compare, nothing
    cached). File modification time is never read.
    """
    normalized = normalize_cache_path(relative_path)
    cached = next((entry for entry in manifest.entries if entry.path == normalized), None)
    file_path = repo_root / normalized

    if not file_path.is_file():
        status = CacheStatus.STALE if cached is not None else CacheStatus.MISS
        return CacheLookupResult(
            path=normalized, status=status, cached_entry=cached, current_entry=None
        )

    data = _read_file_bytes(file_path)
    current = CacheEntry(
        path=normalized,
        content_hash=compute_content_hash(data),
        size_bytes=len(data),
    )
    status = (
        CacheStatus.HIT
        if cached is not None and cached.content_hash == current.content_hash
        else CacheStatus.MISS
    )
    return CacheLookupResult(
        path=normalized, status=status, cached_entry=cached, current_entry=current
    )


def update_entry(manifest: CacheManifest, entry: CacheEntry) -> CacheManifest:
    """Return a new manifest with `entry` inserted, replacing any existing entry
    for the same path. Every other entry is carried over unchanged. Never mutates
    `manifest`."""
    remaining = tuple(existing for existing in manifest.entries if existing.path != entry.path)
    updated = tuple(sorted((*remaining, entry), key=lambda item: item.path))
    return CacheManifest(version=manifest.version, entries=updated)


def invalidate_path(manifest: CacheManifest, relative_path: str) -> CacheManifest:
    """Return a new manifest with the entry for `relative_path` removed. Every
    other entry is carried over unchanged. Never mutates `manifest`."""
    normalized = normalize_cache_path(relative_path)
    remaining = tuple(entry for entry in manifest.entries if entry.path != normalized)
    return CacheManifest(version=manifest.version, entries=remaining)


def serialize_manifest(manifest: CacheManifest) -> bytes:
    """Serialize `manifest` deterministically: sorted keys and fixed indentation,
    so the same manifest content always produces identical bytes regardless of
    field-construction order."""
    payload = manifest.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def load_manifest(manifest_path: Path) -> CacheManifest:
    """Load and validate a cache manifest from `manifest_path`.

    A missing file is not an error: it means nothing has been cached yet, and
    yields an empty manifest.

    Raises:
        CacheError: `manifest_path` exists but is not a regular file, cannot be
            read as UTF-8 text, is not parseable as a single JSON document, or is
            not a mapping at its root.
        pydantic.ValidationError: The document parsed but violates the manifest
            schema (unknown fields, malformed hash, duplicate paths, and so on).
    """
    if not manifest_path.exists():
        return CacheManifest(version=CACHE_MANIFEST_VERSION, entries=())
    if not manifest_path.is_file():
        raise CacheError(f"{manifest_path}: path is not a regular file")

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CacheError(f"{manifest_path}: file cannot be read") from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CacheError(f"{manifest_path}: content is not valid JSON") from exc

    if not isinstance(document, dict):
        raise CacheError(
            f"{manifest_path}: document root must be a mapping, got {type(document).__name__}"
        )

    return CacheManifest.model_validate(document)


def write_manifest_atomically(manifest_path: Path, manifest: CacheManifest) -> None:
    """Write `manifest` to `manifest_path` atomically.

    Writes to a temporary file in the same directory, then `os.replace`s it into
    place. `os.replace` performs a same-directory rename, which is atomic on both
    POSIX and Windows — so a failure at any point up to and including that
    replace leaves whatever was previously at `manifest_path` completely
    untouched; a reader never observes a partially written file.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = serialize_manifest(manifest)

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(manifest_path.parent), prefix=f".{manifest_path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, manifest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

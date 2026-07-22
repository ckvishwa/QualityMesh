"""Deterministic, cache-backed context planning for one task (QM-AUTO-004).

Answers, for exactly `task.context_files` — never `task.allowed_paths`, never a
repository walk — what each file's cache status is and whether its full contents
are needed. Reuses `automation.cache_manager` for every hash and manifest update;
this module only adds task-scoped path validation and per-file status decisions on
top of it. Never mutates the `AutonomousTask` or `CacheManifest` it's given.
"""

from __future__ import annotations

from pathlib import Path

from automation.cache_manager import lookup, update_entry
from automation.schemas.cache import CacheManifest, CacheStatus, normalize_cache_path
from automation.schemas.context import ContextBuildResult, ContextFileRecord, ContextFileStatus
from automation.schemas.task import AutonomousTask


class ContextBuildError(Exception):
    """Raised when `task.context_files` cannot be safely resolved into a context
    plan: an empty, absolute, or traversal path, a duplicate (after
    normalization), a path resolving outside the repository root, a directory,
    or a file that doesn't exist.

    A genuine I/O failure reading a file already confirmed to exist surfaces as
    `automation.cache_manager.CacheError` instead — that is a different failure
    class (the request was valid; the read failed), not this module's concern.
    """


def _normalize_or_raise(raw_path: str) -> str:
    try:
        return normalize_cache_path(raw_path)
    except ValueError as exc:
        raise ContextBuildError(f"{raw_path!r}: {exc}") from exc


def _validate_context_paths(task: AutonomousTask, repo_root: Path) -> tuple[str, ...]:
    """Normalize and validate every path in `task.context_files`.

    Returns the normalized paths in their original declaration order (duplicate
    checking depends on that order for its error message; the caller sorts
    separately for deterministic output). Raises `ContextBuildError` for any
    unsafe or unresolvable path — see this module's docstring and
    `ContextBuildError` for the full list of rejected shapes.
    """
    resolved_root = repo_root.resolve()

    normalized_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in task.context_files:
        normalized = _normalize_or_raise(raw_path)
        if normalized in seen:
            raise ContextBuildError(f"duplicate context file after normalization: {normalized}")
        seen.add(normalized)
        normalized_paths.append(normalized)

    for normalized in normalized_paths:
        file_path = repo_root / normalized
        resolved_file = file_path.resolve()
        if not resolved_file.is_relative_to(resolved_root):
            raise ContextBuildError(f"{normalized}: resolves outside the repository root")
        if file_path.is_dir():
            raise ContextBuildError(f"{normalized}: is a directory, not a file")
        if not file_path.is_file():
            raise ContextBuildError(f"{normalized}: file does not exist")

    return tuple(normalized_paths)


def build_context(
    task: AutonomousTask,
    repo_root: Path,
    manifest: CacheManifest,
    *,
    force_full_source: bool = False,
) -> ContextBuildResult:
    """Build a deterministic context plan for `task.context_files`.

    Every normalized path is checked against `manifest` via
    `automation.cache_manager.lookup`. A path with no prior entry, or whose
    content no longer matches its prior entry, gets a freshly rebuilt entry
    (`MISS`/`STALE`) folded into the returned manifest; a path whose content
    still matches (`HIT`) reuses the existing entry untouched and is not written
    back. `records` is sorted by normalized path, so the result is independent
    of `task.context_files`' declaration order. Neither `task` nor `manifest` is
    mutated.

    `force_full_source` marks every record — including `HIT`s — as needing full
    source; without it, only `MISS`/`STALE` records do.

    Raises:
        ContextBuildError: a `context_files` entry is unsafe or unresolvable —
            see `ContextBuildError`.
        CacheError: a file confirmed to exist could not be read.
        pydantic.ValidationError: a resolved value violates the cache/context
            schema (not expected in ordinary use, given the checks above).
    """
    normalized_paths = _validate_context_paths(task, repo_root)

    records: list[ContextFileRecord] = []
    updated_manifest = manifest
    hit_count = miss_count = stale_count = 0

    for normalized in sorted(normalized_paths):
        result = lookup(repo_root, updated_manifest, normalized)
        if result.current_entry is None:
            # Existence was already confirmed in `_validate_context_paths`; only a
            # race between that check and this read could land here.
            raise ContextBuildError(f"{normalized}: file disappeared during context build")

        if result.status is CacheStatus.HIT:
            if result.cached_entry is None:
                raise ContextBuildError(f"{normalized}: cache reported a hit with no cached entry")
            status = ContextFileStatus.HIT
            entry = result.cached_entry
        elif result.cached_entry is not None:
            status = ContextFileStatus.STALE
            entry = result.current_entry
        else:
            status = ContextFileStatus.MISS
            entry = result.current_entry

        if status is not ContextFileStatus.HIT:
            updated_manifest = update_entry(updated_manifest, entry)

        if status is ContextFileStatus.HIT:
            hit_count += 1
        elif status is ContextFileStatus.MISS:
            miss_count += 1
        else:
            stale_count += 1

        records.append(
            ContextFileRecord(
                path=normalized,
                status=status,
                entry=entry,
                full_source_required=status is not ContextFileStatus.HIT or force_full_source,
            )
        )

    return ContextBuildResult(
        records=tuple(records),
        manifest=updated_manifest,
        hit_count=hit_count,
        miss_count=miss_count,
        stale_count=stale_count,
    )

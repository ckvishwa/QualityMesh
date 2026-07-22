"""Typed models for the content-addressed repository context cache (QM-AUTO-003).

A cache entry records what QualityMesh last saw at one repository-relative path:
its SHA-256 content hash, size, and (optionally) a summary and the commit it was
indexed at. Identity is the file's bytes, never its modification time —
`automation.cache_manager`, the only place file bytes are actually read, never
inspects mtime. This module defines the manifest vocabulary only; it performs no
filesystem I/O itself.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_LETTER_PATTERN = re.compile(r"^[A-Za-z]:")

CACHE_MANIFEST_VERSION: Literal["qualitymesh.dev/automation/cache/v1"] = (
    "qualitymesh.dev/automation/cache/v1"
)


def normalize_cache_path(value: object) -> str:
    """Normalize a repository-relative path deterministically.

    Converts `\\` separators to `/`, drops `.` segments and collapses repeated
    slashes, and rejects anything that isn't safely repository-relative: an empty
    path, a POSIX-absolute path, a Windows drive-letter path, or one containing a
    `..` segment. This is the single source of truth for path identity in the
    cache — `automation.cache_manager` calls it directly for every lookup and
    invalidation so a path is always compared against the exact form it was
    stored under, never a raw, un-normalized caller string.
    """
    if not isinstance(value, str):
        raise ValueError("path must be a string")

    raw = value.strip()
    if not raw:
        raise ValueError("path must not be empty")

    slashed = raw.replace("\\", "/")
    if slashed.startswith("/"):
        raise ValueError("path must be repository-relative, not absolute")
    if _DRIVE_LETTER_PATTERN.match(slashed):
        raise ValueError("path must be repository-relative, not absolute")

    segments = [segment for segment in slashed.split("/") if segment not in ("", ".")]
    if not segments:
        raise ValueError("path must not be empty")
    if any(segment == ".." for segment in segments):
        raise ValueError("path must not contain '..' segments")

    return "/".join(segments)


def _array_to_tuple(value: object) -> object:
    """Accept a JSON/Python array and freeze it as a tuple before strict validation."""

    if isinstance(value, list):
        return tuple(cast(list[object], value))
    if isinstance(value, tuple):
        return cast(tuple[object, ...], value)
    # ValueError, not TypeError: Pydantic only converts ValueError and AssertionError
    # into ValidationError. A TypeError would escape model_validate uncaught.
    raise ValueError("expected an array")


class StrictCacheModel(BaseModel):
    """Base configuration for all cache models.

    Mirrors `automation.schemas.task.StrictTaskModel` and
    `automation.schemas.planning.StrictPlanningModel` — a local base per this
    package's existing per-boundary convention, not a shared one.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class CacheStatus(StrEnum):
    """Result of comparing a manifest's record for a path against its live file."""

    HIT = "hit"
    MISS = "miss"
    STALE = "stale"


NormalizedPath = Annotated[str, BeforeValidator(normalize_cache_path), Field(min_length=1)]
Sha256Hex = Annotated[str, Field(pattern=_SHA256_HEX_PATTERN.pattern, min_length=64, max_length=64)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class CacheEntry(StrictCacheModel):
    """One indexed repository file: its content identity, not its history."""

    path: NormalizedPath
    content_hash: Sha256Hex
    size_bytes: Annotated[int, Field(strict=True, ge=0)]
    summary: NonEmptyStr | None = None
    indexed_commit: NonEmptyStr | None = None


class CacheManifest(StrictCacheModel):
    """A versioned, deterministically ordered collection of cache entries.

    `entries` defaults to empty: unlike a task backlog, a freshly bootstrapped
    cache with nothing indexed yet is a normal, expected state, not an error.
    """

    version: Literal["qualitymesh.dev/automation/cache/v1"]
    entries: Annotated[tuple[CacheEntry, ...], BeforeValidator(_array_to_tuple)] = ()

    @model_validator(mode="after")
    def validate_unique_paths(self) -> Self:
        paths = [entry.path for entry in self.entries]
        if len(set(paths)) != len(paths):
            raise ValueError("cache entries must not contain duplicate paths")
        return self


class CacheLookupResult(StrictCacheModel):
    """The outcome of comparing one path's manifest record against its live file.

    `cached_entry` is the manifest's current record for `path`, if any.
    `current_entry` is what would be recorded if the live file were indexed right
    now — `None` when no file exists there. The two combine deterministically:
    both `None` means never indexed and no file (`MISS`); only `current_entry`
    means new content or changed content (`MISS`); both present with matching
    `content_hash` means unchanged (`HIT`); both present with differing
    `content_hash` also means changed content (`MISS`); only `cached_entry` means
    previously indexed and now gone (`STALE`).
    """

    path: NormalizedPath
    status: CacheStatus
    cached_entry: CacheEntry | None = None
    current_entry: CacheEntry | None = None

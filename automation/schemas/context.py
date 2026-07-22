"""Typed models for deterministic, cache-backed context planning (QM-AUTO-004).

A context plan answers, for exactly the files an `AutonomousTask` declared in
`context_files`: what's their current cache status, and does the caller need the
full file contents or can it rely on what's already cached? This module defines
the plan vocabulary only; `automation.context_builder` is the only place that
touches the filesystem or the cache.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from automation.schemas.cache import CacheEntry, CacheManifest, NormalizedPath


class StrictContextModel(BaseModel):
    """Base configuration for all context-planning models.

    Mirrors `automation.schemas.task.StrictTaskModel`,
    `automation.schemas.planning.StrictPlanningModel` and
    `automation.schemas.cache.StrictCacheModel` — a local base per this package's
    existing per-boundary convention, not a shared one.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class ContextFileStatus(StrEnum):
    """A context file's relationship to the cache, as of this build.

    Deliberately distinct from `automation.schemas.cache.CacheStatus`: that
    enum's `STALE` means "previously cached, file now deleted" — a state that
    can never reach this module, since a context file that no longer exists is
    rejected outright (see `automation.context_builder.build_context`). Here
    `STALE` means "previously cached, but the file's content has changed since"
    — the file is still present, just out of date with the manifest.
    """

    HIT = "hit"
    MISS = "miss"
    STALE = "stale"


NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class ContextFileRecord(StrictContextModel):
    """One resolved context file: its status and the entry that now describes
    it.

    For `HIT`, `entry` is the manifest's existing entry, reused as-is — including
    any `summary`/`indexed_commit` it carried. For `MISS` or `STALE`, `entry` is
    freshly rebuilt from the file's current bytes, with no metadata carried over
    from a stale record.
    """

    path: NormalizedPath
    status: ContextFileStatus
    entry: CacheEntry
    full_source_required: bool


class ContextBuildResult(StrictContextModel):
    """The full result of planning context for one task's `context_files`.

    `records` is ordered by normalized path — deterministic, independent of
    `context_files`' declaration order. `manifest` is a new `CacheManifest` with
    every `MISS`/`STALE` record's rebuilt entry applied; the manifest passed to
    `build_context` is never mutated.
    """

    records: tuple[ContextFileRecord, ...]
    manifest: CacheManifest
    hit_count: NonNegativeInt
    miss_count: NonNegativeInt
    stale_count: NonNegativeInt

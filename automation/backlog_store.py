"""Deterministic, atomic backlog status transitions (QM-AUTO-013).

Owns exactly one concern: moving one task's `status` forward along the only
permitted edges (`READY -> ACTIVE`, `ACTIVE -> COMPLETED`, `ACTIVE ->
BLOCKED`) and persisting the result atomically. Every other task field is
carried over unchanged; no frozen model is ever mutated in place —
`apply_status_transition` builds a new `TaskBacklog` via `model_validate`, so
the backlog's own cross-field invariants (unique IDs, dependency shape, and
so on) are re-checked exactly as they would be for a freshly loaded document,
never re-implemented here.

Persistence mirrors `automation.cache_manager.write_manifest_atomically` and
`automation.evidence_recorder.write_evidence_atomically`: a temporary file in
the same directory, then `os.replace`d into place, so a failure at any point
up to and including that replace leaves whatever was previously on disk
completely untouched. `OSError` from that write is never caught here — it
propagates to the caller, which is expected to stop immediately rather than
guess at the backlog's true on-disk state.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from automation.schemas.task import TaskBacklog, TaskStatus

_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.READY: frozenset({TaskStatus.ACTIVE}),
    TaskStatus.ACTIVE: frozenset({TaskStatus.COMPLETED, TaskStatus.BLOCKED}),
}


class BacklogStoreError(Exception):
    """Raised when a status transition is invalid or `task_id` is not found
    in the backlog. Never raised for an I/O failure writing the backlog file
    — that surfaces as `OSError`, uncaught, so a persistence failure always
    stops the caller immediately rather than being mistaken for a rejected
    transition."""


def transition_task_status(
    backlog: TaskBacklog, task_id: str, new_status: TaskStatus
) -> TaskBacklog:
    """Return a new `TaskBacklog` with `task_id`'s status moved to
    `new_status`. Pure: never mutates `backlog` or any task within it — every
    frozen model this touches is rebuilt via `model_validate`, not
    `model_copy`, so the backlog's full set of cross-field invariants is
    re-verified on the way out, not merely assumed to still hold.

    Raises:
        BacklogStoreError: no task in `backlog` has `task_id`, or the task's
            current status does not permit a transition to `new_status`
            (only `READY -> ACTIVE`, `ACTIVE -> COMPLETED`, and
            `ACTIVE -> BLOCKED` are ever permitted).
    """
    task = next((candidate for candidate in backlog.tasks if candidate.id == task_id), None)
    if task is None:
        raise BacklogStoreError(f"{task_id}: task not found in backlog")

    allowed = _ALLOWED_TRANSITIONS.get(task.status, frozenset())
    if new_status not in allowed:
        raise BacklogStoreError(
            f"{task_id}: transition {task.status.value} -> {new_status.value} is not permitted"
        )

    updated_tasks = [
        {**candidate.model_dump(mode="json"), "status": new_status.value}
        if candidate.id == task_id
        else candidate.model_dump(mode="json")
        for candidate in backlog.tasks
    ]
    backlog_data = {**backlog.model_dump(mode="json"), "tasks": updated_tasks}
    return TaskBacklog.model_validate(backlog_data)


def write_backlog_atomically(path: Path, backlog: TaskBacklog) -> None:
    """Write `backlog` to `path` as YAML, atomically. Mirrors
    `automation.cache_manager.write_manifest_atomically`: any failure up to
    and including the final `os.replace` leaves whatever was previously at
    `path` completely untouched. Raises `OSError` on failure — not wrapped,
    so a persistence failure is never mistaken for a rejected transition."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = backlog.model_dump(mode="json")
    data = yaml.safe_dump(payload, sort_keys=True, default_flow_style=False).encode("utf-8")

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def apply_status_transition(
    path: Path, backlog: TaskBacklog, task_id: str, new_status: TaskStatus
) -> TaskBacklog:
    """Transition `task_id` to `new_status` and persist the result
    atomically. Returns the new, already-persisted `TaskBacklog`.

    Raises:
        BacklogStoreError: the transition itself is invalid (see
            `transition_task_status`) — nothing is written in this case.
        OSError: the atomic write failed. The transition was valid, but its
            result never reached disk; the caller must stop immediately
            rather than assume either the old or the new status is durable.
    """
    updated_backlog = transition_task_status(backlog, task_id, new_status)
    write_backlog_atomically(path, updated_backlog)
    return updated_backlog

"""Evidence-package assembly, pre-commit verification, and atomic evidence
persistence (QM-AUTO-012).

`verify_commit_preconditions` is a pure judgement over caller-supplied facts
— it never touches Git or the filesystem itself, so a caller (typically
`automation.git_operator`'s own read-only helpers) gathers those facts.
`build_evidence_package` hashes each changed file's current bytes and
assembles the full, verifiable record. `write_evidence_atomically` never
leaves a partially written file in place, mirroring
`automation.cache_manager.write_manifest_atomically`.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from automation.cache_manager import compute_content_hash
from automation.schemas.evidence_run import (
    ChangedFileRecord,
    CommitPreconditionCode,
    CommitPreconditionResult,
    EvidencePackage,
)
from automation.schemas.provider import ProviderUsage
from automation.schemas.repair import RepairAttempt
from automation.schemas.task import AutonomousTask


class EvidenceRecorderError(Exception):
    """Raised when a changed file cannot be hashed, or an evidence document
    cannot be safely read. Never raised for an ordinary precondition
    violation — see `CommitPreconditionResult` for that."""


def _path_matches(path: str, entry: str) -> bool:
    if path == entry:
        return True
    prefix = entry if entry.endswith("/") else entry + "/"
    return path.startswith(prefix)


def verify_commit_preconditions(
    task: AutonomousTask,
    changed_files: Sequence[ChangedFileRecord],
    *,
    diff_line_count: int,
    validation_passed: bool,
    has_changes: bool,
    merge_state_clean: bool,
    secret_scan_clean: bool,
) -> CommitPreconditionResult:
    """Judge whether `changed_files` are currently safe to commit for
    `task`. Never mutates `task`. Reports every violated check, in the same
    order as this function's own checks, not just the first."""
    violations: list[CommitPreconditionCode] = []

    if any(
        not any(_path_matches(record.path, allowed) for allowed in task.allowed_paths)
        for record in changed_files
    ):
        violations.append(CommitPreconditionCode.PATH_OUTSIDE_ALLOWED)

    if any(
        any(_path_matches(record.path, forbidden) for forbidden in task.forbidden_paths)
        for record in changed_files
    ):
        violations.append(CommitPreconditionCode.FORBIDDEN_PATH_MATCH)

    if len(changed_files) > task.limits.max_files_changed:
        violations.append(CommitPreconditionCode.FILE_COUNT_EXCEEDED)

    if diff_line_count > task.limits.max_diff_lines:
        violations.append(CommitPreconditionCode.DIFF_LINE_LIMIT_EXCEEDED)

    if not validation_passed:
        violations.append(CommitPreconditionCode.VALIDATION_NOT_PASSED)

    if not has_changes:
        violations.append(CommitPreconditionCode.NO_CHANGES)

    if not merge_state_clean:
        violations.append(CommitPreconditionCode.UNRESOLVED_MERGE_STATE)

    if not secret_scan_clean:
        violations.append(CommitPreconditionCode.SECRET_SCAN_BLOCKED)

    return CommitPreconditionResult(eligible=not violations, violations=tuple(violations))


def _hash_changed_file(worktree_path: Path, relative_path: str) -> ChangedFileRecord:
    file_path = worktree_path / relative_path
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise EvidenceRecorderError(f"{relative_path}: file cannot be read") from exc
    return ChangedFileRecord(
        path=relative_path, sha256=compute_content_hash(data), size_bytes=len(data)
    )


def build_evidence_package(
    task: AutonomousTask,
    *,
    worktree_path: str,
    changed_paths: Sequence[str],
    base_commit: str,
    result_commit: str | None,
    branch: str,
    context_files_supplied: Sequence[str],
    provider_name: str,
    model_identifier: str,
    provider_usage: ProviderUsage,
    attempts: Sequence[RepairAttempt],
    precondition_result: CommitPreconditionResult,
    final_outcome: str,
    created_at: datetime,
    known_risks: Sequence[str] = (),
) -> EvidencePackage:
    """Assemble the full evidence package for one task's execution. Never
    mutates `task`. Hashes every path in `changed_paths` from its current
    bytes under `worktree_path` — never from a caller-supplied digest."""
    worktree_root = Path(worktree_path)
    changed_files = tuple(_hash_changed_file(worktree_root, path) for path in changed_paths)

    return EvidencePackage(
        task_id=task.id,
        objective=task.objective,
        acceptance_criteria=task.acceptance_criteria,
        base_commit=base_commit,
        result_commit=result_commit,
        branch=branch,
        worktree_path=worktree_path,
        changed_files=changed_files,
        context_files_supplied=tuple(context_files_supplied),
        provider_name=provider_name,
        model_identifier=model_identifier,
        provider_usage=provider_usage,
        attempts=tuple(attempts),
        precondition_result=precondition_result,
        known_risks=tuple(known_risks),
        final_outcome=final_outcome,
        created_at=created_at,
    )


def serialize_evidence(evidence: EvidencePackage) -> bytes:
    """Serialize `evidence` deterministically: sorted keys and fixed
    indentation, so the same evidence content always produces identical
    bytes."""
    payload = evidence.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_evidence_atomically(path: Path, evidence: EvidencePackage) -> None:
    """Write `evidence` to `path` atomically — a temporary file in the same
    directory, then `os.replace`d into place, mirroring
    `automation.cache_manager.write_manifest_atomically`: a failure at any
    point up to and including that replace leaves whatever was previously at
    `path` completely untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = serialize_evidence(evidence)

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


def _sanitize_round_trip_document(value: object) -> object:
    """Undo the two things a JSON round trip does that strict validation
    can't absorb on its own: (1) every array becomes a Python `list`, but
    several nested schemas this package embeds (`CommandResult.arguments`,
    `ValidationRunResult.results`, defined in modules earlier tasks own and
    this one may not touch) declare a plain `tuple[...]` with no
    list-accepting `BeforeValidator`, so strict mode would reject them
    as-is; converting every list to a tuple here, uniformly, satisfies both
    the fields that already tolerate a list and the ones that don't. (2)
    `ProviderUsage.total_tokens` is a computed field, so it round-trips into
    the JSON but isn't a real settable field — `extra=\"forbid\"` would
    reject it on the way back in, so it's stripped from any dict shaped like
    a `ProviderUsage` (recognized structurally, by having both
    `input_tokens` and `output_tokens`)."""
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        cleaned = {key: _sanitize_round_trip_document(item) for key, item in mapping.items()}
        if "input_tokens" in cleaned and "output_tokens" in cleaned:
            cleaned.pop("total_tokens", None)  # ProviderUsage.total_tokens
        if "exit_code" in cleaned and "stdout" in cleaned:
            cleaned.pop("timed_out", None)  # CommandResult.timed_out
        return cleaned
    if isinstance(value, list):
        sequence = cast("list[object]", value)
        return tuple(_sanitize_round_trip_document(item) for item in sequence)
    return value


def load_evidence(path: Path) -> EvidencePackage:
    """Load and validate a previously written evidence package.

    Validates leniently (`strict=False`), unlike every other loader in this
    package: `EvidencePackage` embeds enum-bearing models from earlier tasks
    (`ProviderOperationMode`, `AggregateOutcome`, `ValidationEntryStatus`,
    `CommandOutcome`, ...) that this task may not modify, and none of them
    declare a `BeforeValidator` tolerating the plain strings a JSON round
    trip produces — this file was written by `write_evidence_atomically`
    itself, not attacker-supplied, so relaxing strict *type* coercion for
    this one read path is safe; `extra=\"forbid\"` (a separate setting) still
    rejects any genuinely unexpected field.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceRecorderError(f"{path}: file cannot be read") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceRecorderError(f"{path}: content is not valid JSON") from exc
    if not isinstance(document, dict):
        raise EvidenceRecorderError(
            f"{path}: document root must be a mapping, got {type(document).__name__}"
        )
    sanitized = _sanitize_round_trip_document(cast("dict[str, object]", document))
    return EvidencePackage.model_validate(sanitized, strict=False)

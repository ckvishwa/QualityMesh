"""Unit tests for the strict evidence-envelope schema."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from qualitymesh.schemas.contract import EvidenceKind, EvidenceSubject
from qualitymesh.schemas.evidence import EvidenceEnvelope


def test_envelope_accepts_kind_subject_and_raw_bytes() -> None:
    envelope = EvidenceEnvelope(
        kind=EvidenceKind.JUNIT,
        subject=EvidenceSubject.SOURCE_REVISION,
        raw_bytes=b"<testsuite/>",
    )

    assert envelope.kind is EvidenceKind.JUNIT
    assert envelope.subject is EvidenceSubject.SOURCE_REVISION
    assert envelope.raw_bytes == b"<testsuite/>"


def test_envelope_digest_is_computed_from_raw_bytes() -> None:
    payload = b"<testsuite/>"
    envelope = EvidenceEnvelope(
        kind=EvidenceKind.JUNIT, subject=EvidenceSubject.ARTIFACT, raw_bytes=payload
    )

    assert envelope.digest == hashlib.sha256(payload).hexdigest()
    assert envelope.digest_algorithm == "sha256"


def test_envelope_digest_cannot_be_supplied_by_the_caller() -> None:
    # digest is a computed field: a caller-supplied value must be rejected, not
    # silently accepted and trusted. A submitter-claimed digest is a claim, not proof.
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceEnvelope(
            kind=EvidenceKind.JUNIT,
            subject=EvidenceSubject.ARTIFACT,
            raw_bytes=b"<testsuite/>",
            digest="0" * 64,  # pyright: ignore[reportCallIssue]
        )


def test_envelope_two_different_payloads_have_different_digests() -> None:
    first = EvidenceEnvelope(
        kind=EvidenceKind.JUNIT, subject=EvidenceSubject.ARTIFACT, raw_bytes=b"a"
    )
    second = EvidenceEnvelope(
        kind=EvidenceKind.JUNIT, subject=EvidenceSubject.ARTIFACT, raw_bytes=b"b"
    )

    assert first.digest != second.digest


def test_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceEnvelope(
            kind=EvidenceKind.JUNIT,
            subject=EvidenceSubject.ARTIFACT,
            raw_bytes=b"<testsuite/>",
            trusted=True,  # pyright: ignore[reportCallIssue]
        )


def test_envelope_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        EvidenceEnvelope(
            kind="not-a-real-kind",  # pyright: ignore[reportArgumentType]
            subject=EvidenceSubject.ARTIFACT,
            raw_bytes=b"<testsuite/>",
        )


def test_envelope_rejects_invalid_subject() -> None:
    with pytest.raises(ValidationError):
        EvidenceEnvelope(
            kind=EvidenceKind.JUNIT,
            subject="not-a-real-subject",  # pyright: ignore[reportArgumentType]
            raw_bytes=b"<testsuite/>",
        )


def test_envelope_rejects_non_bytes_raw_payload() -> None:
    with pytest.raises(ValidationError):
        EvidenceEnvelope(
            kind=EvidenceKind.JUNIT,
            subject=EvidenceSubject.ARTIFACT,
            raw_bytes="not bytes",  # pyright: ignore[reportArgumentType]
        )


def test_envelope_rejects_missing_kind() -> None:
    with pytest.raises(ValidationError, match="kind"):
        EvidenceEnvelope(
            subject=EvidenceSubject.ARTIFACT,  # pyright: ignore[reportCallIssue]
            raw_bytes=b"<testsuite/>",
        )


def test_envelope_rejects_missing_subject() -> None:
    with pytest.raises(ValidationError, match="subject"):
        EvidenceEnvelope(
            kind=EvidenceKind.JUNIT,  # pyright: ignore[reportCallIssue]
            raw_bytes=b"<testsuite/>",
        )


def test_envelope_rejects_missing_raw_bytes() -> None:
    with pytest.raises(ValidationError, match="raw_bytes"):
        EvidenceEnvelope(
            kind=EvidenceKind.JUNIT,  # pyright: ignore[reportCallIssue]
            subject=EvidenceSubject.ARTIFACT,
        )


def test_envelope_is_immutable() -> None:
    envelope = EvidenceEnvelope(
        kind=EvidenceKind.JUNIT, subject=EvidenceSubject.ARTIFACT, raw_bytes=b"<testsuite/>"
    )

    with pytest.raises(ValidationError, match="frozen"):
        envelope.kind = EvidenceKind.SBOM  # pyright: ignore[reportAttributeAccessIssue]

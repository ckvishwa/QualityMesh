"""Unit tests for hardened JUnit XML evidence parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from qualitymesh.parsers.junit import (
    MAX_JUNIT_BYTES,
    MAX_JUNIT_DEPTH,
    MAX_JUNIT_ELEMENTS,
    MAX_JUNIT_TESTCASES,
    JUnitEnvelopeKindError,
    JUnitTestCaseOutcome,
    ParsedJUnitEvidence,
    UnusableJUnitEvidence,
    parse_junit_evidence,
)
from qualitymesh.schemas.contract import EvidenceKind, EvidenceSubject
from qualitymesh.schemas.evidence import EvidenceEnvelope

FIXTURES = Path(__file__).parent.parent / "fixtures" / "junit"


def envelope_from(name: str) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        kind=EvidenceKind.JUNIT,
        subject=EvidenceSubject.SOURCE_REVISION,
        raw_bytes=(FIXTURES / name).read_bytes(),
    )


def envelope_from_bytes(data: bytes) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        kind=EvidenceKind.JUNIT, subject=EvidenceSubject.SOURCE_REVISION, raw_bytes=data
    )


# --------------------------------------------------------------------------------------
# Valid parsing
# --------------------------------------------------------------------------------------


def test_single_suite_parses_successfully() -> None:
    outcome = parse_junit_evidence(envelope_from("valid-single-suite.xml"))

    assert isinstance(outcome, ParsedJUnitEvidence)
    assert outcome.status == "parsed"
    assert len(outcome.suites) == 1
    assert outcome.suites[0].name == "qualitymesh.unit"
    assert outcome.total_tests == 2


def test_multiple_suites_parse_successfully() -> None:
    outcome = parse_junit_evidence(envelope_from("valid-multiple-suites.xml"))

    assert isinstance(outcome, ParsedJUnitEvidence)
    assert len(outcome.suites) == 2
    assert [suite.name for suite in outcome.suites] == [
        "qualitymesh.unit",
        "qualitymesh.integration",
    ]


def test_passed_failed_errored_and_skipped_testcases_are_distinguished() -> None:
    outcome = parse_junit_evidence(envelope_from("valid-with-failures-and-skips.xml"))

    assert isinstance(outcome, ParsedJUnitEvidence)
    suite = outcome.suites[0]
    outcomes_by_name = {tc.name: tc.outcome for tc in suite.testcases}

    assert outcomes_by_name["test_passes"] is JUnitTestCaseOutcome.PASSED
    assert outcomes_by_name["test_fails"] is JUnitTestCaseOutcome.FAILED
    assert outcomes_by_name["test_errors"] is JUnitTestCaseOutcome.ERRORED
    assert outcomes_by_name["test_skipped"] is JUnitTestCaseOutcome.SKIPPED
    assert outcome.total_failures == 1
    assert outcome.total_errors == 1
    assert outcome.total_skipped == 1


def test_parsing_is_deterministic() -> None:
    data = (FIXTURES / "valid-multiple-suites.xml").read_bytes()

    first = parse_junit_evidence(envelope_from_bytes(data))
    second = parse_junit_evidence(envelope_from_bytes(data))

    assert first == second


def test_parsed_evidence_links_to_the_raw_digest() -> None:
    envelope = envelope_from("valid-single-suite.xml")
    outcome = parse_junit_evidence(envelope)

    assert isinstance(outcome, ParsedJUnitEvidence)
    assert outcome.raw_digest == envelope.digest


def test_parsed_evidence_is_immutable() -> None:
    from pydantic import ValidationError

    outcome = parse_junit_evidence(envelope_from("valid-single-suite.xml"))
    assert isinstance(outcome, ParsedJUnitEvidence)

    with pytest.raises(ValidationError, match="frozen"):
        outcome.suites = ()  # pyright: ignore[reportAttributeAccessIssue]


# --------------------------------------------------------------------------------------
# Envelope precondition
# --------------------------------------------------------------------------------------


def test_non_junit_envelope_is_rejected_outright() -> None:
    envelope = EvidenceEnvelope(
        kind=EvidenceKind.SBOM, subject=EvidenceSubject.ARTIFACT, raw_bytes=b"{}"
    )

    with pytest.raises(JUnitEnvelopeKindError, match="expected 'junit'"):
        parse_junit_evidence(envelope)


# --------------------------------------------------------------------------------------
# Hostile XML: DTD, entities, external references
# --------------------------------------------------------------------------------------


def test_dtd_declaration_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("hostile-dtd-declaration.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "DOCTYPE" in outcome.reason


def test_external_entity_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("hostile-external-entity.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "DOCTYPE" in outcome.reason or "entit" in outcome.reason.lower()


def test_entity_expansion_is_rejected_as_unusable_before_expansion() -> None:
    outcome = parse_junit_evidence(envelope_from("hostile-entity-expansion.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)


def test_unsafe_fixture_does_not_leak_expanded_content() -> None:
    outcome = parse_junit_evidence(envelope_from("hostile-external-entity.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "/etc/passwd" not in outcome.reason


# --------------------------------------------------------------------------------------
# Malformed and empty input
# --------------------------------------------------------------------------------------


def test_malformed_xml_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-malformed-xml.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "XML" in outcome.reason


def test_empty_input_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-empty.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)


def test_wrong_root_element_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-wrong-root.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "report" in outcome.reason


def test_missing_required_attribute_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-missing-required-attribute.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "name" in outcome.reason


def test_missing_suite_count_attribute_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-missing-suite-count-attribute.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "failures" in outcome.reason


def test_missing_testcase_name_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-missing-testcase-name.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "<testcase>" in outcome.reason


def test_non_numeric_count_is_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-non-numeric-count.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "tests" in outcome.reason


def test_contradictory_counts_are_rejected_as_unusable() -> None:
    outcome = parse_junit_evidence(envelope_from("invalid-contradictory-counts.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)


def test_contradictory_failure_count_is_rejected_as_unusable() -> None:
    # `tests` matches the actual testcase count, but `failures` does not match the
    # number of testcases that actually report a <failure> child.
    outcome = parse_junit_evidence(envelope_from("invalid-contradictory-failure-count.xml"))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "failures" in outcome.reason


def test_zero_suites_is_rejected_as_unusable_not_empty_success() -> None:
    outcome = parse_junit_evidence(envelope_from_bytes(b"<testsuites/>"))

    assert isinstance(outcome, UnusableJUnitEvidence)


# --------------------------------------------------------------------------------------
# Resource bounds
# --------------------------------------------------------------------------------------


def test_oversized_input_is_rejected_before_parsing() -> None:
    payload = b"<!--" + b"0" * MAX_JUNIT_BYTES + b"-->"
    outcome = parse_junit_evidence(envelope_from_bytes(payload))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "exceeds" in outcome.reason


def test_excessive_nesting_is_rejected_as_unusable() -> None:
    depth = MAX_JUNIT_DEPTH + 5
    inner = "<a/>"
    for _ in range(depth):
        inner = f"<a>{inner}</a>"
    payload = (
        f'<testsuite name="s" tests="0" failures="0" errors="0" skipped="0">{inner}</testsuite>'
    ).encode()

    outcome = parse_junit_evidence(envelope_from_bytes(payload))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "nest" in outcome.reason.lower()


def test_excessive_element_count_is_rejected_as_unusable() -> None:
    filler = "<f/>" * (MAX_JUNIT_ELEMENTS + 1)
    payload = (
        f'<testsuite name="s" tests="0" failures="0" errors="0" skipped="0">{filler}</testsuite>'
    ).encode()

    outcome = parse_junit_evidence(envelope_from_bytes(payload))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "element" in outcome.reason.lower()


def test_excessive_testcase_count_is_rejected_as_unusable() -> None:
    count = MAX_JUNIT_TESTCASES + 1
    cases = "".join(f'<testcase name="t{i}" classname="c"/>' for i in range(count))
    payload = (
        f'<testsuite name="s" tests="{count}" failures="0" errors="0" '
        f'skipped="0">{cases}</testsuite>'
    ).encode()

    outcome = parse_junit_evidence(envelope_from_bytes(payload))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "testcase" in outcome.reason.lower()


# --------------------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------------------


def test_diagnostics_name_the_source() -> None:
    envelope = envelope_from_bytes(b"not xml at all")
    outcome = parse_junit_evidence(envelope, source_name="junit-report.xml")

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "junit-report.xml" in outcome.reason


def test_diagnostics_do_not_echo_document_contents() -> None:
    payload = b"SUPER-SECRET-TOKEN-VALUE not valid xml"
    outcome = parse_junit_evidence(envelope_from_bytes(payload))

    assert isinstance(outcome, UnusableJUnitEvidence)
    assert "SUPER-SECRET-TOKEN-VALUE" not in outcome.reason

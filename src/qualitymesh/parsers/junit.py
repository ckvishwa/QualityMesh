"""Hardened parsing of JUnit XML evidence.

JUnit XML is untrusted, attacker-controlled input. Parsing proceeds in fixed order:
bound the input by size, parse it with DTDs, entities and external references
forbidden, bound the resulting element tree, then extract only the structures this
task needs (`<testsuites>`/`<testsuite>`/`<testcase>` and their outcome children).

Malformed, hostile, oversized or empty input never raises out of
:func:`parse_junit_evidence` and never becomes a successful empty result. It is always
returned as an explicit :class:`UnusableJUnitEvidence`, distinct from
:class:`ParsedJUnitEvidence`, so a later completeness and policy evaluator can see that
required evidence was present but unusable rather than see nothing at all
(`PRD.md` FR-4.3). The only condition that raises is a caller passing an envelope whose
declared kind is not `junit` — a precondition violation, not evidence content.

This module builds only what JUnit evidence needs. It is not a general XML framework.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as defused_ET
from defusedxml.common import DefusedXmlException
from pydantic import ValidationError, computed_field, model_validator

from qualitymesh.schemas.contract import EvidenceKind
from qualitymesh.schemas.evidence import EvidenceEnvelope, StrictEvidenceModel

MAX_JUNIT_BYTES = 8 * 1024 * 1024
"""Largest accepted JUnit report. A realistic report is well under 1 MiB."""

MAX_JUNIT_DEPTH = 12
"""Deepest accepted element nesting. `testsuites > testsuite > testcase > failure` is 4."""

MAX_JUNIT_ELEMENTS = 200_000
"""Largest accepted total element count across the whole document."""

MAX_JUNIT_TESTCASES = 50_000
"""Largest accepted number of `<testcase>` elements."""


class JUnitEnvelopeKindError(ValueError):
    """Raised when an envelope not declaring `junit` is passed to this parser.

    A precondition violation by the caller, not a property of the evidence content —
    unlike every other rejection in this module, it is raised rather than returned as
    :class:`UnusableJUnitEvidence`.
    """


class JUnitTestCaseOutcome(StrEnum):
    """The outcome a `<testcase>` element reports, derived from its child elements."""

    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
    SKIPPED = "skipped"


class JUnitTestCase(StrictEvidenceModel):
    """One `<testcase>` element, reduced to its identity and outcome.

    No failure or error message text is captured: FR-4 does not call for it, and
    nothing in the repository consumes it yet.
    """

    name: str
    classname: str | None = None
    outcome: JUnitTestCaseOutcome


class JUnitTestSuite(StrictEvidenceModel):
    """One `<testsuite>` element, with its declared counts verified against its
    actual testcases rather than trusted at face value."""

    name: str
    tests: int
    failures: int
    errors: int
    skipped: int
    testcases: tuple[JUnitTestCase, ...]

    @model_validator(mode="after")
    def validate_declared_counts_match_testcases(self) -> Self:
        if self.tests != len(self.testcases):
            raise ValueError(
                f"declared tests={self.tests} does not match {len(self.testcases)} "
                f"testcase element(s)"
            )
        for label, declared, outcome in (
            ("failures", self.failures, JUnitTestCaseOutcome.FAILED),
            ("errors", self.errors, JUnitTestCaseOutcome.ERRORED),
            ("skipped", self.skipped, JUnitTestCaseOutcome.SKIPPED),
        ):
            observed = sum(1 for testcase in self.testcases if testcase.outcome is outcome)
            if declared != observed:
                raise ValueError(f"declared {label}={declared} does not match {observed} observed")
        return self


class ParsedJUnitEvidence(StrictEvidenceModel):
    """JUnit evidence that parsed successfully into at least one non-empty suite."""

    status: Literal["parsed"] = "parsed"
    raw_digest: str
    suites: tuple[JUnitTestSuite, ...]

    @model_validator(mode="after")
    def validate_at_least_one_suite(self) -> Self:
        if not self.suites:
            raise ValueError("parsed evidence must contain at least one test suite")
        return self

    @computed_field
    @property
    def total_tests(self) -> int:
        return sum(suite.tests for suite in self.suites)

    @computed_field
    @property
    def total_failures(self) -> int:
        return sum(suite.failures for suite in self.suites)

    @computed_field
    @property
    def total_errors(self) -> int:
        return sum(suite.errors for suite in self.suites)

    @computed_field
    @property
    def total_skipped(self) -> int:
        return sum(suite.skipped for suite in self.suites)


class UnusableJUnitEvidence(StrictEvidenceModel):
    """JUnit evidence that could not be safely or meaningfully parsed.

    Distinct from a raised exception: this is a first-class outcome so that malformed
    or hostile required evidence remains visible to a later evaluator instead of
    disappearing. `reason` names the source and a concise cause; it never echoes
    document content.
    """

    status: Literal["unusable"] = "unusable"
    raw_digest: str
    reason: str


JUnitParseOutcome = ParsedJUnitEvidence | UnusableJUnitEvidence


class _JUnitRejected(Exception):
    """Internal signal that JUnit content is hostile or malformed.

    Always caught inside :func:`parse_junit_evidence` and converted to
    :class:`UnusableJUnitEvidence`. Never escapes this module.
    """


def parse_junit_evidence(
    envelope: EvidenceEnvelope,
    *,
    source_name: str = "<memory>",
) -> JUnitParseOutcome:
    """Parse JUnit XML evidence into a normalised outcome.

    Raises:
        JUnitEnvelopeKindError: `envelope.kind` is not `EvidenceKind.JUNIT`.
    """
    if envelope.kind is not EvidenceKind.JUNIT:
        raise JUnitEnvelopeKindError(
            f"{source_name}: envelope kind is {envelope.kind.value!r}, expected 'junit'"
        )

    try:
        suites = _parse_suites(envelope.raw_bytes, source_name=source_name)
        return ParsedJUnitEvidence(raw_digest=envelope.digest, suites=suites)
    except _JUnitRejected as exc:
        return UnusableJUnitEvidence(raw_digest=envelope.digest, reason=str(exc))
    except ValidationError as exc:
        return UnusableJUnitEvidence(
            raw_digest=envelope.digest, reason=f"{source_name}: {_first_error(exc)}"
        )


def _first_error(exc: ValidationError) -> str:
    """Render the first validation error's location and message, without dumping the
    full multi-line ValidationError representation (which may echo input values)."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else str(error["msg"])


def _parse_suites(data: bytes, *, source_name: str) -> tuple[JUnitTestSuite, ...]:
    if len(data) > MAX_JUNIT_BYTES:
        raise _JUnitRejected(
            f"{source_name}: input of {len(data)} bytes exceeds the maximum JUnit "
            f"report size of {MAX_JUNIT_BYTES} bytes"
        )
    if not data:
        raise _JUnitRejected(f"{source_name}: input is empty")

    root = _parse_safely(data, source_name=source_name)
    _enforce_element_bounds(root, depth=1, source_name=source_name)

    if root.tag == "testsuites":
        suite_elements = list(root.findall("testsuite"))
    elif root.tag == "testsuite":
        suite_elements = [root]
    else:
        raise _JUnitRejected(
            f"{source_name}: invalid root element {root.tag!r}, expected "
            f"'testsuite' or 'testsuites'"
        )

    # An empty `suite_elements` list (e.g. `<testsuites/>`) is not rejected here: it
    # falls through to `ParsedJUnitEvidence`'s own "at least one suite" invariant,
    # avoiding a second guard for the same condition.
    total_testcases = 0
    suites: list[JUnitTestSuite] = []
    for suite_element in suite_elements:
        testcase_elements = list(suite_element.findall("testcase"))
        total_testcases += len(testcase_elements)
        if total_testcases > MAX_JUNIT_TESTCASES:
            raise _JUnitRejected(
                f"{source_name}: document contains more than {MAX_JUNIT_TESTCASES} "
                f"testcase elements"
            )
        suites.append(_build_suite(suite_element, testcase_elements, source_name=source_name))

    return tuple(suites)


def _parse_safely(data: bytes, *, source_name: str) -> Element:
    try:
        root = defused_ET.fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except DefusedXmlException as exc:
        raise _JUnitRejected(
            f"{source_name}: a DOCTYPE declaration is not permitted in JUnit evidence"
        ) from exc
    except ParseError as exc:
        raise _JUnitRejected(f"{source_name}: content is not valid XML: {exc}") from exc
    return root


def _enforce_element_bounds(element: Element, *, depth: int, source_name: str) -> int:
    """Return the element count rooted at `element`, raising once a bound is exceeded.

    Safe to run after parsing: `forbid_entities`/`forbid_dtd` above already remove the
    only amplification vector (entity expansion), so element count here is linear in
    the input the size limit already bounded.
    """
    if depth > MAX_JUNIT_DEPTH:
        raise _JUnitRejected(
            f"{source_name}: document contains nesting deeper than {MAX_JUNIT_DEPTH} levels"
        )
    total = 1
    for child in element:
        total += _enforce_element_bounds(child, depth=depth + 1, source_name=source_name)
        if total > MAX_JUNIT_ELEMENTS:
            raise _JUnitRejected(
                f"{source_name}: document contains more than {MAX_JUNIT_ELEMENTS} elements"
            )
    return total


def _build_suite(
    suite_element: Element,
    testcase_elements: list[Element],
    *,
    source_name: str,
) -> JUnitTestSuite:
    name = suite_element.get("name")
    if name is None:
        raise _JUnitRejected(f"{source_name}: <testsuite> is missing required attribute 'name'")

    counts: dict[str, int] = {}
    for attribute in ("tests", "failures", "errors", "skipped"):
        raw_value = suite_element.get(attribute)
        if raw_value is None:
            raise _JUnitRejected(
                f"{source_name}: <testsuite name={name!r}> is missing required "
                f"attribute {attribute!r}"
            )
        try:
            counts[attribute] = int(raw_value)
        except ValueError as exc:
            raise _JUnitRejected(
                f"{source_name}: <testsuite name={name!r}> attribute {attribute!r} "
                f"is not a valid integer"
            ) from exc

    testcases = tuple(
        _build_testcase(element, source_name=source_name) for element in testcase_elements
    )

    try:
        return JUnitTestSuite(
            name=name,
            tests=counts["tests"],
            failures=counts["failures"],
            errors=counts["errors"],
            skipped=counts["skipped"],
            testcases=testcases,
        )
    except ValidationError as exc:
        raise _JUnitRejected(
            f"{source_name}: <testsuite name={name!r}> {_first_error(exc)}"
        ) from exc


def _build_testcase(testcase_element: Element, *, source_name: str) -> JUnitTestCase:
    name = testcase_element.get("name")
    if name is None:
        raise _JUnitRejected(f"{source_name}: <testcase> is missing required attribute 'name'")

    outcome: JUnitTestCaseOutcome
    if testcase_element.find("failure") is not None:
        outcome = JUnitTestCaseOutcome.FAILED
    elif testcase_element.find("error") is not None:
        outcome = JUnitTestCaseOutcome.ERRORED
    elif testcase_element.find("skipped") is not None:
        outcome = JUnitTestCaseOutcome.SKIPPED
    else:
        outcome = JUnitTestCaseOutcome.PASSED

    return JUnitTestCase(
        name=name,
        classname=testcase_element.get("classname"),
        outcome=outcome,
    )

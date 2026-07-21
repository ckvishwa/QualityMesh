"""Hardened parsers that turn raw evidence bytes into normalised, strict records."""

from qualitymesh.parsers.junit import (
    MAX_JUNIT_BYTES,
    MAX_JUNIT_DEPTH,
    MAX_JUNIT_ELEMENTS,
    MAX_JUNIT_TESTCASES,
    JUnitEnvelopeKindError,
    JUnitParseOutcome,
    JUnitTestCase,
    JUnitTestCaseOutcome,
    JUnitTestSuite,
    ParsedJUnitEvidence,
    UnusableJUnitEvidence,
    parse_junit_evidence,
)

__all__ = [
    "MAX_JUNIT_BYTES",
    "MAX_JUNIT_DEPTH",
    "MAX_JUNIT_ELEMENTS",
    "MAX_JUNIT_TESTCASES",
    "JUnitEnvelopeKindError",
    "JUnitParseOutcome",
    "JUnitTestCase",
    "JUnitTestCaseOutcome",
    "JUnitTestSuite",
    "ParsedJUnitEvidence",
    "UnusableJUnitEvidence",
    "parse_junit_evidence",
]

"""Unit tests for safe release-contract YAML loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from qualitymesh.contracts import (
    MAX_CONTRACT_BYTES,
    MAX_MAPPING_KEYS,
    MAX_NESTING_DEPTH,
    MAX_SEQUENCE_ITEMS,
    MAX_TOTAL_NODES,
    ContractLoadError,
    load_release_contract_bytes,
    load_release_contract_file,
)
from qualitymesh.schemas import Decision, EvidenceKind, EvidenceSubject, ReleaseContract

FIXTURES = Path(__file__).parent.parent / "fixtures" / "contracts"


def load_fixture(name: str) -> ReleaseContract:
    return load_release_contract_file(FIXTURES / name)


# --------------------------------------------------------------------------------------
# Valid loading
# --------------------------------------------------------------------------------------


def test_minimal_fixture_loads_into_release_contract() -> None:
    contract = load_fixture("valid-minimal.yaml")

    assert isinstance(contract, ReleaseContract)
    assert contract.metadata.name == "minimal-release"
    assert contract.metadata.version == 1


def test_full_fixture_exposes_nested_fields() -> None:
    contract = load_fixture("valid-full.yaml")

    assert contract.trust_boundary.repository == "qualitymesh/example"
    assert contract.trust_boundary.oidc.audience == "qualitymesh"
    assert contract.trust_boundary.require_artifact_attestation is True
    assert contract.trust_boundary.trusted_workflows[1].id == "smoke-test"

    attestation = contract.required_evidence[1]
    assert attestation.kind is EvidenceKind.ARTIFACT_ATTESTATION
    assert attestation.subject is EvidenceSubject.ARTIFACT
    assert attestation.artifact_id == "example-api"

    assert contract.required_evidence[2].maximum_age_seconds == 1800
    assert contract.policy_rules.missing_required_evidence is Decision.INSUFFICIENT_EVIDENCE
    assert contract.policy_rules.suspicious_test_count_reduction_percent == 15


def test_yaml_sequences_become_immutable_tuples() -> None:
    contract = load_fixture("valid-full.yaml")

    assert isinstance(contract.required_evidence, tuple)
    assert isinstance(contract.trust_boundary.trusted_workflows, tuple)
    assert isinstance(contract.trust_boundary.allowed_digest_algorithms, tuple)
    assert contract.trust_boundary.trusted_workflows[0].allowed_refs == (
        "refs/heads/main",
        "refs/pull/*/merge",
    )


def test_loaded_contract_is_immutable() -> None:
    contract = load_fixture("valid-minimal.yaml")

    with pytest.raises(ValidationError, match="frozen"):
        contract.metadata.name = "renamed"  # pyright: ignore[reportAttributeAccessIssue]


# --------------------------------------------------------------------------------------
# YAML safety and document shape
# --------------------------------------------------------------------------------------


def test_unsafe_python_tag_is_rejected() -> None:
    with pytest.raises(ContractLoadError, match="not valid YAML"):
        load_fixture("unsafe-python-tag.yaml")


def test_unsafe_python_tag_does_not_execute_the_tagged_callable() -> None:
    payload = b'!!python/object/apply:os.system\n- "exit 1"\n'

    with pytest.raises(ContractLoadError, match="not valid YAML"):
        load_release_contract_bytes(payload)


def test_yaml_aliases_are_rejected() -> None:
    with pytest.raises(ContractLoadError, match="aliases are not permitted"):
        load_fixture("invalid-yaml-alias.yaml")


def test_alias_amplification_is_rejected_before_expansion() -> None:
    # 375 bytes on the wire that expand to roughly 4.8 million nodes during
    # construction. Post-parse bounds cannot see this: the expansion has already
    # happened by the time they run, so aliases are refused before the parser
    # constructs anything.
    lines = ['a0: &a0 ["x","x","x","x","x","x","x","x","x"]']
    for index in range(1, 8):
        lines.append(f"a{index}: &a{index} [" + f"*a{index - 1}," * 9 + "]")
    payload = ("\n".join(lines) + "\n").encode()
    assert len(payload) < MAX_CONTRACT_BYTES

    with pytest.raises(ContractLoadError, match="aliases are not permitted"):
        load_release_contract_bytes(payload)


def test_multiple_documents_are_rejected() -> None:
    with pytest.raises(ContractLoadError, match="not valid YAML"):
        load_fixture("invalid-multiple-documents.yaml")


def test_empty_document_is_rejected() -> None:
    with pytest.raises(ContractLoadError, match="document is empty"):
        load_fixture("invalid-empty.yaml")


def test_scalar_root_is_rejected() -> None:
    with pytest.raises(ContractLoadError, match="root must be a mapping, got str"):
        load_fixture("invalid-scalar-root.yaml")


def test_sequence_root_is_rejected() -> None:
    with pytest.raises(ContractLoadError, match="root must be a mapping, got list"):
        load_fixture("invalid-sequence-root.yaml")


def test_non_string_mapping_key_is_rejected() -> None:
    with pytest.raises(ContractLoadError, match="mapping keys must be strings, got int"):
        load_fixture("invalid-non-string-key.yaml")


# --------------------------------------------------------------------------------------
# Resource bounds
# --------------------------------------------------------------------------------------


def test_oversized_input_is_rejected_before_parsing() -> None:
    # Comment bytes would otherwise parse to an empty document; the size error proves
    # the limit is enforced ahead of the parser.
    payload = b"#" * (MAX_CONTRACT_BYTES + 1)

    with pytest.raises(ContractLoadError, match="exceeds the maximum contract size"):
        load_release_contract_bytes(payload)


def test_input_at_the_size_limit_is_still_parsed() -> None:
    document = b"root: value\n"
    payload = document + b"#" * (MAX_CONTRACT_BYTES - len(document))
    assert len(payload) == MAX_CONTRACT_BYTES

    # Parses and reaches schema validation rather than tripping the size guard.
    with pytest.raises(ValidationError):
        load_release_contract_bytes(payload)


def test_excessive_nesting_is_rejected() -> None:
    depth = MAX_NESTING_DEPTH + 5
    payload = ("root: " + "[" * depth + "]" * depth + "\n").encode()

    with pytest.raises(ContractLoadError, match="nesting deeper than"):
        load_release_contract_bytes(payload)


def test_excessive_node_count_is_rejected() -> None:
    # Thirteen sequences, each individually under the per-sequence limit.
    per_sequence = MAX_SEQUENCE_ITEMS - 112
    keys = (MAX_TOTAL_NODES // per_sequence) + 1
    lines = [f"key{index}: [" + "0," * per_sequence + "]" for index in range(keys)]
    payload = ("\n".join(lines) + "\n").encode()

    with pytest.raises(ContractLoadError, match=r"more than \d+ nodes"):
        load_release_contract_bytes(payload)


def test_oversized_sequence_is_rejected() -> None:
    payload = ("root: [" + "0," * (MAX_SEQUENCE_ITEMS + 1) + "]\n").encode()

    with pytest.raises(ContractLoadError, match="sequence longer than"):
        load_release_contract_bytes(payload)


def test_oversized_mapping_is_rejected() -> None:
    lines = [f"key{index}: 0" for index in range(MAX_MAPPING_KEYS + 1)]
    payload = ("\n".join(lines) + "\n").encode()

    with pytest.raises(ContractLoadError, match="mapping with more than"):
        load_release_contract_bytes(payload)


# --------------------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------------------


def test_diagnostics_name_the_source() -> None:
    with pytest.raises(ContractLoadError, match=r"release-contract\.yaml"):
        load_release_contract_bytes(b"just-a-scalar\n", source_name="release-contract.yaml")


def test_diagnostics_report_yaml_line_and_column() -> None:
    with pytest.raises(ContractLoadError, match=r"line \d+, column \d+"):
        load_release_contract_bytes(b"a: 1\n---\nb: 2\n")


def test_undecodable_input_is_rejected() -> None:
    # PyYAML raises a reader error that carries no problem mark.
    with pytest.raises(ContractLoadError, match="not valid YAML"):
        load_release_contract_bytes(b"\xff\xfe\x00\x00broken")


def test_diagnostics_do_not_echo_document_contents() -> None:
    payload = b"SUPER-SECRET-TOKEN-VALUE\n"

    with pytest.raises(ContractLoadError) as excinfo:
        load_release_contract_bytes(payload)

    assert "SUPER-SECRET-TOKEN-VALUE" not in str(excinfo.value)


def test_missing_file_is_rejected() -> None:
    with pytest.raises(ContractLoadError, match="cannot be read"):
        load_release_contract_file(FIXTURES / "does-not-exist.yaml")


def test_oversized_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "oversized.yaml"
    path.write_bytes(b"#" * (MAX_CONTRACT_BYTES + 1))

    with pytest.raises(ContractLoadError, match="exceeds the maximum contract size"):
        load_release_contract_file(path)


# --------------------------------------------------------------------------------------
# Schema rejection branches, reached through the loader
# --------------------------------------------------------------------------------------


SCHEMA_REJECTIONS: list[tuple[str, str]] = [
    ("invalid-unknown-field.yaml", "Extra inputs are not permitted"),
    ("invalid-coerced-integer.yaml", "valid integer"),
    ("invalid-unknown-evidence-kind.yaml", "unknown evidence kind: nightly-vibes"),
    ("invalid-unknown-evidence-subject.yaml", "unknown evidence subject: the-vibes"),
    ("invalid-non-string-evidence-kind.yaml", "evidence kind must be a string"),
    ("invalid-non-string-evidence-subject.yaml", "evidence subject must be a string"),
    ("invalid-non-array-allowed-refs.yaml", "expected an array"),
    ("invalid-digest-algorithm.yaml", "'sha256'"),
    ("invalid-duplicate-evidence-id.yaml", "required evidence IDs must be unique"),
    ("invalid-duplicate-workflow-id.yaml", "trusted workflow IDs must be unique"),
    ("invalid-duplicate-workflow-path.yaml", "trusted workflow paths must be unique"),
    ("invalid-empty-allowed-refs.yaml", "allowed_refs must contain at least one"),
    ("invalid-duplicate-allowed-refs.yaml", "allowed_refs must not contain duplicates"),
    ("invalid-empty-allowed-events.yaml", "allowed_events must contain at least one"),
    ("invalid-duplicate-allowed-events.yaml", "allowed_events must not contain duplicates"),
    ("invalid-empty-trusted-workflows.yaml", "trustedWorkflows must contain at least one"),
    ("invalid-empty-digest-algorithms.yaml", "allowedDigestAlgorithms must not be empty"),
    ("invalid-dangling-workflow.yaml", "unknown workflows: untrusted-workflow"),
    ("invalid-artifact-binding.yaml", "artifactId is required"),
    ("invalid-forbidden-artifact-id.yaml", "artifactId is only valid"),
    ("invalid-attestation-obligation.yaml", "required artifact-attestation"),
    ("invalid-empty-required-evidence.yaml", "requiredEvidence must contain at least one item"),
    ("invalid-policy-outcome.yaml", "artifactMismatch"),
    ("invalid-missing-evidence-outcome.yaml", "missingRequiredEvidence"),
    ("invalid-exposed-secrets-outcome.yaml", "exposedSecrets must have outcome BLOCK"),
    (
        "invalid-critical-vulnerabilities-outcome.yaml",
        "criticalVulnerabilities must have outcome BLOCK",
    ),
]


@pytest.mark.parametrize(("fixture_name", "expected_message"), SCHEMA_REJECTIONS)
def test_malformed_contract_is_rejected_by_the_schema(
    fixture_name: str, expected_message: str
) -> None:
    with pytest.raises(ValidationError, match=expected_message):
        load_fixture(fixture_name)


def test_every_contract_fixture_is_exercised() -> None:
    exercised = {name for name, _ in SCHEMA_REJECTIONS} | {
        "valid-minimal.yaml",
        "valid-full.yaml",
        "unsafe-python-tag.yaml",
        "invalid-multiple-documents.yaml",
        "invalid-empty.yaml",
        "invalid-scalar-root.yaml",
        "invalid-sequence-root.yaml",
        "invalid-non-string-key.yaml",
        "invalid-yaml-alias.yaml",
    }
    on_disk = {path.name for path in FIXTURES.glob("*.yaml")}

    assert on_disk == exercised

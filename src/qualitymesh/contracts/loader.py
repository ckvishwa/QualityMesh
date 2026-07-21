"""Safe loading of a versioned release contract from YAML.

A contract document is an untrusted external input. Loading therefore proceeds in
fixed order: bound the input by size, parse it without constructing arbitrary Python
objects, bound the resulting structure, and only then hand a plain mapping to the
strict :class:`~qualitymesh.schemas.contract.ReleaseContract` model.

Schema validation is not duplicated here. Structural and semantic rules — unknown
fields, strict typing, enums, cross-references and fail-closed policy pinning — remain
the model's responsibility, and schema failures surface as
:class:`pydantic.ValidationError` with their own field locations.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import yaml

from qualitymesh.schemas import ReleaseContract

MAX_CONTRACT_BYTES = 256 * 1024
"""Largest accepted contract document. A realistic contract is a few kilobytes."""

MAX_NESTING_DEPTH = 24
"""Deepest accepted nesting. The schema itself nests five levels."""

MAX_TOTAL_NODES = 5_000
"""Largest accepted node count across the whole document, scalars included."""

MAX_SEQUENCE_ITEMS = 512
"""Longest accepted single sequence."""

MAX_MAPPING_KEYS = 256
"""Largest accepted single mapping."""


class ContractLoadError(Exception):
    """Raised when a contract document cannot be safely loaded.

    Covers the size, parse and shape boundaries that precede schema validation.
    Schema failures are deliberately not wrapped in this type: they are raised by
    the model as :class:`pydantic.ValidationError`, which already carries the
    offending field location.

    Messages name the source and, where PyYAML supplies one, the position. They never
    include document content, which may hold secret material.
    """


def load_release_contract_bytes(
    data: bytes,
    *,
    source_name: str = "<memory>",
) -> ReleaseContract:
    """Load and validate a release contract from YAML bytes.

    Raises:
        ContractLoadError: The input is too large, is not parseable as a single safe
            YAML document, or is not a bounded mapping.
        pydantic.ValidationError: The document parsed but violates the contract schema.
    """
    if len(data) > MAX_CONTRACT_BYTES:
        raise ContractLoadError(
            f"{source_name}: input of {len(data)} bytes exceeds the maximum contract "
            f"size of {MAX_CONTRACT_BYTES} bytes"
        )

    _reject_aliases(data, source_name=source_name)
    document = _parse_safely(data, source_name=source_name)
    _require_mapping_root(document, source_name=source_name)
    _enforce_resource_bounds(document, source_name=source_name)
    return ReleaseContract.model_validate(document)


def load_release_contract_file(path: Path) -> ReleaseContract:
    """Load and validate a release contract from a file.

    The size limit is applied to the file's reported size, so an oversized file is
    rejected without being read into memory.
    """
    source_name = str(path)
    try:
        size = path.stat().st_size
        if size > MAX_CONTRACT_BYTES:
            raise ContractLoadError(
                f"{source_name}: file of {size} bytes exceeds the maximum contract "
                f"size of {MAX_CONTRACT_BYTES} bytes"
            )
        data = path.read_bytes()
    except OSError as exc:
        raise ContractLoadError(f"{source_name}: file cannot be read") from exc

    return load_release_contract_bytes(data, source_name=source_name)


def _reject_aliases(data: bytes, *, source_name: str) -> None:
    """Refuse YAML aliases before the parser can expand them.

    Alias expansion happens during construction and multiplies: a few hundred bytes of
    nested aliases expands to millions of nodes, exhausting CPU and memory before any
    post-parse bound can be applied. `MAX_TOTAL_NODES` catches the result, but only
    after the damage is done, so the refusal has to come first.

    Event parsing is streaming and linear in the input, and constructs nothing. A
    release contract has no legitimate use for aliases.

    The suppression below is unavoidable rather than convenient: types-pyyaml declares
    `yaml.parse` as `(stream: Unknown, ...) -> Unknown`, and the event stream is the
    only way to inspect a document before construction. A lexical scan for `&` and `*`
    is not an alternative — both occur in legitimate contract values such as
    `refs/pull/*/merge`. The declared type of `events` pins what is actually consumed.
    """
    try:
        events = cast(
            "Iterable[yaml.Event]",
            yaml.parse(data, Loader=yaml.SafeLoader),  # pyright: ignore[reportUnknownMemberType]
        )
        # `yaml.parse` is a generator, so parse errors surface here, inside the guard.
        contains_alias = any(isinstance(event, yaml.AliasEvent) for event in events)
    except yaml.YAMLError as exc:
        raise _invalid_yaml(exc, source_name=source_name) from exc

    if contains_alias:
        raise ContractLoadError(
            f"{source_name}: YAML aliases are not permitted in a release contract"
        )


def _parse_safely(data: bytes, *, source_name: str) -> object:
    """Parse YAML without honouring tags that construct Python objects."""
    try:
        return yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise _invalid_yaml(exc, source_name=source_name) from exc


def _invalid_yaml(exc: yaml.YAMLError, *, source_name: str) -> ContractLoadError:
    """Build a parse diagnostic naming the position, never the content.

    `str(exc)` on a marked PyYAML error embeds a snippet of the offending source line,
    so only the mark is read.
    """
    mark = exc.problem_mark if isinstance(exc, yaml.MarkedYAMLError) else None
    position = "" if mark is None else f" (line {mark.line + 1}, column {mark.column + 1})"
    return ContractLoadError(f"{source_name}: content is not valid YAML{position}")


def _require_mapping_root(document: object, *, source_name: str) -> None:
    """Reject empty documents and any root that is not a single mapping."""
    if document is None:
        raise ContractLoadError(f"{source_name}: document is empty")
    if not isinstance(document, dict):
        raise ContractLoadError(
            f"{source_name}: document root must be a mapping, got {type(document).__name__}"
        )


def _enforce_resource_bounds(document: object, *, source_name: str) -> None:
    """Bound nesting depth, collection sizes and total node count."""
    total = _walk(document, depth=1, source_name=source_name)
    if total > MAX_TOTAL_NODES:
        raise ContractLoadError(f"{source_name}: document holds more than {MAX_TOTAL_NODES} nodes")


def _walk(node: object, *, depth: int, source_name: str) -> int:
    """Return the node count of `node`, raising once any structural bound is exceeded.

    Recursion is bounded by ``MAX_NESTING_DEPTH``, which is checked before descending.
    """
    if depth > MAX_NESTING_DEPTH:
        raise ContractLoadError(
            f"{source_name}: document contains nesting deeper than {MAX_NESTING_DEPTH} levels"
        )

    if isinstance(node, dict):
        # isinstance establishes the container type but leaves its parameters unknown.
        # Keys and values come from untrusted YAML, so both are narrowed to `object`
        # and checked explicitly below.
        mapping = cast("dict[object, object]", node)
        if len(mapping) > MAX_MAPPING_KEYS:
            raise ContractLoadError(
                f"{source_name}: document contains a mapping with more than {MAX_MAPPING_KEYS} keys"
            )
        total = 1
        for key, value in mapping.items():
            if not isinstance(key, str):
                raise ContractLoadError(
                    f"{source_name}: mapping keys must be strings, got {type(key).__name__}"
                )
            total += _walk(value, depth=depth + 1, source_name=source_name)
        return total

    if isinstance(node, list):
        # As above: the container type is established, its element type is not.
        sequence = cast("list[object]", node)
        if len(sequence) > MAX_SEQUENCE_ITEMS:
            raise ContractLoadError(
                f"{source_name}: document contains a sequence longer than "
                f"{MAX_SEQUENCE_ITEMS} items"
            )
        total = 1
        for entry in sequence:
            total += _walk(entry, depth=depth + 1, source_name=source_name)
        return total

    return 1

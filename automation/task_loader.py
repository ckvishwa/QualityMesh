"""Safe loading of an autonomous-task backlog from YAML.

Mirrors `qualitymesh.contracts.loader`: a backlog document is an untrusted external
input, parsed without constructing arbitrary Python objects and handed to the strict
`TaskBacklog` schema for validation. Schema failures are not wrapped here — they
surface as `pydantic.ValidationError` with their own field locations, same as the
release-contract loader.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from automation.schemas.task import TaskBacklog


class BacklogLoadError(Exception):
    """Raised when a backlog document cannot be safely loaded.

    Covers the file-access, parse and shape boundaries that precede schema
    validation. Schema failures are deliberately not wrapped in this type.
    """


def load_backlog_file(path: Path) -> TaskBacklog:
    """Load and validate an autonomous-task backlog from a YAML file.

    Raises:
        BacklogLoadError: The path does not exist, is not a regular file, cannot be
            read as UTF-8 text, is not parseable as a single YAML document, or is not
            a mapping at its root.
        pydantic.ValidationError: The document parsed but violates the backlog schema.
    """
    if not path.exists():
        raise BacklogLoadError(f"{path}: file does not exist")
    if not path.is_file():
        raise BacklogLoadError(f"{path}: path is not a regular file")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BacklogLoadError(f"{path}: file cannot be read") from exc

    return _load_backlog_text(text, source_name=str(path))


def _load_backlog_text(text: str, *, source_name: str) -> TaskBacklog:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BacklogLoadError(f"{source_name}: content is not valid YAML") from exc

    if document is None:
        raise BacklogLoadError(f"{source_name}: document is empty")
    if not isinstance(document, dict):
        raise BacklogLoadError(
            f"{source_name}: document root must be a mapping, got {type(document).__name__}"
        )

    return TaskBacklog.model_validate(document)

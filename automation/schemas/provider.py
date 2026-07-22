"""Typed models for the provider-neutral coding-model interface (QM-AUTO-010).

A `ProviderRequest` carries exactly what a caller chose to hand a coding
model: the task's own objective/acceptance criteria/path boundaries, plus an
explicit list of `ContextAttachment`s — never a scan of the repository, never
anything implicit. No model here has, or ever should have, a field for an API
key, token, or any other credential: those belong to a concrete provider
implementation's own transport layer, never to this shared vocabulary, and
never to a log line or error message built from these models.

This module defines the vocabulary only; `automation.provider` defines the
`CodingProvider` interface, and `automation.providers.fake` is the only
implementation this task adds.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, computed_field, model_validator


def _array_to_tuple(value: object) -> object:
    """Accept a JSON/Python array and freeze it as a tuple before strict validation."""

    if isinstance(value, list):
        return tuple(cast(list[object], value))
    if isinstance(value, tuple):
        return cast(tuple[object, ...], value)
    # ValueError, not TypeError: Pydantic only converts ValueError and AssertionError
    # into ValidationError. A TypeError would escape model_validate uncaught.
    raise ValueError("expected an array")


class StrictProviderModel(BaseModel):
    """Base configuration for all provider models.

    Mirrors `automation.schemas.task.StrictTaskModel` and its siblings — a
    local base per this package's existing per-boundary convention, not a
    shared one.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class ProviderOperationMode(StrEnum):
    """What kind of action a `ProviderRequest` is asking for."""

    IMPLEMENT = "implement"
    REPAIR = "repair"


class ProviderStopReason(StrEnum):
    """Why a provider call ended, independent of `ProviderResponse.success`."""

    COMPLETED = "completed"
    MAX_OUTPUT_TOKENS = "max-output-tokens"
    STOPPED = "stopped"


class ProviderErrorCode(StrEnum):
    """Why a `CodingProvider.execute` call raised `automation.provider.ProviderError`
    instead of returning a `ProviderResponse` — an infrastructure-level failure
    (couldn't reach the provider, rejected the request, ran out of time), not
    the model's own inability to complete the task (see
    `ProviderResponse.success` for that)."""

    INVALID_REQUEST = "invalid-request"
    RATE_LIMITED = "rate-limited"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider-unavailable"
    UNKNOWN = "unknown"


def _parse_operation_mode(value: object) -> ProviderOperationMode:
    """Accept only a known string value or an existing ProviderOperationMode
    instance. Mirrors `qualitymesh.schemas.contract._parse_evidence_kind`:
    strict mode does not coerce a plain string into a `StrEnum` member on its
    own."""

    if isinstance(value, ProviderOperationMode):
        return value
    if not isinstance(value, str):
        raise ValueError("mode must be a string")
    try:
        return ProviderOperationMode(value)
    except ValueError as exc:
        raise ValueError(f"unknown provider operation mode: {value}") from exc


def _parse_stop_reason(value: object) -> ProviderStopReason:
    """Accept only a known string value or an existing ProviderStopReason
    instance."""

    if isinstance(value, ProviderStopReason):
        return value
    if not isinstance(value, str):
        raise ValueError("stop_reason must be a string")
    try:
        return ProviderStopReason(value)
    except ValueError as exc:
        raise ValueError(f"unknown provider stop reason: {value}") from exc


NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrTuple = Annotated[tuple[str, ...], BeforeValidator(_array_to_tuple)]
NonEmptyStrTuple = Annotated[tuple[NonEmptyStr, ...], BeforeValidator(_array_to_tuple)]


class ContextAttachment(StrictProviderModel):
    """One piece of caller-supplied focused context. Never anything the
    provider itself fetched — the caller decided exactly what this is and
    attached it explicitly."""

    path: NonEmptyStr
    content: str
    truncated: bool = False


class ProviderRequest(StrictProviderModel):
    """One bounded, single-purpose request for a coding action.

    `context` is the complete set of files the provider may see for this
    call — never the repository, never anything the provider requests for
    itself mid-call. `mode` distinguishes an initial `IMPLEMENT` attempt from
    a later `REPAIR` attempt; see `automation.repair_loop` for how attempt
    number and mode relate.
    """

    task_id: NonEmptyStr
    objective: NonEmptyStr
    acceptance_criteria: NonEmptyStrTuple
    allowed_paths: NonEmptyStrTuple
    forbidden_paths: StrTuple = ()
    context: Annotated[tuple[ContextAttachment, ...], BeforeValidator(_array_to_tuple)] = ()
    attempt_number: PositiveInt
    max_output_tokens: PositiveInt
    mode: Annotated[ProviderOperationMode, BeforeValidator(_parse_operation_mode)]

    @model_validator(mode="after")
    def validate_request_invariants(self) -> Self:
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria must contain at least one item")
        if not self.allowed_paths:
            raise ValueError("allowed_paths must contain at least one path")
        return self


class ProviderUsage(StrictProviderModel):
    """Token accounting for one provider call. `total_tokens` is always the
    sum of the other two — never an independently supplied value that could
    disagree with them."""

    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt

    @computed_field
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ProviderResponse(StrictProviderModel):
    """What a provider call produced.

    `success` reflects the model's own report of whether it completed the
    requested action — not whether the change is actually correct. Nothing
    in this repository may treat `success` (or any other field here) as
    validation evidence; only a deterministic validation run does that.
    """

    provider_name: NonEmptyStr
    model_identifier: NonEmptyStr
    text: str
    changed_files: StrTuple = ()
    usage: ProviderUsage
    stop_reason: Annotated[ProviderStopReason, BeforeValidator(_parse_stop_reason)]
    success: bool

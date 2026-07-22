"""Unit tests for the provider-neutral coding-model interface (QM-AUTO-010)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.provider import CodingProvider, ProviderError
from automation.providers.fake import FakeCodingProvider
from automation.schemas.provider import (
    ContextAttachment,
    ProviderErrorCode,
    ProviderOperationMode,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_request(**overrides: Any) -> ProviderRequest:
    fields: dict[str, Any] = {
        "task_id": "task-a",
        "objective": "Implement the thing.",
        "acceptance_criteria": ["It works"],
        "allowed_paths": ["automation/"],
        "context": [{"path": "automation/foo.py", "content": "print(1)\n"}],
        "attempt_number": 1,
        "max_output_tokens": 4096,
        "mode": "implement",
    }
    fields.update(overrides)
    return ProviderRequest.model_validate(fields)


def build_response(**overrides: Any) -> ProviderResponse:
    fields: dict[str, Any] = {
        "provider_name": "fake",
        "model_identifier": "fake-model-1",
        "text": "Implemented the change.",
        "changed_files": ["automation/foo.py"],
        "usage": ProviderUsage(input_tokens=100, output_tokens=50),
        "stop_reason": "completed",
        "success": True,
    }
    fields.update(overrides)
    return ProviderResponse.model_validate(fields)


# --------------------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------------------


def test_valid_request_is_accepted() -> None:
    request = build_request()

    assert request.task_id == "task-a"
    assert request.mode is ProviderOperationMode.IMPLEMENT
    assert request.context[0].path == "automation/foo.py"


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_request(unexpected_field="nope")


def test_missing_acceptance_criteria_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_request(acceptance_criteria=[])


def test_missing_allowed_paths_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_request(allowed_paths=[])


def test_context_attachment_is_immutable() -> None:
    attachment = ContextAttachment(path="a.py", content="x")

    with pytest.raises(ValidationError):
        attachment.content = "y"  # type: ignore[misc]


def test_request_is_immutable() -> None:
    request = build_request()

    with pytest.raises(ValidationError):
        request.objective = "different"  # type: ignore[misc]


# --------------------------------------------------------------------------------------
# Usage propagation
# --------------------------------------------------------------------------------------


def test_usage_total_tokens_is_computed() -> None:
    usage = ProviderUsage(input_tokens=100, output_tokens=25)

    assert usage.total_tokens == 125


def test_usage_propagates_through_response() -> None:
    response = build_response(usage=ProviderUsage(input_tokens=10, output_tokens=5))

    assert response.usage.total_tokens == 15


# --------------------------------------------------------------------------------------
# Fake provider
# --------------------------------------------------------------------------------------


def test_fake_provider_returns_configured_success() -> None:
    response = build_response()
    provider = FakeCodingProvider(response=response)

    result = provider.execute(build_request())

    assert result == response
    assert result.success is True


def test_fake_provider_raises_configured_error() -> None:
    error = ProviderError("rate limited", code=ProviderErrorCode.RATE_LIMITED)
    provider = FakeCodingProvider(error=error)

    with pytest.raises(ProviderError) as exc_info:
        provider.execute(build_request())
    assert exc_info.value.code is ProviderErrorCode.RATE_LIMITED


def test_fake_provider_records_requests_in_order() -> None:
    provider = FakeCodingProvider(response=build_response())
    first = build_request(attempt_number=1)
    second = build_request(attempt_number=2, mode="repair")

    provider.execute(first)
    provider.execute(second)

    assert provider.received_requests == (first, second)


def test_fake_provider_satisfies_coding_provider_protocol() -> None:
    provider = FakeCodingProvider(response=build_response())

    assert isinstance(provider, CodingProvider)


# --------------------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------------------


def test_implement_mode() -> None:
    request = build_request(mode="implement")
    assert request.mode is ProviderOperationMode.IMPLEMENT


def test_repair_mode() -> None:
    request = build_request(mode="repair", attempt_number=2)
    assert request.mode is ProviderOperationMode.REPAIR


# --------------------------------------------------------------------------------------
# Security rules
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls",
    [ProviderRequest, ProviderResponse, ProviderUsage, ContextAttachment],
)
def test_no_secret_bearing_field_names(model_cls: type[BaseModel]) -> None:
    # Deliberately excludes bare "key"/"token": this domain has legitimate,
    # non-secret uses of both (token *counts*, dict keys). Only substrings
    # that are themselves unambiguously credential-shaped are checked.
    forbidden_substrings = ("api_key", "apikey", "secret", "password", "credential", "auth_token")
    for field_name in model_cls.model_fields:
        lowered = field_name.lower()
        for forbidden in forbidden_substrings:
            message = f"{model_cls.__name__}.{field_name} looks secret-bearing"
            assert forbidden not in lowered, message


def test_provider_error_message_does_not_require_secret_data() -> None:
    error = ProviderError("provider unavailable", code=ProviderErrorCode.PROVIDER_UNAVAILABLE)

    assert "key" not in str(error).lower()
    assert "secret" not in str(error).lower()


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_no_input_mutation() -> None:
    request = build_request()
    before = copy.deepcopy(request)
    provider = FakeCodingProvider(response=build_response())

    provider.execute(request)

    assert request == before

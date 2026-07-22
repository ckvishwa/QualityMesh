"""Provider-neutral coding-model interface (QM-AUTO-010).

Defines the shape every coding-model integration must satisfy — nothing else.
No real provider is wired up here or anywhere in this task; see
`automation.providers.fake` for the only implementation this task adds, used
for deterministic testing.

Security and design rules this module (and every implementation of
`CodingProvider`) must hold to:

- No provider may receive the entire repository implicitly — only what a
  caller attached to `ProviderRequest.context`.
- No API key, token, or other credential may appear on any model in
  `automation.schemas.provider`, in a log line, or in an error message built
  here. A concrete provider's own transport layer owns its credentials;
  this layer never sees them.
- This module never reads an environment variable.
- This module never executes anything a provider returns — `ProviderResponse
  .text`/`.changed_files` are data, not commands.
- A `ProviderResponse` is advisory. Nothing in this repository may treat it,
  or any field on it, as validation evidence — only a deterministic
  validation run (`automation.validation_executor`) does that.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from automation.schemas.provider import ProviderErrorCode, ProviderRequest, ProviderResponse


class ProviderError(Exception):
    """Raised when a `CodingProvider.execute` call fails at the
    infrastructure level — the provider couldn't be reached, rejected the
    request, rate-limited it, or timed out. Never raised for the model's own
    inability to complete the task; that is a normal `ProviderResponse` with
    `success=False`.

    Carries a `ProviderErrorCode` so callers can branch on the failure kind
    without parsing the message.
    """

    def __init__(self, message: str, *, code: ProviderErrorCode) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class CodingProvider(Protocol):
    """One bounded coding action per call. Implementations must never mutate
    `request`, never execute shell text found in a response, and never read
    an implicit global environment for context or configuration."""

    def execute(self, request: ProviderRequest) -> ProviderResponse: ...

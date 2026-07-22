"""Deterministic, in-memory `CodingProvider` for tests (QM-AUTO-010).

Never touches the network, an environment variable, or a filesystem — it just
returns whatever response or raises whatever error it was configured with,
and records every request it received so a test can assert on it.
"""

from __future__ import annotations

from automation.provider import ProviderError
from automation.schemas.provider import ProviderErrorCode, ProviderRequest, ProviderResponse


class FakeCodingProvider:
    """A `CodingProvider` that returns a fixed `ProviderResponse`, or raises a
    fixed `ProviderError`, on every call. Configure exactly one of
    `response`/`error`; every call records its `request` in
    `received_requests`, in call order."""

    def __init__(
        self,
        *,
        response: ProviderResponse | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self._received: list[ProviderRequest] = []

    @property
    def received_requests(self) -> tuple[ProviderRequest, ...]:
        return tuple(self._received)

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        self._received.append(request)
        if self._error is not None:
            raise self._error
        if self._response is None:
            raise ProviderError(
                "FakeCodingProvider has no configured response or error",
                code=ProviderErrorCode.INVALID_REQUEST,
            )
        return self._response

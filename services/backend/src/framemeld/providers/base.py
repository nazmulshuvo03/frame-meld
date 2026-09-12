from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """An adapter failure, carrying its own retry verdict.

    The adapter decides, because only the adapter knows what a given
    provider's error means — one service's 429 is a transient queue, the
    next one's is a hard daily cap. Spec section 5.4 splits the world the
    same way: transport and 5xx retry, content refusals are terminal.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProviderTransportError(ProviderError):
    """The call did not complete: a timeout, a connection reset, a 5xx."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class ProviderRefusedError(ProviderError):
    """The provider understood and declined. Retrying spends money to be
    refused again, so this is terminal."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class ProviderResponseError(ProviderError):
    """A well-formed response with an unusable body. Terminal for the same
    reason: the model will almost certainly produce the same shape again,
    and each attempt costs."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass(frozen=True)
class ProviderResult:
    """Spec section 3.3. `cost_usd` must be what the provider actually
    reported, never an estimate — from M3 it is what credit settlement
    subtracts."""

    text: str | None
    bytes_: bytes | None
    cost_usd: Decimal
    provider: str
    model: str
    #: The raw provider response, kept for debugging a bad generation
    #: months later. Never parsed by anything but the adapter itself.
    raw: dict[str, Any]


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self, *, system: str, user: str, json_schema: dict[str, Any]
    ) -> ProviderResult: ...

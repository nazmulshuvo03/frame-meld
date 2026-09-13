from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderResult,
    ProviderTransportError,
)

#: Generous, because a 30-shot script from a free-tier model is slow. The
#: connect timeout stays short: a host that will not answer the handshake
#: is not going to answer the completion either.
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

#: Statuses worth another attempt. Everything else 4xx is a request we
#: built wrong, or a key problem, and retrying changes neither.
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})


class OpenRouterLLMProvider:
    name = "openrouter"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is not set but LLM_PROVIDER=openrouter")
        self.model = settings.llm_model
        # Injected only by tests, via httpx.MockTransport. It is the one
        # seam that lets this adapter's error mapping be tested exhaustively
        # without a network or a budget.
        self._transport = transport

    async def complete(
        self, *, system: str, user: str, json_schema: dict[str, Any]
    ) -> ProviderResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_schema", "json_schema": json_schema},
            "temperature": 0.7,
            # Asks OpenRouter to report what the call actually cost. Free
            # models report zero; paid ones must report truthfully, because
            # from M3 this number is what credit settlement subtracts.
            "usage": {"include": True},
        }
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            # OpenRouter uses these for its model-usage dashboard. Neither
            # is secret.
            "HTTP-Referer": "https://framemeld.local",
            "X-Title": "FrameMeld",
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                response = await client.post(
                    f"{settings.openrouter_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error never contains the request headers,
            # so the key cannot leak through here.
            raise ProviderTransportError(
                f"Could not reach the language model: {type(exc).__name__}."
            ) from exc

        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The language model service returned {response.status_code}. "
                "This is usually temporary."
            )
        if response.status_code >= 400:
            # Deliberately does NOT include the body. A 401's body can echo
            # the key back, and this message reaches the user through
            # jobs.error.
            raise ProviderResponseError(
                f"The language model rejected the request ({response.status_code})."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("The language model returned an unreadable reply.") from exc

        choices = body.get("choices") or []
        if not choices:
            raise ProviderResponseError("The language model returned no reply.")

        choice = choices[0]
        if choice.get("finish_reason") == "content_filter":
            raise ProviderRefusedError(
                "The language model declined to work with this story. "
                "Try rewording the parts it might have flagged."
            )

        return ProviderResult(
            text=(choice.get("message") or {}).get("content"),
            bytes_=None,
            cost_usd=_reported_cost(body),
            provider=self.name,
            model=body.get("model") or self.model,
            raw=body,
        )


def _reported_cost(body: dict[str, Any]) -> Decimal:
    """What the provider says the call cost. Free models omit it entirely,
    which is zero — not an error, and not a reason to guess.

    Non-finite values are rejected rather than passed through: json.loads
    accepts the non-standard NaN and Infinity literals, and Decimal(str(...))
    builds both without complaint. From M3 this number is what credit
    settlement subtracts from a balance, where a NaN would make every
    comparison silently false and an Infinity would corrupt the balance.
    Zero is the safe reading of a cost we cannot trust — it under-charges
    rather than over-charges, and the raw response is kept for the audit.
    """
    raw = (body.get("usage") or {}).get("cost")
    if raw is None:
        return Decimal("0")
    try:
        cost = Decimal(str(raw))
    except InvalidOperation:
        return Decimal("0")
    if not cost.is_finite() or cost < 0:
        return Decimal("0")
    return cost

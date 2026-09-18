import base64
from typing import Any

import httpx

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderResult,
    ProviderTransportError,
)
from framemeld.providers.rates import usd_for_image

#: Longer than the text timeout: image generation is slower, and the
#: connect timeout stays short because a host that will not answer the
#: handshake is not going to draw anything either.
_TIMEOUT = httpx.Timeout(180.0, connect=10.0)

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

_REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"})


class GeminiImageProvider:
    """Gemini's image models over the same generateContent endpoint the text
    adapter uses.

    VERIFIED AGAINST THE LIVE API on 2026-09-19, once billing was enabled on
    the key. M3-D11's probe (2026-09-13) had already established that
    `:predict` 404s for every current image model and that generateContent
    is the right endpoint; what it could not do was exercise the success
    path, because image generation has zero free-tier quota (`limit: 0`).
    `test_the_real_api_draws_something` now passes, and the documented
    `inlineData` shape `_extract_image` was written against turned out to be
    the real one -- no correction was needed.
    """

    name = "gemini"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set but IMAGE_PROVIDER=gemini")
        self.model = settings.image_model
        # Injected only by tests, via httpx.MockTransport -- the same seam
        # the two text adapters use, for the same reason.
        self._transport = transport

    def _request_payload(self, prompt: str, refs: list[bytes]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        # M3 never passes refs; M4 does, with each character's canonical
        # sheet. Inline rather than uploaded: a sheet is well under the
        # inline size limit and an upload is a second request that can fail
        # on its own.
        parts.extend(
            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(ref).decode()}}
            for ref in refs
        )
        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }

    @staticmethod
    def _extract_image(body: dict[str, Any]) -> tuple[bytes, str]:
        candidates = body.get("candidates") or []
        if not candidates:
            block_reason = (body.get("promptFeedback") or {}).get("blockReason")
            if block_reason:
                raise ProviderRefusedError(
                    "The image model declined this description. "
                    "Try rewording the parts it might have flagged."
                )
            raise ProviderResponseError("The image model returned no image.")

        candidate = candidates[0]
        if candidate.get("finishReason") in _REFUSAL_FINISH_REASONS:
            raise ProviderRefusedError(
                "The image model declined this description. "
                "Try rewording the parts it might have flagged."
            )

        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                try:
                    return base64.b64decode(inline["data"]), inline.get("mimeType", "image/png")
                except (ValueError, TypeError) as exc:
                    raise ProviderResponseError(
                        "The image model returned something that is not an image."
                    ) from exc

        raise ProviderResponseError("The image model returned no image.")

    async def generate(
        self, *, prompt: str, refs: list[bytes], params: dict[str, Any]
    ) -> ProviderResult:
        url = f"{settings.gemini_base_url}/models/{self.model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                response = await client.post(
                    url,
                    json=self._request_payload(prompt, refs),
                    headers={"x-goog-api-key": settings.gemini_api_key},
                )
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error never contains request headers, so
            # the key cannot leak through here.
            raise ProviderTransportError(
                f"Could not reach the image model: {type(exc).__name__}."
            ) from exc

        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The image service returned {response.status_code}. This is usually temporary."
            )
        if response.status_code >= 400:
            # Deliberately without the body: this message reaches the user
            # through jobs.error.
            raise ProviderResponseError(
                f"The image model rejected the request ({response.status_code})."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("The image model returned an unreadable reply.") from exc

        data, mime = self._extract_image(body)
        return ProviderResult(
            text=None,
            bytes_=data,
            # Priced per image, not per token -- there is no usage block to
            # read, so the rate table is the only source (M3-D6).
            cost_usd=usd_for_image(self.model),
            provider=self.name,
            model=body.get("modelVersion") or self.model,
            raw={"mimeType": mime},
        )

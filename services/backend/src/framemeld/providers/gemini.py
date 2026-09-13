from typing import Any

import httpx

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderResult,
    ProviderTransportError,
)
from framemeld.providers.rates import usd_for_tokens

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

#: Same split as the OpenRouter adapter: transport/5xx retry, everything
#: else is a request or a key problem retrying will not fix.
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

#: Gemini's finishReason values that mean "the model refused or was
#: blocked", not "something went wrong in transit". Terminal either way --
#: retrying asks the same safety filter the same question again.
_REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"})

#: generateContent's responseSchema is the older, narrower Gemini Schema
#: proto, not the OpenAPI-3.0 draft OpenAI's json_schema wrapper assumes.
#: Verified live (2026-09-13, gemini-3.5-flash-lite): additionalProperties
#: is rejected outright ("Unknown name ... Cannot find field"), and
#: minimum/maximum/minItems/maxItems are silently part of the same
#: rejection once additionalProperties is removed -- the request 400s
#: generically until all four are gone. None of these bounds are lost:
#: framemeld.script.generation's Pydantic validators (_clamp_duration,
#: _truncate_to_max_shots, the enum fallbacks) already clamp rather than
#: trust the model, so dropping them from the schema removes a hint to the
#: model, not the actual guarantee.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"additionalProperties", "minimum", "maximum", "minItems", "maxItems"}
)


def _for_gemini(schema: object) -> object:
    if isinstance(schema, dict):
        return {
            key: _for_gemini(value)
            for key, value in schema.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_for_gemini(item) for item in schema]
    return schema


class GeminiLLMProvider:
    name = "gemini"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set but LLM_PROVIDER=gemini")
        self.model = settings.llm_model
        # Injected only by tests, via httpx.MockTransport -- same seam as
        # the OpenRouter adapter, for the same reason.
        self._transport = transport

    async def complete(
        self, *, system: str, user: str, json_schema: dict[str, Any]
    ) -> ProviderResult:
        generation_config: dict[str, Any] = {"responseMimeType": "application/json"}
        # json_schema arrives in the OpenAI wrapper shape ({name, strict,
        # schema}) that framemeld.script.generation builds once for every
        # adapter. Gemini's responseSchema wants only the inner schema body.
        schema_body = json_schema.get("schema")
        if schema_body is not None:
            generation_config["responseSchema"] = _for_gemini(schema_body)

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": generation_config,
        }
        headers = {"x-goog-api-key": settings.gemini_api_key}
        url = f"{settings.gemini_base_url}/models/{self.model}:generateContent"

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error never contains request headers, so
            # the key cannot leak through here.
            raise ProviderTransportError(
                f"Could not reach the language model: {type(exc).__name__}."
            ) from exc

        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The language model service returned {response.status_code}. "
                "This is usually temporary."
            )
        if response.status_code >= 400:
            # Deliberately does NOT include the body. Gemini's error body
            # echoes the request back in some cases, and the key travels in
            # a header, not the body -- but the message still reaches the
            # user through jobs.error, so it stays generic on principle.
            raise ProviderResponseError(
                f"The language model rejected the request ({response.status_code})."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("The language model returned an unreadable reply.") from exc

        candidates = body.get("candidates") or []
        if not candidates:
            # A prompt can be blocked before any candidate is produced at
            # all; promptFeedback.blockReason is Gemini's signal for that,
            # distinct from a per-candidate finishReason.
            block_reason = (body.get("promptFeedback") or {}).get("blockReason")
            if block_reason:
                raise ProviderRefusedError(
                    "The language model declined to work with this story. "
                    "Try rewording the parts it might have flagged."
                )
            raise ProviderResponseError("The language model returned no reply.")

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            raise ProviderResponseError(
                "The script was too long for the model to finish. Try a shorter story."
            )
        if finish_reason in _REFUSAL_FINISH_REASONS:
            raise ProviderRefusedError(
                "The language model declined to work with this story. "
                "Try rewording the parts it might have flagged."
            )

        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if not text:
            raise ProviderResponseError("The language model returned no reply.")

        usage = body.get("usageMetadata") or {}
        model_used = body.get("modelVersion") or self.model

        return ProviderResult(
            text=text,
            bytes_=None,
            # Gemini reports token counts, never a dollar figure the way
            # OpenRouter's usage.cost does, so the adapter converts. M2 shipped
            # this as a hardcoded zero with the debt recorded; this is it paid.
            # `or 0` because a field can be absent on a cached or zero-token
            # response, and int() of None would be the only way this call ever
            # failed *after* the model had already been paid for.
            cost_usd=usd_for_tokens(
                model_used,
                int(usage.get("promptTokenCount") or 0),
                int(usage.get("candidatesTokenCount") or 0),
            ),
            provider=self.name,
            model=model_used,
            raw=body,
        )

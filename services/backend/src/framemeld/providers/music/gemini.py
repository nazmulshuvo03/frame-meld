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
from framemeld.providers.rates import usd_for_music

#: Lyria writes a whole piece of music before it answers, and a live call
#: took roughly a minute. Generous, and still bounded -- the worker holds an
#: arq slot for the duration of this call.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

_REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"})

#: What Lyria returns, confirmed live: a 192 kbps MP3, not the WAV every
#: other audio path in this codebase produces. Both are fine -- the asset
#: row records a mime, Remotion's Audio decodes either, and the duration
#: reader parses both -- but this is the first place the two diverge, so it
#: is named rather than assumed.
MUSIC_MIME = "audio/mpeg"


class LyriaMusicProvider:
    """Google's Lyria over the same generateContent endpoint every other
    Gemini adapter here uses.

    **Duration is not an input, and that is the interesting part.** Spec
    section 3.3 gives `compose` a `duration_sec` because "a score has to fit
    a film that already exists". Lyria does not take one: it has no duration
    parameter at all, and a live call for a 21-second film returned 174
    seconds of music. So `duration_sec` is honoured on the *other* side --
    the track is handed to Remotion whole and the composition plays as much
    of it as the film is long (M5-D12 already makes music one track across
    the whole film, ducked under the speech).

    That means a short film pays for music it does not use, which is the
    right trade at $0.08 a track: the alternative is trimming audio server
    side to save nothing, since Lyria bills per request rather than per
    second. `duration_sec` is still validated, because a caller asking for
    zero seconds of music has a bug worth surfacing.
    """

    name = "lyria"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set but MUSIC_PROVIDER=lyria")
        self.model = settings.music_model
        # Injected only by tests, via httpx.MockTransport -- the same seam
        # every other adapter here uses, for the same reason.
        self._transport = transport

    @staticmethod
    def _request_payload(prompt: str) -> dict[str, Any]:
        # No generationConfig. `responseModalities: ["AUDIO"]` is accepted
        # and makes no difference -- Lyria answers with audio either way --
        # so sending it would be a line of code claiming to control
        # something it does not.
        return {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}

    @staticmethod
    def _extract_audio(body: dict[str, Any]) -> tuple[bytes, str]:
        candidates = body.get("candidates") or []
        if not candidates:
            block_reason = (body.get("promptFeedback") or {}).get("blockReason")
            if block_reason:
                raise ProviderRefusedError(
                    "The music model declined this description. "
                    "Try rewording the parts it might have flagged."
                )
            raise ProviderResponseError("The music model returned no audio.")

        candidate = candidates[0]
        if candidate.get("finishReason") in _REFUSAL_FINISH_REASONS:
            raise ProviderRefusedError(
                "The music model declined this description. "
                "Try rewording the parts it might have flagged."
            )

        # Lyria answers with two parts: a text part holding section markers
        # ("[[A0]]\n[[B1]]...") and the audio. Walk for the audio rather than
        # indexing, because the text part comes first and the order is not
        # promised anywhere.
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                try:
                    return base64.b64decode(inline["data"]), inline.get("mimeType", MUSIC_MIME)
                except (ValueError, TypeError) as exc:
                    raise ProviderResponseError(
                        "The music model returned something that is not audio."
                    ) from exc

        raise ProviderResponseError("The music model returned no audio.")

    async def compose(
        self, *, prompt: str, duration_sec: float, params: dict[str, Any]
    ) -> ProviderResult:
        if duration_sec <= 0:
            # Matches the stub's refusal, and for the same reason: a caller
            # asking to score a film of no length has a bug, and refusing
            # exercises the refund path rather than billing for it.
            raise ProviderRefusedError("There is no film to score yet.")

        url = f"{settings.gemini_base_url}/models/{self.model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                response = await client.post(
                    url,
                    json=self._request_payload(prompt),
                    headers={"x-goog-api-key": settings.gemini_api_key},
                )
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error never contains request headers, so
            # the key cannot leak through here.
            raise ProviderTransportError(
                f"Could not reach the music model: {type(exc).__name__}."
            ) from exc

        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The music service returned {response.status_code}. This is usually temporary."
            )
        if response.status_code >= 400:
            # Deliberately without the body: this message reaches the user
            # through jobs.error.
            raise ProviderResponseError(
                f"The music model rejected the request ({response.status_code})."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("The music model returned an unreadable reply.") from exc

        data, mime = self._extract_audio(body)
        return ProviderResult(
            text=None,
            bytes_=data,
            # Flat per request. There is no usage block worth reading here:
            # Lyria reports token counts, but it is not billed on them.
            cost_usd=usd_for_music(self.model),
            provider=self.name,
            model=body.get("modelVersion") or self.model,
            raw={"mimeType": mime},
        )

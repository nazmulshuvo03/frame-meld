import base64
import re
from typing import Any

import httpx

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderResult,
    ProviderTransportError,
)
from framemeld.providers.rates import usd_for_speech
from framemeld.providers.speech.wav import SAMPLE_RATE, WAV_MIME, wrap_pcm16

#: Between the text adapter's 60s and the image adapter's 180s. A line of
#: dialogue is a few seconds of audio and should not need either extreme.
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

_REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"})

#: `audio/L16;codec=pcm;rate=24000`. The rate is the only part that varies
#: and the only part that matters -- a WAV header written at the wrong rate
#: plays at the wrong pitch rather than failing, which is the sort of bug
#: that reaches a user.
_RATE_IN_MIME = re.compile(r"rate=(\d+)")


class GeminiSpeechProvider:
    """Gemini's native TTS over the same generateContent endpoint the text
    and image adapters use.

    UNVERIFIED AGAINST THE LIVE API, for the same reason the image adapter
    is (see m3-decisions.md): no call has been made from this account. The
    difference is that TTS *does* have a free tier, so this one is expected
    to work once a key is configured -- but "expected to" is exactly the
    assumption M2's responseSchema incident and M3's Imagen-4 probe both
    punished. Run
    `FRAMEMELD_LIVE_SPEECH=1 pytest tests/test_providers_speech_gemini.py`
    and correct `_extract_audio` against what actually comes back before
    trusting this with a user's credits. SPEECH_PROVIDER defaults to `stub`.
    """

    name = "gemini"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set but SPEECH_PROVIDER=gemini")
        self.model = settings.speech_model
        # Injected only by tests, via httpx.MockTransport -- the same seam
        # the other three adapters use, for the same reason.
        self._transport = transport

    def _request_payload(self, text: str, voice_id: str) -> dict[str, Any]:
        return {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_id}}},
            },
        }

    @staticmethod
    def _extract_audio(body: dict[str, Any]) -> tuple[bytes, str]:
        candidates = body.get("candidates") or []
        if not candidates:
            block_reason = (body.get("promptFeedback") or {}).get("blockReason")
            if block_reason:
                raise ProviderRefusedError(
                    "The speech model declined these words. Try rewording the line."
                )
            raise ProviderResponseError("The speech model returned no audio.")

        candidate = candidates[0]
        if candidate.get("finishReason") in _REFUSAL_FINISH_REASONS:
            raise ProviderRefusedError(
                "The speech model declined these words. Try rewording the line."
            )

        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                try:
                    raw = base64.b64decode(inline["data"])
                except (ValueError, TypeError) as exc:
                    raise ProviderResponseError(
                        "The speech model returned something that is not audio."
                    ) from exc
                return raw, str(inline.get("mimeType", ""))

        raise ProviderResponseError("The speech model returned no audio.")

    @staticmethod
    def _to_wav(raw: bytes, mime: str) -> bytes:
        """Headerless PCM becomes a playable WAV; anything already
        containerised is passed through untouched.

        Gemini documents `audio/L16;codec=pcm;rate=24000`, which is raw
        samples. Wrapping unconditionally would corrupt a response that
        ever arrives as a real WAV by putting a second header on it.
        """
        if mime.startswith("audio/wav") or raw[:4] == b"RIFF":
            return raw
        match = _RATE_IN_MIME.search(mime)
        return wrap_pcm16(raw, rate=int(match.group(1)) if match else SAMPLE_RATE)

    async def speak(self, *, text: str, voice_id: str, params: dict[str, Any]) -> ProviderResult:
        url = f"{settings.gemini_base_url}/models/{self.model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                response = await client.post(
                    url,
                    json=self._request_payload(text, voice_id),
                    headers={"x-goog-api-key": settings.gemini_api_key},
                )
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error never contains request headers, so
            # the key cannot leak through here.
            raise ProviderTransportError(
                f"Could not reach the speech model: {type(exc).__name__}."
            ) from exc

        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The speech service returned {response.status_code}. This is usually temporary."
            )
        if response.status_code >= 400:
            # Deliberately without the body: this message reaches the user
            # through jobs.error.
            raise ProviderResponseError(
                f"The speech model rejected the request ({response.status_code})."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("The speech model returned an unreadable reply.") from exc

        raw, mime = self._extract_audio(body)
        return ProviderResult(
            text=None,
            bytes_=self._to_wav(raw, mime),
            # Priced per character of input, which is how TTS is billed --
            # there is no output-token count to read (M3-D6's rule, same
            # fail-soft table).
            cost_usd=usd_for_speech(self.model, len(text)),
            provider=self.name,
            model=body.get("modelVersion") or self.model,
            raw={"mimeType": WAV_MIME},
        )

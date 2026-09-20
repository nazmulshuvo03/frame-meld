from decimal import Decimal
from typing import Any

import httpx

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderResult,
    ProviderTransportError,
)
from framemeld.providers.speech.wav import WAV_MIME, wrap_pcm16

#: Matches the Gemini speech adapter. A line of dialogue is a couple of
#: seconds of audio on a GPU that is already holding the model.
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

#: Gemini voice name -> Kokoro voice id.
#:
#: voices.py predicted this table four milestones ago: "a future Kokoro
#: adapter maps them rather than the database being rewritten -- a voice id
#: is part of a character's content_hash, and migrating one would
#: invalidate every audio clip in the system." So `characters.voice_id`
#: keeps holding "Kore" whichever provider is configured, and the mapping
#: lives here.
#:
#: Kokoro happens to ship four voices under Gemini's own names (af_kore,
#: am_puck, am_fenrir, af_aoede). The other four have no counterpart and
#: are matched on the gender Gemini publishes for them, which is the only
#: property of a voice this product depends on -- a character cast as a man
#: must not be read by a woman when the provider changes.
_VOICES: dict[str, str] = {
    "Kore": "af_kore",
    "Puck": "am_puck",
    "Charon": "am_michael",
    "Zephyr": "af_heart",
    "Fenrir": "am_fenrir",
    "Aoede": "af_aoede",
    "Leda": "af_bella",
    "Orus": "am_onyx",
}

#: What an unmapped voice reads as. Kokoro grades this one A, and a voice
#: id that reached here without being in the table is a bug elsewhere --
#: refusing would fail a generation over a cosmetic mismatch.
_FALLBACK_VOICE = "af_heart"


class KokoroSpeechProvider:
    """Kokoro-82M in a local container, over its OpenAI-compatible endpoint.

    A development provider (spec section 6.3): it costs nothing, needs no
    key, and runs on the same GPU as the image provider. It exists because
    `StubSpeechProvider` derives a clip's length from `len(text) / 14.0`,
    and that guess is what every cut in the composition is timed against.
    Real words at a real length are the only way a timing bug surfaces
    before a paid Gemini run.

    VERIFIED AGAINST A LOCAL CONTAINER on 2026-09-20
    (`ghcr.io/remsky/kokoro-fastapi-gpu`), which is also where the `pcm`
    decision below came from.
    """

    name = "kokoro"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.model = settings.speech_model
        # Injected only by tests, via httpx.MockTransport -- the same seam
        # every other adapter here uses.
        self._transport = transport

    async def speak(self, *, text: str, voice_id: str, params: dict[str, Any]) -> ProviderResult:
        if not text.strip():
            # Same refusal as the stub and the Gemini adapter, for the same
            # reason: a model asked to read nothing bills for silence.
            raise ProviderRefusedError("There is nothing to say yet.")

        payload = {
            "model": "kokoro",
            "input": text,
            "voice": _VOICES.get(voice_id, _FALLBACK_VOICE),
            # `pcm`, not `wav`, and this is the whole reason this adapter is
            # not two lines shorter. Kokoro's WAV is a *streaming* WAV: its
            # RIFF and data chunk sizes are 0xFFFFFFFF because the header is
            # written before the audio exists. assets/service.py measures a
            # clip with `wave.getnframes()`, so a 1.48-second line records
            # as 4294967295 / 2 / 24000 = 89478.49 seconds -- no exception,
            # no failed job, just a scene spec claiming a day of narration.
            # Raw PCM has no header to be wrong, and `wrap_pcm16` writes a
            # correct one. Measured 2026-09-20: 1.48s, exactly right.
            "response_format": "pcm",
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                response = await client.post(
                    f"{settings.kokoro_base_url}/audio/speech", json=payload
                )
        except httpx.HTTPError as exc:
            raise ProviderTransportError(
                f"Could not reach the speech model: {type(exc).__name__}. "
                "Is the Kokoro container running?"
            ) from exc

        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The speech service returned {response.status_code}. This is usually temporary."
            )
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"The speech model rejected the request ({response.status_code})."
            )

        raw = response.content
        if not raw:
            raise ProviderResponseError("The speech model returned no audio.")

        return ProviderResult(
            text=None,
            bytes_=wrap_pcm16(raw),
            # Local inference. Zero is the true number, not a placeholder:
            # settlement subtracts this, and a development run must not
            # charge a user for electricity.
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"local": True, "mimeType": WAV_MIME, "voice": payload["voice"]},
        )

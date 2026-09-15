import hashlib
import math
import struct
from decimal import Decimal
from typing import Any

from framemeld.config import settings
from framemeld.providers.base import ProviderRefusedError, ProviderResult
from framemeld.providers.speech.wav import SAMPLE_RATE, WAV_MIME, wrap_pcm16

#: Roughly how fast a narrator reads. Only used to give the stub a
#: plausible, text-dependent length: a fixed one-second clip for every line
#: would let a broken duration path ship, since every row would agree.
_CHARS_PER_SECOND = 14.0

#: A floor and a ceiling, so a two-word line still produces something
#: audible and a pasted essay does not produce a minute of test audio.
_MIN_SECONDS = 0.4
_MAX_SECONDS = 8.0


class StubSpeechProvider:
    """A local TTS that costs nothing and never leaves the process.

    The default provider (M4-D9), so the app and the whole suite run with no
    API key and no network. It returns a *real* WAV (M4-D8) rather than a
    placeholder blob, because everything downstream treats this as audio:
    the duration reader parses it, the sha256 becomes a content_hash, and a
    browser plays it. A stub returning b"not-audio" would let all three ship
    broken and only fail against a live provider.

    Its output is derived from the text and the voice, so two shots cannot
    produce byte-identical clips and hide a broken per-shot path.
    """

    name = "stub"

    def __init__(self) -> None:
        self.model = settings.speech_model

    async def speak(self, *, text: str, voice_id: str, params: dict[str, Any]) -> ProviderResult:
        if not text.strip():
            # A real model asked to read nothing would refuse or emit
            # silence billed as a clip. Refusing keeps the failure honest
            # and gives the refund path something to exercise.
            raise ProviderRefusedError("There is nothing to say yet.")

        seconds = min(_MAX_SECONDS, max(_MIN_SECONDS, len(text.strip()) / _CHARS_PER_SECOND))
        digest = hashlib.sha256(f"{voice_id}\x00{text}".encode()).digest()
        # 110-330 Hz: a tone in roughly the range of a speaking voice, and
        # different per voice_id, so a test can hear -- and assert -- that
        # the voice actually reached the provider.
        frequency = 110 + digest[0] % 220

        frames = int(SAMPLE_RATE * seconds)
        step = 2 * math.pi * frequency / SAMPLE_RATE
        pcm = struct.pack(f"<{frames}h", *(int(12000 * math.sin(step * n)) for n in range(frames)))

        return ProviderResult(
            text=None,
            bytes_=wrap_pcm16(pcm),
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"stub": True, "mimeType": WAV_MIME},
        )

import hashlib
import math
import struct
from decimal import Decimal
from typing import Any

from framemeld.config import settings
from framemeld.providers.base import ProviderRefusedError, ProviderResult
from framemeld.providers.speech.wav import SAMPLE_RATE, WAV_MIME, wrap_pcm16

#: A ceiling on what the stub will synthesise. A 3-minute film is the
#: product's limit (spec section 1); anything longer is a caller bug, and
#: generating ten minutes of test audio would hide it behind a slow test.
_MAX_SECONDS = 200.0

#: Semitone offsets of a minor seventh chord, which is the cheapest way to
#: make three sine waves sound deliberate rather than like a fault tone.
_CHORD = (0, 3, 7, 10)


class StubMusicProvider:
    """A local composer that costs nothing and never leaves the process.

    The default and, in M5, the only music provider (M5-D2). It returns a
    *real* WAV for the same reason the speech stub does (M4-D8): the
    duration reader parses it, its sha256 becomes a content_hash, Remotion
    mixes it under the narration and a browser plays it. A placeholder blob
    would let all four ship broken.

    The pitch is derived from the prompt, so two Melds do not score to
    byte-identical audio and hide a broken per-Meld path.
    """

    name = "stub"

    def __init__(self) -> None:
        self.model = settings.music_model

    async def compose(
        self, *, prompt: str, duration_sec: float, params: dict[str, Any]
    ) -> ProviderResult:
        if duration_sec <= 0:
            # A real model asked for zero seconds would refuse or bill for
            # nothing. Refusing keeps the failure honest and exercises the
            # refund path.
            raise ProviderRefusedError("There is no film to score yet.")

        seconds = min(_MAX_SECONDS, duration_sec)
        root = 110 + hashlib.sha256(prompt.encode()).digest()[0] % 60
        frames = int(SAMPLE_RATE * seconds)
        # A fixed fade at each end, so the track does not start and stop on
        # a click -- which is audible, and which a listener reads as a bug
        # in the film rather than in the stub.
        fade = min(frames // 4, SAMPLE_RATE // 2) or 1

        steps = [2 * math.pi * (root * 2 ** (semitone / 12)) / SAMPLE_RATE for semitone in _CHORD]
        samples = []
        for n in range(frames):
            envelope = min(1.0, n / fade, (frames - n) / fade)
            value = sum(math.sin(step * n) for step in steps) / len(steps)
            samples.append(int(6000 * envelope * value))

        return ProviderResult(
            text=None,
            bytes_=wrap_pcm16(struct.pack(f"<{frames}h", *samples)),
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"stub": True, "mimeType": WAV_MIME, "duration_sec": seconds},
        )

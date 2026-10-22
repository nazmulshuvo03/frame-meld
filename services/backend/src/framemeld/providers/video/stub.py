import asyncio
import hashlib
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderResult,
)

VIDEO_MIME = "video/mp4"

#: The frame rate and size the Preview already renders at. Matching them
#: means a Final render is not silently upscaling every clip, and means a
#: clip and a still of the same shot are directly comparable.
FPS = 30
SIZE = "1280x720"

#: Spec section 8: every current image-to-video model caps a clip at about
#: five seconds, and `shots.duration_sec` has a DB constraint to match. The
#: stub refuses beyond it rather than quietly encoding a minute, so a caller
#: that lost the constraint finds out here instead of in a Final render.
MAX_SECONDS = 5.0


class StubVideoProvider:
    """A local image-to-video model that costs nothing and animates nothing.

    The default and, in M6, the only video provider (M6-D9). It emits a
    *real* MP4 (M6-D8) rather than a placeholder blob, because five separate
    things downstream treat these bytes as video: the asset row, the signed
    URL, Remotion's decoder, the muxer that writes the Final render, and the
    browser that plays it. A blob would let all five ship broken, and the
    symptom would be a black film with every job reporting success.

    What it does *not* do is animate the still. It encodes a flat colour
    derived from the still's own bytes, so two shots never produce identical
    video and a broken per-shot path cannot hide -- but nobody should mistake
    the output for a model's. The point is that everything around the model
    is real.
    """

    name = "stub"

    def __init__(self) -> None:
        self.model = settings.video_model

    @staticmethod
    def _ffmpeg() -> str:
        """The bundled encoder, looked up on use rather than at import.

        `imageio-ffmpeg` is in the dev extra (M6-D8): it exists so the stub
        can be honest, and the day a real VideoProvider is configured it
        should not be a deployment requirement. Importing it lazily is what
        keeps that true, and the error says exactly what to install.
        """
        try:
            import imageio_ffmpeg
        except ImportError as exc:  # pragma: no cover - a setup error, not a code path
            raise ProviderResponseError(
                "The stub video provider needs ffmpeg. Install the dev extra: "
                'pip install -e ".[dev]"'
            ) from exc
        return imageio_ffmpeg.get_ffmpeg_exe()

    def _encode(self, colour: str, duration_sec: float) -> bytes:
        # To a file rather than to stdout: a normal MP4 needs a seekable
        # output to write its index at the end, and the fragmented variant
        # stdout forces is a format some decoders handle worse for no gain
        # here.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            result = subprocess.run(
                [
                    self._ffmpeg(),
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={colour}:s={SIZE}:r={FPS}:d={duration_sec:.2f}",
                    "-c:v",
                    "libx264",
                    # ultrafast because nobody is judging this picture, and
                    # a suite that encodes a dozen of these should not spend
                    # a minute doing it.
                    "-preset",
                    "ultrafast",
                    # yuv420p, not the encoder's default: it is the only
                    # pixel format every browser will play.
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0 or not output.exists():
                raise ProviderResponseError(
                    "The stub video provider could not encode a clip: "
                    + result.stderr.decode("utf-8", "replace")[:200]
                )
            return output.read_bytes()

    async def animate(
        self,
        *,
        image: bytes | None,
        motion_prompt: str,
        duration_sec: float,
        params: dict[str, Any],
    ) -> ProviderResult:
        if image is not None and not image:
            # A real model handed no image would refuse or bill for a
            # generation from nothing. Refusing keeps the failure honest and
            # exercises the refund path.
            raise ProviderRefusedError("There is no still to animate.")
        if duration_sec <= 0 or duration_sec > MAX_SECONDS:
            raise ProviderRefusedError(
                f"A clip has to be between 0 and {MAX_SECONDS:g} seconds long."
            )

        # From the image's bytes *and* the motion prompt, because a real
        # image-to-video model reads both: two shots sharing a prompt and
        # holding different pictures must differ, and so must the same
        # picture given different instructions. Keying on only one of them
        # would make the other look like it had no effect -- and the
        # staleness engine, which is downstream of these bytes, would then
        # be silently wrong about work the user had paid to redo.
        digest = hashlib.sha256((image or b"") + b"\x00" + motion_prompt.encode()).hexdigest()[:6]

        # to_thread because this runs inside the arq worker's event loop and
        # ffmpeg is a blocking subprocess -- the same reason LocalDiskStorage
        # writes off-thread.
        data = await asyncio.to_thread(self._encode, f"0x{digest}", duration_sec)

        return ProviderResult(
            text=None,
            bytes_=data,
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"stub": True, "mimeType": VIDEO_MIME, "duration_sec": duration_sec},
        )

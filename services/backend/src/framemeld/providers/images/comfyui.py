import asyncio
import hashlib
import time
from decimal import Decimal
from typing import Any

import httpx

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderResponseError,
    ProviderResult,
    ProviderTransportError,
)

#: Per HTTP call, not per generation. Every request this adapter makes is
#: small and immediate -- queueing a prompt, reading history, fetching a
#: PNG. Waiting for the picture is the poll loop's job, below.
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

#: How long to wait for one image before giving up.
#:
#: Sized by the cold start, not the steady state. A warm ComfyUI draws a
#: 1024x1024 still in 2.18s, but the first prompt after the container
#: starts loads a 6.9GB checkpoint from disk: measured at 107.44s on
#: 2026-09-20, of which sampling was 1.28s. A budget tight enough to be
#: useful for warm runs would fail every first run of the day.
_GENERATION_BUDGET_SEC = 300.0

_POLL_INTERVAL_SEC = 0.5

#: SDXL-Turbo is a distilled model: it draws in a single step at cfg 1.0,
#: and ComfyUI's ordinary defaults (20 steps, cfg 8.0) produce a burned,
#: over-saturated mess on it. These are properties of the checkpoint rather
#: than of a request, which is why they are constants here and not in
#: `params` -- SHEET_PARAMS and STILL_PARAMS carry `size` and a prompt
#: version, and adding sampler settings to them would change the content
#: hash of every sheet and still in the database.
_STEPS = 1
_CFG = 1.0
_SAMPLER = "euler"
_SCHEDULER = "normal"

#: The node ids are arbitrary but fixed: `_extract_filename` reads outputs
#: by the SaveImage node's id, so the two have to agree.
_SAVE_NODE = "9"


def _size(params: dict[str, Any]) -> tuple[int, int]:
    """`{"size": "1024x1024"}` as ComfyUI wants it.

    SHEET_PARAMS and STILL_PARAMS both carry this string because that is
    the shape Gemini's API takes. Parsed rather than assumed so that
    changing one of those constants changes what is drawn, instead of
    silently drawing the old size at the new hash.
    """
    raw = str(params.get("size") or "1024x1024")
    try:
        width, height = (int(part) for part in raw.lower().split("x", 1))
    except ValueError as exc:
        raise ProviderResponseError(f"{raw!r} is not a usable image size.") from exc
    if width <= 0 or height <= 0:
        raise ProviderResponseError(f"{raw!r} is not a usable image size.")
    return width, height


class ComfyUIImageProvider:
    """A local SDXL checkpoint through ComfyUI's HTTP API.

    A development provider (spec section 6.3), and the one that makes the
    Preview worth looking at: with `StubImageProvider` every still is a
    64-pixel flat colour, so a Preview render proves the pipeline works and
    shows nothing a person can judge. Real stills at 2.18s each turn the
    free Preview into an actual film.

    What it deliberately does *not* do is honour `refs`. Those carry each
    character's approved sheet, and matching a face across shots needs
    IP-Adapter nodes and a much larger workflow. Character consistency is a
    question about the *production* provider; ignoring refs here keeps the
    development loop to one container and one checkpoint, and nothing
    downstream reads a still expecting a particular face.

    VERIFIED AGAINST A LOCAL CONTAINER on 2026-09-20
    (`ghcr.io/jemeyer/comfyui`, SDXL-Turbo, RTX 3060 12GB).
    """

    name = "comfyui"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.model = settings.comfyui_checkpoint
        # Injected only by tests, via httpx.MockTransport -- the same seam
        # every other adapter here uses.
        self._transport = transport

    def _workflow(self, prompt: str, params: dict[str, Any]) -> dict[str, Any]:
        width, height = _size(params)
        # Derived from the prompt rather than random, for the same reason
        # voice_for_index is deterministic: an unchanged prompt has to give
        # an unchanged picture, or the staleness engine sees work to redo on
        # every pass. A real model is not reproducible either, but a
        # development provider that is makes a cache hit visible.
        seed = int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:6], "big")
        return {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.model},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": _STEPS,
                    "cfg": _CFG,
                    "sampler_name": _SAMPLER,
                    "scheduler": _SCHEDULER,
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            _SAVE_NODE: {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "framemeld", "images": ["8", 0]},
            },
        }

    @staticmethod
    def _check(response: httpx.Response, what: str) -> None:
        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The image service returned {response.status_code} {what}. "
                "This is usually temporary."
            )
        if response.status_code >= 400:
            # ComfyUI puts the useful part in `node_errors`, and unlike a
            # hosted provider's body it holds no credentials -- it is our
            # own workflow read back. Including it is the difference between
            # "400" and "CheckpointLoaderSimple: value not in list".
            raise ProviderResponseError(
                f"The image model rejected the workflow ({response.status_code}): "
                f"{response.text[:300]}"
            )

    async def generate(
        self, *, prompt: str, refs: list[bytes], params: dict[str, Any]
    ) -> ProviderResult:
        base = settings.comfyui_base_url.rstrip("/")
        workflow = self._workflow(prompt, params)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                queued = await client.post(f"{base}/prompt", json={"prompt": workflow})
                self._check(queued, "queueing a prompt")
                prompt_id = (queued.json() or {}).get("prompt_id")
                if not prompt_id:
                    raise ProviderResponseError("The image model did not accept the workflow.")

                entry = await self._await_result(client, base, str(prompt_id))
                image = self._extract_image(entry)
                view = await client.get(f"{base}/view", params=image)
                self._check(view, "fetching an image")
                data = view.content
        except httpx.HTTPError as exc:
            raise ProviderTransportError(
                f"Could not reach the image model: {type(exc).__name__}. "
                "Is the ComfyUI container running?"
            ) from exc

        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ProviderResponseError("The image model returned something that is not a PNG.")

        return ProviderResult(
            text=None,
            bytes_=data,
            # Local inference, so zero is the true number and not a
            # placeholder -- settlement subtracts this.
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"local": True, "mimeType": "image/png", "prompt_id": prompt_id},
        )

    async def _await_result(
        self, client: httpx.AsyncClient, base: str, prompt_id: str
    ) -> dict[str, Any]:
        """Poll `/history` until the prompt is done, or the budget runs out.

        ComfyUI's queue is asynchronous and it answers `/history/{id}` with
        an empty object until the prompt has finished, so "not there yet"
        and "never existed" look identical. That is why this loop ends in a
        timeout rather than a not-found: after the budget, the honest thing
        to say is that it did not finish.
        """
        deadline = time.monotonic() + _GENERATION_BUDGET_SEC
        while time.monotonic() < deadline:
            history = await client.get(f"{base}/history/{prompt_id}")
            self._check(history, "reading history")
            entry = (history.json() or {}).get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ProviderResponseError(
                        f"The image model failed to draw this: {status.get('messages')}"
                    )
                return entry
            await asyncio.sleep(_POLL_INTERVAL_SEC)

        raise ProviderTransportError(
            f"The image model did not finish within {_GENERATION_BUDGET_SEC:g} seconds."
        )

    @staticmethod
    def _extract_image(entry: dict[str, Any]) -> dict[str, str]:
        """The `/view` query for the first image the SaveImage node wrote."""
        images = ((entry.get("outputs") or {}).get(_SAVE_NODE) or {}).get("images") or []
        if not images:
            raise ProviderResponseError("The image model returned no image.")
        first = images[0]
        return {
            "filename": str(first.get("filename") or ""),
            "subfolder": str(first.get("subfolder") or ""),
            "type": str(first.get("type") or "output"),
        }

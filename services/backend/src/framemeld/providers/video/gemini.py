import asyncio
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
from framemeld.providers.rates import usd_for_video

#: Each individual HTTP call here is small -- a submit, a poll, an operation
#: read. The *generation* is bounded by _MAX_WAIT_SECONDS below, not by this.
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

#: Downloading the finished clip is the one call that moves real bytes: a
#: 6-second 720p clip came back at 3.8 MB.
_DOWNLOAD_TIMEOUT = httpx.Timeout(300.0, connect=10.0)

#: How long to wait for one clip before giving up. A live 6-second
#: generation took a little over a minute; this is generous against that and
#: still comfortably inside the worker's own job_timeout, which is
#: RENDER_TIMEOUT_SECONDS + 120.
_MAX_WAIT_SECONDS = 600.0

#: Veo is a long-running operation, and polling it is cheap. Ten seconds is
#: slow enough not to busy-wait on a model that takes minutes.
_POLL_INTERVAL_SECONDS = 10.0

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})

#: **The duration ladder, and it is not what the error message says.**
#:
#: Veo 3.1 Lite answers a request for 5 seconds with "provide a value
#: between 4 and 8, inclusive" -- and then also rejects 7. Probed one value
#: at a time on 2026-09-19: 3 refused, 4 accepted, 5 refused, 6 accepted,
#: 7 refused, 8 accepted, 9 refused. Only even values in [4, 8] are legal,
#: and the error text is actively misleading about it.
#:
#: This collides with spec section 8, which caps a shot at 5.0 seconds
#: because every image-to-video model caps a clip at about five. Both are
#: true, and together they leave exactly two usable tiers for this product:
#: 4 seconds for a shot of 4.0 or less, and 6 for anything above it.
_DURATION_TIERS = (4, 6, 8)


def duration_tier(duration_sec: float) -> int:
    """The shortest legal Veo duration that is not shorter than the shot.

    Rounding **up** rather than to nearest is the load-bearing choice, and
    it is what keeps the Final render's cuts clean. The composition gives
    each shot exactly `duration_sec` worth of frames and truncates whatever
    the clip has beyond that, so a clip longer than its slot costs bytes and
    nothing else -- while a clip *shorter* than its slot leaves a black
    frame at the cut, which is the failure m6-decisions.md predicted as the
    most likely first bug against a real provider. Up is the direction that
    cannot produce it.
    """
    for tier in _DURATION_TIERS:
        if duration_sec <= tier:
            return tier
    raise ProviderRefusedError(
        f"A clip can be at most {_DURATION_TIERS[-1]} seconds; this shot asks for {duration_sec:g}."
    )


class VeoVideoProvider:
    """Google's Veo image-to-video, over the long-running `predictLongRunning`
    endpoint rather than the `generateContent` every other adapter here uses.

    Three things about this API differ from the rest of the Gemini surface,
    all confirmed by probe on 2026-09-19 before a line of this was written
    (M3-D11's rule):

    1. **It is asynchronous.** The submit returns an operation name; the
       clip exists only after that operation reports `done`. Both steps
       happen inside one worker job, the same way the render client already
       waits on the render service (M5-D10).
    2. **The clip comes back as a URI, not inline.** It is fetched from the
       files endpoint with the same API key, and only then are there bytes.
    3. **It always has an audio track, and cannot be asked not to.**
       `generateAudio: false` is refused outright by this model. Spec
       section 8 assumed silent clips ("no audio in Wan 2.2 output") and
       built the whole audio design on laying narration, dialogue and music
       over silence. The film's audio therefore comes from the audio
       timeline and the clip is muted in the composition -- that is an
       invariant of the design rather than a workaround for this provider,
       so it lives in `Film.tsx` where it holds for any future one.

    Resolution is not requested either: 1080p is refused at the 4-second
    tier, and 720p is what the Preview already renders at and what the stub
    already emits, so the default is the thing that was wanted anyway.
    """

    name = "veo"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set but VIDEO_PROVIDER=veo")
        self.model = settings.video_model
        # Injected only by tests, via httpx.MockTransport -- the same seam
        # every other adapter here uses, for the same reason.
        self._transport = transport

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            transport=self._transport,
            headers={"x-goog-api-key": settings.gemini_api_key},
        )

    @staticmethod
    def _request_payload(image: bytes, motion_prompt: str, seconds: int) -> dict[str, Any]:
        return {
            "instances": [
                {
                    "prompt": motion_prompt,
                    "image": {
                        "bytesBase64Encoded": base64.b64encode(image).decode(),
                        "mimeType": "image/png",
                    },
                }
            ],
            # durationSeconds only. aspectRatio is deliberately absent: a
            # probe with no parameters answered 1280x720 from a 16:9 still,
            # so the default is already right, and a parameter that was
            # never confirmed to work is a parameter that fails on somebody
            # else's shot.
            "parameters": {"durationSeconds": seconds},
        }

    @staticmethod
    def _raise_for_status(response: httpx.Response, what: str) -> None:
        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise ProviderTransportError(
                f"The video service returned {response.status_code} {what}. "
                "This is usually temporary."
            )
        if response.status_code >= 400:
            # Deliberately without the body: this message reaches the user
            # through jobs.error.
            raise ProviderResponseError(
                f"The video model rejected the request ({response.status_code})."
            )

    async def _submit(self, image: bytes, motion_prompt: str, seconds: int) -> str:
        url = f"{settings.gemini_base_url}/models/{self.model}:predictLongRunning"
        try:
            async with self._client(_TIMEOUT) as client:
                response = await client.post(
                    url, json=self._request_payload(image, motion_prompt, seconds)
                )
        except httpx.HTTPError as exc:
            # str(exc) on an httpx error never contains request headers, so
            # the key cannot leak through here.
            raise ProviderTransportError(
                f"Could not reach the video model: {type(exc).__name__}."
            ) from exc

        self._raise_for_status(response, "starting the clip")

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("The video model returned an unreadable reply.") from exc

        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ProviderResponseError("The video model did not say what it started.")
        return name

    async def _await_uri(self, operation: str) -> str:
        deadline = asyncio.get_running_loop().time() + _MAX_WAIT_SECONDS
        url = f"{settings.gemini_base_url}/{operation}"

        while True:
            try:
                async with self._client(_TIMEOUT) as client:
                    response = await client.get(url)
            except httpx.HTTPError as exc:
                raise ProviderTransportError(
                    f"The video model stopped answering: {type(exc).__name__}."
                ) from exc

            self._raise_for_status(response, "while animating")

            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderResponseError(
                    "The video model returned an unreadable reply."
                ) from exc

            if body.get("done"):
                return self._uri_from(body)

            if asyncio.get_running_loop().time() >= deadline:
                # Retryable: a generation that ran long may simply have been
                # queued. The job's own attempt budget stops this repeating
                # forever, and the hold is refunded if it never lands.
                raise ProviderTransportError("The video model took too long.")

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    @staticmethod
    def _uri_from(body: dict[str, Any]) -> str:
        error = body.get("error")
        if error:
            # A finished operation carrying an error is that clip's problem,
            # not a transport one: retrying sends the identical request.
            raise ProviderResponseError(
                str(error.get("message") or "The video model could not animate this shot.")[:300]
            )

        response = body.get("response") or {}
        generated = response.get("generateVideoResponse") or {}
        samples = generated.get("generatedSamples") or []
        if not samples:
            # Veo reports a safety block by finishing with no samples rather
            # than by erroring, so this is the refusal path -- terminal, and
            # refunded in full rather than retried.
            reasons = generated.get("raiMediaFilteredReasons") or []
            raise ProviderRefusedError(
                "The video model declined this shot. "
                + (str(reasons[0])[:200] if reasons else "Try rewording the motion description.")
            )

        uri = (samples[0].get("video") or {}).get("uri")
        if not isinstance(uri, str) or not uri:
            raise ProviderResponseError("The video model finished with nothing to download.")
        return uri

    async def _download(self, uri: str) -> bytes:
        try:
            # follow_redirects: the files endpoint answers with a redirect to
            # storage, and the key travels on the first hop only.
            async with self._client(_DOWNLOAD_TIMEOUT) as client:
                response = await client.get(uri, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise ProviderTransportError(
                f"The finished clip could not be downloaded: {type(exc).__name__}."
            ) from exc

        self._raise_for_status(response, "downloading the clip")
        if not response.content:
            raise ProviderResponseError("The video model returned an empty clip.")
        return response.content

    async def animate(
        self, *, image: bytes, motion_prompt: str, duration_sec: float, params: dict[str, Any]
    ) -> ProviderResult:
        if not image:
            # Matches the stub's refusal: a real model handed no image would
            # refuse or bill for a generation from nothing, and refusing
            # keeps the failure honest and exercises the refund path.
            raise ProviderRefusedError("There is no still to animate.")
        if duration_sec <= 0:
            raise ProviderRefusedError("A clip has to be longer than zero seconds.")

        seconds = duration_tier(duration_sec)
        operation = await self._submit(image, motion_prompt, seconds)
        uri = await self._await_uri(operation)
        data = await self._download(uri)

        return ProviderResult(
            text=None,
            bytes_=data,
            # Billed on the tier generated, not on the shot's own length: a
            # 4.2-second shot is charged as 6 seconds because 6 seconds is
            # what Veo made. Costing this on duration_sec would under-report
            # the only call in this product expensive enough to matter.
            cost_usd=usd_for_video(self.model, seconds),
            provider=self.name,
            model=self.model,
            raw={"mimeType": "video/mp4", "requested_sec": seconds, "shot_sec": duration_sec},
        )

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

from framemeld.config import settings
from framemeld.providers.base import ProviderResponseError, ProviderTransportError

#: How often the worker asks the render service whether it is done (M5-D10).
#: A preview takes seconds; two seconds of latency on the end of it is
#: invisible, and a tighter loop would be a busy-wait against a service
#: that is doing real work.
POLL_INTERVAL_SECONDS = 2.0

#: Short, because every one of these is a small JSON call to a service on
#: the same network. The *render* is bounded by settings.render_timeout_
#: seconds, not by this.
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)

#: Downloading the finished MP4 is the one call that moves real bytes.
_DOWNLOAD_TIMEOUT = httpx.Timeout(300.0, connect=10.0)

_TERMINAL = frozenset({"done", "failed"})

logger = logging.getLogger(__name__)


def _percent(progress: object) -> int | None:
    """The render service's 0..1 fraction as whole percent, or None.

    Clamped rather than trusted: this number is written to a column and
    shown to a user, and a service reporting 1.4 or -0 should move a bar to
    100 or 0 rather than put a nonsense value in the database.
    """
    if not isinstance(progress, (int, float)) or isinstance(progress, bool):
        return None
    return max(0, min(100, round(float(progress) * 100)))


#: Called with whole percent, 0-100, each time the render service reports a
#: figure that differs from the last one. Async because the only caller
#: writes it to the database.
ProgressCallback = Callable[[int], Awaitable[None]]


class RenderClient(Protocol):
    """Spec section 3.1's two endpoints, as something the worker can be
    handed a fake of."""

    async def render(self, spec: dict, on_progress: ProgressCallback | None = None) -> bytes: ...


class HttpRenderClient:
    """The real client: submit, poll, download.

    All three steps happen inside one arq job (M5-D10). The render service
    is asynchronous because a render takes minutes and an HTTP request that
    long is a request that dies to somebody's idle timeout -- not because
    anything here wants a second state machine.
    """

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._base = settings.render_service_url.rstrip("/")
        # Injected only by tests, via httpx.MockTransport -- the same seam
        # every provider adapter uses.
        self._transport = transport

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def render(self, spec: dict, on_progress: ProgressCallback | None = None) -> bytes:
        """The finished MP4's bytes, or a ProviderError explaining why not.

        Raises the provider error types rather than its own: the worker
        already routes those into retry-or-fail and refund, and a fourth
        exception hierarchy meaning the same three things would be a fourth
        place to get the retry verdict wrong. A render service that is down
        is a transport failure and retryable; a render that failed is that
        film's problem and is not.
        """
        render_id = await self._submit(spec)
        output_url = await self._await_output(render_id, on_progress)
        return await self._download(output_url)

    async def _submit(self, spec: dict) -> str:
        try:
            async with self._client(_TIMEOUT) as client:
                response = await client.post(f"{self._base}/render", json=spec)
        except httpx.HTTPError as exc:
            raise ProviderTransportError("The render service did not answer.") from exc

        if response.status_code >= 500:
            raise ProviderTransportError("The render service is not well right now.")
        if response.status_code >= 400:
            raise ProviderResponseError("The render service refused this film.")

        render_id = (response.json() or {}).get("render_id")
        if not isinstance(render_id, str) or not render_id:
            raise ProviderResponseError("The render service did not say what it started.")
        return render_id

    async def _await_output(
        self, render_id: str, on_progress: ProgressCallback | None = None
    ) -> str:
        deadline = asyncio.get_running_loop().time() + settings.render_timeout_seconds
        # The last figure actually reported, so a render that sits at 4% for
        # a minute writes one row rather than thirty identical ones.
        last_reported: int | None = None

        while True:
            try:
                async with self._client(_TIMEOUT) as client:
                    response = await client.get(f"{self._base}/render/{render_id}")
            except httpx.HTTPError as exc:
                raise ProviderTransportError("The render service stopped answering.") from exc

            if response.status_code >= 500:
                raise ProviderTransportError("The render service is not well right now.")
            if response.status_code >= 400:
                raise ProviderResponseError("The render service lost track of this film.")

            body = response.json() or {}
            status = str(body.get("status", ""))

            if on_progress is not None:
                percent = _percent(body.get("progress"))
                if percent is not None and percent != last_reported:
                    last_reported = percent
                    # Never let reporting progress fail a render. This is a
                    # number on a button; the film is the job.
                    try:
                        await on_progress(percent)
                    except Exception:  # pragma: no cover - defensive
                        logger.warning("could not record render progress", exc_info=True)

            if status == "done":
                output_url = body.get("output_url")
                if not isinstance(output_url, str) or not output_url:
                    raise ProviderResponseError("The render finished with nothing to download.")
                return output_url
            if status == "failed":
                # Terminal. The message comes from our own service, so it
                # is safe to show a user -- but it is truncated, because
                # `jobs.error` is displayed and a stack trace is not an
                # explanation.
                raise ProviderResponseError(str(body.get("error") or "The render failed.")[:300])
            if status not in _TERMINAL and not status:
                raise ProviderResponseError("The render service reported no status.")

            if asyncio.get_running_loop().time() >= deadline:
                # Retryable: a render that ran out of time may simply have
                # been queued behind another one. The job's own attempt
                # budget is what stops this repeating forever.
                raise ProviderTransportError("The render took too long.")

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _download(self, output_url: str) -> bytes:
        try:
            async with self._client(_DOWNLOAD_TIMEOUT) as client:
                response = await client.get(output_url)
        except httpx.HTTPError as exc:
            raise ProviderTransportError("The finished film could not be downloaded.") from exc

        if response.status_code >= 500:
            raise ProviderTransportError("The finished film could not be downloaded.")
        if response.status_code >= 400:
            raise ProviderResponseError("The finished film is no longer there.")
        if not response.content:
            raise ProviderResponseError("The render service returned an empty file.")
        return response.content


def get_render_client() -> RenderClient:
    """One place a config name becomes an implementation, like every other
    registry here. There is only one implementation, so this is a seam for
    the worker rather than a choice."""
    return HttpRenderClient()

import base64
import json
import os
import struct
from decimal import Decimal

import httpx
import pytest

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.providers.images.stub import solid_png
from framemeld.providers.video.gemini import VeoVideoProvider, duration_tier

_KEY = "test-video-key"
_MODEL = "veo-3.1-lite-generate-preview"
_OPERATION = f"models/{_MODEL}/operations/abc123"
_URI = "https://generativelanguage.googleapis.com/v1beta/files/xyz:download?alt=media"
_CLIP = b"\x00\x00\x00\x18ftypmp42fake-mp4-payload"
_STILL = solid_png(16, 9, (10, 20, 30))


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", _KEY)
    monkeypatch.setattr(settings, "video_model", _MODEL)
    return VeoVideoProvider(transport=httpx.MockTransport(handler))


def _done_body(uri: str = _URI) -> dict:
    return {
        "name": _OPERATION,
        "done": True,
        "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": uri}}]}},
    }


def _happy(polls_before_done: int = 0):
    """A transport that walks the whole three-step dance: submit, poll until
    done, then serve the bytes from the files endpoint."""
    state = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url.endswith(":predictLongRunning"):
            return httpx.Response(200, json={"name": _OPERATION})
        if url.startswith(_URI.split("?")[0]):
            return httpx.Response(200, content=_CLIP)
        state["polls"] += 1
        if state["polls"] > polls_before_done:
            return httpx.Response(200, json=_done_body())
        return httpx.Response(200, json={"name": _OPERATION, "done": False})

    return handler


# --- the duration ladder ----------------------------------------------------


def test_only_even_tiers_are_legal():
    """Probed live on 2026-09-19, one value at a time: Veo 3.1 Lite accepts
    4, 6 and 8 and refuses 3, 5, 7 and 9 -- while its own error text claims
    the range is "between 4 and 8, inclusive"."""
    assert duration_tier(1.0) == 4
    assert duration_tier(4.0) == 4
    assert duration_tier(4.2) == 6
    assert duration_tier(5.0) == 6
    assert duration_tier(6.0) == 6
    assert duration_tier(7.5) == 8


def test_the_tier_is_never_shorter_than_the_shot():
    """The property that keeps the cuts clean. The composition gives a shot
    exactly its own duration in frames and truncates the rest, so a clip
    longer than its slot is free and a clip shorter than its slot is a black
    frame at the cut -- the failure m6-decisions.md predicted."""
    shot = 0.1
    while shot <= 8.0:
        assert duration_tier(shot) >= shot
        shot = round(shot + 0.1, 2)


def test_a_shot_longer_than_the_top_tier_is_refused():
    with pytest.raises(ProviderRefusedError):
        duration_tier(8.5)


# --- the call ---------------------------------------------------------------


async def test_a_successful_call_walks_submit_poll_download(monkeypatch):
    provider = _provider(_happy(polls_before_done=2), monkeypatch)

    result = await provider.animate(
        image=_STILL, motion_prompt="slow push in", duration_sec=3.5, params={}
    )

    assert result.bytes_ == _CLIP
    assert result.provider == "veo"
    assert result.raw["requested_sec"] == 4
    assert result.raw["shot_sec"] == 3.5


async def test_the_cost_is_billed_on_the_tier_not_the_shot(monkeypatch):
    """A 4.2-second shot is generated as 6 seconds, so it costs 6 seconds.
    Costing it on the shot's own length would under-report the only call in
    this product expensive enough to matter."""
    provider = _provider(_happy(), monkeypatch)

    short = await provider.animate(image=_STILL, motion_prompt="x", duration_sec=4.0, params={})
    over = await provider.animate(image=_STILL, motion_prompt="x", duration_sec=4.2, params={})

    assert short.cost_usd == Decimal("0.05") * 4
    assert over.cost_usd == Decimal("0.05") * 6


async def test_the_submit_carries_the_still_and_the_duration(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url.endswith(":predictLongRunning"):
            seen["key"] = request.headers.get("x-goog-api-key")
            seen["url"] = url
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"name": _OPERATION})
        if url.startswith(_URI.split("?")[0]):
            return httpx.Response(200, content=_CLIP)
        return httpx.Response(200, json=_done_body())

    await _provider(handler, monkeypatch).animate(
        image=_STILL, motion_prompt="drift left", duration_sec=5.0, params={}
    )

    instance = seen["body"]["instances"][0]
    assert instance["prompt"] == "drift left"
    assert base64.b64decode(instance["image"]["bytesBase64Encoded"]) == _STILL
    assert seen["body"]["parameters"] == {"durationSeconds": 6}
    assert seen["key"] == _KEY
    assert _KEY not in seen["url"]


async def test_no_still_is_refused_before_any_call(monkeypatch):
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"name": _OPERATION})

    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderRefusedError):
        await provider.animate(image=b"", motion_prompt="x", duration_sec=4.0, params={})
    assert called is False


async def test_an_operation_that_finishes_with_no_samples_is_a_refusal(monkeypatch):
    """Veo reports a safety block by finishing `done` with an empty sample
    list rather than by erroring. Treating that as a response error would
    retry a request that will be refused identically every time."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": _OPERATION})
        return httpx.Response(
            200,
            json={
                "name": _OPERATION,
                "done": True,
                "response": {
                    "generateVideoResponse": {
                        "generatedSamples": [],
                        "raiMediaFilteredReasons": ["blocked by policy"],
                    }
                },
            },
        )

    with pytest.raises(ProviderRefusedError) as caught:
        await _provider(handler, monkeypatch).animate(
            image=_STILL, motion_prompt="x", duration_sec=4.0, params={}
        )
    assert "blocked by policy" in str(caught.value)


async def test_an_operation_error_is_terminal(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": _OPERATION})
        return httpx.Response(
            200, json={"name": _OPERATION, "done": True, "error": {"message": "bad shot"}}
        )

    with pytest.raises(ProviderResponseError):
        await _provider(handler, monkeypatch).animate(
            image=_STILL, motion_prompt="x", duration_sec=4.0, params={}
        )


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_statuses_on_submit_are_transport_errors(monkeypatch, status):
    provider = _provider(lambda request: httpx.Response(status, json={}), monkeypatch)
    with pytest.raises(ProviderTransportError):
        await provider.animate(image=_STILL, motion_prompt="x", duration_sec=4.0, params={})


async def test_a_400_on_submit_is_terminal(monkeypatch):
    provider = _provider(lambda request: httpx.Response(400, json={}), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.animate(image=_STILL, motion_prompt="x", duration_sec=4.0, params={})


async def test_a_submit_with_no_operation_name_is_an_error(monkeypatch):
    provider = _provider(lambda request: httpx.Response(200, json={}), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.animate(image=_STILL, motion_prompt="x", duration_sec=4.0, params={})


async def test_an_empty_download_is_an_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"name": _OPERATION})
        if url.startswith(_URI.split("?")[0]):
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json=_done_body())

    with pytest.raises(ProviderResponseError):
        await _provider(handler, monkeypatch).animate(
            image=_STILL, motion_prompt="x", duration_sec=4.0, params={}
        )


async def test_no_error_path_quotes_the_api_key(monkeypatch):
    provider = _provider(lambda request: httpx.Response(400, json={"error": _KEY}), monkeypatch)
    with pytest.raises(ProviderResponseError) as caught:
        await provider.animate(image=_STILL, motion_prompt="x", duration_sec=4.0, params={})
    assert _KEY not in str(caught.value)


async def test_a_missing_key_refuses_to_construct(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(ValueError):
        VeoVideoProvider()


def mp4_duration(data: bytes) -> float | None:
    """Seconds, read straight out of the MP4 `mvhd` box.

    Twelve lines of struct rather than a subprocess, for the same reason
    `wav_duration` uses stdlib `wave`: the only thing needed here is
    duration over timescale, and shelling out would put ffprobe on the
    machine's PATH into the definition of whether a test can run. Returns
    None on anything unparseable, so a caller asserts on a number or on
    None rather than on an exception.
    """
    index = data.find(b"mvhd")
    if index < 0:
        return None
    body = data[index + 4 :]
    try:
        version = body[0]
        if version == 1:
            timescale, duration = struct.unpack(">IQ", body[20:32])
        else:
            timescale, duration = struct.unpack(">II", body[12:20])
    except (struct.error, IndexError):
        return None
    return duration / timescale if timescale else None


@pytest.mark.skipif(
    not os.getenv("FRAMEMELD_LIVE_VIDEO"),
    reason="opt-in: calls the real Veo endpoint (this spends real money, ~$0.20 a clip)",
)
async def test_live_video(monkeypatch):
    """The probe M6-D9 could not run, because the key had no billing.

    Asserts the three facts the rest of the system depends on: the clip is a
    real MP4, it is *at least* as long as the shot asked for (short is the
    direction that leaves a black frame at the cut), and it is charged at
    the tier that was actually generated.
    """
    monkeypatch.setattr(settings, "video_provider", "veo")
    monkeypatch.setattr(settings, "video_model", _MODEL)

    result = await VeoVideoProvider().animate(
        image=solid_png(1280, 720, (90, 120, 160)),
        motion_prompt="slow push in, gentle drift",
        duration_sec=3.5,
        params={},
    )

    assert result.bytes_ and len(result.bytes_) > 100_000
    assert result.cost_usd == Decimal("0.05") * 4

    seconds = mp4_duration(result.bytes_)
    assert seconds is not None, "the clip is not a readable MP4"
    assert seconds >= 3.5


def test_a_request_with_no_image_sends_only_the_prompt():
    """A clip made from the script alone: the prompt carries the whole frame."""
    payload = VeoVideoProvider._request_payload(None, "a quiet harbour at dawn", 4)
    assert payload["instances"] == [{"prompt": "a quiet harbour at dawn"}]
    assert payload["parameters"] == {"durationSeconds": 4}

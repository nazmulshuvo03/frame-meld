import base64
import json
import os
from decimal import Decimal

import httpx
import pytest

from framemeld.config import settings
from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.providers.images.gemini import GeminiImageProvider
from framemeld.providers.images.stub import solid_png

_KEY = "test-image-key"


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", _KEY)
    monkeypatch.setattr(settings, "image_model", "gemini-2.5-flash-image")
    return GeminiImageProvider(transport=httpx.MockTransport(handler))


def _ok(data: bytes, mime="image/png", finish_reason="STOP", **extra):
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
                    ]
                },
                "finishReason": finish_reason,
            }
        ]
    } | extra
    return httpx.Response(200, json=body)


async def test_a_successful_call_returns_decoded_bytes(monkeypatch):
    png = solid_png(8, 8, (1, 2, 3))
    provider = _provider(lambda request: _ok(png), monkeypatch)

    result = await provider.generate(prompt="a lighthouse keeper", refs=[], params={})

    assert result.bytes_ == png
    assert result.text is None
    assert result.provider == "gemini"
    assert result.raw == {"mimeType": "image/png"}


async def test_the_cost_comes_from_the_rate_table(monkeypatch):
    """There is no usage block on an image response, so the per-image rate
    is the only source (M3-D6)."""
    provider = _provider(lambda request: _ok(solid_png(4, 4, (0, 0, 0))), monkeypatch)
    result = await provider.generate(prompt="x", refs=[], params={})
    assert result.cost_usd == Decimal("0.039")


async def test_the_request_carries_the_key_in_a_header(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return _ok(solid_png(4, 4, (0, 0, 0)))

    provider = _provider(handler, monkeypatch)
    await provider.generate(prompt="a lighthouse", refs=[], params={})

    assert seen["key"] == _KEY
    # Never in the query string: URLs reach access logs, headers do not.
    assert _KEY not in seen["url"]
    assert seen["url"].endswith("/models/gemini-2.5-flash-image:generateContent")
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "a lighthouse"
    assert seen["body"]["generationConfig"]["responseModalities"] == ["IMAGE"]


async def test_reference_images_travel_as_inline_base64(monkeypatch):
    """M3 always passes an empty list; M4 is the first caller that fills it
    (M3-D12), so the encoding is pinned before anything depends on it."""
    seen = {}
    reference = solid_png(4, 4, (9, 9, 9))

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _ok(solid_png(4, 4, (0, 0, 0)))

    provider = _provider(handler, monkeypatch)
    await provider.generate(prompt="a lighthouse", refs=[reference], params={})

    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["text"] == "a lighthouse"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == reference
    assert parts[1]["inlineData"]["mimeType"] == "image/png"


@pytest.mark.parametrize("status_code", [408, 409, 425, 429, 500, 503])
async def test_transient_statuses_are_retryable(status_code, monkeypatch):
    provider = _provider(lambda request: httpx.Response(status_code, json={}), monkeypatch)
    with pytest.raises(ProviderTransportError) as caught:
        await provider.generate(prompt="x", refs=[], params={})
    assert caught.value.retryable is True


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_client_errors_are_terminal(status_code, monkeypatch):
    """Retrying a 400 spends money to be rejected identically."""
    provider = _provider(lambda request: httpx.Response(status_code, json={}), monkeypatch)
    with pytest.raises(ProviderResponseError) as caught:
        await provider.generate(prompt="x", refs=[], params={})
    assert caught.value.retryable is False


async def test_a_network_failure_is_retryable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderTransportError) as caught:
        await provider.generate(prompt="x", refs=[], params={})
    assert caught.value.retryable is True


async def test_a_blocked_prompt_is_a_terminal_refusal(monkeypatch):
    provider = _provider(
        lambda request: httpx.Response(
            200, json={"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
        ),
        monkeypatch,
    )
    with pytest.raises(ProviderRefusedError) as caught:
        await provider.generate(prompt="x", refs=[], params={})
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    "finish_reason", ["SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"]
)
async def test_a_refusing_finish_reason_is_terminal(finish_reason, monkeypatch):
    provider = _provider(
        lambda request: _ok(solid_png(4, 4, (0, 0, 0)), finish_reason=finish_reason), monkeypatch
    )
    with pytest.raises(ProviderRefusedError):
        await provider.generate(prompt="x", refs=[], params={})


async def test_a_candidate_with_no_image_part_is_a_response_error(monkeypatch):
    """The model answered in words instead of pixels. Nothing downstream
    can store that as a character sheet."""
    provider = _provider(
        lambda request: httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "I drew nothing."}]}}]},
        ),
        monkeypatch,
    )
    with pytest.raises(ProviderResponseError):
        await provider.generate(prompt="x", refs=[], params={})


async def test_undecodable_base64_is_a_response_error(monkeypatch):
    provider = _provider(
        lambda request: httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"inlineData": {"data": "!!!not b64!!!"}}]}}]
            },
        ),
        monkeypatch,
    )
    with pytest.raises(ProviderResponseError):
        await provider.generate(prompt="x", refs=[], params={})


async def test_an_unreadable_body_is_a_response_error(monkeypatch):
    provider = _provider(
        lambda request: httpx.Response(200, content=b"<html>gateway</html>"), monkeypatch
    )
    with pytest.raises(ProviderResponseError):
        await provider.generate(prompt="x", refs=[], params={})


async def test_no_error_message_ever_quotes_the_api_key(monkeypatch):
    """jobs.error is shown to the user. A key that reached it would be
    disclosed to anyone who could make a generation fail."""
    handlers = [
        lambda request: httpx.Response(403, json={"error": {"message": f"bad key {_KEY}"}}),
        lambda request: httpx.Response(500, text=_KEY),
        lambda request: httpx.Response(200, content=b"not json " + _KEY.encode()),
    ]
    for handler in handlers:
        provider = _provider(handler, monkeypatch)
        with pytest.raises(Exception) as caught:
            await provider.generate(prompt="x", refs=[], params={})
        assert _KEY not in str(caught.value)


async def test_a_missing_key_refuses_to_construct(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(ValueError):
        GeminiImageProvider()


@pytest.mark.skipif(
    not os.getenv("FRAMEMELD_LIVE_IMAGE"),
    reason="set FRAMEMELD_LIVE_IMAGE=1 to call the real image API (this spends money)",
)
async def test_the_real_api_draws_something():
    """Separate env var from FRAMEMELD_LIVE_LLM, deliberately: the text
    live test is free on the Flash-Lite tier, and this one is not.

    This has never been run green -- image generation has zero free-tier
    quota, and the key's project has no billing enabled (see the adapter's
    docstring). It is the check that confirms the success-path wire format,
    so run it first thing after enabling billing.
    """
    from framemeld.assets.service import png_dimensions

    assert settings.gemini_api_key, "GEMINI_API_KEY is not set"
    result = await GeminiImageProvider().generate(
        prompt="Character reference sheet, plain grey background, a lighthouse keeper.",
        refs=[],
        params={},
    )
    assert result.bytes_ and len(result.bytes_) > 1000
    assert result.cost_usd > 0
    print("dimensions:", png_dimensions(result.bytes_))

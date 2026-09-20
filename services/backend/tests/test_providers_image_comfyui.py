import json
import struct
import zlib
from decimal import Decimal

import httpx
import pytest

from framemeld.config import settings
from framemeld.providers.base import ProviderResponseError, ProviderTransportError
from framemeld.providers.images.comfyui import ComfyUIImageProvider

_PROMPT_ID = "abc-123"


def _png() -> bytes:
    """A real 1x1 PNG. The adapter checks the magic bytes, so a placeholder
    blob would not exercise the path it guards."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _history(status_str="success", images=None):
    return {
        _PROMPT_ID: {
            "status": {"status_str": status_str, "completed": status_str == "success"},
            "outputs": {
                "9": {
                    "images": images
                    if images is not None
                    else [{"filename": "framemeld_00001_.png", "subfolder": "", "type": "output"}]
                }
            },
        }
    }


def _router(*, history=None, png=None, queued=None, seen=None):
    """One handler for the three endpoints a generation touches."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/prompt":
            if seen is not None:
                seen.update(json.loads(request.content))
            return (
                queued
                if queued is not None
                else httpx.Response(200, json={"prompt_id": _PROMPT_ID})
            )
        if path.startswith("/history/"):
            return httpx.Response(200, json=history if history is not None else _history())
        if path == "/view":
            if seen is not None:
                seen["view_query"] = dict(request.url.params)
            return httpx.Response(200, content=png if png is not None else _png())
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler, monkeypatch):
    monkeypatch.setattr(settings, "comfyui_base_url", "http://comfy.test")
    monkeypatch.setattr(settings, "comfyui_checkpoint", "sd_xl_turbo_1.0_fp16.safetensors")
    return ComfyUIImageProvider(transport=httpx.MockTransport(handler))


async def test_a_generation_returns_the_png_the_workflow_saved(monkeypatch):
    provider = _provider(_router(), monkeypatch)
    result = await provider.generate(prompt="a lighthouse", refs=[], params={"size": "1024x1024"})

    assert result.bytes_[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.cost_usd == Decimal("0")
    assert result.provider == "comfyui"
    assert result.model == "sd_xl_turbo_1.0_fp16.safetensors"


async def test_the_size_in_params_reaches_the_latent(monkeypatch):
    """SHEET_PARAMS and STILL_PARAMS carry `size`, and that string is part
    of the content hash. Drawing a different size than the hash claims is
    the kind of drift that only shows up in a render."""
    seen: dict[str, object] = {}
    provider = _provider(_router(seen=seen), monkeypatch)
    await provider.generate(prompt="a lighthouse", refs=[], params={"size": "512x768"})

    latent = seen["prompt"]["5"]["inputs"]
    assert (latent["width"], latent["height"]) == (512, 768)


async def test_turbo_sampler_settings_are_not_comfyuis_defaults(monkeypatch):
    """SDXL-Turbo is distilled: 1 step at cfg 1.0. ComfyUI's own defaults
    of 20 steps and cfg 8.0 produce a burned image on this checkpoint, and
    the failure is visual rather than an error."""
    seen: dict[str, object] = {}
    provider = _provider(_router(seen=seen), monkeypatch)
    await provider.generate(prompt="a lighthouse", refs=[], params={"size": "1024x1024"})

    sampler = seen["prompt"]["3"]["inputs"]
    assert sampler["steps"] == 1
    assert sampler["cfg"] == 1.0


async def test_the_same_prompt_draws_the_same_picture(monkeypatch):
    """Deterministic by seed, for the same reason voice_for_index is: the
    staleness engine compares hashes, and a random seed would make every
    unchanged still look like work to redo."""
    first: dict[str, object] = {}
    second: dict[str, object] = {}
    await _provider(_router(seen=first), monkeypatch).generate(
        prompt="a lighthouse", refs=[], params={"size": "1024x1024"}
    )
    await _provider(_router(seen=second), monkeypatch).generate(
        prompt="a lighthouse", refs=[], params={"size": "1024x1024"}
    )
    different: dict[str, object] = {}
    await _provider(_router(seen=different), monkeypatch).generate(
        prompt="a harbour", refs=[], params={"size": "1024x1024"}
    )

    assert first["prompt"]["3"]["inputs"]["seed"] == second["prompt"]["3"]["inputs"]["seed"]
    assert first["prompt"]["3"]["inputs"]["seed"] != different["prompt"]["3"]["inputs"]["seed"]


async def test_refs_are_accepted_and_ignored(monkeypatch):
    """Documented behaviour, not an oversight: matching a face across shots
    needs IP-Adapter, and character consistency is a production question.
    The call must still succeed, because the worker passes sheets."""
    seen: dict[str, object] = {}
    provider = _provider(_router(seen=seen), monkeypatch)
    result = await provider.generate(
        prompt="a lighthouse", refs=[b"a sheet", b"another"], params={"size": "1024x1024"}
    )

    assert result.bytes_[:8] == b"\x89PNG\r\n\x1a\n"
    assert "a sheet" not in json.dumps(seen["prompt"])


async def test_a_failed_execution_is_terminal_not_a_retry(monkeypatch):
    """ComfyUI answers 200 and reports the failure inside history. Treating
    that as success would store whatever `/view` returned for an image that
    was never drawn."""
    provider = _provider(_router(history=_history(status_str="error")), monkeypatch)
    with pytest.raises(ProviderResponseError):
        await provider.generate(prompt="a lighthouse", refs=[], params={"size": "1024x1024"})


async def test_a_rejected_workflow_names_the_node(monkeypatch):
    """ComfyUI puts the useful part in node_errors, and it is our own
    workflow read back rather than anything secret -- a missing checkpoint
    should not surface as a bare 400."""
    queued = httpx.Response(400, text='{"node_errors": {"4": "value not in list"}}')
    provider = _provider(_router(queued=queued), monkeypatch)
    with pytest.raises(ProviderResponseError, match="value not in list"):
        await provider.generate(prompt="a lighthouse", refs=[], params={"size": "1024x1024"})


async def test_a_non_png_body_is_refused(monkeypatch):
    """png_dimensions returns None for anything unreadable and the column
    stays null, so a non-PNG would reach the database looking fine."""
    provider = _provider(_router(png=b"<html>error</html>"), monkeypatch)
    with pytest.raises(ProviderResponseError, match="not a PNG"):
        await provider.generate(prompt="a lighthouse", refs=[], params={"size": "1024x1024"})


async def test_an_output_with_no_image_is_refused(monkeypatch):
    provider = _provider(_router(history=_history(images=[])), monkeypatch)
    with pytest.raises(ProviderResponseError, match="no image"):
        await provider.generate(prompt="a lighthouse", refs=[], params={"size": "1024x1024"})


async def test_a_bad_size_fails_before_the_network(monkeypatch):
    def handler(request):  # pragma: no cover - the point is that it is not called
        raise AssertionError("no request should have been made")

    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderResponseError, match="not a usable image size"):
        await provider.generate(prompt="a lighthouse", refs=[], params={"size": "wide"})


async def test_an_unreachable_container_says_so(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler, monkeypatch)
    with pytest.raises(ProviderTransportError, match="ComfyUI container"):
        await provider.generate(prompt="a lighthouse", refs=[], params={"size": "1024x1024"})

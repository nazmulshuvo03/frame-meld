import pytest

from framemeld.assets.service import png_dimensions
from framemeld.providers.base import ProviderRefusedError
from framemeld.providers.images.registry import get_image_provider
from framemeld.providers.images.stub import STUB_SIZE, StubImageProvider


async def test_the_registry_returns_the_stub_by_default():
    assert isinstance(get_image_provider(), StubImageProvider)


async def test_an_unknown_provider_raises(monkeypatch):
    from framemeld.config import settings

    monkeypatch.setattr(settings, "image_provider", "midjourney-by-fax")
    with pytest.raises(ValueError):
        get_image_provider()


async def test_the_stub_returns_a_real_png():
    result = await StubImageProvider().generate(prompt="a woman", refs=[], params={})
    assert result.bytes_ is not None
    assert png_dimensions(result.bytes_) == (STUB_SIZE, STUB_SIZE)
    assert result.provider == "stub"


async def test_different_prompts_produce_different_images():
    """Two characters must not get byte-identical sheets, or a test that
    each character got its own image would pass against a generation path
    that reused one."""
    first = await StubImageProvider().generate(prompt="Elena", refs=[], params={})
    second = await StubImageProvider().generate(prompt="Marcus", refs=[], params={})
    assert first.bytes_ != second.bytes_


async def test_the_same_prompt_is_deterministic():
    first = await StubImageProvider().generate(prompt="Elena", refs=[], params={})
    second = await StubImageProvider().generate(prompt="Elena", refs=[], params={})
    assert first.bytes_ == second.bytes_


async def test_an_empty_prompt_is_refused():
    with pytest.raises(ProviderRefusedError):
        await StubImageProvider().generate(prompt="   ", refs=[], params={})

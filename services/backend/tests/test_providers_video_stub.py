import pytest

from framemeld.config import settings
from framemeld.providers.base import ProviderRefusedError
from framemeld.providers.images.stub import solid_png
from framemeld.providers.video.registry import get_video_provider
from framemeld.providers.video.stub import StubVideoProvider

STILL = solid_png(8, 8, (12, 34, 56))


async def test_the_stub_returns_a_real_mp4():
    """M6-D8, and the strongest case for the rule yet: these bytes are
    stored, signed, decoded by Remotion, muxed into a second MP4 and played
    in a browser. A placeholder blob would let all five ship broken."""
    result = await StubVideoProvider().animate(
        image=STILL, motion_prompt="a slow push in", duration_sec=2.0, params={}
    )
    assert result.bytes_ is not None
    # An MP4's first box is `ftyp`, at offset 4 after its length.
    assert result.bytes_[4:8] == b"ftyp"
    assert result.raw["mimeType"] == "video/mp4"


async def test_two_different_stills_do_not_animate_to_the_same_clip():
    """Two shots sharing a motion prompt and holding different pictures must
    not collide -- this stands in for an *image*-to-video model."""
    first = await StubVideoProvider().animate(
        image=STILL, motion_prompt="same", duration_sec=1.0, params={}
    )
    second = await StubVideoProvider().animate(
        image=solid_png(8, 8, (200, 10, 10)), motion_prompt="same", duration_sec=1.0, params={}
    )
    assert first.bytes_ != second.bytes_


async def test_animating_nothing_is_refused_rather_than_billed():
    with pytest.raises(ProviderRefusedError):
        await StubVideoProvider().animate(image=b"", motion_prompt="x", duration_sec=2.0, params={})


@pytest.mark.parametrize("duration", [0.0, -1.0, 5.5, 60.0])
async def test_a_clip_outside_the_model_limit_is_refused(duration):
    """Spec section 8: every current image-to-video model caps a clip at
    about five seconds, which is why shots.duration_sec has a constraint.
    A caller that lost it finds out here, not in a Final render."""
    with pytest.raises(ProviderRefusedError):
        await StubVideoProvider().animate(
            image=STILL, motion_prompt="x", duration_sec=duration, params={}
        )


async def test_the_stub_costs_nothing():
    result = await StubVideoProvider().animate(
        image=STILL, motion_prompt="x", duration_sec=1.0, params={}
    )
    assert result.cost_usd == 0


def test_an_unknown_provider_name_raises_rather_than_falling_back(monkeypatch):
    """This is the adapter whose calls cost dollars rather than cents. A
    fallback would answer a $24 render with a colour field."""
    monkeypatch.setattr(settings, "video_provider", "wan")
    with pytest.raises(ValueError, match="Unknown VIDEO_PROVIDER"):
        get_video_provider()


async def test_the_same_still_given_different_instructions_differs_too():
    """The other half. A real model reads both, and keying on only one of
    them would make the other look like it had no effect -- which the
    staleness engine, sitting downstream of these bytes, would then report
    as fact.
    """
    first = await StubVideoProvider().animate(
        image=STILL, motion_prompt="a slow push in", duration_sec=1.0, params={}
    )
    second = await StubVideoProvider().animate(
        image=STILL, motion_prompt="a violent whip pan", duration_sec=1.0, params={}
    )
    assert first.bytes_ != second.bytes_

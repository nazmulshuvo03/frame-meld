from decimal import Decimal

from framemeld.providers.rates import (
    usd_for_image,
    usd_for_music,
    usd_for_speech,
    usd_for_tokens,
    usd_for_video,
)


def test_token_cost_is_priced_per_million():
    # 1M input + 1M output at flash-lite's rates ($0.30 + $2.50).
    assert usd_for_tokens("gemini-flash-lite-latest", 1_000_000, 1_000_000) == Decimal("2.80")


def test_the_alias_and_the_id_it_resolves_to_cost_the_same():
    """The adapter looks the rate up with the `modelVersion` the response
    carries, not with the alias that was requested -- asking for
    gemini-flash-lite-latest answers as gemini-3.5-flash-lite. A table with
    only the alias would silently cost every real call at zero."""
    alias = usd_for_tokens("gemini-flash-lite-latest", 2_000, 800)
    resolved = usd_for_tokens("gemini-3.5-flash-lite", 2_000, 800)
    assert alias == resolved > Decimal("0")


def test_a_small_call_costs_a_fraction_of_a_cent():
    cost = usd_for_tokens("gemini-flash-lite-latest", 2_000, 800)
    assert Decimal("0") < cost < Decimal("0.01")


def test_an_unpriced_model_costs_zero_rather_than_raising():
    """M3-D5: this number is observability, not billing. 'We have not
    written this price down' must never become 'the user's generation
    fails'."""
    assert usd_for_tokens("some-model-shipped-next-tuesday", 1000, 1000) == Decimal("0")
    assert usd_for_image("some-image-model-shipped-next-tuesday") == Decimal("0")


def test_image_cost_scales_with_count():
    one = usd_for_image("gemini-2.5-flash-image")
    assert usd_for_image("gemini-2.5-flash-image", 3) == one * 3


def test_speech_is_priced_on_both_sides():
    """Corrected 2026-09-19: the published $10.00 is per million *audio
    output* tokens, and text input is a separate, much cheaper rate. The
    table used to hold one number per model and multiply it by the input's
    character count."""
    cost = usd_for_speech("gemini-2.5-flash-preview-tts", 1_000_000, 1_000_000)
    assert cost == Decimal("10.50")


def test_music_is_flat_per_track():
    """Lyria bills per request, not per second, which is why the adapter
    does not try to ask for a shorter piece on a short film."""
    assert usd_for_music("lyria-3.5") == Decimal("0.08")


def test_video_is_priced_per_second_of_the_tier_generated():
    assert usd_for_video("veo-3.1-lite-generate-preview", 4) == Decimal("0.20")
    assert usd_for_video("veo-3.1-lite-generate-preview", 6) == Decimal("0.30")


def test_a_clip_still_carries_the_margin_spec_section_7_assumes():
    """80 credits is $0.80 retail (spec 4.3). Both tiers must stay under it,
    or the one expensive action in this product sells at a loss."""
    retail = Decimal("0.80")
    for seconds in (4, 6, 8):
        assert usd_for_video("veo-3.1-lite-generate-preview", seconds) < retail


def test_unpriced_music_and_video_models_cost_zero_rather_than_raising():
    assert usd_for_music("some-music-model-shipped-next-tuesday") == Decimal("0")
    assert usd_for_video("some-video-model-shipped-next-tuesday", 6) == Decimal("0")

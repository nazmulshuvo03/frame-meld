from decimal import Decimal

from framemeld.providers.rates import usd_for_image, usd_for_tokens


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

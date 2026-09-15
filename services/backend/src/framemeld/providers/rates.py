import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

#: USD per 1,000,000 tokens, as (input, output). Captured from
#: https://ai.google.dev/gemini-api/docs/pricing on 2026-09-13.
#:
#: Keyed by both the configured alias and the concrete model id, because
#: the adapter looks this up with whatever `modelVersion` the response
#: carries -- and Gemini answers a request to `gemini-flash-lite-latest`
#: with `gemini-3.5-flash-lite`. Verified live on 2026-09-13, not assumed:
#: an alias's price moves under it whenever Google rotates what it points
#: at, which is why this table is checked against the page rather than
#: trusted forever.
_TEXT_RATES: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-flash-lite-latest": (Decimal("0.30"), Decimal("2.50")),
    "gemini-3.5-flash-lite": (Decimal("0.30"), Decimal("2.50")),
    "gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
    "gemini-flash-latest": (Decimal("0.75"), Decimal("3.75")),
    "gemini-3.8-flash": (Decimal("0.75"), Decimal("3.75")),
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
}

#: USD per generated image, at the resolution this codebase asks for (1K).
#:
#: Imagen 4 is absent on purpose: the whole family was shut down on
#: 2026-08-17, a month before this milestone, so the plan's original
#: default of imagen-4.0-fast-generate-001 names an endpoint that no longer
#: answers. Pricing a dead model would make a wrong number look maintained.
_IMAGE_RATES: dict[str, Decimal] = {
    "gemini-2.5-flash-image": Decimal("0.039"),
    "gemini-3.1-flash-lite-image": Decimal("0.0336"),
    "gemini-3.1-flash-image": Decimal("0.067"),
    "gemini-3-pro-image": Decimal("0.134"),
}

#: USD per 1,000,000 input characters. TTS is billed on what you send,
#: not on what comes back, so there is no output side to this one.
#: Captured from https://ai.google.dev/gemini-api/docs/pricing alongside the
#: tables above; like them, it is checked against the page rather than
#: trusted forever, and like them it has NOT been confirmed by a live call
#: from this account (see the adapter's docstring).
_SPEECH_RATES: dict[str, Decimal] = {
    "gemini-2.5-flash-preview-tts": Decimal("10.00"),
    "gemini-2.5-pro-preview-tts": Decimal("20.00"),
}

_MILLION = Decimal("1000000")


def usd_for_tokens(model: str, prompt_tokens: int, output_tokens: int) -> Decimal:
    """What a completion cost, from the token counts the provider reported.

    An unpriced model is logged and costed as zero rather than raising.
    This number is margin observability, never the billing input (M3-D5):
    credits are quoted at a fixed retail price and settled against work
    done. Raising here would turn "we have not written this model's price
    down yet" into "the user's generation fails", which is the wrong trade
    by a wide margin.
    """
    rates = _TEXT_RATES.get(model)
    if rates is None:
        logger.warning("no token rate for model %s; recording cost 0", model)
        return Decimal("0")

    prompt_rate, output_rate = rates
    return (prompt_rate * prompt_tokens + output_rate * output_tokens) / _MILLION


def usd_for_image(model: str, count: int = 1) -> Decimal:
    """What an image generation cost. Same fail-soft rule as above."""
    rate = _IMAGE_RATES.get(model)
    if rate is None:
        logger.warning("no image rate for model %s; recording cost 0", model)
        return Decimal("0")
    return rate * count


def usd_for_speech(model: str, characters: int) -> Decimal:
    """What a TTS call cost, from the length of the text that was sent.

    Same fail-soft rule as the two above: an unpriced model is logged and
    costed as zero rather than raising. This number is margin
    observability, never the billing input (M3-D5).
    """
    rate = _SPEECH_RATES.get(model)
    if rate is None:
        logger.warning("no speech rate for model %s; recording cost 0", model)
        return Decimal("0")
    return rate * characters / _MILLION

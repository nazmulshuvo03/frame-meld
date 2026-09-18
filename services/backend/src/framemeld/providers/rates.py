import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

#: USD per 1,000,000 tokens, as (input, output). Captured from
#: https://ai.google.dev/gemini-api/docs/pricing on 2026-09-13 and
#: re-checked on 2026-09-19.
#:
#: Keyed by both the configured alias and the concrete model id, because
#: the adapter looks this up with whatever `modelVersion` the response
#: carries -- and Gemini answers a request to `gemini-flash-lite-latest`
#: with `gemini-3.5-flash-lite`. Verified live on 2026-09-13 and again on
#: 2026-09-19, not assumed: an alias's price moves under it whenever Google
#: rotates what it points at, which is why this table is checked against
#: the page rather than trusted forever.
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
#: 2026-08-17, so the plan's original default of imagen-4.0-fast-generate-001
#: names an endpoint that no longer answers. Pricing a dead model would make
#: a wrong number look maintained.
_IMAGE_RATES: dict[str, Decimal] = {
    "gemini-2.5-flash-image": Decimal("0.039"),
    "gemini-3.1-flash-lite-image": Decimal("0.0336"),
    "gemini-3.1-flash-image": Decimal("0.067"),
    "gemini-3-pro-image": Decimal("0.134"),
}

#: USD per 1,000,000 tokens, as (text in, audio out).
#:
#: **Corrected 2026-09-19.** This table used to hold a single number per
#: model and `usd_for_speech` multiplied it by the *character* count of the
#: text sent. Both halves of that were wrong: the published figure is the
#: price of a million **audio output tokens**, and the input side is a
#: separate, much cheaper text rate. A live call confirms the shape --
#: `usageMetadata` reports `promptTokenCount` against modality TEXT and
#: `candidatesTokenCount` against modality AUDIO -- so the counts to bill
#: are the ones the response carries, exactly as the text adapter already
#: does. The old form under-reported a line of narration by roughly half
#: and would have drifted further as lines got longer, because characters
#: and audio tokens do not move together.
_SPEECH_RATES: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-2.5-flash-preview-tts": (Decimal("0.50"), Decimal("10.00")),
    "gemini-2.5-pro-preview-tts": (Decimal("1.00"), Decimal("20.00")),
}

#: USD per generated track. Lyria bills a flat rate per request rather than
#: per second -- the model decides how long the piece is, and asking for a
#: shorter one does not make it cheaper. Captured 2026-09-19.
_MUSIC_RATES: dict[str, Decimal] = {
    "lyria-3.5": Decimal("0.08"),
}

#: USD per second of generated video, at the resolution this codebase asks
#: for (720p). Captured 2026-09-19.
#:
#: The one rate here that is worth watching: at 4 seconds a clip costs
#: $0.20 against an 80-credit ($0.80) retail price, and a shot that needs
#: the 6-second tier costs $0.30. Spec section 7's whole cost model rests on
#: this number, and it is billed per second rather than per call, so a
#: change to the duration ladder in the Veo adapter is a change to margin.
#: The other two Veo tiers are absent deliberately. They exist on this key
#: (`veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`) and this
#: adapter can be pointed at either, but their per-second prices have not
#: been read off the pricing page, and a guessed number here would be
#: indistinguishable from a checked one. Unpriced costs zero and logs, which
#: is a visible gap; a wrong price is an invisible one.
_VIDEO_RATES: dict[str, Decimal] = {
    "veo-3.1-lite-generate-preview": Decimal("0.05"),
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


def usd_for_speech(model: str, prompt_tokens: int, audio_tokens: int) -> Decimal:
    """What a TTS call cost, from the token counts the response reported.

    Same two-sided shape and same fail-soft rule as `usd_for_tokens`: TTS is
    billed on the text going in *and* the audio coming out, and the audio
    side dominates by twenty to one. Taking both from `usageMetadata` rather
    than measuring the input ourselves keeps this honest the same way the
    text adapter is -- the provider's own count is the one being billed.
    """
    rates = _SPEECH_RATES.get(model)
    if rates is None:
        logger.warning("no speech rate for model %s; recording cost 0", model)
        return Decimal("0")

    prompt_rate, audio_rate = rates
    return (prompt_rate * prompt_tokens + audio_rate * audio_tokens) / _MILLION


def usd_for_music(model: str) -> Decimal:
    """What one generated track cost. Flat per request, not per second.

    Same fail-soft rule as the rest: an unpriced model is logged and costed
    as zero rather than failing a user's generation (M3-D5).
    """
    rate = _MUSIC_RATES.get(model)
    if rate is None:
        logger.warning("no music rate for model %s; recording cost 0", model)
        return Decimal("0")
    return rate


def usd_for_video(model: str, seconds: float) -> Decimal:
    """What a clip cost, from the seconds actually billed.

    `seconds` is the duration *requested of the provider*, not the shot's
    length -- Veo bills the tier it generated, and this adapter rounds a
    4.2-second shot up to the 6-second tier (see the adapter). Passing the
    shot's own duration here would under-report the one call in this product
    that is expensive enough to matter.
    """
    rate = _VIDEO_RATES.get(model)
    if rate is None:
        logger.warning("no video rate for model %s; recording cost 0", model)
        return Decimal("0")
    return rate * Decimal(str(seconds))

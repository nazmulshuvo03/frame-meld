from framemeld.models.job import JobKind

#: Bump when any price below changes. Stored on nothing yet; M6's purchase
#: flow will want it on the ledger row so an old charge can be explained
#: against the table that produced it.
PRICING_VERSION = 1

#: Retail credits per action. 1 credit = $0.01 (spec section 4.3).
#:
#: A fixed price per action, not a function of the provider's bill: the
#: user is quoted before they click and the quote is a ceiling (M3-D3), so
#: the number here has to be knowable in advance. The markup over provider
#: cost is roughly 4x, which is what spec section 7's ~$6.90 cost and
#: ~$25-30 retail imply.
#:
#: generate_characters: one LLM call over a few thousand tokens, well under
#: a cent at Flash-Lite rates.
#: generate_character_image: ~$0.02 (Imagen 4 Fast) to ~$0.04 (Nano Banana)
#: per image -- see providers/rates.py, and re-check both before changing
#: this number.
#: map_shot_characters: one LLM call over the cast and the shot list,
#: the same order of magnitude as the extraction above.
#: generate_shot_image: the same model and the same provider cost as a
#: character sheet, so the same price -- a still is not more expensive to
#: make just because it matters more.
#: generate_speech: a few hundred characters of TTS, well under a tenth of
#: a cent at Gemini's rate (providers/rates.py). Priced above zero anyway,
#: because a free action is one a script can call thirty thousand times.
#: generate_music: one full-length track. Lyria is ~$0.04-0.08 and
#: ACE-Step ~$0.008 (spec sections 6 and 6.3); 24 credits is the same ~3x
#: markup the image actions carry, taken against the dearer of the two.
#:
#: generate_shot_clip: the one expensive action in this product. Spec
#: section 7 puts the clips at $6.00 of a $6.90 film and implies $25-30
#: retail, i.e. 2,500-3,000 credits; 30 x 80 = 2,400 of that, and the
#: M3-M5 actions on a 30-shot, 5-character film come to ~514.
#: render_final: section 7's $0.20 of render compute at the same ~3x
#: markup the image actions carry. Unlike a Preview it is charged,
#: because it is the deliverable rather than the gate in front of it.
#:
#: render_preview is deliberately absent (M5-D7). Spec 4.2 and 9 both make
#: Preview free, because it is the approval gate that makes a $6 Final
#: render humane -- charging for it would defeat what it is for. Its abuse
#: control is the idempotency key: an unchanged film re-renders for
#: nothing because it does not re-render at all.
QUOTES: dict[JobKind, int] = {
    JobKind.GENERATE_CHARACTERS: 5,
    JobKind.GENERATE_CHARACTER_IMAGE: 12,
    JobKind.MAP_SHOT_CHARACTERS: 5,
    JobKind.GENERATE_SHOT_IMAGE: 12,
    JobKind.GENERATE_SPEECH: 2,
    JobKind.GENERATE_MUSIC: 24,
    JobKind.GENERATE_SHOT_CLIP: 80,
    JobKind.RENDER_FINAL: 60,
}


def quote_for(kind: JobKind) -> int:
    """What this action costs, in credits.

    Raises KeyError for an unpriced kind rather than returning a default.
    A default of 0 would quote future work as free and only be noticed on
    a revenue report; a default of "something large" would refuse work for
    no reason. Neither is better than failing at the call site.
    """
    return QUOTES[kind]

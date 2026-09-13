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
QUOTES: dict[JobKind, int] = {
    JobKind.GENERATE_CHARACTERS: 5,
    JobKind.GENERATE_CHARACTER_IMAGE: 12,
}


def quote_for(kind: JobKind) -> int:
    """What this action costs, in credits.

    Raises KeyError for an unpriced kind rather than returning a default.
    A default of 0 would quote future work as free and only be noticed on
    a revenue report; a default of "something large" would refuse work for
    no reason. Neither is better than failing at the call site.
    """
    return QUOTES[kind]

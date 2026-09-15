#: The voices a character may be given.
#:
#: Gemini's prebuilt TTS voice names, because that is the adapter this
#: milestone ships against a real service. They are stored on
#: `characters.voice_id` verbatim and travel to whichever provider is
#: configured, so a future Kokoro adapter (spec section 6) maps them rather
#: than the database being rewritten -- a voice id is part of a character's
#: content_hash, and migrating one would invalidate every audio clip in the
#: system.
VOICE_IDS: tuple[str, ...] = (
    "Kore",
    "Puck",
    "Charon",
    "Zephyr",
    "Fenrir",
    "Aoede",
    "Leda",
    "Orus",
)

#: What a shot with no speaker is read in -- narration, and the fallback
#: for a character whose voice has never been set.
DEFAULT_VOICE_ID = VOICE_IDS[0]


def voice_for_index(index: int) -> str:
    """A deterministic voice per character position.

    Seeded at extraction time so a fresh cast can be heard immediately
    without the user choosing eight voices first. Deterministic rather than
    random because `voice_id` is inside the character's content_hash: a
    random seed would give the same story a different hash on every
    extraction, and every audio clip would look stale for no reason.
    """
    return VOICE_IDS[index % len(VOICE_IDS)]

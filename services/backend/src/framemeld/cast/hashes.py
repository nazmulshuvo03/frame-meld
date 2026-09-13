from framemeld.config import settings
from framemeld.hashing import canonical_hash
from framemeld.models.cast import MAX_CHARACTERS, Character, CharacterImageKind

#: Bump when the extraction prompt or schema changes in a way that would
#: produce a different cast from the same story. Part of CHARACTERS_PARAMS,
#: so bumping it correctly marks every existing cast stale.
CHARACTERS_PROMPT_VERSION = 1

#: Bump when the sheet prompt template changes. Marks every existing sheet
#: stale -- which is true, and expensive, so bump it deliberately.
SHEET_PROMPT_VERSION = 1

CHARACTERS_PARAMS: dict[str, object] = {
    "prompt_version": CHARACTERS_PROMPT_VERSION,
    # Lower than the script generator's 0.7: a cast list is extraction, not
    # invention, and the failure mode to avoid is confident embellishment.
    "temperature": 0.4,
    "max_characters": MAX_CHARACTERS,
}

SHEET_PARAMS: dict[str, object] = {
    "prompt_version": SHEET_PROMPT_VERSION,
    "size": "1024x1024",
}


def content_hash_for_character(character: Character) -> str:
    """Every user-editable field that changes what the character *is*.

    `order_index` is deliberately excluded, unlike Scene and Shot. A shot's
    position is part of the film; a character's position in a list is
    presentation. Including it would mean dragging a character up the cast
    list invalidated their artwork and charged the user to regenerate an
    identical picture.

    Leaving a field out here is not a small bug: M4's shot images key on
    this value, so an omitted field is one whose edit never invalidates the
    artwork generated from it.
    """
    return canonical_hash(
        {
            "kind": "character",
            "name": character.name,
            "role": character.role.value,
            "description": character.description,
            "appearance": character.appearance,
            "voice_id": character.voice_id,
        }
    )


def content_hash_for_character_image(asset_sha256: str) -> str:
    """What the image *is*: a hash of the bytes that were produced.

    A generated row still needs a content_hash, because spec section 4.1 is
    explicit that downstream keys on content_hash and never on input_hash --
    and in M4 a shot image's input_hash includes the content_hash of every
    character sheet it referenced. Wrapped in a labelled payload rather
    than storing the raw sha256, so a hash from this table can never be
    mistaken for one from another.
    """
    return canonical_hash({"kind": "character_image", "asset_sha256": asset_sha256})


def characters_input_hash(story_content_hash: str) -> str:
    """What would produce this cast if it were extracted right now.

    Reads the *current* provider configuration, the same rule M2's
    script_input_hash follows (D2): staleness and job idempotency are then
    the same function, so a row the UI shows as stale can never turn out to
    be a silent cache hit when the user presses Regenerate.
    """
    return canonical_hash(
        {
            "kind": "characters",
            "story_content_hash": story_content_hash,
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "params": CHARACTERS_PARAMS,
        }
    )


def character_image_input_hash(character_content_hash: str, kind: CharacterImageKind) -> str:
    """What would produce this image right now.

    Keyed on the character's content_hash -- what they look like now -- not
    on their input_hash. That is the whole point of the two-hash split: a
    human rewriting `appearance` must invalidate the sheet, and editing
    never changes an input_hash.

    Reads the *image* provider settings, not the LLM's: changing
    LLM_MODEL must not invalidate artwork no language model produced.
    """
    return canonical_hash(
        {
            "kind": "character_image",
            "image_kind": kind.value,
            "character_content_hash": character_content_hash,
            "provider": settings.image_provider,
            "model": settings.image_model,
            "params": SHEET_PARAMS,
        }
    )

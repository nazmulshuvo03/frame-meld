import uuid
from decimal import ROUND_HALF_UP, Decimal

from framemeld.config import settings
from framemeld.hashing import canonical_hash
from framemeld.models.script import MAX_SHOT_SECONDS, MAX_SHOTS, Scene, Shot

# Bump whenever the prompt template or the JSON schema changes in a way that
# would produce a different script from the same story. It is part of
# SCRIPT_PARAMS, so bumping it correctly marks every existing script stale.
PROMPT_VERSION = 1

# Imported rather than restated: a drifted copy would leave the hash
# describing parameters that were not used (R1).
SCRIPT_PARAMS: dict[str, object] = {
    "prompt_version": PROMPT_VERSION,
    "temperature": 0.7,
    "max_shots": MAX_SHOTS,
    "max_shot_seconds": f"{MAX_SHOT_SECONDS:.1f}",
}


def _duration_at_column_scale(value: Decimal) -> str:
    """The duration as the database will hold it.

    Decimal("4.5") and Decimal("4.50") compare equal but str() differently,
    and the column is Numeric(4, 2) — so Postgres always hands back the
    two-place form. Hashing str(value) directly would make a shot's hash
    change across a flush-and-reload with nothing edited, and every shot
    would look modified the moment it was read back.

    ROUND_HALF_UP rather than the default banker's rounding, because that is
    what Postgres numeric does: Decimal("4.005") must hash as the 4.01 the
    column will actually store, not the 4.00 an f-string would produce.
    """
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _optional_uuid(value: uuid.UUID | None) -> str | None:
    """canonical_hash refuses UUID deliberately, so the conversion is here
    and explicit rather than hidden in a default= hook."""
    return None if value is None else str(value)


def content_hash_for_story(text: str) -> str:
    """What the story *says*. A story has no generation behind it, so this
    is its only hash — see the spec's §4.1 correction."""
    return canonical_hash({"kind": "story", "text": text})


def content_hash_for_scene(scene: Scene) -> str:
    """Every user-editable field of the scene, and nothing else.

    Not its shots: a scene and its shots are siblings produced by one
    generation (D3), not parent and child in the dependency graph.
    """
    return canonical_hash(
        {
            "kind": "scene",
            "order_index": scene.order_index,
            "heading": scene.heading,
            "summary": scene.summary,
            "location": scene.location,
            "time_of_day": scene.time_of_day,
        }
    )


def content_hash_for_shot(shot: Shot) -> str:
    """Every user-editable field of the shot.

    Leaving a field out here is not a small bug: from M4 the shot image's
    input_hash is built from this value, so an omitted field is one whose
    edit never invalidates the image generated from it.
    """
    return canonical_hash(
        {
            "kind": "shot",
            "order_index": shot.order_index,
            "image_prompt": shot.image_prompt,
            "motion_prompt": shot.motion_prompt,
            "duration_sec": _duration_at_column_scale(shot.duration_sec),
            "camera": shot.camera.value,
            "dialogue_kind": shot.dialogue_kind.value,
            "speech_text": shot.speech_text,
            "speaker_character_id": _optional_uuid(shot.speaker_character_id),
        }
    )


def script_input_hash(story_content_hash: str) -> str:
    """What would produce this script if it were generated right now.

    D2: read from the *current* provider configuration, not from whatever
    produced the existing rows. The same function supplies the job's
    idempotency key, so a row the UI shows as stale can never turn out to
    be a silent cache hit when the user presses Regenerate. The price is
    that changing LLM_MODEL, or bumping PROMPT_VERSION, marks every
    existing script stale — which is true, and operator-initiated.
    """
    return canonical_hash(
        {
            "kind": "script",
            "story_content_hash": story_content_hash,
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "params": SCRIPT_PARAMS,
        }
    )

from framemeld.config import settings
from framemeld.hashing import canonical_hash
from framemeld.models.cast import Character
from framemeld.models.script import MAX_SHOTS, Shot
from framemeld.models.shots import AudioKind

#: Bump when the casting prompt or schema changes in a way that would map
#: the same cast onto the same shots differently. Part of CASTING_PARAMS,
#: so bumping it correctly marks every existing mapping stale.
CASTING_PROMPT_VERSION = 1

#: Bump when the still prompt template changes. Marks every existing still
#: stale -- which is true, and expensive, so bump it deliberately.
STILL_PROMPT_VERSION = 1

#: Bump when the speech request changes shape.
SPEECH_PROMPT_VERSION = 1

CASTING_PARAMS: dict[str, object] = {
    "prompt_version": CASTING_PROMPT_VERSION,
    # Lowest of the three LLM passes. Casting is a lookup -- which of these
    # named people is in this described frame -- and the failure mode to
    # avoid is the model deciding someone is "probably there".
    "temperature": 0.2,
    "max_shots": MAX_SHOTS,
}

STILL_PARAMS: dict[str, object] = {
    "prompt_version": STILL_PROMPT_VERSION,
    "size": "1024x1024",
}

SPEECH_PARAMS: dict[str, object] = {
    "prompt_version": SPEECH_PROMPT_VERSION,
}


def visual_hash_for_shot(shot: Shot) -> str:
    """The part of a shot a still is actually drawn from.

    A *projection*, and deliberately a narrow one -- the same shape as
    cast/hashes.py's visual_hash_for_character, for the same reason. A
    shot's content_hash also covers motion_prompt, duration_sec,
    speech_text, dialogue_kind, speaker_character_id and order_index, none
    of which still_prompt() reads. Keying a still on all of that would mean
    re-recording a line, or dragging a shot up the list, invalidated a
    picture that would come back byte-identical.

    Spec 4.1's rule is that downstream keys on upstream's content_hash and
    never on its input_hash. Narrowing to the fields that genuinely produced
    the row honours that rule rather than bending it: this is still
    upstream's current content, just the part of it that is upstream *of
    this artifact*. Widen it the day still_prompt() reads another field,
    and every existing still correctly goes stale.
    """
    return canonical_hash(
        {"kind": "shot_visual", "image_prompt": shot.image_prompt, "camera": shot.camera.value}
    )


def speech_hash_for_shot(shot: Shot, voice_id: str) -> str:
    """The part of a shot an audio clip is actually read from.

    Disjoint from visual_hash_for_shot above, which is the whole point:
    editing the image prompt must not cost the user a recording, and
    rewriting the line must not cost them a picture.

    `voice_id` is passed in rather than read off the shot, because it lives
    on the speaking character -- and changing a character's voice must
    invalidate their dialogue. It is already inside
    content_hash_for_character and deliberately outside
    visual_hash_for_character, so a voice change invalidates audio and
    leaves the sheet alone, with no new machinery.
    """
    return canonical_hash(
        {
            "kind": "shot_speech",
            "speech_text": shot.speech_text,
            "dialogue_kind": shot.dialogue_kind.value,
            "voice_id": voice_id,
        }
    )


def content_hash_for_shot_image(asset_sha256: str) -> str:
    """What the still *is*: a hash of the bytes that were produced.

    M6's clip generation keys on this, the same way this row's input_hash
    keys on the character sheets' content hashes. Wrapped in a labelled
    payload rather than storing the raw sha256, so a hash from this table
    can never be mistaken for one from another.
    """
    return canonical_hash({"kind": "shot_image", "asset_sha256": asset_sha256})


def content_hash_for_audio_clip(asset_sha256: str) -> str:
    """What the clip *is*. M5's render reads it to know the audio changed."""
    return canonical_hash({"kind": "audio_clip", "asset_sha256": asset_sha256})


def casting_hash_for_shot(shot: Shot) -> str:
    """The part of a shot the casting pass is actually told about.

    Exactly the three fields build_casting_messages() prints -- and this
    projection is not a nicety, it is what makes the job cacheable at all.
    The pass *writes* `speaker_character_id`, which is inside the shot's
    content_hash; keying on the whole content_hash therefore meant the job
    invalidated its own idempotency key the moment it succeeded, and every
    later press of the button charged the user again for an answer that had
    not changed. Found by the cache-hit test, which is the only thing that
    could have found it.

    The same narrow-projection rule as visual_hash_for_shot and
    speech_hash_for_shot, for the third time. Widen it the day
    build_casting_messages sends another field.
    """
    return canonical_hash(
        {
            "kind": "shot_casting",
            "image_prompt": shot.image_prompt,
            "camera": shot.camera.value,
            "speech_text": shot.speech_text,
        }
    )


def casting_hash_for_character(character: Character) -> str:
    """The part of a character the casting pass is told about.

    Name, role and description -- what build_casting_messages prints, and
    nothing else. Notably not `appearance`: the casting model is deciding
    who is in a frame, not drawing them, so redescribing somebody's face
    must not re-run and re-charge the whole mapping.
    """
    return canonical_hash(
        {
            "kind": "character_casting",
            "name": character.name,
            "role": character.role.value,
            "description": character.description,
        }
    )


def shot_characters_input_hash(shot_casting_hashes: list[str], cast_hash: str) -> str:
    """What would produce this shot/character mapping right now.

    Two parents, both as content: the shots being cast, and the cast being
    cast from. Sorted, because the mapping does not depend on the order the
    rows came back in and a hash that did would go stale on a query-plan
    change.
    """
    return canonical_hash(
        {
            "kind": "shot_characters",
            "shot_casting_hashes": sorted(shot_casting_hashes),
            "cast_hash": cast_hash,
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "params": CASTING_PARAMS,
        }
    )


def cast_hash(character_casting_hashes: list[str]) -> str:
    """One value standing for "this cast, as the casting pass reads it"."""
    return canonical_hash(
        {"kind": "cast", "character_casting_hashes": sorted(character_casting_hashes)}
    )


def shot_image_input_hash(shot_visual_hash: str, ref_content_hashes: list[str]) -> str:
    """What would produce this still right now (M4-D2).

    The first multi-parent hash in the system. Spec 4.1 is explicit that it
    includes the content_hash of the shot it depicts *and* of every
    character_image it used as a reference -- that link is what catches
    "the character changed", the case that otherwise silently ships a film
    where a face changes halfway through.

    Sorted, so the order the sheets happened to be loaded in cannot change
    the hash and charge a user to redraw an identical picture.

    Reads the *image* provider settings, not the LLM's: changing LLM_MODEL
    must not invalidate artwork no language model produced.
    """
    return canonical_hash(
        {
            "kind": "shot_image",
            "shot_visual_hash": shot_visual_hash,
            "ref_content_hashes": sorted(ref_content_hashes),
            "provider": settings.image_provider,
            "model": settings.image_model,
            "params": STILL_PARAMS,
        }
    )


def audio_clip_input_hash(shot_speech_hash: str, kind: AudioKind) -> str:
    """What would produce this clip right now.

    Reads the *speech* provider settings. Three adapters now, three
    independent invalidation domains: switching image models must not
    re-record anything, and switching voices must not redraw anything.
    """
    return canonical_hash(
        {
            "kind": "audio_clip",
            "audio_kind": kind.value,
            "shot_speech_hash": shot_speech_hash,
            "provider": settings.speech_provider,
            "model": settings.speech_model,
            "params": SPEECH_PARAMS,
        }
    )

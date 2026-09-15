"""The M4 hash rules.

The two things this milestone gets wrong silently and expensively are a
hash that is too wide (the user pays to redraw an identical picture) and a
hash that is too narrow (an edit never invalidates the artwork it changed).
Every test here pins one or the other.
"""

import uuid
from decimal import Decimal

import pytest

from framemeld.config import settings
from framemeld.models.script import CameraAngle, DialogueKind, Shot
from framemeld.models.shots import AudioKind
from framemeld.shots.hashes import (
    audio_clip_input_hash,
    cast_hash,
    content_hash_for_audio_clip,
    content_hash_for_shot_image,
    shot_characters_input_hash,
    shot_image_input_hash,
    speech_hash_for_shot,
    visual_hash_for_shot,
)


def _shot(**overrides) -> Shot:
    fields = {
        "id": uuid.uuid4(),
        "scene_id": uuid.uuid4(),
        "order_index": 0,
        "image_prompt": "A woman reads a letter by a window.",
        "motion_prompt": "Slow push in.",
        "duration_sec": Decimal("4.00"),
        "camera": CameraAngle.MEDIUM,
        "dialogue_kind": DialogueKind.NARRATION,
        "speech_text": "She had known for years.",
        "speaker_character_id": None,
    }
    fields.update(overrides)
    return Shot(**fields)


# --- the visual projection ------------------------------------------------


def test_the_image_prompt_is_in_the_visual_hash():
    before = visual_hash_for_shot(_shot())
    after = visual_hash_for_shot(_shot(image_prompt="A man burns a letter."))
    assert before != after


def test_the_camera_is_in_the_visual_hash():
    before = visual_hash_for_shot(_shot())
    after = visual_hash_for_shot(_shot(camera=CameraAngle.CLOSE))
    assert before != after


@pytest.mark.parametrize(
    "field,value",
    [
        ("motion_prompt", "Hold still."),
        ("duration_sec", Decimal("2.50")),
        ("speech_text", "A completely different line."),
        ("dialogue_kind", DialogueKind.OFF_SCREEN),
        ("speaker_character_id", uuid.uuid4()),
        ("order_index", 7),
    ],
)
def test_nothing_the_still_prompt_never_reads_is_in_the_visual_hash(field, value):
    """M4-D2, and the single most valuable rule in this file.

    still_prompt() is built from image_prompt and camera and nothing else,
    so an edit to any of these must leave the picture fresh. This is M3's
    description-versus-appearance lesson, one artifact further down.
    """
    assert visual_hash_for_shot(_shot()) == visual_hash_for_shot(_shot(**{field: value}))


# --- the speech projection ------------------------------------------------


def test_the_line_and_the_voice_are_in_the_speech_hash():
    base = speech_hash_for_shot(_shot(), "Kore")
    assert base != speech_hash_for_shot(_shot(speech_text="Something else"), "Kore")
    assert base != speech_hash_for_shot(_shot(), "Puck")
    assert base != speech_hash_for_shot(_shot(dialogue_kind=DialogueKind.OFF_SCREEN), "Kore")


def test_the_image_prompt_is_not_in_the_speech_hash():
    """The other half of M4-D3: redrawing a frame must not re-record it."""
    assert speech_hash_for_shot(_shot(), "Kore") == speech_hash_for_shot(
        _shot(image_prompt="A completely different frame.", camera=CameraAngle.WIDE), "Kore"
    )


def test_the_two_projections_are_disjoint():
    """Editing one half must move exactly one hash, in both directions."""
    visual_only = _shot(image_prompt="A door closes.")
    speech_only = _shot(speech_text="Nobody came back.")

    assert visual_hash_for_shot(_shot()) != visual_hash_for_shot(visual_only)
    assert speech_hash_for_shot(_shot(), "Kore") == speech_hash_for_shot(visual_only, "Kore")

    assert speech_hash_for_shot(_shot(), "Kore") != speech_hash_for_shot(speech_only, "Kore")
    assert visual_hash_for_shot(_shot()) == visual_hash_for_shot(speech_only)


# --- the multi-parent hash ------------------------------------------------


def test_a_still_keys_on_every_reference():
    """Spec 4.1: the still's input_hash includes every referenced sheet's
    content_hash. A sheet that is redrawn moves its content_hash, and this
    is the link that makes the still notice."""
    base = shot_image_input_hash("visual", ["sheet-a", "sheet-b"])
    assert base != shot_image_input_hash("visual", ["sheet-a", "sheet-b-redrawn"])


def test_adding_a_character_changes_the_still_hash():
    assert shot_image_input_hash("visual", ["sheet-a"]) != shot_image_input_hash(
        "visual", ["sheet-a", "sheet-b"]
    )


def test_the_reference_order_does_not_change_the_still_hash():
    """Sorted, deliberately. The order sheets come back in is a query-plan
    detail, and a hash that depended on it would charge a user to redraw an
    identical picture the first time Postgres changed its mind."""
    assert shot_image_input_hash("visual", ["b", "a"]) == shot_image_input_hash(
        "visual", ["a", "b"]
    )


def test_a_still_with_no_references_still_hashes():
    """Inserts and establishing shots have nobody in them, which is a
    correct answer rather than a special case."""
    assert shot_image_input_hash("visual", []) != shot_image_input_hash("other", [])


def test_the_still_hash_reads_the_image_provider_not_the_llm(monkeypatch):
    base = shot_image_input_hash("visual", ["a"])

    monkeypatch.setattr(settings, "llm_model", "some-other-llm")
    assert shot_image_input_hash("visual", ["a"]) == base, (
        "changing LLM_MODEL must not invalidate artwork no language model produced"
    )

    monkeypatch.setattr(settings, "image_model", "some-other-image-model")
    assert shot_image_input_hash("visual", ["a"]) != base


def test_the_audio_hash_reads_the_speech_provider(monkeypatch):
    base = audio_clip_input_hash("speech", AudioKind.NARRATION)

    monkeypatch.setattr(settings, "image_model", "some-other-image-model")
    assert audio_clip_input_hash("speech", AudioKind.NARRATION) == base, (
        "switching image models must not re-record anything"
    )

    monkeypatch.setattr(settings, "speech_model", "some-other-tts")
    assert audio_clip_input_hash("speech", AudioKind.NARRATION) != base


def test_narration_and_dialogue_hash_differently():
    assert audio_clip_input_hash("speech", AudioKind.NARRATION) != audio_clip_input_hash(
        "speech", AudioKind.DIALOGUE
    )


# --- the casting hash -----------------------------------------------------


def test_the_casting_hash_has_two_parents():
    base = shot_characters_input_hash(["shot-a"], cast_hash(["char-a"]))
    assert base != shot_characters_input_hash(["shot-b"], cast_hash(["char-a"]))
    assert base != shot_characters_input_hash(["shot-a"], cast_hash(["char-b"]))


def test_the_casting_hash_ignores_row_order():
    assert shot_characters_input_hash(["b", "a"], cast_hash(["y", "x"])) == (
        shot_characters_input_hash(["a", "b"], cast_hash(["x", "y"]))
    )


# --- content hashes are labelled -----------------------------------------


def test_a_still_and_a_clip_from_identical_bytes_hash_differently():
    """Every content hash is wrapped in a labelled payload, so a hash from
    one table can never be mistaken for one from another."""
    assert content_hash_for_shot_image("abc") != content_hash_for_audio_clip("abc")


# --- the casting projection ----------------------------------------------


def _character(**overrides):
    from framemeld.models.cast import Character, CharacterRole

    fields = {
        "id": uuid.uuid4(),
        "meld_id": uuid.uuid4(),
        "order_index": 0,
        "name": "Elena",
        "role": CharacterRole.LEAD,
        "description": "Finds the letter.",
        "appearance": "Silver hair pinned back.",
        "voice_id": "Kore",
        "content_hash": "c",
        "input_hash": "i",
    }
    fields.update(overrides)
    return Character(**fields)


def test_the_speaker_is_not_in_the_casting_hash():
    """The bug the cache-hit test found, pinned so it cannot come back.

    The casting pass *writes* speaker_character_id. Keying it on a value it
    sets means the job invalidates its own idempotency key the moment it
    succeeds, and every later press of the button charges again for an
    answer that has not changed.
    """
    from framemeld.shots.hashes import casting_hash_for_shot

    assert casting_hash_for_shot(_shot()) == casting_hash_for_shot(
        _shot(speaker_character_id=uuid.uuid4())
    )


def test_what_the_casting_prompt_sends_is_in_the_casting_hash():
    from framemeld.shots.hashes import casting_hash_for_shot

    base = casting_hash_for_shot(_shot())
    assert base != casting_hash_for_shot(_shot(image_prompt="Something else"))
    assert base != casting_hash_for_shot(_shot(camera=CameraAngle.INSERT))
    assert base != casting_hash_for_shot(_shot(speech_text="A different line"))


def test_an_appearance_edit_does_not_re_run_the_casting_pass():
    """The casting model decides who is in a frame, not what they look
    like, so redescribing a face must not re-charge the whole mapping."""
    from framemeld.shots.hashes import casting_hash_for_character

    assert casting_hash_for_character(_character()) == casting_hash_for_character(
        _character(appearance="Completely different", voice_id="Puck")
    )


def test_a_rename_does_re_run_the_casting_pass():
    """The name is what the model matches on, so it is a real dependency."""
    from framemeld.shots.hashes import casting_hash_for_character

    assert casting_hash_for_character(_character()) != casting_hash_for_character(
        _character(name="Elodie")
    )

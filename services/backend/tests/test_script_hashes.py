import uuid
from decimal import Decimal

import pytest

from framemeld.config import settings
from framemeld.models.script import CameraAngle, DialogueKind, Shot
from framemeld.script.hashes import (
    content_hash_for_scene,
    content_hash_for_shot,
    content_hash_for_story,
    script_input_hash,
)


def _shot(**overrides) -> Shot:
    fields = {
        "order_index": 0,
        "image_prompt": "A woman reads a letter by lamplight.",
        "motion_prompt": "Slow push in.",
        "duration_sec": Decimal("4.50"),
        "camera": CameraAngle.MEDIUM,
        "dialogue_kind": DialogueKind.NARRATION,
        "speech_text": "She had not expected an answer.",
        "speaker_character_id": None,
    }
    return Shot(**(fields | overrides))


def test_a_story_hash_is_stable():
    assert content_hash_for_story("once upon a time") == content_hash_for_story("once upon a time")
    assert content_hash_for_story("a") != content_hash_for_story("b")


def test_duration_is_hashed_at_the_column_scale():
    """Decimal("4.5") and Decimal("4.50") are equal but str() differently,
    and the column is Numeric(4, 2). Without normalisation a shot hashed
    before its flush and the same shot hashed after reloading would
    disagree, so every shot would look edited the moment it was read back."""
    assert content_hash_for_shot(_shot(duration_sec=Decimal("4.5"))) == content_hash_for_shot(
        _shot(duration_sec=Decimal("4.50"))
    )


def test_duration_still_distinguishes_real_differences():
    assert content_hash_for_shot(_shot(duration_sec=Decimal("4.50"))) != content_hash_for_shot(
        _shot(duration_sec=Decimal("4.51"))
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("order_index", 1),
        ("image_prompt", "Something else entirely."),
        ("motion_prompt", "Hold still."),
        ("camera", CameraAngle.CLOSE),
        ("dialogue_kind", DialogueKind.OFF_SCREEN),
        ("speech_text", "Different words."),
        ("speaker_character_id", uuid.uuid4()),
    ],
)
def test_every_editable_shot_field_is_in_the_hash(field, value):
    """A field left out of the hash is a field whose edit never invalidates
    the image generated from it -- a silent wrong render in M4, traced back
    to this function."""
    assert content_hash_for_shot(_shot()) != content_hash_for_shot(_shot(**{field: value}))


def test_a_scene_hash_covers_its_own_fields_only(db_scene_factory):
    scene = db_scene_factory()
    baseline = content_hash_for_scene(scene)
    scene.heading = "EXT. ROAD - DAY"
    assert content_hash_for_scene(scene) != baseline


def test_the_script_input_hash_follows_the_story():
    assert script_input_hash("a" * 64) != script_input_hash("b" * 64)


def test_the_script_input_hash_follows_the_configured_model(monkeypatch):
    """D2, made explicit: swapping the model marks every script stale,
    because regenerating really would produce something different."""
    before = script_input_hash("a" * 64)
    monkeypatch.setattr(settings, "llm_model", "some/other-model")
    assert script_input_hash("a" * 64) != before


def test_the_script_input_hash_follows_the_prompt_version(monkeypatch):
    import framemeld.script.hashes as hashes

    before = script_input_hash("a" * 64)
    monkeypatch.setitem(hashes.SCRIPT_PARAMS, "prompt_version", 99)
    assert script_input_hash("a" * 64) != before

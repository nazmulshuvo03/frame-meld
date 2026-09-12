import uuid
from decimal import Decimal

import pytest

from framemeld.config import settings
from framemeld.models.script import CameraAngle, DialogueKind, Scene, Shot
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


def _scene(**overrides) -> Scene:
    fields = {
        "order_index": 0,
        "heading": "INT. KITCHEN - NIGHT",
        "summary": "Elena finds the letter.",
        "location": "Kitchen",
        "time_of_day": "night",
    }
    return Scene(**(fields | overrides))


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


def test_duration_rounds_the_way_the_column_does():
    """Postgres numeric(4,2) rounds half away from zero; Python's default is
    half-to-even. A shot stored as 4.01 must not hash as 4.00."""
    assert content_hash_for_shot(_shot(duration_sec=Decimal("4.005"))) == content_hash_for_shot(
        _shot(duration_sec=Decimal("4.01"))
    )


SHOT_CASES = {
    "order_index": 1,
    "image_prompt": "Something else entirely.",
    "motion_prompt": "Hold still.",
    "duration_sec": Decimal("4.51"),
    "camera": CameraAngle.CLOSE,
    "dialogue_kind": DialogueKind.OFF_SCREEN,
    "speech_text": "Different words.",
    "speaker_character_id": uuid.uuid4(),
}

SCENE_CASES = {
    "order_index": 1,
    "heading": "EXT. ROAD - DAY",
    "summary": "Something else happens.",
    "location": "A roadside",
    "time_of_day": "dawn",
}

# Identity, provenance and bookkeeping. Everything else on these tables is
# something a user can edit, and must therefore be in the content hash.
_NOT_EDITABLE = frozenset(
    {
        "id",
        "meld_id",
        "scene_id",
        "content_hash",
        "input_hash",
        "state",
        "created_at",
        "updated_at",
    }
)


def _editable_columns(model) -> set[str]:
    return {column.name for column in model.__table__.columns} - _NOT_EDITABLE


@pytest.mark.parametrize(("model", "cases"), [(Shot, SHOT_CASES), (Scene, SCENE_CASES)])
def test_the_case_lists_cover_every_editable_column(model, cases):
    """Guards the lists below against the model itself.

    A column added in a later milestone would otherwise slip out of the
    content hash unnoticed — and a field missing from the hash is a field
    whose edit never invalidates what was generated from it, which surfaces
    as a wrong render three milestones from here.
    """
    assert _editable_columns(model) == set(cases)


@pytest.mark.parametrize(("field", "value"), sorted(SHOT_CASES.items()))
def test_every_editable_shot_field_is_in_the_hash(field, value):
    """A field left out of the hash is a field whose edit never invalidates
    the image generated from it -- a silent wrong render in M4, traced back
    to this function."""
    assert content_hash_for_shot(_shot()) != content_hash_for_shot(_shot(**{field: value}))


@pytest.mark.parametrize(("field", "value"), sorted(SCENE_CASES.items()))
def test_every_editable_scene_field_is_in_the_hash(field, value):
    assert content_hash_for_scene(_scene()) != content_hash_for_scene(_scene(**{field: value}))


def test_the_script_input_hash_follows_the_story():
    assert script_input_hash("a" * 64) != script_input_hash("b" * 64)


def test_the_script_input_hash_follows_the_configured_model(monkeypatch):
    """D2, made explicit: swapping the model marks every script stale,
    because regenerating really would produce something different."""
    before = script_input_hash("a" * 64)
    monkeypatch.setattr(settings, "llm_model", "some/other-model")
    assert script_input_hash("a" * 64) != before


def test_the_script_input_hash_follows_the_configured_provider(monkeypatch):
    before = script_input_hash("a" * 64)
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    assert script_input_hash("a" * 64) != before


def test_the_script_input_hash_follows_the_prompt_version(monkeypatch):
    import framemeld.script.hashes as hashes

    before = script_input_hash("a" * 64)
    monkeypatch.setitem(hashes.SCRIPT_PARAMS, "prompt_version", 99)
    assert script_input_hash("a" * 64) != before

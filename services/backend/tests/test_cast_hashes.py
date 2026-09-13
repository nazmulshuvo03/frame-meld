import pytest

from framemeld.cast.hashes import (
    character_image_input_hash,
    characters_input_hash,
    content_hash_for_character,
    content_hash_for_character_image,
)
from framemeld.config import settings
from framemeld.models.cast import Character, CharacterImageKind, CharacterRole


def _character(**overrides) -> Character:
    fields = {
        "order_index": 0,
        "name": "Elena",
        "role": CharacterRole.LEAD,
        "description": "The letter's recipient.",
        "appearance": "A woman in her sixties, silver hair pinned back.",
        "voice_id": None,
    } | overrides
    return Character(**fields)


def test_editing_the_appearance_changes_the_content_hash():
    before = content_hash_for_character(_character())
    after = content_hash_for_character(_character(appearance="A woman in her thirties."))
    assert before != after


def test_reordering_a_character_does_not_change_their_content_hash():
    """Dragging someone up the cast list must not invalidate -- and charge
    for -- an identical picture."""
    assert content_hash_for_character(_character(order_index=0)) == content_hash_for_character(
        _character(order_index=5)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "Elena Vasquez"),
        ("role", CharacterRole.BACKGROUND),
        ("description", "Someone else entirely."),
        ("voice_id", "af_heart"),
    ],
)
def test_every_hashed_field_moves_the_hash(field, value):
    """An omitted field is one whose edit would never invalidate the
    artwork generated from it -- which is a silent, expensive bug in M4."""
    assert content_hash_for_character(_character()) != content_hash_for_character(
        _character(**{field: value})
    )


def test_changing_the_image_model_invalidates_sheets(monkeypatch):
    before = character_image_input_hash("a" * 64, CharacterImageKind.SHEET)
    monkeypatch.setattr(settings, "image_model", "some-other-image-model")
    assert character_image_input_hash("a" * 64, CharacterImageKind.SHEET) != before


def test_changing_the_llm_model_does_not_invalidate_sheets(monkeypatch):
    """Artwork no language model produced must not go stale because the
    language model changed."""
    before = character_image_input_hash("a" * 64, CharacterImageKind.SHEET)
    monkeypatch.setattr(settings, "llm_model", "some-other-text-model")
    assert character_image_input_hash("a" * 64, CharacterImageKind.SHEET) == before


def test_changing_the_llm_model_does_invalidate_the_cast(monkeypatch):
    before = characters_input_hash("a" * 64)
    monkeypatch.setattr(settings, "llm_model", "some-other-text-model")
    assert characters_input_hash("a" * 64) != before


def test_an_image_content_hash_follows_its_bytes():
    assert content_hash_for_character_image("a" * 64) != content_hash_for_character_image("b" * 64)

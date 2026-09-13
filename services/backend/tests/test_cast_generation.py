import json
import uuid

import pytest
from sqlalchemy import select

from framemeld.cast.generation import (
    CHARACTERS_JSON_SCHEMA,
    apply_characters,
    parse_characters,
    sheet_prompt,
)
from framemeld.melds import service as melds_service
from framemeld.models.cast import MAX_CHARACTERS, Character, CharacterRole
from framemeld.models.meld import Meld, MeldStage
from framemeld.models.script import ArtifactState
from framemeld.models.user import User
from framemeld.providers.base import ProviderResponseError


def _payload(count: int = 2) -> str:
    return json.dumps(
        {
            "characters": [
                {
                    "name": f"Character {index}",
                    "role": "lead" if index == 0 else "supporting",
                    "description": "Does something.",
                    "appearance": "Tall, dark coat.",
                }
                for index in range(count)
            ]
        }
    )


def test_a_well_formed_cast_parses():
    draft = parse_characters(_payload())
    assert [c.name for c in draft.characters] == ["Character 0", "Character 1"]
    assert draft.characters[0].role is CharacterRole.LEAD


def test_prose_is_a_terminal_error():
    with pytest.raises(ProviderResponseError):
        parse_characters("Here are the characters you asked for!")


def test_an_empty_cast_is_a_terminal_error():
    with pytest.raises(ProviderResponseError):
        parse_characters(json.dumps({"characters": []}))


def test_an_unknown_role_falls_back_rather_than_losing_the_cast():
    payload = json.dumps(
        {
            "characters": [
                {
                    "name": "Elena",
                    "role": "protagonist",
                    "description": "d",
                    "appearance": "a",
                }
            ]
        }
    )
    assert parse_characters(payload).characters[0].role is CharacterRole.SUPPORTING


def test_an_overlong_cast_is_truncated_not_rejected():
    draft = parse_characters(_payload(MAX_CHARACTERS + 5))
    assert len(draft.characters) == MAX_CHARACTERS


def test_the_schema_bounds_the_cast_size():
    array = CHARACTERS_JSON_SCHEMA["schema"]["properties"]["characters"]
    assert array["maxItems"] == MAX_CHARACTERS


def test_the_sheet_prompt_uses_appearance_and_not_description():
    """The image model has not read the story. Feeding it plot produces an
    illustration of a scene instead of a reference of a person."""
    character = Character(
        order_index=0,
        name="Elena",
        role=CharacterRole.LEAD,
        description="SECRET-PLOT-DETAIL",
        appearance="Silver hair pinned back, grey wool cardigan.",
    )
    prompt = sheet_prompt(character)
    assert "Silver hair" in prompt
    assert "SECRET-PLOT-DETAIL" not in prompt


async def _meld(db):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    return await melds_service.create_meld(db, user.id, "T")


async def test_applying_a_cast_persists_it_and_advances_the_stage(db):
    meld = await _meld(db)
    count = await apply_characters(db, meld, parse_characters(_payload(3)), "b" * 64)
    db.expire_all()

    stored = (await db.execute(select(Character).order_by(Character.order_index))).scalars().all()
    assert count == 3
    assert [c.order_index for c in stored] == [0, 1, 2]
    assert all(c.state is ArtifactState.FRESH for c in stored)
    assert all(c.content_hash for c in stored)
    # Re-read rather than touching the expired instance: a refresh on an
    # expired attribute would be sync IO inside an async session.
    assert (await db.execute(select(Meld))).scalar_one().stage is MeldStage.CAST


async def test_regenerating_replaces_rather_than_appends(db):
    meld = await _meld(db)
    await apply_characters(db, meld, parse_characters(_payload(3)), "b" * 64)
    await apply_characters(db, meld, parse_characters(_payload(2)), "c" * 64)
    db.expire_all()

    assert len((await db.execute(select(Character))).scalars().all()) == 2

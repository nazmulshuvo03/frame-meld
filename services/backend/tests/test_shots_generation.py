import uuid
from decimal import Decimal

import pytest

from framemeld.credits.service import grant
from framemeld.melds import service as melds_service
from framemeld.models.cast import Character, CharacterRole
from framemeld.models.meld import MeldStage
from framemeld.models.script import CameraAngle, DialogueKind, Scene, Shot
from framemeld.models.shots import ShotCharacter
from framemeld.models.user import User
from framemeld.providers.base import ProviderResponseError
from framemeld.providers.stub import StubLLMProvider
from framemeld.shots.generation import (
    SHOT_CHARACTERS_JSON_SCHEMA,
    apply_shot_characters,
    build_casting_messages,
    parse_shot_characters,
    shots_in_film_order,
    still_prompt,
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
        "dialogue_kind": DialogueKind.NONE,
        "speech_text": "",
        "speaker_character_id": None,
        "content_hash": "c",
        "input_hash": "i",
    }
    fields.update(overrides)
    return Shot(**fields)


def _character(name: str, index: int = 0) -> Character:
    return Character(
        id=uuid.uuid4(),
        meld_id=uuid.uuid4(),
        order_index=index,
        name=name,
        role=CharacterRole.LEAD if index == 0 else CharacterRole.SUPPORTING,
        description=f"{name} does things.",
        appearance=f"{name} looks like something.",
        content_hash="c",
        input_hash="i",
    )


# --- the prompt -----------------------------------------------------------


def test_the_casting_message_numbers_the_shots():
    _, user = build_casting_messages(
        [_character("Elena"), _character("Marcus", 1)],
        [_shot(image_prompt="One"), _shot(image_prompt="Two")],
    )
    assert "- Elena (lead)" in user
    assert "1. [medium] One" in user
    assert "2. [medium] Two" in user


def test_the_still_prompt_names_its_references_in_order():
    """Reference images arrive as unlabelled bytes, so the only thing that
    tells the model which face belongs to whom is this sentence, in this
    order."""
    prompt = still_prompt(_shot(), ["Elena", "Marcus"])
    assert "in order: Elena, Marcus" in prompt
    assert prompt.index("Elena") < prompt.index("Marcus")


def test_the_still_prompt_omits_the_reference_sentence_when_there_are_none():
    assert "reference images" not in still_prompt(_shot(), [])


def test_the_still_prompt_carries_the_camera_as_direction():
    assert "Close-up" in still_prompt(_shot(camera=CameraAngle.CLOSE), [])
    assert "Wide shot" in still_prompt(_shot(camera=CameraAngle.WIDE), [])


def test_the_still_prompt_reads_only_what_the_visual_hash_covers():
    """If these two ever disagree, the hash describes inputs the prompt
    never used and an edit changes the picture without invalidating it."""
    a = still_prompt(_shot(), [])
    b = still_prompt(
        _shot(motion_prompt="different", speech_text="different", duration_sec=Decimal("1.00")), []
    )
    assert a == b


# --- the parser -----------------------------------------------------------


def test_prose_instead_of_json_is_terminal():
    with pytest.raises(ProviderResponseError):
        parse_shot_characters("Sure! Here is who is in each shot.")


def test_the_wrong_shape_is_terminal():
    with pytest.raises(ProviderResponseError):
        parse_shot_characters('{"mapping": []}')


def test_a_bare_string_where_a_list_belongs_is_accepted():
    """The right answer in the wrong container costs the field's shape, not
    the whole paid mapping."""
    draft = parse_shot_characters('{"shots":[{"shot":1,"characters":"Elena","speaker":""}]}')
    assert draft.shots[0].characters == ["Elena"]


def test_an_empty_mapping_is_not_an_error():
    """A film of inserts and landscapes has nobody in any shot, which is a
    correct answer rather than a failure."""
    assert parse_shot_characters('{"shots":[]}').shots == []


# --- the applier ----------------------------------------------------------


async def _meld_with_shots(db, shot_kwargs: list[dict] | None = None):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")
    await grant(db, user.id, 500, "welcome")

    scene = Scene(meld_id=meld.id, order_index=0, content_hash="c", input_hash="i")
    db.add(scene)
    await db.flush()
    shots = []
    for index, extra in enumerate(shot_kwargs or [{}, {}]):
        shot = _shot(scene_id=scene.id, order_index=index, **extra)
        db.add(shot)
        shots.append(shot)
    await db.flush()
    return meld, shots


async def test_applying_writes_the_join_rows_and_the_speaker(db):
    meld, shots = await _meld_with_shots(db)
    elena, marcus = _character("Elena"), _character("Marcus", 1)
    for character in (elena, marcus):
        character.meld_id = meld.id
        db.add(character)
    await db.flush()

    draft = parse_shot_characters(
        '{"shots":['
        '{"shot":1,"characters":["Elena","Marcus"],"speaker":"Elena"},'
        '{"shot":2,"characters":[],"speaker":""}'
        "]}"
    )
    written = await apply_shot_characters(db, meld, [elena, marcus], shots, draft)

    assert written == 2
    assert shots[0].speaker_character_id == elena.id
    assert shots[1].speaker_character_id is None
    assert meld.stage is MeldStage.SHOTS


async def test_the_speaker_moves_the_shots_content_hash(db):
    """speaker_character_id is inside content_hash_for_shot, and this pass
    is the only writer of it that is not the user."""
    meld, shots = await _meld_with_shots(db)
    elena = _character("Elena")
    elena.meld_id = meld.id
    db.add(elena)
    await db.flush()

    before = shots[0].content_hash
    await apply_shot_characters(
        db,
        meld,
        [elena],
        shots,
        parse_shot_characters('{"shots":[{"shot":1,"characters":[],"speaker":"Elena"}]}'),
    )
    assert shots[0].content_hash != before


async def test_an_invented_name_is_dropped_rather_than_failing_the_job(db):
    meld, shots = await _meld_with_shots(db)
    elena = _character("Elena")
    elena.meld_id = meld.id
    db.add(elena)
    await db.flush()

    written = await apply_shot_characters(
        db,
        meld,
        [elena],
        shots,
        parse_shot_characters(
            '{"shots":[{"shot":1,"characters":["Elena","Nobody At All"],"speaker":""}]}'
        ),
    )
    assert written == 1


async def test_a_name_is_matched_case_insensitively(db):
    meld, shots = await _meld_with_shots(db)
    elena = _character("Elena")
    elena.meld_id = meld.id
    db.add(elena)
    await db.flush()

    written = await apply_shot_characters(
        db,
        meld,
        [elena],
        shots,
        parse_shot_characters('{"shots":[{"shot":1,"characters":["  elena "],"speaker":""}]}'),
    )
    assert written == 1


async def test_a_repeated_name_does_not_violate_the_primary_key(db):
    meld, shots = await _meld_with_shots(db)
    elena = _character("Elena")
    elena.meld_id = meld.id
    db.add(elena)
    await db.flush()

    written = await apply_shot_characters(
        db,
        meld,
        [elena],
        shots,
        parse_shot_characters('{"shots":[{"shot":1,"characters":["Elena","Elena"],"speaker":""}]}'),
    )
    await db.flush()
    assert written == 1


async def test_applying_replaces_rather_than_merges(db):
    meld, shots = await _meld_with_shots(db)
    elena, marcus = _character("Elena"), _character("Marcus", 1)
    for character in (elena, marcus):
        character.meld_id = meld.id
        db.add(character)
    await db.flush()

    await apply_shot_characters(
        db,
        meld,
        [elena, marcus],
        shots,
        parse_shot_characters('{"shots":[{"shot":1,"characters":["Elena"],"speaker":""}]}'),
    )
    await apply_shot_characters(
        db,
        meld,
        [elena, marcus],
        shots,
        parse_shot_characters('{"shots":[{"shot":1,"characters":["Marcus"],"speaker":""}]}'),
    )
    rows = (await db.execute(ShotCharacter.__table__.select())).all()
    assert [row.character_id for row in rows] == [marcus.id]


async def test_shots_come_back_in_film_order_not_shot_order(db):
    """order_index is unique per scene, so ordering on it alone interleaves
    scene 2's first shot with scene 1's -- and the casting pass numbers
    shots from this list, so a wrong order maps the right characters onto
    the wrong frames."""
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")

    expected = []
    for scene_index in range(2):
        scene = Scene(meld_id=meld.id, order_index=scene_index, content_hash="c", input_hash="i")
        db.add(scene)
        await db.flush()
        for shot_index in range(2):
            shot = _shot(
                scene_id=scene.id,
                order_index=shot_index,
                image_prompt=f"s{scene_index}-{shot_index}",
            )
            db.add(shot)
            expected.append(shot.image_prompt)
    await db.flush()

    ordered = await shots_in_film_order(db, meld.id)
    assert [shot.image_prompt for shot in ordered] == expected


# --- the stub -------------------------------------------------------------


async def test_the_stub_answers_the_casting_schema(monkeypatch):
    """The M3 lesson: the stub only knew the script schema, so the Cast
    page's first action was broken under the default configuration. The
    same trap, one milestone later."""
    _, user = build_casting_messages(
        [_character("Elena"), _character("Marcus", 1)],
        [_shot(image_prompt=f"Shot {n}") for n in range(4)],
    )
    result = await StubLLMProvider().complete(
        system="s", user=user, json_schema=SHOT_CHARACTERS_JSON_SCHEMA
    )
    draft = parse_shot_characters(result.text or "")

    assert [entry.shot for entry in draft.shots] == [1, 2, 3, 4]
    assert all(name in {"Elena", "Marcus"} for entry in draft.shots for name in entry.characters)
    # Both branches the worker cares about are exercised by any run: a shot
    # with references and a shot without.
    assert any(entry.characters for entry in draft.shots)
    assert any(not entry.characters for entry in draft.shots)

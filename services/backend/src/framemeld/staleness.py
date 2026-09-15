import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.cast.hashes import (
    character_image_input_hash,
    characters_input_hash,
    visual_hash_for_character,
)
from framemeld.models.cast import Character, CharacterImage
from framemeld.models.meld import Meld, Story
from framemeld.models.script import ArtifactState, Scene, Shot
from framemeld.models.shots import AudioClip, AudioOwnerType, ShotCharacter, ShotImage
from framemeld.providers.speech.voices import DEFAULT_VOICE_ID
from framemeld.script.hashes import script_input_hash
from framemeld.shots.hashes import (
    audio_clip_input_hash,
    shot_image_input_hash,
    speech_hash_for_shot,
    visual_hash_for_shot,
)

# Only these two states are recomputed. A row that is `empty` has nothing to
# be stale about; a `generating` row is owned by a worker that is about to
# write it; a `failed` row's problem is not its inputs. (D5)
_RECOMPUTABLE = (ArtifactState.FRESH, ArtifactState.STALE)


@dataclass(frozen=True)
class StalenessReport:
    stale_scenes: int
    stale_shots: int
    stale_characters: int
    stale_sheets: int
    stale_stills: int
    stale_audio: int
    #: How many rows this call actually moved. Zero means the API can skip
    #: telling the user anything changed.
    changed: int


def _reconcile(row, expected: str, now: datetime) -> int:
    """Move one row between fresh and stale, and say whether it moved.

    The whole staleness rule, in one place: compare the hash the row was
    generated from against the hash its inputs would produce right now.
    Extracted rather than repeated per node type -- four copies of this
    comparison is four chances for one of them to drift.
    """
    if row.state not in _RECOMPUTABLE:
        return 0
    wanted = ArtifactState.FRESH if row.input_hash == expected else ArtifactState.STALE
    if row.state is wanted:
        return 0
    row.state = wanted
    row.updated_at = now
    return 1


async def recompute_staleness(db: DbSession, meld_id: uuid.UUID) -> StalenessReport:
    """Bring every generated row in one Meld back in line with its inputs.

    The one place staleness is decided, so there is one answer rather than
    one per call site. Called after any edit to an upstream artifact —
    in M2 that means the story.

    Rows are never regenerated here and never deleted: regeneration costs
    money, so it stays an explicit user action (spec §4.1). All this does is
    flip `state` between `fresh` and `stale`, in both directions.

    Loads the rows rather than issuing a bulk UPDATE. A Meld holds at most
    30 shots (spec §1), and the D5 filter plus the per-row counts are far
    clearer in Python than in SQL. Revisit if a Meld ever holds thousands.
    """
    meld = (
        await db.execute(select(Meld).where(Meld.id == meld_id, Meld.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if meld is None:
        return StalenessReport(0, 0, 0, 0, 0, 0, 0)

    story = (await db.execute(select(Story).where(Story.meld_id == meld_id))).scalar_one_or_none()
    if story is None:
        return StalenessReport(0, 0, 0, 0, 0, 0, 0)

    expected = script_input_hash(story.content_hash)

    scenes = list((await db.execute(select(Scene).where(Scene.meld_id == meld_id))).scalars())
    shots = list(
        (
            await db.execute(
                select(Shot).join(Scene, Shot.scene_id == Scene.id).where(Scene.meld_id == meld_id)
            )
        ).scalars()
    )

    characters = list(
        (await db.execute(select(Character).where(Character.meld_id == meld_id))).scalars()
    )
    images = list(
        (
            await db.execute(
                select(CharacterImage)
                .join(Character, CharacterImage.character_id == Character.id)
                .where(Character.meld_id == meld_id)
            )
        ).scalars()
    )

    now = datetime.now(UTC)
    changed = 0
    for row in (*scenes, *shots):
        changed += _reconcile(row, expected, now)

    expected_cast = characters_input_hash(story.content_hash)
    for character in characters:
        changed += _reconcile(character, expected_cast, now)

    # Each sheet is compared against its OWN character's current content --
    # not against one Meld-wide expectation, because that is what the
    # two-hash rule means here: editing one character's appearance must
    # invalidate that character's sheet and no one else's.
    by_id = {character.id: character for character in characters}
    for image in images:
        owner = by_id.get(image.character_id)
        if owner is None:
            continue
        changed += _reconcile(
            image, character_image_input_hash(visual_hash_for_character(owner), image.kind), now
        )

    # Stills are the first multi-parent node (M4-D2): each one is compared
    # against its own shot's visual hash *and* the current content_hash of
    # every sheet it referenced. `ref_character_image_ids` is the historical
    # record of what was sent, so a sheet that has since been redrawn moves
    # its content_hash and the still that used it correctly goes stale.
    shots_by_id = {shot.id: shot for shot in shots}
    sheet_content = {image.id: image.content_hash for image in images}
    stills = list(
        (
            await db.execute(
                select(ShotImage)
                .join(Shot, ShotImage.shot_id == Shot.id)
                .join(Scene, Shot.scene_id == Scene.id)
                .where(Scene.meld_id == meld_id)
            )
        ).scalars()
    )
    # What the shot references *now*, which is not the same as what the
    # still was drawn from: adding a character to a shot must invalidate a
    # still that was drawn without them.
    current_refs: dict[uuid.UUID, list[str]] = {}
    for shot_id, image_id in (
        await db.execute(
            select(ShotCharacter.shot_id, CharacterImage.id)
            .join(Character, ShotCharacter.character_id == Character.id)
            .join(CharacterImage, CharacterImage.character_id == Character.id)
            .where(Character.meld_id == meld_id)
        )
    ).all():
        content = sheet_content.get(image_id)
        if content:
            current_refs.setdefault(shot_id, []).append(content)

    for still in stills:
        shot = shots_by_id.get(still.shot_id)
        if shot is None:
            continue
        changed += _reconcile(
            still,
            shot_image_input_hash(visual_hash_for_shot(shot), current_refs.get(still.shot_id, [])),
            now,
        )

    clips = list(
        (
            await db.execute(
                select(AudioClip).where(
                    AudioClip.owner_type == AudioOwnerType.SHOT,
                    AudioClip.owner_id.in_(list(shots_by_id)),
                )
            )
        ).scalars()
    )
    # The clip's own voice_id, not the speaker's current one. Both are
    # compared: the hash covers the voice the clip was recorded in, and
    # `_voice_now` below is what it would be recorded in today, so changing
    # a character's voice invalidates their lines and nothing else.
    voices = {character.id: character.voice_id or DEFAULT_VOICE_ID for character in characters}
    for clip in clips:
        shot = shots_by_id.get(clip.owner_id)
        if shot is None:
            continue
        voice_now = (
            DEFAULT_VOICE_ID
            if shot.speaker_character_id is None
            else voices.get(shot.speaker_character_id, DEFAULT_VOICE_ID)
        )
        changed += _reconcile(
            clip, audio_clip_input_hash(speech_hash_for_shot(shot, voice_now), clip.kind), now
        )

    if changed:
        await db.flush()

    return StalenessReport(
        stale_scenes=sum(1 for scene in scenes if scene.state is ArtifactState.STALE),
        stale_shots=sum(1 for shot in shots if shot.state is ArtifactState.STALE),
        stale_characters=sum(1 for c in characters if c.state is ArtifactState.STALE),
        stale_sheets=sum(1 for image in images if image.state is ArtifactState.STALE),
        stale_stills=sum(1 for still in stills if still.state is ArtifactState.STALE),
        stale_audio=sum(1 for clip in clips if clip.state is ArtifactState.STALE),
        changed=changed,
    )

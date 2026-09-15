import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.models.cast import Character
from framemeld.models.meld import Meld, MeldStage
from framemeld.models.script import MAX_SHOTS, CameraAngle, Scene, Shot
from framemeld.models.shots import ShotCharacter
from framemeld.providers.base import ProviderResponseError
from framemeld.script.hashes import content_hash_for_shot

SHOT_CHARACTERS_JSON_SCHEMA: dict[str, Any] = {
    "name": "shot_characters",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["shots"],
        "properties": {
            "shots": {
                "type": "array",
                "maxItems": MAX_SHOTS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["shot", "characters", "speaker"],
                    "properties": {
                        # The 1-based number this pass printed next to the
                        # shot, not a uuid. A model asked to echo a uuid
                        # mistypes one character and the row is silently
                        # dropped; a small integer it cannot get subtly
                        # wrong.
                        "shot": {"type": "integer", "minimum": 1},
                        "characters": {"type": "array", "items": {"type": "string"}},
                        # "" rather than null for "nobody speaks": strict
                        # mode plus a nullable type is the combination
                        # OpenAI-style schemas handle worst.
                        "speaker": {"type": "string"},
                    },
                },
            }
        },
    },
}

_CASTING_SYSTEM = """\
You decide which characters are visible in each shot of a film, and who \
speaks over it. The user message lists the cast, then the shots.

Rules you must follow:
- `characters` lists only cast members a viewer would SEE in that frame. \
Someone mentioned, remembered, or talked about is not in the shot.
- Use the names exactly as the cast list spells them. Never invent a name.
- An empty list is a correct answer. Inserts, landscapes and object \
close-ups usually have nobody in them.
- `speaker` is the one cast member whose voice is heard over the shot, or \
an empty string when nobody speaks or the line is unattributed narration.
- A speaker need not be visible: a character can narrate a shot they are \
not in. Do not add them to `characters` just because they speak.
- Return one entry per shot, using the number printed beside it.
"""


def build_casting_messages(characters: list[Character], shots: list[Shot]) -> tuple[str, str]:
    """The cast and the shot list, numbered.

    Both halves are sent as their *current* text rather than as ids,
    because that is what the model can actually reason about -- and because
    the input_hash covers exactly those content hashes, so what is hashed
    and what is sent stay the same thing.
    """
    cast_lines = "\n".join(
        f"- {character.name} ({character.role.value}): {character.description}".rstrip(": ")
        for character in characters
    )
    shot_lines = "\n".join(
        f"{index + 1}. [{shot.camera.value}] {shot.image_prompt}"
        + (f"\n   spoken: {shot.speech_text}" if shot.speech_text.strip() else "")
        for index, shot in enumerate(shots)
    )
    return _CASTING_SYSTEM, f"CAST:\n{cast_lines}\n\nSHOTS:\n{shot_lines}\n"


class DraftShotCasting(BaseModel):
    shot: int = Field(ge=1)
    characters: list[str] = Field(default_factory=list)
    speaker: str = ""

    @field_validator("characters", mode="before")
    @classmethod
    def _listish(cls, value: object) -> object:
        """A model that answers with a bare string for a one-character shot
        has given the right answer in the wrong container. Costs this
        field's shape, not the whole mapping."""
        return [value] if isinstance(value, str) else value


class CastingDraft(BaseModel):
    shots: list[DraftShotCasting]


def parse_shot_characters(text: str) -> CastingDraft:
    """Turn a provider's response into a draft, or fail terminally.

    Every failure here is a ProviderResponseError, which is not retryable:
    a model that answered with prose once will answer with prose again, and
    each attempt costs. The user sees the message, so it says what happened
    rather than quoting the body at them.
    """
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProviderResponseError(
            "The model did not say who is in each shot. Try again."
        ) from exc

    try:
        return CastingDraft.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(
            "The model answered in an unexpected shape. Try again."
        ) from exc


async def apply_shot_characters(
    db: DbSession, meld: Meld, characters: list[Character], shots: list[Shot], draft: CastingDraft
) -> int:
    """Replace this Meld's shot/character mapping with the draft.

    Replace rather than merge, for the same reason apply_script and
    apply_characters do: a re-run is a new answer, and reconciling it with
    the old one would guess at an intent nobody expressed.

    Unknown names are dropped rather than failing the job. A model that
    invents one character out of thirty has produced a mostly-correct
    mapping, and discarding all of it would cost the user the whole pass;
    the join table's foreign key would refuse the row anyway, one statement
    later and far less clearly.
    """
    await db.execute(
        delete(ShotCharacter)
        .where(ShotCharacter.shot_id.in_([shot.id for shot in shots]))
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    # Case- and whitespace-insensitive, because the model is echoing names
    # back as prose. Matching exactly would silently drop "alice" for
    # "Alice" and leave a shot with nobody in it.
    by_name = {character.name.strip().casefold(): character for character in characters}
    by_number = {index + 1: shot for index, shot in enumerate(shots)}

    now = datetime.now(UTC)
    written = 0
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for entry in draft.shots:
        shot = by_number.get(entry.shot)
        if shot is None:
            continue

        for name in entry.characters:
            character = by_name.get(name.strip().casefold())
            # A duplicated name in one entry would violate the composite
            # primary key, which is a model formatting slip, not a reason
            # to fail a paid job.
            if character is None or (shot.id, character.id) in seen:
                continue
            seen.add((shot.id, character.id))
            db.add(ShotCharacter(shot_id=shot.id, character_id=character.id))
            written += 1

        speaker = by_name.get(entry.speaker.strip().casefold())
        speaker_id = speaker.id if speaker is not None else None
        if shot.speaker_character_id != speaker_id:
            shot.speaker_character_id = speaker_id
            # The shot's own content moved, so its hash must move with it.
            # Nothing keys on a shot's whole content_hash today, but leaving
            # it behind here would make every later reader of that column
            # wrong, and this is the only writer that is not the user.
            shot.content_hash = content_hash_for_shot(shot)
            shot.updated_at = now

    meld.stage = MeldStage.SHOTS
    meld.updated_at = now
    await db.flush()
    return written


#: One clause per camera value, so the enum the user picked reaches the
#: model as direction rather than as a word it has to interpret.
_CAMERA_FRAMING: dict[CameraAngle, str] = {
    CameraAngle.WIDE: "Wide shot: the full figures small within their surroundings.",
    CameraAngle.MEDIUM: "Medium shot: from roughly the waist up.",
    CameraAngle.CLOSE: "Close-up: the face fills most of the frame.",
    CameraAngle.INSERT: "Insert: a tight detail of an object, no full figures.",
    CameraAngle.ESTABLISHING: "Establishing shot: the whole location, figures distant or absent.",
}


def still_prompt(shot: Shot, named_refs: list[str]) -> str:
    """The image prompt for one shot's still.

    Built from `image_prompt` and `camera` and nothing else, which is
    exactly what visual_hash_for_shot covers -- the two must agree, or the
    hash describes inputs the prompt never used and an edit to one of them
    changes the picture without invalidating it.

    The reference sheets arrive as unlabelled bytes (M3-D12), so the prompt
    has to say which is which, in the order they were attached. Without that
    a two-character shot is a coin flip on who is wearing whose face, which
    is exactly the drift reference conditioning exists to prevent.
    """
    lines = [
        "A single film still. Cinematic lighting, photorealistic, sharp focus. "
        "No text, no watermark, no border, no collage, no character sheet layout."
    ]
    framing = _CAMERA_FRAMING.get(shot.camera)
    if framing:
        lines.append(framing)
    if named_refs:
        lines.append(
            "The attached reference images show, in order: "
            + ", ".join(named_refs)
            + ". Match their faces, hair and clothing exactly."
        )
    lines.append(shot.image_prompt.strip())
    return "\n\n".join(lines)


async def shots_in_film_order(db: DbSession, meld_id: uuid.UUID) -> list[Shot]:
    """Every shot in a Meld, in film order.

    Ordered by scene *then* shot, not by shot alone: `order_index` is
    unique per scene, so ordering on it alone interleaves scene 2's first
    shot with scene 1's. The casting pass numbers shots from this list and
    the model answers with those numbers, so a different order here would
    map the right characters onto the wrong frames.
    """
    result = await db.execute(
        select(Shot)
        .join(Scene, Shot.scene_id == Scene.id)
        .where(Scene.meld_id == meld_id)
        .order_by(Scene.order_index, Shot.order_index)
    )
    return list(result.scalars().all())

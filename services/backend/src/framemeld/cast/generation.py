import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.cast.hashes import content_hash_for_character
from framemeld.models.cast import MAX_CHARACTERS, Character, CharacterRole
from framemeld.models.meld import Meld, MeldStage
from framemeld.models.script import ArtifactState
from framemeld.providers.base import ProviderResponseError
from framemeld.providers.speech.voices import voice_for_index

CHARACTERS_JSON_SCHEMA: dict[str, Any] = {
    "name": "characters",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["characters"],
        "properties": {
            "characters": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CHARACTERS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "role", "description", "appearance"],
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string", "enum": [r.value for r in CharacterRole]},
                        "description": {"type": "string"},
                        "appearance": {"type": "string"},
                    },
                },
            }
        },
    },
}

_SYSTEM = """\
You are a casting director for a short AI-generated film. The user message \
is the story verbatim. List the characters who appear in it.

Rules you must follow:
- Only characters the story actually contains. Do not invent a cast.
- `description` is who they are: their part in the story, what they want.
- `appearance` is ONLY what a camera would see -- age, build, hair, skin, \
clothing, distinguishing features. It is sent on its own to an image model \
that has not read the story, so it must stand alone and must not refer back \
to the plot.
- Keep `appearance` specific and physical. "Sad-looking" is not a \
description of a face; "deep lines around the eyes, mouth set flat" is.
- Do not describe a scene, a background, or an action in `appearance`. It \
describes a person standing still.
- `role` is `lead` for characters the story follows, `supporting` for named \
characters with a part to play, `background` for everyone else.
"""


def build_character_messages(story_text: str) -> tuple[str, str]:
    """The user message is the story text, unwrapped and unpadded -- the
    same contract build_messages() established in M2, including that the
    stub treats an empty/whitespace user message as nothing to work from
    and refuses rather than inventing a cast."""
    return _SYSTEM, story_text


def _truncated(value: object, limit: int) -> object:
    return value[:limit] if isinstance(value, str) and len(value) > limit else value


class DraftCharacter(BaseModel):
    name: str = Field(min_length=1)
    role: CharacterRole = CharacterRole.SUPPORTING
    description: str = ""
    appearance: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _truncate_name(cls, value: object) -> object:
        # min_length=1 stays terminal: a nameless character is a missing
        # character. Overlong is different -- truncating keeps them.
        return _truncated(value, 120)

    @field_validator("description", "appearance", mode="before")
    @classmethod
    def _truncate_text(cls, value: object) -> object:
        return _truncated(value, 4000)

    @field_validator("role", mode="before")
    @classmethod
    def _known_role(cls, value: object) -> CharacterRole:
        """An invented role costs this field's fidelity, not the cast."""
        try:
            return CharacterRole(value)
        except ValueError:
            return CharacterRole.SUPPORTING


class CastDraft(BaseModel):
    characters: list[DraftCharacter]


def parse_characters(text: str) -> CastDraft:
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
            "The model did not return a cast list. Try generating again."
        ) from exc

    try:
        draft = CastDraft.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(
            "The model returned a cast list in an unexpected shape. Try generating again."
        ) from exc

    draft.characters = [c for c in draft.characters if c.name.strip()][:MAX_CHARACTERS]
    if not draft.characters:
        raise ProviderResponseError("The model found no characters. Try generating again.")
    return draft


async def apply_characters(db: DbSession, meld: Meld, draft: CastDraft, input_hash: str) -> int:
    """Replace the Meld's cast with this draft.

    Replace, not merge, exactly as apply_script does: a regenerated cast is
    a new cast, and matching new characters to old ones would guess at an
    intent the user has not expressed. Their edits are protected by
    staleness *never regenerating on its own*.

    The cascade takes character_images with the characters, so a regenerate
    discards artwork the user paid for. That is the same trade the script
    stage already makes and the UI must say so before the click -- see
    Task 15's confirmation copy.
    """
    await db.execute(
        delete(Character)
        .where(Character.meld_id == meld.id)
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    now = datetime.now(UTC)
    for index, drafted in enumerate(draft.characters):
        character = Character(
            id=uuid.uuid4(),
            meld_id=meld.id,
            order_index=index,
            name=drafted.name,
            role=drafted.role,
            description=drafted.description,
            appearance=drafted.appearance,
            # Seeded so a fresh cast can be heard immediately rather than
            # needing eight voices chosen first. Deterministic, because
            # voice_id is inside content_hash_for_character and a random
            # seed would give the same story a different hash on every
            # extraction. The user can change it; nothing else ever does.
            voice_id=voice_for_index(index),
            content_hash="",
            input_hash=input_hash,
            state=ArtifactState.FRESH,
            created_at=now,
            updated_at=now,
        )
        character.content_hash = content_hash_for_character(character)
        db.add(character)

    meld.stage = MeldStage.CAST
    meld.updated_at = now
    await db.flush()
    return len(draft.characters)


def sheet_prompt(character: Character) -> str:
    """The image prompt for one character's reference sheet.

    Built from `appearance` alone, never `description`: the image model has
    not read the story, and feeding it plot would produce an illustration
    of a scene rather than a neutral reference of a person. The framing
    clauses are fixed so that every sheet in a Meld is shot the same way --
    consistency between sheets is what makes them usable as references in
    M4.
    """
    return (
        "Character reference sheet. Full body, front view, neutral pose, "
        "plain light grey background, even studio lighting, photorealistic, "
        "sharp focus on the face. No text, no watermark, no border, no collage.\n\n"
        f"{character.appearance.strip()}"
    )

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.models.meld import Meld, MeldStage
from framemeld.models.script import (
    MAX_SHOT_SECONDS,
    MAX_SHOTS,
    ArtifactState,
    CameraAngle,
    DialogueKind,
    Scene,
    Shot,
)
from framemeld.models.shots import AudioClip, AudioOwnerType
from framemeld.providers.base import ProviderResponseError
from framemeld.script.hashes import content_hash_for_scene, content_hash_for_shot

# MAX_SHOTS lives in models/script.py so hashes.py can import it too (R1).
MIN_SHOT_SECONDS = Decimal("0.50")

SCRIPT_JSON_SCHEMA: dict[str, Any] = {
    "name": "script",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["heading", "summary", "location", "time_of_day", "shots"],
                    "properties": {
                        "heading": {"type": "string"},
                        "summary": {"type": "string"},
                        "location": {"type": "string"},
                        "time_of_day": {"type": "string"},
                        "shots": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 8,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "image_prompt",
                                    "motion_prompt",
                                    "duration_sec",
                                    "camera",
                                    "dialogue_kind",
                                    "speech_text",
                                ],
                                "properties": {
                                    "image_prompt": {"type": "string"},
                                    "motion_prompt": {"type": "string"},
                                    "duration_sec": {
                                        "type": "number",
                                        # float(): JSON Schema needs a number, and MIN_SHOT_SECONDS
                                        # / MAX_SHOT_SECONDS are Decimals -- imported rather than
                                        # restated, or a drifted copy would leave the input hash
                                        # describing bounds the schema no longer enforces.
                                        "minimum": float(MIN_SHOT_SECONDS),
                                        "maximum": float(MAX_SHOT_SECONDS),
                                    },
                                    "camera": {
                                        "type": "string",
                                        "enum": [c.value for c in CameraAngle],
                                    },
                                    "dialogue_kind": {
                                        "type": "string",
                                        "enum": [d.value for d in DialogueKind],
                                    },
                                    "speech_text": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        },
    },
}

_SYSTEM = """\
You are a shot-list editor for a short AI-generated film. The user message is \
the story verbatim; you turn it into a numbered list of scenes, each broken \
into shots.

Rules you must follow:
- Every shot lasts at most 5 seconds. This is a hard limit of the video model, \
not a stylistic preference.
- Every shot needs TWO prompts: `image_prompt` describes the frame as a still \
image, `motion_prompt` describes only what moves within it.
- `image_prompt` must be self-contained. It is sent to an image model that has \
not read the story, so name what is visible rather than referring back.
- Prefer narration, reaction shots and off-screen dialogue. Characters' mouths \
cannot be synchronised to speech, so on-screen dialogue looks wrong. Use \
`dialogue_kind: "on_screen"` only when nothing else will carry the moment.
- `dialogue_kind` and `speech_text` are one decision, not two. A shot whose \
`dialogue_kind` is anything but `"none"` MUST carry the words in \
`speech_text`; a shot with nothing heard over it MUST be \
`dialogue_kind: "none"`. A dialogue kind with an empty `speech_text` is a \
silent shot wearing a label, and is never what was meant.
- Unless the story is wordless, carry it with narration: most shots that \
advance the story should have `dialogue_kind: "narration"` and the line to \
be read in `speech_text`. A film with no `speech_text` anywhere is a silent \
film.
- Aim for 10 to 30 shots in total. Do not pad a short story.
"""


def build_messages(story_text: str) -> tuple[str, str]:
    """The user message is the story text, unwrapped and unpadded.

    Not "STORY:\\n{story}" or similar framing: the stub (and any future
    adapter) treats an empty/whitespace user message as nothing to work
    from and refuses rather than inventing a script (see
    ProviderRefusedError in stub.py, and test_an_empty_story_is_refused in
    test_providers_stub.py, which calls .complete(user=story_text) the same
    way). Padding the user message with instruction text would make that
    check never fire, and would hand the stub's paragraph-splitting an
    extra "paragraph" that is not part of the story at all.

    A consequence: untrusted story text now reaches the model with no
    boundary marker separating it from instructions. If a delimiter is
    ever wanted, adapt the stub's paragraph heuristic to match -- not this
    prompt -- since the stub's contract is what now constrains its shape.
    """
    return _SYSTEM, story_text


def _truncated(value: object, limit: int) -> object:
    """Shared by the string-overrun validators below: an over-long value is
    truncated rather than rejected, the same clamp-not-reject policy the
    numeric and enum fields follow -- a 250-character heading failing
    ScriptDraft.model_validate would lose the entire script over one field,
    which is the opposite of that rule. Non-strings pass through unchanged
    so pydantic's own type error still fires; this only trims length."""
    return value[:limit] if isinstance(value, str) and len(value) > limit else value


class DraftShot(BaseModel):
    image_prompt: str = Field(min_length=1)
    motion_prompt: str = Field(default="")
    duration_sec: Decimal
    camera: CameraAngle = CameraAngle.MEDIUM
    dialogue_kind: DialogueKind = DialogueKind.NONE
    speech_text: str = Field(default="")

    @field_validator("image_prompt", mode="before")
    @classmethod
    def _truncate_image_prompt(cls, value: object) -> object:
        # min_length=1 stays terminal: an empty prompt is a missing shot,
        # not a fidelity loss. Overlong is different -- truncating keeps
        # the shot instead of losing the whole script over one long field.
        return _truncated(value, 4000)

    @field_validator("motion_prompt", mode="before")
    @classmethod
    def _truncate_motion_prompt(cls, value: object) -> object:
        return _truncated(value, 2000)

    @field_validator("speech_text", mode="before")
    @classmethod
    def _truncate_speech_text(cls, value: object) -> object:
        return _truncated(value, 4000)

    @field_validator("duration_sec", mode="before")
    @classmethod
    def _clamp_duration(cls, value: object) -> Decimal:
        """Clamped rather than rejected. Models ignore numeric bounds
        routinely, and losing a whole script over one bad number would be
        the wrong trade — while a value over 5 s would be rejected by the
        database CHECK anyway."""
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError):
            parsed = MIN_SHOT_SECONDS
        # NaN and the infinities survive Decimal(str(...)) — json.loads
        # accepts NaN/Infinity by default — and then raise InvalidOperation
        # from the comparison below. That is an ArithmeticError, not a
        # ProviderError, and would escape the worker's except clause
        # entirely if left unguarded here.
        if not parsed.is_finite():
            parsed = MIN_SHOT_SECONDS
        clamped = min(max(parsed, MIN_SHOT_SECONDS), MAX_SHOT_SECONDS)
        return clamped.quantize(Decimal("0.01"))

    @field_validator("camera", mode="before")
    @classmethod
    def _known_camera(cls, value: object) -> CameraAngle:
        """An invented angle costs this field's fidelity, not the script."""
        try:
            return CameraAngle(value)
        except ValueError:
            return CameraAngle.MEDIUM

    @field_validator("dialogue_kind", mode="before")
    @classmethod
    def _known_dialogue_kind(cls, value: object) -> DialogueKind:
        try:
            return DialogueKind(value)
        except ValueError:
            return DialogueKind.NONE

    @model_validator(mode="after")
    def _dialogue_and_speech_agree(self) -> "DraftShot":
        """A dialogue kind and the words are one fact, so make them one here.

        Probed 2026-09-20: Qwen3-8B returned all fourteen shots of a script
        as `dialogue_kind: "off_screen"` with `speech_text: ""`. Nothing
        rejected it -- both fields were individually valid -- and the result
        was a film the Shots stage renders with a speech field the user can
        see is empty on every single shot, and a Preview with no narration
        in it. Reconciled rather than rejected, on the same trade as
        `_clamp_duration`: one field's fidelity is worth less than the whole
        script.

        The prompt above now states the pairing as an invariant, which is
        what actually gets narration written. This is the guard for when a
        model ignores it, which is the normal case rather than the
        exception.
        """
        if not self.speech_text.strip():
            # Labelled but silent. The label is the part that is wrong.
            self.dialogue_kind = DialogueKind.NONE
        elif self.dialogue_kind is DialogueKind.NONE:
            # Words with nowhere to come from. Narration is what the prompt
            # asks for by default and the only kind that needs no speaker.
            self.dialogue_kind = DialogueKind.NARRATION
        return self


class DraftScene(BaseModel):
    heading: str = Field(default="")
    summary: str = Field(default="")
    location: str = Field(default="")
    time_of_day: str = Field(default="")
    shots: list[DraftShot] = Field(default_factory=list)

    @field_validator("heading", mode="before")
    @classmethod
    def _truncate_heading(cls, value: object) -> object:
        # heading/location are varchar(200) columns; summary is Text with
        # no DB-enforced width, but truncating it to the same bound this
        # field used to reject at keeps generated content reasonably sized.
        return _truncated(value, 200)

    @field_validator("location", mode="before")
    @classmethod
    def _truncate_location(cls, value: object) -> object:
        return _truncated(value, 200)

    @field_validator("time_of_day", mode="before")
    @classmethod
    def _truncate_time_of_day(cls, value: object) -> object:
        # varchar(50).
        return _truncated(value, 50)

    @field_validator("summary", mode="before")
    @classmethod
    def _truncate_summary(cls, value: object) -> object:
        return _truncated(value, 4000)


class ScriptDraft(BaseModel):
    scenes: list[DraftScene]


def parse_script(text: str) -> ScriptDraft:
    """Turn a provider's response into a draft, or fail terminally.

    Every failure here is a ProviderResponseError, which is not retryable:
    a model that answered with prose once will answer with prose again, and
    each attempt costs. The user sees the message, so it says what happened
    rather than quoting the body back at them.
    """
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProviderResponseError(
            "The model did not return a script. Try generating again."
        ) from exc

    try:
        draft = ScriptDraft.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(
            "The model returned a script in an unexpected shape. Try generating again."
        ) from exc

    # Drop empty scenes before the count check, so "one scene with no shots"
    # is treated as the empty script it is.
    draft.scenes = [scene for scene in draft.scenes if scene.shots]
    if not draft.scenes:
        raise ProviderResponseError("The model returned an empty script. Try generating again.")

    _truncate_to_max_shots(draft)
    return draft


def _truncate_to_max_shots(draft: ScriptDraft) -> None:
    """Keep the first MAX_SHOTS shots in document order and discard the
    rest, dropping any scene left empty. Truncating from the end keeps a
    coherent opening rather than an arbitrary sample."""
    remaining = MAX_SHOTS
    kept: list[DraftScene] = []
    for scene in draft.scenes:
        if remaining <= 0:
            break
        scene.shots = scene.shots[:remaining]
        remaining -= len(scene.shots)
        kept.append(scene)
    draft.scenes = kept


async def apply_script(
    db: DbSession, meld: Meld, draft: ScriptDraft, input_hash: str
) -> tuple[int, int]:
    """Replace the Meld's script with this draft.

    Replace, not merge: a regenerated script is a new script, and trying to
    match new shots to old ones would guess at an intent the user has not
    expressed. Their edits are protected by *not regenerating* — which is
    why staleness never regenerates on its own.
    """
    # Audio first, by hand. `audio_clips.owner_id` is polymorphic (spec
    # §5.3: a clip hangs from a shot or from the Meld), so it carries no
    # foreign key and nothing cascades it. Without this, regenerating a
    # script leaves every recording behind pointing at a shot id that no
    # longer resolves — rows nobody can reach and nobody will clean up.
    await db.execute(
        delete(AudioClip)
        .where(
            AudioClip.owner_type == AudioOwnerType.SHOT,
            AudioClip.owner_id.in_(
                select(Shot.id)
                .join(Scene, Shot.scene_id == Scene.id)
                .where(Scene.meld_id == meld.id)
            ),
        )
        .execution_options(synchronize_session=False)
    )

    # A bulk delete; Postgres cascades to shots, and from shots to
    # shot_images and shot_characters. synchronize_session=False because
    # nothing in this session should still be holding the old rows, and the
    # ORM's default strategy would issue a SELECT to find out.
    await db.execute(
        delete(Scene).where(Scene.meld_id == meld.id).execution_options(synchronize_session=False)
    )
    await db.flush()

    now = datetime.now(UTC)
    shot_count = 0

    for scene_index, draft_scene in enumerate(draft.scenes):
        scene = Scene(
            id=uuid.uuid4(),
            meld_id=meld.id,
            order_index=scene_index,
            heading=draft_scene.heading,
            summary=draft_scene.summary,
            location=draft_scene.location,
            time_of_day=draft_scene.time_of_day,
            content_hash="",
            input_hash=input_hash,
            state=ArtifactState.FRESH,
            created_at=now,
            updated_at=now,
        )
        scene.content_hash = content_hash_for_scene(scene)
        db.add(scene)

        for shot_index, draft_shot in enumerate(draft_scene.shots):
            shot = Shot(
                scene_id=scene.id,
                order_index=shot_index,
                image_prompt=draft_shot.image_prompt,
                motion_prompt=draft_shot.motion_prompt,
                duration_sec=draft_shot.duration_sec,
                camera=draft_shot.camera,
                dialogue_kind=draft_shot.dialogue_kind,
                speech_text=draft_shot.speech_text,
                content_hash="",
                input_hash=input_hash,
                state=ArtifactState.FRESH,
                created_at=now,
                updated_at=now,
            )
            shot.content_hash = content_hash_for_shot(shot)
            db.add(shot)
            shot_count += 1

    # The Meld has reached stage 2. Purely presentational — nothing gates on
    # stage — but the dashboard shows it, and a Meld with a script that still
    # says "story" is a lie the user can see.
    meld.stage = MeldStage.SCRIPT
    meld.updated_at = now
    await db.flush()
    return len(draft.scenes), shot_count

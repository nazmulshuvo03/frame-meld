import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from framemeld.models.script import MAX_SHOT_SECONDS, CameraAngle, DialogueKind
from framemeld.script.generation import MIN_SHOT_SECONDS


class ShotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_index: int
    image_prompt: str
    motion_prompt: str
    #: Serialised as a JSON string, not a float. A binary float cannot hold
    #: 4.05 exactly, and the client sends this value straight back on the
    #: next edit — a round trip through float would silently change it and
    #: the shot would look edited when it was not.
    duration_sec: Decimal
    camera: CameraAngle
    dialogue_kind: DialogueKind
    speech_text: str
    state: str
    content_hash: str
    updated_at: datetime


class SceneOut(BaseModel):
    """A scene's own fields, and the response to a scene PATCH.

    Deliberately without `shots`: update_scene selects the Scene without
    eager-loading the relationship, so a schema that declared `shots` would
    lazy-load under asyncio and raise MissingGreenlet. A field edit has no
    business returning the shot list either.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_index: int
    heading: str
    summary: str
    location: str
    time_of_day: str
    state: str
    content_hash: str
    updated_at: datetime


class SceneDetail(SceneOut):
    """A scene with its shots, for the script listing — which does eager-load
    them. Unlike a bare `pass` subclass this adds a field, so it earns its
    existence."""

    shots: list[ShotOut]


class ScriptOut(BaseModel):
    scenes: list[SceneDetail]
    stale_scenes: int
    stale_shots: int


class _RejectsExplicitNull:
    """Shared by ScenePatch and ShotPatch. Every field on both is optional in
    the sense of "may be omitted", not "may be cleared to nothing": each
    backs a NOT NULL column with a "" default, so send "" to empty a text
    field. `model_dump(exclude_unset=True)` excludes fields the client
    *omitted*, not fields it explicitly set to null — so an unguarded
    `{"heading": null}` reaches `setattr(row, name, None)` against a NOT
    NULL column and raises IntegrityError at flush, a 500 instead of a 422.

    A `mode="before"` validator does not run for a field the client
    omitted, only for one it actually sent — checked against the installed
    pydantic (2.13.5): an omitted field keeps its default untouched and
    unvalidated, so this rejects null without disturbing the
    omitted-means-unchanged path that `exclude_unset` relies on.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_not_a_value(cls, value: object) -> object:
        if value is None:
            raise ValueError('null is not a valid value — omit the field, or send ""')
        return value


class ScenePatch(_RejectsExplicitNull, BaseModel):
    """Every field optional: the editor autosaves one field at a time, and
    a full-object PUT would let a stale tab clobber a field it never
    touched."""

    heading: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=200)
    time_of_day: str | None = Field(default=None, max_length=50)


class ShotPatch(_RejectsExplicitNull, BaseModel):
    image_prompt: str | None = Field(default=None, max_length=4000)
    motion_prompt: str | None = Field(default=None, max_length=2000)
    #: Bounded by the same floor the generator clamps to, so a value the
    #: column cannot hold is a readable 422 rather than a 500 from the
    #: CHECK: `gt=0` would accept 0.001, which quantises to 0.00 and
    #: violates `duration_sec > 0`.
    duration_sec: Decimal | None = Field(default=None, ge=MIN_SHOT_SECONDS, le=MAX_SHOT_SECONDS)
    camera: CameraAngle | None = None
    dialogue_kind: DialogueKind | None = None
    speech_text: str | None = Field(default=None, max_length=4000)

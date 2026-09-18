import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from framemeld.models.script import MAX_SHOT_SECONDS, CameraAngle, DialogueKind
from framemeld.schemas_shared import _RejectsExplicitNull
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


class ScenePatch(_RejectsExplicitNull, BaseModel):
    """Every field optional: the editor autosaves one field at a time, and
    a full-object PUT would let a stale tab clobber a field it never
    touched."""

    # A field this schema does not declare is one the API cannot change.
    # Silently dropping it would return 200 for an edit that did nothing,
    # which is a worse failure than refusing it.
    model_config = ConfigDict(extra="forbid")

    heading: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=200)
    time_of_day: str | None = Field(default=None, max_length=50)


class ShotPatch(_RejectsExplicitNull, BaseModel):
    # A field this schema does not declare is one the API cannot change.
    # Silently dropping it would return 200 for an edit that did nothing,
    # which is a worse failure than refusing it.
    model_config = ConfigDict(extra="forbid")

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


class MoveIn(BaseModel):
    """One step up or down. Constrained to exactly +/-1 rather than any
    integer: a caller sending 7 means something the endpoint does not do,
    and silently clamping it would move the row somewhere they did not ask
    for."""

    model_config = ConfigDict(extra="forbid")

    delta: Literal[-1, 1]

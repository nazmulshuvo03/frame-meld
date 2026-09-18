import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from framemeld.models.shots import AudioKind
from framemeld.script.schemas import ShotOut


class ShotImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    state: str
    #: Null until a generation succeeds. The frontend builds its <img> src
    #: from this; there is no URL field because the route is derivable and
    #: a stored URL would be a second thing to keep true.
    asset_id: uuid.UUID | None
    #: Which sheets this picture was actually drawn from. Exposed because
    #: "why does this face look wrong" is otherwise unanswerable from the
    #: outside.
    ref_character_image_ids: list[uuid.UUID]
    credits_spent: int
    updated_at: datetime


class AudioClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: AudioKind
    state: str
    asset_id: uuid.UUID | None
    text: str
    voice_id: str
    duration_sec: Decimal | None
    credits_spent: int
    updated_at: datetime


class ShotClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    state: str
    asset_id: uuid.UUID | None
    #: Which still this clip was animated from. Exposed for the same reason
    #: a still exposes its reference sheets: "why does this shot look wrong"
    #: is otherwise unanswerable from outside.
    source_image_id: uuid.UUID | None
    duration_sec: Decimal | None
    credits_spent: int
    updated_at: datetime


class ShotMediaOut(ShotOut):
    """A shot, plus everything generated from it.

    Extends the Script page's ShotOut rather than restating it: the two
    pages show the same shot, and a second copy of those eight fields would
    be a second thing to keep true.
    """

    #: The characters the casting pass placed in this frame, in cast order
    #: -- the same order their sheets are attached to the image call, so a
    #: reader can tell which reference was which.
    character_ids: list[uuid.UUID]
    character_names: list[str]
    still: ShotImageOut | None
    audio: AudioClipOut | None
    clip: ShotClipOut | None


class ShotsOut(BaseModel):
    shots: list[ShotMediaOut]
    stale_stills: int
    stale_audio: int
    #: Whether the shot/character mapping has been run at all. The page
    #: needs to tell "nobody is in any shot" from "nobody has been placed
    #: in a shot yet", and those look identical from the rows alone.
    cast_into_shots: bool

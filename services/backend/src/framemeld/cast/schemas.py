import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from framemeld.models.cast import CharacterImageKind, CharacterRole
from framemeld.schemas_shared import _RejectsExplicitNull


class CharacterImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: CharacterImageKind
    is_canonical: bool
    state: str
    #: Null until a generation succeeds. The frontend builds its <img> src
    #: from this; there is no URL field because the route is derivable and
    #: a stored URL would be a second thing to keep true.
    asset_id: uuid.UUID | None
    credits_spent: int
    updated_at: datetime


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_index: int
    name: str
    role: CharacterRole
    description: str
    appearance: str
    #: Which voice reads this character's lines. Inside the character's
    #: content_hash and outside their visual hash, so changing it
    #: invalidates their dialogue and leaves their sheet alone.
    voice_id: str | None
    state: str
    content_hash: str
    updated_at: datetime
    #: The sheet, when there is one. A list would promise the portrait and
    #: expression kinds that M3 never produces (M3-D8).
    sheet: CharacterImageOut | None


class CastOut(BaseModel):
    characters: list[CharacterOut]
    stale_characters: int
    stale_sheets: int


class CharacterPatch(_RejectsExplicitNull, BaseModel):
    """Every field optional: the editor autosaves one field at a time, and
    a full-object PUT would let a stale tab clobber a field it never
    touched. `extra="forbid"` because silently dropping an unknown field
    returns 200 for an edit that did nothing."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    role: CharacterRole | None = None
    description: str | None = Field(default=None, max_length=4000)
    appearance: str | None = Field(default=None, max_length=4000)
    voice_id: str | None = Field(default=None, max_length=80)

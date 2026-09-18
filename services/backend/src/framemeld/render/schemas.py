import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from framemeld.models.render import RenderKind


class RenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: RenderKind
    state: str
    #: Null until a render succeeds. The page builds its <video src> from
    #: this; there is no URL field, for the same reason ShotImageOut has
    #: none -- the route is derivable and a stored URL is a second thing to
    #: keep true.
    asset_id: uuid.UUID | None
    duration_sec: Decimal | None
    resolution: str
    updated_at: datetime


class MusicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    state: str
    asset_id: uuid.UUID | None
    #: The derived prompt (M5-D3). Shown, because "why does my film sound
    #: like that" is otherwise unanswerable from the outside.
    text: str
    duration_sec: Decimal | None
    credits_spent: int
    updated_at: datetime


class FilmOut(BaseModel):
    shot_count: int
    #: How many shots have a still with bytes. The page renders
    #: "{stills_ready} of {shot_count} shots drawn" and the render button
    #: is refused below parity (M5-D4).
    stills_ready: int
    #: The same for clips, gating the Final render instead (M6-D7). A stale
    #: clip still counts as ready -- whether to pay 80 credits to redo it is
    #: the user's call, so it is reported separately rather than silently
    #: blocking the deliverable.
    clips_ready: int
    clips_stale: int
    duration_sec: float
    music: MusicOut | None
    preview: RenderOut | None
    final: RenderOut | None

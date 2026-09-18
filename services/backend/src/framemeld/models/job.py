import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from framemeld.db import Base
from framemeld.models.columns import enum_column


class JobKind(enum.StrEnum):
    """The full list from spec section 5.4. Only generate_script is reachable
    in M2; the rest are declared now so later milestones add rows rather than
    migrations — enum_column writes a CHECK, and widening one means an ALTER.

    `map_shot_characters` is the one kind spec section 5.4 did not list. M4-D1
    made the shot/character mapping a first-class artifact with its own hash
    and its own price rather than an un-versioned side effect of the cast
    extraction, and a job kind is what that means. It cost the ALTER this
    docstring warns about; the spec is corrected in place.
    """

    GENERATE_SCRIPT = "generate_script"
    GENERATE_CHARACTERS = "generate_characters"
    GENERATE_CHARACTER_IMAGE = "generate_character_image"
    MAP_SHOT_CHARACTERS = "map_shot_characters"
    GENERATE_SHOT_IMAGE = "generate_shot_image"
    GENERATE_SPEECH = "generate_speech"
    GENERATE_MUSIC = "generate_music"
    RENDER_PREVIEW = "render_preview"
    GENERATE_SHOT_CLIP = "generate_shot_clip"
    RENDER_FINAL = "render_final"


class JobState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meld_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("melds.id", ondelete="CASCADE"), index=True
    )
    # length=40: the longest value, generate_character_image, is 24
    # characters and the helper defaults to 20.
    kind: Mapped[JobKind] = mapped_column(enum_column(JobKind, "job_kind", length=40))

    # A loose (table, id) pair rather than nine nullable foreign keys. What a
    # job targets varies by kind, so a real FK per kind would mean a column
    # per kind. target_id is nullable because some kinds create their target.
    target_table: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    input_hash: Mapped[str] = mapped_column(String(64))
    # = canonical_hash({kind, target_id, input_hash}). Unique, and that is the
    # whole mechanism: a second Generate click for unchanged inputs finds this
    # row instead of paying a provider twice.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    state: Mapped[JobState] = mapped_column(
        enum_column(JobState, "job_state"), default=JobState.QUEUED
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Written from M3, when quote/hold/settle goes live. Zero until then.
    credits_held: Mapped[int] = mapped_column(Integer, default=0)
    #: What the provider actually billed for this call, as the adapter
    #: reported it (M3-D6). Not the billing input -- credits are quoted at a
    #: retail price and settled against work done (M3-D5) -- but the only
    #: place margin is measurable. Numeric, never float: six decimal places
    #: because image calls are priced in cents and LLM calls in fractions of
    #: one.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    #: How far along a render is, 0-100, or NULL for a job kind that has no
    #: notion of progress.
    #:
    #: NULL rather than 0 for those, deliberately: "this job does not report
    #: progress" and "this render has not started yet" are different facts,
    #: and a UI that cannot tell them apart shows a 0% bar on a job that will
    #: never move. Only the two render kinds write it -- everything else is
    #: one provider call the worker cannot see inside of.
    #:
    #: Whole percent, not the 0..1 fraction the render service reports. The
    #: conversion happens once, at the client boundary, because a percentage
    #: is what is displayed and storing three decimal places of a number
    #: rounded for display is precision nobody reads.
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

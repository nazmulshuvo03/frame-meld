import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from framemeld.db import Base
from framemeld.models.columns import enum_column

# Spec section 8: every current image-to-video model has a hard clip-length
# limit, so this is a property of the data. Enforced in three places -- the
# CHECK below, the generator's JSON schema, and the edit schema -- because
# the value arrives from three different directions.
MAX_SHOT_SECONDS = Decimal("5.0")

# Spec section 1: 1-3 minutes, 10-30 shots. The cap is enforced; there is
# deliberately no floor, because padding a short story with invented shots
# would be worse than returning a short film.
#
# It lives here rather than beside the generator that enforces it because
# the hashing module also needs it, and hashes.py importing generation.py
# would be a cycle. Two copies would be worse than an odd home: a copy that
# drifted would leave the input hash describing parameters that were not
# used, and staleness would go quietly wrong.
MAX_SHOTS = 30


class ArtifactState(enum.StrEnum):
    """Spec section 4.1. `stale` never means "deleted": the row keeps its
    content, so reverting the upstream edit restores it for free."""

    EMPTY = "empty"
    GENERATING = "generating"
    FRESH = "fresh"
    STALE = "stale"
    FAILED = "failed"


class CameraAngle(enum.StrEnum):
    WIDE = "wide"
    MEDIUM = "medium"
    CLOSE = "close"
    INSERT = "insert"
    ESTABLISHING = "establishing"


class DialogueKind(enum.StrEnum):
    """Exists because lip-sync is unsolved cheaply (spec section 8). The
    script generator is biased towards narration and off-screen dialogue,
    and this field is how that bias is recorded rather than implied."""

    NONE = "none"
    NARRATION = "narration"
    OFF_SCREEN = "off_screen"
    ON_SCREEN = "on_screen"


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        # Deferrable so a future reorder can renumber in one statement
        # instead of shuffling through temporary indexes. M2 does not
        # reorder, but making it deferrable now costs nothing and a later
        # ALTER is a migration nobody will remember to write.
        UniqueConstraint(
            "meld_id",
            "order_index",
            name="uq_scenes_meld_order",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meld_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("melds.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column()

    heading: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    time_of_day: Mapped[str] = mapped_column(String(50), default="")

    content_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[ArtifactState] = mapped_column(
        enum_column(ArtifactState, "scene_state"), default=ArtifactState.FRESH
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    shots: Mapped[list["Shot"]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        # The database already cascades, so let it. Without this SQLAlchemy
        # loads every child row purely to null its FK before the parent goes.
        passive_deletes=True,
        order_by="Shot.order_index",
    )


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (
        CheckConstraint(
            "duration_sec > 0 AND duration_sec <= 5.0",
            name="ck_shots_duration_sec",
        ),
        UniqueConstraint(
            "scene_id",
            "order_index",
            name="uq_shots_scene_order",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scene_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column()

    image_prompt: Mapped[str] = mapped_column(Text, default="")
    # Distinct from image_prompt because image-to-video models take an image
    # *and* a description of how it should move (spec section 8).
    motion_prompt: Mapped[str] = mapped_column(Text, default="")
    duration_sec: Mapped[Decimal] = mapped_column(Numeric(4, 2))

    camera: Mapped[CameraAngle] = mapped_column(
        enum_column(CameraAngle, "shot_camera"), default=CameraAngle.MEDIUM
    )
    dialogue_kind: Mapped[DialogueKind] = mapped_column(
        enum_column(DialogueKind, "shot_dialogue_kind"), default=DialogueKind.NONE
    )
    speech_text: Mapped[str] = mapped_column(Text, default="")
    # No ForeignKey yet: the `characters` table arrives in M3, and a FK to a
    # table that does not exist will not migrate. M3's migration adds the
    # constraint to this existing column.
    speaker_character_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )

    content_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[ArtifactState] = mapped_column(
        enum_column(ArtifactState, "shot_state"), default=ArtifactState.FRESH
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scene: Mapped["Scene"] = relationship(back_populates="shots")

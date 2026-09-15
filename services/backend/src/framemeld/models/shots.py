import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from framemeld.db import Base
from framemeld.models.columns import enum_column
from framemeld.models.script import ArtifactState


class ShotCharacter(Base):
    """Which characters appear in a shot (spec section 5.2).

    A real join table rather than a `uuid[]` on `shots`, because this
    relation drives both reference-image selection and staleness
    propagation: a deleted character must not leave a dangling id inside a
    shot, quietly producing a still that references nothing and a shot that
    can never go stale.

    Deliberately carries none of the generated-artifact columns even though
    a job produces it. What the mapping *is* lives in the rows themselves,
    and what produced them lives on the shot images that reference them --
    a per-row input_hash here would be five copies of one job's hash.
    """

    __tablename__ = "shot_characters"

    shot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("shots.id", ondelete="CASCADE"), primary_key=True
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True
    )


class ShotImage(Base):
    """The still, and in M6 the first frame of the clip (spec section 5.3)."""

    __tablename__ = "shot_images"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    shot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("shots.id", ondelete="CASCADE"), index=True
    )
    #: Null until a provider that accepts one is configured. Declared now
    #: because it is part of the row's identity in spec section 5.3 and
    #: adding it later would re-hash nothing but would still be a migration.
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A plain array, not a join table, and that difference is deliberate
    #: (spec section 5.2): this is a historical record of which sheets were
    #: actually sent to the model, not a live relation. It must survive the
    #: character being deleted, because it explains a picture that exists.
    ref_character_image_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), default=list
    )

    #: A hash of the bytes produced. M6's clip generation keys on it, the
    #: same way this row's input_hash keys on the sheets' content hashes.
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[ArtifactState] = mapped_column(
        enum_column(ArtifactState, "shot_image_state"), default=ArtifactState.EMPTY
    )

    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    credits_spent: Mapped[int] = mapped_column(Integer, default=0)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AudioOwnerType(enum.StrEnum):
    """Spec section 5.3. M4 only ever writes `shot`; `meld` is what M5's
    music hangs from, and declaring it now costs nothing where widening a
    CHECK later costs a migration."""

    SHOT = "shot"
    MELD = "meld"


class AudioKind(enum.StrEnum):
    """Same rule: M4 produces `narration` and `dialogue` only."""

    NARRATION = "narration"
    DIALOGUE = "dialogue"
    MUSIC = "music"
    SFX = "sfx"


class AudioClip(Base):
    """Generated audio (spec section 5.3).

    `owner_type`/`owner_id` rather than a nullable FK per owner, because
    music (M5) hangs from the Meld and speech hangs from a shot. There is
    no foreign key on `owner_id` for that reason; the cascade is done by
    hand in the one place that deletes shots.
    """

    __tablename__ = "audio_clips"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_type: Mapped[AudioOwnerType] = mapped_column(
        enum_column(AudioOwnerType, "audio_clip_owner_type"), default=AudioOwnerType.SHOT
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    kind: Mapped[AudioKind] = mapped_column(
        enum_column(AudioKind, "audio_clip_kind"), default=AudioKind.NARRATION
    )

    #: The words that were spoken, as they were at generation time. Kept on
    #: the row rather than read back through the shot, because the shot's
    #: text can be edited afterwards and this has to keep explaining the
    #: audio that exists.
    text: Mapped[str] = mapped_column(Text, default="")
    voice_id: Mapped[str] = mapped_column(String(80), default="")
    #: Duplicated from the asset deliberately: the scene spec (M5) reads
    #: durations to lay out a timeline, and it must not have to join to
    #: `assets` for a number that can also be null there.
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    content_hash: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[ArtifactState] = mapped_column(
        enum_column(ArtifactState, "audio_clip_state"), default=ArtifactState.EMPTY
    )

    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    credits_spent: Mapped[int] = mapped_column(Integer, default=0)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

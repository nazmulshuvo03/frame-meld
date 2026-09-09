import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from framemeld.db import Base


class MeldStage(enum.StrEnum):
    STORY = "story"
    SCRIPT = "script"
    CAST = "cast"
    SHOTS = "shots"
    FILM = "film"


class MeldStatus(enum.StrEnum):
    DRAFT = "draft"
    COMPLETE = "complete"
    ARCHIVED = "archived"


class ModerationState(enum.StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FLAGGED = "flagged"


def _enum_column(enum_cls: type[enum.StrEnum], constraint_name: str) -> Enum:
    """A VARCHAR column with a named CHECK constraint over the enum's *values*.

    All four arguments are load-bearing and each fails silently if omitted:

    - `native_enum=False` avoids a Postgres native enum, which would need an
      `ALTER TYPE` migration every time a member is added.
    - `create_constraint=True` is required. SQLAlchemy 2.0 defaults it to
      False, so `native_enum=False` on its own produces a bare, completely
      unconstrained VARCHAR — no validation at any layer.
    - `name` names the CHECK so a later migration can drop and recreate it.
    - `values_callable` stores `'story'` rather than the member name
      `'STORY'`, keeping the database consistent with the API and with the
      values listed in the spec.

    Later milestones add enums in other model files; move this to a shared
    module at that point rather than copying it.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        create_constraint=True,
        name=constraint_name,
        values_callable=lambda e: [member.value for member in e],
    )


class Meld(Base):
    __tablename__ = "melds"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))

    # See _enum_column above for why all four Enum arguments are required.
    stage: Mapped[MeldStage] = mapped_column(
        _enum_column(MeldStage, "meld_stage"), default=MeldStage.STORY
    )
    status: Mapped[MeldStatus] = mapped_column(
        _enum_column(MeldStatus, "meld_status"), default=MeldStatus.DRAFT
    )
    moderation_state: Mapped[ModerationState] = mapped_column(
        _enum_column(ModerationState, "meld_moderation_state"),
        default=ModerationState.PENDING,
    )

    aspect_ratio: Mapped[str] = mapped_column(String(10), default="16:9")
    style_preset: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    story: Mapped["Story | None"] = relationship(
        back_populates="meld", cascade="all, delete-orphan", uselist=False
    )


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meld_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("melds.id", ondelete="CASCADE"), unique=True, index=True
    )
    text: Mapped[str] = mapped_column(Text, default="")
    input_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    meld: Mapped["Meld"] = relationship(back_populates="story")

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from framemeld.db import Base
from framemeld.models.columns import enum_column
from framemeld.models.script import ArtifactState

#: Spec section 1 targets 10-30 shots; a cast larger than this is the model
#: inventing extras rather than reading the story. Enforced by the parser,
#: not by a constraint -- the cap is a generation policy, not a data
#: invariant, and a user who hand-adds a 13th character in M4 is doing
#: nothing wrong.
MAX_CHARACTERS = 12


class CharacterRole(enum.StrEnum):
    LEAD = "lead"
    SUPPORTING = "supporting"
    BACKGROUND = "background"


class CharacterImageKind(enum.StrEnum):
    """Spec section 5.3. Only `sheet` is produced in M3 (M3-D8); the rest
    are declared now so adding one later is a row, not a migration --
    enum_column writes a CHECK, and widening one means an ALTER."""

    SHEET = "sheet"
    PORTRAIT = "portrait"
    FULL_BODY = "full_body"
    EXPRESSION = "expression"


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        UniqueConstraint(
            "meld_id",
            "order_index",
            name="uq_characters_meld_order",
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
    order_index: Mapped[int] = mapped_column(Integer)

    name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[CharacterRole] = mapped_column(
        enum_column(CharacterRole, "character_role"), default=CharacterRole.SUPPORTING
    )
    description: Mapped[str] = mapped_column(Text, default="")
    #: THE canonical visual description. This is what the sheet prompt is
    #: built from, and in M4 what a shot image's input_hash will key on, so
    #: an edit here is the thing that invalidates generated artwork.
    #: Kept separate from `description` (who they are) precisely so that
    #: rewriting their backstory does not cost the user an image.
    appearance: Mapped[str] = mapped_column(Text, default="")
    #: Spec section 2: voices arrive with speech generation in M4. The
    #: column exists now because it is part of the character's content hash
    #: and adding it later would re-hash every existing row.
    voice_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    content_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[ArtifactState] = mapped_column(
        enum_column(ArtifactState, "character_state"), default=ArtifactState.FRESH
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    images: Mapped[list["CharacterImage"]] = relationship(
        cascade="all, delete-orphan",
        # The database already cascades, so let it. Without this SQLAlchemy
        # loads every child row purely to null its FK before the parent goes.
        passive_deletes=True,
    )


class CharacterImage(Base):
    __tablename__ = "character_images"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[CharacterImageKind] = mapped_column(
        enum_column(CharacterImageKind, "character_image_kind"),
        default=CharacterImageKind.SHEET,
    )
    #: The one image a shot generation in M4 will actually reference.
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False)

    #: What this image *is* -- a hash of the bytes that were produced (see
    #: cast/hashes.py). A generated row still needs a content_hash because
    #: M4's shot images depend on it, and spec section 4.1 is explicit that
    #: downstream keys on content_hash and never on input_hash.
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[ArtifactState] = mapped_column(
        enum_column(ArtifactState, "character_image_state"), default=ArtifactState.EMPTY
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

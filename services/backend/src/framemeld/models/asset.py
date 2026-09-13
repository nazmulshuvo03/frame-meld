import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from framemeld.db import Base


class Asset(Base):
    """The file registry (spec section 5.3). Not a generated-artifact table:
    it carries none of input_hash/state/provider/model/params. It records
    that some bytes exist, who owns them, and where they are.

    Deduplication by sha256 is a later optimisation, not v1 -- the column
    is indexed so it can become one without a migration.
    """

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Ownership lives here rather than being reached through the Meld: the
    #: asset route authorises on this column directly, and an asset may
    #: outlive the Meld whose generation produced it.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    #: Unique: two rows pointing at one file would make deleting either of
    #: them corrupt the other.
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    mime: Mapped[str] = mapped_column(String(100))
    bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)

    #: Null for formats this codebase cannot measure without a decoder --
    #: see assets/service.py's png_dimensions.
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

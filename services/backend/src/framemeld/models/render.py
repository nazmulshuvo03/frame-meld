import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from framemeld.db import Base
from framemeld.models.columns import enum_column
from framemeld.models.script import ArtifactState


class RenderKind(enum.StrEnum):
    """Spec section 4.2's two modes. M5 produces `preview` only; `final` is
    declared now for the same reason every other enum declares ahead --
    widening a CHECK later costs a migration, declaring a value costs
    nothing."""

    PREVIEW = "preview"
    FINAL = "final"


class Render(Base):
    """One assembled film (spec section 5.3).

    `scene_spec` is stored rather than rebuilt on demand: it is the exact
    document that was handed to the render service, and it is the only way
    to answer "why does this cut look like that" about a file that already
    exists. It holds asset *ids*, never the signed URLs that were sent --
    a signature expires, and a stored credential is a stored liability.
    """

    __tablename__ = "renders"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meld_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("melds.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[RenderKind] = mapped_column(
        enum_column(RenderKind, "render_kind"), default=RenderKind.PREVIEW
    )

    scene_spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    #: The film's running time, copied off the spec so a listing does not
    #: have to parse JSONB to show it.
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    #: "1280x720". A string rather than two integers because it is shown,
    #: never computed with, and because the pair is meaningless apart.
    resolution: Mapped[str] = mapped_column(String(20), default="")

    content_hash: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[ArtifactState] = mapped_column(
        enum_column(ArtifactState, "render_state"), default=ArtifactState.EMPTY
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

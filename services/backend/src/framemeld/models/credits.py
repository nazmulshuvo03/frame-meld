import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from framemeld.db import Base
from framemeld.models.columns import enum_column


class CreditKind(enum.StrEnum):
    """Spec section 5.1. `hold` and `settle`/`refund` always come in pairs:
    a hold is taken before a provider is called, and exactly one settle or
    refund closes it once the job is terminal."""

    PURCHASE = "purchase"
    GRANT = "grant"
    HOLD = "hold"
    SETTLE = "settle"
    REFUND = "refund"


class CreditLedger(Base):
    """Append-only. The balance is SUM(delta) over these rows and is never
    stored anywhere (spec section 4.3) -- that is what makes it impossible
    to corrupt with a lost update.

    Nothing at the database level enforces append-only; a trigger would,
    and is deliberately not here. The rule is held in one place instead
    (credits/service.py, which only ever INSERTs) because a trigger is a
    second source of truth that migrations then have to maintain.
    """

    __tablename__ = "credit_ledger"
    __table_args__ = (Index("ix_credit_ledger_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    #: Negative is a spend. Integer credits only: 1 credit = $0.01 retail,
    #: and a float balance would eventually drift.
    delta: Mapped[int] = mapped_column(Integer)
    kind: Mapped[CreditKind] = mapped_column(enum_column(CreditKind, "credit_ledger_kind"))

    #: Both nullable: a grant belongs to no job and no Meld.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    meld_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("melds.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(200), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

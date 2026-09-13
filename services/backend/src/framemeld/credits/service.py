import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.models.credits import CreditKind, CreditLedger
from framemeld.models.user import User

#: The kinds that close a hold. Exactly one of these may exist per job.
_CLOSING_KINDS = (CreditKind.SETTLE, CreditKind.REFUND)


class InsufficientCredits(Exception):
    """Raised before any provider is called (spec section 4.3), so a user
    who cannot pay never triggers a bill we then have to eat."""

    def __init__(self, *, required: int, available: int) -> None:
        super().__init__(f"Needs {required} credits, has {available}.")
        self.required = required
        self.available = available


async def balance(db: DbSession, user_id: uuid.UUID) -> int:
    """SUM(delta) over the append-only ledger (spec section 4.3).

    Never cached and never stored: a materialised balance is the one thing
    that can silently disagree with its own history.
    """
    total = await db.scalar(
        select(func.coalesce(func.sum(CreditLedger.delta), 0)).where(
            CreditLedger.user_id == user_id
        )
    )
    return int(total or 0)


async def _lock_user(db: DbSession, user_id: uuid.UUID) -> None:
    """Serialise every balance-changing path for one user (M3-D4).

    SUM-then-INSERT is a time-of-check/time-of-use race: two Generate
    clicks milliseconds apart both read a sufficient balance, both insert a
    hold, and the user ends up owing more than they had. Locking the user
    row makes the read and the write one critical section, per user, without
    touching anyone else's throughput.
    """
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


async def _add(
    db: DbSession,
    *,
    user_id: uuid.UUID,
    delta: int,
    kind: CreditKind,
    job_id: uuid.UUID | None = None,
    meld_id: uuid.UUID | None = None,
    description: str = "",
) -> CreditLedger:
    row = CreditLedger(
        user_id=user_id,
        delta=delta,
        kind=kind,
        job_id=job_id,
        meld_id=meld_id,
        description=description[:200],
    )
    db.add(row)
    await db.flush()
    return row


async def grant(db: DbSession, user_id: uuid.UUID, credits: int, description: str) -> CreditLedger:
    """Credits given, not bought. Stripe is post-M6 (spec section 10), so
    this is the only way credits enter the system in M3."""
    return await _add(
        db, user_id=user_id, delta=credits, kind=CreditKind.GRANT, description=description
    )


async def hold(
    db: DbSession,
    *,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    meld_id: uuid.UUID | None,
    credits: int,
    description: str,
) -> CreditLedger:
    """Take the money before the work starts.

    Must run in the same transaction as the job row it refers to: a commit
    between the two would leave either a job nobody paid for or a charge
    for a job that does not exist.
    """
    await _lock_user(db, user_id)
    available = await balance(db, user_id)
    if available < credits:
        raise InsufficientCredits(required=credits, available=available)

    return await _add(
        db,
        user_id=user_id,
        delta=-credits,
        kind=CreditKind.HOLD,
        job_id=job_id,
        meld_id=meld_id,
        description=description,
    )


async def _already_closed(db: DbSession, job_id: uuid.UUID) -> bool:
    existing = await db.scalar(
        select(CreditLedger.id)
        .where(CreditLedger.job_id == job_id, CreditLedger.kind.in_(_CLOSING_KINDS))
        .limit(1)
    )
    return existing is not None


async def settle(
    db: DbSession,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    held: int,
    used: int,
    description: str,
) -> CreditLedger | None:
    """Close a hold once the work is done, returning any unused credits.

    Returns None when there is nothing to write -- either the job was
    already closed, or the work cost exactly what was quoted. A zero-delta
    row is noise in a ledger a human has to be able to read.

    Never charges more than was held (M3-D3). Going over budget is logged
    by the caller and absorbed; the quote the user agreed to is a promise.
    """
    await _lock_user(db, user_id)
    if await _already_closed(db, job_id):
        return None

    refundable = held - min(used, held)
    if refundable <= 0:
        return None

    return await _add(
        db,
        user_id=user_id,
        delta=refundable,
        kind=CreditKind.SETTLE,
        job_id=job_id,
        description=description,
    )


async def refund(
    db: DbSession, *, job_id: uuid.UUID, user_id: uuid.UUID, held: int, description: str
) -> CreditLedger | None:
    """Give all of it back. Spec section 4.3: a failed job refunds in full,
    including a provider content refusal, which is terminal.

    Returns None if this job's hold was already closed -- arq can deliver
    the same message twice, and the ledger is append-only, so a second
    refund would be free money with no undo.
    """
    await _lock_user(db, user_id)
    if await _already_closed(db, job_id):
        return None

    return await _add(
        db,
        user_id=user_id,
        delta=held,
        kind=CreditKind.REFUND,
        job_id=job_id,
        description=description,
    )

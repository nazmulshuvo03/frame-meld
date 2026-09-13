from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.auth.deps import CurrentUser
from framemeld.credits.pricing import PRICING_VERSION, QUOTES
from framemeld.credits.schemas import BalanceOut, PricingOut
from framemeld.credits.service import balance
from framemeld.db import get_session

router = APIRouter(prefix="/credits", tags=["credits"])

Db = Annotated[DbSession, Depends(get_session)]


@router.get("", response_model=BalanceOut)
async def get_balance(user: CurrentUser, db: Db) -> BalanceOut:
    return BalanceOut(balance=await balance(db, user.id))


@router.get("/pricing", response_model=PricingOut)
async def get_pricing(user: CurrentUser) -> PricingOut:
    """Authenticated even though the table is the same for everyone: an
    anonymous price list is a thing to keep in sync with a marketing page,
    and nothing needs it yet."""
    return PricingOut(
        version=PRICING_VERSION, quotes={kind.value: credits for kind, credits in QUOTES.items()}
    )

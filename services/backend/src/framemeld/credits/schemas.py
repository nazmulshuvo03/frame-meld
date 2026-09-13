from pydantic import BaseModel


class BalanceOut(BaseModel):
    balance: int


class PricingOut(BaseModel):
    version: int
    #: Keyed by JobKind's *value*, so the frontend can look a price up with
    #: the same string the job API hands back.
    quotes: dict[str, int]

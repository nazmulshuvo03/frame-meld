"""Grant credits to an account by email.

    conda run --no-capture-output -n framemeld \\
        python -m framemeld.scripts.grant_credits someone@example.com 500

The only top-up path until Stripe arrives post-M6 (spec section 10).
"""

import argparse
import asyncio

from sqlalchemy import select

from framemeld.credits.service import balance, grant
from framemeld.db import SessionLocal
from framemeld.models.user import User


async def _main(email: str, credits: int) -> int:
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if user is None:
            print(f"No account for {email}.")
            return 1

        await grant(db, user.id, credits, "manual grant")
        await db.commit()
        print(f"{email}: granted {credits}, balance is now {await balance(db, user.id)}.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("credits", type=int)
    args = parser.parse_args()
    if args.credits <= 0:
        # Removing credits is not a grant. If a negative adjustment is ever
        # needed it wants its own kind and its own audit story, not this.
        parser.error("credits must be positive")
    return asyncio.run(_main(args.email, args.credits))


if __name__ == "__main__":
    raise SystemExit(main())

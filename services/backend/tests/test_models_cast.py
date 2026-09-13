import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from framemeld.models.asset import Asset
from framemeld.models.cast import Character, CharacterImage, CharacterImageKind, CharacterRole
from framemeld.models.credits import CreditKind, CreditLedger
from framemeld.models.job import Job, JobKind, JobState
from framemeld.models.meld import Meld
from framemeld.models.script import ArtifactState
from framemeld.models.user import User


async def _user(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def _meld(db, user: User) -> Meld:
    meld = Meld(user_id=user.id, title="T")
    db.add(meld)
    await db.flush()
    return meld


async def _character(db, meld: Meld, order_index: int = 0) -> Character:
    character = Character(
        meld_id=meld.id,
        order_index=order_index,
        name="Elena",
        role=CharacterRole.LEAD,
        description="The letter's recipient.",
        appearance="A woman in her sixties, silver hair pinned back, grey wool cardigan.",
        content_hash="a" * 64,
        input_hash="b" * 64,
        state=ArtifactState.FRESH,
    )
    db.add(character)
    await db.flush()
    return character


async def _asset(db, user: User) -> Asset:
    asset = Asset(
        user_id=user.id,
        storage_key=f"{user.id}/{uuid.uuid4()}.png",
        mime="image/png",
        bytes=1234,
        sha256="c" * 64,
        width=1024,
        height=1024,
    )
    db.add(asset)
    await db.flush()
    return asset


async def test_a_ledger_row_round_trips(db):
    user = await _user(db)
    db.add(
        CreditLedger(
            user_id=user.id, delta=-12, kind=CreditKind.HOLD, description="character sheet"
        )
    )
    await db.flush()
    db.expire_all()

    row = (await db.execute(select(CreditLedger))).scalar_one()
    assert row.delta == -12
    assert row.kind is CreditKind.HOLD


async def test_ledger_kinds_are_checked_in_the_database(db):
    """Proves enum_column's create_constraint=True reached this table.
    Raw SQL because the ORM would reject 'bribe' before it left Python."""
    user = await _user(db)
    row = CreditLedger(user_id=user.id, delta=1, kind=CreditKind.GRANT)
    db.add(row)
    await db.flush()

    with pytest.raises(DBAPIError):
        await db.execute(
            text("update credit_ledger set kind = 'bribe' where id = :id"), {"id": row.id}
        )


async def test_ledger_enums_store_their_values_not_their_member_names(db):
    user = await _user(db)
    db.add(CreditLedger(user_id=user.id, delta=500, kind=CreditKind.GRANT))
    await db.flush()

    raw = await db.execute(text("select kind from credit_ledger"))
    assert raw.scalar_one() == "grant"


async def test_a_character_and_its_image_round_trip(db):
    user = await _user(db)
    meld = await _meld(db, user)
    character = await _character(db, meld)
    asset = await _asset(db, user)

    db.add(
        CharacterImage(
            character_id=character.id,
            kind=CharacterImageKind.SHEET,
            is_canonical=True,
            content_hash="d" * 64,
            input_hash="e" * 64,
            state=ArtifactState.FRESH,
            provider="stub",
            model="stub-image",
            params={"seed": 7},
            credits_spent=12,
            asset_id=asset.id,
        )
    )
    await db.flush()
    asset_id = asset.id  # read before expire_all: a refresh here would be sync IO
    db.expire_all()

    stored = (await db.execute(select(CharacterImage))).scalar_one()
    assert stored.kind is CharacterImageKind.SHEET
    assert stored.is_canonical is True
    assert stored.params == {"seed": 7}
    assert stored.credits_spent == 12
    assert stored.asset_id == asset_id


async def test_deleting_a_meld_cascades_to_characters_and_their_images(db):
    """Melds are soft-deleted in the app, so this never fires in practice --
    but the FKs must still be right, or a future hard delete or a data
    migration leaves orphans."""
    user = await _user(db)
    meld = await _meld(db, user)
    character = await _character(db, meld)
    asset = await _asset(db, user)
    db.add(
        CharacterImage(
            character_id=character.id,
            kind=CharacterImageKind.SHEET,
            content_hash="d" * 64,
            input_hash="e" * 64,
            state=ArtifactState.FRESH,
            asset_id=asset.id,
        )
    )
    await db.flush()

    await db.execute(delete(Meld).where(Meld.id == meld.id))
    await db.flush()
    db.expire_all()

    assert (await db.execute(select(Character))).scalars().all() == []
    assert (await db.execute(select(CharacterImage))).scalars().all() == []
    # The asset row survives: the file it points at is still on disk, and
    # orphan cleanup is a separate concern from cascade correctness.
    assert (await db.execute(select(Asset))).scalars().all() != []


async def test_two_characters_cannot_share_an_order_index(db):
    user = await _user(db)
    meld = await _meld(db, user)
    await _character(db, meld, order_index=0)
    await _character(db, meld, order_index=0)
    await db.flush()
    # The constraint is DEFERRABLE INITIALLY DEFERRED (so a future reorder can
    # renumber in one statement), so it is only checked at commit -- or here.
    with pytest.raises(IntegrityError):
        await db.execute(text("set constraints all immediate"))


async def test_a_storage_key_is_unique(db):
    """Two assets pointing at one file would make deleting either of them
    corrupt the other."""
    user = await _user(db)
    first = await _asset(db, user)
    db.add(
        Asset(
            user_id=user.id,
            storage_key=first.storage_key,
            mime="image/png",
            bytes=1,
            sha256="f" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_a_job_records_what_the_provider_charged(db):
    """M3-D6: adapters report a real cost_usd and it is persisted, so margin
    is measurable. Numeric, not float -- money never round-trips through
    binary floating point in this codebase."""
    user = await _user(db)
    meld = await _meld(db, user)
    job = Job(
        meld_id=meld.id,
        kind=JobKind.GENERATE_CHARACTER_IMAGE,
        target_table="character_images",
        target_id=None,
        input_hash="a" * 64,
        idempotency_key="b" * 64,
        state=JobState.QUEUED,
        cost_usd=Decimal("0.039000"),
    )
    db.add(job)
    await db.flush()
    db.expire_all()

    assert (await db.execute(select(Job))).scalar_one().cost_usd == Decimal("0.039000")

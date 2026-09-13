import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

import framemeld.moderation.service as moderation_service
from framemeld.config import settings
from framemeld.melds import service as melds_service
from framemeld.models.job import Job
from framemeld.models.meld import Meld, ModerationState
from framemeld.models.user import User
from framemeld.moderation.service import Verdict, check, check_and_record
from framemeld.providers.base import ProviderResult, ProviderTransportError

STORY = "Elena finds a letter beneath the floorboard."


class _Classifier:
    """An LLM that answers the moderation schema, counting its calls."""

    name = "fake"
    model = "fake-model"

    def __init__(self, allowed=True, reason="", error=None):
        self._allowed = allowed
        self._reason = reason
        self._error = error
        self.calls = 0

    async def complete(self, *, system, user, json_schema):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ProviderResult(
            text=json.dumps({"allowed": self._allowed, "reason": self._reason}),
            bytes_=None,
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={},
        )


@pytest.fixture
def moderation_on(monkeypatch):
    monkeypatch.setattr(settings, "moderation_enabled", True)


def _use(monkeypatch, classifier):
    monkeypatch.setattr(moderation_service, "get_llm_provider", lambda: classifier)
    return classifier


async def _meld(db):
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    meld = await melds_service.create_meld(db, user.id, "T")
    await melds_service.save_story(db, user.id, meld.id, STORY)
    return user, meld


async def test_the_gate_is_off_by_default(monkeypatch):
    """The stub LLM cannot classify anything, so leaving this on in local
    development would mean a fixed stub verdict decided what a user may
    write."""
    classifier = _use(monkeypatch, _Classifier(allowed=False, reason="no"))
    assert (await check("anything at all")).allowed is True
    assert classifier.calls == 0


async def test_an_allowed_text_passes(monkeypatch, moderation_on):
    _use(monkeypatch, _Classifier(allowed=True))
    assert await check(STORY) == Verdict(allowed=True, reason="")


async def test_a_refused_text_carries_its_reason(monkeypatch, moderation_on):
    _use(monkeypatch, _Classifier(allowed=False, reason="Rewrite the third paragraph."))
    verdict = await check(STORY)
    assert verdict.allowed is False
    assert verdict.reason == "Rewrite the third paragraph."


async def test_empty_text_is_not_sent_to_a_classifier(monkeypatch, moderation_on):
    classifier = _use(monkeypatch, _Classifier(allowed=False))
    assert (await check("   ")).allowed is True
    assert classifier.calls == 0


async def test_moderation_failing_open_does_not_block_the_user(monkeypatch, moderation_on):
    """A provider outage is an availability problem. Turning it into 'nobody
    can generate anything' is a bigger outage than the window it closes --
    the same trade D7 made for the rate limiter."""
    _use(monkeypatch, _Classifier(error=ProviderTransportError("down")))
    assert (await check(STORY)).allowed is True


async def test_an_unreadable_verdict_fails_open(monkeypatch, moderation_on):
    class _Garbage(_Classifier):
        async def complete(self, *, system, user, json_schema):
            self.calls += 1
            return ProviderResult(
                text="I think that's fine?",
                bytes_=None,
                cost_usd=Decimal("0"),
                provider="fake",
                model="fake",
                raw={},
            )

    _use(monkeypatch, _Garbage())
    assert (await check(STORY)).allowed is True


async def test_a_verdict_is_cached_against_the_text_it_judged(db, monkeypatch, moderation_on):
    """Two Generate presses classify once."""
    _user, meld = await _meld(db)
    classifier = _use(monkeypatch, _Classifier(allowed=True))

    assert (await check_and_record(db, meld, STORY)).allowed is True
    assert (await check_and_record(db, meld, STORY)).allowed is True

    assert classifier.calls == 1
    assert meld.moderation_state is ModerationState.PASSED


async def test_rewriting_the_text_re_checks_it(db, monkeypatch, moderation_on):
    """A `passed` verdict must never outlive the words it approved --
    otherwise a user gets a benign story approved and then rewrites it."""
    _user, meld = await _meld(db)
    classifier = _use(monkeypatch, _Classifier(allowed=True))

    await check_and_record(db, meld, STORY)
    await check_and_record(db, meld, STORY + " Then something else entirely happens.")

    assert classifier.calls == 2


async def test_flagged_content_is_recorded_not_deleted(db, monkeypatch, moderation_on):
    """Spec 4.5: moderation_state exists so flagged content can be reviewed
    rather than silently vanishing."""
    user, meld = await _meld(db)
    _use(monkeypatch, _Classifier(allowed=False, reason="no"))

    verdict = await check_and_record(db, meld, STORY)

    assert verdict.allowed is False
    assert meld.moderation_state is ModerationState.FLAGGED
    assert meld.moderation_hash
    # The story is still there to be looked at.
    stored = await melds_service.get_meld(db, user.id, meld.id)
    assert stored is not None
    assert stored.story.text == STORY


async def test_a_flagged_verdict_is_not_cached_as_a_pass(db, monkeypatch, moderation_on):
    """Only a pass short-circuits. A flagged text is re-checked, so fixing
    the classifier or the prompt does not leave a user permanently locked
    out of their own Meld."""
    _user, meld = await _meld(db)
    classifier = _use(monkeypatch, _Classifier(allowed=False, reason="no"))
    await check_and_record(db, meld, STORY)

    _use(monkeypatch, classifier := _Classifier(allowed=True))
    assert (await check_and_record(db, meld, STORY)).allowed is True
    assert classifier.calls == 1
    assert meld.moderation_state is ModerationState.PASSED


# --- Through the API, which is where the money is ------------------------


async def _meld_with_story(client, text=STORY):
    created = await client.post("/melds", json={"title": "T"})
    meld_id = created.json()["id"]
    assert (await client.put(f"/melds/{meld_id}/story", json={"text": text})).status_code == 200
    return meld_id


async def test_a_blocked_story_costs_nothing(logged_in, db, monkeypatch, moderation_on):
    """The whole point of gating before the hold: a refusal must not create
    a job, must not move the balance, and must not reach an image
    provider."""
    _use(monkeypatch, _Classifier(allowed=False, reason="Rewrite the opening."))
    meld_id = await _meld_with_story(logged_in)
    before = (await logged_in.get("/credits")).json()["balance"]

    response = await logged_in.post(f"/melds/{meld_id}/cast/generate")

    assert response.status_code == 422
    assert response.json()["detail"] == "Rewrite the opening."
    assert (await logged_in.get("/credits")).json()["balance"] == before
    assert (await db.execute(select(Job))).scalars().all() == []


async def test_an_allowed_story_still_generates(logged_in, db, monkeypatch, moderation_on):
    """The gate must not become an outage for ordinary writing."""
    _use(monkeypatch, _Classifier(allowed=True))
    meld_id = await _meld_with_story(logged_in)

    response = await logged_in.post(f"/melds/{meld_id}/cast/generate")

    assert response.status_code == 202, response.text


async def test_a_flagged_verdict_survives_the_refusal(logged_in, db, monkeypatch, moderation_on):
    """Spec 4.5 wants flagged content recorded for review. A verdict that
    rolled back with the failed request would be recorded nowhere, and
    every retry would classify from scratch."""
    classifier = _use(monkeypatch, _Classifier(allowed=False, reason="Rewrite the opening."))
    meld_id = await _meld_with_story(logged_in)

    assert (await logged_in.post(f"/melds/{meld_id}/cast/generate")).status_code == 422

    # Read from the database, not the API: the Meld schema does not expose
    # moderation_state, and nothing in M3 needs it to -- the point is that
    # the row survived the refusal for a human to review.
    db.expire_all()
    stored = (await db.execute(select(Meld).where(Meld.id == uuid.UUID(meld_id)))).scalar_one()
    assert stored.moderation_state is ModerationState.FLAGGED
    # A second press re-classifies rather than replaying the refusal. Only
    # a pass is cached (see test_a_flagged_verdict_is_not_cached_as_a_pass),
    # so a fixed classifier or a reworded prompt cannot leave a user locked
    # out of their own Meld. The cost is one short classification per retry,
    # which is the cheap side of that trade.
    assert (await logged_in.post(f"/melds/{meld_id}/cast/generate")).status_code == 422
    assert classifier.calls == 2

import json
from decimal import Decimal

import pytest

from framemeld.providers.base import (
    ProviderRefusedError,
    ProviderResponseError,
    ProviderTransportError,
)
from framemeld.providers.registry import get_llm_provider
from framemeld.providers.stub import StubLLMProvider

STORY = (
    "Elena found the letter beneath the floorboard. She read it twice. "
    "Her hands would not stop shaking.\n\n"
    "Outside, the rain began. She burned the letter in the sink."
)


async def _script(story=STORY):
    result = await StubLLMProvider().complete(system="s", user=story, json_schema={})
    return json.loads(result.text)


async def test_the_stub_costs_nothing():
    result = await StubLLMProvider().complete(system="s", user=STORY, json_schema={})
    assert result.cost_usd == Decimal("0")
    assert result.provider == "stub"


async def test_the_stub_is_deterministic():
    """Otherwise every test that asserts on generated content is flaky."""
    assert await _script() == await _script()


async def test_the_stub_derives_its_scenes_from_the_story():
    """A stub that ignored its input would let a broken prompt path pass
    every test in this milestone."""
    script = await _script()
    assert len(script["scenes"]) == 2
    assert script["scenes"][0]["shots"][0]["image_prompt"].startswith("Elena found the letter")


async def test_different_stories_give_different_scripts():
    assert await _script("A single sentence.") != await _script(STORY)


async def test_no_shot_exceeds_five_seconds():
    script = await _script()
    for scene in script["scenes"]:
        for shot in scene["shots"]:
            assert 0 < float(shot["duration_sec"]) <= 5.0


async def test_every_shot_has_a_motion_prompt():
    """Image-to-video needs one per shot (spec section 8); a stub that
    omitted it would hide the omission until M6."""
    script = await _script()
    assert all(shot["motion_prompt"] for scene in script["scenes"] for shot in scene["shots"])


async def test_an_empty_story_is_refused_rather_than_invented():
    with pytest.raises(ProviderRefusedError):
        await StubLLMProvider().complete(system="s", user="   ", json_schema={})


def test_the_registry_returns_the_stub_by_default():
    assert get_llm_provider().name == "stub"


def test_the_registry_rejects_an_unknown_provider(monkeypatch):
    from framemeld.config import settings

    monkeypatch.setattr(settings, "llm_provider", "nope")
    with pytest.raises(ValueError, match="nope"):
        get_llm_provider()


@pytest.mark.parametrize(
    ("error_cls", "retryable"),
    [
        (ProviderTransportError, True),
        (ProviderRefusedError, False),
        (ProviderResponseError, False),
    ],
)
def test_the_retry_verdict_matches_the_error_kind(error_cls, retryable):
    """Task 8's worker re-queues a job on this flag alone.

    Inverted, it either drops work silently or spends money retrying a
    refusal that will refuse again — and the symptom appears in the worker,
    nowhere near the one-character cause here.
    """
    assert error_cls("boom").retryable is retryable

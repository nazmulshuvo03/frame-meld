import json
import re
from decimal import Decimal
from typing import Any

from framemeld.config import settings
from framemeld.providers.base import ProviderRefusedError, ProviderResult

# Deterministic rotations, so a test can assert an exact script. Chosen to
# exercise every value at least once across a handful of shots rather than
# to look like good direction.
_CAMERAS = ("establishing", "medium", "close", "wide", "insert")
_DURATIONS = ("3.00", "4.00", "5.00", "2.50")

_SENTENCE = re.compile(r"(?<=[.!?])\s+")

MAX_SHOTS_PER_SCENE = 5


class StubLLMProvider:
    """A local, deterministic LLM that costs nothing and never leaves the
    process.

    It is the default provider (see settings.llm_provider) so that the app
    runs and the whole test suite passes with no API key. Its output is
    *derived from the story* rather than fixed, because a stub that ignored
    its input would let a broken prompt-building path pass every test in
    this milestone.

    It reports `settings.llm_model` as its model so that script_input_hash,
    which reads the same setting, describes what actually ran.
    """

    name = "stub"

    def __init__(self) -> None:
        self.model = settings.llm_model

    async def complete(
        self, *, system: str, user: str, json_schema: dict[str, Any]
    ) -> ProviderResult:
        if not user.strip():
            # A real model asked to dramatise nothing would refuse or
            # hallucinate. Refusing keeps the failure honest and gives the
            # error path something to exercise.
            raise ProviderRefusedError("There is no story to work from yet.")

        scenes = []
        shot_number = 0
        for index, paragraph in enumerate(self._paragraphs(user)):
            shots = []
            for sentence in self._sentences(paragraph)[:MAX_SHOTS_PER_SCENE]:
                shots.append(
                    {
                        "image_prompt": sentence,
                        "motion_prompt": f"Slow {'push in' if shot_number % 2 else 'drift left'}.",
                        "duration_sec": _DURATIONS[shot_number % len(_DURATIONS)],
                        "camera": _CAMERAS[shot_number % len(_CAMERAS)],
                        # Biased to narration: lip-sync is unsolved cheaply
                        # (spec section 8), so the stub models the same bias
                        # the real prompt asks for.
                        "dialogue_kind": "narration" if shot_number % 3 == 0 else "none",
                        "speech_text": sentence if shot_number % 3 == 0 else "",
                    }
                )
                shot_number += 1
            if shots:
                scenes.append(
                    {
                        "heading": f"SCENE {index + 1}",
                        "summary": shots[0]["image_prompt"],
                        "location": "Unspecified",
                        "time_of_day": "day",
                        "shots": shots,
                    }
                )

        payload = {"scenes": scenes}
        return ProviderResult(
            text=json.dumps(payload),
            bytes_=None,
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"stub": True},
        )

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        return [block.strip() for block in text.split("\n\n") if block.strip()]

    @staticmethod
    def _sentences(paragraph: str) -> list[str]:
        return [s.strip() for s in _SENTENCE.split(paragraph) if s.strip()]

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

#: Below models.cast.MAX_CHARACTERS, so the stub never exercises the
#: parser's truncation path by accident and a test that wants it has to
#: ask for it explicitly.
MAX_STUB_CHARACTERS = 6

#: Words that are capitalised in prose without being names. Cosmetic only --
#: a stub that cast "She" as a lead would still be deterministic and
#: story-derived, just confusing to look at during manual testing.
_NOT_NAMES = frozenset(
    """a an the and but or so then now here there this that these those
    he she it they we you i his her its their our your him them us me my
    when while after before outside inside above below once again still
    if as at in on of to for from with without by up down over under
    one two three no yes not never always""".split()
)

_HAIR = ("dark", "silver", "red", "fair", "greying", "black")
_CLOTHES = (
    "wool coat",
    "plain shirt",
    "long raincoat",
    "knitted cardigan",
    "denim jacket",
    "grey suit",
)


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

        # Which artifact was asked for. Branching on the schema's name
        # rather than on the system prompt because the name is the stable
        # contract -- the prompt is prose and gets reworded.
        if json_schema.get("name") == "characters":
            payload: dict[str, Any] = {"characters": self._characters(user)}
        else:
            payload = {"scenes": self._scenes(user)}

        return ProviderResult(
            text=json.dumps(payload),
            bytes_=None,
            cost_usd=Decimal("0"),
            provider=self.name,
            model=self.model,
            raw={"stub": True},
        )

    def _characters(self, user: str) -> list[dict[str, str]]:
        """A cast derived from the story's own proper nouns.

        Derived rather than fixed for the same reason the scene list is: a
        stub that returned the same two names for every story would let a
        broken prompt-building path pass every test here. Falls back to one
        unnamed figure when a story contains no capitalised names at all,
        because an empty cast is a terminal parse error and "this story has
        no proper nouns" is not a generation failure.
        """
        names = self._proper_nouns(user)[:MAX_STUB_CHARACTERS] or ["The Narrator"]
        return [
            {
                "name": name,
                "role": "lead" if index == 0 else "supporting",
                "description": f"Appears in the story. Mentioned as {name}.",
                # Deterministic but not identical between characters: two
                # characters sharing an appearance would make two sheets
                # byte-identical and hide a broken per-character path.
                "appearance": (
                    f"A person of average build with {_HAIR[index % len(_HAIR)]} hair, "
                    f"wearing a {_CLOTHES[index % len(_CLOTHES)]}."
                ),
            }
            for index, name in enumerate(names)
        ]

    @staticmethod
    def _proper_nouns(text: str) -> list[str]:
        """Capitalised words, minus the ones that are only capitalised
        because they began a sentence.

        Crude on purpose -- it has to be deterministic and story-dependent,
        not correct English. Skipping every sentence-initial word instead
        would look more principled and work much worse: in ordinary prose
        most character names introduce their own sentence, so that rule
        discarded nearly every real name and left only the fallback cast.
        """
        seen: list[str] = []
        for word in text.split():
            candidate = word.strip(".,;:!?\"'()-").strip()
            if (
                candidate[:1].isupper()
                and candidate.isalpha()
                and candidate.lower() not in _NOT_NAMES
                and candidate not in seen
            ):
                seen.append(candidate)
        return seen

    def _scenes(self, user: str) -> list[dict[str, Any]]:
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
        return scenes

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        return [block.strip() for block in text.split("\n\n") if block.strip()]

    @staticmethod
    def _sentences(paragraph: str) -> list[str]:
        return [s.strip() for s in _SENTENCE.split(paragraph) if s.strip()]

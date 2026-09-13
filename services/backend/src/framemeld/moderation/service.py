import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from framemeld.config import settings
from framemeld.models.meld import Meld, ModerationState
from framemeld.providers.base import ProviderError
from framemeld.providers.registry import get_llm_provider
from framemeld.script.hashes import content_hash_for_story

logger = logging.getLogger(__name__)

MODERATION_JSON_SCHEMA = {
    "name": "moderation",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["allowed", "reason"],
        "properties": {
            "allowed": {"type": "boolean"},
            # Shown to the user verbatim, so the model is told to write it
            # for them rather than for a log.
            "reason": {"type": "string"},
        },
    },
}

_SYSTEM = """\
You decide whether a piece of text may be sent to an image generation \
model for a short film. Refuse only these: sexual content involving \
minors; instructions for real-world violence or weapons; content whose \
purpose is to depict a real, identifiable person in a false or degrading \
way; and content promoting hatred against a protected group.

Fiction is allowed. Dark, violent, frightening and adult themes in a \
story are allowed. You are not a taste filter and not a rating board -- \
refusing ordinary dramatic writing is a worse failure than allowing it.

If you refuse, `reason` is one short sentence addressed to the writer, \
naming what to change. Never quote the text back.
"""


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""


class Blocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def check(text: str) -> Verdict:
    """Classify one piece of text.

    Fails **open** on a provider error, logging at WARNING. This is the
    same trade D7 made for the rate limiter, for the same reason: the
    provider being down is an availability problem, and converting it into
    "nobody can generate anything" is a bigger outage than the window it
    closes. The gate exists to stop the obvious cases reaching a paid
    provider, not to be a security boundary.
    """
    if not settings.moderation_enabled or not text.strip():
        return Verdict(allowed=True)

    try:
        result = await get_llm_provider().complete(
            system=_SYSTEM, user=text, json_schema=MODERATION_JSON_SCHEMA
        )
        payload = json.loads(result.text or "")
        return Verdict(allowed=bool(payload["allowed"]), reason=str(payload.get("reason", "")))
    except (ProviderError, ValueError, KeyError, TypeError) as exc:
        logger.warning("moderation unavailable (%s); allowing", exc)
        return Verdict(allowed=True)


async def check_and_record(db: DbSession, meld: Meld, text: str) -> Verdict:
    """Classify, and remember the answer against the text it was about.

    Cached on the Meld so pressing Generate twice does not classify twice.
    The cache key is the text's content hash, so a rewrite re-checks -- a
    `passed` verdict must never outlive the words it approved.
    """
    text_hash = content_hash_for_story(text)
    if meld.moderation_hash == text_hash and meld.moderation_state is ModerationState.PASSED:
        return Verdict(allowed=True)

    verdict = await check(text)
    meld.moderation_hash = text_hash
    meld.moderation_state = ModerationState.PASSED if verdict.allowed else ModerationState.FLAGGED
    meld.updated_at = datetime.now(UTC)
    await db.flush()
    return verdict

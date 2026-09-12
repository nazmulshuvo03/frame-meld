import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_UNAVAILABLE = "rate limiter unavailable (%s); allowing the request"


async def retry_after(redis: Redis, key: str, limit: int) -> int | None:
    """Seconds the caller must wait, or None if it is under the limit.

    Fails open (D7): if Redis cannot answer, the request is allowed and a
    warning is logged. A Redis outage already means the job queue is down,
    so turning it into a total login outage trades a small security window
    for a large availability loss.
    """
    try:
        current = await redis.get(key)
        if current is None or int(current) < limit:
            return None
        ttl = await redis.ttl(key)
    except RedisError as exc:
        logger.warning(_UNAVAILABLE, exc)
        return None

    # ttl is -1 for a key with no expiry and -2 for a missing key; neither
    # should be reachable here, but reporting a negative Retry-After would
    # be worse than reporting a conservative one.
    return max(ttl, 1)


async def record_failure(redis: Redis, key: str, window_seconds: int) -> None:
    """Count one failure into the current window.

    `nx=True` on EXPIRE sets the TTL only when the key has none, which is
    what makes this a *fixed* window: the clock starts at the first failure
    and later failures cannot push it out. A sliding window here would let
    an attacker keep a victim's account locked indefinitely by failing once
    every few minutes.
    """
    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        await pipe.execute()
    except RedisError as exc:
        logger.warning(_UNAVAILABLE, exc)


async def clear(redis: Redis, key: str) -> None:
    try:
        await redis.delete(key)
    except RedisError as exc:
        logger.warning(_UNAVAILABLE, exc)

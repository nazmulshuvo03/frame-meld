from redis.asyncio import Redis

from framemeld.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """A process-wide client, created on first use.

    Used as a FastAPI dependency so tests can override it with a fake
    without the real one ever opening a socket. redis-py's asyncio client
    is connection-pooled and safe to share across requests; creating one
    per request would exhaust file descriptors under load.

    decode_responses=True because every value this app keeps in Redis is a
    counter or a short string, and callers should not each have to
    remember to decode bytes.
    """
    global _client
    if _client is None:
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

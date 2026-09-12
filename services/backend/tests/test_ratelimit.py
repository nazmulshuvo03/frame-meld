import pytest
from redis.exceptions import RedisError

from framemeld.ratelimit import clear, record_failure, retry_after


class BrokenRedis:
    """Every command raises, which is what an unreachable Redis looks like
    to redis-py once the connection attempt fails."""

    async def get(self, *args, **kwargs):
        raise RedisError("connection refused")

    async def ttl(self, *args, **kwargs):
        raise RedisError("connection refused")

    async def delete(self, *args, **kwargs):
        raise RedisError("connection refused")

    def pipeline(self, *args, **kwargs):
        raise RedisError("connection refused")


async def test_under_the_limit_is_allowed(fake_redis):
    await record_failure(fake_redis, "k", 900)
    assert await retry_after(fake_redis, "k", limit=3) is None


async def test_at_the_limit_reports_a_wait(fake_redis):
    for _ in range(3):
        await record_failure(fake_redis, "k", 900)
    wait = await retry_after(fake_redis, "k", limit=3)
    assert wait is not None
    assert 0 < wait <= 900


async def test_the_window_does_not_slide(fake_redis):
    """The TTL is set once, by the first failure. A later failure must not
    extend it, or a persistent attacker could hold a victim locked out
    indefinitely."""
    await record_failure(fake_redis, "k", 900)
    first_ttl = await fake_redis.ttl("k")
    await record_failure(fake_redis, "k", 900)
    assert await fake_redis.ttl("k") <= first_ttl


async def test_clear_resets_the_counter(fake_redis):
    for _ in range(5):
        await record_failure(fake_redis, "k", 900)
    await clear(fake_redis, "k")
    assert await retry_after(fake_redis, "k", limit=1) is None


@pytest.mark.parametrize("call", ["retry_after", "record_failure", "clear"])
async def test_an_unreachable_redis_fails_open(call):
    """D7: a Redis outage must not become a login outage."""
    broken = BrokenRedis()
    if call == "retry_after":
        assert await retry_after(broken, "k", limit=1) is None
    elif call == "record_failure":
        await record_failure(broken, "k", 900)
    else:
        await clear(broken, "k")

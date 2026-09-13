import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from framemeld.config import settings


class Queue(Protocol):
    async def enqueue(self, task: str, job_id: uuid.UUID) -> None: ...


class ArqQueue:
    """The real queue. Deliberately thin: it converts a job id to a string
    and hands it to arq, and knows nothing about what the task does."""

    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, task: str, job_id: uuid.UUID) -> None:
        await self._pool.enqueue_job(task, str(job_id))


class RecordingQueue:
    """Records what would have been enqueued.

    A test double rather than a mock, so a test can assert *that* a job was
    dispatched without a Redis running and without arq's message format
    leaking into assertions.
    """

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, uuid.UUID]] = []

    async def enqueue(self, task: str, job_id: uuid.UUID) -> None:
        self.enqueued.append((task, job_id))


_pool: ArqRedis | None = None


async def get_queue() -> Queue:
    """FastAPI dependency. The pool is process-wide and created on first
    use, for the same reason the Redis client is."""
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return ArqQueue(_pool)


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None

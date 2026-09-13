import asyncio
from pathlib import Path

from framemeld.storage.base import safe_key


class LocalDiskStorage:
    """Files under one directory. The development backend, and the reason
    M3 needs no cloud credentials to run end to end."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / safe_key(key)).resolve()
        # Belt and braces. safe_key already makes traversal unrepresentable;
        # this is the check that still holds if someone loosens that regex
        # later without thinking about what it was protecting.
        if not path.is_relative_to(self._root):
            raise ValueError("Storage key escapes the storage root")
        return path

    async def put(self, *, key: str, data: bytes, mime: str) -> None:
        # `mime` is unused here and deliberately still in the signature: S3
        # needs it as ContentType at write time, and a protocol that only
        # fits the local backend would have to change the day the second
        # one arrives.
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # to_thread because this runs inside the arq worker's event loop. A
        # couple of megabytes to a slow disk would otherwise stall every
        # other job in the same process for the duration of the write.
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, *, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

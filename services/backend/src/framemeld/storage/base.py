import re
from typing import Protocol

#: <user uuid>/<asset uuid>.<ext>, and nothing else. Keys are built by this
#: codebase and never by a user, but they become filesystem paths, so the
#: shape is enforced rather than trusted. With this pattern a traversal
#: sequence is not merely rejected, it is unrepresentable.
_SAFE_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"\.[a-z0-9]{2,5}$"
)


def safe_key(key: str) -> str:
    if not _SAFE_KEY.fullmatch(key):
        raise ValueError(f"Unsafe storage key: {key!r}")
    return key


class Storage(Protocol):
    async def put(self, *, key: str, data: bytes, mime: str) -> None: ...

    async def get(self, *, key: str) -> bytes: ...

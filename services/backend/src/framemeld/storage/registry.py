from pathlib import Path

from framemeld.config import settings
from framemeld.storage.base import Storage
from framemeld.storage.local import LocalDiskStorage


def get_storage() -> Storage:
    """The single place a backend name becomes an implementation, mirroring
    providers/registry.py. An unknown name raises rather than falling back,
    so a typo in production fails at startup instead of writing a user's
    artwork somewhere nobody looks."""
    if settings.storage_backend == "local":
        return LocalDiskStorage(Path(settings.storage_local_root))
    raise ValueError(f"Unknown STORAGE_BACKEND: {settings.storage_backend!r}")

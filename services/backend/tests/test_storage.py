import pytest

from framemeld.storage.base import safe_key
from framemeld.storage.local import LocalDiskStorage


def test_a_well_formed_key_is_accepted():
    key = "0f2b1d1e-6f3a-4a1e-9c2b-6f9d2e7a1c33/9a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d.png"
    assert safe_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "../../../etc/passwd",
        "0f2b1d1e-6f3a-4a1e-9c2b-6f9d2e7a1c33/../secrets.png",
        "0f2b1d1e-6f3a-4a1e-9c2b-6f9d2e7a1c33/nested/deep.png",
        "not-a-uuid/also-not.png",
        "",
    ],
)
def test_a_key_that_is_not_two_uuids_is_refused(key):
    """Keys are built by this codebase, never by a user -- but they end up
    as filesystem paths, so the shape is enforced rather than assumed.
    Traversal is unrepresentable when the only legal key is
    <uuid>/<uuid>.<ext>."""
    with pytest.raises(ValueError):
        safe_key(key)


async def test_bytes_round_trip(tmp_path):
    storage = LocalDiskStorage(tmp_path)
    key = "0f2b1d1e-6f3a-4a1e-9c2b-6f9d2e7a1c33/9a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d.png"

    await storage.put(key=key, data=b"\x89PNG-ish", mime="image/png")

    assert await storage.get(key=key) == b"\x89PNG-ish"
    assert (tmp_path / key).exists()


async def test_reading_a_missing_key_raises(tmp_path):
    storage = LocalDiskStorage(tmp_path)
    with pytest.raises(FileNotFoundError):
        await storage.get(
            key="0f2b1d1e-6f3a-4a1e-9c2b-6f9d2e7a1c33/9a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d.png"
        )

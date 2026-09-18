import time
import uuid

import pytest

from framemeld.assets.signing import (
    SigningNotConfigured,
    sign_asset,
    signed_asset_url,
    verify_asset_signature,
)
from framemeld.config import settings


def _parts(query: str) -> tuple[str, str]:
    fields = dict(pair.split("=", 1) for pair in query.split("&"))
    return fields["exp"], fields["sig"]


def test_a_signature_this_system_made_is_accepted():
    asset_id = uuid.uuid4()
    expires, signature = _parts(sign_asset(asset_id))
    assert verify_asset_signature(asset_id, expires, signature) is True


def test_a_signature_cannot_be_moved_to_another_asset():
    """The whole point of putting the id inside the signed message. Without
    it, one signed URL would be a key to the user's entire library."""
    signed, other = uuid.uuid4(), uuid.uuid4()
    expires, signature = _parts(sign_asset(signed))
    assert verify_asset_signature(other, expires, signature) is False


def test_an_expired_signature_is_refused():
    asset_id = uuid.uuid4()
    expires, signature = _parts(sign_asset(asset_id, ttl_seconds=-1))
    assert int(expires) < time.time()
    assert verify_asset_signature(asset_id, expires, signature) is False


def test_the_expiry_cannot_be_moved_forward_without_the_key():
    """`exp` is in the signed message too, so a caller who found a dead URL
    cannot simply raise the number."""
    asset_id = uuid.uuid4()
    expires, signature = _parts(sign_asset(asset_id, ttl_seconds=-1))
    assert verify_asset_signature(asset_id, str(int(expires) + 100_000), signature) is False


@pytest.mark.parametrize(
    ("expires", "signature"),
    [(None, None), ("", "abc"), ("not-a-number", "abc"), ("99999999999", "")],
)
def test_a_malformed_signature_is_refused_rather_than_raising(expires, signature):
    assert verify_asset_signature(uuid.uuid4(), expires, signature) is False


def test_signing_without_a_key_raises_rather_than_using_a_default(monkeypatch):
    """A signing secret with a default is a signing secret everybody has."""
    monkeypatch.setattr(settings, "asset_signing_secret", "")
    with pytest.raises(SigningNotConfigured):
        sign_asset(uuid.uuid4())


def test_verification_without_a_key_refuses_rather_than_raising(monkeypatch):
    """The signing side must fail loudly; the verifying side must not, or a
    missing key would turn every asset request into a 500."""
    asset_id = uuid.uuid4()
    expires, signature = _parts(sign_asset(asset_id))
    monkeypatch.setattr(settings, "asset_signing_secret", "")
    assert verify_asset_signature(asset_id, expires, signature) is False


def test_a_different_key_does_not_verify(monkeypatch):
    asset_id = uuid.uuid4()
    expires, signature = _parts(sign_asset(asset_id))
    monkeypatch.setattr(settings, "asset_signing_secret", "a-different-secret")
    assert verify_asset_signature(asset_id, expires, signature) is False


def test_the_url_is_absolute_and_built_from_the_configured_api(monkeypatch):
    """Not from the inbound request: the consumer is another service on
    another host, and a `localhost` inferred from a Host header would point
    the render service at itself."""
    monkeypatch.setattr(settings, "public_api_url", "https://api.example.test/")
    asset_id = uuid.uuid4()
    url = signed_asset_url(asset_id)
    assert url.startswith(f"https://api.example.test/assets/{asset_id}?")
    assert "exp=" in url and "sig=" in url

import hashlib
import hmac
import time
import uuid

from framemeld.config import settings

#: How long a signed asset URL stays valid. Long enough for a render that
#: takes minutes to fetch every still it needs, short enough that a URL
#: found in a log tomorrow is already dead.
DEFAULT_TTL_SECONDS = 3600


class SigningNotConfigured(RuntimeError):
    """ASSET_SIGNING_SECRET is empty.

    Raised rather than falling back to a built-in key: a signing secret
    with a default is a signing secret everybody has, and the failure it
    would cause -- anyone able to read any user's artwork -- is silent.
    """


def _secret() -> bytes:
    if not settings.asset_signing_secret:
        raise SigningNotConfigured(
            "ASSET_SIGNING_SECRET is not set, so asset URLs cannot be signed."
        )
    return settings.asset_signing_secret.encode()


def _signature(asset_id: uuid.UUID, expires_at: int) -> str:
    # The asset id is inside the signed message, so a signature cannot be
    # lifted from one file's URL and pasted onto another's.
    message = f"{asset_id}:{expires_at}".encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def sign_asset(asset_id: uuid.UUID, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """The query string that lets a cookie-less client fetch one asset.

    The render service has no session and no database (spec section 3.1),
    so the scene spec hands it URLs rather than ids. This is the whole of
    that mechanism: twenty lines of HMAC over the route that already exists
    (M5-D1), rather than an S3 presigner and the account behind it.
    """
    expires_at = int(time.time()) + ttl_seconds
    return f"exp={expires_at}&sig={_signature(asset_id, expires_at)}"


def signed_asset_url(asset_id: uuid.UUID, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """An absolute URL the render service can GET.

    Absolute, and built from PUBLIC_API_URL rather than from the request:
    the consumer is another service on another host, so a relative path or
    a `localhost` inferred from an inbound Host header would resolve to the
    wrong machine exactly when it matters.
    """
    base = settings.public_api_url.rstrip("/")
    return f"{base}/assets/{asset_id}?{sign_asset(asset_id, ttl_seconds=ttl_seconds)}"


def verify_asset_signature(asset_id: uuid.UUID, expires: str | None, signature: str | None) -> bool:
    """Whether this signature really authorises this asset, right now.

    Returns False for every failure rather than raising per cause: a caller
    that could tell "expired" from "wrong key" from "malformed" would hand
    that distinction to an attacker, and no caller here needs it.
    """
    if not expires or not signature:
        return False
    try:
        expires_at = int(expires)
    except ValueError:
        return False
    if expires_at < time.time():
        return False
    try:
        expected = _signature(asset_id, expires_at)
    except SigningNotConfigured:
        return False
    # compare_digest, not ==: string comparison returns early on the first
    # differing byte, which leaks how much of a guess was right.
    return hmac.compare_digest(expected, signature)

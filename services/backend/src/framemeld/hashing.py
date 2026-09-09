import hashlib
import json
from typing import Any


def canonical_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of a payload serialised deterministically.

    This is the single implementation of `input_hash` for the whole
    system (spec section 4.1). Every generated artifact hashes the
    upstream artifacts' input_hash values plus its own prompt, provider,
    model and params through this function.

    sort_keys makes key order irrelevant; separators removes the
    whitespace that would otherwise vary between Python versions;
    ensure_ascii keeps the bytes stable regardless of locale.
    """
    serialised = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()

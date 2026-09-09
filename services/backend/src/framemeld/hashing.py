import hashlib
import json
from typing import Any


def canonical_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of a payload serialised deterministically.

    This is the single implementation of `input_hash` for the whole
    system (spec section 4.1). Every generated artifact hashes the
    upstream artifacts' input_hash values plus its own prompt, provider,
    model and params through this function.

    sort_keys makes mapping key order irrelevant; separators removes the
    whitespace that would otherwise vary between Python versions;
    ensure_ascii keeps the bytes stable regardless of locale.

    Only JSON-native values are accepted. Anything else — datetime,
    Decimal, UUID, set, a custom object — raises TypeError, deliberately.
    Coercing them with str() would let a typed value collide with its own
    string form, and would make any set hash differently on every process
    restart. Callers must convert to a JSON-native value explicitly.

    One known ambiguity is accepted: tuples and lists serialise
    identically, so (1, 2) and [1, 2] hash the same. Both mean "ordered
    sequence" here, so that is intended rather than overlooked.
    """
    serialised = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()

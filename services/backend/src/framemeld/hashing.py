import hashlib
import json
from typing import Any


def canonical_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of a payload serialised deterministically.

    This is the single implementation of both hashes the staleness engine
    relies on (spec section 4.1): `content_hash`, what a row *says* right
    now, hashed from its own editable fields; and `input_hash`, what
    *produced* a generated row, hashed from the upstream artifacts'
    content_hash values plus its own prompt, provider, model and params.
    Downstream depends on upstream's content_hash, never its input_hash —
    input_hash records which generation produced a row, not what the row
    currently says, so keying off it would leave an edited upstream row
    unable to mark its dependents stale.

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

from framemeld.hashing import canonical_hash


def test_returns_64_char_lowercase_hex():
    result = canonical_hash({"a": 1})
    assert len(result) == 64
    assert result == result.lower()
    assert all(c in "0123456789abcdef" for c in result)


def test_key_order_does_not_change_the_hash():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_different_values_produce_different_hashes():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_nested_key_order_does_not_change_the_hash():
    left = canonical_hash({"outer": {"x": 1, "y": 2}})
    right = canonical_hash({"outer": {"y": 2, "x": 1}})
    assert left == right


def test_none_and_missing_key_are_different():
    """A field explicitly set to null must not hash the same as an absent
    field. Otherwise clearing a prompt would look like never having set one,
    and the artifact would wrongly stay fresh."""
    assert canonical_hash({"a": None}) != canonical_hash({})


def test_int_and_string_are_different():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": "1"})


def test_list_order_is_significant():
    """Shot order matters, so list order must affect the hash."""
    assert canonical_hash({"a": [1, 2]}) != canonical_hash({"a": [2, 1]})

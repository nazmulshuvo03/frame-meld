from framemeld.auth.passwords import hash_password, verify_password


def test_hash_is_not_the_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$argon2")


def test_correct_password_verifies():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_wrong_password_does_not_verify():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False


def test_same_password_hashes_differently_each_time():
    """Argon2 salts every hash, so two hashes of one password must differ.
    Identical hashes would leak which accounts share a password."""
    assert hash_password("same") != hash_password("same")


def test_malformed_hash_returns_false_rather_than_raising():
    """A corrupt or legacy value in the column must fail the login, not
    500 the endpoint."""
    assert verify_password("anything", "not-a-valid-hash") is False

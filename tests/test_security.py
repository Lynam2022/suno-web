from src.security import is_authorized


def test_no_keys_configured_means_open():
    assert is_authorized(None, set()) is True
    assert is_authorized("anything", set()) is True


def test_keys_configured_requires_match():
    keys = {"k1", "k2"}
    assert is_authorized("k1", keys) is True
    assert is_authorized("k2", keys) is True
    assert is_authorized("wrong", keys) is False
    assert is_authorized(None, keys) is False
    assert is_authorized("", keys) is False

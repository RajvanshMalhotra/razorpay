import pytest
from exchange.config import Config


def test_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret123")
    monkeypatch.setenv("EXCHANGE_DB_PATH", "runs/test.db")

    cfg = Config.from_env()

    assert cfg.razorpay_key_id == "rzp_test_abc"
    assert cfg.razorpay_key_secret == "secret123"
    assert cfg.db_path == "runs/test.db"


def test_the_privacy_floor_is_defined_in_exactly_one_place():
    """It used to be defined twice — here and in insights — and only one of
    them was read. Two constants that must agree, and nothing making them."""
    import exchange.config as config

    assert not hasattr(config, "K_MIN")
    assert not hasattr(Config, "k_min")


def test_from_env_rejects_live_keys(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret123")

    with pytest.raises(ValueError, match="test mode"):
        Config.from_env()


def test_from_env_requires_key_id(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret123")

    with pytest.raises(ValueError, match="RAZORPAY_KEY_ID"):
        Config.from_env()

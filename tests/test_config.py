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
    assert cfg.k_min == 25
    assert cfg.max_negotiation_rounds == 4


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

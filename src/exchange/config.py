"""Configuration loaded from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass

K_MIN = 25
MAX_NEGOTIATION_ROUNDS = 4


@dataclass(frozen=True)
class Config:
    razorpay_key_id: str
    razorpay_key_secret: str
    db_path: str
    k_min: int = K_MIN
    max_negotiation_rounds: int = MAX_NEGOTIATION_ROUNDS

    @classmethod
    def from_env(cls) -> "Config":
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")

        if not key_id:
            raise ValueError("RAZORPAY_KEY_ID is not set")
        if not key_secret:
            raise ValueError("RAZORPAY_KEY_SECRET is not set")
        if not key_id.startswith("rzp_test_"):
            raise ValueError(
                f"Refusing to run: key {key_id[:12]}... is not a test mode key. "
                "This project runs in test mode only."
            )

        return cls(
            razorpay_key_id=key_id,
            razorpay_key_secret=key_secret,
            db_path=os.environ.get("EXCHANGE_DB_PATH", "runs/exchange.db"),
        )

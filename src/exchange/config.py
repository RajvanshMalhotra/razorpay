"""Configuration loaded from the environment.

Two constants used to live here and neither did anything.

`K_MIN` was a SECOND definition of the privacy floor. `check_privacy` defaults
to `insights.K_MIN` and no caller ever passed `Config.k_min`, so the
configurable-looking one was inert — two numbers that had to agree, with
nothing making them agree, and the wrong one is the one a reader finds first.
The floor is defined once, in `house/insights.py`, where the check that
enforces it lives.

`MAX_NEGOTIATION_ROUNDS` contradicted the design it appeared to configure:
`negotiate()` bounds by token budget and stall detection on purpose, and a
round cap nothing reads is a claim about how negotiation is bounded that is
not true.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    razorpay_key_id: str
    razorpay_key_secret: str
    db_path: str

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

"""Prefixed identifier generation."""
from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """Return an id like 'evt_9f3c1a2b4d5e'."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

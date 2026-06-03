"""Turn stored experiences into training arrays for the brain."""
from __future__ import annotations

from tradebot.models import Experience


def to_xy(experiences: list[Experience]) -> tuple[list[list[float]], list[int]]:
    X = [e.features for e in experiences if e.features]
    y = [int(e.won) for e in experiences if e.features]
    return X, y

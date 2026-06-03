"""Turn stored experiences into training arrays for the brain.

The brain learns P(trade wins), which is direction-dependent, so each row is the
stored outcome features PLUS the executed trade context (side, edge) — matching
``ml.features.build_brain_features`` used at inference time.
"""
from __future__ import annotations

from tradebot.models import Experience


def to_xy(experiences: list[Experience]) -> tuple[list[list[float]], list[int]]:
    X: list[list[float]] = []
    y: list[int] = []
    for e in experiences:
        if not e.features:
            continue
        X.append(list(e.features) + [1.0 if e.is_yes else 0.0, float(e.edge)])
        y.append(int(e.won))
    return X, y

"""Cold-start helpers: build predictor training data from resolved trades."""
from __future__ import annotations

from tradebot.models import Trade


def predictor_training_data(resolved: list[Trade]) -> tuple[list[list[float]], list[int]]:
    X: list[list[float]] = []
    y: list[int] = []
    for t in resolved:
        if t.features and t.resolved_yes is not None:
            X.append(t.features)
            y.append(int(t.resolved_yes))
    return X, y

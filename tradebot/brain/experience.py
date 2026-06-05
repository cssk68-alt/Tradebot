"""Turn stored experiences into training arrays for the brain.

The brain learns P(trade wins), which is direction-dependent, so each row is the
stored outcome features PLUS the executed trade context (side, edge) — matching
``ml.features.build_brain_features`` used at inference time.

COUNTERFACTUAL REFLECTION LEARNING
----------------------------------
Counterfactual trades (mirror / veto simulations) are NOT discarded — they carry
real price-path information about what WOULD have happened. However, they are
down-weighted relative to real trades so the learned decision boundary is driven
primarily by actual executed outcomes, not simulated ones.

``to_weighted_xy`` assigns sample_weights:
  - Real trades (executed):           weight = 1.0
  - Counterfactual trades (mirror/veto): weight = CF_WEIGHT (default 0.35)

This prevents the brain from learning a pessimistic bias from mirror trades
(which often have negative edge and still win, confusing the direction signal)
while still allowing it to extract generalizable patterns from the larger pool
of counterfactual replay outcomes.
"""
from __future__ import annotations

from tradebot.models import Experience

# Counterfactual sample weight: real trades contribute ~3x more to the gradient
# than counterfactual simulations. This prevents the majority of training rows
# (which may be mirror/veto) from dominating the decision boundary.
_CF_WEIGHT = 0.35


def to_xy(experiences: list[Experience]) -> tuple[list[list[float]], list[int]]:
    """Vanilla X, y conversion (no weighting). Used by diagnostics / validation
    where sample weighting is irrelevant."""
    X: list[list[float]] = []
    y: list[int] = []
    for e in experiences:
        if not e.features:
            continue
        X.append(list(e.features) + [1.0 if e.is_yes else 0.0, float(e.edge)])
        y.append(int(e.won))
    return X, y


def to_weighted_xy(
    experiences: list[Experience],
    cf_weight: float = _CF_WEIGHT,
) -> tuple[list[list[float]], list[int], list[float]]:
    """X, y with per-sample weights for training.

    ``sample_weights[i]`` = 1.0 for real trades, ``cf_weight`` for counterfactuals.
    The weights are passed to ``NeuralBrain.train(sample_weights=...)`` which
    scales the BCE gradient per row, so counterfactual examples exert proportionally
    less influence on the learned weights.

    Returns (X, y, sample_weights)."""
    X: list[list[float]] = []
    y: list[int] = []
    w: list[float] = []
    for e in experiences:
        if not e.features:
            continue
        X.append(list(e.features) + [1.0 if e.is_yes else 0.0, float(e.edge)])
        y.append(int(e.won))
        w.append(cf_weight if e.is_counterfactual else 1.0)
    return X, y, w

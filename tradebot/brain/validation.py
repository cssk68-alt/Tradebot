"""Brain validation (Problem 2): out-of-sample evaluation + feature importance.

These are REPORTING tools — they never replace the production brain (which keeps
training on ALL experiences). ``evaluate_oos`` trains a fresh net on the older
part of the data and measures it on the newer, unseen part (no lookahead), so we
can see whether the brain generalizes. ``permutation_importance`` shuffles each
feature on held-out data and measures the loss degradation, revealing which of the
22 features carry real signal and which are noise.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

_EPS = 1e-12
# Minimums for a trustworthy out-of-sample read.
_MIN_TRAIN = 8   # the net's own training floor
_MIN_TEST = 4


def time_split(X: list[list[float]], y: list[int], test_frac: float = 0.25):
    """Chronological split (rows are in insert/ID order): last ``test_frac`` = test.
    No shuffling — a trading model must be validated forward in time."""
    n = len(y)
    n_test = int(round(n * test_frac))
    n_test = max(0, min(n - 1, n_test)) if n > 1 else 0
    cut = n - n_test
    return (X[:cut], y[:cut]), (X[cut:], y[cut:])


def _clip(p: float) -> float:
    return min(1.0 - _EPS, max(_EPS, p))


def log_loss(ps: list[float], ys: list[int]) -> float:
    if not ys:
        return float("nan")
    return -sum(y * math.log(_clip(p)) + (1 - y) * math.log(1 - _clip(p))
                for p, y in zip(ps, ys)) / len(ys)


def accuracy(ps: list[float], ys: list[int]) -> float:
    if not ys:
        return float("nan")
    return sum(int((p >= 0.5) == bool(y)) for p, y in zip(ps, ys)) / len(ys)


def auc(ps: list[float], ys: list[int]) -> float:
    """Rank-based ROC AUC (Mann–Whitney U) with average ranks for ties.
    Returns 0.5 when one class is absent (undefined)."""
    pos = sum(ys)
    neg = len(ys) - pos
    if pos == 0 or neg == 0:
        return 0.5
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    ranks = [0.0] * len(ps)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and ps[order[j + 1]] == ps[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank over the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    sum_pos = sum(r for r, y in zip(ranks, ys) if y == 1)
    return (sum_pos - pos * (pos + 1) / 2.0) / (pos * neg)


def evaluate_oos(
    make_net: Callable[[int], object],
    input_dim: int,
    X: list[list[float]],
    y: list[int],
    test_frac: float = 0.25,
    l2: float = 0.0,
) -> dict:
    """Train a FRESH net on the older split, evaluate on the newer held-out split."""
    (Xtr, ytr), (Xte, yte) = time_split(X, y, test_frac)
    if len(ytr) < _MIN_TRAIN or len(set(ytr)) < 2 or len(yte) < _MIN_TEST or len(set(yte)) < 2:
        return {"status": "insufficient", "n_train": len(ytr), "n_test": len(yte)}
    net = make_net(input_dim)
    if not net.train(Xtr, ytr, l2=l2):
        return {"status": "insufficient", "n_train": len(ytr), "n_test": len(yte)}
    ps = [net.predict(x) for x in Xte]
    return {
        "status": "ok",
        "n_train": len(ytr),
        "n_test": len(yte),
        "accuracy": round(accuracy(ps, yte), 4),
        "logloss": round(log_loss(ps, yte), 4),
        "auc": round(auc(ps, yte), 4),
    }


def diagnose(
    make_net: Callable[[int], object],
    input_dim: int,
    X: list[list[float]],
    y: list[int],
    names: list[str],
    test_frac: float = 0.25,
    l2: float = 0.0,
    top_k: int = 8,
) -> dict:
    """OOS metrics + feature importance from ONE held-out fit.

    Trains a fresh net on the older split and measures it on the newer split, then
    runs permutation importance on that SAME held-out slice (no train/test leakage),
    so both numbers reflect generalization rather than memorization."""
    (Xtr, ytr), (Xte, yte) = time_split(X, y, test_frac)
    if len(ytr) < _MIN_TRAIN or len(set(ytr)) < 2 or len(yte) < _MIN_TEST or len(set(yte)) < 2:
        return {"oos": {"status": "insufficient", "n_train": len(ytr), "n_test": len(yte)},
                "feature_importance": []}
    net = make_net(input_dim)
    if not net.train(Xtr, ytr, l2=l2):
        return {"oos": {"status": "insufficient", "n_train": len(ytr), "n_test": len(yte)},
                "feature_importance": []}
    ps = [net.predict(x) for x in Xte]
    oos = {
        "status": "ok", "n_train": len(ytr), "n_test": len(yte),
        "accuracy": round(accuracy(ps, yte), 4),
        "logloss": round(log_loss(ps, yte), 4),
        "auc": round(auc(ps, yte), 4),
    }
    imp = permutation_importance(net, Xte, yte, names)[:top_k]
    return {"oos": oos, "feature_importance": imp}


def permutation_importance(
    net, X: list[list[float]], y: list[int], names: list[str], seed: int = 0
) -> list[dict]:
    """Per-feature importance = increase in log-loss when that feature is shuffled.

    Higher = the net relies on it more (real signal); ≈0 = noise. Computed on the
    given (ideally held-out) rows with the already-trained ``net``."""
    if not y or not getattr(net, "trained", False):
        return []
    Xa = np.array(X, dtype=float)
    base = log_loss([net.predict(list(row)) for row in Xa], y)
    rng = np.random.default_rng(seed)
    out: list[dict] = []
    for j in range(Xa.shape[1]):
        col = Xa[:, j].copy()
        Xa[:, j] = rng.permutation(col)
        shuffled = log_loss([net.predict(list(row)) for row in Xa], y)
        Xa[:, j] = col  # restore
        name = names[j] if j < len(names) else f"f{j}"
        out.append({"name": name, "importance": round(shuffled - base, 4)})
    out.sort(key=lambda d: d["importance"], reverse=True)
    return out

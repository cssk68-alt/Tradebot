"""Brain validation: time-split OOS, metrics, permutation importance, L2 (Problem 2)."""
import random

from tradebot.brain.network import NeuralBrain, make_brain
from tradebot.brain.validation import (
    accuracy,
    auc,
    diagnose,
    evaluate_oos,
    log_loss,
    permutation_importance,
    time_split,
)


def test_time_split_is_chronological():
    X = [[i] for i in range(10)]
    y = [i % 2 for i in range(10)]
    (Xtr, ytr), (Xte, yte) = time_split(X, y, 0.2)
    assert len(yte) == 2 and Xtr[0] == [0] and Xte[-1] == [9]


def test_metrics():
    assert auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert auc([0.5, 0.5], [0, 1]) == 0.5            # tie -> chance
    assert accuracy([0.9, 0.1], [1, 0]) == 1.0
    assert log_loss([0.5, 0.5], [1, 0]) > 0


def _separable(n=40, seed=1):
    rng = random.Random(seed)
    X, y = [], []
    for i in range(n):
        label = i % 2  # alternating -> both classes in any chronological split
        X.append([float(label), rng.random()])  # feature0 = signal, feature1 = noise
        y.append(label)
    return X, y


def test_evaluate_oos_learns_signal():
    X, y = _separable()
    res = evaluate_oos(make_brain, 2, X, y, test_frac=0.25, l2=1e-4)
    assert res["status"] == "ok"
    assert res["auc"] >= 0.9  # the net generalizes on unseen rows


def test_evaluate_oos_insufficient():
    res = evaluate_oos(make_brain, 2, [[0.0, 0.0]] * 3, [0, 1, 0], test_frac=0.25)
    assert res["status"] == "insufficient"


def test_permutation_importance_signal_beats_noise():
    X, y = _separable()
    net = make_brain(2)
    net.train(X, y, l2=1e-4)
    imp = permutation_importance(net, X, y, ["signal", "noise"])
    score = {d["name"]: d["importance"] for d in imp}
    assert score["signal"] > score["noise"]
    assert imp[0]["name"] == "signal"  # sorted desc


def test_diagnose_returns_oos_and_importance():
    X, y = _separable()
    d = diagnose(make_brain, 2, X, y, ["signal", "noise"], l2=1e-4)
    assert d["oos"]["status"] == "ok"
    assert d["feature_importance"] and d["feature_importance"][0]["name"] == "signal"


def test_l2_shrinks_weights():
    X, y = _separable(n=24)
    a = NeuralBrain(2)
    a.train(X, y, l2=0.0)
    b = NeuralBrain(2)
    b.train(X, y, l2=0.5)
    mag = lambda net: abs(net.w1).sum() + abs(net.w2).sum()
    assert mag(b) < mag(a)

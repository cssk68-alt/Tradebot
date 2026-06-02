import numpy as np
import pytest

from tradebot.brain.network import NeuralBrain, make_brain


def _data(n=200, seed=0):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for _ in range(n):
        f = rng.normal(0, 1, 10).tolist()
        p = 1.0 / (1.0 + np.exp(-(2.0 * f[6] - 2.0 * f[3])))
        X.append(f)
        y.append(int(rng.random() < p))
    return X, y


def test_make_brain_returns_usable_backend():
    b = make_brain(10)
    for m in ("predict", "train", "save", "load"):
        assert hasattr(b, m)
    assert b.predict([0.0] * 10) == 0.5  # untrained neutral


def test_torch_backend_learns_and_interops_with_numpy(tmp_path):
    pytest.importorskip("torch")
    from tradebot.brain.network import TorchBrain

    X, y = _data()
    tb = TorchBrain(10)
    assert tb.train(X, y)
    good = [0.0] * 10
    good[6], good[3] = 2.0, -2.0
    bad = [0.0] * 10
    bad[6], bad[3] = -2.0, 2.0
    assert tb.predict(good) > tb.predict(bad)

    # Same .npz format: numpy-trained weights load into the torch backend and
    # produce the same prediction (weights carry over between backends).
    nb = NeuralBrain(10)
    nb.train(X, y)
    path = str(tmp_path / "b.npz")
    nb.save(path)
    tb2 = TorchBrain(10)
    assert tb2.load(path)
    f = [0.2] * 10
    assert abs(nb.predict(f) - tb2.predict(f)) < 1e-5

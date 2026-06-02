import numpy as np

from tradebot.brain.network import NeuralBrain


def _data(n=300, seed=0):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for _ in range(n):
        f = rng.normal(0, 1, 10).tolist()
        # Learnable rule: high sentiment (idx 6) + low spread (idx 3) => more wins.
        p = 1.0 / (1.0 + np.exp(-(2.0 * f[6] - 2.0 * f[3])))
        X.append(f)
        y.append(int(rng.random() < p))
    return X, y


def test_untrained_brain_is_neutral():
    assert NeuralBrain(10).predict([0.0] * 10) == 0.5


def test_brain_learns_pattern():
    X, y = _data()
    net = NeuralBrain(10)
    assert net.train(X, y)
    good = [0.0] * 10
    good[6], good[3] = 2.0, -2.0  # high sentiment, low spread
    bad = [0.0] * 10
    bad[6], bad[3] = -2.0, 2.0
    assert net.predict(good) > net.predict(bad)


def test_brain_save_load_carryover(tmp_path):
    """Weights persist and load identically — paper learning carries into live."""
    X, y = _data()
    net = NeuralBrain(10)
    net.train(X, y)
    path = str(tmp_path / "brain.npz")
    net.save(path)

    reloaded = NeuralBrain(10)
    assert reloaded.load(path)
    f = [0.3] * 10
    assert abs(net.predict(f) - reloaded.predict(f)) < 1e-9

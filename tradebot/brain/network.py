"""A dependency-free neural network — the bot's 'brain'.

One hidden-layer MLP (ReLU + sigmoid) trained with mini-batch gradient descent
and binary cross-entropy. It predicts P(trade wins) from the pre-trade feature
vector. Weights persist to an .npz file, so what it learns carries across runs
and across paper/live modes. (A PyTorch implementation can be swapped in behind
the same interface; numpy keeps it lightweight and always runnable.)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


class NeuralBrain:
    def __init__(self, input_dim: int, hidden: int = 16, seed: int = 7):
        rng = np.random.default_rng(seed)
        self.input_dim = input_dim
        self.w1 = rng.normal(0, 0.5, (input_dim, hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = rng.normal(0, 0.5, (hidden, 1))
        self.b2 = np.zeros(1)
        self.mu = np.zeros(input_dim)
        self.sd = np.ones(input_dim)
        self.trained = False

    def _norm(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) / self.sd

    def _forward(self, Xn: np.ndarray):
        z1 = Xn @ self.w1 + self.b1
        a1 = np.maximum(0.0, z1)  # ReLU
        z2 = a1 @ self.w2 + self.b2
        out = 1.0 / (1.0 + np.exp(-z2))  # sigmoid
        return z1, a1, out

    def predict(self, features: list[float]) -> float:
        if not self.trained:
            return 0.5
        X = np.array([features], dtype=float)
        _, _, out = self._forward(self._norm(X))
        return float(min(1.0, max(0.0, out[0, 0])))

    def train(self, X: list[list[float]], y: list[int], epochs: int = 400, lr: float = 0.05) -> bool:
        if len(y) < 8 or len(set(y)) < 2:
            return False
        Xa = np.array(X, dtype=float)
        if Xa.ndim != 2 or Xa.shape[1] != self.input_dim:
            return False  # feature-schema drift -> refuse rather than matmul-crash
        ya = np.array(y, dtype=float).reshape(-1, 1)
        self.mu = Xa.mean(axis=0)
        self.sd = Xa.std(axis=0) + 1e-6
        Xn = self._norm(Xa)
        n = len(ya)
        for _ in range(epochs):
            z1, a1, out = self._forward(Xn)
            d2 = (out - ya) / n  # gradient of BCE wrt z2 (sigmoid)
            dw2 = a1.T @ d2
            db2 = d2.sum(axis=0)
            da1 = d2 @ self.w2.T
            dz1 = da1 * (z1 > 0)
            dw1 = Xn.T @ dz1
            db1 = dz1.sum(axis=0)
            self.w2 -= lr * dw2
            self.b2 -= lr * db2
            self.w1 -= lr * dw1
            self.b1 -= lr * db1
        self.trained = True
        return True

    def save(self, path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2,
            mu=self.mu, sd=self.sd, trained=np.array([self.trained]),
        )

    def load(self, path) -> bool:
        try:
            d = np.load(path)
        except Exception:
            return False
        try:
            if d["w1"].shape[0] != self.input_dim:
                return False
            self.w1, self.b1 = d["w1"], d["b1"]
            self.w2, self.b2 = d["w2"], d["b2"]
            self.mu, self.sd = d["mu"], d["sd"]
            self.trained = bool(d["trained"][0])
            return True
        except Exception:
            return False


class TorchBrain:
    """PyTorch MLP backend with the SAME interface and SAME .npz weight format as
    NeuralBrain, so learned weights are interoperable between the two backends."""

    def __init__(self, input_dim: int, hidden: int = 16, seed: int = 7):
        import torch
        import torch.nn as nn

        self.torch = torch
        torch.manual_seed(seed)
        self.input_dim = input_dim
        self.hidden = hidden
        self.net = nn.Sequential(nn.Linear(input_dim, hidden), nn.ReLU(), nn.Linear(hidden, 1))
        self.mu = np.zeros(input_dim)
        self.sd = np.ones(input_dim)
        self.trained = False

    def _norm(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) / self.sd

    def predict(self, features: list[float]) -> float:
        if not self.trained:
            return 0.5
        t = self.torch
        x = t.tensor(self._norm(np.array([features], dtype=float)), dtype=t.float32)
        with t.no_grad():
            p = t.sigmoid(self.net(x)).item()
        return float(min(1.0, max(0.0, p)))

    def train(self, X: list[list[float]], y: list[int], epochs: int = 400, lr: float = 0.05) -> bool:
        if len(y) < 8 or len(set(y)) < 2:
            return False
        t = self.torch
        Xa = np.array(X, dtype=float)
        if Xa.ndim != 2 or Xa.shape[1] != self.input_dim:
            return False  # feature-schema drift -> refuse rather than crash
        self.mu = Xa.mean(axis=0)
        self.sd = Xa.std(axis=0) + 1e-6
        xt = t.tensor(self._norm(Xa), dtype=t.float32)
        yt = t.tensor(np.array(y, dtype=float).reshape(-1, 1), dtype=t.float32)
        opt = t.optim.Adam(self.net.parameters(), lr=lr)
        loss_fn = t.nn.BCEWithLogitsLoss()
        for _ in range(epochs):
            opt.zero_grad()
            loss_fn(self.net(xt), yt).backward()
            opt.step()
        self.trained = True
        return True

    def save(self, path) -> None:
        sd = self.net.state_dict()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path,
            w1=sd["0.weight"].cpu().numpy().T, b1=sd["0.bias"].cpu().numpy(),
            w2=sd["2.weight"].cpu().numpy().T, b2=sd["2.bias"].cpu().numpy(),
            mu=self.mu, sd=self.sd, trained=np.array([self.trained]),
        )

    def load(self, path) -> bool:
        try:
            d = np.load(path)
        except Exception:
            return False
        try:
            if d["w1"].shape[0] != self.input_dim:
                return False
            t = self.torch
            self.net.load_state_dict(
                {
                    "0.weight": t.tensor(d["w1"].T, dtype=t.float32),
                    "0.bias": t.tensor(d["b1"], dtype=t.float32),
                    "2.weight": t.tensor(d["w2"].T, dtype=t.float32),
                    "2.bias": t.tensor(d["b2"], dtype=t.float32),
                }
            )
            self.mu, self.sd = d["mu"], d["sd"]
            self.trained = bool(d["trained"][0])
            return True
        except Exception:
            return False


def make_brain(input_dim: int, hidden: int = 16, seed: int = 7):
    """Return a PyTorch-backed brain if torch is installed, else the numpy one.
    Both use the same .npz format, so weights carry over between backends."""
    try:
        import torch  # noqa: F401

        return TorchBrain(input_dim, hidden, seed)
    except Exception:
        return NeuralBrain(input_dim, hidden, seed)

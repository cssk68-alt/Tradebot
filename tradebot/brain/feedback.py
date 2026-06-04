"""Stage 5 'brain': loads/saves the neural net, trains from experience (wins AND
losses), and produces the brain-score that gates stages 3 and 4. The weights live
in `data/` and load in both paper and live mode, so learning carries over."""
from __future__ import annotations

from tradebot.brain.experience import to_xy
from tradebot.brain.network import make_brain
from tradebot.ml.features import BRAIN_FEATURE_DIM, BRAIN_FEATURE_NAMES
from tradebot.models import Experience


class Brain:
    def __init__(self, path, log, input_dim: int = BRAIN_FEATURE_DIM, l2: float = 0.0):
        self.path = str(path)
        self.log = log
        self.l2 = float(l2)  # L2 weight-decay used for training + OOS validation
        self.net = make_brain(input_dim)
        if self.net.load(self.path):
            self.log.info("Brain: loaded existing weights from %s", self.path)

    def _compatible_xy(self, experiences: list[Experience]):
        """Drop rows from an older (narrower) feature schema so growing the feature
        set never crashes training; returns (X, y) the current net can consume."""
        X, y = to_xy(experiences)
        dim = self.net.input_dim
        compat = [(xi, yi) for xi, yi in zip(X, y) if len(xi) == dim]
        if len(compat) < len(X):
            self.log.info(
                "Brain: skipped %d experience(s) from an older feature schema "
                "(net expects %d-dim); %d compatible remain.",
                len(X) - len(compat), dim, len(compat),
            )
        return [xi for xi, _ in compat], [yi for _, yi in compat]

    def train_from_experiences(self, experiences: list[Experience]) -> bool:
        X, y = self._compatible_xy(experiences)
        if self.net.train(X, y, l2=self.l2):
            self.net.save(self.path)
            wins = sum(y)
            self.log.info(
                "Brain: trained on %d experiences (%d wins / %d losses)",
                len(y), wins, len(y) - wins,
            )
            return True
        return False

    def diagnostics(self, experiences: list[Experience]) -> dict:
        """Out-of-sample metrics + feature importance (REPORTING only; the
        production net above still trains on all data)."""
        from tradebot.brain.validation import diagnose

        X, y = self._compatible_xy(experiences)
        return diagnose(make_brain, self.net.input_dim, X, y, BRAIN_FEATURE_NAMES, l2=self.l2)

    def score(self, features: list[float]) -> float:
        """P(this setup wins) in [0, 1]; 0.5 when untrained (cold start)."""
        return self.net.predict(features)

    @property
    def trained(self) -> bool:
        return self.net.trained

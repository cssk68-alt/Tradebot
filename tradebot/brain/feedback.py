"""Stage 5 'brain': loads/saves the neural net, trains from experience (wins AND
losses), and produces the brain-score that gates stages 3 and 4. The weights live
in `data/` and load in both paper and live mode, so learning carries over."""
from __future__ import annotations

from tradebot.brain.experience import to_xy, to_weighted_xy
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

    def _compatible_weighted_xy(self, experiences: list[Experience]):
        """Like _compatible_xy but returns sample weights from to_weighted_xy.
        Counterfactual rows get a reduced weight (default 0.35) so the net learns
        from real trades ~3x more than from mirror/veto simulations."""
        X, y, w = to_weighted_xy(experiences)
        dim = self.net.input_dim
        compat = [(xi, yi, wi) for xi, yi, wi in zip(X, y, w) if len(xi) == dim]
        if len(compat) < len(X):
            self.log.info(
                "Brain: skipped %d experience(s) from an older feature schema "
                "(net expects %d-dim); %d compatible remain.",
                len(X) - len(compat), dim, len(compat),
            )
        return [xi for xi, _, _ in compat], [yi for _, yi, _ in compat], [wi for _, _, wi in compat]

    def train_from_experiences(self, experiences: list[Experience]) -> bool:
        # Use weighted training: counterfactual samples contribute less
        # so real trades dominate the learning signal.
        X, y, w = self._compatible_weighted_xy(experiences)
        if self.net.train(X, y, l2=self.l2, sample_weights=w):
            self.net.save(self.path)
            wins = sum(y)
            cf_count = sum(1 for e in experiences if e.is_counterfactual)
            self.log.info(
                "Brain: trained on %d experiences (%d real / %d cf, %d wins / %d losses)",
                len(y), len(experiences) - cf_count, cf_count, wins, len(y) - wins,
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

    def score_diagnostics(self, features: list[float]) -> dict:
        """Full diagnostics for one inference call: includes raw pre-sigmoid
        value and score, for logging in predict.py."""
        raw = self.net.predict_raw(features)
        return {
            "brain_score": raw["score"],
            "z2_raw": raw["z2_raw"],
            "active_neurons": raw["active_neurons"],
        }

    def check_score_collapse(self, threshold: float = 0.001) -> bool:
        """Warn if recent brain scores have near-zero variance (collapse to
        constant output). Returns True if the variance is suspiciously low."""
        var = self.net.score_variance(window=20)
        if var < threshold and self.net.trained:
            self.log.warning(
                "Brain: score variance over last 20 predictions = %.6f "
                "(below %.4f threshold) — scores may be collapsing to constant",
                var, threshold,
            )
            return True
        return False

    @property
    def trained(self) -> bool:
        return self.net.trained

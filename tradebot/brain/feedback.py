"""Stage 5 'brain': loads/saves the neural net, trains from experience (wins AND
losses), and produces the brain-score that gates stages 3 and 4. The weights live
in `data/` and load in both paper and live mode, so learning carries over."""
from __future__ import annotations

from tradebot.brain.experience import to_xy
from tradebot.brain.network import make_brain
from tradebot.ml.features import FEATURE_DIM
from tradebot.models import Experience


class Brain:
    def __init__(self, path, log, input_dim: int = FEATURE_DIM):
        self.path = str(path)
        self.log = log
        self.net = make_brain(input_dim)
        if self.net.load(self.path):
            self.log.info("Brain: loaded existing weights from %s", self.path)

    def train_from_experiences(self, experiences: list[Experience]) -> bool:
        X, y = to_xy(experiences)
        if self.net.train(X, y):
            self.net.save(self.path)
            wins = sum(y)
            self.log.info(
                "Brain: trained on %d experiences (%d wins / %d losses)",
                len(y), wins, len(y) - wins,
            )
            return True
        return False

    def score(self, features: list[float]) -> float:
        """P(this setup wins) in [0, 1]; 0.5 when untrained (cold start)."""
        return self.net.predict(features)

    @property
    def trained(self) -> bool:
        return self.net.trained

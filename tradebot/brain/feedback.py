"""Stage 5 'brain': loads/saves the neural net, trains from experience (wins AND
losses), and produces the brain-score that gates stages 3 and 4. The weights live
in `data/` and load in both paper and live mode, so learning carries over.

PROBABILISTIC EMERGENT RULE LEARNING
-------------------------------------
The Brain now integrates a PatternEngine that independently learns from
experience via a 4-stage emergence pipeline:
  1. OBSERVATION    (1-5)      - soft signal only
  2. WEAK PATTERN   (6-20)     - minor risk_penalty_score adjustment
  3. STRONG PATTERN (21-80)    - significant confidence_modifier + soft steering
  4. HARD CONSTRAINT (80-100+) - only NOW may a hard rule be created

The neural net (NeuralBrain / TorchBrain) learns P(trade wins) from feature
vectors. The PatternEngine learns context-level biases (side bias, edge range,
price range) from outcome patterns across multiple trades. They are complementary:
the net answers "how likely is THIS specific setup to win?" while the pattern
engine answers "what does history say about this TYPE of trade?"
"""
from __future__ import annotations

from tradebot.brain.experience import to_xy, to_weighted_xy
from tradebot.brain.network import make_brain
from tradebot.brain.patterns import PatternEngine
from tradebot.ml.features import BRAIN_FEATURE_DIM, BRAIN_FEATURE_NAMES
from tradebot.models import Experience
from tradebot.store.db import Store


class Brain:
    def __init__(self, path, log, input_dim: int = BRAIN_FEATURE_DIM, l2: float = 0.0):
        self.path = str(path)
        self.log = log
        self.l2 = float(l2)
        self.net = make_brain(input_dim)
        if self.net.load(self.path):
            self.log.info("Brain: loaded existing weights from %s", self.path)

        # Probabilistic Pattern Engine - emerges rules from trade outcomes
        # across 4 stages, never from single events.
        self.patterns = PatternEngine(log)

    @property
    def trained(self) -> bool:
        return self.net.trained

    # ---- Neural net methods ----

    def _compatible_xy(self, experiences: list[Experience]):
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
        from tradebot.brain.validation import diagnose

        X, y = self._compatible_xy(experiences)
        diag = diagnose(make_brain, self.net.input_dim, X, y, BRAIN_FEATURE_NAMES, l2=self.l2)
        diag["pattern_engine"] = self.patterns.stats()
        diag["patterns"] = self.patterns.list_patterns()
        return diag

    def score(self, features: list[float]) -> float:
        return self.net.predict(features)

    def score_diagnostics(self, features: list[float]) -> dict:
        raw = self.net.predict_raw(features)
        return {
            "brain_score": raw["score"],
            "z2_raw": raw["z2_raw"],
            "active_neurons": raw["active_neurons"],
        }

    def check_score_collapse(self, threshold: float = 0.001) -> bool:
        var = self.net.score_variance(window=20)
        if var < threshold and self.net.trained:
            self.log.warning(
                "Brain: score variance over last 20 predictions = %.6f "
                "(below %.4f threshold) - scores may be collapsing to constant",
                var, threshold,
            )
            return True
        return False

    # ---- Pattern Engine integration ----

    def record_outcome(self, resolved_trade) -> None:
        """Feed a resolved trade outcome into the PatternEngine."""
        self.patterns.record_outcome(
            is_yes=resolved_trade.is_yes,
            edge=resolved_trade.edge,
            confidence=resolved_trade.brain_score,
            brain_score=resolved_trade.brain_score,
            entry_price=resolved_trade.entry_price,
            spread=0.0,
            sentiment_agreement=0.5,
            won=bool(resolved_trade.won),
            pnl=resolved_trade.pnl,
        )

    def evaluate_patterns(self, signal) -> dict:
        """Evaluate a trade candidate against all emerged patterns."""
        return self.patterns.evaluate(
            is_yes=signal.is_yes,
            edge=signal.edge,
            confidence=signal.confidence,
            brain_score=signal.brain_score,
            entry_price=signal.market_price,
        )

    def pattern_stats(self) -> dict:
        return self.patterns.stats()

    def list_patterns(self) -> list[dict]:
        return self.patterns.list_patterns()

        # ---- Meta-Learning LLM insight (observer-only) ----

    def set_meta_insight(self, insight: Optional[dict]) -> None:
        """Store the latest For-The-Future Learner insight.

        This is strictly observer data — never used in decision-making.
        Stored here so the dashboard can access it via the brain reference.
        The orchestrator sets it after calling meta_learner.run().
        """
        self._meta_insight = insight

    def meta_insight(self) -> Optional[dict]:
        return getattr(self, '_meta_insight', None)

    # ---- Serialisation ----

    def save_patterns(self, store: Store) -> None:
        store.save_pattern_state(self.patterns.to_saveable())

    def load_patterns(self, store: Store) -> None:
        data = store.load_pattern_state()
        if data:
            self.patterns = PatternEngine.from_saveable(data, self.log)
            ps = self.patterns.stats()
            if ps["total_emerged_patterns"] > 0:
                self.log.info(
                    "PatternEngine: loaded %d patterns (%d observations, %d hard rules)",
                    ps["total_emerged_patterns"], ps["total_observations"], ps["hard_rules"],
                )

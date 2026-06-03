"""Stage 3: combine XGBoost + Claude + the brain into a calibrated edge -> Signal."""
from __future__ import annotations

from tradebot.agents.base import Agent
from tradebot.ml.features import build_features
from tradebot.models import Candidate, ResearchReport, Side, Signal
from tradebot.store.lessons import format_lessons


class PredictAgent(Agent):
    name = "predict"

    def __init__(self, settings, store, log, predictor, brain, claude=None):
        super().__init__(settings, store, log)
        self.predictor = predictor
        self.brain = brain
        self.claude = claude

    def run(
        self, candidates: list[Candidate], reports: dict[str, ResearchReport]
    ) -> list[Signal]:
        s = self.settings
        lessons = format_lessons(self.store.recent_lessons())
        signals: list[Signal] = []

        for c in candidates:
            m = c.market
            report = reports.get(m.id)
            feats = build_features(m, report, c.price_move)
            model_prob = self.predictor.predict_yes(feats)

            true_prob = model_prob
            llm_bump = 0.0
            reason = "model"
            if self.claude is not None and self.claude.available and report is not None:
                est = self.claude.estimate_prob(m.question, report.narrative, m.yes_price, lessons)
                if est is not None:
                    llm_prob, llm_conf, reason = est
                    true_prob = 0.5 * model_prob + 0.5 * llm_prob
                    llm_bump = 0.15 * (llm_conf - 0.5)

            brain_score = self.brain.score(feats)

            if true_prob >= m.yes_price + s.edge_threshold:
                is_yes, price, token = True, m.yes_price, m.yes_token_id
                edge = true_prob - price
            elif true_prob <= m.yes_price - s.edge_threshold:
                is_yes, price, token = False, 1.0 - m.yes_price, m.no_token_id
                edge = (1.0 - true_prob) - price
            else:
                continue  # no actionable edge

            # Scalping: the spread is paid round-trip, so only enter where the
            # take-profit target still clears the spread by min_net_profit.
            if s.strategy == "scalp" and m.spread > s.take_profit - s.min_net_profit:
                self.log.info(
                    "Predict: skip '%s' — spread %.3f eats target %.3f",
                    m.question[:40], m.spread, s.take_profit,
                )
                continue

            confidence = min(
                1.0,
                max(0.0, 0.5 + 2.0 * min(abs(edge), 0.25) + 0.3 * (brain_score - 0.5) + llm_bump),
            )
            signals.append(
                Signal(
                    market_id=m.id,
                    token_id=token or f"{m.id}-{'YES' if is_yes else 'NO'}",
                    question=m.question, side=Side.BUY, market_price=price,
                    true_prob=true_prob, edge=edge, confidence=confidence, is_yes=is_yes,
                    features=feats, brain_score=brain_score, rationale=reason,
                )
            )

        self.log.info("Predict: %d signals from %d candidates", len(signals), len(candidates))
        return signals

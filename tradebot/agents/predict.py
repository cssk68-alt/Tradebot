"""Stage 3: combine XGBoost + LLM agent + the brain into a calibrated edge -> Signal."""
from __future__ import annotations

from tradebot.agents.base import Agent
from tradebot.exchange.ticks import targets_collapse
from tradebot.ml.features import build_brain_features, build_features
from tradebot.models import Candidate, ResearchReport, Side, Signal
from tradebot.risk.adjuster import risk_profile
from tradebot.store.lessons import format_lessons


class PredictAgent(Agent):
    name = "predict"

    def __init__(self, settings, store, log, predictor, brain, client=None):
        super().__init__(settings, store, log)
        self.predictor = predictor
        self.brain = brain
        self.client = client

    def run(
        self, candidates: list[Candidate], reports: dict[str, ResearchReport]
    ) -> list[Signal]:
        s = self.settings
        # Effective edge bar from the Risk-Adjuster (base bar when aggressiveness=0).
        edge_threshold = risk_profile(s).edge_threshold
        lessons = format_lessons(self.store.recent_lessons())
        signals: list[Signal] = []

        for c in candidates:
            m = c.market
            report = reports.get(m.id)

            # HARD-FAIL gate (PAPER and LIVE alike): never act without real
            # external research. No sources -> no trade (no synthetic/offline-prior
            # edges). Paper is held to the exact same bar as live, so a paper track
            # record is meaningful and transfers 1:1 to real money.
            if report is None or (report.n_sources == 0 and report.fact_prob is None):
                self.log.info(
                    "Predict: skip '%s' — no external research sources", m.question[:40]
                )
                continue

            feats = build_features(m, report, c.price_move)
            model_prob = self.predictor.predict_yes(feats)

            true_prob = model_prob
            llm_bump = 0.0
            reason = "model"
            if self.client is not None and self.client.available and report is not None:
                est = self.client.estimate_prob(m.question, report.narrative, m.yes_price, lessons)
                if est is not None:
                    llm_prob, llm_conf, reason = est
                    true_prob = 0.5 * model_prob + 0.5 * llm_prob
                    llm_bump = 0.15 * (llm_conf - 0.5)

            # Fold in a hard quantitative prior (live crypto price / bookmaker
            # odds) weighted by its confidence — a calibrated number anchoring the
            # fuzzy text estimate. It is already in the narrative (the forecaster
            # saw it); this also nudges the math side directly.
            if report.fact_prob is not None:
                w = 0.5 * max(0.0, min(1.0, report.fact_confidence))
                true_prob = (1.0 - w) * true_prob + w * report.fact_prob
                if reason == "model":
                    reason = f"fact:{report.fact_source}"

            if true_prob >= m.yes_price + edge_threshold:
                is_yes, price, token = True, m.yes_price, m.yes_token_id
                edge = true_prob - price
            elif true_prob <= m.yes_price - edge_threshold:
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
            # Tick-size guard (Teil B.5): block scalps whose take-profit or
            # stop-loss exit collapses onto the entry on the price grid — such a
            # trade can never realize its target and only ever pays the spread.
            if s.strategy == "scalp" and targets_collapse(price, s.take_profit, s.stop_loss):
                self.log.info(
                    "Predict: skip '%s' — TP/SL collapse on tick grid at price %.3f",
                    m.question[:40], price,
                )
                continue

            # Score the actual trade (direction + edge), not a generic setup, and
            # apply the CONFIGURED brain_weight (was hard-coded before).
            brain_feats = build_brain_features(feats, is_yes, edge)
            brain_score = self.brain.score(brain_feats)
            brain_adj = s.brain_weight * (brain_score - 0.5)

            confidence = min(
                1.0,
                max(0.0, 0.5 + 2.0 * min(abs(edge), 0.25) + brain_adj + llm_bump),
            )
            signals.append(
                Signal(
                    market_id=m.id,
                    token_id=token or f"{m.id}-{'YES' if is_yes else 'NO'}",
                    question=m.question, side=Side.BUY, market_price=price,
                    true_prob=true_prob, model_prob=model_prob, edge=edge,
                    confidence=confidence, is_yes=is_yes, features=feats,
                    brain_score=brain_score, rationale=reason,
                )
            )

        self.log.info("Predict: %d signals from %d candidates", len(signals), len(candidates))
        return signals

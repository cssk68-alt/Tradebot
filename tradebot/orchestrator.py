"""Wires the five stages together (one-shot or loop) and runs the learning loop."""
from __future__ import annotations

import time
from typing import Callable, Optional

from tradebot.agents.postmortem import PostmortemAgent
from tradebot.agents.predict import PredictAgent
from tradebot.agents.research import ResearchAgent
from tradebot.agents.risk import RiskAgent
from tradebot.agents.scan import ScanAgent
from tradebot.brain.feedback import Brain
from tradebot.data.gamma import GammaClient
from tradebot.exchange.paper import PaperExchange
from tradebot.exchange.polymarket import PolymarketExchange
from tradebot.llm.claude import Claude
from tradebot.ml.bootstrap import predictor_training_data
from tradebot.ml.model import Predictor
from tradebot.models import Experience, Mode
from tradebot.store.db import Store


class Orchestrator:
    def __init__(self, settings, log, dry_run: bool = False, confirm: Optional[Callable] = None):
        self.settings = settings
        self.log = log
        self.store = Store(settings.db_path)
        self.gamma = GammaClient(log)
        self.claude = Claude(settings.anthropic_api_key)
        self.brain = Brain(settings.brain_path, log)
        self.predictor = Predictor(log)
        self.mode = Mode.LIVE if settings.mode == "live" else Mode.PAPER

        if self.mode == Mode.LIVE:
            self.exchange = PolymarketExchange(self.gamma, log, settings, dry_run=dry_run)
            self.confirm = confirm or default_confirm
        else:
            self.exchange = PaperExchange(self.gamma, log, settings)
            self.confirm = None

        self.scan = ScanAgent(settings, self.store, log)
        self.research = ResearchAgent(settings, self.store, log, self.claude)
        self.predict = PredictAgent(
            settings, self.store, log, self.predictor, self.brain, self.claude
        )
        self.risk = RiskAgent(settings, self.store, log, self.exchange, self.confirm)
        self.postmortem = PostmortemAgent(settings, self.store, log, self.claude)

        self._train_models()

    # --- learning ---
    def _train_models(self) -> None:
        self.brain.train_from_experiences(self.store.load_experiences())
        X, y = predictor_training_data(self.store.resolved_trades())
        self.predictor.train(X, y)

    def bankroll(self) -> float:
        return self.settings.bankroll + self.store.realized_pnl(self.mode)

    def settle_open(self):
        resolved = []
        for t in self.store.open_trades(self.mode):
            r = self.exchange.settle(t)
            if r is None:
                continue
            self.store.update_trade(r)
            self.store.save_experience(
                Experience(
                    features=r.features, edge=r.edge, size=r.size, brain_score=r.brain_score,
                    won=bool(r.won), pnl=r.pnl, mode=r.mode,
                )
            )
            resolved.append(r)
        if resolved:
            wins = sum(1 for r in resolved if r.won)
            self.log.info(
                "Settled %d trades (%d wins) pnl %.2f", len(resolved), wins,
                sum(r.pnl for r in resolved),
            )
            self.postmortem.run(resolved)
            self._train_models()  # brain + predictor learn; carries over to live mode
        return resolved

    # --- main cycle ---
    def run_once(self):
        self.log.info(
            "=== Cycle start (mode=%s, bankroll=%.2f, brain_trained=%s) ===",
            self.mode.value, self.bankroll(), self.brain.trained,
        )
        self.settle_open()
        markets = self.exchange.list_markets()
        candidates = self.scan.run(markets)
        reports = self.research.run(candidates)
        signals = self.predict.run(candidates, reports)
        liq = {c.market.id: c.market.liquidity for c in candidates}
        placed = self.risk.run(signals, self.bankroll(), liq)
        self.log.info(
            "=== Cycle done: %d candidates, %d signals, %d trades placed ===",
            len(candidates), len(signals), len(placed),
        )
        return placed

    def run_loop(self, iterations: int = 5, interval: float = 0.0):
        for i in range(iterations):
            self.log.info("---- iteration %d/%d ----", i + 1, iterations)
            self.run_once()
            if interval and i < iterations - 1:
                time.sleep(interval)


def default_confirm(order) -> bool:
    prompt = (
        f"\n*** LIVE ORDER — REAL MONEY ***\n"
        f"  {order.question}\n"
        f"  side: {'YES' if order.is_yes else 'NO'}  price: {order.price:.2f}  "
        f"size: {order.size:.1f}  cost: ${order.cost:.2f}\n"
        f"Proceed? [y/N]: "
    )
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False

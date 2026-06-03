"""Wires the five stages together (one-shot or loop) and runs the learning loop."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from tradebot.agents.brain_manager import BrainManager
from tradebot.agents.postmortem import PostmortemAgent
from tradebot.agents.predict import PredictAgent
from tradebot.agents.research import ResearchAgent
from tradebot.agents.risk import RiskAgent
from tradebot.agents.scan import ScanAgent
from tradebot.brain.feedback import Brain
from tradebot.data.gamma import DataUnavailableError, GammaClient
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
        self.manager = BrainManager(settings, self.store, log, self.claude)
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

    def _record_resolved(self, r) -> None:
        self.store.update_trade(r)
        if r.won is None:
            # Void / canceled market — a non-outcome; persist it but don't train
            # the brain on a trade that neither won nor lost.
            return
        self.store.save_experience(
            Experience(
                features=r.features, edge=r.edge, size=r.size, brain_score=r.brain_score,
                won=bool(r.won), pnl=r.pnl, mode=r.mode, is_yes=r.is_yes,
            )
        )

    def _after_resolved(self, resolved, verb: str) -> None:
        if not resolved:
            return
        wins = sum(1 for r in resolved if r.won)
        self.log.info(
            "%s %d trades (%d net-positive) pnl %.2f", verb, len(resolved), wins,
            sum(r.pnl for r in resolved),
        )
        self.postmortem.run(resolved)
        self._train_models()  # brain + predictor learn; carries over to live mode

    def settle_open(self):
        """Hold-to-event settlement (the `settle` poller) — REAL resolution, no dice."""
        resolved = []
        for t in self.store.open_trades(self.mode):
            r = self.exchange.settle(t)
            if r is not None:
                self._record_resolved(r)
                resolved.append(r)
        self._after_resolved(resolved, "Settled")
        return resolved

    def _scalp_trigger(self, t, market) -> Optional[str]:
        opened = t.opened_at if t.opened_at.tzinfo else t.opened_at.replace(tzinfo=timezone.utc)
        held = (datetime.now(timezone.utc) - opened).total_seconds()
        cur = market.yes_price if t.is_yes else 1.0 - market.yes_price
        move = cur - t.entry_price
        if move >= self.settings.take_profit:
            return "take_profit"
        if move <= -self.settings.stop_loss:
            return "stop_loss"
        if held >= self.settings.max_hold_seconds:
            return "time"
        return None

    def manage_open(self, markets=None):
        """Close/settle open trades against FRESH prices.
        scalp  -> exit on price (take-profit / stop-loss / max hold);
        resolve -> settle from the real resolution."""
        by_id = {m.id: m for m in (markets or [])}
        resolved = []
        for t in self.store.open_trades(self.mode):
            if self.settings.strategy == "scalp":
                m = by_id.get(t.market_id)
                if m is None:
                    continue  # no current price -> leave open
                reason = self._scalp_trigger(t, m)
                if reason is None:
                    continue
                r = self.exchange.close(t, m, reason=reason)
            else:
                r = self.exchange.settle(t)
            if r is not None:
                self._record_resolved(r)
                resolved.append(r)
        self._after_resolved(resolved, "Closed")
        return resolved

    # --- main cycle ---
    def run_once(self):
        self.log.info(
            "=== Cycle start (mode=%s, strategy=%s, bankroll=%.2f, brain_trained=%s) ===",
            self.mode.value, self.settings.strategy, self.bankroll(), self.brain.trained,
        )
        try:
            markets = self.exchange.list_markets()
        except DataUnavailableError as e:
            # HARD-FAIL: no real market data -> abort the cycle; a trading cycle
            # must never run on synthetic/fallback data.
            self.log.error("HARD-FAIL: aborting cycle — %s", e)
            raise
        self.manage_open(markets)
        candidates = self.scan.run(markets)
        reports = self.research.run(candidates)
        signals = self.predict.run(candidates, reports)
        # Stage 5 meta-controller: Claude Haiku approves/vetoes each signal before
        # it can reach execution, and records its reasoning to the DB.
        approved = self.manager.run(signals, reports)
        liq = {c.market.id: c.market.liquidity for c in candidates}
        placed = self.risk.run(approved, self.bankroll(), liq)
        self.log.info(
            "=== Cycle done: %d candidates, %d signals, %d approved, %d trades placed ===",
            len(candidates), len(signals), len(approved), len(placed),
        )
        self._export_dashboard()
        return placed

    def _export_dashboard(self) -> None:
        try:
            from tradebot.dashboard import export_state

            export_state(self.store, self.settings, self.brain, self.settings.dashboard_path)
        except Exception as e:  # never let the dashboard break a run
            self.log.warning("dashboard export failed: %s", e)

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

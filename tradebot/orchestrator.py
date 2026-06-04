"""Wires the five stages together (one-shot or loop) and runs the learning loop."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from tradebot.agents.brain_manager import BrainManager
from tradebot.agents.postmortem import PostmortemAgent
from tradebot.agents.predict import PredictAgent
from tradebot.agents.research import ResearchAgent
from tradebot.agents.risk import RiskAgent
from tradebot.agents.scan import ScanAgent
from tradebot.brain.counterfactual import settle_scalp_path
from tradebot.brain.feedback import Brain
from tradebot.brain.hold_analysis import recommend_max_hold, scalp_hold_seconds
from tradebot.data.gamma import DataUnavailableError, GammaClient
from tradebot.exchange.paper import PaperExchange
from tradebot.exchange.polymarket import PolymarketExchange
from tradebot.llm import LLMUnavailableError, make_client
from tradebot.ml.bootstrap import predictor_training_data
from tradebot.ml.model import Predictor
from tradebot.models import Counterfactual, Experience, Mode
from tradebot.risk.circuit_breaker import circuit_breaker_reason
from tradebot.store.db import Store


class Orchestrator:
    def __init__(self, settings, log, dry_run: bool = False, confirm: Optional[Callable] = None):
        self.settings = settings
        self.log = log

        # HARD-FAIL (coupled Brain+Agent design): no LLM agent -> no input signals
        # for the brain and no calibration feedback -> a run is pointless. Abort
        # BEFORE building anything, with an actionable message. Agent da -> alles
        # funktioniert; Agent nicht da -> nix funktioniert.
        self.client = make_client(settings)
        if not self.client.available:
            raise LLMUnavailableError(
                f"No LLM agent available for LLM_PROVIDER={settings.llm_provider!r}. "
                f"Set the matching API key in .env "
                f"({'ANTHROPIC_API_KEY' if settings.llm_provider == 'anthropic' else 'DEEPSEEK_API_KEY'}). "
                "The bot is a coupled Brain+Agent system and will not run without an agent."
            )

        self.store = Store(settings.db_path)
        self.gamma = GammaClient(log)
        self.brain = Brain(settings.brain_path, log, l2=float(getattr(settings, "brain_l2", 0.0)))
        self.predictor = Predictor(log)
        self.mode = Mode.LIVE if settings.mode == "live" else Mode.PAPER

        if self.mode == Mode.LIVE:
            self.exchange = PolymarketExchange(self.gamma, log, settings, dry_run=dry_run)
            self.confirm = confirm or default_confirm
        else:
            self.exchange = PaperExchange(self.gamma, log, settings)
            self.confirm = None

        self.scan = ScanAgent(settings, self.store, log)
        self.research = ResearchAgent(settings, self.store, log, self.client)
        self.predict = PredictAgent(
            settings, self.store, log, self.predictor, self.brain, self.client
        )
        self.manager = BrainManager(settings, self.store, log, self.client)
        self.risk = RiskAgent(settings, self.store, log, self.exchange, self.confirm)
        self.postmortem = PostmortemAgent(settings, self.store, log, self.client)
        # Set by run_once when the circuit breaker trips, so a loop driver (server
        # / CLI) can stop the run and wind down open positions gracefully.
        self.breaker_reason = ""

        self._train_models()

    # --- learning ---
    def _train_models(self) -> None:
        self.brain.train_from_experiences(self.store.load_experiences())
        X, y = predictor_training_data(self.store.resolved_trades())
        self.predictor.train(X, y)

    def bankroll(self) -> float:
        return self.settings.bankroll + self.store.realized_pnl(self.mode)

    def circuit_breaker_tripped(self) -> Optional[str]:
        """Trip reason if the daily-loss / loss-streak breaker fires, else None."""
        return circuit_breaker_reason(
            self.store.realized_pnl_today(self.mode),
            self.bankroll(),
            self.store.consecutive_losses(self.mode),
            self.settings,
        )

    def hold_recommendation(self) -> dict:
        """Brain's empirical max-hold recommendation (Teil A.2) — advice only."""
        won, lost = scalp_hold_seconds(self.store.resolved_trades(), self.mode)
        return recommend_max_hold(won, lost, getattr(self.settings, "max_hold_seconds", 300.0))

    # --- counterfactual (veto/mirror) learning ---
    def _record_counterfactuals(self, signals, placed) -> None:
        """Record what the trades we did NOT make would have done (Problem 1).

        For every signal this cycle: if it was NOT executed (vetoed / sized-out) we
        probe its OWN side; for every signal we also probe the MIRROR (opposite)
        side. Settled later against the real snapshot price path. Scalp-only — the
        counterfactual replay uses the scalp exit logic."""
        if not signals or getattr(self.settings, "strategy", "scalp") != "scalp":
            return
        placed_ids = {t.market_id for t in placed}
        veto_reason = {
            sig.market_id: reason
            for sig, ok, reason in getattr(self.manager, "decisions", [])
            if not ok
        }
        now = datetime.now(timezone.utc)
        for sig in signals:
            traded = sig.market_id in placed_ids
            if not traded:
                self._save_cf(
                    sig, sig.is_yes, sig.market_price, sig.edge, "veto",
                    veto_reason.get(sig.market_id, "not executed"), now,
                )
            self._save_cf(
                sig, not sig.is_yes, 1.0 - sig.market_price, -sig.edge, "mirror",
                "mirror of executed trade" if traded else "mirror of vetoed signal", now,
            )

    def _save_cf(self, sig, is_yes, entry_price, edge, source, reason, now) -> None:
        self.store.save_counterfactual(
            Counterfactual(
                market_id=sig.market_id, is_yes=is_yes, entry_price=entry_price,
                entry_ts=now, edge=edge, brain_score=sig.brain_score,
                features=list(sig.features), source=source, reason=reason,
                take_profit=self.settings.take_profit, stop_loss=self.settings.stop_loss,
                max_hold=getattr(self.settings, "max_hold_seconds", 300.0),
            )
        )

    def settle_counterfactuals(self) -> int:
        """Replay pending counterfactuals over the real snapshot price path; settled
        ones become flagged training experiences (if learn_from_vetos). Returns the
        number of NEW training experiences added (0 if none -> no retrain)."""
        pending = self.store.pending_counterfactuals()
        if not pending:
            return 0
        now = datetime.now(timezone.utc)
        learn = bool(getattr(self.settings, "learn_from_vetos", True))
        spread_floor = getattr(self.settings, "min_spread_cost", 0.01)
        added = 0
        for cf in pending:
            series = self.store.snapshots_between(cf.market_id, cf.entry_ts, now)
            res = settle_scalp_path(
                cf.entry_price, cf.is_yes, series, cf.entry_ts, cf.take_profit,
                cf.stop_loss, cf.max_hold, now, spread_floor=spread_floor,
            )
            if res["status"] == "pending":
                continue
            cf.status = res["status"]  # "settled" | "expired"
            cf.settled_at = now
            if res["status"] == "settled":
                cf.exit_price = res["exit_price"]
                cf.pnl = res["pnl"]
                cf.won = res["won"]
                cf.exit_reason = res["exit_reason"]
                self.store.update_counterfactual(cf)
                if learn:
                    self.store.save_experience(
                        Experience(
                            features=cf.features, edge=cf.edge, size=1.0,
                            brain_score=cf.brain_score, won=bool(cf.won), pnl=cf.pnl,
                            mode=self.mode, is_yes=cf.is_yes, is_counterfactual=True,
                        )
                    )
                    added += 1
            else:
                self.store.update_counterfactual(cf)
        if added:
            self._train_models()
            self.log.info("Counterfactuals: %d settled -> brain retrained", added)
        return added

    def brain_diagnostics(self) -> dict:
        """Out-of-sample metrics + feature importance + veto scoreboard (Problem 2)."""
        exps = self.store.load_experiences()
        diag = self.brain.diagnostics(exps)
        diag["counterfactuals"] = self.store.counterfactual_stats()
        real = sum(1 for e in exps if not e.is_counterfactual)
        diag["experiences"] = {
            "real": real, "counterfactual": len(exps) - real, "total": len(exps),
        }
        return diag

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
        # Surface the empirical max-hold advice when the distribution suggests a
        # change (advice only — the slider stays the operator's).
        rec = self.hold_recommendation()
        if rec.get("status") == "ok" and rec.get("direction") != "keep":
            self.log.info("Hold-Analyse: %s", rec["message"])

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
        # Settle any counterfactuals whose scalp window has elapsed (Problem 1):
        # learns from vetoed/mirror setups via the real snapshot price path.
        self.settle_counterfactuals()
        # Circuit breaker (Teil B.2): check AFTER managing open trades (so today's
        # realized PnL / streak are fresh) and BEFORE opening anything new. When it
        # trips we open NOTHING this cycle; open positions are untouched (no abandon)
        # and a loop driver can stop + wind down on seeing ``breaker_reason``.
        self.breaker_reason = self.circuit_breaker_tripped() or ""
        if self.breaker_reason:
            self.log.warning(
                "CIRCUIT BREAKER tripped (%s) — opening no new trades this cycle.",
                self.breaker_reason,
            )
            self._export_dashboard()
            self.log.info("=== Cycle done: circuit breaker active, 0 trades placed ===")
            return []
        candidates = self.scan.run(markets)
        reports = self.research.run(candidates)
        signals = self.predict.run(candidates, reports)
        # Stage 5 meta-controller: the LLM agent approves/vetoes each signal before
        # it can reach execution, and records its reasoning to the DB.
        approved = self.manager.run(signals, reports)
        liq = {c.market.id: c.market.liquidity for c in candidates}
        spreads = {c.market.id: c.market.spread for c in candidates}
        placed = self.risk.run(approved, self.bankroll(), liq, spreads)
        # Record counterfactuals for what we did NOT trade (vetoed/sized-out) and the
        # mirror of what we did — settled next cycles via the snapshot price path.
        self._record_counterfactuals(signals, placed)
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

"""Wires the five stages together (one-shot or loop) and runs the learning loop."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from tradebot import watchdog as _wd
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
from tradebot.exchange.execution_style import resolve_maker_fill
from tradebot.exchange.paper import PaperExchange
from tradebot.exchange.polymarket import PolymarketExchange
from tradebot.llm import LLMUnavailableError, make_client
from tradebot.ml.bootstrap import predictor_training_data
from tradebot.ml.model import Predictor
from tradebot.models import Counterfactual, Experience, Mode, ResolutionStatus
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

        _wd.start(log=log)
        self._train_models()
        self._resume_queue()

    # --- startup recovery ---
    def _resume_queue(self) -> None:
        """Retry any 'pending' execution-queue entries left over from a previous crash."""
        pending = self.store.pending_executions()
        if not pending:
            return
        self.log.warning("ExecutionQueue: resuming %d pending trade(s) from previous run", len(pending))
        for row in pending:
            eid = row["execution_id"]
            market_id = row["market_id"]
            is_yes = bool(row["is_yes"])
            retries = int(row["retries"])

            if retries >= 3:
                self.log.warning("ExecutionQueue: %s exceeded max retries — marking failed", market_id)
                self.store.mark_execution_failed(eid, "max retries exceeded on resume")
                continue

            if self.store.has_open_execution(market_id, is_yes, self.mode):
                self.log.info("ExecutionQueue: open trade already exists for %s — marking done (idempotent)", market_id)
                self.store.mark_execution_done(eid)
                continue

            try:
                from tradebot.models import Order
                order = Order.model_validate_json(row["order_json"])
            except Exception as e:
                self.log.error("ExecutionQueue: cannot parse order for %s: %s — marking failed", market_id, e)
                self.store.mark_execution_failed(eid, str(e))
                continue

            try:
                trade = self.exchange.place_order(order)
            except Exception as e:
                self.log.warning("ExecutionQueue: place_order failed for %s: %s", market_id, e)
                self.store.bump_execution_retry(eid, str(e))
                continue

            if trade is not None:
                trade.kind = "scalp" if self.settings.strategy == "scalp" else "resolve"
                self.store.save_trade(trade)
                self.store.mark_execution_done(eid)
                self.log.info("ExecutionQueue: resumed trade for '%s'", market_id[:40])
            else:
                self.store.bump_execution_retry(eid, "place_order returned None on resume")

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
        settled_pairs: dict[str, dict] = {}  # market_id -> {real, mirror}
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
                    # Track paired outcomes for reflection logging
                    settled_pairs[cf.market_id] = {
                        "is_yes": cf.is_yes,
                        "won": cf.won,
                        "source": cf.source,
                    }
            else:
                self.store.update_counterfactual(cf)

        if added:
            # Log counterfactual reflection insights for paired markets
            for mid, outcome in settled_pairs.items():
                # If we have a pair (both mirror and veto settled for same market)
                # we could log a reflection insight here, but the minimal approach
                # just logs that we added weighted experiences.
                pass
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

    def _scalp_trigger(self, t, market, wind_down_deadline: Optional[float] = None) -> Optional[str]:
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
        # Stop-Deadline abgelaufen: sofort schließen, egal ob Gewinn oder Verlust
        if wind_down_deadline is not None and time.time() >= wind_down_deadline:
            return "stop"
        return None

    def resolve_pending_makers(self, markets=None) -> None:
        """Decide resting PAPER maker orders against the REAL price path (Teil B.3).

        A maker BUY was rested one tick inside last cycle (``PaperExchange.place_order``
        → status ``pending_maker``). Here we confirm it honestly — never on faith:

          * **filled**  — the real observed side price traded down to the limit within
            ``maker_timeout_seconds`` → open at the better limit price; the scalp clock
            starts at the fill time (so max-hold counts from the real fill, not posting).
          * **missed**  — the window passed without the price reaching the bid → take at
            the CURRENT price (the live taker fallback, mirrored honestly in paper).
          * **pending** — window still open → wait for the next cycle's ticks.

        Stale leftovers (e.g. resolved long after a paused loop, or the market vanished)
        are cancelled, not turned into a stale taker fill. Live is untouched — its fills
        are confirmed by the real CLOB inside ``place_order``."""
        pend = self.store.pending_maker_trades(self.mode)
        if not pend:
            return
        by_id = {m.id: m for m in (markets or [])}
        now = datetime.now(timezone.utc)
        timeout = float(getattr(self.settings, "maker_timeout_seconds", 60.0))
        opened, missed, canceled = 0, 0, 0
        for t in pend:
            posted = t.opened_at if t.opened_at.tzinfo else t.opened_at.replace(tzinfo=timezone.utc)
            deadline = posted + timedelta(seconds=timeout)
            m = by_id.get(t.market_id)
            series = self.store.snapshots_between(t.market_id, posted, now)
            if m is not None:  # freshest data point = the current live price
                series = series + [(now, m.yes_price, m.spread)]
            res = resolve_maker_fill(t.entry_price, t.is_yes, series, posted, deadline, now)
            if res["status"] == "pending":
                continue
            if res["status"] == "filled":
                t.status, t.exec_style = "open", "maker"
                t.opened_at = res["fill_ts"]
                self.store.open_pending_trade(t)
                opened += 1
                continue
            # missed — take at the current price, unless it's stale or the market is gone.
            stale = (now - deadline).total_seconds() > timeout
            if m is None or stale:
                self._cancel_pending_maker(t)
                canceled += 1
                continue
            t.status, t.exec_style = "open", "taker"
            t.entry_price = round(m.yes_price if t.is_yes else 1.0 - m.yes_price, 6)
            t.opened_at = now
            self.store.open_pending_trade(t)
            missed += 1
        if opened or missed or canceled:
            self.log.info(
                "Maker-Gebote aufgelöst: %d gefüllt (Tick gespart), %d verpasst→Taker, %d storniert",
                opened, missed, canceled,
            )

    def _cancel_pending_maker(self, t) -> None:
        """A resting maker that never filled and is stale / lost its market: cancel it.
        Terminal status ``canceled`` keeps it out of open/resolved/exposure and out of
        the brain's training data (it was a non-event, neither win nor loss)."""
        t.status, t.kind, t.pnl, t.won = "canceled", "scalp", 0.0, None
        t.resolved_at = datetime.now(timezone.utc)
        self.store.update_trade(t)
        self.log.info("Paper Maker storniert (kein Fill): '%s'", t.question[:50])

    def manage_open(self, markets=None, wind_down_deadline: Optional[float] = None):
        """Close/settle open trades against FRESH prices.

        scalp  -> exit on price (take-profit / stop-loss / max-hold / stop-deadline);
        resolve -> settle from the real resolution.

        wind_down_deadline: Unix-Timestamp (von time.time()) ab dem alle offenen Trades
        sofort geschlossen werden — auch ohne Gewinn. Wird gesetzt wenn der Nutzer Stop
        drückt, damit ALLE Trades innerhalb von max_hold_seconds enden."""
        by_id = {m.id: m for m in (markets or [])}
        resolved = []
        for t in self.store.open_trades(self.mode):
            # Beim Wind-down (stop_deadline gesetzt) immer Scalp-Logik verwenden,
            # auch wenn strategy="resolve" konfiguriert ist — kein Trade bleibt offen.
            if self.settings.strategy == "scalp" or wind_down_deadline is not None:
                m = by_id.get(t.market_id)
                if m is None:
                    # Markt fehlt in der Bulk-Liste (aufgelöst, in Settling, oder unter
                    # dem Liquiditätsfilter). NICHT still überspringen — sonst bleibt der
                    # Trade ewig offen. Stattdessen aktiv beschaffen, was zum Schließen
                    # nötig ist (echte Resolution oder direkter Einzel-Preisabruf).
                    r = self._close_missing(t, wind_down_deadline)
                else:
                    reason = self._scalp_trigger(t, m, wind_down_deadline=wind_down_deadline)
                    r = self.exchange.close(t, m, reason=reason) if reason else None
            else:
                r = self.exchange.settle(t)
            if r is not None:
                self._record_resolved(r)
                resolved.append(r)
        self._after_resolved(resolved, "Closed")
        return resolved

    def _close_missing(self, t, wind_down_deadline: Optional[float] = None):
        """Schließe einen Trade, dessen Markt NICHT in der Bulk-Liste ist.

        Ein offener Trade darf nie still hängenbleiben — der Code holt sich aktiv, was
        er zum Schließen braucht, über Direktabrufe der einzelnen Markt-Endpoints
        (die auch funktionieren, wenn der Markt aus ``list_markets()`` gefallen ist):

          1. **Echte Resolution** (``/markets/{id}``): Markt aufgelöst (z.B. UFC-Kampf
             vorbei → Settling → entschieden) → real setteln mit dem echten Ergebnis.
          2. **Noch offen, nur aus der Liste gefallen** (Liquiditätsfilter / temporär
             inaktiv): Einzel-Preisabruf → frischer Live-Preis → normaler Scalp-Trigger.
             max_hold garantiert, dass der Trade spätestens nach ``max_hold_seconds``
             schließt; ein junger Trade bei transientem Drop-out bleibt offen und wird
             nächsten Zyklus erneut geprüft (kein verfrühtes Rauswerfen).
          3. **Weder Resolution noch Preis** (echtes Settling-Limbo / API-Fehler): laut
             loggen statt still schlucken; Trade bleibt offen und der nächste Zyklus
             versucht es erneut. Erst hier ist die Stuck-Warnung im Dashboard berechtigt.

        Gibt den geschlossenen/gesetelten Trade zurück, sonst None (bleibt offen)."""
        res = self.gamma.get_resolution(t.market_id)
        if res.status != ResolutionStatus.OPEN:
            # Aufgelöst / void / ambiguous / error — über den echten Settlement-Pfad
            # (AMBIGUOUS und ERROR geben None zurück → bleiben für manuelle Prüfung offen).
            r = self.exchange.settle(t, resolution=res)
            if r is not None:
                self.log.info(
                    "Markt nicht in Liste, aber aufgelöst — '%s' real gesettelt (pnl %+.2f).",
                    t.question[:60], r.pnl,
                )
            else:
                self.log.error(
                    "Trade '%s' (market_id=%s) nicht automatisch schließbar: Resolution=%s (%s). "
                    "Bleibt offen — bitte auf Polymarket prüfen.",
                    t.question[:60], t.market_id, res.status.value, res.reason or "—",
                )
            return r

        # Markt noch OPEN, aber aus der Bulk-Liste gefallen → frischen Preis direkt holen.
        m = self.gamma.fetch_market(t.market_id)
        if m is None:
            self.log.error(
                "Trade '%s' (market_id=%s) nicht automatisch schließbar: Markt offen, aber "
                "kein Live-Preis verfügbar. Bleibt offen — bitte auf Polymarket prüfen.",
                t.question[:60], t.market_id,
            )
            return None
        reason = self._scalp_trigger(t, m, wind_down_deadline=wind_down_deadline)
        if reason is None:
            return None  # kein Exit-Grund (junger Trade / transienter Drop-out) → nächster Zyklus
        r = self.exchange.close(t, m, reason=reason)
        if r is None:
            self.log.error(
                "Trade '%s' (market_id=%s): SELL zum Schließen nicht angenommen — bleibt offen.",
                t.question[:60], t.market_id,
            )
        else:
            self.log.info(
                "Markt '%s' per Einzelabruf geschlossen (%s, pnl %+.2f).",
                t.question[:60], reason, r.pnl,
            )
        return r

    def _watchdog_abort(self, stage: str) -> bool:
        """Return True and log if the watchdog fired — caller should abort the cycle."""
        if _wd.fired.is_set():
            self.log.error("WATCHDOG fired during '%s' — aborting cycle, starting fresh next run", stage)
            _wd.reset()
            return True
        return False

    # --- main cycle ---
    def run_once(self):
        _wd.beat()
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
        _wd.beat()
        # Confirm/deny resting paper maker orders against the real price path BEFORE
        # managing open trades, so a maker filled this cycle becomes a normal open
        # position right away.
        self.resolve_pending_makers(markets)
        _wd.beat()
        self.manage_open(markets)
        _wd.beat()
        # Settle any counterfactuals whose scalp window has elapsed (Problem 1):
        # learns from vetoed/mirror setups via the real snapshot price path.
        self.settle_counterfactuals()
        _wd.beat()
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
        _wd.beat()
        if self._watchdog_abort("scan"):
            return []
        reports = self.research.run(candidates)
        _wd.beat()
        if self._watchdog_abort("research"):
            return []
        signals = self.predict.run(candidates, reports)
        _wd.beat()
        if self._watchdog_abort("predict"):
            return []
        # Stage 5 meta-controller: the LLM agent approves/vetoes each signal before
        # it can reach execution, and records its reasoning to the DB.
        approved = self.manager.run(signals, reports)
        _wd.beat()
        if self._watchdog_abort("brainmanager"):
            return []
        liq = {c.market.id: c.market.liquidity for c in candidates}
        spreads = {c.market.id: c.market.spread for c in candidates}
        placed = self.risk.run(approved, self.bankroll(), liq, spreads)
        _wd.beat()
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

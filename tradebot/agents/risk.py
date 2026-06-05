"""Stage 4: Kelly sizing + caps + brain veto, then execute via the exchange.

Execution is queue-first: every approved trade is written to the persistent
``execution_queue`` table BEFORE ``place_order`` is called.  The queue entry
is only marked 'done' after the exchange confirms the placement, so a crash
between sizing and execution leaves a recoverable 'pending' entry that the
orchestrator retries on the next startup.

Idempotency: before executing a queued entry, the agent checks whether an
open trade for the same market+side already exists and skips if so (no double
trades across retries or restarts).
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Optional

from tradebot.agents.base import Agent
from tradebot.models import Order, Signal, Trade
from tradebot.risk.kelly import size_position

_MAX_EXEC_RETRIES = 3


class RiskAgent(Agent):
    name = "risk"

    def __init__(self, settings, store, log, exchange, confirm: Optional[Callable] = None):
        super().__init__(settings, store, log)
        self.exchange = exchange
        self.confirm = confirm

    def run(
        self,
        signals: list[Signal],
        bankroll: float,
        liquidity_by_market: dict[str, float],
        spread_by_market: dict[str, float] | None = None,
    ) -> list[Trade]:
        placed: list[Trade] = []
        exposure = self.store.open_exposure(self.exchange.mode)
        spread_by_market = spread_by_market or {}

        for sig in signals:
            liq = liquidity_by_market.get(sig.market_id, 1e9)
            decision = size_position(sig, bankroll, self.settings, exposure, liq)
            if not decision.approved:
                self.log.info("Risk: skip '%s' — %s", sig.question[:40], decision.reason)
                continue

            order = Order(
                market_id=sig.market_id, token_id=sig.token_id, question=sig.question,
                side=sig.side, is_yes=sig.is_yes, price=sig.market_price,
                size=decision.size, mode=self.exchange.mode,
                edge=sig.edge, spread=spread_by_market.get(sig.market_id, 0.0),
            )

            # Persist intent before touching the exchange — recoverable on crash.
            execution_id = str(uuid.uuid4())
            self.store.enqueue_execution(
                execution_id, sig.market_id, sig.is_yes, order.model_dump_json()
            )

            trade = self._execute(execution_id, order, sig)
            if trade is None:
                continue

            exposure += decision.amount
            placed.append(trade)

        return placed

    def _execute(self, execution_id: str, order: Order, sig: Signal) -> Optional[Trade]:
        """Place order with up to _MAX_EXEC_RETRIES attempts.  Marks queue done/failed."""
        for attempt in range(_MAX_EXEC_RETRIES):
            if attempt > 0:
                self.log.warning(
                    "Risk: execution retry %d/%d — '%s'",
                    attempt + 1, _MAX_EXEC_RETRIES, order.question[:40],
                )
                time.sleep(2 ** attempt)

            # Idempotency: if a previous attempt already created the trade, stop.
            if self.store.has_open_execution(order.market_id, order.is_yes, order.mode):
                self.log.info(
                    "Risk: open trade already exists for '%s' %s — skipping (idempotent)",
                    order.market_id, "YES" if order.is_yes else "NO",
                )
                self.store.mark_execution_done(execution_id)
                return None

            try:
                trade = self.exchange.place_order(order, confirm=self.confirm)
            except Exception as e:
                self.log.warning("Risk: place_order raised (attempt %d/%d): %s", attempt + 1, _MAX_EXEC_RETRIES, e)
                self.store.bump_execution_retry(execution_id, str(e))
                continue

            if trade is None:
                self.store.bump_execution_retry(execution_id, "place_order returned None")
                continue

            # Confirmed by exchange — decorate and persist.
            trade.features = sig.features
            trade.edge = sig.edge
            trade.brain_score = sig.brain_score
            trade.kind = "scalp" if self.settings.strategy == "scalp" else "resolve"
            self.store.save_trade(trade)
            self.store.mark_execution_done(execution_id)
            self.log.info(
                "Risk: PLACED %s '%s' %.0f sh @ %.2f ($%.0f | edge %.2f brain %.2f)",
                "YES" if sig.is_yes else "NO", sig.question[:30], order.size,
                order.price, order.cost, sig.edge, sig.brain_score,
            )
            return trade

        self.store.mark_execution_failed(execution_id, "all retries exhausted")
        self.log.error("Risk: FAILED to place '%s' after %d attempts", order.question[:40], _MAX_EXEC_RETRIES)
        return None

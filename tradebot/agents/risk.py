"""Stage 4: Kelly sizing + caps + brain veto, then execute via the exchange."""
from __future__ import annotations

from typing import Callable, Optional

from tradebot.agents.base import Agent
from tradebot.models import Order, Signal, Trade
from tradebot.risk.kelly import size_position


class RiskAgent(Agent):
    name = "risk"

    def __init__(self, settings, store, log, exchange, confirm: Optional[Callable] = None):
        super().__init__(settings, store, log)
        self.exchange = exchange
        self.confirm = confirm

    def run(
        self, signals: list[Signal], bankroll: float, liquidity_by_market: dict[str, float]
    ) -> list[Trade]:
        placed: list[Trade] = []
        exposure = self.store.open_exposure(self.exchange.mode)

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
            )
            trade = self.exchange.place_order(order, confirm=self.confirm)
            if trade is None:
                continue

            trade.features = sig.features
            trade.edge = sig.edge
            trade.brain_score = sig.brain_score
            trade.kind = "scalp" if self.settings.strategy == "scalp" else "resolve"
            self.store.save_trade(trade)
            exposure += decision.amount
            placed.append(trade)
            self.log.info(
                "Risk: PLACED %s '%s' %.0f sh @ %.2f ($%.0f | edge %.2f brain %.2f)",
                "YES" if sig.is_yes else "NO", sig.question[:30], decision.size,
                sig.market_price, decision.amount, sig.edge, sig.brain_score,
            )
        return placed

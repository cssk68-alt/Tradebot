"""Paper exchange — NO simulated outcomes.

Fills paper orders at the real market price, and resolves them from REAL data:
  - scalp exit  (`close`):  realized pnl from the market's CURRENT price, net of spread
  - hold to event (`settle`): the REAL Gamma resolution (paper-with-real-settlement)

So the brain learns from real price behaviour / real resolutions — only the money
is simulated, never the outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from tradebot.exchange.base import Exchange, mark_yes_no, settle_from_resolution
from tradebot.models import Market, Mode, Order, Trade


class PaperExchange(Exchange):
    def __init__(self, gamma, log, settings):
        super().__init__(gamma, log)
        self.settings = settings

    @property
    def mode(self) -> Mode:
        return Mode.PAPER

    def place_order(
        self, order: Order, confirm: Optional[Callable[[Order], bool]] = None
    ) -> Optional[Trade]:
        return Trade(
            market_id=order.market_id, token_id=order.token_id, question=order.question,
            side=order.side, is_yes=order.is_yes, entry_price=order.price, size=order.size,
            mode=Mode.PAPER, status="open",
        )

    def settle(self, trade: Trade, force_yes: Optional[bool] = None) -> Optional[Trade]:
        """Hold-to-event: settle from the REAL Gamma resolution (None if not resolved
        yet, on API error, or on an ambiguous outcome). ``force_yes`` forces the
        outcome for tests/backfills."""
        if force_yes is not None:
            return mark_yes_no(trade, bool(force_yes))
        return settle_from_resolution(trade, self.gamma.get_resolution(trade.market_id), self.log)

    def close(self, trade: Trade, market: Market, reason: str = "time") -> Optional[Trade]:
        """Scalp exit: realize pnl from the market's CURRENT price, charging the
        round-trip spread (buy at ask, sell at bid). Flat price => small spread loss."""
        cur = market.yes_price if trade.is_yes else 1.0 - market.yes_price
        spread = max(market.spread, self.settings.min_spread_cost)
        pnl = trade.size * (cur - trade.entry_price - spread)
        trade.exit_price = round(cur, 4)
        trade.pnl = pnl
        trade.won = pnl > 0
        trade.kind = "scalp"
        trade.status = "resolved"
        trade.resolved_at = datetime.now(timezone.utc)
        self.log.info(
            "Paper close (%s): %s %.0f sh  %.3f -> %.3f  spread %.3f  pnl %+.2f",
            reason, "YES" if trade.is_yes else "NO", trade.size,
            trade.entry_price, cur, spread, pnl,
        )
        return trade

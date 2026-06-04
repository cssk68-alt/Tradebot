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
from tradebot.exchange.execution_style import decide_execution_style
from tradebot.exchange.ticks import get_tick_size
from tradebot.models import Market, Mode, Order, Resolution, Trade


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
        # Maker-first (Teil B.3), modelled HONESTLY in paper: a maker order is NOT
        # filled at the better price on faith. We rest it as ``pending_maker`` at the
        # limit and let the orchestrator confirm or deny the fill against the REAL
        # recorded price path next cycle (``resolve_pending_makers``) — exactly how
        # the live CLOB only fills when the book actually trades to the order. A taker
        # crosses immediately and opens at the current price.
        plan = decide_execution_style(order.price, order.edge, order.spread, self.settings)
        if plan.style == "maker":
            self.log.info(
                "Paper Maker-Gebot ruht: %s %.0f sh @ %.3f (bis %ds) — Fill folgt aus echtem "
                "Preispfad",
                "YES" if order.is_yes else "NO", order.size, plan.limit_price,
                int(float(getattr(self.settings, "maker_timeout_seconds", 60.0))),
            )
            return Trade(
                market_id=order.market_id, token_id=order.token_id, question=order.question,
                side=order.side, is_yes=order.is_yes, entry_price=plan.limit_price, size=order.size,
                mode=Mode.PAPER, status="pending_maker", exec_style="maker",
            )
        self.log.info(
            "Paper fill (taker): %s %.0f sh @ %.3f  (%s)",
            "YES" if order.is_yes else "NO", order.size, order.price, plan.reason,
        )
        return Trade(
            market_id=order.market_id, token_id=order.token_id, question=order.question,
            side=order.side, is_yes=order.is_yes, entry_price=order.price, size=order.size,
            mode=Mode.PAPER, status="open", exec_style="taker",
        )

    def settle(
        self,
        trade: Trade,
        force_yes: Optional[bool] = None,
        resolution: Optional[Resolution] = None,
    ) -> Optional[Trade]:
        """Hold-to-event: settle from the REAL Gamma resolution (None if not resolved
        yet, on API error, or on an ambiguous outcome). ``force_yes`` forces the
        outcome for tests/backfills; ``resolution`` settles from an already-fetched
        Resolution instead of querying Gamma again."""
        if force_yes is not None:
            return mark_yes_no(trade, bool(force_yes))
        res = resolution if resolution is not None else self.gamma.get_resolution(trade.market_id)
        return settle_from_resolution(trade, res, self.log)

    def close(self, trade: Trade, market: Market, reason: str = "time") -> Optional[Trade]:
        """Scalp exit: realize pnl from the market's CURRENT price, charging the
        round-trip spread (buy at ask, sell at bid). Flat price => small spread loss."""
        cur = market.yes_price if trade.is_yes else 1.0 - market.yes_price
        # Round-trip spread floor = one tick at this price (the realistic minimum),
        # capped by min_spread_cost. A flat absolute floor is nonsensical below ~1c:
        # at a 0.0015 longshot it exceeds the price itself and — times the huge share
        # count (size = stake/price) — loses many times the stake.
        spread = max(market.spread, min(self.settings.min_spread_cost, get_tick_size(trade.entry_price)))
        pnl = trade.size * (cur - trade.entry_price - spread)
        # A long can never lose more than its stake (the price floors at 0).
        pnl = max(pnl, -trade.size * trade.entry_price)
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

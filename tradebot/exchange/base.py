"""Abstract exchange interface + shared settlement helpers.

Paper and live implementations share market data (both read from Gamma) and the
mapping from a typed ``Resolution`` to a settled ``Trade``; they differ only in
execution and how a close (SELL) is performed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable, Optional

from tradebot.models import (
    Market,
    Mode,
    Order,
    Resolution,
    ResolutionStatus,
    Trade,
)


class Exchange(ABC):
    def __init__(self, gamma, log):
        self.gamma = gamma
        self.log = log

    @property
    @abstractmethod
    def mode(self) -> Mode: ...

    def list_markets(self) -> list[Market]:
        return self.gamma.fetch_markets()

    @abstractmethod
    def place_order(
        self, order: Order, confirm: Optional[Callable[[Order], bool]] = None
    ) -> Optional[Trade]:
        """Execute an order. Returns the opened Trade, or None if not placed."""

    @abstractmethod
    def settle(self, trade: Trade, force_yes: Optional[bool] = None) -> Optional[Trade]:
        """Resolve an open trade if its market has resolved. Returns the resolved
        Trade (status='resolved', pnl/won set) or None if still open."""

    @abstractmethod
    def close(self, trade: Trade, market: Market, reason: str = "time") -> Optional[Trade]:
        """Scalp exit: close (sell) an open position at the market's CURRENT price.
        Returns the closed Trade with realized, net-of-spread pnl/won set."""


def mark_yes_no(trade: Trade, resolved_yes: bool) -> Trade:
    """Settle a hold-to-event trade from a definitive YES/NO outcome."""
    won = resolved_yes if trade.is_yes else (not resolved_yes)
    trade.resolved_yes = resolved_yes
    trade.won = won
    trade.pnl = (
        trade.size * (1.0 - trade.entry_price) if won else -trade.size * trade.entry_price
    )
    trade.kind = "resolve"
    trade.status = "resolved"
    trade.resolved_at = datetime.now(timezone.utc)
    return trade


def _mark_void(trade: Trade) -> Trade:
    """Canceled / void market — treat as a refund (pnl 0); won is None so the
    non-outcome is not fed to the brain as a loss."""
    trade.resolved_yes = None
    trade.won = None
    trade.pnl = 0.0
    trade.kind = "resolve"
    trade.status = "resolved"
    trade.resolved_at = datetime.now(timezone.utc)
    return trade


def settle_from_resolution(trade: Trade, res: Resolution, log) -> Optional[Trade]:
    """Map a typed Resolution to a settled Trade, or None if it must stay open.

    OPEN      -> still running (None, silent)
    ERROR     -> API/network failure (None, logged) — never invent an outcome
    AMBIGUOUS -> closed but unclear (None, logged) — left for manual review
    CANCELED  -> void/refund, settled with pnl 0
    YES/NO    -> settled normally
    """
    status = res.status
    if status == ResolutionStatus.OPEN:
        return None
    if status == ResolutionStatus.ERROR:
        log.error("Settle: API error for %s (%s) — trade stays open", trade.market_id, res.reason)
        return None
    if status == ResolutionStatus.AMBIGUOUS:
        log.warning(
            "Settle: ambiguous resolution for %s (%s) — left open for review",
            trade.market_id, res.reason,
        )
        return None
    if status == ResolutionStatus.CANCELED:
        log.info("Settle: %s canceled/void (%s) — refund, pnl 0", trade.market_id, res.reason)
        return _mark_void(trade)
    return mark_yes_no(trade, bool(res.resolved_yes))

"""Abstract exchange interface. Paper and live implementations share market data
(both read from Gamma) and differ only in execution + settlement."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

from tradebot.models import Market, Mode, Order, Trade


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

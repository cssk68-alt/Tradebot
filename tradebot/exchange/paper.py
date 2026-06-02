"""Simulated exchange — fills paper orders at the market price and simulates
resolution. The paper-mode outcome has a latent YES probability that depends on
sentiment (a learnable information edge), so the full learning loop runs without
real money or waiting days for settlement."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np

from tradebot.exchange.base import Exchange
from tradebot.ml.features import PRICE_IDX, SENTIMENT_IDX
from tradebot.models import Mode, Order, Trade


class PaperExchange(Exchange):
    def __init__(self, gamma, log, settings, seed: int = 12345):
        super().__init__(gamma, log)
        self.settings = settings
        self.rng = np.random.default_rng(seed)

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
        yes_outcome = bool(force_yes) if force_yes is not None else self._simulate_yes(trade)
        won = yes_outcome if trade.is_yes else (not yes_outcome)
        trade.resolved_yes = yes_outcome
        trade.won = won
        trade.pnl = (
            trade.size * (1.0 - trade.entry_price) if won else -trade.size * trade.entry_price
        )
        trade.status = "resolved"
        trade.resolved_at = datetime.now(timezone.utc)
        return trade

    def _simulate_yes(self, trade: Trade) -> bool:
        feats = trade.features or []
        yes_price = feats[PRICE_IDX] if feats else 0.5
        sentiment = feats[SENTIMENT_IDX] if len(feats) > SENTIMENT_IDX else 0.0
        latent = yes_price + 0.25 * sentiment + float(self.rng.normal(0.0, 0.05))
        latent = min(0.98, max(0.02, latent))
        return bool(self.rng.random() < latent)

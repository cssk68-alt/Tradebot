"""Pydantic data models shared across the pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Mode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class Market(BaseModel):
    """A binary Polymarket market (YES/NO outcome tokens)."""

    id: str
    question: str
    yes_token_id: str = ""
    no_token_id: str = ""
    yes_price: float = 0.5  # implied probability of YES, 0..1
    volume_24h: float = 0.0
    liquidity: float = 0.0
    end_date: Optional[datetime] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None

    @property
    def spread(self) -> float:
        if self.best_bid is not None and self.best_ask is not None:
            return max(0.0, self.best_ask - self.best_bid)
        return 0.0

    def days_to_resolution(self, now: Optional[datetime] = None) -> float:
        if self.end_date is None:
            return 9999.0
        end = self.end_date
        if end.tzinfo is None:  # treat naive timestamps as UTC
            end = end.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (end - now).total_seconds() / 86400.0


class Candidate(BaseModel):
    """A market that passed the scan filters, with anomaly flags."""

    market: Market
    flags: list[str] = Field(default_factory=list)
    price_move: float = 0.0  # abs change vs last snapshot


class ResearchReport(BaseModel):
    market_id: str
    sentiment: float = 0.0  # -1..1
    narrative: str = ""
    n_sources: int = 0
    implied_prob: float = 0.5


class Signal(BaseModel):
    market_id: str
    token_id: str
    question: str
    side: Side
    market_price: float
    true_prob: float
    edge: float
    confidence: float
    is_yes: bool = True  # True = bet YES outcome, False = bet NO
    features: list[float] = Field(default_factory=list)
    brain_score: float = 0.5
    rationale: str = ""


class Order(BaseModel):
    market_id: str
    token_id: str
    question: str
    side: Side
    is_yes: bool = True
    price: float
    size: float  # number of outcome shares
    mode: Mode

    @property
    def cost(self) -> float:
        return self.price * self.size


class Trade(BaseModel):
    id: Optional[int] = None
    market_id: str
    token_id: str
    question: str
    side: Side
    entry_price: float
    size: float
    mode: Mode
    is_yes: bool = True
    status: str = "open"  # "open" | "resolved"
    pnl: float = 0.0
    won: Optional[bool] = None
    resolved_yes: Optional[bool] = None
    brain_score: float = 0.5
    edge: float = 0.0
    features: list[float] = Field(default_factory=list)
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None


class Experience(BaseModel):
    """One resolved trade, used to train the brain (wins and losses)."""

    features: list[float]
    edge: float
    size: float
    brain_score: float
    won: bool
    pnl: float
    mode: Mode


class Lesson(BaseModel):
    trade_id: Optional[int] = None
    category: str
    cause: str
    recommendation: str
    text: str = ""

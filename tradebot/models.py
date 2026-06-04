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


class ResolutionStatus(str, Enum):
    """Settlement outcome of a market — replaces the old boolean True/False/None,
    so an API error, a still-open market and a canceled/void event are distinct."""

    OPEN = "open"  # not resolved yet
    YES = "yes"  # resolved YES
    NO = "no"  # resolved NO
    CANCELED = "canceled"  # void / refunded (e.g. 50/50 terminal price)
    AMBIGUOUS = "ambiguous"  # closed but terminal price is not a clean 0/1
    ERROR = "error"  # could not be determined (API/network error)


class Resolution(BaseModel):
    """Typed result of a settlement query."""

    status: ResolutionStatus
    resolved_yes: Optional[bool] = None
    reason: str = ""


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
    sentiment: float = 0.0  # -1..1, aggregate across all sources
    narrative: str = ""
    n_sources: int = 0
    implied_prob: float = 0.5
    # Source-separated signals so the brain can learn per-source noise (Reddit
    # irony/hype vs. RSS macro news) instead of one collapsed score.
    rss_sentiment: float = 0.0
    reddit_sentiment: float = 0.0
    rss_sources: int = 0
    reddit_sources: int = 0
    web_sentiment: float = 0.0
    web_sources: int = 0
    source_quality: float = 0.0  # 0..1 confidence in the research depth
    # Hard quantitative prior from a live fact feed (crypto price / sports odds).
    # ``fact_prob`` is a calibrated P(YES); None means no applicable fact found.
    fact_prob: Optional[float] = None
    fact_confidence: float = 0.0  # 0..1 trust in the fact prior
    fact_text: str = ""  # human-readable fact, also injected into the narrative
    fact_source: str = ""  # "coingecko" | "odds-api" | ""


class Signal(BaseModel):
    market_id: str
    token_id: str
    question: str
    side: Side
    market_price: float
    true_prob: float  # blended XGBoost + LLM probability
    model_prob: float = 0.5  # raw XGBoost P(YES), kept separate for the BrainManager
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
    # Microstructure context the exchange needs to choose maker-vs-taker (Teil B.3)
    # and that would otherwise be lost between the signal and execution.
    edge: float = 0.0
    spread: float = 0.0

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
    status: str = "open"  # "open" | "resolved" | "pending_maker" (resting bid) | "canceled"
    kind: str = "resolve"  # "resolve" = hold to event; "scalp" = close on price
    exec_style: str = ""  # "maker" | "taker" — how the entry was executed (Teil B.3)
    pnl: float = 0.0
    won: Optional[bool] = None
    resolved_yes: Optional[bool] = None
    exit_price: Optional[float] = None  # price we closed at (scalp exits)
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
    is_yes: bool = True  # traded side — the brain needs it to learn P(trade wins)
    # True when this row is a COUNTERFACTUAL (a veto/mirror replayed over the real
    # price path), not a trade the bot actually executed. Lets validation/dashboard
    # separate self-selected trades from counterfactual learning data.
    is_counterfactual: bool = False


class Counterfactual(BaseModel):
    """A trade the bot did NOT execute (vetoed, sized-out, or the opposite/mirror
    side of a real trade), tracked so the brain can later learn what WOULD have
    happened over our short scalp window — replayed against the real price path
    in ``snapshots`` (no simulated outcome; only the position is hypothetical)."""

    id: Optional[int] = None
    market_id: str
    is_yes: bool
    entry_price: float  # the traded SIDE price at signal time (yes or 1-yes)
    entry_ts: datetime
    edge: float = 0.0
    brain_score: float = 0.5
    features: list[float] = Field(default_factory=list)
    source: str = "veto"  # "veto" (own side we blocked) | "mirror" (opposite side)
    reason: str = ""  # why it was not traded (veto/size reason) — for the scoreboard
    take_profit: float = 0.02
    stop_loss: float = 0.03
    max_hold: float = 300.0
    status: str = "pending"  # "pending" | "settled" | "expired"
    exit_price: Optional[float] = None
    pnl: float = 0.0
    won: Optional[bool] = None
    exit_reason: str = ""  # "take_profit" | "stop_loss" | "time"
    settled_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Lesson(BaseModel):
    trade_id: Optional[int] = None
    category: str
    cause: str
    recommendation: str
    text: str = ""


class ManagerDecision(BaseModel):
    """The BrainManager's (Claude Haiku) final verdict on a signal — persisted for
    every judged signal so its reasoning is auditable in the local database."""

    id: Optional[int] = None
    market_id: str
    question: str
    approved: bool
    reason: str
    model_prob: float = 0.5
    brain_score: float = 0.5
    edge: float = 0.0
    is_yes: bool = True
    rss_sentiment: float = 0.0
    reddit_sentiment: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

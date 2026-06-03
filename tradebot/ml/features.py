"""Feature engineering shared by the predictor (XGBoost) and the brain."""
from __future__ import annotations

import math
from typing import Optional

from tradebot.models import Market, ResearchReport

FEATURE_NAMES = [
    "yes_price", "log_volume", "log_liquidity", "spread", "days_norm",
    "price_move", "sentiment", "log_sources", "dist_from_half", "fav_flag",
]
FEATURE_DIM = len(FEATURE_NAMES)

PRICE_IDX = 0
SENTIMENT_IDX = 6


def build_features(
    market: Market, report: Optional[ResearchReport] = None, price_move: float = 0.0
) -> list[float]:
    sentiment = report.sentiment if report else 0.0
    n_sources = report.n_sources if report else 0
    days = market.days_to_resolution()
    return [
        market.yes_price,
        math.log1p(max(0.0, market.volume_24h)) / 15.0,
        math.log1p(max(0.0, market.liquidity)) / 12.0,
        market.spread,
        min(days, 60.0) / 60.0,
        price_move,
        sentiment,
        math.log1p(max(0, n_sources)) / 4.0,
        abs(market.yes_price - 0.5),
        1.0 if market.yes_price > 0.5 else 0.0,
    ]

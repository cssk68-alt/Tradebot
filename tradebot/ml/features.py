"""Feature engineering for the predictor (XGBoost) and the brain.

Two related vectors are built here:

* ``build_features``      -> outcome features the predictor uses to estimate P(YES).
                            RSS and Reddit are kept as SEPARATE dimensions so the
                            brain can learn source-specific noise instead of one
                            collapsed sentiment score.
* ``build_brain_features`` -> outcome features PLUS the executed trade context
                            (traded side + executable edge). The brain predicts
                            P(trade wins), which is direction-dependent, so it
                            must see whether the bot bought YES or NO.
"""
from __future__ import annotations

import math
from typing import Optional

from tradebot.models import Market, ResearchReport

FEATURE_NAMES = [
    "yes_price", "log_volume", "log_liquidity", "spread", "days_norm",
    "price_move", "sentiment", "log_sources", "dist_from_half", "fav_flag",
    # source-separated research signals
    "rss_sentiment", "reddit_sentiment", "rss_log_sources", "reddit_log_sources",
    "source_quality",
    # web search + hard-fact prior (live crypto price / bookmaker odds)
    "web_sentiment", "web_log_sources", "fact_prob", "fact_confidence",
]
FEATURE_DIM = len(FEATURE_NAMES)

# Trade-context dimensions appended for the brain (not the predictor).
BRAIN_EXTRA_NAMES = ["is_yes", "exec_edge"]
BRAIN_FEATURE_NAMES = FEATURE_NAMES + BRAIN_EXTRA_NAMES
BRAIN_FEATURE_DIM = len(BRAIN_FEATURE_NAMES)

PRICE_IDX = 0
SENTIMENT_IDX = 6


def build_features(
    market: Market, report: Optional[ResearchReport] = None, price_move: float = 0.0
) -> list[float]:
    """Outcome features for the predictor (estimating P(YES))."""
    sentiment = report.sentiment if report else 0.0
    n_sources = report.n_sources if report else 0
    rss_sentiment = report.rss_sentiment if report else 0.0
    reddit_sentiment = report.reddit_sentiment if report else 0.0
    rss_sources = report.rss_sources if report else 0
    reddit_sources = report.reddit_sources if report else 0
    source_quality = report.source_quality if report else 0.0
    web_sentiment = report.web_sentiment if report else 0.0
    web_sources = report.web_sources if report else 0
    fact_prob = report.fact_prob if (report and report.fact_prob is not None) else 0.5
    fact_confidence = report.fact_confidence if report else 0.0
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
        rss_sentiment,
        reddit_sentiment,
        math.log1p(max(0, rss_sources)) / 4.0,
        math.log1p(max(0, reddit_sources)) / 4.0,
        source_quality,
        web_sentiment,
        math.log1p(max(0, web_sources)) / 4.0,
        fact_prob,
        fact_confidence,
    ]


def build_brain_features(
    outcome_features: list[float], is_yes: bool, edge: float
) -> list[float]:
    """Brain input = outcome features + executed trade context (side, edge).

    Keeping these appended (rather than baked into ``build_features``) lets the
    predictor and the brain share the same base vector while the brain still sees
    the direction it actually traded — so YES wins and NO wins are not mixed."""
    return list(outcome_features) + [1.0 if is_yes else 0.0, float(edge)]

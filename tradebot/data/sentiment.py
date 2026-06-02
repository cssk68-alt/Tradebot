"""Sentiment analysis: Claude if available, else VADER, else a deterministic prior."""
from __future__ import annotations

import hashlib


def _vader_score(texts: list[str]):
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        if not texts:
            return None
        an = SentimentIntensityAnalyzer()
        vals = [an.polarity_scores(t)["compound"] for t in texts]
        return sum(vals) / len(vals)
    except Exception:
        return None


def _pseudo(market_question: str) -> float:
    """Deterministic offline prior so the pipeline still produces edges with no data."""
    h = int(hashlib.sha256(market_question.encode()).hexdigest(), 16)
    return ((h % 1000) / 1000.0 - 0.5) * 0.5  # -0.25 .. 0.25


def analyze(texts: list[str], market_question: str, claude=None) -> tuple[float, str]:
    """Return (sentiment in [-1, 1], narrative)."""
    if claude is not None and claude.available and texts:
        res = claude.sentiment(market_question, texts)
        if res is not None:
            return res
    v = _vader_score(texts)
    if v is not None:
        return max(-1.0, min(1.0, v)), f"{len(texts)} sources, avg sentiment {v:+.2f}."
    return _pseudo(market_question), "No external data; using offline prior."

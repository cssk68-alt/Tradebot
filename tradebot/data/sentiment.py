"""Sentiment analysis — the LLM agent when available, otherwise strictly neutral.

HARD-FAIL policy: there is NO deterministic offline prior and NO VADER fallback.
When there is no real external text (RSS/Reddit returned nothing), or when no
LLM scorer is available to read the texts, the sentiment is neutral ``0.0`` so
the pipeline can never fabricate an edge out of thin air (e.g. from a hash of the
question). Real signals only.
"""
from __future__ import annotations

NEUTRAL: tuple[float, str] = (0.0, "No external data; neutral sentiment.")


def analyze(texts: list[str], market_question: str, client=None) -> tuple[float, str]:
    """Return ``(sentiment in [-1, 1], narrative)``.

    Neutral whenever there is no real data to score, so a missing source can never
    turn into a synthetic trading signal."""
    if not texts:
        return NEUTRAL
    if client is not None and client.available:
        res = client.sentiment(market_question, texts)
        if res is not None:
            return res
    # Real texts exist but there is no scorer (no API key): stay neutral rather
    # than guess — we never manufacture sentiment.
    return 0.0, f"{len(texts)} sources fetched; no LLM scorer available — neutral."

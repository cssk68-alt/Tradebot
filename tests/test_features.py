"""Tests for the cross-source agreement feature (ml/features.py).

The brain gets a NUMERIC signal of whether the populated sentiment channels
(RSS / social / web) agree — consensus it can learn to trust, contradiction it
can learn to distrust."""
import pytest

from tradebot.ml.features import (
    BRAIN_FEATURE_DIM,
    FEATURE_DIM,
    FEATURE_NAMES,
    build_features,
)
from tradebot.models import Market, ResearchReport

_AGREE = FEATURE_NAMES.index("sentiment_agreement")


def _market():
    return Market(id="m", question="q", yes_price=0.5)


def test_schema_grew_and_agreement_is_last():
    assert FEATURE_NAMES[-1] == "sentiment_agreement"
    assert len(build_features(_market(), None)) == FEATURE_DIM
    assert BRAIN_FEATURE_DIM == FEATURE_DIM + 2  # + is_yes + exec_edge


def test_agreement_consensus_high():
    # Two channels almost agree (0.6 vs 0.5) -> agreement near 1.
    r = ResearchReport(market_id="m", rss_sentiment=0.6, rss_sources=3,
                        web_sentiment=0.5, web_sources=2)
    assert build_features(_market(), r)[_AGREE] == pytest.approx(0.95)


def test_agreement_conflict_low():
    # Opposite extremes (+0.8 vs -0.8) -> agreement near 0.
    r = ResearchReport(market_id="m", rss_sentiment=0.8, rss_sources=2,
                        reddit_sentiment=-0.8, reddit_sources=2)
    assert build_features(_market(), r)[_AGREE] == pytest.approx(0.2)


def test_agreement_neutral_with_single_channel():
    # Only one populated channel -> nothing to compare -> neutral 0.5.
    r = ResearchReport(market_id="m", rss_sentiment=0.9, rss_sources=4)
    assert build_features(_market(), r)[_AGREE] == 0.5


def test_agreement_neutral_without_report():
    f = build_features(_market(), None)
    assert f[_AGREE] == 0.5

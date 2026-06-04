"""Empirical max-hold recommendation engine (Teil A.2)."""
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.brain.hold_analysis import (
    percentile,
    recommend_max_hold,
    scalp_hold_seconds,
)
from tradebot.models import Mode, Side, Trade


def test_percentile_basic():
    xs = [10, 20, 30, 40, 50]
    assert percentile(xs, 50) == pytest.approx(30)
    assert percentile([], 50) == 0.0
    assert percentile([7], 90) == 7.0


def test_insufficient_data_keeps_current():
    rec = recommend_max_hold(holds_won=[100, 120], holds_lost=[], current=300)
    assert rec["status"] == "insufficient"
    assert rec["direction"] == "keep"
    assert rec["recommended"] == 300


def test_recommends_raise_when_winners_take_longer():
    # Winners mostly mature around 400-500s but the cap is only 200s.
    won = [380, 400, 420, 450, 470, 500, 520, 540]
    rec = recommend_max_hold(won, holds_lost=[120, 90], current=200)
    assert rec["status"] == "ok"
    assert rec["direction"] == "raise"
    assert rec["recommended"] > 200


def test_recommends_lower_when_winners_are_fast():
    won = [40, 45, 50, 55, 60, 62, 65, 70]
    rec = recommend_max_hold(won, holds_lost=[300, 290], current=500)
    assert rec["direction"] == "lower"
    assert 30 <= rec["recommended"] < 500


def test_keep_when_cap_matches_distribution():
    won = [240, 250, 255, 260, 270, 280, 290, 300]
    rec = recommend_max_hold(won, holds_lost=[100], current=round(300 * 1.2))
    assert rec["direction"] == "keep"


def test_scalp_hold_seconds_filters_correctly():
    now = datetime.now(timezone.utc)

    def trade(kind, won, hold_s):
        return Trade(
            market_id="m", token_id="t", question="q", side=Side.BUY, entry_price=0.5,
            size=10, mode=Mode.PAPER, status="resolved", kind=kind, won=won,
            opened_at=now - timedelta(seconds=hold_s), resolved_at=now,
        )

    trades = [
        trade("scalp", True, 120),
        trade("scalp", False, 300),
        trade("resolve", True, 9999),  # not a scalp -> ignored
        Trade(market_id="m", token_id="t", question="q", side=Side.BUY, entry_price=0.5,
              size=10, mode=Mode.PAPER, status="open", kind="scalp"),  # open -> ignored
    ]
    won, lost = scalp_hold_seconds(trades)
    assert won == [120.0]
    assert lost == [300.0]

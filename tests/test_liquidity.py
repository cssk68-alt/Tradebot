"""Spread- and depth-based filters (Teil A.1)."""
from tradebot.models import Market
from tradebot.risk.liquidity import (
    MAX_BOOK_FRACTION,
    depth_too_thin,
    max_order_for_depth,
    passes_spread_filter,
)


def _m(bid=None, ask=None, liq=0.0):
    return Market(id="m", question="q", yes_price=0.5, best_bid=bid, best_ask=ask, liquidity=liq)


def test_spread_gate_when_book_known():
    assert passes_spread_filter(_m(bid=0.49, ask=0.51), max_spread=0.03) is True   # 0.02
    assert passes_spread_filter(_m(bid=0.46, ask=0.54), max_spread=0.03) is False  # 0.08


def test_spread_gate_falls_back_to_liquidity_when_book_unknown():
    # No bid/ask -> decide on the liquidity floor.
    assert passes_spread_filter(_m(liq=5000.0), max_spread=0.03, min_liquidity_floor=1000) is True
    assert passes_spread_filter(_m(liq=100.0), max_spread=0.03, min_liquidity_floor=1000) is False


def test_depth_cap_scales_with_liquidity():
    assert max_order_for_depth(10000.0) == MAX_BOOK_FRACTION * 10000.0
    assert max_order_for_depth(-5.0) == 0.0


def test_depth_too_thin_for_tiny_books():
    assert depth_too_thin(5.0) is True       # 0.1 * 5 = 0.5 < $1
    assert depth_too_thin(50.0) is False     # 0.1 * 50 = 5 >= $1

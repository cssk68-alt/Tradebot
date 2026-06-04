"""Spread- and depth-based market-quality filters (Punkt 1 / Teil A.1).

Replaces the old absolute USDC thresholds (``min_liquidity`` / ``min_volume_24h``
as hard scan gates) with microstructure-aware checks:

* ``passes_spread_filter`` — the bid/ask spread is the dominant round-trip cost on
  Polymarket, so the scan gates on it directly. When the order book is not yet
  published (no best_bid/best_ask) it falls back to a minimum-liquidity floor so a
  market we cannot even price is never traded.
* ``max_order_for_depth`` / ``depth_too_thin`` — order-book DEPTH checked against
  the planned order size at sizing time (risk/kelly): never plan an order larger
  than a small fraction of the visible liquidity, and reject outright when the
  book is too thin to place even a $1 order.
"""
from __future__ import annotations

# Never plan a single order larger than this fraction of the market's visible
# liquidity (USDC). Bounds slippage and keeps the bot from being the whole book.
MAX_BOOK_FRACTION = 0.1


def passes_spread_filter(market, max_spread: float, min_liquidity_floor: float = 0.0) -> bool:
    """A.1 market-quality gate.

    Book known  -> pass iff ``spread <= max_spread`` (the real round-trip cost).
    Book unknown -> fall back to a liquidity sanity floor (never trade a market we
                    cannot price)."""
    if market.best_bid is not None and market.best_ask is not None:
        return market.spread <= max(0.0, float(max_spread))
    return market.liquidity >= max(0.0, float(min_liquidity_floor))


def max_order_for_depth(liquidity: float) -> float:
    """Largest order notional (USDC) we allow against this visible depth."""
    return MAX_BOOK_FRACTION * max(0.0, float(liquidity))


def depth_too_thin(liquidity: float, min_notional: float = 1.0) -> bool:
    """True if the book is too thin to place even a ``min_notional`` order."""
    return max_order_for_depth(liquidity) < float(min_notional)

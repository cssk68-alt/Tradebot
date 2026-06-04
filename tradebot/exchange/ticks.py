"""Tick-size awareness for Polymarket CLOB prices (Punkt 7 / Teil B.5).

Polymarket order prices live on a discrete grid; an order whose price is not a
valid multiple of the market's tick size is rejected by the exchange. The grid
is 1 cent ($0.01) across the normal range and a finer 0.1 cent ($0.001) near the
extremes, where prices cluster and a 1-cent grid would be too coarse.

This module is the single source of truth for that grid:

* ``get_tick_size(price)`` — the valid tick at a given price.
* ``round_to_tick(price)`` — snap a price to the nearest valid tick (round-half-up).
* ``targets_collapse(...)`` — guard: after rounding, has the scalp's take-profit or
  stop-loss exit price collapsed onto the entry (so the trigger can never fire)?
  Such a trade must be BLOCKED — it can only ever lose the spread.
"""
from __future__ import annotations

# Grid breakpoints. Polymarket uses a 1c grid in the middle of the book and a
# finer 0.1c grid near 0/1 where order prices bunch up.
_EXTREME_LOW = 0.05
_EXTREME_HIGH = 0.95
_TICK_NORMAL = 0.01
_TICK_FINE = 0.001


def get_tick_size(price: float) -> float:
    """Valid tick size at ``price`` per the Polymarket CLOB grid.

    1 cent in the normal range; 0.1 cent near the extremes (``<=0.05`` or
    ``>=0.95``) where prices cluster. Clamped to [0, 1] defensively."""
    p = max(0.0, min(1.0, float(price)))
    return _TICK_FINE if (p <= _EXTREME_LOW or p >= _EXTREME_HIGH) else _TICK_NORMAL


def round_to_tick(price: float, tick: float | None = None) -> float:
    """Snap ``price`` to the nearest valid tick (round-half-up).

    ``tick`` defaults to ``get_tick_size(price)``. The result is re-rounded to 6
    decimals to clear binary-float fuzz (e.g. 0.07000000000001)."""
    if tick is None:
        tick = get_tick_size(price)
    if tick <= 0:
        return round(float(price), 6)
    return round(round(float(price) / tick) * tick, 6)


def targets_collapse(entry_price: float, take_profit: float, stop_loss: float) -> bool:
    """True if the scalp's take-profit OR stop-loss exit collapses onto the entry
    after tick rounding — i.e. the configured move is smaller than half a tick, so
    the rounded target price is not strictly beyond the (rounded) entry and the
    trigger could never fire cleanly on the grid.

    A trade for which this is True can only ever pay the spread, so the caller
    must block it (Teil B.5: "Blockiere Trades, bei denen TP/SL nach Rounding
    kollabieren")."""
    tick = get_tick_size(entry_price)
    entry = round_to_tick(entry_price, tick)
    tp_price = round_to_tick(entry + max(0.0, take_profit), tick)
    sl_price = round_to_tick(entry - max(0.0, stop_loss), tick)
    return tp_price <= entry or sl_price >= entry

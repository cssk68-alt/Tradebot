"""Maker-first vs taker execution decision (Punkt 5 / Teil B.3).

A pure policy function so the choice is testable in isolation from the live CLOB:

* When the edge is thin we cannot afford to wait for a passive fill — take
  liquidity now (TAKER, a marketable order).
* When the edge is comfortably large we can rest a passive bid one tick inside the
  reference price (MAKER) for a short window. On Polymarket the maker side pays
  ZERO fee and saves the spread; the "one tick inside" keeps fill odds high
  ("nah am Taker"). If it does not fill within ``maker_timeout_seconds`` the live
  exchange cancels it and falls back to a taker order.

The chosen style is recorded on the Trade (``exec_style``) and written to the
trade log, as required.
"""
from __future__ import annotations

from dataclasses import dataclass

from tradebot.exchange.ticks import get_tick_size, round_to_tick


@dataclass
class ExecPlan:
    style: str  # "maker" | "taker"
    limit_price: float  # tick-valid price to submit
    reason: str


def decide_execution_style(side_price: float, edge: float, spread: float, settings) -> ExecPlan:
    """Decide how to execute a BUY of the traded side priced at ``side_price``.

    ``edge`` is the executable edge on the Signal; ``spread`` the market's bid/ask
    spread (informational). Knobs (all via ``getattr`` so minimal settings stubs
    still work): ``maker_first`` (off-switch, default True), ``maker_min_edge``
    (edge below which we always take, default 0.03), ``maker_timeout_seconds``."""
    tick = get_tick_size(side_price)
    taker_price = round_to_tick(side_price, tick)

    maker_first = bool(getattr(settings, "maker_first", True))
    if not maker_first:
        return ExecPlan("taker", taker_price, "maker-first deaktiviert")

    min_edge = float(getattr(settings, "maker_min_edge", 0.03))
    if edge < min_edge:
        return ExecPlan(
            "taker", taker_price,
            f"Edge {edge:.3f} < maker_min_edge {min_edge:.3f}: sofort Liquiditaet nehmen",
        )

    # Rest one tick inside the reference price (≈ mid − 1 Tick): a passive bid that
    # is still close to the touch, so fill odds stay high while we save the spread.
    limit = round_to_tick(side_price - tick, tick)
    if limit <= 0.0:
        return ExecPlan("taker", taker_price, "Preis zu niedrig fuer einen Maker-Tick")

    timeout = int(float(getattr(settings, "maker_timeout_seconds", 60.0)))
    return ExecPlan(
        "maker", limit,
        f"Edge {edge:.3f} >= {min_edge:.3f}: bis {timeout}s als Maker bei {limit:.3f} ruhen",
    )

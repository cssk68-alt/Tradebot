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
from datetime import datetime, timedelta, timezone

from tradebot.exchange.ticks import get_tick_size, round_to_tick


@dataclass
class ExecPlan:
    style: str  # "maker" | "taker"
    limit_price: float  # tick-valid price to submit
    reason: str


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resolve_maker_fill(
    limit_price: float,
    is_yes: bool,
    series: list[tuple[datetime, float, float]],
    posted_ts: datetime,
    deadline_ts: datetime,
    now: datetime,
    late_factor: float = 2.0,
) -> dict:
    """Did a resting PAPER maker BUY at ``limit_price`` fill? Decided from the REAL
    observed price path — never on faith (mirrors the live CLOB, which only fills
    when the book actually trades to the order).

    A BUY limit rests one tick BELOW the touch and fills when the traded side's price
    trades DOWN to it. ``series`` is ``[(ts, yes_price, spread)]`` oldest-first
    (snapshots in the resting window plus the current live price as the freshest
    point); ``is_yes`` selects the traded side (NO price = ``1 - yes_price``).

    Coarse-sampling reality: paper snapshots arrive ~once per cycle, so the window
    often has no observation strictly inside it. Like ``settle_scalp_path``'s
    ``max_settle_factor``, we therefore let the FIRST sample at/after the deadline
    (up to ``deadline + window*(late_factor-1)``) stand for the window's end: if the
    side reached the bid by then it counts as filled, otherwise as missed. A sample
    far past that tolerance is ignored (never fabricates a fill for a stale tick).

    Returns ``status`` in:
      * ``"filled"``  — side reached ``<= limit`` in-window or by the window-end sample;
                        also ``fill_ts`` and ``spread`` (round-trip cost at the fill).
      * ``"missed"``  — window closed without the price reaching the bid (caller takes).
      * ``"pending"`` — deadline not yet reached and no touch yet (wait for more ticks).
    """
    posted, deadline, now = _utc(posted_ts), _utc(deadline_ts), _utc(now)
    window = max(0.0, (deadline - posted).total_seconds())
    cutoff = deadline + timedelta(seconds=window * max(0.0, late_factor - 1.0))

    for ts, yes_price, spread in series:
        ts = _utc(ts)
        if ts <= posted:
            continue
        if ts > cutoff:
            break  # too stale to represent this resting window
        side = yes_price if is_yes else 1.0 - yes_price
        hit = side <= limit_price + 1e-9
        if ts <= deadline:
            if hit:
                return {"status": "filled", "fill_ts": ts, "spread": spread}
            continue  # still in-window — the price may yet dip to the bid
        # First sample past the deadline (within tolerance) decides the window's end.
        if hit:
            return {"status": "filled", "fill_ts": ts, "spread": spread}
        return {"status": "missed"}

    if now < deadline:
        return {"status": "pending"}
    return {"status": "missed"}


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

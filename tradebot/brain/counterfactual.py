"""Replay a counterfactual scalp over the REAL price path (Problem 1).

A counterfactual is a trade the bot did NOT make (a vetoed/sized-out signal, or
the opposite/mirror side of a real trade). To learn from it WITHOUT inventing an
outcome, we replay the position over the market's real recorded price series
(``store.snapshots``) using the exact same exit logic as a live scalp
(``orchestrator._scalp_trigger`` + ``PaperExchange.close`` PnL): take-profit,
stop-loss or max-hold timeout. Only the position is hypothetical — every price is
real, and the holding window is our short scalp window (NOT hold-to-resolution).

``settle_scalp_path`` is pure (no DB/IO), so it is trivially testable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def settle_scalp_path(
    entry_price: float,
    is_yes: bool,
    series: list[tuple[datetime, float, float]],
    entry_ts: datetime,
    take_profit: float,
    stop_loss: float,
    max_hold: float,
    now: datetime,
    spread_floor: float = 0.01,
    size: float = 1.0,
) -> dict:
    """Replay one scalp over ``series`` ([(ts, yes_price, spread)] oldest-first).

    Returns a dict with ``status`` in {"pending","settled","expired"}; when settled
    also ``exit_price``, ``pnl``, ``won`` and ``exit_reason`` ("take_profit" |
    "stop_loss" | "time"). ``pending`` while the window has not elapsed and no
    trigger fired; ``expired`` if the window elapsed but no price data exists."""
    entry_ts = _utc(entry_ts)
    now = _utc(now)

    def _settled(cur: float, spread: float, reason: str) -> dict:
        cost = max(spread, spread_floor)
        pnl = size * (cur - entry_price - cost)
        return {
            "status": "settled", "exit_price": round(cur, 4), "pnl": pnl,
            "won": pnl > 0, "exit_reason": reason,
        }

    last: Optional[tuple[float, float]] = None  # (side_price, spread) of last seen tick
    for ts, yes_price, spread in series:
        ts = _utc(ts)
        if ts <= entry_ts:
            continue
        cur = yes_price if is_yes else 1.0 - yes_price
        held = (ts - entry_ts).total_seconds()
        move = cur - entry_price
        if move >= take_profit:
            return _settled(cur, spread, "take_profit")
        if move <= -stop_loss:
            return _settled(cur, spread, "stop_loss")
        if held >= max_hold:
            return _settled(cur, spread, "time")
        last = (cur, spread)

    # No trigger fired across the available series.
    window_elapsed = (now - entry_ts).total_seconds() >= max_hold
    if not window_elapsed:
        return {"status": "pending"}
    # Window is over but the market stopped being scanned before max_hold — settle
    # at the last real price we did see (best available "time" exit), else expire.
    if last is not None:
        return _settled(last[0], last[1], "time")
    return {"status": "expired"}

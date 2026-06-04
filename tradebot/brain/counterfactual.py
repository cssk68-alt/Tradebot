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
    max_settle_factor: float = 2.0,
) -> dict:
    """Replay one scalp over ``series`` ([(ts, yes_price, spread)] oldest-first).

    REAL VALUES ONLY — every settled outcome comes from a real observed price at a
    real observed time; nothing is interpolated or invented. We settle exclusively
    on (a) a real take-profit / stop-loss crossing within the hold window, or
    (b) the first real snapshot at/after ``max_hold`` AND no later than
    ``max_hold * max_settle_factor`` (a timely "time" exit). If the price path has
    a gap so we never observe the window's end — or the first post-window tick is
    too late to represent this short scalp — we do NOT guess an outcome: the
    counterfactual stays ``pending`` (window still open) or becomes ``expired``
    (unknown, not learned from).

    Returns ``status`` in {"pending","settled","expired"}; when settled also
    ``exit_price``, ``pnl``, ``won`` and ``exit_reason``."""
    entry_ts = _utc(entry_ts)
    now = _utc(now)
    cutoff = max_hold * max_settle_factor  # ignore ticks observed far past the window

    def _settled(cur: float, spread: float, reason: str) -> dict:
        cost = max(spread, spread_floor)
        pnl = size * (cur - entry_price - cost)
        return {
            "status": "settled", "exit_price": round(cur, 4), "pnl": pnl,
            "won": pnl > 0, "exit_reason": reason,
        }

    for ts, yes_price, spread in series:
        ts = _utc(ts)
        if ts <= entry_ts:
            continue
        held = (ts - entry_ts).total_seconds()
        if held > cutoff:
            break  # stale tick (gap then reappear) — not a real exit for this window
        cur = yes_price if is_yes else 1.0 - yes_price
        move = cur - entry_price
        if move >= take_profit:
            return _settled(cur, spread, "take_profit")
        if move <= -stop_loss:
            return _settled(cur, spread, "stop_loss")
        if held >= max_hold:
            return _settled(cur, spread, "time")

    # No real qualifying exit was observed.
    if (now - entry_ts).total_seconds() < max_hold:
        return {"status": "pending"}  # window still open — wait for more real ticks
    return {"status": "expired"}      # window over but outcome never observed — don't guess

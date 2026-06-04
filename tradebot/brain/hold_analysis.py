"""Empirical max-hold recommendation engine (Punkt 2 / Teil A.2).

The brain studies how long WINNING scalp trades actually took to mature and turns
that into a *recommendation* for ``max_hold_seconds`` — it never changes the
setting itself (that stays a slider the operator owns). The idea: a scalp wins by
hitting its take-profit, so the hold-time distribution of winners tells us how
long a profitable move needs. Set the cap too low and you cut winners off before
they mature; set it too high and capital rots in stale trades.

We report the 50th / 75th / 95th percentile of winner hold-times (the three the
spec asks the brain to weigh) and recommend ≈ P75 × margin: long enough to let
roughly three-quarters of winners mature, with headroom, but no longer.
"""
from __future__ import annotations

from datetime import timezone
from typing import Optional

# Need at least this many resolved scalp trades before a recommendation is
# trustworthy (mirrors the brain's own ">=8 to learn" bar).
_MIN_SAMPLES = 8
# Headroom on top of P75 so we do not clip winners sitting just past it.
_MARGIN = 1.2
# Clamp to the same range the UI slider exposes.
_FLOOR, _CEIL = 30.0, 600.0


def scalp_hold_seconds(trades, mode=None) -> tuple[list[float], list[float]]:
    """Split resolved SCALP trades into (winner_holds, loser_holds) in seconds.

    Trades that are not scalps, not resolved, voids (won is None), or (optionally)
    not in ``mode`` are ignored. Naive timestamps are treated as UTC."""
    won: list[float] = []
    lost: list[float] = []
    for t in trades:
        if getattr(t, "kind", "") != "scalp" or t.won is None or t.resolved_at is None:
            continue
        if mode is not None and t.mode != mode:
            continue
        opened = t.opened_at if t.opened_at.tzinfo else t.opened_at.replace(tzinfo=timezone.utc)
        resolved = t.resolved_at if t.resolved_at.tzinfo else t.resolved_at.replace(tzinfo=timezone.utc)
        held = (resolved - opened).total_seconds()
        if held < 0:
            continue
        (won if t.won else lost).append(held)
    return won, lost


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (``p`` in [0, 100]); 0.0 for empty input."""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    rank = (p / 100.0) * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return float(xs[lo] + (xs[hi] - xs[lo]) * frac)


def recommend_max_hold(
    holds_won: list[float],
    holds_lost: list[float],
    current: float,
) -> dict:
    """Build a max-hold recommendation from winner/loser hold-times (seconds).

    Returns a dict that is safe to embed in the dashboard state and to log:
    ``status`` is ``"insufficient"`` until enough data, else ``"ok"`` with
    percentiles, the recommended value and a ``direction`` (raise/lower/keep)."""
    n = len(holds_won) + len(holds_lost)
    current = float(current)
    if n < _MIN_SAMPLES or not holds_won:
        return {
            "status": "insufficient",
            "n": n,
            "current": round(current, 1),
            "recommended": round(current, 1),
            "direction": "keep",
            "message": (
                f"Zu wenig abgeschlossene Scalp-Trades fuer eine Haltedauer-Empfehlung "
                f"({n}/{_MIN_SAMPLES}, davon {len(holds_won)} Gewinner)."
            ),
        }

    p50 = percentile(holds_won, 50)
    p75 = percentile(holds_won, 75)
    p95 = percentile(holds_won, 95)
    recommended = max(_FLOOR, min(_CEIL, round(p75 * _MARGIN)))

    # Direction relative to the current setting (10% dead-band = "keep").
    if recommended > current * 1.10:
        direction = "raise"
        msg = (
            f"Gewinner brauchen oft laenger (P75 {p75:.0f}s) als das aktuelle Limit "
            f"({current:.0f}s) — erwaege Erhoehung auf ~{recommended:.0f}s, sonst werden "
            f"reifende Gewinner zu frueh geschlossen."
        )
    elif recommended < current * 0.90:
        direction = "lower"
        msg = (
            f"Gewinner reifen schnell (P75 {p75:.0f}s); das aktuelle Limit ({current:.0f}s) "
            f"bindet Kapital unnoetig lange — erwaege Senkung auf ~{recommended:.0f}s."
        )
    else:
        direction = "keep"
        msg = (
            f"Aktuelles Limit ({current:.0f}s) passt gut zur Gewinner-Haltedauer "
            f"(P75 {p75:.0f}s)."
        )

    return {
        "status": "ok",
        "n": n,
        "n_won": len(holds_won),
        "n_lost": len(holds_lost),
        "p50": round(p50, 1),
        "p75": round(p75, 1),
        "p95": round(p95, 1),
        "current": round(current, 1),
        "recommended": round(float(recommended), 1),
        "direction": direction,
        "message": msg,
    }

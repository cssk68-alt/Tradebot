"""Daily-loss / loss-streak circuit breaker (Punkt 4 / Teil B.2).

A pure predicate over already-computed numbers, so it is trivially testable and
has no DB/IO coupling. The orchestrator feeds it today's realized PnL, the live
bankroll and the current consecutive-loss streak; when it trips, the run stops
opening NEW trades. Open positions are NEVER abandoned — the caller winds them
down gracefully (see server ``_wind_down``).

Both limits are opt-in: a value of 0 (or below) disables that arm.
"""
from __future__ import annotations

from typing import Optional


def circuit_breaker_reason(
    realized_today: float,
    bankroll: float,
    consecutive_losses: int,
    settings,
) -> Optional[str]:
    """Return a human-readable trip reason, or ``None`` if trading may continue.

    * Daily loss: trips when today's realized PnL has fallen to ``-max_daily_loss_pct``
      of the (live) bankroll.
    * Loss streak: trips when ``consecutive_losses`` reaches ``max_consecutive_losses``.
    """
    # Globaler An/Aus-Schalter (UI-Toggle): wenn deaktiviert, kein Auslösen
    if not bool(getattr(settings, "circuit_breaker_enabled", True)):
        return None

    max_loss_pct = float(getattr(settings, "max_daily_loss_pct", 0.0) or 0.0)
    max_streak = int(getattr(settings, "max_consecutive_losses", 0) or 0)

    if max_loss_pct > 0.0 and bankroll > 0.0:
        limit = -max_loss_pct * bankroll
        if realized_today <= limit:
            return (
                f"Tagesverlust-Limit erreicht: {realized_today:+.2f} "
                f"<= {limit:.2f} (-{max_loss_pct * 100:.0f}% von {bankroll:.0f})"
            )

    if max_streak > 0 and consecutive_losses >= max_streak:
        return (
            f"Verlust-Streak-Limit erreicht: {consecutive_losses} Verluste in Folge "
            f">= {max_streak}"
        )

    return None

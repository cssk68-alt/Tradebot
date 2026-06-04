"""Daily-loss / loss-streak circuit breaker (Teil B.2) — pure logic + store queries."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.models import Mode, Side, Trade
from tradebot.risk.circuit_breaker import circuit_breaker_reason
from tradebot.store.db import Store


class _S:
    max_daily_loss_pct = 0.05
    max_consecutive_losses = 5


def test_breaker_quiet_within_limits():
    assert circuit_breaker_reason(-10.0, 1000.0, 2, _S()) is None


def test_breaker_trips_on_daily_loss():
    # -60 on a 1000 bankroll is -6% <= -5% limit.
    reason = circuit_breaker_reason(-60.0, 1000.0, 0, _S())
    assert reason and "Tagesverlust" in reason


def test_breaker_trips_on_loss_streak():
    reason = circuit_breaker_reason(0.0, 1000.0, 5, _S())
    assert reason and "Streak" in reason


def test_breaker_disabled_when_limits_zero():
    class Off:
        max_daily_loss_pct = 0.0
        max_consecutive_losses = 0

    assert circuit_breaker_reason(-999.0, 1000.0, 99, Off()) is None


# --- store-backed inputs -----------------------------------------------------

def _resolved(store, won, pnl, when, mode=Mode.PAPER):
    t = Trade(
        market_id="m", token_id="t", question="q", side=Side.BUY, entry_price=0.5,
        size=10, mode=mode, status="resolved", kind="scalp", won=won, pnl=pnl,
        opened_at=when - timedelta(seconds=60), resolved_at=when,
    )
    store.save_trade(t)
    return t


def test_realized_pnl_today_excludes_yesterday(tmp_path):
    s = Store(Path(tmp_path) / "t.db")
    now = datetime.now(timezone.utc)
    _resolved(s, True, 5.0, now)                       # today
    _resolved(s, False, -3.0, now - timedelta(days=1))  # yesterday
    assert abs(s.realized_pnl_today(Mode.PAPER) - 5.0) < 1e-9


def test_consecutive_losses_counts_until_first_win(tmp_path):
    s = Store(Path(tmp_path) / "t.db")
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    _resolved(s, True, 4.0, base + timedelta(minutes=1))   # oldest: win
    _resolved(s, False, -2.0, base + timedelta(minutes=2))
    _resolved(s, False, -2.0, base + timedelta(minutes=3))
    _resolved(s, False, -2.0, base + timedelta(minutes=4))  # newest: 3 losses in a row
    assert s.consecutive_losses(Mode.PAPER) == 3


def test_consecutive_losses_reset_by_recent_win(tmp_path):
    s = Store(Path(tmp_path) / "t.db")
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    _resolved(s, False, -2.0, base + timedelta(minutes=1))
    _resolved(s, True, 4.0, base + timedelta(minutes=2))    # newest is a win
    assert s.consecutive_losses(Mode.PAPER) == 0

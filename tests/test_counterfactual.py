"""Pure counterfactual scalp replay over the real price path (Problem 1)."""
from datetime import datetime, timedelta, timezone

from tradebot.brain.counterfactual import settle_scalp_path

BASE = datetime(2026, 6, 4, tzinfo=timezone.utc)


def _ts(secs):
    return BASE + timedelta(seconds=secs)


def test_take_profit_exit_wins():
    # YES entry 0.50, price rises to 0.53 (+0.03 >= TP 0.02) within the window.
    r = settle_scalp_path(0.50, True, [(_ts(60), 0.53, 0.005)], BASE,
                          take_profit=0.02, stop_loss=0.03, max_hold=300, now=_ts(120))
    assert r["status"] == "settled" and r["exit_reason"] == "take_profit" and r["won"] is True


def test_stop_loss_exit_loses():
    r = settle_scalp_path(0.50, True, [(_ts(60), 0.46, 0.005)], BASE,
                          take_profit=0.02, stop_loss=0.03, max_hold=300, now=_ts(120))
    assert r["exit_reason"] == "stop_loss" and r["won"] is False


def test_time_exit_when_no_trigger():
    series = [(_ts(60), 0.505, 0.005), (_ts(360), 0.505, 0.005)]  # held>=300 at 2nd tick
    r = settle_scalp_path(0.50, True, series, BASE,
                          take_profit=0.05, stop_loss=0.05, max_hold=300, now=_ts(400))
    assert r["status"] == "settled" and r["exit_reason"] == "time"
    # flat price, spread floored to 0.01 -> small loss
    assert r["won"] is False


def test_pending_while_window_open():
    r = settle_scalp_path(0.50, True, [(_ts(60), 0.505, 0.005)], BASE,
                          take_profit=0.05, stop_loss=0.05, max_hold=300, now=_ts(120))
    assert r["status"] == "pending"


def test_expired_when_no_price_data():
    r = settle_scalp_path(0.50, True, [], BASE,
                          take_profit=0.05, stop_loss=0.05, max_hold=300, now=_ts(400))
    assert r["status"] == "expired"


def test_mirror_no_side_uses_complement_price():
    # NO entry at 0.50; YES drops 0.50->0.45 => NO price 0.50->0.55 (+0.05 >= TP) -> win.
    r = settle_scalp_path(0.50, False, [(_ts(60), 0.45, 0.005)], BASE,
                          take_profit=0.02, stop_loss=0.03, max_hold=300, now=_ts(120))
    assert r["exit_reason"] == "take_profit" and r["won"] is True


def test_spread_cost_is_floored():
    series = [(_ts(60), 0.50, 0.0), (_ts(360), 0.50, 0.0)]  # flat, no observed spread
    r = settle_scalp_path(0.50, True, series, BASE, take_profit=0.05, stop_loss=0.05,
                          max_hold=300, now=_ts(400), spread_floor=0.01, size=1.0)
    assert abs(r["pnl"] - (-0.01)) < 1e-9  # pays the floored 1c round-trip

"""Dashboard state.json carries the new A.2/B.2/B.3 fields."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.brain.feedback import Brain
from tradebot.config import Settings
from tradebot.dashboard import build_state
from tradebot.log import get_logger
from tradebot.models import Mode, Side, Trade
from tradebot.store.db import Store


def _store(tmp):
    s = Settings(db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz")
    return s, Store(s.db_path)


def _scalp(won, pnl, hold_s, when, exec_style="maker"):
    return Trade(
        market_id="m", token_id="t", question="q", side=Side.BUY, entry_price=0.5,
        size=10, mode=Mode.PAPER, status="resolved", kind="scalp", won=won, pnl=pnl,
        exec_style=exec_style, opened_at=when - timedelta(seconds=hold_s), resolved_at=when,
    )


def test_state_has_new_fields(tmp_path):
    s, store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    for i in range(8):
        store.save_trade(_scalp(True, 2.0, 120 + i, now - timedelta(minutes=i)))
    store.save_trade(_scalp(False, -2.0, 300, now))

    state = build_state(store, s, Brain(s.brain_path, get_logger("t")))

    assert "hold_recommendation" in state and state["hold_recommendation"]["status"] in ("ok", "insufficient")
    assert "circuit_breaker" in state and "tripped" in state["circuit_breaker"]
    # exec_style is surfaced per trade
    assert all("exec_style" in t for t in state["resolved_trades"])
    assert any(t["exec_style"] == "maker" for t in state["resolved_trades"])
    # new config knobs are echoed for the UI
    assert "max_spread" in state["config"]


def test_circuit_breaker_status_trips_in_state(tmp_path):
    s, store = _store(tmp_path)
    s.max_consecutive_losses = 2
    now = datetime.now(timezone.utc)
    store.save_trade(_scalp(False, -5.0, 100, now - timedelta(minutes=2)))
    store.save_trade(_scalp(False, -5.0, 100, now))
    state = build_state(store, s, Brain(s.brain_path, get_logger("t")))
    assert state["circuit_breaker"]["tripped"] is True
    assert state["circuit_breaker"]["consecutive_losses"] == 2

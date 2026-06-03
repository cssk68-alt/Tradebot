from pathlib import Path

from tradebot.backtest import run_backtest
from tradebot.config import Settings


def _settings(tmp):
    return Settings(
        db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz",
        bankroll=1000.0, edge_threshold=0.05,
    )


def test_backtest_is_deterministic(tmp_path):
    s = _settings(tmp_path)
    assert run_backtest(s, n=200, seed=3) == run_backtest(s, n=200, seed=3)


def test_backtest_runs_and_trades(tmp_path):
    r = run_backtest(_settings(tmp_path), n=300, seed=1, signal_strength=0.6)
    assert r.n_trades > 0
    assert 0.0 <= r.win_rate <= 1.0
    assert r.n_trades == r.wins + r.losses
    assert len(r.equity_curve) >= 1


def test_backtest_profitable_with_real_signal(tmp_path):
    # When sentiment genuinely carries information, expectancy is positive.
    r = run_backtest(_settings(tmp_path), n=1000, seed=2, signal_strength=0.85)
    assert r.end_bankroll > r.start_bankroll
    assert r.roi > 0

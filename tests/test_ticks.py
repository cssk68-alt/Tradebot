"""Tick-size awareness (Teil B.5)."""
import pytest

from tradebot.exchange.ticks import get_tick_size, round_to_tick, targets_collapse


def test_tick_size_grid():
    assert get_tick_size(0.50) == 0.01
    assert get_tick_size(0.10) == 0.01
    assert get_tick_size(0.90) == 0.01
    # Near the extremes the grid is finer.
    assert get_tick_size(0.03) == 0.001
    assert get_tick_size(0.97) == 0.001
    # Clamped, never crashes on out-of-range input.
    assert get_tick_size(-1.0) == 0.001
    assert get_tick_size(2.0) == 0.001


def test_round_to_tick_snaps_to_grid():
    assert round_to_tick(0.6149) == pytest.approx(0.61)
    assert round_to_tick(0.6151) == pytest.approx(0.62)
    assert round_to_tick(0.623) == pytest.approx(0.62)
    # Fine grid near the extreme.
    assert round_to_tick(0.0234) == pytest.approx(0.023)
    # Explicit tick overrides the auto grid.
    assert round_to_tick(0.617, 0.05) == pytest.approx(0.60)


def test_targets_do_not_collapse_for_normal_settings():
    # 2c TP / 3c SL on a 1c grid at 0.50 -> both clear the entry comfortably.
    assert targets_collapse(0.50, 0.02, 0.03) is False


def test_targets_collapse_when_move_below_half_tick():
    # TP of 0.4c on a 1c grid rounds back onto the entry -> collapse -> block.
    assert targets_collapse(0.50, 0.004, 0.03) is True
    # A zero stop-loss collapses too.
    assert targets_collapse(0.50, 0.02, 0.0) is True

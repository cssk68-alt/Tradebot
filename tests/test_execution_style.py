"""Maker-first vs taker decision (Teil B.3)."""
from tradebot.exchange.execution_style import decide_execution_style


class _S:
    maker_first = True
    maker_min_edge = 0.03
    maker_timeout_seconds = 60.0


def test_taker_when_edge_below_threshold():
    plan = decide_execution_style(side_price=0.60, edge=0.01, spread=0.02, settings=_S())
    assert plan.style == "taker"
    assert plan.limit_price == 0.60  # tick-snapped reference price


def test_maker_when_edge_large_rests_one_tick_inside():
    plan = decide_execution_style(side_price=0.60, edge=0.08, spread=0.02, settings=_S())
    assert plan.style == "maker"
    # one 1c tick inside the 0.60 reference
    assert plan.limit_price == 0.59
    assert "Maker" in plan.reason or "ruhen" in plan.reason


def test_maker_first_off_forces_taker():
    class Off(_S):
        maker_first = False

    plan = decide_execution_style(side_price=0.60, edge=0.50, spread=0.02, settings=Off())
    assert plan.style == "taker"


def test_minimal_settings_stub_defaults_work():
    # An object with no maker_* attrs falls back to sane getattr defaults.
    plan = decide_execution_style(side_price=0.40, edge=0.10, spread=0.01, settings=object())
    assert plan.style == "maker"
    assert plan.limit_price == 0.39

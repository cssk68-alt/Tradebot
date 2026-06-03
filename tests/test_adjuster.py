"""Tests for the aggressiveness Risk-Adjuster (risk/adjuster.py).

Verifies that the single knob loosens the gates monotonically, respects the hard
safety floors, and that the loosening actually flips a borderline trade from
vetoed -> approved in kelly.size_position — without the knob being set, behaviour
is byte-identical to before (the maths is untouched)."""
from tradebot.risk.adjuster import (
    CONFIDENCE_FLOOR,
    EDGE_FLOOR,
    MIN_SIZE_FACTOR,
    risk_profile,
)
from tradebot.risk.kelly import size_position
from tradebot.models import Side, Signal


class _Settings:
    confidence_threshold = 0.6
    brain_veto_threshold = 0.35
    edge_threshold = 0.05
    kelly_fraction = 0.25
    max_trade_pct = 0.05
    max_exposure_pct = 0.5

    def __init__(self, aggressiveness=0.0):
        self.aggressiveness = aggressiveness


def _sig(edge=0.1, price=0.5, conf=0.55, brain=0.2):
    return Signal(
        market_id="m", token_id="t", question="q", side=Side.BUY, market_price=price,
        true_prob=price + edge, edge=edge, confidence=conf, brain_score=brain,
    )


def test_conservative_is_unchanged():
    p = risk_profile(_Settings(0.0))
    assert p.brain_veto_threshold == 0.35
    assert p.confidence_threshold == 0.6
    assert p.edge_threshold == 0.05
    assert p.size_factor == 1.0
    assert p.label == "conservative"
    assert p.appetite_prompt == ""  # prompt identical to today's default


def test_missing_knob_defaults_to_conservative():
    class Bare:
        confidence_threshold = 0.6
        brain_veto_threshold = 0.35
    p = risk_profile(Bare())  # no `aggressiveness` attribute at all
    assert p.aggressiveness == 0.0 and p.size_factor == 1.0


def test_full_aggression_hits_floors():
    p = risk_profile(_Settings(1.0))
    assert p.brain_veto_threshold == 0.0          # brain score gate fully off
    assert p.confidence_threshold == CONFIDENCE_FLOOR
    assert p.edge_threshold == EDGE_FLOOR
    assert p.size_factor == MIN_SIZE_FACTOR       # smaller stake per trade
    assert p.label == "aggressive"
    assert "AGGRESSIVE" in p.appetite_prompt


def test_monotonic_loosening():
    lo, hi = risk_profile(_Settings(0.2)), risk_profile(_Settings(0.8))
    assert hi.brain_veto_threshold < lo.brain_veto_threshold
    assert hi.confidence_threshold <= lo.confidence_threshold
    assert hi.size_factor < lo.size_factor


def test_aggression_flips_a_borderline_trade():
    # brain 0.2 < 0.35 veto AND confidence 0.55 < 0.6 -> blocked when conservative.
    assert not size_position(_sig(), 1000, _Settings(0.0)).approved
    # Crank aggressiveness: veto threshold -> 0 and confidence bar -> 0.5, so it
    # now clears both gates and gets sized.
    d = size_position(_sig(), 1000, _Settings(1.0))
    assert d.approved and d.size > 0


def test_positive_edge_floor_still_required():
    # Even at full aggression a non-positive Kelly edge is never approved.
    assert not size_position(_sig(edge=0.0), 1000, _Settings(1.0)).approved

from tradebot.models import Side, Signal
from tradebot.risk.kelly import kelly_fraction, size_position


class _Settings:
    confidence_threshold = 0.6
    brain_veto_threshold = 0.35
    kelly_fraction = 0.25
    max_trade_pct = 0.05
    max_exposure_pct = 0.5


def _sig(edge=0.1, price=0.5, conf=0.9, brain=0.8):
    return Signal(
        market_id="m", token_id="t", question="q", side=Side.BUY, market_price=price,
        true_prob=price + edge, edge=edge, confidence=conf, brain_score=brain,
    )


def test_kelly_zero_or_negative_edge():
    assert kelly_fraction(0.0, 0.5) == 0.0
    assert kelly_fraction(-0.1, 0.5) == 0.0


def test_kelly_formula_and_monotonic():
    assert abs(kelly_fraction(0.1, 0.5) - 0.2) < 1e-9  # edge / (1 - price)
    assert kelly_fraction(0.05, 0.5) < kelly_fraction(0.10, 0.5)


def test_size_blocks_low_confidence():
    assert not size_position(_sig(conf=0.3), 1000, _Settings()).approved


def test_size_blocks_brain_veto():
    assert not size_position(_sig(brain=0.1), 1000, _Settings()).approved


def test_size_approves_and_respects_cap():
    d = size_position(_sig(), 1000, _Settings())
    assert d.approved
    assert d.amount <= _Settings.max_trade_pct * 1000 + 1e-9
    assert d.size > 0

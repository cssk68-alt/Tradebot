from datetime import datetime, timedelta, timezone

from tradebot.models import Market


def test_days_to_resolution_timezone_aware():
    m = Market(id="m", question="q", end_date=datetime.now(timezone.utc) + timedelta(days=10))
    assert 9.5 < m.days_to_resolution() < 10.5


def test_days_to_resolution_naive_does_not_crash():
    # A naive end_date (no tzinfo) must be handled, not raise TypeError.
    m = Market(id="m", question="q", end_date=datetime.utcnow() + timedelta(days=5))
    assert 4.5 < m.days_to_resolution() < 5.5


def test_days_to_resolution_none():
    assert Market(id="m", question="q").days_to_resolution() == 9999.0


def test_market_spread():
    m = Market(id="m", question="q", best_bid=0.40, best_ask=0.44)
    assert abs(m.spread - 0.04) < 1e-9
    assert Market(id="m", question="q").spread == 0.0

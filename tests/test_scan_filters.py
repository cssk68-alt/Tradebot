from pathlib import Path

from tradebot.agents.scan import ScanAgent
from tradebot.config import Settings
from tradebot.data.fixtures import sample_markets
from tradebot.log import get_logger
from tradebot.models import Market
from tradebot.store.db import Store


def _settings(tmp, **kw):
    base = dict(
        db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz",
        min_liquidity=1000, min_volume_24h=1000, max_spread=0.03,
        min_days_to_resolution=1, max_days_to_resolution=60,
    )
    base.update(kw)
    return Settings(**base)


def test_scan_spread_gate_filters_wide_spreads(tmp_path):
    # A.1: with max_spread=0.03 the wide-spread fixtures (eth 0.04, ai-model 0.07,
    # weather 0.07) are rejected; only the tight 1c markets survive.
    s = _settings(tmp_path)
    cands = ScanAgent(s, Store(s.db_path), get_logger("t")).run(sample_markets())
    assert len(cands) > 0
    for c in cands:
        assert c.market.spread <= s.max_spread + 1e-9


def test_scan_tighter_spread_keeps_fewer(tmp_path):
    # A tighter spread gate is strictly more selective than a looser one.
    loose = ScanAgent(_settings(tmp_path, max_spread=0.08), Store(Path(tmp_path) / "a.db"),
                      get_logger("t")).run(sample_markets())
    tight = ScanAgent(_settings(tmp_path, max_spread=0.02), Store(Path(tmp_path) / "b.db"),
                      get_logger("t")).run(sample_markets())
    assert len(tight) <= len(loose)


def test_scan_flags_wide_spread_when_allowed(tmp_path):
    # With a loose gate the ~0.07-spread fixtures pass the filter but are flagged
    # as a cost risk (wide_spread).
    s = _settings(tmp_path, max_spread=0.08)
    cands = ScanAgent(s, Store(s.db_path), get_logger("t")).run(sample_markets())
    flagged = [c for c in cands if any("spread" in f for f in c.flags)]
    assert flagged


def test_scan_unknown_book_falls_back_to_liquidity(tmp_path):
    # No best_bid/best_ask -> the spread is unknown, so the liquidity floor decides.
    s = _settings(tmp_path, min_liquidity=1000, max_spread=0.03)
    rich = Market(id="rich", question="q", yes_price=0.5, liquidity=5000.0,
                  volume_24h=9999.0, end_date=sample_markets()[0].end_date)
    poor = Market(id="poor", question="q", yes_price=0.5, liquidity=100.0,
                  volume_24h=9999.0, end_date=sample_markets()[0].end_date)
    cands = ScanAgent(s, Store(s.db_path), get_logger("t")).run([rich, poor])
    ids = {c.market.id for c in cands}
    assert "rich" in ids and "poor" not in ids

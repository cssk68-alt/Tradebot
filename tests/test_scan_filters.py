from pathlib import Path

from tradebot.agents.scan import ScanAgent
from tradebot.config import Settings
from tradebot.data.fixtures import sample_markets
from tradebot.log import get_logger
from tradebot.store.db import Store


def _settings(tmp, **kw):
    base = dict(
        db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz",
        min_liquidity=1000, min_volume_24h=1000,
        min_days_to_resolution=1, max_days_to_resolution=60,
    )
    base.update(kw)
    return Settings(**base)


def test_scan_passes_and_respects_filters(tmp_path):
    s = _settings(tmp_path)
    cands = ScanAgent(s, Store(s.db_path), get_logger("t")).run(sample_markets())
    assert len(cands) > 0
    for c in cands:
        assert c.market.liquidity >= s.min_liquidity
        assert c.market.volume_24h >= s.min_volume_24h


def test_scan_high_liquidity_cutoff(tmp_path):
    s = _settings(tmp_path, min_liquidity=100000)
    cands = ScanAgent(s, Store(s.db_path), get_logger("t")).run(sample_markets())
    assert all(c.market.liquidity >= 100000 for c in cands)


def test_scan_flags_wide_spread(tmp_path):
    s = _settings(tmp_path)
    cands = ScanAgent(s, Store(s.db_path), get_logger("t")).run(sample_markets())
    # the ai-model fixture has a ~0.07 spread -> should be flagged
    flagged = [c for c in cands if any("spread" in f for f in c.flags)]
    assert flagged

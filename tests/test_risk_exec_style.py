"""RiskAgent carries edge/spread to the exchange, which labels the trade's
execution style (Teil B.3 wiring: Signal -> Order -> PaperExchange -> exec_style)."""
from pathlib import Path

from tradebot.agents.risk import RiskAgent
from tradebot.config import Settings
from tradebot.data.gamma import GammaClient
from tradebot.exchange.paper import PaperExchange
from tradebot.log import get_logger
from tradebot.models import Side, Signal
from tradebot.store.db import Store


def _setup(tmp, **kw):
    base = dict(db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz",
                confidence_threshold=0.6, brain_veto_threshold=0.35, maker_min_edge=0.03)
    base.update(kw)
    s = Settings(**base)
    log = get_logger("t")
    store = Store(s.db_path)
    ex = PaperExchange(GammaClient(log), log, s)
    return RiskAgent(s, store, log, ex), store


def _sig(edge=0.2):
    return Signal(
        market_id="m", token_id="t", question="q", side=Side.BUY, market_price=0.5,
        true_prob=0.5 + edge, edge=edge, confidence=0.9, brain_score=0.8, is_yes=True,
    )


def test_large_edge_is_labeled_maker(tmp_path):
    risk, store = _setup(tmp_path)
    placed = risk.run([_sig(edge=0.2)], 1000.0, {"m": 1e6}, {"m": 0.02})
    assert len(placed) == 1
    assert placed[0].exec_style == "maker"
    # persisted, not just in-memory
    row = store.conn.execute("SELECT exec_style FROM trades").fetchone()
    assert row["exec_style"] == "maker"


def test_thin_edge_is_labeled_taker(tmp_path):
    # Edge below maker_min_edge -> take liquidity now. (Still a positive Kelly edge
    # so the order is placed.)
    risk, _ = _setup(tmp_path, maker_min_edge=0.10)
    placed = risk.run([_sig(edge=0.05)], 1000.0, {"m": 1e6}, {"m": 0.02})
    assert len(placed) == 1
    assert placed[0].exec_style == "taker"

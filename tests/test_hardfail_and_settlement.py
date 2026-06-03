"""Tests for the hard-fail architecture, settlement enum, live-execution guards
and the Stage-5 BrainManager."""
from pathlib import Path

import pytest

from tradebot.agents.brain_manager import BrainManager
from tradebot.agents.predict import PredictAgent
from tradebot.brain.experience import to_xy
from tradebot.brain.feedback import Brain
from tradebot.config import Settings
from tradebot.data import sentiment
from tradebot.data.gamma import DataUnavailableError, GammaClient
from tradebot.exchange.paper import PaperExchange
from tradebot.exchange.polymarket import (
    PolymarketExchange,
    _is_order_accepted_or_filled,
    _parse_execution,
)
from tradebot.log import get_logger
from tradebot.ml.features import BRAIN_FEATURE_DIM, FEATURE_DIM, build_brain_features
from tradebot.ml.model import Predictor
from tradebot.models import (
    Candidate,
    Experience,
    Market,
    Mode,
    ResearchReport,
    Resolution,
    ResolutionStatus,
    Side,
    Signal,
    Trade,
)
from tradebot.store.db import Store

log = get_logger("t")


def _settings(tmp, **kw) -> Settings:
    base = dict(db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz")
    base.update(kw)
    return Settings(**base)


def _open_trade(**kw) -> Trade:
    base = dict(
        market_id="m", token_id="t", question="q", side=Side.BUY, entry_price=0.4,
        size=100, mode=Mode.LIVE, is_yes=True, status="open",
    )
    base.update(kw)
    return Trade(**base)


class _FakeGamma:
    def __init__(self, res: Resolution):
        self._res = res

    def get_resolution(self, market_id: str) -> Resolution:
        return self._res


# --- hard-fail: sentiment has no pseudo/vader, stays neutral without data ---

def test_sentiment_is_neutral_without_data():
    assert sentiment.analyze([], "Will X happen?") == (0.0, "No external data; neutral sentiment.")


def test_sentiment_has_no_pseudo_or_vader():
    assert not hasattr(sentiment, "_pseudo")
    assert not hasattr(sentiment, "_vader_score")


# --- hard-fail: gamma raises instead of returning fixtures ---

def test_fetch_markets_raises_on_empty(monkeypatch):
    g = GammaClient(log)
    monkeypatch.setattr(g, "_fetch_live", lambda: [])
    with pytest.raises(DataUnavailableError):
        g.fetch_markets()


def test_fetch_markets_raises_on_error(monkeypatch):
    g = GammaClient(log)

    def boom():
        raise RuntimeError("403 forbidden")

    monkeypatch.setattr(g, "_fetch_live", boom)
    with pytest.raises(DataUnavailableError):
        g.fetch_markets()


# --- Bug 2.2: live mode skips signals with no real research sources ---

def test_live_skips_signals_without_research(tmp_path):
    s = _settings(tmp_path, mode="live", edge_threshold=0.05)
    agent = PredictAgent(s, Store(s.db_path), log, Predictor(log), Brain(s.brain_path, log))
    c = Candidate(market=Market(id="m", question="q", yes_price=0.5))
    report = ResearchReport(market_id="m", sentiment=0.25, n_sources=0)
    assert agent.run([c], {"m": report}) == []


def test_live_trades_with_real_research(tmp_path):
    s = _settings(tmp_path, mode="live", edge_threshold=0.05)
    agent = PredictAgent(s, Store(s.db_path), log, Predictor(log), Brain(s.brain_path, log))
    c = Candidate(market=Market(id="m", question="q", yes_price=0.5))
    report = ResearchReport(market_id="m", sentiment=0.8, n_sources=5)
    sigs = agent.run([c], {"m": report})
    assert len(sigs) == 1 and sigs[0].is_yes is True


# --- Bug 1.3: brain features carry the traded side + edge ---

def test_build_brain_features_appends_side_and_edge():
    f = build_brain_features([0.0] * FEATURE_DIM, is_yes=True, edge=0.12)
    assert len(f) == BRAIN_FEATURE_DIM
    assert f[-2] == 1.0 and abs(f[-1] - 0.12) < 1e-9


def test_brain_training_rows_include_side():
    common = dict(
        features=[0.5] * FEATURE_DIM, edge=0.1, size=10.0, brain_score=0.5,
        pnl=1.0, mode=Mode.PAPER,
    )
    yes_exp = Experience(won=True, is_yes=True, **common)
    no_exp = Experience(won=False, is_yes=False, **common)
    X, y = to_xy([yes_exp, no_exp])
    assert len(X[0]) == BRAIN_FEATURE_DIM
    assert X[0][-2] == 1.0 and X[1][-2] == 0.0
    assert X[0] != X[1]


# --- Bug 4.4: settlement enum maps terminal prices and errors ---

def _gamma_with(monkeypatch, payload):
    g = GammaClient(log)
    monkeypatch.setattr(g, "_get", lambda path, params: payload)
    return g


def test_resolution_yes(monkeypatch):
    g = _gamma_with(monkeypatch, {"closed": True, "outcomePrices": '["1", "0"]', "outcomes": '["Yes", "No"]'})
    res = g.get_resolution("m")
    assert res.status == ResolutionStatus.YES and res.resolved_yes is True


def test_resolution_no(monkeypatch):
    g = _gamma_with(monkeypatch, {"closed": True, "outcomePrices": '["0", "1"]', "outcomes": '["Yes", "No"]'})
    assert g.get_resolution("m").status == ResolutionStatus.NO


def test_resolution_open(monkeypatch):
    g = _gamma_with(monkeypatch, {"closed": False})
    assert g.get_resolution("m").status == ResolutionStatus.OPEN


def test_resolution_canceled(monkeypatch):
    g = _gamma_with(monkeypatch, {"closed": True, "outcomePrices": '["0.5", "0.5"]', "outcomes": '["Yes", "No"]'})
    assert g.get_resolution("m").status == ResolutionStatus.CANCELED


def test_resolution_ambiguous(monkeypatch):
    g = _gamma_with(monkeypatch, {"closed": True, "outcomePrices": '["0.7", "0.3"]', "outcomes": '["Yes", "No"]'})
    assert g.get_resolution("m").status == ResolutionStatus.AMBIGUOUS


def test_resolution_error(monkeypatch):
    g = GammaClient(log)

    def boom(path, params):
        raise RuntimeError("network")

    monkeypatch.setattr(g, "_get", boom)
    assert g.get_resolution("m").status == ResolutionStatus.ERROR


def test_settle_error_keeps_trade_open(tmp_path):
    ex = PaperExchange(_FakeGamma(Resolution(status=ResolutionStatus.ERROR, reason="x")), log, _settings(tmp_path))
    tr = _open_trade(mode=Mode.PAPER)
    assert ex.settle(tr) is None and tr.status == "open"


def test_settle_ambiguous_keeps_trade_open(tmp_path):
    ex = PaperExchange(_FakeGamma(Resolution(status=ResolutionStatus.AMBIGUOUS)), log, _settings(tmp_path))
    tr = _open_trade(mode=Mode.PAPER)
    assert ex.settle(tr) is None and tr.status == "open"


def test_settle_canceled_is_void_refund(tmp_path):
    ex = PaperExchange(_FakeGamma(Resolution(status=ResolutionStatus.CANCELED)), log, _settings(tmp_path))
    tr = _open_trade(mode=Mode.PAPER, size=100, entry_price=0.4)
    r = ex.settle(tr)
    assert r.status == "resolved" and r.pnl == 0.0 and r.won is None


def test_settle_yes_via_resolution(tmp_path):
    ex = PaperExchange(_FakeGamma(Resolution(status=ResolutionStatus.YES, resolved_yes=True)), log, _settings(tmp_path))
    tr = _open_trade(mode=Mode.PAPER, is_yes=True, size=100, entry_price=0.4)
    r = ex.settle(tr)
    assert r.won is True and abs(r.pnl - 60.0) < 1e-6


# --- Bug 3.1: live close failure keeps the trade open ---

class _BadClient:
    def create_order(self, args):
        return object()

    def post_order(self, signed, order_type):
        raise RuntimeError("network down")


def test_live_close_failure_keeps_trade_open(tmp_path):
    ex = PolymarketExchange(None, log, _settings(tmp_path), dry_run=False)
    ex._client = _BadClient()
    trade = _open_trade(entry_price=0.5, size=100, is_yes=True)
    market = Market(id="m", question="q", yes_price=0.55)
    assert ex.close(trade, market, reason="take_profit") is None
    assert trade.status == "open"


def test_dry_run_close_marks_resolved(tmp_path):
    ex = PolymarketExchange(None, log, _settings(tmp_path), dry_run=True)
    trade = _open_trade(entry_price=0.5, size=100, is_yes=True)
    market = Market(id="m", question="q", yes_price=0.55, best_bid=0.545, best_ask=0.555)
    r = ex.close(trade, market, reason="take_profit")
    assert r is not None and r.status == "resolved" and r.kind == "scalp"


# --- Bug 3.2: live BUY only becomes a trade when a fill is confirmed ---

def test_parse_execution_filled():
    r = _parse_execution({"status": "matched", "filled_size": 100, "avg_price": 0.42, "orderID": "x"})
    assert r.accepted and r.filled_size == 100.0 and r.avg_price == 0.42 and r.order_id == "x"


def test_parse_execution_accepted_but_unfilled():
    r = _parse_execution({"status": "live", "filled_size": 0})
    assert r.accepted and r.filled_size == 0.0  # resting maker -> no position


def test_parse_execution_unknown_is_not_accepted():
    assert not _parse_execution({}).accepted
    assert not _parse_execution({"status": "rejected"}).accepted


def test_is_order_accepted_or_filled():
    assert _is_order_accepted_or_filled({"success": True})
    assert _is_order_accepted_or_filled({"filled_size": 5})
    assert not _is_order_accepted_or_filled({"status": "rejected"})


# --- Stage 5: BrainManager approves/vetoes and always writes a DB record ---

class _ClaudeApprove:
    available = True

    def decide_execution(self, **kw):
        return True, "signals are consistent"


class _ClaudeVeto:
    available = True

    def decide_execution(self, **kw):
        return False, "reddit hype contradicts rss news"


class _ClaudeUnparseable:
    available = True

    def decide_execution(self, **kw):
        return None


def _signal() -> Signal:
    return Signal(
        market_id="m", token_id="t", question="q", side=Side.BUY, market_price=0.5,
        true_prob=0.7, model_prob=0.68, edge=0.2, confidence=0.8, is_yes=True, brain_score=0.6,
    )


def test_manager_approves_and_records(tmp_path):
    s = _settings(tmp_path)
    store = Store(s.db_path)
    mgr = BrainManager(s, store, log, _ClaudeApprove())
    out = mgr.run([_signal()], {"m": ResearchReport(market_id="m")})
    assert len(out) == 1
    row = store.conn.execute("SELECT approved, reason FROM manager_decisions").fetchone()
    assert row["approved"] == 1 and row["reason"]


def test_manager_vetoes_and_records(tmp_path):
    s = _settings(tmp_path)
    store = Store(s.db_path)
    mgr = BrainManager(s, store, log, _ClaudeVeto())
    assert mgr.run([_signal()], {"m": ResearchReport(market_id="m")}) == []
    row = store.conn.execute("SELECT approved FROM manager_decisions").fetchone()
    assert row["approved"] == 0


def test_manager_fails_closed_on_unparseable_llm(tmp_path):
    s = _settings(tmp_path)
    store = Store(s.db_path)
    mgr = BrainManager(s, store, log, _ClaudeUnparseable())
    assert mgr.run([_signal()], {"m": ResearchReport(market_id="m")}) == []


def test_manager_vetoes_without_llm(tmp_path):
    # No auto-approve fallback, not even in paper mode: no agent -> fail-closed veto.
    s = _settings(tmp_path)
    store = Store(s.db_path)
    mgr = BrainManager(s, store, log, client=None)
    out = mgr.run([_signal()], {"m": ResearchReport(market_id="m")})
    assert out == []
    row = store.conn.execute("SELECT approved FROM manager_decisions").fetchone()
    assert row["approved"] == 0

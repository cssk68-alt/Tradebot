"""Tests for closing trades whose market has dropped out of ``list_markets()``.

Core invariant (paper AND live): an open scalp trade must NEVER hang open silently
just because its market vanished from the bulk feed. A market disappears when it
resolves (e.g. a UFC fight ends → settling), or when it falls below the liquidity
filter. ``Orchestrator._close_missing`` must then actively fetch what it needs to
close — the real resolution, or a fresh single-market price — instead of skipping.

These exercise the pure control flow with a fake Gamma + the REAL PaperExchange
(so settle/close run their real logic); no network, no LLM, no full ctor."""
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tradebot.data.gamma import GammaClient
from tradebot.exchange.paper import PaperExchange
from tradebot.log import get_logger
from tradebot.models import (
    Market,
    Mode,
    Resolution,
    ResolutionStatus,
    Side,
    Trade,
)
from tradebot.orchestrator import Orchestrator

log = get_logger("t")


def _ns(**kw) -> SimpleNamespace:
    base = dict(
        strategy="scalp", take_profit=0.02, stop_loss=0.03,
        max_hold_seconds=300.0, min_spread_cost=0.01,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _trade(**kw) -> Trade:
    base = dict(
        market_id="gone", token_id="tok", question="Will X happen?", side=Side.BUY,
        entry_price=0.5, size=100, mode=Mode.PAPER, is_yes=True, status="open",
    )
    base.update(kw)
    return Trade(**base)


def _old(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


class _FakeGamma:
    """Stands in for GammaClient: canned resolution + single-market price, and
    counts ``fetch_market`` calls so a test can assert the price path was (not) taken."""

    def __init__(self, resolution: Resolution, market=None):
        self._res = resolution
        self._market = market
        self.fetch_calls = 0

    def get_resolution(self, market_id):
        return self._res

    def fetch_market(self, market_id):
        self.fetch_calls += 1
        return self._market


class _FakeStore:
    def __init__(self, open_trades):
        self._open = list(open_trades)
        self.updated = []
        self.experiences = []

    def open_trades(self, mode):
        return list(self._open)

    def update_trade(self, t):
        self.updated.append(t)
        if t in self._open:
            self._open.remove(t)

    def save_experience(self, e):
        self.experiences.append(e)


def _orch(gamma, settings, store=None) -> Orchestrator:
    o = Orchestrator.__new__(Orchestrator)  # skip the heavy ctor (LLM/DB/training)
    o.gamma = gamma
    o.exchange = PaperExchange(gamma, log, settings)
    o.settings = settings
    o.store = store
    o.log = log
    o.mode = Mode.PAPER
    return o


# --- _close_missing: market gone but RESOLVED → settle for real -----------------

def test_missing_but_resolved_yes_settles_real():
    g = _FakeGamma(Resolution(status=ResolutionStatus.YES, resolved_yes=True))
    o = _orch(g, _ns())
    t = _trade(is_yes=True, entry_price=0.4, size=100)
    r = o._close_missing(t, None)
    assert r is not None and r.status == "resolved" and r.kind == "resolve"
    assert r.won is True and abs(r.pnl - 60.0) < 1e-6
    assert g.fetch_calls == 0  # settled from resolution; no price fetch needed


def test_missing_but_canceled_is_void_refund():
    g = _FakeGamma(Resolution(status=ResolutionStatus.CANCELED, reason="50/50"))
    o = _orch(g, _ns())
    r = o._close_missing(_trade(entry_price=0.4, size=100), None)
    assert r is not None and r.status == "resolved"
    assert r.pnl == 0.0 and r.won is None  # non-outcome, not fed to the brain as a loss


# --- _close_missing: market gone but STILL OPEN → single-market price → scalp ----

def test_missing_open_past_max_hold_closes_on_time():
    g = _FakeGamma(
        Resolution(status=ResolutionStatus.OPEN),
        market=Market(id="gone", question="q", yes_price=0.55, best_bid=0.545, best_ask=0.555),
    )
    o = _orch(g, _ns())
    t = _trade(is_yes=True, entry_price=0.5, size=100, opened_at=_old(400))  # > max_hold
    r = o._close_missing(t, None)
    assert r is not None and r.status == "resolved" and r.kind == "scalp"
    assert r.exit_price == 0.55 and abs(r.pnl - 4.0) < 1e-6
    assert g.fetch_calls == 1


def test_missing_open_young_trade_stays_open():
    # Transient drop-out of a healthy young trade: fetch the price, but no exit
    # trigger fires → leave it open (don't dump it) and retry next cycle.
    g = _FakeGamma(
        Resolution(status=ResolutionStatus.OPEN),
        market=Market(id="gone", question="q", yes_price=0.50, best_bid=0.495, best_ask=0.505),
    )
    o = _orch(g, _ns())
    t = _trade(entry_price=0.5, size=100, opened_at=datetime.now(timezone.utc))
    assert o._close_missing(t, None) is None
    assert t.status == "open" and g.fetch_calls == 1


def test_missing_open_no_price_stays_open():
    g = _FakeGamma(Resolution(status=ResolutionStatus.OPEN), market=None)
    o = _orch(g, _ns())
    t = _trade(opened_at=_old(400))
    assert o._close_missing(t, None) is None
    assert t.status == "open" and g.fetch_calls == 1  # tried the price path, gave up loudly


def test_missing_ambiguous_is_left_for_review_not_scalped():
    # Closed-but-ambiguous is explicitly NOT auto-closed at a guessed price; it stays
    # open for manual review and the price path must not even be attempted.
    g = _FakeGamma(Resolution(status=ResolutionStatus.AMBIGUOUS, reason="non-terminal price"))
    o = _orch(g, _ns())
    t = _trade(opened_at=_old(400))
    assert o._close_missing(t, None) is None
    assert t.status == "open" and g.fetch_calls == 0


# --- _close_missing during wind-down: force-close at any price once deadline hits -

def test_missing_open_wind_down_force_closes_at_loss():
    g = _FakeGamma(
        Resolution(status=ResolutionStatus.OPEN),
        market=Market(id="gone", question="q", yes_price=0.48, best_bid=0.475, best_ask=0.485),
    )
    o = _orch(g, _ns())
    t = _trade(is_yes=True, entry_price=0.5, size=100, opened_at=datetime.now(timezone.utc))
    r = o._close_missing(t, wind_down_deadline=time.time() - 1.0)  # deadline already past
    assert r is not None and r.status == "resolved" and r.kind == "scalp"
    assert r.won is False and abs(r.pnl - (-3.0)) < 1e-6  # closed even at a loss


# --- manage_open routes a missing market through _close_missing ------------------

def test_manage_open_settles_missing_market_end_to_end():
    g = _FakeGamma(Resolution(status=ResolutionStatus.YES, resolved_yes=True))
    t = _trade(market_id="gone", is_yes=True, entry_price=0.4, size=100)
    store = _FakeStore([t])
    o = _orch(g, _ns(), store=store)
    o._after_resolved = lambda resolved, verb: None  # skip postmortem/retrain
    resolved = o.manage_open(markets=[])  # "gone" is not in the bulk list
    assert len(resolved) == 1 and resolved[0].won is True
    assert store.updated == [t] and store._open == []  # persisted resolved, no longer open


# --- GammaClient.fetch_market: single-market direct fetch -----------------------

def test_fetch_market_parses_single_payload(monkeypatch):
    g = GammaClient(log)
    monkeypatch.setattr(g, "_get", lambda path, params: {
        "id": "123", "question": "Will X?", "outcomePrices": '["0.6","0.4"]',
        "outcomes": '["Yes","No"]', "clobTokenIds": '["yes-tok","no-tok"]',
        "bestBid": "0.59", "bestAsk": "0.61",
    })
    m = g.fetch_market("123")
    assert m is not None and abs(m.yes_price - 0.6) < 1e-9
    assert m.yes_token_id == "yes-tok" and abs(m.spread - 0.02) < 1e-9


def test_fetch_market_unwraps_list(monkeypatch):
    g = GammaClient(log)
    monkeypatch.setattr(g, "_get", lambda path, params: [
        {"id": "1", "question": "q", "outcomePrices": '["1","0"]', "outcomes": '["Yes","No"]'}
    ])
    m = g.fetch_market("1")
    assert m is not None and m.yes_price == 1.0


def test_fetch_market_none_without_prices(monkeypatch):
    g = GammaClient(log)
    monkeypatch.setattr(g, "_get", lambda path, params: {"id": "1", "closed": False})
    assert g.fetch_market("1") is None  # no fabricated 0.5 default


def test_fetch_market_none_on_error(monkeypatch):
    g = GammaClient(log)

    def boom(path, params):
        raise RuntimeError("network")

    monkeypatch.setattr(g, "_get", boom)
    assert g.fetch_market("1") is None

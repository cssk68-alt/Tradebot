"""Tests for honest maker-first execution in PAPER mode.

A maker order is NOT filled at the better price on faith. PaperExchange rests it as
``pending_maker``; ``Orchestrator.resolve_pending_makers`` then confirms or denies the
fill against the REAL recorded price path (snapshots + current live price), exactly
like the live CLOB only fills when the book trades to the order. Filled → open at the
better limit; missed → take at the current price; stale/lost → cancel."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tradebot.exchange.execution_style import resolve_maker_fill
from tradebot.exchange.paper import PaperExchange
from tradebot.log import get_logger
from tradebot.models import Market, Mode, Order, Side, Trade
from tradebot.orchestrator import Orchestrator
from tradebot.store.db import Store

log = get_logger("t")

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _series(*points):
    return [(T0 + timedelta(seconds=s), yes, spread) for s, yes, spread in points]


# --- pure resolve_maker_fill: YES side --------------------------------------------

def test_fill_in_window_touch():
    # Side price dips to the limit inside the window → filled at that tick.
    res = resolve_maker_fill(
        0.50, True, _series((20, 0.49, 0.01)), T0, T0 + timedelta(seconds=60),
        now=T0 + timedelta(seconds=30),
    )
    assert res["status"] == "filled" and res["fill_ts"] == T0 + timedelta(seconds=20)


def test_fill_at_window_end_sample():
    # No in-window tick; the first sample at/after the deadline is at/below the bid.
    res = resolve_maker_fill(
        0.50, True, _series((65, 0.49, 0.01)), T0, T0 + timedelta(seconds=60),
        now=T0 + timedelta(seconds=65),
    )
    assert res["status"] == "filled"


def test_missed_when_price_stays_above():
    res = resolve_maker_fill(
        0.50, True, _series((65, 0.55, 0.01)), T0, T0 + timedelta(seconds=60),
        now=T0 + timedelta(seconds=65),
    )
    assert res["status"] == "missed"


def test_pending_before_deadline_no_touch():
    res = resolve_maker_fill(
        0.50, True, _series((20, 0.55, 0.01)), T0, T0 + timedelta(seconds=60),
        now=T0 + timedelta(seconds=30),
    )
    assert res["status"] == "pending"


def test_stale_tick_past_tolerance_does_not_fill():
    # A touch long after the window (gap then reappear) must not fabricate a fill.
    res = resolve_maker_fill(
        0.50, True, _series((200, 0.40, 0.01)), T0, T0 + timedelta(seconds=60),
        now=T0 + timedelta(seconds=200),
    )
    assert res["status"] == "missed"


def test_fill_no_side_uses_complement():
    # NO trade: side price = 1 - yes. Limit 0.40 fills when yes rises to >= 0.60.
    res = resolve_maker_fill(
        0.40, False, _series((30, 0.62, 0.01)), T0, T0 + timedelta(seconds=60),
        now=T0 + timedelta(seconds=30),
    )
    assert res["status"] == "filled"


# --- PaperExchange.place_order: maker rests, taker opens --------------------------

def _settings(**kw):
    base = dict(
        strategy="scalp", maker_first=True, maker_min_edge=0.03, maker_timeout_seconds=60.0,
        take_profit=0.02, stop_loss=0.03, max_hold_seconds=300.0, min_spread_cost=0.01,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _order(**kw):
    base = dict(
        market_id="m", token_id="t", question="q", side=Side.BUY, is_yes=True,
        price=0.50, size=100, mode=Mode.PAPER, edge=0.10, spread=0.01,
    )
    base.update(kw)
    return Order(**base)


def test_paper_maker_order_rests_as_pending():
    ex = PaperExchange(None, log, _settings())
    t = ex.place_order(_order(edge=0.10))  # edge >= maker_min_edge → maker
    assert t.status == "pending_maker" and t.exec_style == "maker"
    assert t.entry_price == 0.49  # one tick inside 0.50, not the reference price


def test_paper_taker_order_opens_immediately():
    ex = PaperExchange(None, log, _settings())
    t = ex.place_order(_order(edge=0.01))  # edge < maker_min_edge → taker
    assert t.status == "open" and t.exec_style == "taker" and t.entry_price == 0.50


# --- Orchestrator.resolve_pending_makers (real Store) ----------------------------

def _orch(store, **settings_kw):
    o = Orchestrator.__new__(Orchestrator)
    o.store = store
    o.settings = _settings(**settings_kw)
    o.log = log
    o.mode = Mode.PAPER
    return o


def _pending(store, *, limit, is_yes, age_s, size=100):
    """Persist a resting maker order posted ``age_s`` seconds ago."""
    t = Trade(
        market_id="m", token_id="t", question="q", side=Side.BUY, is_yes=is_yes,
        entry_price=limit, size=size, mode=Mode.PAPER, status="pending_maker",
        exec_style="maker", kind="scalp",
        opened_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
    )
    store.save_trade(t)
    return t.id


def _mkt(yes_price):
    return Market(id="m", question="q", yes_price=yes_price, best_bid=yes_price - 0.005,
                  best_ask=yes_price + 0.005)


def _row(store, tid):
    return store.conn.execute(
        "SELECT status, exec_style, entry_price FROM trades WHERE id=?", (tid,)
    ).fetchone()


def test_resolve_fills_maker_when_price_reaches_bid(tmp_path):
    store = Store(tmp_path / "t.db")
    tid = _pending(store, limit=0.50, is_yes=True, age_s=20)  # window still open
    o = _orch(store)
    o.resolve_pending_makers([_mkt(0.49)])  # current price reached the bid
    r = _row(store, tid)
    assert r["status"] == "open" and r["exec_style"] == "maker" and r["entry_price"] == 0.50


def test_resolve_misses_to_taker_at_current_price(tmp_path):
    store = Store(tmp_path / "t.db")
    tid = _pending(store, limit=0.50, is_yes=True, age_s=90)  # deadline passed, not stale
    o = _orch(store)
    o.resolve_pending_makers([_mkt(0.55)])  # never reached the bid → take now
    r = _row(store, tid)
    assert r["status"] == "open" and r["exec_style"] == "taker" and r["entry_price"] == 0.55


def test_resolve_keeps_pending_before_deadline(tmp_path):
    store = Store(tmp_path / "t.db")
    tid = _pending(store, limit=0.50, is_yes=True, age_s=10)  # window still open
    o = _orch(store)
    o.resolve_pending_makers([_mkt(0.55)])  # above the bid, deadline not reached
    assert _row(store, tid)["status"] == "pending_maker"


def test_resolve_cancels_when_market_vanished(tmp_path):
    store = Store(tmp_path / "t.db")
    tid = _pending(store, limit=0.50, is_yes=True, age_s=90)
    o = _orch(store)
    o.resolve_pending_makers([])  # market not in the universe and deadline passed
    assert _row(store, tid)["status"] == "canceled"


def test_resolve_cancels_stale_leftover(tmp_path):
    store = Store(tmp_path / "t.db")
    tid = _pending(store, limit=0.50, is_yes=True, age_s=300)  # resolved long after window
    o = _orch(store)
    o.resolve_pending_makers([_mkt(0.55)])  # too late to be a faithful taker fill
    assert _row(store, tid)["status"] == "canceled"


def test_pending_maker_counts_toward_exposure(tmp_path):
    store = Store(tmp_path / "t.db")
    _pending(store, limit=0.50, is_yes=True, age_s=10, size=100)   # 0.50 * 100 = 50
    store.save_trade(Trade(
        market_id="m2", token_id="t2", question="q", side=Side.BUY, is_yes=True,
        entry_price=0.40, size=100, mode=Mode.PAPER, status="open",
    ))  # 0.40 * 100 = 40
    assert store.open_exposure(Mode.PAPER) == 90.0
    assert len(store.open_trades(Mode.PAPER)) == 1          # pending maker is NOT open
    assert len(store.pending_maker_trades(Mode.PAPER)) == 1

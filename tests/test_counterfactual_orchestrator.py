"""Orchestrator counterfactual wiring: record (veto/mirror) + settle -> experience."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.config import Settings
from tradebot.log import get_logger
from tradebot.models import Counterfactual, Side, Signal
from tradebot.orchestrator import Orchestrator

log = get_logger("t")


def _orch(tmp, **kw):
    base = dict(
        deepseek_api_key="x", mode="paper", strategy="scalp",
        db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz",
        take_profit=0.02, stop_loss=0.03, max_hold_seconds=300,
    )
    base.update(kw)
    return Orchestrator(Settings(**base), log)


def _sig(mid="m", is_yes=True, price=0.5, edge=0.1):
    return Signal(market_id=mid, token_id="t", question="q", side=Side.BUY,
                  market_price=price, true_prob=price + edge, edge=edge, confidence=0.8,
                  is_yes=is_yes, features=[0.1] * 20, brain_score=0.5)


def test_record_veto_makes_own_side_plus_mirror(tmp_path):
    o = _orch(tmp_path)
    sig = _sig()
    o.manager.decisions = [(sig, False, "veto reason")]
    o._record_counterfactuals([sig], placed=[])
    cfs = o.store.pending_counterfactuals()
    assert len(cfs) == 2
    by_src = {c.source: c for c in cfs}
    assert by_src["veto"].is_yes is True and abs(by_src["veto"].entry_price - 0.5) < 1e-9
    assert by_src["mirror"].is_yes is False and abs(by_src["mirror"].entry_price - 0.5) < 1e-9
    assert by_src["veto"].reason == "veto reason"


def test_record_executed_makes_only_mirror(tmp_path):
    o = _orch(tmp_path)
    sig = _sig()
    o.manager.decisions = [(sig, True, "ok")]

    class _Placed:
        market_id = "m"

    o._record_counterfactuals([sig], placed=[_Placed()])
    cfs = o.store.pending_counterfactuals()
    assert len(cfs) == 1 and cfs[0].source == "mirror" and cfs[0].is_yes is False


def test_record_skipped_in_resolve_mode(tmp_path):
    o = _orch(tmp_path, strategy="resolve")
    sig = _sig()
    o.manager.decisions = [(sig, False, "veto")]
    o._record_counterfactuals([sig], placed=[])
    assert o.store.pending_counterfactuals() == []


def test_settle_counterfactual_creates_flagged_experience(tmp_path):
    o = _orch(tmp_path)
    now = datetime.now(timezone.utc)
    entry = now - timedelta(minutes=10)  # window (5 min) already elapsed
    o.store.save_counterfactual(Counterfactual(
        market_id="m", is_yes=True, entry_price=0.5, entry_ts=entry, edge=0.1,
        brain_score=0.5, features=[0.1] * 20, source="veto",
        take_profit=0.02, stop_loss=0.03, max_hold=300,
    ))
    # real price path after entry: YES rose 0.50 -> 0.55 (a win for the YES side)
    o.store.conn.execute(
        "INSERT INTO snapshots(market_id, yes_price, ts, spread) VALUES (?,?,?,?)",
        ("m", 0.55, (entry + timedelta(seconds=60)).isoformat(), 0.005),
    )
    o.store.conn.commit()

    added = o.settle_counterfactuals()
    assert added == 1
    exps = o.store.load_experiences()
    assert len(exps) == 1 and exps[0].is_counterfactual is True and exps[0].won is True
    stats = o.store.counterfactual_stats()
    assert stats["settled"] == 1 and stats["pending"] == 0


def test_settle_respects_learn_from_vetos_off(tmp_path):
    o = _orch(tmp_path, learn_from_vetos=False)
    now = datetime.now(timezone.utc)
    entry = now - timedelta(minutes=10)
    o.store.save_counterfactual(Counterfactual(
        market_id="m", is_yes=True, entry_price=0.5, entry_ts=entry, features=[0.1] * 20,
        source="veto", take_profit=0.02, stop_loss=0.03, max_hold=300,
    ))
    o.store.conn.execute(
        "INSERT INTO snapshots(market_id, yes_price, ts, spread) VALUES (?,?,?,?)",
        ("m", 0.55, (entry + timedelta(seconds=60)).isoformat(), 0.005),
    )
    o.store.conn.commit()
    added = o.settle_counterfactuals()
    # scoreboard still updates, but NO training experience is created
    assert added == 0
    assert o.store.load_experiences() == []
    assert o.store.counterfactual_stats()["settled"] == 1

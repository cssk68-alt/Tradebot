"""Store: counterfactuals CRUD, snapshots_between, is_counterfactual roundtrip."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.models import Counterfactual, Experience, Mode
from tradebot.store.db import Store


def test_counterfactual_crud_and_scoreboard(tmp_path):
    s = Store(Path(tmp_path) / "t.db")
    now = datetime.now(timezone.utc)
    cf = Counterfactual(market_id="m", is_yes=True, entry_price=0.5, entry_ts=now,
                        features=[0.1] * 20, source="veto", reason="r")
    cid = s.save_counterfactual(cf)
    assert cid > 0

    pend = s.pending_counterfactuals()
    assert len(pend) == 1 and pend[0].market_id == "m" and pend[0].is_yes is True

    c = pend[0]
    c.status = "settled"
    c.won = False  # vetoed setup would have LOST -> veto was right
    c.pnl = -1.0
    c.exit_reason = "time"
    c.settled_at = now
    s.update_counterfactual(c)

    assert s.pending_counterfactuals() == []
    stats = s.counterfactual_stats()
    assert stats["settled"] == 1 and stats["brain_right"] == 1 and stats["brain_wrong"] == 0


def test_snapshots_between_window(tmp_path):
    s = Store(Path(tmp_path) / "t.db")
    base = datetime.now(timezone.utc)
    for k in range(3):  # ts at +0, +60, +120
        s.conn.execute(
            "INSERT INTO snapshots(market_id, yes_price, ts, spread) VALUES (?,?,?,?)",
            ("m", 0.50 + 0.01 * k, (base + timedelta(seconds=60 * k)).isoformat(), 0.005),
        )
    s.conn.commit()
    series = s.snapshots_between("m", base, base + timedelta(seconds=130))
    assert len(series) == 2          # +0 excluded (not > t0); +60 and +120 included
    assert abs(series[0][1] - 0.51) < 1e-9 and abs(series[0][2] - 0.005) < 1e-9


def test_experience_counterfactual_roundtrip(tmp_path):
    s = Store(Path(tmp_path) / "t.db")
    s.save_experience(Experience(features=[0.1] * 20, edge=0.1, size=1.0, brain_score=0.5,
                                 won=True, pnl=1.0, mode=Mode.PAPER, is_yes=True,
                                 is_counterfactual=True))
    s.save_experience(Experience(features=[0.1] * 20, edge=0.1, size=1.0, brain_score=0.5,
                                 won=False, pnl=-1.0, mode=Mode.PAPER, is_yes=False))
    loaded = s.load_experiences()
    assert {e.is_counterfactual for e in loaded} == {True, False}

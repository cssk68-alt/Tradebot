"""PredictAgent blocks scalps whose TP/SL collapse on the tick grid (Teil B.5)."""
from pathlib import Path

from tradebot.agents.predict import PredictAgent
from tradebot.brain.feedback import Brain
from tradebot.config import Settings
from tradebot.log import get_logger
from tradebot.ml.model import Predictor
from tradebot.models import Candidate, Market, ResearchReport
from tradebot.store.db import Store


def _agent(tmp, **kw):
    base = dict(
        db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz",
        edge_threshold=0.05, strategy="scalp", min_net_profit=0.0,
    )
    base.update(kw)
    s = Settings(**base)
    log = get_logger("t")
    return PredictAgent(s, Store(s.db_path), log, Predictor(log), Brain(s.brain_path, log))


def _inputs():
    c = Candidate(market=Market(id="m", question="q", yes_price=0.5))
    return [c], {"m": ResearchReport(market_id="m", sentiment=0.8, n_sources=8)}  # strong YES edge


def test_collapsed_tp_blocks_the_scalp(tmp_path):
    # take_profit 0.4c on the 1c grid -> the TP target rounds back onto the entry,
    # so the trade can never realize and must be blocked.
    cands, reports = _inputs()
    assert _agent(tmp_path, take_profit=0.004, stop_loss=0.03).run(cands, reports) == []


def test_normal_tp_still_trades(tmp_path):
    # The same setup with a sane 2c take-profit produces a signal — proving the
    # collapse guard, not something else, suppressed the trade above.
    cands, reports = _inputs()
    sigs = _agent(tmp_path, take_profit=0.02, stop_loss=0.03).run(cands, reports)
    assert len(sigs) == 1 and sigs[0].is_yes is True


def _inputs_at(yes):
    c = Candidate(market=Market(id="m", question="q", yes_price=yes))
    return [c], {"m": ResearchReport(market_id="m", sentiment=0.8, n_sources=8)}


def test_extreme_low_price_scalp_blocked_but_resolve_allowed(tmp_path):
    # Regression (the Elon −16.80 bug at its source): a longshot priced below the
    # stop-loss distance must NOT be scalped — its stop sits below 0 and its huge
    # share count makes the spread dwarf the stake. Hold-to-event is fine, though.
    cands, reports = _inputs_at(0.02)  # price 0.02 < stop_loss 0.07
    # resolve: the scalp guard does not apply -> a signal IS produced (edge exists),
    # so the scalp block below is the new guard C, not a missing edge.
    assert len(_agent(tmp_path, strategy="resolve", stop_loss=0.07, take_profit=0.03)
               .run(cands, reports)) == 1
    # scalp: blocked because the 0.07 stop would sit at -0.05 (unreachable).
    assert _agent(tmp_path, strategy="scalp", stop_loss=0.07, take_profit=0.03).run(cands, reports) == []

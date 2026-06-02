from pathlib import Path

from tradebot.agents.predict import PredictAgent
from tradebot.brain.feedback import Brain
from tradebot.config import Settings
from tradebot.log import get_logger
from tradebot.ml.model import Predictor
from tradebot.models import Candidate, Market, ResearchReport
from tradebot.store.db import Store


def _agent(tmp):
    s = Settings(db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz", edge_threshold=0.05)
    log = get_logger("t")
    store = Store(s.db_path)
    return PredictAgent(s, store, log, Predictor(log), Brain(s.brain_path, log))


def _candidate(yes_price=0.5):
    return Candidate(market=Market(id="m", question="q", yes_price=yes_price))


def test_buys_yes_on_positive_edge(tmp_path):
    agent = _agent(tmp_path)
    reports = {"m": ResearchReport(market_id="m", sentiment=0.8)}  # heuristic prob ~0.70
    sigs = agent.run([_candidate()], reports)
    assert len(sigs) == 1
    assert sigs[0].is_yes is True
    assert sigs[0].edge > 0


def test_buys_no_on_negative_edge(tmp_path):
    agent = _agent(tmp_path)
    reports = {"m": ResearchReport(market_id="m", sentiment=-0.8)}  # heuristic prob ~0.30
    sigs = agent.run([_candidate()], reports)
    assert len(sigs) == 1
    assert sigs[0].is_yes is False


def test_no_signal_when_no_edge(tmp_path):
    agent = _agent(tmp_path)
    reports = {"m": ResearchReport(market_id="m", sentiment=0.0)}  # prob == price
    assert agent.run([_candidate()], reports) == []

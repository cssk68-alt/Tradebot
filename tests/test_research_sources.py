"""Tests for the new research sources: web search, hard-fact feeds, and their
wiring into features + the predict gate. All offline — network fetches are
injected or parsers are exercised directly."""
from pathlib import Path

from tradebot.agents.predict import PredictAgent
from tradebot.brain.feedback import Brain
from tradebot.config import Settings
from tradebot.data import facts, websearch
from tradebot.log import get_logger
from tradebot.ml.features import FEATURE_DIM, FEATURE_NAMES, build_features
from tradebot.ml.model import Predictor
from tradebot.models import Candidate, Market, ResearchReport
from tradebot.store.db import Store


# --- web search parsers ---------------------------------------------------

def test_websearch_parsers_extract_text():
    tav = websearch._parse_tavily(
        {"results": [{"title": "BTC rallies", "content": "to new highs"}, {"title": "", "content": ""}]}
    )
    assert tav == ["BTC rallies to new highs"]
    gd = websearch._parse_gdelt({"articles": [{"title": "Election update"}, {"title": ""}]})
    assert gd == ["Election update"]
    dd = websearch._parse_ddg([{"title": "T", "body": "B"}, {"title": "", "body": ""}])
    assert dd == ["T B"]


def test_websearch_empty_query_is_silent():
    assert websearch.search("") == []


# --- crypto facts ---------------------------------------------------------

def test_crypto_fact_above_strike_is_bullish():
    f = facts.crypto_fact(
        "Will the price of Bitcoin be above $64,000 on June 30?",
        price_fetcher=lambda cid: 72000.0,
    )
    assert f is not None and f.source == "coingecko"
    assert f.prob > 0.5  # price well above strike -> YES likely
    assert "64,000" in f.text


def test_crypto_fact_below_direction_inverts():
    above = facts.crypto_fact("Will Bitcoin be above $64,000?", price_fetcher=lambda c: 60000.0)
    below = facts.crypto_fact("Will Bitcoin fall below $64,000?", price_fetcher=lambda c: 60000.0)
    assert above is not None and below is not None
    assert above.prob < 0.5  # price under the strike -> 'above' unlikely
    assert below.prob > 0.5  # ... which makes 'below' likely


def test_crypto_fact_handles_k_suffix():
    strike, direction = facts._parse_strike("Will ETH reach $5k this year?")
    assert strike == 5000.0 and direction == "above"


def test_crypto_strike_ignores_trailing_by_word():
    # Regression: "$64,000 by Friday" must NOT read the 'b' of 'by' as billions.
    strike, direction = facts._parse_strike("Will Bitcoin be above $64,000 by Friday?")
    assert strike == 64000.0 and direction == "above"


def test_crypto_fact_none_without_coin_or_strike():
    assert facts.crypto_fact("Will Flavio Cobolli win?", price_fetcher=lambda c: 1.0) is None
    assert facts.crypto_fact("Will Bitcoin go up?", price_fetcher=lambda c: 1.0) is None


# --- bookmaker odds facts -------------------------------------------------

def _fake_event(name_a, odds_a, name_b, odds_b):
    return {
        "bookmakers": [
            {"markets": [{"key": "h2h", "outcomes": [
                {"name": name_a, "price": odds_a},
                {"name": name_b, "price": odds_b},
            ]}]}
        ]
    }


def test_odds_fact_removes_vig_and_matches_name():
    ev = _fake_event("Flavio Cobolli", 1.5, "Andres Andrade", 2.5)
    f = facts.odds_fact(
        "Will Flavio Cobolli win the match?",
        api_key="x",
        events_fetcher=lambda sport, key: [ev],
        sports=("tennis_atp",),
    )
    assert f is not None and f.source == "odds-api"
    # implied 1/1.5=0.667, 1/2.5=0.4 -> normalised Cobolli = 0.667/1.067 ~ 0.625
    assert 0.60 < f.prob < 0.65


def test_odds_fact_none_without_key_or_match():
    ev = _fake_event("Player A", 1.5, "Player B", 2.5)
    assert facts.odds_fact("Will Cobolli win?", api_key="", events_fetcher=lambda s, k: [ev]) is None
    none_match = facts.odds_fact(
        "Will Cobolli win?", api_key="x",
        events_fetcher=lambda s, k: [ev], sports=("tennis_atp",),
    )
    assert none_match is None  # no outcome name contains 'cobolli'


# --- feature wiring -------------------------------------------------------

def test_feature_vector_matches_declared_dim():
    m = Market(id="m", question="q", yes_price=0.5)
    assert len(build_features(m, None)) == FEATURE_DIM == len(FEATURE_NAMES)
    rep = ResearchReport(market_id="m", n_sources=3, fact_prob=0.8, fact_confidence=0.7)
    assert len(build_features(m, rep)) == FEATURE_DIM


def test_fact_prob_defaults_neutral_when_absent():
    m = Market(id="m", question="q", yes_price=0.5)
    feats = build_features(m, ResearchReport(market_id="m"))  # no fact
    assert feats[FEATURE_NAMES.index("fact_prob")] == 0.5
    assert feats[FEATURE_NAMES.index("fact_confidence")] == 0.0


# --- predict gate: a hard fact counts as real research --------------------

def _agent(tmp):
    s = Settings(db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz", edge_threshold=0.05)
    log = get_logger("t")
    return PredictAgent(s, Store(s.db_path), log, Predictor(log), Brain(s.brain_path, log))


def test_fact_only_research_is_tradable(tmp_path):
    # No text sources, but a confident hard fact (e.g. live BTC price) -> the
    # market is NOT skipped and a NO signal is produced against a high price.
    agent = _agent(tmp_path)
    cand = Candidate(market=Market(id="m", question="Will Bitcoin be above $64,000?", yes_price=0.85))
    reports = {"m": ResearchReport(market_id="m", n_sources=0, fact_prob=0.30, fact_confidence=1.0)}
    sigs = agent.run([cand], reports)
    assert len(sigs) == 1 and sigs[0].is_yes is False


def test_no_fact_no_text_still_skipped(tmp_path):
    agent = _agent(tmp_path)
    cand = Candidate(market=Market(id="m", question="q", yes_price=0.5))
    reports = {"m": ResearchReport(market_id="m", n_sources=0)}  # fact_prob None
    assert agent.run([cand], reports) == []

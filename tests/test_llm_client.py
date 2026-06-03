"""Tests for the provider-agnostic LLM layer (Option B).

Covers the shared interface (parsing identical across providers), the factory
dispatch, the ``available`` gate, the DeepSeek transport (mocked), and the
orchestrator hard-fail when no agent is configured.
"""
from pathlib import Path

import pytest

from tradebot.config import Settings
from tradebot.llm import (
    AnthropicClient,
    DeepSeekClient,
    LLMClient,
    LLMUnavailableError,
    make_client,
)
from tradebot.llm import deepseek as deepseek_mod


# --- shared interface: parsing is provider-independent ---

class _FakeLLM(LLMClient):
    """A concrete LLMClient whose transport returns canned text."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str, int]] = []

    @property
    def available(self) -> bool:
        return True

    def _complete(self, system, user, max_tokens=512):
        self.calls.append((system, user, max_tokens))
        return self.reply


def test_sentiment_parses_json():
    c = _FakeLLM('{"sentiment": 0.7, "narrative": "bullish"}')
    score, narr = c.sentiment("Will X?", ["headline a", "headline b"])
    assert score == 0.7 and narr == "bullish"


def test_sentiment_clamps_and_handles_prose_wrapping():
    c = _FakeLLM('Sure! {"sentiment": 5, "narrative": "very bullish"} hope that helps')
    score, narr = c.sentiment("Will X?", ["a"])
    assert score == 1.0 and narr == "very bullish"  # clamped to [-1, 1], JSON sliced out


def test_estimate_prob_parses_and_clamps():
    c = _FakeLLM('{"prob": 1.5, "confidence": 0.8, "reason": "strong"}')
    prob, conf, reason = c.estimate_prob("q", "narr", 0.4, "lessons")
    assert prob == 1.0 and conf == 0.8 and reason == "strong"


def test_decide_execution_parses():
    c = _FakeLLM('{"approved": false, "reason": "sentiment opposes side"}')
    approved, reason = c.decide_execution(
        question="q", is_yes=True, model_prob=0.6, brain_score=0.5, edge=0.1,
        rss_sentiment=-0.5, reddit_sentiment=0.4, rss_sources=3, reddit_sources=2,
    )
    assert approved is False and "opposes" in reason


def test_postmortem_parses():
    c = _FakeLLM('{"category": "risk", "cause": "thin edge", "recommendation": "wait"}')
    cat, cause, rec = c.postmortem("trade desc")
    assert (cat, cause, rec) == ("risk", "thin edge", "wait")


def test_unparseable_reply_returns_none():
    c = _FakeLLM("not json at all")
    assert c.sentiment("q", ["a"]) is None
    assert c.estimate_prob("q", "n", 0.4, "l") is None
    assert c.decide_execution(
        question="q", is_yes=True, model_prob=0.5, brain_score=0.5, edge=0.1,
        rss_sentiment=0.0, reddit_sentiment=0.0, rss_sources=0, reddit_sources=0,
    ) is None
    assert c.postmortem("d") is None


def test_empty_reply_returns_none():
    c = _FakeLLM("")
    assert c.sentiment("q", ["a"]) is None


# --- factory dispatch ---

def test_make_client_dispatches_by_provider():
    a = make_client(Settings(llm_provider="anthropic", anthropic_api_key="sk-test"))
    d = make_client(Settings(llm_provider="deepseek", deepseek_api_key="ds-test"))
    assert isinstance(a, AnthropicClient)
    assert isinstance(d, DeepSeekClient)


def test_make_client_provider_override_beats_settings():
    c = make_client(Settings(llm_provider="anthropic"), provider="deepseek")
    assert isinstance(c, DeepSeekClient)


def test_make_client_unknown_provider_raises():
    with pytest.raises(ValueError):
        make_client(Settings(llm_provider="gpt5"))


# --- available gate reflects key presence ---

def test_clients_not_available_without_key():
    assert make_client(Settings(llm_provider="anthropic", anthropic_api_key="")).available is False
    assert make_client(Settings(llm_provider="deepseek", deepseek_api_key="")).available is False


def test_deepseek_available_with_key():
    assert DeepSeekClient(api_key="ds-test").available is True


# --- DeepSeek transport (mocked httpx) ---

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_deepseek_complete_builds_request_and_parses(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp(
            {"choices": [{"message": {"content": '{"sentiment": -0.3, "narrative": "bearish"}'}}]}
        )

    monkeypatch.setattr(deepseek_mod.httpx, "post", fake_post)
    c = DeepSeekClient(api_key="ds-test", model="deepseek-chat")
    score, narr = c.sentiment("Will X?", ["a", "b"])

    assert (score, narr) == (-0.3, "bearish")
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer ds-test"
    assert captured["json"]["model"] == "deepseek-chat"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["role"] == "user"


def test_deepseek_transport_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(deepseek_mod.httpx, "post", boom)
    c = DeepSeekClient(api_key="ds-test")
    assert c.sentiment("q", ["a"]) is None


# --- orchestrator hard-fail: no agent -> raise before doing anything ---

def test_orchestrator_hard_fails_without_agent(tmp_path):
    from tradebot.log import get_logger
    from tradebot.orchestrator import Orchestrator

    s = Settings(
        llm_provider="deepseek", deepseek_api_key="", anthropic_api_key="",
        db_path=Path(tmp_path) / "t.db", brain_path=Path(tmp_path) / "b.npz",
    )
    with pytest.raises(LLMUnavailableError):
        Orchestrator(s, get_logger("t"))

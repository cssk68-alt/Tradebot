"""Provider-agnostic LLM client.

`LLMClient` is the abstract interface every provider (Anthropic, DeepSeek, ...)
implements. ALL prompt/parse logic lives here in the base class — a concrete
provider only has to supply two things:

  * ``available``  — whether an API key (and SDK/transport) is present, and
  * ``_complete(system, user, max_tokens)`` — one chat round-trip returning text.

That keeps the four tasks (sentiment, probability estimate, BrainManager verdict,
postmortem) identical across providers, so swapping ``anthropic`` ↔ ``deepseek``
is a pure config change with no logic difference.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Optional


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM agent is configured but one is required to run.

    The bot is a coupled Brain+Agent system: without an LLM there are no input
    signals for the brain and no calibration feedback for the agent, so a run is
    pointless. Entry points raise this BEFORE a cycle starts (hard-fail)."""


class LLMClient(ABC):
    """Abstract LLM agent. Subclass and implement ``available`` + ``_complete``."""

    # Token pricing in EUR per token (0 = unknown). Providers override these and
    # call ``_add_usage`` inside ``_complete`` so a run can be capped by a budget.
    PRICE_IN_EUR: float = 0.0
    PRICE_OUT_EUR: float = 0.0

    def _add_usage(self, prompt_tokens, completion_tokens) -> None:
        self.prompt_tokens = getattr(self, "prompt_tokens", 0) + int(prompt_tokens or 0)
        self.completion_tokens = getattr(self, "completion_tokens", 0) + int(completion_tokens or 0)

    @property
    def cost_eur(self) -> float:
        """Accumulated spend in EUR since this client was created."""
        return (
            getattr(self, "prompt_tokens", 0) * self.PRICE_IN_EUR
            + getattr(self, "completion_tokens", 0) * self.PRICE_OUT_EUR
        )

    @property
    @abstractmethod
    def available(self) -> bool:
        """True iff this client can actually make calls (key + transport present)."""

    @abstractmethod
    def _complete(self, system: str, user: str, max_tokens: int = 512) -> Optional[str]:
        """One chat round-trip. Return the text, or ``None`` on any failure."""

    # --- tasks (provider-independent) ---

    def sentiment(self, question: str, texts: list[str]):
        joined = "\n".join(f"- {t}" for t in texts[:15])
        out = self._complete(
            "You are a market sentiment analyst. Respond ONLY with compact JSON "
            '{"sentiment": <float -1..1>, "narrative": "<one sentence>"}.',
            f"Market: {question}\nSources:\n{joined}",
            max_tokens=200,
        )
        if not out:
            return None
        try:
            d = json.loads(_json_slice(out))
            return max(-1.0, min(1.0, float(d["sentiment"]))), str(d.get("narrative", ""))
        except Exception:
            return None

    def estimate_prob(self, question: str, narrative: str, market_price: float, lessons: str):
        out = self._complete(
            "You are a calibrated forecaster for binary prediction markets. Respond ONLY "
            'with JSON {"prob": <0..1>, "confidence": <0..1>, "reason": "<short>"}.',
            f"Question: {question}\nMarket-implied YES prob: {market_price:.2f}\n"
            f"Narrative: {narrative}\nPast lessons:\n{lessons}",
            max_tokens=250,
        )
        if not out:
            return None
        try:
            d = json.loads(_json_slice(out))
            return (
                max(0.0, min(1.0, float(d["prob"]))),
                max(0.0, min(1.0, float(d.get("confidence", 0.5)))),
                str(d.get("reason", "")),
            )
        except Exception:
            return None

    def decide_execution(
        self,
        question: str,
        is_yes: bool,
        model_prob: float,
        brain_score: float,
        edge: float,
        rss_sentiment: float,
        reddit_sentiment: float,
        rss_sources: int,
        reddit_sources: int,
    ) -> Optional[tuple[bool, str]]:
        """BrainManager (Stage 5): final approve/veto verdict on a trade.

        The agent sees the math model, the MLP veto and the SEPARATE Reddit/RSS
        sentiment and looks for logical contradictions. Returns
        ``(approved, reason)`` or ``None`` if no parseable verdict came back."""
        side = "YES" if is_yes else "NO"
        out = self._complete(
            "You are the BrainManager, the final risk meta-controller for a prediction-market "
            "trading bot. You receive the XGBoost probability that YES resolves, the neural-net "
            "(MLP) veto score in [0,1] (higher = more confident the trade wins), the executable "
            "edge, and SEPARATE Reddit vs RSS sentiment. Approve only if the signals are mutually "
            "consistent; veto if you detect a logical contradiction — e.g. sentiment strongly "
            "opposes the traded side, the MLP veto score is low while the edge is thin, or Reddit "
            "hype contradicts the RSS news signal. Respond ONLY with JSON "
            '{"approved": <true|false>, "reason": "<one sentence>"}.',
            f"Traded side: {side}\n"
            f"XGBoost P(YES): {model_prob:.3f}\n"
            f"MLP veto score: {brain_score:.3f}\n"
            f"Executable edge: {edge:+.3f}\n"
            f"RSS sentiment: {rss_sentiment:+.2f} from {rss_sources} sources\n"
            f"Reddit sentiment: {reddit_sentiment:+.2f} from {reddit_sources} sources",
            max_tokens=200,
        )
        if not out:
            return None
        try:
            d = json.loads(_json_slice(out))
            return bool(d["approved"]), str(d.get("reason", ""))
        except Exception:
            return None

    def postmortem(self, trade_desc: str):
        out = self._complete(
            "You are five expert analysts (data, signal, risk, market, timing) running a "
            "trade postmortem. Identify the single biggest lesson. Respond ONLY with JSON "
            '{"category":"<word>","cause":"<short>","recommendation":"<short>"}.',
            trade_desc,
            max_tokens=250,
        )
        if not out:
            return None
        try:
            d = json.loads(_json_slice(out))
            return d.get("category", "general"), d.get("cause", ""), d.get("recommendation", "")
        except Exception:
            return None


def _json_slice(s: str) -> str:
    a, b = s.find("{"), s.rfind("}")
    return s[a : b + 1] if a >= 0 and b > a else s

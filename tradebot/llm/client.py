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
import os
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

from tradebot.config import DATA_DIR
from tradebot.log import get_logger

# Full transcript of every model call (exact prompt + answer + token use), one
# JSON object per line, so the otherwise-invisible LLM conversation is auditable
# after the fact. The console shows only a short one-liner; this file has it all.
_LLM_LOG_LOCK = threading.Lock()


def _llm_log_path() -> Path:
    """Where the full LLM transcript is written.

    Defaults to ``data/llm_log.jsonl`` but can be redirected with the
    ``TRADEBOT_LLM_LOG`` env var, so test runs (see ``tests/conftest.py``) write to
    a throwaway file instead of polluting the real production log. Resolved per
    call — not cached at import — so the override always wins regardless of import
    order."""
    override = os.environ.get("TRADEBOT_LLM_LOG", "").strip()
    return Path(override) if override else (DATA_DIR / "llm_log.jsonl")


def _ascii(s: str) -> str:
    """cp1252-safe text for the console one-liner; the file keeps full UTF-8."""
    return s.encode("ascii", "replace").decode("ascii")


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

    def _complete_logged(
        self, system: str, user: str, max_tokens: int = 512, *, task: str = "", ctx: str = ""
    ) -> Optional[str]:
        """``_complete`` with retry (3 attempts, 60 s timeout per attempt), START/DONE
        logging, and watchdog heartbeats so a hung provider is visible immediately."""
        from tradebot import watchdog as _wd

        _log = get_logger("llm")
        short_ctx = ctx if len(ctx) <= 50 else ctx[:50] + "..."
        label = task or "llm"

        _log.info("START %s | %s", label, _ascii(short_ctx))

        out: Optional[str] = None
        before_in = getattr(self, "prompt_tokens", 0)
        before_out = getattr(self, "completion_tokens", 0)

        for attempt in range(3):
            _wd.beat()  # reset watchdog before each attempt
            if attempt > 0:
                _log.warning("LLM retry %d/3 — task=%s ctx=%s", attempt + 1, label, _ascii(short_ctx))
                time.sleep(2 ** attempt)  # 2 s, 4 s back-off
            out = self._complete(system, user, max_tokens)
            if out is not None:
                break
            _log.warning("LLM attempt %d/3 returned no answer — task=%s", attempt + 1, label)

        _wd.beat()  # still alive after all attempts
        din = getattr(self, "prompt_tokens", 0) - before_in
        dout = getattr(self, "completion_tokens", 0) - before_out

        # File: complete machine-readable transcript (UTF-8, untruncated).
        try:
            path = _llm_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "task": task,
                "ctx": ctx,
                "model": getattr(self, "model", ""),
                "tokens_in": din,
                "tokens_out": dout,
                "system": system,
                "user": user,
                "answer": out,
            }
            with _LLM_LOG_LOCK, open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

        answer = out if out is not None else "(no answer - all attempts failed)"
        ans1 = " ".join(answer.split())
        short_ans = ans1 if len(ans1) <= 100 else ans1[:100] + "..."
        _log.info("DONE %s | %s → %s (in=%d out=%d tok)", label, _ascii(short_ctx), _ascii(short_ans), din, dout)
        return out

    # --- tasks (provider-independent) ---

    def sentiment(self, question: str, texts: list[str]):
        joined = "\n".join(f"- {t}" for t in texts[:15])
        out = self._complete_logged(
            "You are a market sentiment analyst. Respond ONLY with compact JSON "
            '{"sentiment": <float -1..1>, "narrative": "<one sentence>"}.',
            f"Market: {question}\nSources:\n{joined}",
            max_tokens=220, task="sentiment", ctx=question,
        )
        if not out:
            return None
        try:
            d = json.loads(_json_slice(out))
            return max(-1.0, min(1.0, float(d["sentiment"]))), str(d.get("narrative", ""))
        except Exception:
            return None

    def estimate_prob(self, question: str, narrative: str, market_price: float, lessons: str):
        # ``reason`` is repurposed as a SHORT structured handoff to the BrainManager
        # (Stage 5): <=2 sentences naming the main driver and the main risk, so the
        # risk meta-controller decides on the forecaster's actual reasoning, not just
        # the numbers. It rides the existing return tuple -> no signature change.
        out = self._complete_logged(
            "You are a calibrated forecaster for binary prediction markets. Respond ONLY "
            'with JSON {"prob": <0..1>, "confidence": <0..1>, "reason": "<=2 sentences: the '
            'single biggest driver of your estimate AND the main risk to it, written for the '
            'downstream risk manager to act on"}.',
            f"Question: {question}\nMarket-implied YES prob: {market_price:.2f}\n"
            f"Narrative: {narrative}\nPast lessons:\n{lessons}",
            max_tokens=275, task="forecast", ctx=question,
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
        risk_appetite: str = "",
        forecast_context: str = "",
    ) -> Optional[tuple[bool, str]]:
        """BrainManager (Stage 5): final approve/veto verdict on a trade.

        The agent sees the math model, the MLP veto and the SEPARATE Reddit/RSS
        sentiment and looks for logical contradictions. Returns
        ``(approved, reason)`` or ``None`` if no parseable verdict came back.

        ``risk_appetite`` is the operator's Risk-Adjuster 'Ping' (see
        risk/adjuster.py): a free-text instruction appended to the system prompt
        that tells the agent how bold to be. Empty string == today's default
        (conservative) behaviour, so the prompt is unchanged when the knob is 0.

        ``forecast_context`` is the forecaster's <=2-sentence handoff (its ``reason``):
        the main driver + main risk. Appended to the user prompt only when present,
        so the agent can judge the actual reasoning, not just the numbers. Empty
        string keeps the prompt byte-identical to before."""
        side = "YES" if is_yes else "NO"
        system = (
            "You are the BrainManager, the final risk meta-controller for a prediction-market "
            "trading bot. You receive the XGBoost probability that YES resolves, the neural-net "
            "(MLP) veto score in [0,1] — P(the TRADED side wins); this is NOT P(YES). "
            "On a NO trade, a score of 0.0 means the MLP predicts the NO bet will LOSE, not that "
            "it confirms the NO side. Higher always means more confidence in the specific traded "
            "direction; lower always means less. The executable "
            "edge, and SEPARATE social (Bluesky/HN/Lemmy/Reddit) vs RSS sentiment. Approve only if "
            "the signals are mutually consistent; veto if you detect a logical contradiction — e.g. "
            "sentiment strongly opposes the traded side, the MLP veto score is low while the edge "
            "is thin, or social hype contradicts the RSS news signal. Respond ONLY with JSON "
            '{"approved": <true|false>, "reason": "<one sentence>"}.'
        )
        if risk_appetite:
            system += " " + risk_appetite
        user = (
            f"Traded side: {side}\n"
            f"XGBoost P(YES): {model_prob:.3f}\n"
            f"MLP veto score P({side} wins): {brain_score:.3f}\n"
            f"Executable edge: {edge:+.3f}\n"
            f"RSS sentiment: {rss_sentiment:+.2f} from {rss_sources} sources\n"
            f"Social sentiment: {reddit_sentiment:+.2f} from {reddit_sources} sources"
        )
        if forecast_context:
            user += f"\nForecaster's note: {forecast_context}"
        out = self._complete_logged(system, user, max_tokens=220, task="brainmanager", ctx=question)
        if not out:
            return None
        try:
            d = json.loads(_json_slice(out))
            return bool(d["approved"]), str(d.get("reason", ""))
        except Exception:
            return None

    def meta_learn(self, trade_history: str) -> Optional[dict]:
        """Meta-Learning LLM ("Anwalt / For-The-Future Learner").

        Analyzes a batch of completed trades (input -> decision -> outcome) and
        returns structured insights about recurring structural patterns. This is
        strictly OBSERVER-ONLY — never influences live decisions.

        Returns dict with keys:
          - insight_summary: list[str] (max 5)
          - confidence_of_insight: float (0..1)
          - category_tags: list[str]
          - suggested_future_hypotheses: list[str] (NOT rules)
        """
        out = self._complete_logged(
            "You are \"Der Anwalt\" — the For-The-Future Learner. Your ONLY role is to "
            "observe and analyze completed trades. You do NOT make decisions, you do NOT "
            "modify risk, you do NOT block or approve trades. You detect recurring structural "
            "patterns and identify weaknesses in the trading system.\n\n"
            "Analyze this batch of trades. Compare LLM predictions vs real outcomes. "
            "Look for: systematic over/undervaluation in certain categories, sentiment lag "
            "in fast regimes, pattern engine underreaction to volatility, consistency issues.\n\n"
            "Respond ONLY with JSON: "
            '{"insight_summary": ["<max 5 short bullet points>"], '
            '"confidence_of_insight": <0..1>, '
            '"category_tags": ["<tag1>", "<tag2>", ...], '
            '"suggested_future_hypotheses": ["<not rules, just hypotheses for future observation>"]}',
            trade_history,
            max_tokens=500, task="meta_learn", ctx="batch analysis",
        )
        if not out:
            return None
        try:
            d = json.loads(_json_slice(out))
            return {
                "insight_summary": d.get("insight_summary", [])[:5],
                "confidence_of_insight": max(0.0, min(1.0, float(d.get("confidence_of_insight", 0.0)))),
                "category_tags": d.get("category_tags", []),
                "suggested_future_hypotheses": d.get("suggested_future_hypotheses", []),
            }
        except Exception:
            return None

    def postmortem(self, trade_desc: str):
        out = self._complete_logged(
            "You are five expert analysts (data, signal, risk, market, timing) running a "
            "trade postmortem. Identify the single biggest lesson. Respond ONLY with JSON "
            '{"category":"<word>","cause":"<short>","recommendation":"<short>"}.',
            trade_desc,
            max_tokens=275, task="postmortem", ctx=trade_desc[:60],
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

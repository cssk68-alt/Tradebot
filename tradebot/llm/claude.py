"""Anthropic / Claude wrapper with graceful degradation and prompt caching.

If no API key or SDK is present, `available` is False and callers fall back to
heuristics, so the bot still runs in paper mode without a key.
"""
from __future__ import annotations

import json
from typing import Optional

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class Claude:
    def __init__(self, api_key: str = "", model: str = DEFAULT_MODEL):
        self.model = model
        self._client = None
        if api_key:
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=api_key)
            except Exception:
                self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def _complete(self, system: str, user: str, max_tokens: int = 512) -> Optional[str]:
        if not self.available:
            return None
        try:
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        except Exception:
            return None

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

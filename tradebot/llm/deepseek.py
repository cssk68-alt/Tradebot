"""DeepSeek implementation of :class:`LLMClient`.

DeepSeek exposes an OpenAI-compatible chat-completions endpoint, so a single
``httpx`` POST is enough — no extra SDK. Prompts and parsing are shared in
``tradebot.llm.client``; only the transport lives here.

DeepSeek is ~10x cheaper than Claude Haiku, which is why it is the default
provider for the paper-trading / learning phase.
"""
from __future__ import annotations

from typing import Optional

from tradebot.llm.client import LLMClient

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekClient(LLMClient):
    # deepseek-v4-flash pricing (what deepseek-chat maps to), EUR per token. Input
    # at the cache-MISS rate ($0.14/1M) for a conservative cap; output $0.28/1M;
    # USD->EUR ~0.92. Source: api-docs.deepseek.com/quick_start/pricing
    PRICE_IN_EUR = 0.14 / 1_000_000 * 0.92
    PRICE_OUT_EUR = 0.28 / 1_000_000 * 0.92

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ):
        self._api_key = (api_key or "").strip()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self._api_key) and httpx is not None

    def _complete(self, system: str, user: str, max_tokens: int = 512) -> Optional[str]:
        if not self.available:
            return None
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            u = data.get("usage") or {}
            self._add_usage(u.get("prompt_tokens"), u.get("completion_tokens"))
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

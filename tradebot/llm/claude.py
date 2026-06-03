"""Anthropic (Claude) implementation of :class:`LLMClient`.

Only the transport lives here — prompts and parsing are shared in
``tradebot.llm.client``. Uses prompt caching on the system block. ``Claude`` is
kept as a backwards-compatible alias for ``AnthropicClient``.
"""
from __future__ import annotations

from typing import Optional

from tradebot.llm.client import LLMClient

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicClient(LLMClient):
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


# Backwards-compatible alias (old code imported ``Claude``).
Claude = AnthropicClient

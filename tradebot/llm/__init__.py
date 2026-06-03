"""Provider-agnostic LLM layer.

Pick a provider with ``LLM_PROVIDER`` in ``.env`` (``anthropic`` | ``deepseek``)
and build the client via :func:`make_client`. Everything downstream depends on
the :class:`LLMClient` interface, never on a concrete provider.
"""
from __future__ import annotations

from typing import Optional

from tradebot.llm.claude import AnthropicClient, Claude
from tradebot.llm.client import LLMClient, LLMUnavailableError
from tradebot.llm.deepseek import DeepSeekClient

__all__ = [
    "LLMClient",
    "LLMUnavailableError",
    "AnthropicClient",
    "Claude",
    "DeepSeekClient",
    "make_client",
]


def make_client(settings=None, provider: Optional[str] = None) -> LLMClient:
    """Build the configured LLM client.

    ``provider`` (or ``settings.llm_provider``) selects the implementation. The
    returned client may report ``available == False`` if its API key is missing;
    callers that require an agent should check that and raise
    :class:`LLMUnavailableError` (the orchestrator does this as a hard-fail)."""
    provider = (provider or (getattr(settings, "llm_provider", "") if settings else "") or "anthropic").lower()

    if provider == "anthropic":
        return AnthropicClient(
            api_key=getattr(settings, "anthropic_api_key", "") if settings else "",
            model=getattr(settings, "anthropic_model", None) or "claude-haiku-4-5-20251001",
        )
    if provider == "deepseek":
        return DeepSeekClient(
            api_key=getattr(settings, "deepseek_api_key", "") if settings else "",
            model=getattr(settings, "deepseek_model", None) or "deepseek-chat",
        )
    raise ValueError(
        f"Unknown LLM_PROVIDER {provider!r} — expected 'anthropic' or 'deepseek'."
    )

"""General web search as a third research source.

Provider-agnostic, like the LLM client: it uses the best backend that is actually
available and otherwise degrades to an EMPTY list (never fabricated text):

  1. Tavily   — if a TAVILY_API_KEY is configured (built for agents, clean text).
  2. DuckDuckGo — if the optional ``ddgs`` / ``duckduckgo_search`` package exists
                  (no key needed).
  3. GDELT    — free public news API, no key; always tried as a last resort so
                web search returns *something* out of the box.

Returns a list of short text snippets that are sentiment-scored exactly like the
RSS and Reddit sources, so the brain keeps web search as its own separate signal.
Every backend is wrapped so a failure can only ever yield ``[]``.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

try:  # shared transport, already a dependency (used by the DeepSeek client)
    import httpx
except Exception:  # pragma: no cover
    httpx = None

_UA = "Mozilla/5.0 (compatible; tradebot/0.1; +research)"


def search(query: str, api_key: str = "", limit: int = 8, timeout: float = 8.0) -> list[str]:
    """Best-effort web search. Never raises; returns ``[]`` when nothing works."""
    if not query.strip():
        return []
    if api_key:
        out = _tavily(query, api_key, limit, timeout)
        if out:
            return out[:limit]
    out = _duckduckgo(query, limit)
    if out:
        return out[:limit]
    return _gdelt(query, limit, timeout)[:limit]


# --- backends -------------------------------------------------------------

def _tavily(query: str, api_key: str, limit: int, timeout: float) -> list[str]:
    if httpx is None:
        return []
    try:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return _parse_tavily(r.json())
    except Exception:
        return []


def _duckduckgo(query: str, limit: int) -> list[str]:
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:
        try:
            from duckduckgo_search import DDGS  # type: ignore  # older name
        except Exception:
            return []
    try:
        with DDGS() as ddg:
            return _parse_ddg(list(ddg.text(query, max_results=limit)))
    except Exception:
        return []


def _gdelt(query: str, limit: int, timeout: float) -> list[str]:
    # Free, no key. GDELT DOC 2.0 article search (recent global news coverage).
    try:
        qs = urllib.parse.urlencode(
            {
                "query": query,
                "mode": "ArtList",
                "maxrecords": max(1, min(limit, 75)),
                "format": "json",
                "sort": "DateDesc",
            }
        )
        url = f"https://api.gdeltproject.org/api/v2/doc/doc?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return _parse_gdelt(data)
    except Exception:
        return []


# --- pure parsers (unit-tested without network) ---------------------------

def _parse_tavily(data: dict) -> list[str]:
    out: list[str] = []
    for it in (data or {}).get("results", []):
        text = f"{it.get('title', '')} {it.get('content', '')}".strip()
        if text:
            out.append(text[:500])
    return out


def _parse_ddg(items: list) -> list[str]:
    out: list[str] = []
    for it in items or []:
        text = f"{it.get('title', '')} {it.get('body', '')}".strip()
        if text:
            out.append(text[:500])
    return out


def _parse_gdelt(data: dict) -> list[str]:
    out: list[str] = []
    for art in (data or {}).get("articles", []):
        title = (art.get("title") or "").strip()
        if title:
            out.append(title[:500])
    return out

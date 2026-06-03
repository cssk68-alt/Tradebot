"""Free, key-less SOCIAL / forum sentiment sources — the Reddit replacement.

Reddit's public API now requires OAuth and frequently returns 403, so without
configured creds it yields nothing and the whole 'social discussion' research
channel goes neutral. These sources fill that gap with NO API key, NO signup and
NO paid tier — they are free and stay free:

  * Bluesky    — ``app.bsky.feed.searchPosts`` on the public AppView (no auth,
                 ~3000 req / 5 min per IP). Strong real-time politics/crypto/news.
  * Hacker News — Algolia search API (no key, no documented daily cap). Excellent
                 for tech / AI / crypto / big-tech markets.
  * Lemmy      — federated Reddit-like; public ``/api/v3/search`` on a large
                 instance (default lemmy.world). Reddit-style discussion depth.

Every backend is best-effort and wrapped so any failure can only ever yield
``[]`` — a dead source never breaks a research cycle. The returned snippets are
sentiment-scored exactly like the RSS and web sources, so the brain keeps a
'social discussion' signal of its own.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (compatible; tradebot/0.1; +research)"


def search_social(
    query: str, limit: int = 8, timeout: float = 8.0, lemmy_instance: str = "lemmy.world"
) -> list[str]:
    """Aggregate free social/forum text for ``query``. Never raises; returns ``[]``
    when nothing works. Results are de-duplicated and each snippet capped at 500
    chars; the combined list is capped so LLM scoring stays cheap."""
    if not query.strip():
        return []
    out: list[str] = []
    for backend in (_bluesky, _hackernews, _lemmy):
        try:
            if backend is _lemmy:
                out += backend(query, limit, timeout, lemmy_instance)
            else:
                out += backend(query, limit, timeout)
        except Exception:
            continue  # one dead source must never sink the others
    # De-dup (preserve order) and cap. 15 matches the LLM sentiment truncation.
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            deduped.append(t[:500])
    return deduped[:15]


# --- backends -------------------------------------------------------------

def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _bluesky(query: str, limit: int, timeout: float) -> list[str]:
    qs = urllib.parse.urlencode({"q": query, "limit": max(1, min(limit, 25))})
    url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?{qs}"
    return _parse_bluesky(_get_json(url, timeout))


def _hackernews(query: str, limit: int, timeout: float) -> list[str]:
    qs = urllib.parse.urlencode(
        {"query": query, "tags": "story", "hitsPerPage": max(1, min(limit, 25))}
    )
    url = f"https://hn.algolia.com/api/v1/search?{qs}"
    return _parse_hn(_get_json(url, timeout))


def _lemmy(query: str, limit: int, timeout: float, instance: str = "lemmy.world") -> list[str]:
    qs = urllib.parse.urlencode(
        {"q": query, "type_": "Posts", "sort": "New", "limit": max(1, min(limit, 25))}
    )
    url = f"https://{instance}/api/v3/search?{qs}"
    return _parse_lemmy(_get_json(url, timeout))


# --- pure parsers (unit-tested without network) ---------------------------

def _parse_bluesky(data: dict) -> list[str]:
    out: list[str] = []
    for post in (data or {}).get("posts", []):
        text = ((post.get("record") or {}).get("text") or "").strip()
        if text:
            out.append(text)
    return out


def _parse_hn(data: dict) -> list[str]:
    out: list[str] = []
    for hit in (data or {}).get("hits", []):
        text = f"{hit.get('title') or ''} {hit.get('story_text') or ''}".strip()
        if text:
            out.append(text)
    return out


def _parse_lemmy(data: dict) -> list[str]:
    out: list[str] = []
    for item in (data or {}).get("posts", []):
        post = item.get("post") or {}
        text = f"{post.get('name') or ''} {post.get('body') or ''}".strip()
        if text:
            out.append(text)
    return out

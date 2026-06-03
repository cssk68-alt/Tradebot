"""Public Reddit search via the JSON endpoint (no API key)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (compatible; tradebot/0.1; +research)"


def search_reddit(query: str, limit: int = 8, timeout: float = 8.0) -> list[str]:
    try:
        qs = urllib.parse.urlencode({"q": query, "limit": limit, "sort": "new"})
        url = f"https://www.reddit.com/search.json?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out: list[str] = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            text = f"{d.get('title', '')} {d.get('selftext', '')}".strip()
            if text:
                out.append(text[:500])
        return out
    except Exception:
        return []

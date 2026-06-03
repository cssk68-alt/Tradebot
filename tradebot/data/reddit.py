"""Reddit search via OAuth (free app key) or fallback to silence."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Optional

_UA = "Mozilla/5.0 (compatible; tradebot/0.1; +research)"

# Cache token + expiry to avoid re-auth on every call in a cycle.
_token_cache: Optional[str] = None
_token_expiry: float = 0.0


def _get_token(client_id: str, client_secret: str, timeout: float = 8.0) -> Optional[str]:
    """Obtain a Reddit OAuth token. Cached internally for 3300s (< 1hr limit)."""
    global _token_cache, _token_expiry
    now = time.time()
    if _token_cache and now < _token_expiry:
        return _token_cache
    try:
        auth = (client_id, client_secret)
        data = urllib.parse.urlencode({"grant_type": "client_credentials"})
        req = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token",
            data=data.encode("utf-8"),
            headers={"User-Agent": _UA},
        )
        with urllib.request.urlopen(req, auth=auth, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        _token_cache = payload["access_token"]
        _token_expiry = now + 3300
        return _token_cache
    except Exception:
        return None


def search_reddit(
    query: str, client_id: str = "", client_secret: str = "", limit: int = 8, timeout: float = 8.0
) -> list[str]:
    """Reddit search via OAuth. Without client_id+secret, returns [] (no public fallback)."""
    if not client_id or not client_secret:
        return []
    token = _get_token(client_id, client_secret, timeout)
    if not token:
        return []
    try:
        qs = urllib.parse.urlencode({"q": query, "limit": limit, "sort": "new"})
        url = f"https://oauth.reddit.com/search?{qs}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"bearer {token}",
                "User-Agent": _UA,
            },
        )
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

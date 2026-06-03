"""News headlines via Google News RSS. Uses feedparser if installed, else stdlib XML."""
from __future__ import annotations

import urllib.parse
import urllib.request
from xml.etree import ElementTree as ET

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_headlines(query: str, limit: int = 8, timeout: float = 8.0) -> list[str]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-US"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        return []
    titles = _parse_feedparser(raw) or _parse_stdlib(raw)
    return [t for t in titles if t][:limit]


def _parse_feedparser(raw: bytes) -> list[str]:
    try:
        import feedparser  # type: ignore

        feed = feedparser.parse(raw)
        return [e.get("title", "") for e in feed.entries]
    except Exception:
        return []


def _parse_stdlib(raw: bytes) -> list[str]:
    try:
        root = ET.fromstring(raw)
        titles = [t.text or "" for t in root.iter("title")]
        return titles[1:]  # skip the channel <title>
    except Exception:
        return []

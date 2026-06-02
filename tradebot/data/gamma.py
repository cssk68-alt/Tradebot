"""Polymarket Gamma API client — read-only market data, no auth required.

Falls back to built-in fixtures when the network/API is unavailable so the
pipeline always runs end-to-end.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from tradebot.data.fixtures import sample_markets
from tradebot.models import Market

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

BASE = "https://gamma-api.polymarket.com"

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


class GammaClient:
    def __init__(self, log, limit: int = 300, page_size: int = 100, timeout: float = 10.0):
        self.log = log
        self.limit = limit
        self.page_size = page_size
        self.timeout = timeout

    def fetch_markets(self) -> list[Market]:
        try:
            markets = self._fetch_live()
            if markets:
                self.log.info("Gamma: fetched %d active markets", len(markets))
                return markets
            raise RuntimeError("no markets returned")
        except Exception as e:
            self.log.warning("Gamma unavailable (%s) — using built-in fixtures", e)
            return sample_markets()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4), reraise=True)
    def _get(self, path: str, params: dict):
        if httpx is None:
            raise RuntimeError("httpx not installed")
        r = httpx.get(
            BASE + path, params=params, timeout=self.timeout, headers=_BROWSER_HEADERS,
        )
        r.raise_for_status()
        return r.json()

    def _fetch_live(self) -> list[Market]:
        out: list[Market] = []
        offset = 0
        while len(out) < self.limit:
            data = self._get(
                "/markets",
                {
                    "active": "true", "closed": "false",
                    "limit": self.page_size, "offset": offset,
                    "order": "volume24hr", "ascending": "false",
                },
            )
            if not isinstance(data, list) or not data:
                break
            for obj in data:
                m = self._parse(obj)
                if m:
                    out.append(m)
            offset += self.page_size
            if len(data) < self.page_size:
                break
        return out[: self.limit]

    def get_resolution(self, market_id: str) -> Optional[bool]:
        """True if YES won, False if NO, None if not yet resolved."""
        try:
            data = self._get(f"/markets/{market_id}", {})
        except Exception:
            return None
        if isinstance(data, list):
            data = data[0] if data else {}
        if not data.get("closed"):
            return None
        prices = _loads(data.get("outcomePrices"))
        outcomes = _loads(data.get("outcomes"))
        if not prices or not outcomes:
            return None
        yi = _yes_index(outcomes)
        try:
            return float(prices[yi]) >= 0.5
        except Exception:
            return None

    @staticmethod
    def _parse(obj: dict) -> Optional[Market]:
        try:
            prices = _loads(obj.get("outcomePrices")) or []
            outcomes = _loads(obj.get("outcomes")) or []
            tokens = _loads(obj.get("clobTokenIds")) or []
            yi = _yes_index(outcomes)
            ni = 1 - yi if len(prices) > 1 else 0
            yes_price = float(prices[yi]) if len(prices) > yi else 0.5
            end_dt = None
            end = obj.get("endDate")
            if end:
                try:
                    end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                except Exception:
                    end_dt = None
            return Market(
                id=str(obj.get("id") or obj.get("conditionId") or obj.get("slug")),
                question=obj.get("question") or obj.get("title") or "",
                yes_token_id=str(tokens[yi]) if len(tokens) > yi else "",
                no_token_id=str(tokens[ni]) if len(tokens) > ni else "",
                yes_price=yes_price,
                volume_24h=_f(obj.get("volume24hr") or obj.get("volume")),
                liquidity=_f(obj.get("liquidity") or obj.get("liquidityNum")),
                end_date=end_dt,
                best_bid=_opt_f(obj.get("bestBid")),
                best_ask=_opt_f(obj.get("bestAsk")),
            )
        except Exception:
            return None


def _loads(v):
    if v is None:
        return None
    if isinstance(v, list):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def _yes_index(outcomes) -> int:
    for i, o in enumerate(outcomes or []):
        if str(o).strip().lower() in ("yes", "true"):
            return i
    return 0


def _f(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _opt_f(v):
    try:
        return None if v is None else float(v)
    except Exception:
        return None

"""Polymarket Gamma API client — read-only market data, no auth required.

HARD-FAIL policy: there is NO fixtures fallback. If real markets cannot be
fetched, ``fetch_markets`` raises ``DataUnavailableError`` so a trading cycle can
never run on synthetic/sample data. Settlement is resilient by contrast: a failed
resolution query returns ``ResolutionStatus.ERROR`` (logged, trade stays open)
rather than silently inventing an outcome.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from tradebot.models import Market, Resolution, ResolutionStatus

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


class DataUnavailableError(RuntimeError):
    """Raised when real market data cannot be obtained — the cycle must abort."""


class GammaClient:
    def __init__(self, log, limit: int = 300, page_size: int = 100, timeout: float = 10.0):
        self.log = log
        self.limit = limit
        self.page_size = page_size
        self.timeout = timeout

    def fetch_markets(self) -> list[Market]:
        """Return real active markets, or raise — never fall back to fixtures."""
        try:
            markets = self._fetch_live()
        except Exception as e:
            raise DataUnavailableError(f"Gamma market fetch failed: {e}") from e
        if not markets:
            raise DataUnavailableError("Gamma returned no active markets")
        self.log.info("Gamma: fetched %d active markets", len(markets))
        return markets

    def _get(self, path: str, params: dict):
        if httpx is None:
            raise RuntimeError("httpx not installed")
        last_err: Optional[BaseException] = None
        for _ in range(3):  # retry transport/5xx only; 4xx (e.g. 403) fails fast
            try:
                r = httpx.get(
                    BASE + path, params=params, timeout=self.timeout, headers=_BROWSER_HEADERS,
                )
            except Exception as e:
                last_err = e
                continue
            if r.status_code >= 500:
                last_err = RuntimeError(f"HTTP {r.status_code}")
                continue
            r.raise_for_status()
            return r.json()
        raise last_err or RuntimeError("request failed")

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

    def fetch_market(self, market_id: str) -> Optional[Market]:
        """Direct single-market fetch (``/markets/{id}``) for a position whose market
        has dropped out of the bulk ``fetch_markets()`` list (below the liquidity
        filter, or temporarily inactive). Returns a Market with the CURRENT book.

        Returns None when the market is unknown or carries no usable price data — so
        a caller never closes a position at the fabricated 0.5 default ``_parse``
        would otherwise yield. Resolved/closed markets are handled by
        ``get_resolution`` instead; this path is for markets that are still trading.
        """
        try:
            data = self._get(f"/markets/{market_id}", {})
        except Exception as e:
            self.log.warning("Gamma single-market fetch failed for %s: %s", market_id, e)
            return None
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict) or not _loads(data.get("outcomePrices")):
            return None
        return self._parse(data)

    def get_resolution(self, market_id: str) -> Resolution:
        """Typed settlement status (OPEN / YES / NO / CANCELED / AMBIGUOUS / ERROR).

        API/network failures map to ERROR (not OPEN) so they are distinguishable
        from a market that simply has not resolved yet."""
        try:
            data = self._get(f"/markets/{market_id}", {})
        except Exception as e:
            return Resolution(status=ResolutionStatus.ERROR, reason=str(e))
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return Resolution(status=ResolutionStatus.ERROR, reason="unexpected payload shape")
        if not data.get("closed"):
            return Resolution(status=ResolutionStatus.OPEN)

        prices = _loads(data.get("outcomePrices"))
        outcomes = _loads(data.get("outcomes"))
        if not prices or not outcomes or len(prices) < 2:
            return Resolution(
                status=ResolutionStatus.AMBIGUOUS, reason="closed but missing prices/outcomes"
            )
        yi = _yes_index(outcomes)
        try:
            yes_price = float(prices[yi])
        except Exception:
            return Resolution(status=ResolutionStatus.AMBIGUOUS, reason="unparseable terminal price")

        if abs(yes_price - 1.0) < 1e-6:
            return Resolution(status=ResolutionStatus.YES, resolved_yes=True)
        if abs(yes_price - 0.0) < 1e-6:
            return Resolution(status=ResolutionStatus.NO, resolved_yes=False)
        if abs(yes_price - 0.5) < 1e-6:
            return Resolution(
                status=ResolutionStatus.CANCELED, reason="50/50 refund-like terminal price"
            )
        return Resolution(
            status=ResolutionStatus.AMBIGUOUS, reason=f"non-terminal yes price {yes_price:.4f}"
        )

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

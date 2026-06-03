"""Built-in sample markets so the pipeline runs fully offline (no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradebot.models import Market

# (id, question, yes_price, volume_24h, liquidity, days_to_res, best_bid, best_ask)
_RAW = [
    ("btc-100k", "Will Bitcoin close above $100k this month?", 0.62, 250000, 8000, 12, 0.61, 0.63),
    ("eth-etf", "Will a spot ETH ETF see >$1B inflows in 30 days?", 0.41, 90000, 5200, 21, 0.39, 0.43),
    ("fed-cut", "Will the Fed cut rates at the next meeting?", 0.73, 410000, 15000, 7, 0.72, 0.74),
    ("elec-x", "Will candidate X win the upcoming election?", 0.52, 1200000, 42000, 25, 0.51, 0.53),
    ("ai-model", "Will a new frontier AI model launch this month?", 0.68, 60000, 3100, 14, 0.64, 0.71),
    ("sports-y", "Will team Y reach the finals?", 0.34, 175000, 6400, 18, 0.32, 0.36),
    ("weather-z", "Will it be the hottest month on record?", 0.58, 22000, 1500, 9, 0.55, 0.62),
    ("rate-hold", "Will unemployment stay below 4.5% next report?", 0.81, 130000, 9800, 5, 0.80, 0.82),
]


def sample_markets() -> list[Market]:
    now = datetime.now(timezone.utc)
    out: list[Market] = []
    for mid, q, price, vol, liq, days, bid, ask in _RAW:
        out.append(
            Market(
                id=mid,
                question=q,
                yes_token_id=f"{mid}-YES",
                no_token_id=f"{mid}-NO",
                yes_price=price,
                volume_24h=float(vol),
                liquidity=float(liq),
                end_date=now + timedelta(days=days),
                best_bid=bid,
                best_ask=ask,
            )
        )
    return out

"""Hard, quantitative 'fact' signals that beat headline sentiment.

Unlike the text sources (RSS / Reddit / web), these produce a NUMBER — a
calibrated prior probability for YES — plus a short human-readable fact that is
injected into the narrative the LLM forecaster and BrainManager read.

  * ``crypto_fact`` — live coin price (CoinGecko, no key) compared to a ``$X``
    strike parsed from the question -> a price-based prior.
  * ``odds_fact``   — real bookmaker odds (the-odds-api, free key) for a
    'Will X win' / 'X vs Y' match -> the vig-removed implied probability.

Both are STRICTLY best-effort and return ``None`` when they do not apply or no
real data is available. They never fabricate a number. The network fetch in each
is injectable so the parsing/maths is unit-tested offline.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

_UA = "Mozilla/5.0 (compatible; tradebot/0.1; +research)"


@dataclass
class FactSignal:
    prob: float        # calibrated P(YES) implied by the fact, 0..1
    text: str          # human-readable fact (also injected into the narrative)
    source: str        # "coingecko" | "odds-api"
    confidence: float  # 0..1, how much weight the prior deserves


def best_fact(question: str, odds_api_key: str = "") -> Optional[FactSignal]:
    """Return the most relevant hard fact for a market, or ``None``."""
    f = crypto_fact(question)
    if f is not None:
        return f
    if odds_api_key:
        f = odds_fact(question, odds_api_key)
        if f is not None:
            return f
    return None


# --- crypto price (CoinGecko, no key) -------------------------------------

_COINS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "ether": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "ripple": "ripple", "xrp": "ripple",
    "cardano": "cardano", "ada": "cardano",
    "binance coin": "binancecoin", "bnb": "binancecoin",
    "litecoin": "litecoin", "ltc": "litecoin",
}
_ABOVE = ("above", "over", "exceed", "reach", "hit", "greater", "higher", "more than")
_BELOW = ("below", "under", "less than", "lower", "drop to", "fall to")


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _detect_coin(ql: str) -> Optional[str]:
    for key in sorted(_COINS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", ql):
            return _COINS[key]
    return None


def _parse_strike(question: str) -> tuple[Optional[float], Optional[str]]:
    ql = question.lower()
    direction = None
    if any(w in ql for w in _ABOVE):
        direction = "above"
    elif any(w in ql for w in _BELOW):
        direction = "below"
    # Require an explicit '$' so we never grab a year/date as a strike. The
    # k/m/b multiplier must be attached to the number (e.g. "$5k"); the trailing
    # \b stops "$64,000 by Friday" from reading the 'b' of "by" as billions.
    m = re.search(r"\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)(k|m|b)?\b", ql)
    if not m:
        return None, direction
    val = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2) or "", 1.0)
    return val * mult, direction


def _coingecko_price(coin_id: str, timeout: float = 8.0) -> Optional[float]:
    url = "https://api.coingecko.com/api/v3/simple/price?" + urllib.parse.urlencode(
        {"ids": coin_id, "vs_currencies": "usd"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return float(data[coin_id]["usd"])


def crypto_fact(
    question: str, price_fetcher: Callable[[str], Optional[float]] = _coingecko_price
) -> Optional[FactSignal]:
    coin_id = _detect_coin(question.lower())
    if not coin_id:
        return None
    strike, direction = _parse_strike(question)
    if strike is None or strike <= 0 or direction is None:
        return None
    try:
        price = price_fetcher(coin_id)
    except Exception:
        price = None
    if not price or price <= 0:
        return None

    prob_above = _logistic(10.0 * (price / strike - 1.0))
    prob = prob_above if direction == "above" else 1.0 - prob_above
    coin_name = "BNB" if coin_id == "binancecoin" else coin_id.title()
    text = (
        f"{coin_name} is currently ${price:,.0f} versus the ${strike:,.0f} "
        f"strike (market asks '{direction}')."
    )
    return FactSignal(prob=prob, text=text, source="coingecko", confidence=0.5)


# --- bookmaker odds (the-odds-api, free key) ------------------------------

# A small set of common the-odds-api sport keys to scan for a match. Best-effort:
# unmatched markets simply yield no odds fact.
_SPORTS = (
    "tennis_atp", "tennis_wta",
    "soccer_epl", "soccer_uefa_champs_league",
    "basketball_nba", "americanfootball_nfl",
    "mma_mixed_martial_arts", "baseball_mlb",
)


def _clean_name(s: str) -> str:
    s = re.sub(r"[^A-Za-z .'-]", " ", s).strip()
    return " ".join(s.split()[:4])


def _surname_key(name: str) -> str:
    parts = name.strip().lower().split()
    return parts[-1] if parts else ""


def _parse_match(question: str) -> tuple[Optional[str], Optional[str]]:
    """Extract (subject, opponent-or-None) from a sports question."""
    s = question.strip()
    s = re.sub(r"^[A-Za-z0-9 ]{1,20}:\s*", "", s)  # strip "Tyler: " style prefix
    m = re.search(r"(.+?)\s+vs?\.?\s+(.+)", s, re.I)
    if m:
        return _clean_name(m.group(1)), _clean_name(m.group(2))
    m = re.search(r"will\s+(.+?)\s+(?:win|beat|defeat|advance|reach)\b", s, re.I)
    if m:
        return _clean_name(m.group(1)), None
    return None, None


def _match_prob(event: dict, subject: str) -> Optional[float]:
    """Vig-removed implied win probability for ``subject`` in a h2h event."""
    key = _surname_key(subject)
    if not key:
        return None
    for bk in event.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            outs = mk.get("outcomes", [])
            prices = [o.get("price") for o in outs]
            if len(outs) < 2 or not all(isinstance(p, (int, float)) and p > 1.0 for p in prices):
                continue
            implied = [1.0 / p for p in prices]
            total = sum(implied)
            for o, im in zip(outs, implied):
                if key in (o.get("name") or "").lower():
                    return im / total
    return None


def _oddsapi_events(sport: str, api_key: str, timeout: float = 8.0) -> list:
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds?" + urllib.parse.urlencode(
        {"apiKey": api_key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def odds_fact(
    question: str,
    api_key: str,
    events_fetcher: Optional[Callable[[str, str], list]] = None,
    sports: tuple = _SPORTS,
) -> Optional[FactSignal]:
    if not api_key:
        return None
    subject, _opponent = _parse_match(question)
    if not subject:
        return None
    fetch = events_fetcher or _oddsapi_events
    for sport in sports:
        try:
            events = fetch(sport, api_key)
        except Exception:
            events = []
        for ev in events or []:
            prob = _match_prob(ev, subject)
            if prob is not None:
                return FactSignal(
                    prob=prob,
                    text=f"Bookmaker odds imply {subject} wins with {prob:.0%} probability.",
                    source="odds-api",
                    confidence=0.7,
                )
    return None

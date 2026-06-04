"""Stage 1: scan the market universe, filter by spread/time, flag anomalies.

The market-quality gate is SPREAD-based (Teil A.1): the bid/ask spread is the
dominant round-trip cost on Polymarket, so we filter on it directly instead of on
absolute USDC liquidity/volume thresholds. ``min_liquidity`` survives only as a
fallback when the order book is not yet published, and as the depth source for
sizing (risk/kelly). Order-book DEPTH vs the planned order size is enforced later,
at sizing time.
"""
from __future__ import annotations

from tradebot.agents.base import Agent
from tradebot.models import Candidate, Market
from tradebot.risk.liquidity import passes_spread_filter


class ScanAgent(Agent):
    name = "scan"

    def run(self, markets: list[Market], top_n: int = 15) -> list[Candidate]:
        s = self.settings
        max_spread = float(getattr(s, "max_spread", 0.03))
        candidates: list[Candidate] = []
        for m in markets:
            days = m.days_to_resolution()
            last = self.store.last_yes_price(m.id)
            self.store.record_snapshot(m.id, m.yes_price, m.spread)
            price_move = abs(m.yes_price - last) if last is not None else 0.0

            if not passes_spread_filter(m, max_spread, s.min_liquidity):
                continue
            if days < s.min_days_to_resolution or days > s.max_days_to_resolution:
                continue

            flags: list[str] = []
            if price_move >= 0.08:
                flags.append(f"price_move={price_move:.2f}")
            # A market that passed the gate but sits in the upper part of the
            # allowed spread band still trades, but is flagged as a cost risk.
            if m.spread >= max(0.05, 0.6 * max_spread):
                flags.append(f"wide_spread={m.spread:.2f}")
            candidates.append(Candidate(market=m, flags=flags, price_move=price_move))

        # Rank: anomalies first, then larger moves, then markets nearer 0.5 (most uncertain).
        candidates.sort(
            key=lambda c: (len(c.flags), c.price_move, 0.5 - abs(c.market.yes_price - 0.5)),
            reverse=True,
        )
        self.log.info("Scan: %d/%d markets passed filters", len(candidates), len(markets))
        return candidates[:top_n]

"""Stage 1: scan the market universe, filter by liquidity/volume/time, flag anomalies."""
from __future__ import annotations

from tradebot.agents.base import Agent
from tradebot.models import Candidate, Market


class ScanAgent(Agent):
    name = "scan"

    def run(self, markets: list[Market], top_n: int = 15) -> list[Candidate]:
        s = self.settings
        candidates: list[Candidate] = []
        for m in markets:
            days = m.days_to_resolution()
            last = self.store.last_yes_price(m.id)
            self.store.record_snapshot(m.id, m.yes_price)
            price_move = abs(m.yes_price - last) if last is not None else 0.0

            if m.liquidity < s.min_liquidity:
                continue
            if m.volume_24h < s.min_volume_24h:
                continue
            if days < s.min_days_to_resolution or days > s.max_days_to_resolution:
                continue

            flags: list[str] = []
            if price_move >= 0.08:
                flags.append(f"price_move={price_move:.2f}")
            if m.spread >= 0.05:
                flags.append(f"wide_spread={m.spread:.2f}")
            candidates.append(Candidate(market=m, flags=flags, price_move=price_move))

        # Rank: anomalies first, then larger moves, then markets nearer 0.5 (most uncertain).
        candidates.sort(
            key=lambda c: (len(c.flags), c.price_move, 0.5 - abs(c.market.yes_price - 0.5)),
            reverse=True,
        )
        self.log.info("Scan: %d/%d markets passed filters", len(candidates), len(markets))
        return candidates[:top_n]

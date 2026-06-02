"""Stage 2: parallel research + sentiment per candidate (RSS + Reddit, free sources)."""
from __future__ import annotations

import asyncio

from tradebot.agents.base import Agent
from tradebot.data import reddit, rss, sentiment
from tradebot.models import Candidate, ResearchReport


class ResearchAgent(Agent):
    name = "research"

    def __init__(self, settings, store, log, claude=None):
        super().__init__(settings, store, log)
        self.claude = claude

    async def _one(self, c: Candidate) -> ResearchReport:
        q = c.market.question
        groups = await asyncio.gather(
            asyncio.to_thread(rss.fetch_headlines, q),
            asyncio.to_thread(reddit.search_reddit, q),
        )
        texts = [t for g in groups for t in g]
        score, narrative = await asyncio.to_thread(sentiment.analyze, texts, q, self.claude)
        return ResearchReport(
            market_id=c.market.id, sentiment=score, narrative=narrative,
            n_sources=len(texts), implied_prob=c.market.yes_price,
        )

    async def _run_async(self, candidates: list[Candidate]) -> dict[str, ResearchReport]:
        reports = await asyncio.gather(*[self._one(c) for c in candidates])
        return {r.market_id: r for r in reports}

    def run(self, candidates: list[Candidate]) -> dict[str, ResearchReport]:
        if not candidates:
            return {}
        reports = asyncio.run(self._run_async(candidates))
        self.log.info("Research: analyzed %d candidates", len(reports))
        return reports

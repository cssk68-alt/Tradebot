"""Stage 2: parallel research + sentiment per candidate.

RSS (news) and Reddit are fetched AND scored separately so the ResearchReport
carries source-specific sentiment/volume. The brain can then learn that, say,
Reddit is hype-driven for one market type while RSS is better calibrated for
another — instead of seeing one collapsed score.
"""
from __future__ import annotations

import asyncio

from tradebot.agents.base import Agent
from tradebot.data import reddit, rss, sentiment
from tradebot.models import Candidate, ResearchReport


class ResearchAgent(Agent):
    name = "research"

    def __init__(self, settings, store, log, client=None):
        super().__init__(settings, store, log)
        self.client = client

    async def _one(self, c: Candidate) -> ResearchReport:
        q = c.market.question
        rss_texts, reddit_texts = await asyncio.gather(
            asyncio.to_thread(rss.fetch_headlines, q),
            asyncio.to_thread(reddit.search_reddit, q),
        )
        rss_score, rss_narr = await asyncio.to_thread(sentiment.analyze, rss_texts, q, self.client)
        reddit_score, reddit_narr = await asyncio.to_thread(
            sentiment.analyze, reddit_texts, q, self.client
        )
        n_rss, n_reddit = len(rss_texts), len(reddit_texts)
        total = n_rss + n_reddit
        score = (rss_score * n_rss + reddit_score * n_reddit) / total if total else 0.0
        return ResearchReport(
            market_id=c.market.id,
            sentiment=score,
            narrative=f"RSS: {rss_narr} | Reddit: {reddit_narr}",
            n_sources=total,
            implied_prob=c.market.yes_price,
            rss_sentiment=rss_score,
            reddit_sentiment=reddit_score,
            rss_sources=n_rss,
            reddit_sources=n_reddit,
            source_quality=min(1.0, total / 8.0),
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

"""Stage 2: parallel research + sentiment per candidate.

RSS (news) and Reddit are fetched AND scored separately so the ResearchReport
carries source-specific sentiment/volume. The brain can then learn that, say,
Reddit is hype-driven for one market type while RSS is better calibrated for
another — instead of seeing one collapsed score.
"""
from __future__ import annotations

import asyncio

from tradebot.agents.base import Agent
from tradebot.data import facts, reddit, rss, sentiment, social, websearch
from tradebot.models import Candidate, ResearchReport


class ResearchAgent(Agent):
    name = "research"

    def __init__(self, settings, store, log, client=None):
        super().__init__(settings, store, log)
        self.client = client

    async def _one(self, c: Candidate) -> ResearchReport:
        q = c.market.question
        s = self.settings
        rss_texts, reddit_texts, social_texts, web_texts, fact = await asyncio.gather(
            asyncio.to_thread(rss.fetch_headlines, q),
            asyncio.to_thread(reddit.search_reddit, q, s.reddit_client_id, s.reddit_client_secret),
            asyncio.to_thread(social.search_social, q),
            asyncio.to_thread(websearch.search, q, s.tavily_api_key),
            asyncio.to_thread(facts.best_fact, q, s.odds_api_key),
        )
        # The 'social' research channel: Reddit (only if OAuth creds are set, else
        # empty) PLUS the free key-less forums (Bluesky / Hacker News / Lemmy). The
        # extra sources keep this channel populated even without Reddit, so fewer
        # markets are skipped for lack of research and there is more discussion
        # signal to find an edge. It still feeds the existing reddit_* feature slot,
        # so the feature schema (and the brain) is unchanged.
        social_all = reddit_texts + social_texts
        rss_score, rss_narr = await asyncio.to_thread(sentiment.analyze, rss_texts, q, self.client)
        reddit_score, reddit_narr = await asyncio.to_thread(
            sentiment.analyze, social_all, q, self.client
        )
        web_score, web_narr = await asyncio.to_thread(
            sentiment.analyze, web_texts, q, self.client
        )
        n_rss, n_reddit, n_web = len(rss_texts), len(social_all), len(web_texts)
        total = n_rss + n_reddit + n_web
        score = (
            (rss_score * n_rss + reddit_score * n_reddit + web_score * n_web) / total
            if total else 0.0
        )
        narrative = f"RSS: {rss_narr} | Social: {reddit_narr} | Web: {web_narr}"
        if fact is not None:
            narrative = f"FACT: {fact.text} | " + narrative
        return ResearchReport(
            market_id=c.market.id,
            sentiment=score,
            narrative=narrative,
            n_sources=total,
            implied_prob=c.market.yes_price,
            rss_sentiment=rss_score,
            reddit_sentiment=reddit_score,
            rss_sources=n_rss,
            reddit_sources=n_reddit,
            web_sentiment=web_score,
            web_sources=n_web,
            source_quality=min(1.0, total / 8.0),
            fact_prob=(fact.prob if fact is not None else None),
            fact_confidence=(fact.confidence if fact is not None else 0.0),
            fact_text=(fact.text if fact is not None else ""),
            fact_source=(fact.source if fact is not None else ""),
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

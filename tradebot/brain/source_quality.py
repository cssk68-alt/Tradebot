"""Phase 2: per-source reliability scoring from resolved trades.

As trades resolve, each research source (RSS, Reddit, Web, Fact) contributed to
that trade's decision. This module learns: *which sources were actually right?*

The score is a simple Bayesian posterior: (true_positives + 1) / (total + 2), so
a source with no data starts at 50% (neutral), and converges to its true hit rate
as trades accumulate. Scores are written to the ResearchReport and blended into
confidence — so the LLM forecaster and BrainManager see "you found 8 sources but
only 3 types are reliable."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tradebot.models import Experience


@dataclass
class SourceScores:
    """Reliability of each research source, 0..1 (higher = more trustworthy)."""

    rss: float = 0.5
    reddit: float = 0.5
    web: float = 0.5
    fact: float = 0.5

    def as_dict(self) -> dict[str, float]:
        return {"rss": self.rss, "reddit": self.reddit, "web": self.web, "fact": self.fact}


@dataclass
class SourceCounter:
    """Tally of hits per source (used during scoring computation)."""

    rss_correct: int = 0
    rss_total: int = 0
    reddit_correct: int = 0
    reddit_total: int = 0
    web_correct: int = 0
    web_total: int = 0
    fact_correct: int = 0
    fact_total: int = 0

    def score_from_experiences(self, experiences: list[Experience]) -> SourceScores:
        """Tally a set of resolved trades and compute per-source reliability."""
        for exp in experiences:
            # Features: indices match FEATURE_NAMES in ml/features.py
            # rss_sentiment=10, reddit_sentiment=11, web_sentiment=17, fact_prob=19
            rss_sent = exp.features[10] if len(exp.features) > 10 else 0.0
            reddit_sent = exp.features[11] if len(exp.features) > 11 else 0.0
            web_sent = exp.features[17] if len(exp.features) > 17 else 0.0
            fact_prob = exp.features[19] if len(exp.features) > 19 else 0.5

            # A source was *active* if it contributed signal (non-zero sentiment or
            # present fact). We count it as "correct" if the trade won.
            if rss_sent != 0.0:
                self.rss_total += 1
                if exp.won:
                    self.rss_correct += 1
            if reddit_sent != 0.0:
                self.reddit_total += 1
                if exp.won:
                    self.reddit_correct += 1
            if web_sent != 0.0:
                self.web_total += 1
                if exp.won:
                    self.web_correct += 1
            if fact_prob != 0.5:  # 0.5 = neutral / absent
                self.fact_total += 1
                if exp.won:
                    self.fact_correct += 1

        # Bayesian posterior: (hits + 1) / (trials + 2) so no-data = 50%.
        return SourceScores(
            rss=(self.rss_correct + 1) / (self.rss_total + 2),
            reddit=(self.reddit_correct + 1) / (self.reddit_total + 2),
            web=(self.web_correct + 1) / (self.web_total + 2),
            fact=(self.fact_correct + 1) / (self.fact_total + 2),
        )

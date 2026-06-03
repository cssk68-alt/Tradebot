"""Stage 5 meta-controller: the Claude Haiku "BrainManager".

Before an order is executed in Stage 4, Haiku acts as the final arbiter. For each
signal it receives:
  (a) the ResearchReport with SEPARATED Reddit/RSS sentiment,
  (b) the mathematical prediction (raw XGBoost P(YES)),
  (c) the MLP veto score (the brain).

It checks them for logical contradictions and returns a final verdict —
"Execution Approved" or "Execution Vetoed". The reasoning for every decision is
written to the local database (an audit trail), as required.

Paper-mode preservation: if no LLM is configured (no API key), the manager
auto-approves and records that fact, so the paper pipeline keeps running on real
signals without an Anthropic key. When an LLM IS configured but fails to return a
parseable verdict, it fails CLOSED (vetoes) for safety.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tradebot.agents.base import Agent
from tradebot.models import ManagerDecision, ResearchReport, Signal


@dataclass
class Verdict:
    approved: bool
    reason: str


class BrainManager(Agent):
    name = "brain_manager"

    def __init__(self, settings, store, log, claude=None):
        super().__init__(settings, store, log)
        self.claude = claude

    def run(
        self, signals: list[Signal], reports: dict[str, ResearchReport]
    ) -> list[Signal]:
        approved: list[Signal] = []
        for sig in signals:
            report = reports.get(sig.market_id)
            verdict = self._decide(sig, report)
            self.store.save_manager_decision(
                ManagerDecision(
                    market_id=sig.market_id, question=sig.question,
                    approved=verdict.approved, reason=verdict.reason,
                    model_prob=sig.model_prob, brain_score=sig.brain_score,
                    edge=sig.edge, is_yes=sig.is_yes,
                    rss_sentiment=report.rss_sentiment if report else 0.0,
                    reddit_sentiment=report.reddit_sentiment if report else 0.0,
                )
            )
            if verdict.approved:
                approved.append(sig)
                self.log.info(
                    "BrainManager APPROVED '%s' — %s", sig.question[:40], verdict.reason
                )
            else:
                self.log.warning(
                    "BrainManager VETOED '%s' — %s", sig.question[:40], verdict.reason
                )
        self.log.info("BrainManager: %d/%d signals approved", len(approved), len(signals))
        return approved

    def _decide(self, sig: Signal, report: Optional[ResearchReport]) -> Verdict:
        if self.claude is not None and self.claude.available:
            res = self.claude.decide_execution(
                question=sig.question, is_yes=sig.is_yes, model_prob=sig.model_prob,
                brain_score=sig.brain_score, edge=sig.edge,
                rss_sentiment=report.rss_sentiment if report else 0.0,
                reddit_sentiment=report.reddit_sentiment if report else 0.0,
                rss_sources=report.rss_sources if report else 0,
                reddit_sources=report.reddit_sources if report else 0,
            )
            if res is not None:
                approved, reason = res
                return Verdict(approved, reason)
            return Verdict(False, "BrainManager (Haiku) returned no parseable verdict — vetoed.")
        return Verdict(True, "BrainManager LLM unavailable (no API key) — auto-approved (paper).")

"""Stage 5 meta-controller: the LLM-agent "BrainManager".

Before an order is executed in Stage 4, the LLM agent acts as the final arbiter.
For each signal it receives:
  (a) the ResearchReport with SEPARATED Reddit/RSS sentiment,
  (b) the mathematical prediction (raw XGBoost P(YES)),
  (c) the MLP veto score (the brain).

It checks them for logical contradictions and returns a final verdict —
"Execution Approved" or "Execution Vetoed". The reasoning for every decision is
written to the local database (an audit trail), as required.

FAIL-CLOSED, always: there is NO auto-approve fallback (not even in paper mode).
If no LLM agent is available, or it returns an unparseable verdict, the trade is
VETOED. In practice the orchestrator hard-fails at startup without an agent, so
this is defense-in-depth — no trade is ever approved without the agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tradebot.agents.base import Agent
from tradebot.models import ManagerDecision, ResearchReport, Signal
from tradebot.risk.adjuster import risk_profile


@dataclass
class Verdict:
    approved: bool
    reason: str


class BrainManager(Agent):
    name = "brain_manager"

    def __init__(self, settings, store, log, client=None):
        super().__init__(settings, store, log)
        self.client = client

    def run(
        self, signals: list[Signal], reports: dict[str, ResearchReport]
    ) -> list[Signal]:
        approved: list[Signal] = []
        # The operator's Risk-Adjuster 'Ping': one instruction line that tells the
        # agent how bold to be this cycle (empty when the knob is conservative).
        profile = risk_profile(self.settings)
        if profile.appetite_prompt:
            self.log.info("BrainManager risk appetite: %s", profile.label)
        for sig in signals:
            report = reports.get(sig.market_id)
            verdict = self._decide(sig, report, profile.appetite_prompt)
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

    def _decide(
        self, sig: Signal, report: Optional[ResearchReport], risk_appetite: str = ""
    ) -> Verdict:
        if self.client is not None and self.client.available:
            res = self.client.decide_execution(
                question=sig.question, is_yes=sig.is_yes, model_prob=sig.model_prob,
                brain_score=sig.brain_score, edge=sig.edge,
                rss_sentiment=report.rss_sentiment if report else 0.0,
                reddit_sentiment=report.reddit_sentiment if report else 0.0,
                rss_sources=report.rss_sources if report else 0,
                reddit_sources=report.reddit_sources if report else 0,
                risk_appetite=risk_appetite,
            )
            if res is not None:
                approved, reason = res
                return Verdict(approved, reason)
            return Verdict(False, "BrainManager agent returned no parseable verdict — vetoed.")
        # FAIL-CLOSED: no agent -> no approval, ever (no auto-approve, not even paper).
        return Verdict(False, "BrainManager LLM unavailable (no agent) — vetoed (fail-closed).")

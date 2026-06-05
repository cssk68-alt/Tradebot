"""For-The-Future Learner — Meta-Learning LLM ("Anwalt").

Runs AFTER all execution decisions are finalized. Purely OBSERVER-ONLY:
- does NOT influence trades, sizing, risk, or pattern evaluation
- does NOT block or approve anything
- analyzes completed trades en masse
- detects recurring structural patterns
- generates insights for the dashboard (human-readable, not decision rules)

Design: extends the observer pattern of PostmortemAgent but operates on
BATCHES of trades looking for cross-trade patterns (postmortem already
handles per-trade lessons). Called after _after_resolved and after
counterfactual settlement, so it sees both real trades and settled
veto/mirror outcomes.

HARD SEPARATION: This module has NO write access to any decision-making
state (no risk, no patterns, no execution). Only writes to:
  - meta_insights (in-memory, for dashboard export)
  - dashboard JSON (read-only display)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from tradebot.agents.base import Agent
from tradebot.models import MetaInsight, Trade


class MetaLearner(Agent):
    """For-The-Future Learner — observes, analyzes, never acts."""

    name = "meta_learner"

    def __init__(self, settings, store, log, brain, client=None):
        super().__init__(settings, store, log)
        self.client = client
        self.brain = brain
        # Latest insight (replaced every cycle, not cumulative)
        self.latest_insight: Optional[MetaInsight] = None

    def run(self, resolved: list[Trade]) -> Optional[MetaInsight]:
        """Analyze a batch of resolved trades. Returns MetaInsight or None.

        Called AFTER _after_resolved so both the postmortem (per-trade LLM lessons)
        and pattern engine have already processed these trades. This adds the
        META layer: cross-trade patterns the per-trade analysis can't see.
        """
        if not self.client or not self.client.available:
            return None
        if len(resolved) < 3:
            # Need enough trades for cross-trade pattern detection
            return None

        # Build a compact trade history string for the LLM prompt
        trade_history = self._build_trade_history(resolved)
        if not trade_history:
            return None

        raw = self.client.meta_learn(trade_history)
        if raw is None:
            return None

        self.latest_insight = MetaInsight(
            insight_summary=raw.get("insight_summary", []),
            confidence_of_insight=raw.get("confidence_of_insight", 0.0),
            category_tags=raw.get("category_tags", []),
            suggested_future_hypotheses=raw.get("suggested_future_hypotheses", []),
            n_trades_analyzed=len(resolved),
            generated_at=datetime.now(timezone.utc),
        )

        # Log insights at info level for terminal visibility
        self.log.info("Meta-Learner: analyzed %d trades, %d insight(s)",
                       len(resolved), len(self.latest_insight.insight_summary))
        for bullet in self.latest_insight.insight_summary:
            self.log.info("  Meta-Insight: %s", bullet)

        return self.latest_insight

    def _build_trade_history(self, resolved: list[Trade]) -> str:
        """Build a concise multi-trade summary for the LLM.

        Includes: input signals (edge, brain_score, confidence), decision outcome,
        pattern engine state at time of analysis, and realized PnL.
        Does NOT include any execution or risk internals.
        """
        if not resolved:
            return ""

        # Aggregate statistics
        n = len(resolved)
        wins = sum(1 for t in resolved if t.won)
        losses = sum(1 for t in resolved if t.won is False)
        total_pnl = sum(t.pnl for t in resolved)
        avg_edge = sum(t.edge for t in resolved) / n
        avg_brain = sum(t.brain_score for t in resolved) / n

        # Pattern engine stats (non-intrusive, informational only)
        ps = self.brain.pattern_stats() if callable(getattr(self.brain, 'pattern_stats', None)) else {}

        lines = [
            f"Trades analyzed: {n}",
            f"Wins: {wins}, Losses: {losses}, Total PnL: {total_pnl:+.2f}",
            f"Average edge: {avg_edge:.3f}, Average brain score: {avg_brain:.3f}",
            "",
        ]

        # Pattern engine context (for detecting systematic biases)
        active_patterns = ps.get("total_emerged_patterns", 0)
        if active_patterns > 0:
            lines.append(f"Active emerged patterns: {active_patterns}")
            by_stage = ps.get("patterns_by_stage", {})
            lines.append(f"  Weak: {by_stage.get('WEAK', 0)}, Strong: {by_stage.get('STRONG', 0)}, "
                         f"Mature: {by_stage.get('MATURE', 0)}")
            lines.append(f"  Total observations: {ps.get('total_observations', 0)}")

        lines.append("")

        # Per-trade details (last 20 max to keep prompt reasonable)
        for t in resolved[-20:]:
            side = "YES" if t.is_yes else "NO"
            outcome = "WIN" if t.won else "LOSS" if t.won is False else "VOID"
            lines.append(
                f"Trade: {side} | edge={t.edge:+.3f} brain={t.brain_score:.2f} "
                f"entry={t.entry_price:.3f} -> {outcome} pnl={t.pnl:+.2f}"
            )

        lines.append("")
        lines.append("Compare LLM prediction signals vs real outcomes. "
                     "Identify structural patterns, systematic biases, "
                     "and weaknesses in the decision system.")
        return "\n".join(lines)

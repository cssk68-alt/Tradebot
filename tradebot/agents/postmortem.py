"""Stage 5 (LLM side): postmortem every resolved trade into a Lesson — for losses
AND wins. The neural brain itself learns from the Experience records (in the
orchestrator); these textual lessons feed back into the Claude prompts."""
from __future__ import annotations

from typing import Optional

from tradebot.agents.base import Agent
from tradebot.models import Lesson, Trade


class PostmortemAgent(Agent):
    name = "postmortem"

    def __init__(self, settings, store, log, claude=None):
        super().__init__(settings, store, log)
        self.claude = claude

    def run(self, resolved: list[Trade]) -> list[Lesson]:
        lessons: list[Lesson] = []
        for t in resolved:
            lesson = self._analyze(t)
            if lesson is not None:
                self.store.save_lesson(lesson)
                lessons.append(lesson)
        if lessons:
            self.log.info("Postmortem: recorded %d lessons", len(lessons))
        return lessons

    def _analyze(self, t: Trade) -> Optional[Lesson]:
        desc = (
            f"Trade on '{t.question}', side {'YES' if t.is_yes else 'NO'}, "
            f"entry {t.entry_price:.2f}, edge {t.edge:.2f}, brain {t.brain_score:.2f}, "
            f"outcome {'WIN' if t.won else 'LOSS'} (pnl {t.pnl:.2f})."
        )
        if self.claude is not None and self.claude.available:
            res = self.claude.postmortem(desc)
            if res is not None:
                cat, cause, rec = res
                return Lesson(trade_id=t.id, category=cat, cause=cause, recommendation=rec, text=desc)

        if t.won:
            return Lesson(
                trade_id=t.id, category="win", cause="profitable setup",
                recommendation="reinforce similar edge/sentiment patterns", text=desc,
            )
        cause = "thin edge" if abs(t.edge) < 0.07 else "adverse resolution"
        if t.brain_score < 0.5:
            cause = "brain had warned (low score)"
        return Lesson(
            trade_id=t.id, category="loss", cause=cause,
            recommendation="avoid similar setups; trust the brain veto", text=desc,
        )

"""Format stored lessons for injection into LLM prompts (stage 3 / postmortem)."""
from __future__ import annotations

from tradebot.models import Lesson


def format_lessons(lessons: list[Lesson]) -> str:
    if not lessons:
        return "No prior lessons yet."
    return "\n".join(
        f"- [{l.category}] {l.cause} -> {l.recommendation}" for l in lessons
    )

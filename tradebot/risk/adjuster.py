"""Aggressiveness adjuster — the manual 'Risk-Adjuster' / Aggressivitäts-Regler.

ONE operator knob (`aggressiveness` in [0, 1]) is translated into runtime-loosened
filter thresholds WITHOUT touching the mathematical core (the Kelly formula, the
MLP, the edge calculation all stay byte-for-byte identical). This module only
shifts the *thresholds those formulas are compared against* and produces a short
human-readable risk appetite that is injected into the BrainManager's prompt.

That is exactly the "Ping" idea: the slider is a variable in the agent context,
not a code change. Turn it up and the LLM arbiter is told to be bolder on thin
data while the numeric gates relax in lock-step — turn it down and everything
returns to the configured base thresholds.

  aggressiveness = 0.0  -> fully conservative: use the configured base thresholds.
  aggressiveness = 1.0  -> maximally bold: the brain effectively never vetoes on
                           its score, the confidence bar drops to a coin-flip and
                           the edge bar to a thin floor. Meant for PAPER-mode
                           bootstrapping (see note below).

HARD SAFETY FLOORS (an 'aggressive' run can never disable risk management):
  * a strictly POSITIVE edge is still required (EDGE_FLOOR), so we never buy a
    coin-flip or a negative-EV contract;
  * the confidence bar never drops below CONFIDENCE_FLOOR (0.5);
  * per-trade size is SHRUNK as aggressiveness rises (size_factor), so "bolder"
    means *more, smaller* trades — not betting the farm on marginal setups;
  * the per-trade / exposure / liquidity caps in kelly.py are untouched, and the
    BrainManager's logical-contradiction veto still fires on hard contradictions.

Why this also fixes the cold-start brain (see STAND/probleme): the brain stays at
its 0.500 default until it has >=8 RESOLVED trades with both a win and a loss. If
almost nothing is approved, it never gets that data and is stuck at 0.5 forever.
Running PAPER with higher aggressiveness deliberately approves more marginal
trades -> more resolved outcomes -> the brain crosses the training threshold and
starts to learn. Once it is trained, dial aggressiveness back down and let the
now-educated brain be selective again.
"""
from __future__ import annotations

from dataclasses import dataclass

# Hard floors — aggressiveness loosens toward these but never past them.
CONFIDENCE_FLOOR = 0.5   # never bet below a coin-flip's worth of confidence
EDGE_FLOOR = 0.02        # always require *some* positive edge (covers typical spread)
MIN_SIZE_FACTOR = 0.5    # at max aggression, per-trade size is halved (more, smaller)


@dataclass
class RiskProfile:
    """Effective, aggressiveness-adjusted thresholds for one cycle.

    The base thresholds in `settings` are never mutated; this is a derived view
    the gates read instead of the raw settings, so the maths stays untouched."""

    aggressiveness: float
    brain_veto_threshold: float
    confidence_threshold: float
    edge_threshold: float
    size_factor: float
    label: str            # "conservative" | "balanced" | "aggressive"
    appetite_prompt: str  # one line injected into the BrainManager prompt (or "")


def _lerp_down(base: float, floor: float, a: float) -> float:
    """Move `base` toward `floor` by fraction `a` (only ever downward)."""
    return base - a * max(0.0, base - floor)


def risk_profile(settings) -> RiskProfile:
    """Build the effective risk profile from the single `aggressiveness` knob.

    Robust to settings objects that predate the knob (`getattr` defaults), so
    existing callers/tests keep their original conservative behaviour."""
    a = max(0.0, min(1.0, float(getattr(settings, "aggressiveness", 0.0))))

    base_veto = float(getattr(settings, "brain_veto_threshold", 0.35))
    base_conf = float(getattr(settings, "confidence_threshold", 0.6))
    base_edge = float(getattr(settings, "edge_threshold", 0.05))

    # Brain veto fades linearly to 0 (the score gate is fully off at a == 1).
    eff_veto = base_veto * (1.0 - a)
    # Confidence and edge bars relax toward their hard floors.
    eff_conf = _lerp_down(base_conf, CONFIDENCE_FLOOR, a)
    eff_edge = _lerp_down(base_edge, EDGE_FLOOR, a)
    # Bolder => smaller individual stakes, so approval-rate can rise without the
    # dollar risk per marginal trade rising with it.
    size_factor = 1.0 - (1.0 - MIN_SIZE_FACTOR) * a

    if a < 0.34:
        label = "conservative"
    elif a < 0.67:
        label = "balanced"
    else:
        label = "aggressive"

    # The "Ping": a context line for the agent. Empty when conservative so the
    # BrainManager prompt is byte-identical to today's default behaviour.
    appetite_prompt = ""
    if label == "balanced":
        appetite_prompt = (
            "Operator risk appetite: BALANCED. Approve setups that are broadly "
            "consistent even if one signal is merely neutral; still veto on a clear "
            "contradiction."
        )
    elif label == "aggressive":
        appetite_prompt = (
            "Operator risk appetite: AGGRESSIVE. The operator explicitly accepts "
            "thinner and partly missing data and wants more trades to fuel learning. "
            "Approve borderline setups UNLESS there is a hard logical contradiction "
            "(e.g. sentiment strongly opposes the traded side). A low MLP score or "
            "thin/neutral sentiment alone is NOT a veto reason in this mode."
        )

    return RiskProfile(
        aggressiveness=a,
        brain_veto_threshold=eff_veto,
        confidence_threshold=eff_conf,
        edge_threshold=eff_edge,
        size_factor=size_factor,
        label=label,
        appetite_prompt=appetite_prompt,
    )

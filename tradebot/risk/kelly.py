"""Fractional Kelly position sizing with hard caps."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tradebot.models import Signal
from tradebot.risk.adjuster import risk_profile
from tradebot.risk.liquidity import depth_too_thin, max_order_for_depth


@dataclass
class SizeDecision:
    size: float  # number of shares
    amount: float  # dollars at risk
    approved: bool
    reason: str


def kelly_fraction(edge: float, price: float) -> float:
    """Fraction of bankroll to put at risk for a binary contract bought at `price`
    with edge = true_prob - price.  f* = edge / (1 - price)."""
    if price <= 0.0 or price >= 1.0:
        return 0.0
    return max(0.0, edge / (1.0 - price))


def size_position(
    signal: Signal,
    bankroll: float,
    settings,
    current_exposure: float = 0.0,
    liquidity: float = 1e9,
    pattern_eval: Optional[dict] = None,
) -> SizeDecision:
    # Aggressiveness-adjusted thresholds (the maths below is unchanged; only the
    # bars it is compared against shift with the operator's Risk-Adjuster knob).
    profile = risk_profile(settings)

    # ---- Probabilistic Rule Learning adjustments (v2) ----
    #
    # AUDIT FIX: Eliminated double-penalty issue.
    #
    # v1 had THREE overlapping penalties for the same pattern signal:
    #   risk_penalty -> reduced effective_confidence  (gate)
    #   risk_penalty -> reduced frac                   (size)
    #   size_mult    -> reduced frac                   (size)
    #
    # v2: Single coherent adjustment per dimension:
    #   confidence: conf_mod (additive, -0.3..+0.3)
    #   size:       size_mult (multiplicative, 0.5..1.2)
    #
    # risk_penalty is now a MONITORING-ONLY metric. It does NOT directly
    # adjust size or confidence. It is logged for the dashboard.
    pattern_eval = pattern_eval or {}
    risk_penalty = pattern_eval.get("risk_penalty_score", 0.0)
    conf_mod = pattern_eval.get("confidence_modifier", 0.0)
    size_mult = pattern_eval.get("position_size_multiplier", 1.0)
    constraint_strength = pattern_eval.get("constraint_strength", 0.0)
    patterns_applied = pattern_eval.get("patterns_applied", [])

    # ---- Confidence gate (single adjustment via conf_mod) ----
    effective_confidence = signal.confidence + conf_mod

    # ---- Edge gate (unchanged) ----
    price = signal.market_price
    f_star = kelly_fraction(signal.edge, price)
    if f_star <= 0.0:
        return SizeDecision(0.0, 0.0, False, "no positive Kelly edge")

    # ---- Confidence gate (single check) ----
    if effective_confidence < profile.confidence_threshold:
        return SizeDecision(
            0.0, 0.0, False,
            f"confidence {effective_confidence:.2f} (base {signal.confidence:.2f}) < threshold",
        )

    # ---- Brain veto gate (UNCHANGED: separate from pattern engine) ----
    if signal.brain_score < profile.brain_veto_threshold:
        return SizeDecision(0.0, 0.0, False, f"brain veto (score {signal.brain_score:.2f})")

    # ---- Order-book depth gate (unchanged) ----
    if depth_too_thin(liquidity):
        return SizeDecision(0.0, 0.0, False, "order-book depth too thin for any order")

    # ---- Position size (single adjustment via size_mult) ----
    frac = min(settings.kelly_fraction * f_star, settings.max_trade_pct) * profile.size_factor
    frac *= size_mult  # Single size adjustment (0.5..1.2)
    amount = frac * bankroll
    budget = max(0.0, settings.max_exposure_pct * bankroll - current_exposure)
    amount = min(amount, budget, max_order_for_depth(liquidity))
    if amount < 1.0:
        return SizeDecision(0.0, 0.0, False, "size below $1 / budget exhausted")

    # ---- Reason string ----
    reason = "approved"
    if patterns_applied:
        reason = f"approved (patterns: {', '.join(patterns_applied[:3])})"
        rs = []
        if risk_penalty > 0.01:
            rs.append(f"risk_pen={risk_penalty:.2f}")
        if constraint_strength > 0.1:
            rs.append(f"constraint={constraint_strength:.2f}")
        if rs:
            reason += " [" + "; ".join(rs) + "]"

    return SizeDecision(amount / price, amount, True, reason)

"""Fractional Kelly position sizing with hard caps."""
from __future__ import annotations

from dataclasses import dataclass

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
) -> SizeDecision:
    # Aggressiveness-adjusted thresholds (the maths below is unchanged; only the
    # bars it is compared against shift with the operator's Risk-Adjuster knob).
    profile = risk_profile(settings)

    price = signal.market_price
    f_star = kelly_fraction(signal.edge, price)
    if f_star <= 0.0:
        return SizeDecision(0.0, 0.0, False, "no positive Kelly edge")
    if signal.confidence < profile.confidence_threshold:
        return SizeDecision(0.0, 0.0, False, f"confidence {signal.confidence:.2f} < threshold")
    if signal.brain_score < profile.brain_veto_threshold:
        return SizeDecision(0.0, 0.0, False, f"brain veto (score {signal.brain_score:.2f})")

    # Order-book DEPTH vs planned order size (Teil A.1): never plan an order larger
    # than a small fraction of the visible liquidity, and reject outright when the
    # book is too thin to place even a $1 order.
    if depth_too_thin(liquidity):
        return SizeDecision(0.0, 0.0, False, "order-book depth too thin for any order")

    # Bolder => more, SMALLER trades: shrink the stake as aggressiveness rises so
    # approval-rate can climb without the dollar risk per marginal trade climbing.
    frac = min(settings.kelly_fraction * f_star, settings.max_trade_pct) * profile.size_factor
    amount = frac * bankroll
    budget = max(0.0, settings.max_exposure_pct * bankroll - current_exposure)
    amount = min(amount, budget, max_order_for_depth(liquidity))
    if amount < 1.0:
        return SizeDecision(0.0, 0.0, False, "size below $1 / budget exhausted")
    return SizeDecision(amount / price, amount, True, "approved")

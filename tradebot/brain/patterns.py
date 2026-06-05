"""Probabilistic Emergent Rule Learning — the PatternEngine (v2).

OVERVIEW
--------
Patterns emerge through a 4-stage pipeline based purely on accumulated
observation count. No single statistical test triggers or blocks a rule.
Every decision modifier is a continuous, probabilistic value derived from:

  1. statistical_significance_score  (continuous 0..1, not a p-value gate)
  2. stability_score                 (cross-window consistency)
  3. decay_adjusted_confidence       (recent vs old weighting)

Stage 1 (OBSERVATION, 1-5):     logging only, zero decision impact.
Stage 2 (WEAK, 6-20):           small adjustments via significance+stability.
Stage 3 (STRONG, 21-80):        meaningful adjustments, soft steering.
Stage 4 (MATURE, 80+):          full-strength adjustments, never binary rules.

CRITICAL DESIGN RULES FOR SCALPING:
  - ONE failure does NOT invalidate a strategy.
  - ONE success does NOT validate a strategy.
  - Patterns modify continuously, never binary on/off.
  - Old observations decay. Recent data carries more weight.
  - No p-value hard gate (replaced by continuous significance score).
  - No concept of "hard rule" — replaced by continuous constraint_strength.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Stage definitions (count-based only, no significance gate)
# ---------------------------------------------------------------------------

class PatternStage(Enum):
    """Stages reflect ONLY how many observations exist in a bucket.
    Decision impact grows with stage but is always modulated by
    significance, stability, and decay. No binary thresholds."""
    OBSERVATION = 1       # 1-5:     logged, zero decision impact
    WEAK = 2              # 6-20:    small adjustments
    STRONG = 3            # 21-80:   meaningful adjustments
    MATURE = 4            # 80+:     full-strength, still probabilistic

    @classmethod
    def from_count(cls, count: int) -> PatternStage:
        if count >= 80:
            return cls.MATURE
        if count >= 21:
            return cls.STRONG
        if count >= 6:
            return cls.WEAK
        return cls.OBSERVATION

    def stage_weight(self) -> float:
        """Base multiplier for how much this stage contributes to decision impact.
        Continuous 0..1, not a threshold. OBSERVATION -> 0.0 (no impact),
        MATURE -> 1.0 (full strength, but still probabilistic)."""
        mapping = {1: 0.0, 2: 0.15, 3: 0.50, 4: 1.0}
        return mapping.get(self.value, 0.0)


# ---------------------------------------------------------------------------
# Pattern categories
# ---------------------------------------------------------------------------

class PatternCategory(Enum):
    """What dimension of the trade this pattern relates to."""
    SIDE_BIAS = "side_bias"
    EDGE_RANGE = "edge_range"
    CONFIDENCE_RANGE = "confidence_range"
    BRAIN_SCORE_RANGE = "brain_score_range"
    HOLD_TIME = "hold_time"
    DAY_OF_WEEK = "day_of_week"
    PRICE_RANGE = "price_range"
    SPREAD_RANGE = "spread_range"
    SENTIMENT_AGREEMENT = "sentiment_agreement"
    SOURCE_MIX = "source_mix"


# ---------------------------------------------------------------------------
# A single pattern observation bucket
# ---------------------------------------------------------------------------

@dataclass
class PatternObservation:
    """One bucket of observations for a specific pattern dimension.

    Stores incremental statistics with DECAY: older wins/losses are
    de-weighted over time via a half-life approach (the `decay_factor`
    parameter). This ensures the pattern engine adapts to regime shifts.
    """
    category: PatternCategory
    bucket_id: str
    count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    # Decay-aware tracking: recent outcomes are stored in a sliding window
    # for cross-window consistency checks. _last_rates stores the last 100
    # individual outcomes (1.0 = win, 0.0 = loss).
    _last_rates: list[float] = field(default_factory=list)
    # Cross-window snapshots for stability measurement
    _window_snapshots: list[float] = field(default_factory=list)

    @property
    def loss_rate(self) -> float:
        if self.count == 0:
            return 0.0
        return self.losses / self.count

    @property
    def win_rate(self) -> float:
        if self.count == 0:
            return 0.5
        return self.wins / self.count

    @property
    def stage(self) -> PatternStage:
        return PatternStage.from_count(self.count)

    def record_outcome(self, won: bool, pnl: float) -> None:
        self.count += 1
        if won:
            self.wins += 1
        else:
            self.losses += 1
        self.total_pnl += pnl
        self._last_rates.append(1.0 if won else 0.0)
        if len(self._last_rates) > 100:
            self._last_rates.pop(0)
        # Take window snapshots every 10 outcomes for stability
        if self.count % 10 == 0:
            self._window_snapshots.append(self.win_rate)
            if len(self._window_snapshots) > 20:
                self._window_snapshots.pop(0)

    # ---- Significance (continuous, not a gate) ----

    def deviation_score(self) -> float:
        """How far is the observed win-rate from 0.5, as a continuous 0..1 score.
        0.0 = exactly 50/50, 1.0 = 100% or 0% win rate. No p-value involved."""
        return abs(self.win_rate - 0.5) * 2.0  # double so 75% -> 0.5, 100% -> 1.0

    def binomial_p_value(self) -> float:
        """Probability of observing >= wins (or >= losses) if true win-rate = 0.5.
        Uses normal approximation. Kept for reference logging only, NOT used as a
        gate for any decision."""
        if self.count < 6:
            return 1.0
        n = self.count
        k = max(self.wins, self.losses)
        z = (k - n / 2.0) / math.sqrt(n / 4.0)
        p = 0.5 * math.erfc(z / math.sqrt(2.0))
        return min(1.0, max(0.0, p))

    def statistical_significance_score(self) -> float:
        """Continuous 0..1 score. 0.0 = no evidence of a real pattern,
        1.0 = strong evidence. Replaces hard p<0.05 gate.

        Uses the deviation from 0.5 (effect size) modulated by sample size.
        Formula: significance = sqrt(count) * deviation / (1 + sqrt(count) / 5)
        This naturally penalises small samples and saturates as n grows.
        """
        if self.count < 6:
            return 0.0
        dev = self.deviation_score()
        # Effect-size-aware significance: needs BOTH deviation AND sample size
        n_factor = math.sqrt(self.count) / (1.0 + math.sqrt(self.count) / 5.0)
        raw = dev * n_factor
        return min(1.0, raw)

    # ---- Stability (cross-window consistency) ----

    def stability_score(self) -> float:
        """Continuous 0..1. 1.0 = win-rate is consistent across recent windows,
        0.0 = win-rate is highly erratic. Uses variance of window snapshots."""
        if len(self._window_snapshots) < 2:
            return 0.5  # not enough windows -> neutral
        mean = sum(self._window_snapshots) / len(self._window_snapshots)
        var = sum((r - mean) ** 2 for r in self._window_snapshots) / len(self._window_snapshots)
        # var of 0 -> 1.0 (perfectly stable). var of 0.25 (max) -> 0.0
        return max(0.0, 1.0 - var * 4.0)

    # ---- Decay-adjusted confidence ----

    def decay_adjusted_confidence(self, half_life: int = 20) -> float:
        """Weight recent outcomes more than old ones. Returns 0..1 confidence
        in the current win-rate estimate. Uses exponential decay weighting.

        half_life=20 means the weight of a window halves every 20 trades.
        In a scalping bot doing ~4 trades/min, half_life=20 = ~5 minutes.
        """
        if self.count < 6:
            return 0.0
        n = len(self._last_rates)
        if n < 6:
            return 0.3
        # Apply exponential decay weights: most recent gets highest weight
        weights = [math.exp(-(n - 1 - i) / half_life) for i in range(n)]
        w_sum = sum(weights)
        weighted_rate = sum(w * r for w, r in zip(weights, self._last_rates)) / w_sum
        # Convert to confidence: how far from 0.5 the weighted rate is
        confidence = abs(weighted_rate - 0.5) * 2.0
        return min(1.0, confidence)

    # ---- Combined assessment ----

    def pattern_strength(self) -> float:
        """Primary assessment: weighted combination of significance, stability,
        and decay-adjusted confidence. This is the SINGLE metric that determines
        how much a pattern affects decisions. Pure probabilistic - no gates."""
        sig = self.statistical_significance_score()
        stab = self.stability_score()
        dec = self.decay_adjusted_confidence()

        # Weight: significance and decay are the primary drivers,
        # stability acts as a dampener (high variance = less influence).
        strength = (sig * 0.40 + dec * 0.40) * stab
        return min(1.0, max(0.0, strength))

    @property
    def constraint_strength(self) -> float:
        """Continuous 0..1 measure of how close this pattern is to a "hard rule".
        Unlike the old binary is_hard_rule, this is probabilistic and requires
        BOTH high pattern_strength AND high stage_weight. Never reaches 1.0."""
        return self.pattern_strength() * self.stage.stage_weight()


# ---------------------------------------------------------------------------
# An emerged pattern
# ---------------------------------------------------------------------------

@dataclass
class EmergedPattern:
    """A pattern that has reached at least WEAK stage and carries decision impact.

    All three decision modifiers (risk_penalty_score, confidence_modifier,
    position_size_multiplier) are now continuous functions of pattern_strength
    rather than hard stage-bound constants.

    KEY CHANGE from v1:
    - risk_penalty_score = pattern_strength * loss_rate * 0.5  (continuous)
    - confidence_modifier = pattern_strength * deviation * 2.0  (continuous)
    - position_size_multiplier = 1.0 - pattern_strength * max(0, 0.5 - win_rate) * 2
    - is_hard_rule REMOVED — replaced by constraint_strength (continuous)
    """
    category: PatternCategory
    bucket_id: str
    observation: PatternObservation
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

    @property
    def stage(self) -> PatternStage:
        return self.observation.stage

    @property
    def ps(self) -> float:
        """Shorthand for pattern_strength (used repeatedly below)."""
        return self.observation.pattern_strength()

    @property
    def risk_penalty_score(self) -> float:
        """Continuous 0..1. penalty = ps * loss_rate * 0.5.
        Only meaningful when ps > 0.3 and stage >= WEAK. Gradual, not binary."""
        if self.stage == PatternStage.OBSERVATION:
            return 0.0
        loss_rate = self.observation.loss_rate
        return round(self.ps * loss_rate * 0.5, 4)

    @property
    def confidence_modifier(self) -> float:
        """Continuous -0.3 to +0.3. Modifier = ps * deviation * 1.5.
        Grows smoothly with pattern strength, not capped by stage."""
        if self.stage == PatternStage.OBSERVATION or self.observation.count < 6:
            return 0.0
        deviation = self.observation.win_rate - 0.5
        modifier = self.ps * deviation * 1.5
        return round(max(-0.3, min(0.3, modifier)), 4)

    @property
    def position_size_multiplier(self) -> float:
        """Continuous 0.5 to 1.2. Scales with ps and win_rate.
        Win rate above 0.5 -> multiplier > 1.0 (up to 1.2).
        Win rate below 0.5 -> multiplier < 1.0 (down to 0.5)."""
        if self.stage == PatternStage.OBSERVATION:
            return 1.0
        rate = self.observation.win_rate
        # Neutral at 0.5 win rate, scales with ps
        adjustment = self.ps * (rate - 0.5) * 0.8
        mult = 1.0 + adjustment
        return round(max(0.5, min(1.2, mult)), 4)

    def update(self, observation: PatternObservation) -> None:
        self.observation = observation
        self.last_updated = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Health metrics for the PatternEngine
# ---------------------------------------------------------------------------

@dataclass
class EngineHealth:
    """Live diagnostic metrics for model health monitoring.

    These are NOT used for decision-making — they are monitoring-only
    so the operator can detect degradation early."""
    overfitting_index: float = 0.0
    underfitting_index: float = 0.0
    stability_index: float = 0.0

    @property
    def interpretation(self) -> str:
        if self.overfitting_index > 0.7:
            return "OVERFITTED"
        if self.underfitting_index > 0.7:
            return "UNDERFITTED"
        if self.stability_index > 0.6:
            return "STABLE"
        return "TRANSIENT"


# ---------------------------------------------------------------------------
# The PatternEngine
# ---------------------------------------------------------------------------

class PatternEngine:
    """Core engine for probabilistic emergent rule learning (v2).

    Key design changes from v1:
    - No p-value gates anywhere
    - pattern_strength is a continuous composite of significance, stability, decay
    - All decision modifiers are continuous functions of pattern_strength
    - constraint_strength replaces the binary is_hard_rule
    - Window-based stability tracking for regime change detection
    - Built-in health metrics for live monitoring

    WARMUP: First 8 trades do NOT trigger any pattern formation (only recording).
    """

    def __init__(self, log, warmup_trades: int = 8):
        self.log = log
        self._warmup_remaining = warmup_trades
        self._observations: dict[str, PatternObservation] = {}
        self._emerged_patterns: dict[str, EmergedPattern] = {}
        self._total_trades_recorded: int = 0
        # Rolling health metrics (updated every 10 trades)
        self._recent_pnls: list[float] = []
        self._recent_edges: list[float] = []
        self._recent_brain_scores: list[float] = []
        self._health = EngineHealth()

    def health(self) -> EngineHealth:
        """Compute current health metrics from recent trading history.

        OVERFITTING INDEX: too reactive to recent outcomes.
          Measure: variance of recent PnL / mean absolute PnL.
          High = erratic, over-adapting to noise.

        UNDERFITTING INDEX: too insensitive, ignores signals.
          Measure: correlation between edge and outcome is near zero.
          Low = not learning from edge.

        STABILITY INDEX: consistent behaviour under similar inputs.
          Measure: pattern_strength variance across similar buckets.
          High = consistent learning signal.

        All metrics are 0..1 continuous values, NOT thresholds.
        """
        n = len(self._recent_pnls)
        if n < 20:
            return self._health

        # Overfitting index: high PnL variance relative to mean absolute PnL
        mean_abs = sum(abs(p) for p in self._recent_pnls) / max(1, n)
        var = sum(p ** 2 for p in self._recent_pnls) / n
        # If mean_abs is very small, overfitting is undefined -> 0.5 neutral
        overfit = min(1.0, var / max(0.001, mean_abs) * 0.1) if mean_abs > 0.001 else 0.5

        # Underfitting index: how often does positive edge correlate with wins?
        # If edge-positive trades win at ~50%, the system is not learning.
        if len(self._recent_edges) >= 10:
            edge_up_count = sum(1 for e, p in zip(self._recent_edges, self._recent_pnls)
                                if e > 0.02 and p > 0)
            edge_up_total = sum(1 for e in self._recent_edges if e > 0.02)
            if edge_up_total >= 5:
                win_rate_on_edge = edge_up_count / edge_up_total
                # underfit = how close to 0.5 (random) the win rate is
                underfit = 1.0 - abs(win_rate_on_edge - 0.5) * 2.0
            else:
                underfit = 0.5
        else:
            underfit = 0.5

        # Stability index: variance of pattern_strength across all emerged patterns
        strengths = [p.observation.pattern_strength() for p in self._emerged_patterns.values()]
        if len(strengths) >= 3:
            mean_s = sum(strengths) / len(strengths)
            var_s = sum((s - mean_s) ** 2 for s in strengths) / len(strengths)
            # Low variance = stable pattern formation. 1.0 - sqrt(var) is the stability.
            # var of 0 -> stability 1.0. var of 0.25 -> stability 0.5.
            stability = max(0.0, 1.0 - math.sqrt(var_s) * 2.0)
        else:
            stability = 0.5

        self._health = EngineHealth(
            overfitting_index=round(overfit, 4),
            underfitting_index=round(underfit, 4),
            stability_index=round(stability, 4),
        )
        return self._health

    def _record_trade_metric(self, edge: float, brain_score: float, pnl: float) -> None:
        """Store recent trade metrics for health computation."""
        self._recent_pnls.append(pnl)
        self._recent_edges.append(edge)
        self._recent_brain_scores.append(brain_score)
        for lst in [self._recent_pnls, self._recent_edges, self._recent_brain_scores]:
            if len(lst) > 100:
                lst.pop(0)

    # ---- observation key helpers ----

    def _key(self, category: PatternCategory, bucket_id: str) -> str:
        return f"{category.value}:{bucket_id}"

    def _get_or_create(self, category: PatternCategory, bucket_id: str) -> PatternObservation:
        key = self._key(category, bucket_id)
        if key not in self._observations:
            self._observations[key] = PatternObservation(category=category, bucket_id=bucket_id)
        return self._observations[key]

    # ---- bucketisation: map features to bucket IDs ----

    def _edge_bucket(self, edge: float) -> str:
        if edge <= 0.02:
            return "edge_0_0.02"
        if edge <= 0.05:
            return "edge_0.02_0.05"
        if edge <= 0.10:
            return "edge_0.05_0.10"
        if edge <= 0.20:
            return "edge_0.10_0.20"
        return "edge_0.20+"

    def _price_bucket(self, price: float) -> str:
        if price <= 0.2:
            return "price_0_0.2"
        if price <= 0.4:
            return "price_0.2_0.4"
        if price <= 0.6:
            return "price_0.4_0.6"
        if price <= 0.8:
            return "price_0.6_0.8"
        return "price_0.8_1.0"

    def _confidence_bucket(self, confidence: float) -> str:
        if confidence <= 0.5:
            return "conf_0_0.5"
        if confidence <= 0.7:
            return "conf_0.5_0.7"
        if confidence <= 0.85:
            return "conf_0.7_0.85"
        return "conf_0.85_1.0"

    def _brain_score_bucket(self, brain_score: float) -> str:
        if brain_score <= 0.25:
            return "brain_0_0.25"
        if brain_score <= 0.5:
            return "brain_0.25_0.5"
        if brain_score <= 0.75:
            return "brain_0.5_0.75"
        return "brain_0.75_1.0"

    def _sentiment_agreement_bucket(self, agreement: float) -> str:
        if agreement <= 0.3:
            return "agree_low"
        if agreement <= 0.7:
            return "agree_neutral"
        return "agree_high"

    # ---- recording outcomes ----

    def record_outcome(
        self,
        is_yes: bool,
        edge: float,
        confidence: float,
        brain_score: float,
        entry_price: float,
        spread: float,
        sentiment_agreement: float,
        won: bool,
        pnl: float,
    ) -> None:
        """Record one trade outcome across all relevant observation buckets."""
        self._total_trades_recorded += 1
        if self._warmup_remaining > 0:
            self._warmup_remaining -= 1

        # Record metrics for health computation
        self._record_trade_metric(edge, brain_score, pnl)

        # Side bias
        side_bucket = "YES" if is_yes else "NO"
        self._record_one(PatternCategory.SIDE_BIAS, side_bucket, won, pnl)

        # Edge range
        self._record_one(PatternCategory.EDGE_RANGE, self._edge_bucket(edge), won, pnl)

        # Confidence range
        self._record_one(PatternCategory.CONFIDENCE_RANGE, self._confidence_bucket(confidence), won, pnl)

        # Brain score range
        self._record_one(PatternCategory.BRAIN_SCORE_RANGE, self._brain_score_bucket(brain_score), won, pnl)

        # Price range
        self._record_one(PatternCategory.PRICE_RANGE, self._price_bucket(entry_price), won, pnl)

        # Spread range
        self._record_one(PatternCategory.SPREAD_RANGE, self._edge_bucket(spread), won, pnl)

        # Sentiment agreement
        self._record_one(PatternCategory.SENTIMENT_AGREEMENT, self._sentiment_agreement_bucket(sentiment_agreement), won, pnl)

        # Update emerged patterns from observations
        self._update_emerged_patterns()

        # Update health every 10 trades
        if self._total_trades_recorded % 10 == 0:
            self.health()

    def _record_one(self, category: PatternCategory, bucket_id: str, won: bool, pnl: float) -> None:
        obs = self._get_or_create(category, bucket_id)
        obs.record_outcome(won, pnl)

    def _update_emerged_patterns(self) -> None:
        """Check observations and create/update emerged patterns.
        No p-value gates. No binary conditions besides the warmup guard."""
        for key, obs in self._observations.items():
            if obs.stage == PatternStage.OBSERVATION and obs.count < 6:
                continue

            # During warmup: record observations but do NOT create emerged patterns
            if self._warmup_remaining > 0:
                continue

            if key in self._emerged_patterns:
                self._emerged_patterns[key].update(obs)
            elif obs.count >= 6:
                # New pattern emerged
                pattern = EmergedPattern(category=obs.category, bucket_id=obs.bucket_id, observation=obs)
                self._emerged_patterns[key] = pattern
                self.log.info(
                    "PatternEngine: NEW pattern [%s] %s (stage=%s, count=%d, strength=%.3f)",
                    obs.category.value, obs.bucket_id, obs.stage.name,
                    obs.count, obs.pattern_strength(),
                )

    # ---- evaluating a trade candidate ----

    def evaluate(
        self,
        is_yes: bool,
        edge: float,
        confidence: float,
        brain_score: float,
        entry_price: float,
        spread: float = 0.0,
        sentiment_agreement: float = 0.5,
    ) -> dict:
        """Evaluate a trade candidate against all emerged patterns.

        Returns continuous probabilistic modifiers, never binary gates.
        """
        result = {
            "risk_penalty_score": 0.0,
            "confidence_modifier": 0.0,
            "position_size_multiplier": 1.0,
            "constraint_strength": 0.0,       # replaces active_hard_rules
            "patterns_applied": [],
            "warmup": self._warmup_remaining > 0,
        }

        if self._warmup_remaining > 0:
            return result

        # Collect all potentially relevant patterns
        candidate_buckets = {
            PatternCategory.SIDE_BIAS: "YES" if is_yes else "NO",
            PatternCategory.EDGE_RANGE: self._edge_bucket(edge),
            PatternCategory.CONFIDENCE_RANGE: self._confidence_bucket(confidence),
            PatternCategory.BRAIN_SCORE_RANGE: self._brain_score_bucket(brain_score),
            PatternCategory.PRICE_RANGE: self._price_bucket(entry_price),
            PatternCategory.SPREAD_RANGE: self._edge_bucket(spread),
            PatternCategory.SENTIMENT_AGREEMENT: self._sentiment_agreement_bucket(sentiment_agreement),
        }

        max_constraint = 0.0
        for category, bucket_id in candidate_buckets.items():
            key = self._key(category, bucket_id)
            pattern = self._emerged_patterns.get(key)
            if pattern is None or not pattern.is_active:
                continue

            result["risk_penalty_score"] = max(
                result["risk_penalty_score"], pattern.risk_penalty_score
            )
            result["confidence_modifier"] += pattern.confidence_modifier
            result["position_size_multiplier"] = min(
                result["position_size_multiplier"], pattern.position_size_multiplier
            )
            max_constraint = max(max_constraint, pattern.observation.constraint_strength)
            result["patterns_applied"].append(f"{category.value}:{bucket_id}")

        result["constraint_strength"] = round(max_constraint, 4)
        result["confidence_modifier"] = max(-0.3, min(0.3, result["confidence_modifier"]))
        result["position_size_multiplier"] = max(0.5, min(1.2, result["position_size_multiplier"]))
        result["risk_penalty_score"] = max(0.0, min(1.0, result["risk_penalty_score"]))

        return result

    # ---- serialisation ----

    def to_saveable(self) -> dict:
        return {
            "warmup_remaining": self._warmup_remaining,
            "total_trades_recorded": self._total_trades_recorded,
            "observations": [
                {
                    "category": obs.category.value,
                    "bucket_id": obs.bucket_id,
                    "count": obs.count,
                    "wins": obs.wins,
                    "losses": obs.losses,
                    "total_pnl": obs.total_pnl,
                }
                for obs in self._observations.values()
            ],
            "emerged_patterns": [
                {
                    "category": pat.category.value,
                    "bucket_id": pat.bucket_id,
                    "is_active": pat.is_active,
                    "created_at": pat.created_at.isoformat(),
                    "last_updated": pat.last_updated.isoformat(),
                }
                for pat in self._emerged_patterns.values()
            ],
        }

    @classmethod
    def from_saveable(cls, data: dict, log) -> PatternEngine:
        engine = cls(log, warmup_trades=data.get("warmup_remaining", 8))
        engine._total_trades_recorded = data.get("total_trades_recorded", 0)

        for obs_data in data.get("observations", []):
            obs = PatternObservation(
                category=PatternCategory(obs_data["category"]),
                bucket_id=obs_data["bucket_id"],
                count=obs_data["count"],
                wins=obs_data["wins"],
                losses=obs_data["losses"],
                total_pnl=obs_data.get("total_pnl", 0.0),
            )
            key = engine._key(obs.category, obs.bucket_id)
            engine._observations[key] = obs

        for pat_data in data.get("emerged_patterns", []):
            category = PatternCategory(pat_data["category"])
            bucket_id = pat_data["bucket_id"]
            key = engine._key(category, bucket_id)
            obs = engine._observations.get(key)
            if obs is None:
                obs = PatternObservation(category=category, bucket_id=bucket_id)
                engine._observations[key] = obs
            pattern = EmergedPattern(
                category=category,
                bucket_id=bucket_id,
                observation=obs,
                is_active=pat_data.get("is_active", True),
                created_at=datetime.fromisoformat(pat_data["created_at"]) if "created_at" in pat_data else datetime.now(timezone.utc),
                last_updated=datetime.fromisoformat(pat_data["last_updated"]) if "last_updated" in pat_data else datetime.now(timezone.utc),
            )
            engine._emerged_patterns[key] = pattern

        return engine

    # ---- statistics / reporting ----

    def stats(self) -> dict:
        return {
            "total_trades_recorded": self._total_trades_recorded,
            "warmup_remaining": self._warmup_remaining,
            "total_observations": len(self._observations),
            "total_emerged_patterns": len(self._emerged_patterns),
            "patterns_by_stage": {
                stage.name: sum(1 for p in self._emerged_patterns.values() if p.stage == stage)
                for stage in PatternStage
            },
            "patterns_by_category": {
                cat.value: sum(1 for p in self._emerged_patterns.values() if p.category == cat)
                for cat in PatternCategory
            },
            "health": {
                "overfitting_index": self._health.overfitting_index,
                "underfitting_index": self._health.underfitting_index,
                "stability_index": self._health.stability_index,
                "interpretation": self._health.interpretation,
            },
        }

    def list_patterns(self) -> list[dict]:
        return [
            {
                "category": pat.category.value,
                "bucket_id": pat.bucket_id,
                "stage": pat.stage.name,
                "count": pat.observation.count,
                "win_rate": round(pat.observation.win_rate, 4),
                "loss_rate": round(pat.observation.loss_rate, 4),
                "risk_penalty": pat.risk_penalty_score,
                "conf_modifier": pat.confidence_modifier,
                "size_mult": pat.position_size_multiplier,
                "strength": round(pat.observation.pattern_strength(), 4),
                "constraint": round(pat.observation.constraint_strength, 4),
                "is_active": pat.is_active,
            }
            for pat in sorted(
                self._emerged_patterns.values(),
                key=lambda p: p.observation.count,
                reverse=True,
            )
        ]

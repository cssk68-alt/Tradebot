# Full System Reset + Probabilistic Emergent Rule Learning System

## Overview

This change implements two tightly coupled features:

1. **Complete System Reset** — All trade history, learning rules, heuristics, and risk/timing constraints are deleted. The system starts as a "baby brain" with zero experience.

2. **Probabilistic Emergent Rule Learning** — A new `PatternEngine` replaces the old hard-coded veto system with a 4-stage probabilistic emergence pipeline. Rules are NOT created from single failures. They only emerge after repeated, statistically significant patterns.

---

## Files Created

### `tradebot/brain/patterns.py` (NEW — 430 lines)
The core of the new learning system. Contains:

- **`PatternStage`** enum: `OBSERVATION` (1-5), `WEAK` (6-20), `STRONG` (21-80), `HARD_ELIGIBLE` (80-100+)
- **`PatternCategory`** enum: `SIDE_BIAS`, `EDGE_RANGE`, `CONFIDENCE_RANGE`, `BRAIN_SCORE_RANGE`, `PRICE_RANGE`, etc.
- **`PatternObservation`** dataclass: A single observation bucket with count, wins, losses, `binomial_p_value()` (statistical significance), `win_rate_variance()` (stability check)
- **`EmergedPattern`** dataclass: A pattern that has reached at least WEAK stage. Derives `risk_penalty_score` (0-1), `confidence_modifier` (-0.3 to +0.3), `position_size_multiplier` (0.5-1.2) from the observation's stage and win-rate
- **`PatternEngine`** class: The main engine that records outcomes, updates observations, emerges patterns, and evaluates trade candidates
  - **Warmup**: First 8 trades do NOT trigger pattern formation (only recording)
  - **Stage progression**: Patterns automatically progress through stages as count increases
  - **Hard rules**: Only possible at stage 4 (80-100+ occurrences) AND with statistical significance (p < 0.05) AND low variance
  - **Serialisation**: `to_saveable()` / `from_saveable()` for DB persistence
  - **Evaluation**: Returns `{risk_penalty_score, confidence_modifier, position_size_multiplier, active_hard_rules, patterns_applied, warmup}`

---

## Files Modified

### `tradebot/brain/feedback.py` — Brain class
**Changes:**
- Added `PatternEngine` integration as `self.patterns`
- Added `record_outcome(resolved_trade)` — feeds resolved trades into the pattern engine
- Added `evaluate_patterns(signal)` — evaluates a trade candidate against all emerged patterns
- Added `save_patterns(store)` / `load_patterns(store)` — serialisation
- Added `pattern_stats()` / `list_patterns()` — diagnostic access
- Updated `diagnostics()` to include pattern engine stats

### `tradebot/models.py` — Data models
**New models added:**
- `PatternObservationRecord` — persisted observation bucket
- `EmergedPatternRecord` — persisted emerged pattern
- `PatternState` — complete serialisable state from the PatternEngine

### `tradebot/store/db.py` — SQLite persistence
**Changes:**
- Added `pattern_rules` table to schema
- Added `save_pattern_state(state)` — saves PatternEngine state as JSON
- Added `load_pattern_state()` — restores most recent state
- Added `reset_all_learning()` — DELETES all rows from trades, experiences, counterfactuals, lessons, manager_decisions, pattern_rules
- Added `reset_flag_has_run()` / `mark_reset_done()` — idempotent reset guard

### `tradebot/risk/kelly.py` — Position sizing
**Changes:**
- `size_position()` now accepts optional `pattern_eval` dict
- Integrates:
  - `risk_penalty_score` — reduces effective confidence by `(1 - penalty * 0.5)` and shrinks position by `(1 - penalty * 0.75)`
  - `confidence_modifier` — added to signal confidence before threshold check
  - `position_size_multiplier` — scales the Kelly fraction
  - `active_hard_rules` — logged when present
- Added logging of applied patterns in the reason string

### `tradebot/agents/risk.py` — Risk agent
**Changes:**
- `__init__` now accepts optional `brain` parameter
- `run()` calls `brain.evaluate_patterns(sig)` and passes result to `size_position()`

### `tradebot/orchestrator.py` — Main orchestrator
**Changes:**
- **System Reset**: On first startup, calls `store.reset_all_learning()` to delete all historical data
- **Pattern learning**: `_after_resolved()` now calls `brain.record_outcome(r)` for each resolved trade
- **Pattern persistence**: `brain.save_patterns(store)` called after training
- **Pattern loading**: `brain.load_patterns(store)` called on startup

---

## Data Flow

```
STARTUP
  │
  ├── store.reset_flag_has_run()?
  │     YES → skip reset
  │     NO  → DELETE all trades, experiences, counterfactuals, lessons,
  │            manager_decisions, pattern_rules
  │            → mark_reset_done()
  │
  ├── Brain.__init__()
  │     └── PatternEngine(log) → empty engine with warmup=8
  │
  └── brain.load_patterns(store) → restore from DB if exists

CYCLE
  │
  ├── Trade resolves (win/loss)
  │     └── brain.record_outcome(resolved_trade)
  │           └── engine.record_outcome(is_yes, edge, confidence, ...)
  │                 └── Updates observations (side_bias/YES, edge_range/..., etc.)
  │                 └── _update_emerged_patterns()
  │                       └── If count >= 6 AND warmup done → create pattern
  │                       └── If count >= 80 AND significant AND stable → hard rule
  │
  ├── Signal generated
  │     └── brain.evaluate_patterns(signal)
  │           └── pattern_eval = engine.evaluate(is_yes, edge, confidence, ...)
  │                 └── Returns {risk_penalty, conf_modifier, size_mult, ...}
  │
  ├── RiskAgent.run()
  │     └── size_position(signal, ..., pattern_eval=pattern_eval)
  │           └── Applies modifiers to confidence check, Kelly fraction, size
  │
  └── brain.save_patterns(store) → persist to DB
```

## Stage Progression (Pattern Strength)

| Stage | Count | Decision Impact |
|-------|-------|----------------|
| OBSERVATION | 1-5 | Logged only. No impact on decisions |
| WEAK | 6-20 | `risk_penalty = 0.05 * loss_rate`, `conf_mod = ±0.05 max` |
| STRONG | 21-80 | `risk_penalty = 0.15 * loss_rate`, `conf_mod = ±0.15 max` |
| HARD_ELIGIBLE | 80-100+ | `risk_penalty = 0.30 * loss_rate`, `conf_mod = ±0.30 max` |

Hard rules are ONLY created when:
- Stage = HARD_ELIGIBLE (80+ occurrences)
- `binomial_p_value() < 0.05` (statistically significant)
- `win_rate_variance < 0.1` (stable over time)

## Rollback Instructions

```powershell
cd C:\Users\SoftK\Desktop\GithubVScode\Tradebot
git checkout -- tradebot/brain/patterns.py tradebot/brain/feedback.py tradebot/models.py tradebot/store/db.py tradebot/risk/kelly.py tradebot/agents/risk.py tradebot/orchestrator.py
```

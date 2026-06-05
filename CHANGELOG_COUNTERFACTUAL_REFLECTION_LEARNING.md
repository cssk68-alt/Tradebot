# Counterfactual Reflection Learning — Changelog

## Problem Diagnosis (from investigation)

**Root cause of "MLP veto score = 0.000":** The brain was trained on 155 experiences where ~87% of REAL trades were losses and 33% of rows were counterfactuals (mirror/veto simulations). The model learned an overfit binary mapping: most trades map to near-0.0 or near-1.0, with ~66% of training predictions near-0.0. This caused real-time brain_score < 0.01 for ~80% of live trade candidates, triggering brain veto.

**Root cause of pessimistic bias:** Counterfactual mirror trades (opposite side with negative edge) were treated as identical to real trades in training. Since mirror trades often have confusing feature patterns (negative edge + is_yes flipped), they polluted the decision boundary.

---

## Files Modified

### 1. `tradebot/brain/network.py` — Neural network core

**Changes:**
- Added sliding window `_recent_scores` tracking last 100 predictions for variance monitoring
- Added `predict_raw(features)` method — returns dict with `{score, z2_raw (pre-sigmoid), active_neurons}` for diagnostics
- Added `score_variance(window)` method — detects near-constant outputs (score collapse)
- Added `sample_weights` parameter to `train()` — scales per-row BCE gradient by sample weight
- Same additions to `TorchBrain` class for PyTorch backend compatibility
- Re-weighted default gradient from `(out - ya) / n` to `(out - ya) * sw` where sw is per-sample weight (with no weights provided, behaves identical to before)

**Backward compatible:** `train()` still works without `sample_weights` (defaults to uniform weighting)

### 2. `tradebot/brain/experience.py` — Training data preparation

**Changes:**
- Added `_CF_WEIGHT = 0.35` constant — counterfactual weight factor
- Added `to_weighted_xy(experiences, cf_weight=0.35)` function
  - Real trades: weight = 1.0
  - Counterfactual trades: weight = cf_weight (default 0.35)
- `to_xy()` unchanged for backward compatibility (still used by diagnostics/validation)

**Rationale:** 0.35 means ~3 real trades = 1 counterfactual in gradient contribution. This prevents mirror/veto simulations from dominating despite often being more numerous.

### 3. `tradebot/brain/feedback.py` — Brain manager

**Changes:**
- Added import: `to_weighted_xy`
- Added `_compatible_weighted_xy()` method — schema-filtered version returning (X, y, weights)
- Modified `train_from_experiences()`:
  - Now uses `_compatible_weighted_xy` instead of `_compatible_xy`
  - Passes `sample_weights=w` to `net.train()`
  - Log now shows real/cf/wins/losses breakdown
- Added `score_diagnostics(features)` — full diagnostic output for predict.py logging
- Added `check_score_collapse(threshold=0.001)` — logs warning if prediction variance drops below threshold

### 4. `tradebot/agents/predict.py` — Prediction agent (inference)

**Changes:**
- `self.brain.score(brain_feats)` → `self.brain.score_diagnostics(brain_feats)`
- Added diagnostic logging for low scores (< 0.01): logs `z2_raw` (pre-sigmoid) and `active_neurons`
- Added diagnostic logging for high scores (> 0.5)
- Added `self.brain.check_score_collapse()` call after processing all signals

### 5. `tradebot/orchestrator.py` — Main orchestrator

**Changes:**
- Added `settled_pairs` dictionary to track paired counterfactual outcomes per market_id
- Added `pass` placeholder for reflection insight logging (extensible for future paired analysis)

---

## Data Flow After Changes

```
Experience DB tables
    │
    ├── Real trades (weight=1.0) ──┐
    └── Counterfactual (weight=0.35) ──┤
                                       ▼
    to_weighted_xy() → X, y, sample_weights
                                       │
                                       ▼
    NeuralBrain.train(sample_weights=w)
    ─ Each row's BCE gradient scaled by its weight
    ─ Real trades contribute ~3x more than CF
                                       │
                                       ▼
    brain.npz (saved weights)
                                       │
                                       ▼
    predict.py → brain.score_diagnostics()
    ─ Logs brain_score, z2_raw, active_neurons
    ─ check_score_collapse() warns on low variance
```

## Diagnostic Signals Available

| Method | Returns | Purpose |
|--------|---------|---------|
| `brain.score()` | float 0..1 | Original interface, unchanged |
| `brain.score_diagnostics()` | `{brain_score, z2_raw, active_neurons}` | Logging in predict.py |
| `brain.check_score_collapse()` | bool (warning) | Post-batch variance check |
| `net.score_variance(window=20)` | float | Raw variance of recent predictions |

## Rollback Instructions

To revert all changes:

```powershell
cd C:\Users\SoftK\Desktop\GithubVScode\Tradebot
git checkout -- tradebot/brain/network.py tradebot/brain/feedback.py tradebot/brain/experience.py tradebot/agents/predict.py tradebot/orchestrator.py
```

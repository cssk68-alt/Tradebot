# Tradebot

A multi-agent prediction-market trading bot for **Polymarket**. It runs a
five-stage pipeline and a learning loop, defaults to a simulated **paper** mode,
and switches to **live** on-chain trading (real USDC on Polygon) only behind an
explicit per-trade confirmation.

## Pipeline

```
[1] Scan  ->  [2] Research  ->  [3] Prediction  ->  [4] Risk + Execution
 filter the     parallel RSS/      XGBoost + Claude     fractional Kelly +
 universe       Reddit + sentiment  -> calibrated edge   caps + brain veto
                                          ^                     |
                                          | brain-score         v  resolved trades
                                   [5] Brain (neural net)  <-----+  (wins & losses)
                                   learns from every outcome, carries over paper -> live
```

1. **Scan** (`agents/scan.py`) — pulls active markets from the public Gamma API,
   filters by liquidity / 24h volume / time-to-resolution, flags price moves and
   wide spreads.
2. **Research** (`agents/research.py`) — for each candidate, gathers free signal
   (Google News RSS + public Reddit JSON) in parallel and scores sentiment
   (Claude if an API key is set, else VADER, else an offline prior).
3. **Prediction** (`agents/predict.py`) — combines an XGBoost predictor, an
   optional Claude estimate, and the brain-score into a calibrated `true_prob`,
   then `edge = true_prob - price`. Emits a signal only past the edge threshold.
4. **Risk** (`agents/risk.py`, `risk/kelly.py`) — sizes with fractional Kelly,
   applies hard caps and the brain veto, then executes (paper fill or live order).
5. **Brain** (`brain/`) — a dependency-free neural network that learns
   `P(trade wins)` from every **resolved** trade (wins *and* losses). Its score
   feeds back into stages 3 and 4. Weights live in `data/brain.npz` and load in
   both modes, so what it learns in paper carries into live.

## Setup

```bash
pip install -e .
cp .env.example .env   # optional: add ANTHROPIC_API_KEY and live keys later
```

`numpy`, `pydantic`, `httpx`, `anthropic`, `xgboost` and friends are required;
`torch`/`feedparser` are optional (numpy net and stdlib RSS are used otherwise).

## Usage

```bash
# Stage 1 only — see the filtered shortlist (no key needed)
python -m tradebot.cli scan

# Full pipeline in paper mode (default), one cycle
python -m tradebot.cli run

# Paper loop — places, settles, and learns over several cycles
python -m tradebot.cli run --loop --iterations 8

# Live, but build + confirm WITHOUT sending (safe dry-run)
python -m tradebot.cli run --mode live --dry-run
```

In a single `run`, trades are placed but settle on the next cycle, so use
`--loop` to watch the brain accumulate experience and improve.

## Live trading (real money)

Live mode trades real USDC on Polygon via `py-clob-client`. It needs a funded
wallet and API credentials, and **every order requires explicit confirmation**.

```bash
# 1. put POLYMARKET_PRIVATE_KEY in .env, then derive API creds:
python scripts/derive_api_creds.py     # prints POLYMARKET_API_* lines for .env
# 2. set MODE=live and run (omit --dry-run to send real orders)
python -m tradebot.cli run --mode live
```

Secrets are read only from `.env` (git-ignored). Never commit real keys.

## Configuration

All knobs live in `.env` (see `.env.example`): `MODE`, `BANKROLL`,
`KELLY_FRACTION`, `MAX_TRADE_PCT`, `MAX_EXPOSURE_PCT`, `MIN_LIQUIDITY`,
`MIN_VOLUME_24H`, `EDGE_THRESHOLD`, `CONFIDENCE_THRESHOLD`, `BRAIN_WEIGHT`,
`BRAIN_VETO_THRESHOLD`, and the resolution-time window.

## Dashboard (GitHub Pages)

A simple static dashboard lives in `docs/` and reads `docs/dashboard/state.json`,
which is written automatically at the end of every `run` (or via
`python -m tradebot.cli export`). It shows bankroll / PnL, win rate, an equity
curve, the brain's status, recent trades, and lessons — no server needed.

Enable it once: repo **Settings → Pages → Source: Deploy from a branch →
Branch `main` (or your branch) / folder `/docs`**. The site is then at:

```
https://cssk68-alt.github.io/Tradebot/
```

Preview locally with any static server:

```bash
python -m tradebot.cli run --loop --iterations 12   # refresh the data
python -m http.server -d docs 8000                  # open http://localhost:8000
```

## Tests

```bash
pytest -q
```

Covers Kelly math, scan filters, paper fills/settlement, edge/side logic, and
that the brain learns and its weights persist across modes.

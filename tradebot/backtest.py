"""Monte-Carlo backtest of the strategy over synthetic markets.

Each market gets a hidden 'true' YES probability and a noisy market price; the
sentiment signal carries partial information about the truth (the bot's edge).
The real feature -> predictor -> Kelly pipeline runs on each, outcomes are
simulated, and aggregate performance is reported. Deterministic per seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from tradebot.ml.features import build_features
from tradebot.ml.model import Predictor
from tradebot.models import Market, ResearchReport, Side, Signal
from tradebot.risk.kelly import size_position


class _NullLog:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


@dataclass
class BacktestResult:
    n_markets: int
    n_trades: int
    wins: int
    losses: int
    win_rate: float
    start_bankroll: float
    end_bankroll: float
    roi: float
    total_pnl: float
    avg_edge: float
    max_drawdown: float
    equity_curve: list = field(default_factory=list)


def run_backtest(
    settings,
    n: int = 500,
    seed: int = 7,
    signal_strength: float = 0.6,
    price_noise: float = 0.06,
    sent_noise: float = 0.4,
    market_efficiency: float = 0.6,
) -> BacktestResult:
    rng = np.random.default_rng(seed)
    predictor = Predictor(_NullLog())
    start = float(settings.bankroll)
    bankroll = start
    equity = [round(bankroll, 2)]
    peak = bankroll
    maxdd = 0.0
    trades = wins = 0
    edge_sum = pnl_total = 0.0

    for _ in range(n):
        info = float(rng.uniform(-0.4, 0.4))
        true_yes = float(np.clip(0.5 + info, 0.02, 0.98))
        # The market only partially prices in the information (efficiency < 1),
        # leaving an exploitable edge; at efficiency == 1 there is no edge.
        market_price = float(
            np.clip(0.5 + market_efficiency * info + rng.normal(0, price_noise), 0.02, 0.98)
        )
        # Sentiment reveals the underlying information — this is the bot's signal.
        sentiment = float(np.clip(signal_strength * (info / 0.4) + rng.normal(0, sent_noise), -1, 1))
        m = Market(id="bt", question="bt", yes_price=market_price)
        feats = build_features(m, ResearchReport(market_id="bt", sentiment=sentiment), 0.0)
        pred_yes = predictor.predict_yes(feats)  # heuristic: price + 0.25 * sentiment

        if pred_yes >= market_price + settings.edge_threshold:
            is_yes, price, edge = True, market_price, pred_yes - market_price
        elif pred_yes <= market_price - settings.edge_threshold:
            is_yes, price, edge = False, 1.0 - market_price, (1.0 - pred_yes) - (1.0 - market_price)
        else:
            continue

        confidence = min(1.0, max(0.0, 0.5 + 2.0 * min(abs(edge), 0.25)))
        sig = Signal(
            market_id="bt", token_id="bt", question="bt", side=Side.BUY, market_price=price,
            true_prob=pred_yes, edge=edge, confidence=confidence, is_yes=is_yes, brain_score=0.6,
        )
        dec = size_position(sig, bankroll, settings, current_exposure=0.0, liquidity=1e9)
        if not dec.approved:
            continue

        outcome_yes = rng.random() < true_yes
        won = outcome_yes if is_yes else (not outcome_yes)
        pnl = dec.size * (1.0 - price) if won else -dec.size * price
        bankroll += pnl
        pnl_total += pnl
        trades += 1
        wins += 1 if won else 0
        edge_sum += edge
        peak = max(peak, bankroll)
        if peak > 0:
            maxdd = max(maxdd, (peak - bankroll) / peak)
        equity.append(round(bankroll, 2))

    return BacktestResult(
        n_markets=n,
        n_trades=trades,
        wins=wins,
        losses=trades - wins,
        win_rate=round(wins / trades, 4) if trades else 0.0,
        start_bankroll=round(start, 2),
        end_bankroll=round(bankroll, 2),
        roi=round((bankroll - start) / start, 4) if start else 0.0,
        total_pnl=round(pnl_total, 2),
        avg_edge=round(edge_sum / trades, 4) if trades else 0.0,
        max_drawdown=round(maxdd, 4),
        equity_curve=equity[-300:],
    )

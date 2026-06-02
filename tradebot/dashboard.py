"""Export a JSON snapshot of bot state for the static GitHub Pages dashboard.

The Python bot writes `docs/dashboard/state.json`; the static site reads it. This
is how the UI integrates with the code without needing a server.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tradebot.models import Mode, Trade


def _trade_dict(t: Trade) -> dict:
    return {
        "question": t.question,
        "side": "YES" if t.is_yes else "NO",
        "entry": round(t.entry_price, 3),
        "size": round(t.size, 1),
        "edge": round(t.edge, 3),
        "brain": round(t.brain_score, 3),
        "pnl": round(t.pnl, 2),
        "won": t.won,
        "mode": t.mode.value,
        "opened_at": t.opened_at.isoformat(),
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
    }


def build_state(store, settings, brain) -> dict:
    mode = Mode.LIVE if settings.mode == "live" else Mode.PAPER
    resolved = [t for t in store.resolved_trades() if t.mode == mode]
    resolved.sort(key=lambda t: t.resolved_at or t.opened_at)
    open_trades = store.open_trades(mode)
    experiences = store.load_experiences()

    n = len(resolved)
    wins = sum(1 for t in resolved if t.won)
    start = float(settings.bankroll)

    equity, cum = [], 0.0
    for t in resolved:
        cum += t.pnl
        equity.append(
            {
                "t": (t.resolved_at or t.opened_at).isoformat(),
                "pnl": round(t.pnl, 2),
                "cum": round(start + cum, 2),
            }
        )

    exp_wins = sum(1 for e in experiences if e.won)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode.value,
        "starting_bankroll": round(start, 2),
        "bankroll": round(start + store.realized_pnl(mode), 2),
        "realized_pnl": round(sum(t.pnl for t in resolved), 2),
        "n_trades": n,
        "n_wins": wins,
        "n_losses": n - wins,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "n_open": len(open_trades),
        "open_trades": [_trade_dict(t) for t in open_trades],
        "resolved_trades": [_trade_dict(t) for t in resolved][-50:],
        "equity_curve": equity[-300:],
        "brain": {
            "trained": bool(brain.trained),
            "experiences": len(experiences),
            "wins": exp_wins,
            "losses": len(experiences) - exp_wins,
        },
        "lessons": [
            {"category": l.category, "cause": l.cause, "recommendation": l.recommendation}
            for l in store.recent_lessons(12)
        ],
        "config": {
            "kelly_fraction": settings.kelly_fraction,
            "edge_threshold": settings.edge_threshold,
            "confidence_threshold": settings.confidence_threshold,
            "max_trade_pct": settings.max_trade_pct,
            "brain_veto_threshold": settings.brain_veto_threshold,
        },
    }


def export_state(store, settings, brain, out_path) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(build_state(store, settings, brain), indent=2))
    return p

"""Export a JSON snapshot of bot state for the static GitHub Pages dashboard.

The Python bot writes `docs/dashboard/state.json`; the static site reads it. This
is how the UI integrates with the code without needing a server.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tradebot.brain.hold_analysis import recommend_max_hold, scalp_hold_seconds
from tradebot.models import Mode, Trade
from tradebot.risk.circuit_breaker import circuit_breaker_reason


def _trade_dict(t: Trade) -> dict:
    return {
        "question": t.question,
        "side": "YES" if t.is_yes else "NO",
        "kind": getattr(t, "kind", "resolve"),
        "exec_style": getattr(t, "exec_style", "") or "",
        "entry": round(t.entry_price, 3),
        "exit": round(t.exit_price, 3) if t.exit_price is not None else None,
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
    pending_makers = store.pending_maker_trades(mode)
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

    won_holds, lost_holds = scalp_hold_seconds(resolved)
    hold_rec = recommend_max_hold(
        won_holds, lost_holds, getattr(settings, "max_hold_seconds", 300.0)
    )

    realized_today = store.realized_pnl_today(mode)
    streak = store.consecutive_losses(mode)
    cb_reason = circuit_breaker_reason(realized_today, start + store.realized_pnl(mode), streak, settings)

    # Brain diagnostics
    brain_diag = brain.diagnostics(experiences)
    brain_diag["counterfactuals"] = store.counterfactual_stats()
    real_exp = sum(1 for e in experiences if not getattr(e, "is_counterfactual", False))
    brain_diag["experiences"] = {
        "real": real_exp, "counterfactual": len(experiences) - real_exp, "total": len(experiences),
    }

    # ---- Model health (live) ----
    health = brain.health_metrics()

    # ---- Pattern engine state ----
    pe = brain.pattern_stats()
    active_patterns = pe.get("total_emerged_patterns", 0)
    patterns_by_stage = pe.get("patterns_by_stage", {})
    weak_p = patterns_by_stage.get("WEAK", 0)
    strong_p = patterns_by_stage.get("STRONG", 0)
    mature_p = patterns_by_stage.get("MATURE", 0)

    # ---- Flow health ----
    pattern_risk_ok = pe.get("total_trades_recorded", 0) > 0
    risk_kelly_ok = len(resolved) > 0
    kelly_exec_ok = len(resolved) + len(open_trades) > 0

    # ---- Last 10 trades summary ----
    # ---- For-The-Future Learner insights (observer-only meta-analysis) ----
    meta_insight = brain.meta_insight() if callable(getattr(brain, 'meta_insight', None)) else None

    last10 = resolved[-10:] if len(resolved) >= 10 else resolved
    avg_edge = sum(t.edge for t in last10) / max(1, len(last10))
    avg_conf = sum(t.brain_score for t in last10) / max(1, len(last10))

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
        "n_pending_maker": len(pending_makers),
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
        "hold_recommendation": hold_rec,
        "brain_diagnostics": brain_diag,
        # ---- Pattern Engine (v2 continuous probabilistic) ----
        "pattern_engine": {
            "active_patterns": active_patterns,
            "weak_patterns": weak_p,
            "strong_patterns": strong_p,
            "mature_patterns": mature_p,
            "observations": pe.get("total_observations", 0),
            "trades_recorded": pe.get("total_trades_recorded", 0),
        },
        # ---- Model health (live, not for decision-making) ----
        "model_health": {
            "overfitting_index": health.get("overfitting_index", 0.0),
            "underfitting_index": health.get("underfitting_index", 0.0),
            "stability_index": health.get("stability_index", 0.0),
            "interpretation": health.get("interpretation", "UNKNOWN"),
        },
        # ---- Execution flow health audit ----
        "execution_flow": {
            "pattern_to_risk": "OK" if pattern_risk_ok else "DEGRADED",
            "risk_to_kelly": "OK" if risk_kelly_ok else "DEGRADED",
            "kelly_to_execution": "OK" if kelly_exec_ok else "DEGRADED",
        },
        # ---- Last 10 trades summary ----
        "last_10_trades": {
            "n": len(last10),
            "wins": sum(1 for t in last10 if t.won),
            "losses": sum(1 for t in last10 if t.won is False),
            "pending": sum(1 for t in last10 if t.won is None),
            "avg_edge": round(avg_edge, 3),
            "avg_confidence": round(avg_conf, 3),
        },
        # ---- For-The-Future Learner Insights (observer-only) ----
        "for_the_future_learner": {
            "insight_summary": (meta_insight.get("insight_summary", [])
                                if meta_insight else []),
            "confidence_of_insight": (meta_insight.get("confidence_of_insight", 0.0)
                                      if meta_insight else 0.0),
            "category_tags": (meta_insight.get("category_tags", [])
                              if meta_insight else []),
            "suggested_future_hypotheses": (meta_insight.get("suggested_future_hypotheses", [])
                                            if meta_insight else []),
            "n_trades_analyzed": (meta_insight.get("n_trades_analyzed", 0)
                                  if meta_insight else 0),
            "generated_at": (meta_insight.get("generated_at", "")
                             if meta_insight else ""),
        },
        "circuit_breaker": {
            "tripped": bool(cb_reason),
            "reason": cb_reason or "",
            "realized_today": round(realized_today, 2),
            "consecutive_losses": streak,
        },
        "config": {
            "kelly_fraction": settings.kelly_fraction,
            "edge_threshold": settings.edge_threshold,
            "confidence_threshold": settings.confidence_threshold,
            "max_trade_pct": settings.max_trade_pct,
            "brain_veto_threshold": settings.brain_veto_threshold,
            "max_spread": getattr(settings, "max_spread", 0.03),
            "max_daily_loss_pct": getattr(settings, "max_daily_loss_pct", 0.0),
            "max_consecutive_losses": getattr(settings, "max_consecutive_losses", 0),
            "max_hold_seconds": getattr(settings, "max_hold_seconds", 300.0),
        },
    }


def format_ascii_dashboard(store, settings, brain) -> str:
    """Build the ASCII CLI dashboard (single source of truth for visualization).

    This renders the exact format requested: model health bars, pattern engine
    stats, execution flow health, and last 10 trades. Uses ASCII-safe characters
    for cross-platform display.
    """
    state = build_state(store, settings, brain)

    pe = state.get("pattern_engine", {})
    health = state.get("model_health", {})
    flow = state.get("execution_flow", {})
    last10 = state.get("last_10_trades", {})
    cb = state.get("circuit_breaker", {})

    overfit = health.get("overfitting_index", 0.0)
    underfit = health.get("underfitting_index", 0.0)
    stability = health.get("stability_index", 0.0)
    interp = health.get("interpretation", "UNKNOWN")

    def bar(v, w=12):
        filled = max(0, min(w, int(round(v * w))))
        return "#" * filled + "." * (w - filled)

    lines = [
        "=" * 50,
        "TRADEBOT LIVE SYSTEM HEALTH",
        "=" * 50,
        "",
        "Market State:",
        f"  Active Trades        : {state.get('n_open', 0)}",
        f"  Recent Signal Rate   : {state.get('win_rate', 0)*100:.0f}% win rate over {state.get('n_trades', 0)} trades",
        f"  Volatility Regime    : {'HIGH' if cb.get('tripped', False) else 'MEDIUM' if state.get('n_trades', 0) > 20 else 'LOW'}",
        "",
        "-" * 50,
        "MODEL HEALTH:",
        f"  Overfitting Index   : {bar(overfit)} {overfit:.2f}",
        f"  Underfitting Index  : {bar(underfit)} {underfit:.2f}",
        f"  Stability Index     : {bar(stability)} {stability:.2f}",
        "Interpretation Line:",
        f"  -> {interp}",
        "",
        "-" * 50,
        "PATTERN ENGINE:",
        f"  Active Patterns     : {pe.get('active_patterns', 0)}",
        f"  Weak Patterns       : {pe.get('weak_patterns', 0)}",
        f"  Strong Patterns     : {pe.get('strong_patterns', 0)}",
        f"  Mature Patterns     : {pe.get('mature_patterns', 0)}",
        f"  Observations        : {pe.get('observations', 0)}",
        f"  Trades Recorded     : {pe.get('trades_recorded', 0)}",
        "",
        "-" * 50,
        "RISK SYSTEM:",
        f"  Avg Risk Penalty    : {state.get('brain_diagnostics', {}).get('pattern_engine', {}).get('health', {}).get('overfitting_index', 0):.2f}",
        f"  Avg Position Size   : {state.get('bankroll', 0):.2f}",
        f"  Kelly Adjustment    : {state.get('config', {}).get('kelly_fraction', 0):.2f}",
        "",
        "-" * 50,
        "EXECUTION FLOW HEALTH:",
        f"  Pattern -> Risk     : {flow.get('pattern_to_risk', 'UNKNOWN')}",
        f"  Risk -> Kelly       : {flow.get('risk_to_kelly', 'UNKNOWN')}",
        f"  Kelly -> Execution  : {flow.get('kelly_to_execution', 'UNKNOWN')}",
        "",
        "-" * 50,
        "FOR-THE-FUTURE LEARNER INSIGHTS:",
    ]

    ftl = state.get("for_the_future_learner", {})
    if ftl.get("insight_summary"):
        for bullet in ftl["insight_summary"]:
            lines.append(f"  - {bullet}")
        lines.append(f"  Confidence: {ftl.get('confidence_of_insight', 0):.2f} | "
                     f"Categories: {', '.join(ftl.get('category_tags', []))}")
        lines.append(f"  Hypotheses: {', '.join(ftl.get('suggested_future_hypotheses', []))}")
        lines.append(f"  (Analyzed {ftl.get('n_trades_analyzed', 0)} trades)")
    else:
        lines.append("  No insights yet (waiting for resolved trades)")

    lines.extend([
        "",
        "-" * 50,
        "LAST 10 TRADES:",
    ])

    resolved = state.get("resolved_trades", [])[-10:]
    for t in reversed(resolved):
        w = t.get("won")
        sym = "V" if w else ("X" if w is False else "-")
        lines.append(
            f"  {sym} {t.get('side','?')} {t.get('question','?')[:30]:30s} "
            f"edge={t.get('edge',0):.2f} conf={t.get('brain',0):.2f} "
            f"pnl={t.get('pnl',0):+.2f}"
        )

    lines.extend([
        f"  Avg Edge        : {last10.get('avg_edge', 0):.2f}",
        f"  Avg Confidence  : {last10.get('avg_confidence', 0):.2f}",
        "",
        "=" * 50,
    ])
    return "\n".join(lines)


def export_state(store, settings, brain, out_path) -> Path:
    """Write the dashboard snapshot atomically (temp file + os.replace) so a reader
    never observes a half-written state.json."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(build_state(store, settings, brain), indent=2)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return p

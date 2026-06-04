"""Tradebot command-line interface."""
from __future__ import annotations

import typer

from tradebot.config import get_settings
from tradebot.log import get_logger

app = typer.Typer(add_completion=False, help="Multi-agent prediction-market bot for Polymarket.")
log = get_logger("tradebot")


def _build_orchestrator(s, **kw):
    """Construct the Orchestrator, turning the no-agent hard-fail into a clean exit."""
    from tradebot.llm import LLMUnavailableError
    from tradebot.orchestrator import Orchestrator

    try:
        return Orchestrator(s, log, **kw)
    except LLMUnavailableError as e:
        log.error("%s", e)
        raise typer.Exit(1)


@app.command()
def scan():
    """Stage 1 only: scan + filter markets and print the shortlist."""
    from tradebot.agents.scan import ScanAgent
    from tradebot.data.gamma import GammaClient
    from tradebot.store.db import Store

    s = get_settings()
    store = Store(s.db_path)
    candidates = ScanAgent(s, store, log).run(GammaClient(log).fetch_markets())
    for c in candidates:
        m = c.market
        log.info(
            "  %-45s yes=%.2f vol=%-9.0f liq=%-8.0f flags=%s",
            m.question[:45], m.yes_price, m.volume_24h, m.liquidity,
            ",".join(c.flags) or "-",
        )


@app.command()
def run(
    mode: str = typer.Option(None, help="paper | live (overrides .env)"),
    strategy: str = typer.Option(None, help="scalp | resolve (overrides .env)"),
    loop: bool = typer.Option(False, help="run multiple cycles instead of one"),
    iterations: int = typer.Option(5, help="cycles to run with --loop"),
    interval: float = typer.Option(0.0, help="seconds to sleep between cycles"),
    dry_run: bool = typer.Option(False, help="live mode: build + confirm but DON'T send orders"),
):
    """Run the full pipeline (scan -> research -> predict -> risk -> execute -> learn)."""
    s = get_settings()
    if mode:
        s.mode = mode
    if strategy:
        s.strategy = strategy
    orch = _build_orchestrator(s, dry_run=dry_run)
    if loop:
        orch.run_loop(iterations=iterations, interval=interval)
    else:
        orch.run_once()


@app.command()
def scalp(
    minutes: float = typer.Option(30.0, help="how long to keep scalping"),
    interval: float = typer.Option(60.0, help="seconds between cycles (poll + close)"),
    mode: str = typer.Option(None, help="paper | live (overrides .env)"),
):
    """Short-horizon loop: open AND close positions within minutes at REAL prices.

    Paper mode learns from real price moves (net of spread) without risking money."""
    import time as _time

    s = get_settings()
    s.strategy = "scalp"
    if mode:
        s.mode = mode
    orch = _build_orchestrator(s)
    deadline = _time.time() + minutes * 60.0
    i = 0
    while True:
        i += 1
        log.info("---- scalp cycle %d ----", i)
        orch.run_once()
        if orch.breaker_reason:  # circuit breaker: stop opening, then wind down
            log.warning("Circuit breaker — stopping scalp loop: %s", orch.breaker_reason)
            break
        if _time.time() >= deadline:
            break
        _time.sleep(interval)
    orch.manage_open(orch.exchange.list_markets())  # final sweep at the latest price


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", help="confirm: wipe trades/experiences/brain"),
):
    """Start clean: delete ALL trades, experiences, lessons and brain weights.

    Use this once to drop the old simulated-outcome history before real learning."""
    from pathlib import Path

    from tradebot.store.db import Store

    s = get_settings()
    if not yes:
        log.error("Deletes ALL trades/experiences/lessons + brain. Re-run with: reset --yes")
        raise typer.Exit(1)
    store = Store(s.db_path)
    store.conn.executescript(
        "DELETE FROM trades; DELETE FROM experiences; DELETE FROM lessons; "
        "DELETE FROM snapshots; DELETE FROM manager_decisions; DELETE FROM counterfactuals;"
    )
    store.conn.commit()
    bp = Path(s.brain_path)
    if bp.exists():
        bp.unlink()
    log.info("Reset complete — next run learns from real data only.")


@app.command()
def backtest(
    n: int = typer.Option(500, help="number of synthetic markets"),
    seed: int = typer.Option(7),
    signal: float = typer.Option(0.6, help="signal strength 0..1 (the edge the bot can see)"),
):
    """Monte-Carlo backtest of the strategy over synthetic markets."""
    from tradebot.backtest import run_backtest

    r = run_backtest(get_settings(), n=n, seed=seed, signal_strength=signal)
    log.info(
        "Backtest: %d markets -> %d trades | win-rate %.1f%% | ROI %+.1f%% | "
        "PnL %+.2f | avg-edge %.3f | max-DD %.1f%%",
        r.n_markets, r.n_trades, r.win_rate * 100, r.roi * 100, r.total_pnl,
        r.avg_edge, r.max_drawdown * 100,
    )
    log.info("Bankroll: %.2f -> %.2f", r.start_bankroll, r.end_bankroll)


@app.command()
def settle(
    mode: str = typer.Option(None, help="paper | live (overrides .env)"),
    loop: bool = typer.Option(False, help="keep polling"),
    interval: float = typer.Option(60.0, help="seconds between polls when --loop"),
    iterations: int = typer.Option(0, help="max polls with --loop (0 = until none open)"),
):
    """Poll for resolution of open trades and settle them (live-settlement polling)."""
    import time as _time

    s = get_settings()
    if mode:
        s.mode = mode
    orch = _build_orchestrator(s)
    i = 0
    while True:
        resolved = orch.settle_open()
        open_left = len(orch.store.open_trades(orch.mode))
        log.info("settle: %d resolved this poll, %d still open", len(resolved), open_left)
        i += 1
        if not loop or (iterations and i >= iterations) or (iterations == 0 and open_left == 0):
            break
        _time.sleep(interval)


@app.command("brain-report")
def brain_report():
    """Print brain validation: out-of-sample metrics, feature importance, veto scoreboard."""
    from tradebot.brain.feedback import Brain
    from tradebot.store.db import Store

    s = get_settings()
    store = Store(s.db_path)
    brain = Brain(s.brain_path, log, l2=float(getattr(s, "brain_l2", 0.0)))
    exps = store.load_experiences()
    diag = brain.diagnostics(exps)
    cf = store.counterfactual_stats()
    real = sum(1 for e in exps if not e.is_counterfactual)

    log.info("Experiences: %d total (%d real, %d counterfactual)", len(exps), real, len(exps) - real)
    oos = diag.get("oos", {})
    if oos.get("status") == "ok":
        log.info(
            "Out-of-sample: acc %.3f | logloss %.3f | AUC %.3f (train %d / test %d)",
            oos["accuracy"], oos["logloss"], oos["auc"], oos["n_train"], oos["n_test"],
        )
    else:
        log.info("Out-of-sample: insufficient data (train %s / test %s)",
                 oos.get("n_train"), oos.get("n_test"))
    imp = diag.get("feature_importance", [])
    if imp:
        log.info("Top features (permutation importance):")
        for f in imp:
            log.info("  %-22s %+.4f", f["name"], f["importance"])
    log.info(
        "Veto scoreboard: %d settled | %d vetos right (would have lost) / %d too strict (would have won) | %d pending",
        cf["settled"], cf["brain_right"], cf["brain_wrong"], cf["pending"],
    )


@app.command()
def export(out: str = typer.Option(None, help="output path for the dashboard state.json")):
    """Write the dashboard snapshot (docs/dashboard/state.json) from the current DB."""
    from tradebot.brain.feedback import Brain
    from tradebot.dashboard import export_state
    from tradebot.store.db import Store

    s = get_settings()
    store = Store(s.db_path)
    brain = Brain(s.brain_path, log, l2=float(getattr(s, "brain_l2", 0.0)))
    path = export_state(store, s, brain, out or s.dashboard_path)
    log.info("Wrote dashboard state to %s", path)


@app.command()
def serve(port: int = typer.Option(8080, help="port to listen on")):
    """Start the local dashboard + settings server at http://localhost:PORT."""
    from tradebot.server import serve as _serve

    _serve(port=port, open_browser=True)


@app.command("derive-creds")
def derive_creds():
    """Derive Polymarket API creds from POLYMARKET_PRIVATE_KEY (prints .env lines)."""
    from tradebot.exchange.polymarket import derive_api_creds

    s = get_settings()
    if not s.polymarket_private_key:
        log.error("Set POLYMARKET_PRIVATE_KEY in .env first.")
        raise typer.Exit(1)
    creds = derive_api_creds(s, log)
    if creds:
        log.info("Add these lines to your .env:")
        for k, v in creds.items():
            print(f"POLYMARKET_{k.upper()}={v}")


if __name__ == "__main__":
    app()

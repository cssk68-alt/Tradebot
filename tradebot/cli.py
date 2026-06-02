"""Tradebot command-line interface."""
from __future__ import annotations

import typer

from tradebot.config import get_settings
from tradebot.log import get_logger

app = typer.Typer(add_completion=False, help="Multi-agent prediction-market bot for Polymarket.")
log = get_logger("tradebot")


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
    loop: bool = typer.Option(False, help="run multiple cycles instead of one"),
    iterations: int = typer.Option(5, help="cycles to run with --loop"),
    interval: float = typer.Option(0.0, help="seconds to sleep between cycles"),
    dry_run: bool = typer.Option(False, help="live mode: build + confirm but DON'T send orders"),
):
    """Run the full pipeline (scan -> research -> predict -> risk -> execute -> learn)."""
    from tradebot.orchestrator import Orchestrator

    s = get_settings()
    if mode:
        s.mode = mode
    orch = Orchestrator(s, log, dry_run=dry_run)
    if loop:
        orch.run_loop(iterations=iterations, interval=interval)
    else:
        orch.run_once()


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

    from tradebot.orchestrator import Orchestrator

    s = get_settings()
    if mode:
        s.mode = mode
    orch = Orchestrator(s, log)
    i = 0
    while True:
        resolved = orch.settle_open()
        open_left = len(orch.store.open_trades(orch.mode))
        log.info("settle: %d resolved this poll, %d still open", len(resolved), open_left)
        i += 1
        if not loop or (iterations and i >= iterations) or (iterations == 0 and open_left == 0):
            break
        _time.sleep(interval)


@app.command()
def export(out: str = typer.Option(None, help="output path for the dashboard state.json")):
    """Write the dashboard snapshot (docs/dashboard/state.json) from the current DB."""
    from tradebot.brain.feedback import Brain
    from tradebot.dashboard import export_state
    from tradebot.store.db import Store

    s = get_settings()
    store = Store(s.db_path)
    brain = Brain(s.brain_path, log)
    path = export_state(store, s, brain, out or s.dashboard_path)
    log.info("Wrote dashboard state to %s", path)


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

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

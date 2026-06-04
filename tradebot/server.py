"""Local dashboard server — stdlib only, no extra dependencies.

Serves docs/ as static files and exposes two JSON endpoints:
  GET  /api/config  → merged defaults + data/config.json overrides
  POST /api/config  → persist overrides to data/config.json
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CONFIG_PATH = ROOT / "data" / "config.json"

DEFAULTS: dict = {
    "bankroll": 1000.0,
    "kelly_fraction": 0.25,
    "max_trade_pct": 0.05,
    "max_exposure_pct": 0.5,
    "min_liquidity": 500.0,
    "min_volume_24h": 1000.0,
    "max_spread": 0.03,  # spread-based market-quality gate (Teil A.1)
    "edge_threshold": 0.05,
    "confidence_threshold": 0.6,
    "brain_weight": 0.3,
    "brain_veto_threshold": 0.35,
    "aggressiveness": 0.0,  # Risk-Adjuster knob (Seite 2), 0..1
    "max_slippage": 0.02,
    "min_days_to_resolution": 1.0,
    "max_days_to_resolution": 30.0,
    # circuit breaker (Teil B.2) — 0 disables that arm
    "max_daily_loss_pct": 0.05,
    "max_consecutive_losses": 5,
    # maker-first execution (Teil B.3)
    "maker_first": True,
    "maker_min_edge": 0.03,
    "maker_timeout_seconds": 60.0,
    # short-horizon scalping
    "max_hold_seconds": 300.0,
    "take_profit": 0.02,
    "stop_loss": 0.03,
    "min_net_profit": 0.005,
    # Dashboard run controls (Seite 1) — persisted here in UI-native units so the
    # sliders survive a page reload (front + back in the same place as Seite 2).
    "run_interval": 60.0,        # seconds between cycles
    "run_max_eur": 1.0,          # euro budget cap (0 = unlimited)
    "run_max_runtime_min": 60.0,  # total runtime cap in MINUTES (0 = unlimited)
    # Which Seite-2 preset is active ("frei" = free sliders, else a named preset).
    # Purely a UI bookmark so the highlight survives a reload; the slider VALUES
    # remain the single source of truth, and Settings ignores this unknown key.
    "preset": "frei",
}


def _load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception:
            pass
    return cfg


def _atomic_write_json(path: Path, obj: object) -> None:
    """Write JSON via a temp file + os.replace so a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class _Runner:
    """Runs the orchestrator loop in a background thread (paper, or gated live).

    The HTTP server stays responsive because the blocking cycle (DeepSeek/Gamma
    calls) runs off-thread. Live trading can only be armed with an explicit
    confirm token, so a stray POST can never start real-money trading."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._force = threading.Event()  # 2nd Stop while winding down = hard abort
        self.mode = ""
        self.strategy = ""
        self.interval = 60.0
        self.cycle = 0
        self.last = ""
        self.error = ""
        self.started_at = ""
        self.cost = 0.0
        self.max_eur = 0.0
        self.max_runtime = 0.0
        self.stop_reason = ""
        self.draining = False  # winding down: finishing open trades, opening none

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        return {
            "running": self.running, "mode": self.mode, "strategy": self.strategy,
            "cycle": self.cycle, "last": self.last, "error": self.error,
            "started_at": self.started_at, "cost": round(self.cost, 4),
            "max_eur": self.max_eur, "max_runtime": self.max_runtime,
            "stop_reason": self.stop_reason, "draining": self.draining,
        }

    def start(self, mode: str, strategy: str, interval: float, confirm: str,
              max_eur: float = 0.0, max_runtime: float = 0.0) -> dict:
        with self._lock:
            if self.running:
                return {"ok": False, "error": "läuft bereits"}
            mode = "live" if str(mode).lower() == "live" else "paper"
            # Server-side guard: live needs the explicit token even if the UI is
            # bypassed — defense in depth for real money.
            if mode == "live" and confirm != "LIVE":
                return {"ok": False, "error": "Live erfordert confirm=LIVE"}
            self._stop.clear()
            self._force.clear()
            self.draining = False
            self.mode, self.strategy = mode, strategy or "scalp"
            self.interval = max(5.0, float(interval))
            self.max_eur = max(0.0, float(max_eur))         # 0 = unbegrenzt
            self.max_runtime = max(0.0, float(max_runtime))  # seconds, 0 = unbegrenzt
            self.cycle, self.last, self.error, self.stop_reason = 0, "", "", ""
            self.cost = 0.0
            import time as _t

            self.started_at = _t.strftime("%H:%M:%S")
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return {"ok": True, **self.status()}

    def stop(self) -> dict:
        # First Stop = graceful: stop opening new trades, but let the open ones
        # finish (see _wind_down). A second Stop *while winding down* forces a hard
        # abort, leaving any still-open trades to the `settle` poller.
        if self.draining:
            self._force.set()
            return {"ok": True, "forcing": True}
        self._stop.set()
        return {"ok": True, "stopping": True}

    def _loop(self) -> None:
        try:
            import time as _t

            from tradebot.config import get_settings
            from tradebot.log import get_logger
            from tradebot.orchestrator import Orchestrator

            s = get_settings()
            s.mode, s.strategy = self.mode, self.strategy
            log = get_logger("tradebot")
            # Live orders were gated at start (confirm == "LIVE"); auto-approve here
            # so the background loop is not blocked on stdin. Paper ignores confirm.
            confirm = (lambda order: True) if self.mode == "live" else None
            orch = Orchestrator(s, log, confirm=confirm)
            deadline = (_t.time() + self.max_runtime) if self.max_runtime else None
            while not self._stop.is_set():
                # Stop on whichever cap is hit first, BEFORE starting a new cycle.
                if deadline and _t.time() >= deadline:
                    self.stop_reason = f"Laufzeit-Limit erreicht ({self.max_runtime / 60:.0f} min)"
                    break
                if self.max_eur and self.cost >= self.max_eur:
                    self.stop_reason = f"Budget erreicht (€{self.cost:.4f} >= €{self.max_eur:.2f})"
                    break
                self.cycle += 1
                placed = orch.run_once()
                self.cost = orch.client.cost_eur
                self.last = f"Zyklus {self.cycle}: {len(placed)} Trade(s), €{self.cost:.4f}"
                # Circuit breaker (Teil B.2): stop opening new trades, then wind
                # down the open ones gracefully (no abandon).
                if orch.breaker_reason:
                    self.stop_reason = f"Circuit-Breaker: {orch.breaker_reason}"
                    self.log.warning("Stopping run — %s", self.stop_reason)
                    break
                self._stop.wait(self.interval)
            # Stop / cap reached: don't abandon live positions — wind down (open
            # nothing new, but keep closing the open ones until the book is flat).
            self._wind_down(orch, log)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self._stop.set()

    def _wind_down(self, orch, log) -> None:
        """Graceful Stop: open NO new positions, but let the OPEN ones finish.

        A Stop (or a hit budget/runtime cap) must never abandon a live position.
        Scalp trades exit on price (take-profit / stop-loss / max-hold), so they
        need active polling: keep calling ``manage_open`` — opening nothing new —
        until the book is flat or a safety deadline (max_hold + margin) passes.
        Resolve trades settle only at the real market resolution (days away): one
        settle sweep, then they stay in the DB for the ``settle`` poller — they are
        persisted, never lost. A second Stop while winding down sets ``_force`` and
        aborts this hard, handing any leftovers to ``settle`` too."""
        import time as _t

        self.draining = True
        try:
            strategy = getattr(orch.settings, "strategy", self.strategy or "scalp")

            def _sweep() -> int:
                try:
                    orch.manage_open(orch.exchange.list_markets())
                except Exception as e:  # never let a sweep crash the wind-down
                    log.warning("wind-down manage_open failed: %s", e)
                self.cost = orch.client.cost_eur
                return len(orch.store.open_trades(orch.mode))

            open_left = _sweep()

            if strategy != "scalp":
                # resolve: can't force-close before the market resolves; the open
                # trades persist in the DB for the settle poller — not abandoned.
                self.last = f"Gestoppt — {open_left} offene Trade(s) warten auf Resolution"
                self.stop_reason = (self.stop_reason or "Gestoppt") + (
                    f" — {open_left} offene Trade(s) an settle-Poller uebergeben"
                    if open_left else " — keine offenen Trades"
                )
                return

            # scalp: actively drain until flat or the safety deadline. A scalp
            # position closes within max_hold_seconds at the latest, so this is
            # bounded; the margin covers the final poll + settlement.
            deadline = _t.time() + float(getattr(orch.settings, "max_hold_seconds", 300.0)) + 120.0
            poll = max(5.0, min(self.interval, 30.0))
            while open_left and not self._force.is_set():
                self.last = f"Beende offene Trades … {open_left} noch offen"
                if _t.time() >= deadline:
                    self.stop_reason = (self.stop_reason or "Gestoppt") + (
                        f" — Zeitlimit, {open_left} Trade(s) noch offen (settle uebernimmt)"
                    )
                    return
                self._force.wait(poll)
                open_left = _sweep()

            if self._force.is_set() and open_left:
                self.last = f"Hart gestoppt — {open_left} Trade(s) offen"
                self.stop_reason = (self.stop_reason or "Gestoppt") + (
                    f" — hart abgebrochen, {open_left} Trade(s) offen (settle uebernimmt)"
                )
            else:
                self.last = "Alle offenen Trades beendet"
                self.stop_reason = (self.stop_reason or "Gestoppt") + " — alle offenen Trades beendet"
        finally:
            self.draining = False


RUNNER = _Runner()


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS), **kwargs)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/config":
            self._json(_load_config())
        elif self.path == "/api/state":
            p = DOCS / "dashboard" / "state.json"
            self._json(json.loads(p.read_text()) if p.exists() else {})
        elif self.path == "/api/status":
            self._json(RUNNER.status())
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/config":
            data = self._read_json()
            # MERGE, don't overwrite: Seite 1 (run controls) and Seite 2 (strategy)
            # each POST only their own keys, so a partial save must not wipe the
            # other page's values. Start from what is already stored and update.
            merged = _load_config()
            merged.update({k: data[k] for k in DEFAULTS if k in data})
            _atomic_write_json(CONFIG_PATH, merged)
            self._json({"ok": True})
        elif self.path == "/api/run":
            d = self._read_json()
            self._json(RUNNER.start(
                d.get("mode", "paper"), d.get("strategy", "scalp"),
                d.get("interval", 60.0), d.get("confirm", ""),
                d.get("max_eur", 0.0), d.get("max_runtime", 0.0),
            ))
        elif self.path == "/api/stop":
            self._json(RUNNER.stop())
        else:
            self.send_response(404)
            self.end_headers()

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            return {}

    def _json(self, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def end_headers(self):
        # Local dev dashboard: don't cache, so edits to HTML/JS/CSS show up on a
        # plain refresh instead of a stale cached copy (the slider bug).
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # silence per-request noise
        pass


def serve(port: int = 8080, open_browser: bool = True) -> None:
    server = HTTPServer(("localhost", port), _Handler)
    url = f"http://localhost:{port}"
    print(f"\n  Dashboard    →  {url}")
    print(f"  Einstellungen →  {url}/settings.html")
    print("  (Strg+C oder Fenster schließen zum Beenden)\n")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=[url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
    finally:
        server.server_close()

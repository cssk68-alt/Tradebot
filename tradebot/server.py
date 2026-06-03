"""Local dashboard server — stdlib only, no extra dependencies.

Serves docs/ as static files and exposes two JSON endpoints:
  GET  /api/config  → merged defaults + data/config.json overrides
  POST /api/config  → persist overrides to data/config.json
"""
from __future__ import annotations

import json
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
    "edge_threshold": 0.05,
    "confidence_threshold": 0.6,
    "brain_weight": 0.3,
    "brain_veto_threshold": 0.35,
    "max_slippage": 0.02,
    "min_days_to_resolution": 1.0,
    "max_days_to_resolution": 30.0,
}


def _load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception:
            pass
    return cfg


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
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/config":
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n))
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            clean = {k: data[k] for k in DEFAULTS if k in data}
            CONFIG_PATH.write_text(json.dumps(clean, indent=2))
            self._json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()

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

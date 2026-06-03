"""Structured logging helper (uses rich if available)."""
from __future__ import annotations

import logging

try:  # pragma: no cover - cosmetic
    from rich.logging import RichHandler

    _handler: logging.Handler = RichHandler(rich_tracebacks=True, show_path=False)
    _fmt = "%(message)s"
except Exception:  # pragma: no cover
    _handler = logging.StreamHandler()
    _fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        logging.basicConfig(
            level=logging.INFO, format=_fmt, datefmt="%H:%M:%S", handlers=[_handler]
        )
        # Silence the repetitive third-party HTTP chatter ("HTTP Request: POST
        # ... 200 OK", one line per LLM call). The requests still happen — we
        # just stop logging every success. Real warnings/errors still get through.
        for _noisy in ("httpx", "httpcore", "urllib3"):
            logging.getLogger(_noisy).setLevel(logging.WARNING)
        _configured = True
    return logging.getLogger(name)

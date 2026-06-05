"""Background watchdog: fires if no heartbeat for TIMEOUT_S seconds.

Usage
-----
* Call ``beat()`` at every stage boundary and inside LLM calls.
* Check ``fired.is_set()`` in the main loop; if True, skip the rest of the
  cycle and call ``reset()`` before the next one.

When the watchdog fires it dumps the stack-trace of every live thread (so a
hung httpx call or blocked DB write is immediately visible) and sets the
``fired`` event so the orchestrator can abort the stalled cycle cleanly.

Windows note: signal.alarm is not available on Windows, so a background
daemon thread is used instead.  The thread cannot forcibly kill a hung
call — that is handled by the per-provider SDK timeouts (60 s).  The
watchdog's job is purely diagnostic + cycle-abort on unexpected freezes.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

TIMEOUT_S: float = 120.0
_CHECK_INTERVAL_S: float = 30.0

# Module-level state — one global watchdog per process.
_last_beat: float = time.monotonic()
fired: threading.Event = threading.Event()
_log = None  # injected by start()


def beat() -> None:
    """Reset the inactivity timer.  Call this at every stage boundary."""
    global _last_beat
    _last_beat = time.monotonic()
    fired.clear()


def reset() -> None:
    """Clear the fired flag at the start of a new cycle."""
    fired.clear()
    beat()


def start(log=None, timeout_s: float = TIMEOUT_S) -> None:
    """Start the background watchdog thread (idempotent — safe to call twice)."""
    global _log, TIMEOUT_S
    _log = log
    TIMEOUT_S = timeout_s
    t = threading.Thread(target=_run, name="watchdog", daemon=True)
    t.start()


# --- internals ---

def _run() -> None:
    while True:
        time.sleep(_CHECK_INTERVAL_S)
        age = time.monotonic() - _last_beat
        if age > TIMEOUT_S:
            _fire(age)


def _fire(age: float) -> None:
    msg = (
        f"WATCHDOG: no activity for {age:.0f}s "
        f"(limit {TIMEOUT_S:.0f}s) — dumping all thread stacktraces"
    )
    if _log:
        _log.error(msg)
    else:
        print(msg, file=sys.stderr)

    frames = sys._current_frames()
    for tid, frame in frames.items():
        name = next((t.name for t in threading.enumerate() if t.ident == tid), str(tid))
        stack = "".join(traceback.format_stack(frame))
        if _log:
            _log.error("Thread [%s]:\n%s", name, stack)
        else:
            print(f"Thread [{name}]:\n{stack}", file=sys.stderr)

    fired.set()
    beat()  # reset timer so we don't spam on every check interval

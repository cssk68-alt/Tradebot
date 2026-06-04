"""Tests for graceful Stop: the server runner must finish OPEN trades on stop
instead of abandoning them (server._Runner._wind_down).

Stop semantics:
  * stop opening NEW positions immediately,
  * but keep closing the OPEN ones (scalp: poll until flat; resolve: hand to settle),
  * a 2nd stop while winding down = hard abort.

These exercise the pure control flow with a fake orchestrator — no network, and
``_force.wait`` is stubbed so the drain loop never actually sleeps."""
from types import SimpleNamespace

from tradebot.log import get_logger
from tradebot.models import Mode
from tradebot.server import _Runner

log = get_logger("t")


class _FakeExchange:
    def list_markets(self):
        return []


class _FakeStore:
    def __init__(self, open_ids):
        self._open = list(open_ids)

    def open_trades(self, mode):
        return list(self._open)


class _FakeOrch:
    """Closes ``close_per_sweep`` open trades on every ``manage_open`` call, so a
    drain loop terminates deterministically without any real price/time passing."""

    def __init__(self, open_ids, strategy="scalp", close_per_sweep=1):
        self.settings = SimpleNamespace(strategy=strategy, max_hold_seconds=300.0)
        self.exchange = _FakeExchange()
        self.store = _FakeStore(open_ids)
        self.client = SimpleNamespace(cost_eur=0.0)
        self.mode = Mode.PAPER
        self._close_per_sweep = close_per_sweep
        self.sweeps = 0

    def manage_open(self, markets):
        self.sweeps += 1
        for _ in range(self._close_per_sweep):
            if self.store._open:
                self.store._open.pop()


def _runner() -> _Runner:
    r = _Runner()
    r.strategy = "scalp"
    r.interval = 5.0
    return r


def test_stop_is_graceful_then_forces():
    r = _Runner()
    # First stop, not winding down yet -> graceful request.
    assert r.stop() == {"ok": True, "stopping": True}
    assert r._stop.is_set()
    # While winding down a 2nd stop becomes a hard abort.
    r.draining = True
    out = r.stop()
    assert out.get("forcing") is True
    assert r._force.is_set()


def test_wind_down_scalp_drains_until_flat():
    r = _runner()
    orch = _FakeOrch(["a", "b", "c"], strategy="scalp", close_per_sweep=1)
    r._force.wait = lambda timeout=None: False  # don't actually sleep between sweeps
    r._wind_down(orch, log)
    assert orch.store.open_trades(Mode.PAPER) == []  # book is flat
    assert "alle offenen Trades beendet" in r.stop_reason
    assert r.draining is False


def test_wind_down_resolve_hands_off_to_settle():
    # Resolve trades settle only at the real market resolution -> one sweep, then
    # they PERSIST for the settle poller (not force-closed, not abandoned).
    r = _runner()
    orch = _FakeOrch(["a", "b"], strategy="resolve", close_per_sweep=0)
    r._wind_down(orch, log)
    assert orch.sweeps == 1
    assert len(orch.store.open_trades(Mode.PAPER)) == 2
    assert "settle" in r.stop_reason.lower()
    assert r.draining is False


def test_wind_down_hard_abort_leaves_trades_for_settle():
    r = _runner()
    orch = _FakeOrch(["a", "b", "c"], strategy="scalp", close_per_sweep=0)
    r._force.set()  # a 2nd stop already forced a hard abort
    r._wind_down(orch, log)
    assert len(orch.store.open_trades(Mode.PAPER)) == 3  # nothing force-closed
    assert orch.sweeps == 1                              # only the pre-loop sweep
    assert "hart abgebrochen" in r.stop_reason
    assert r.draining is False

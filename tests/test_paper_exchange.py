from pathlib import Path

from tradebot.config import Settings
from tradebot.data.gamma import GammaClient
from tradebot.exchange.paper import PaperExchange
from tradebot.log import get_logger
from tradebot.models import Mode, Order, Side


def _ex(tmp):
    s = Settings(db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz")
    log = get_logger("t")
    return PaperExchange(GammaClient(log), log, s)


def _order(price=0.4, is_yes=True):
    return Order(
        market_id="m", token_id="t", question="q", side=Side.BUY, is_yes=is_yes,
        price=price, size=100, mode=Mode.PAPER,
    )


def test_paper_fill_is_open_at_price(tmp_path):
    tr = _ex(tmp_path).place_order(_order())
    assert tr.status == "open"
    assert tr.entry_price == 0.4
    assert tr.is_yes is True


def test_paper_settle_win_pnl(tmp_path):
    ex = _ex(tmp_path)
    tr = ex.place_order(_order())
    tr.features = [0.4] + [0.0] * 9
    r = ex.settle(tr, force_yes=True)
    assert r.status == "resolved" and r.won is True
    assert abs(r.pnl - 100 * (1 - 0.4)) < 1e-6


def test_paper_settle_loss_pnl(tmp_path):
    ex = _ex(tmp_path)
    tr = ex.place_order(_order())
    tr.features = [0.4] + [0.0] * 9
    r = ex.settle(tr, force_yes=False)
    assert r.won is False
    assert abs(r.pnl + 100 * 0.4) < 1e-6

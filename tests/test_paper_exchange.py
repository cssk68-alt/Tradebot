from pathlib import Path

from tradebot.config import Settings
from tradebot.data.gamma import GammaClient
from tradebot.exchange.paper import PaperExchange
from tradebot.log import get_logger
from tradebot.models import Market, Mode, Order, Side, Trade


def _ex(tmp):
    s = Settings(db_path=Path(tmp) / "t.db", brain_path=Path(tmp) / "b.npz")
    log = get_logger("t")
    return PaperExchange(GammaClient(log), log, s)


def _order(price=0.4, is_yes=True):
    return Order(
        market_id="m", token_id="t", question="q", side=Side.BUY, is_yes=is_yes,
        price=price, size=100, mode=Mode.PAPER,
    )


def _market(yes=0.4, bid=0.395, ask=0.405):
    return Market(id="m", question="q", yes_price=yes, best_bid=bid, best_ask=ask)


def test_paper_fill_is_open_at_price(tmp_path):
    tr = _ex(tmp_path).place_order(_order())
    assert tr.status == "open"
    assert tr.entry_price == 0.4
    assert tr.is_yes is True


def test_paper_settle_win_pnl(tmp_path):
    # hold-to-event settlement uses the REAL resolution (here forced for the test)
    ex = _ex(tmp_path)
    tr = ex.place_order(_order())
    r = ex.settle(tr, force_yes=True)
    assert r.status == "resolved" and r.won is True and r.kind == "resolve"
    assert abs(r.pnl - 100 * (1 - 0.4)) < 1e-6


def test_paper_settle_loss_pnl(tmp_path):
    ex = _ex(tmp_path)
    tr = ex.place_order(_order())
    r = ex.settle(tr, force_yes=False)
    assert r.won is False
    assert abs(r.pnl + 100 * 0.4) < 1e-6


def test_scalp_close_profit_net_of_spread(tmp_path):
    # entry 0.40, price moved to 0.45, tight spread 0.004 -> floored to min 0.01
    ex = _ex(tmp_path)
    tr = ex.place_order(_order(price=0.40))
    r = ex.close(tr, _market(yes=0.45, bid=0.448, ask=0.452), reason="take_profit")
    assert r.status == "resolved" and r.kind == "scalp" and r.won is True
    # 100 * (0.45 - 0.40 - 0.01) = 4.0
    assert abs(r.pnl - 4.0) < 1e-6
    assert r.exit_price == 0.45


def test_scalp_flat_price_loses_the_spread(tmp_path):
    # price unchanged -> you still pay the round-trip spread (0.01) => small loss
    ex = _ex(tmp_path)
    tr = ex.place_order(_order(price=0.40))
    r = ex.close(tr, _market(yes=0.40, bid=0.395, ask=0.405), reason="time")
    assert r.won is False
    assert abs(r.pnl + 1.0) < 1e-6  # 100 * (0 - 0.01)


def test_scalp_no_side_uses_complement_price(tmp_path):
    # NO position: token price = 1 - yes_price. entry 0.60 (NO), yes drops 0.45->0.40
    # so NO price rises 0.55 -> 0.60; flat-ish move, spread floor applies
    ex = _ex(tmp_path)
    tr = ex.place_order(_order(price=0.55, is_yes=False))
    r = ex.close(tr, _market(yes=0.40, bid=0.398, ask=0.402), reason="take_profit")
    # NO price now 1 - 0.40 = 0.60; pnl = 100*(0.60 - 0.55 - 0.01) = 4.0
    assert r.won is True and abs(r.pnl - 4.0) < 1e-6


def _open_trade(entry, size, is_yes=True):
    return Trade(market_id="m", token_id="t", question="q", side=Side.BUY,
                 is_yes=is_yes, entry_price=entry, size=size, mode=Mode.PAPER, status="open")


def test_lowprice_scalp_spread_floor_is_tick(tmp_path):
    # Regression (the Elon −16.80 bug): a 0.0015 longshot buys a huge share count
    # (size = stake/price ≈ 1680 for a $2.52 stake). The OLD flat 0.01 spread floor ×
    # 1680 shares lost $16.80 on a $2.52 stake. Now the floor is one tick (0.001 near
    # the extremes), so a flat exit loses ~$1.68 — never the phantom $16.80.
    ex = _ex(tmp_path)
    r = ex.close(_open_trade(0.0015, 1679.7), _market(yes=0.0015, bid=0.001, ask=0.002), "time")
    assert abs(r.pnl - (-1.68)) < 0.01          # size × tick(0.001), not size × 0.01
    assert r.pnl > -16.0                          # nowhere near the old catastrophe


def test_long_scalp_loss_capped_at_stake(tmp_path):
    # A long can never lose more than its stake (price floors at 0). Even if the price
    # crashes to ~0, the loss is capped at size × entry — not blown past it by the
    # spread term × a huge share count.
    ex = _ex(tmp_path)
    stake = 1679.7 * 0.0015
    r = ex.close(_open_trade(0.0015, 1679.7), _market(yes=0.0, bid=0.0, ask=0.001), "stop_loss")
    assert abs(r.pnl - (-stake)) < 1e-6          # exactly the stake, the cap binds

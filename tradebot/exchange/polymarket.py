"""Live Polymarket execution via py-clob-client.

SAFETY: every real-money order is gated behind an explicit confirmation callback,
and ``dry_run`` builds + confirms the order without sending it. Exact client method
names and response shapes vary across py-clob-client versions, so calls and the
response parsing in ``_parse_execution`` are defensive and may need tuning to the
installed client version.

Two production-critical invariants are enforced here:

* BUY (``place_order``): a Trade is recorded only when the API confirms a fill
  (``filled_size > 0``). An accepted-but-resting maker order opens no position.
* SELL (``close``): the trade is marked ``resolved`` only after a confirmed SELL
  (or in dry-run). If the SELL fails or is not accepted, the trade STAYS OPEN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from tradebot.exchange.base import Exchange, mark_yes_no, settle_from_resolution
from tradebot.exchange.execution_style import decide_execution_style
from tradebot.exchange.ticks import get_tick_size, round_to_tick
from tradebot.models import Market, Mode, Order, Resolution, Trade


@dataclass
class ExecutionResult:
    """Normalized view of a py-clob-client order response."""

    accepted: bool
    filled_size: float
    avg_price: Optional[float]
    order_id: Optional[str]
    raw: dict = field(default_factory=dict)


class PolymarketExchange(Exchange):
    HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137  # Polygon

    def __init__(self, gamma, log, settings, dry_run: bool = False):
        super().__init__(gamma, log)
        self.settings = settings
        self.dry_run = dry_run
        self._client = None

    @property
    def mode(self) -> Mode:
        return Mode.LIVE

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            client = ClobClient(
                host=self.HOST, chain_id=self.CHAIN_ID,
                key=self.settings.polymarket_private_key,
            )
            if self.settings.polymarket_api_key:
                client.set_api_creds(
                    ApiCreds(
                        api_key=self.settings.polymarket_api_key,
                        api_secret=self.settings.polymarket_api_secret,
                        api_passphrase=self.settings.polymarket_api_passphrase,
                    )
                )
            else:
                client.set_api_creds(_derive(client))
            self._client = client
        except Exception as e:  # pragma: no cover - needs live deps/keys
            self.log.error("Polymarket client unavailable: %s", e)
            self._client = None
        return self._client

    def place_order(
        self, order: Order, confirm: Optional[Callable[[Order], bool]] = None
    ) -> Optional[Trade]:
        # Maker-first decision (Teil B.3): when the edge is large enough, rest a
        # passive maker bid for a window; otherwise take liquidity immediately.
        plan = decide_execution_style(order.price, order.edge, order.spread, self.settings)
        if self.dry_run:
            self.log.info(
                "[DRY-RUN] would place LIVE order: %s %s %.2f x %.1f (cost $%.2f) "
                "exec=%s (%s) — not sent",
                order.question, "YES" if order.is_yes else "NO",
                order.price, order.size, order.cost, plan.style, plan.reason,
            )
            return None
        # SAFETY: real money — require explicit confirmation first.
        if confirm is not None and not confirm(order):
            self.log.warning("Live order ABORTED before sending: %s", order.question)
            return None
        client = self._client_or_none()
        if client is None:
            self.log.error("No live client available; order not placed.")
            return None

        # Try the maker leg first; if it does not fill within the window, cancel
        # it and fall through to a taker order (never leaves a stray resting order).
        if plan.style == "maker":  # pragma: no cover - needs live deps/keys
            trade = self._try_maker(client, order, plan)
            if trade is not None:
                return trade
            self.log.info(
                "Maker leg unfilled within timeout — falling back to taker for '%s'",
                order.question[:40],
            )
        return self._place_taker(client, order)  # pragma: no cover - needs live deps/keys

    def _place_taker(self, client, order: Order) -> Optional[Trade]:  # pragma: no cover
        """Cross the spread with a marketable GTC order (the original execution path)."""
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            args = OrderArgs(
                token_id=order.token_id, price=round_to_tick(order.price),
                size=order.size, side=BUY,
            )
            resp = client.post_order(client.create_order(args), OrderType.GTC)
        except Exception as e:
            self.log.error("Live taker order failed: %s", e)
            return None

        result = _parse_execution(resp)
        if not result.accepted:
            self.log.warning("Live order not accepted; no position opened: %s", result.raw)
            return None
        if result.filled_size <= 0:
            # Accepted but resting — no position yet, so do NOT record a Trade.
            self.log.info(
                "Live order accepted but unfilled (resting order %s); no trade recorded.",
                result.order_id,
            )
            return None
        self.log.info(
            "Live taker filled: %.1f sh @ %s (id %s)",
            result.filled_size, result.avg_price, result.order_id,
        )
        return self._trade_from(order, result, "taker")

    def _try_maker(self, client, order: Order, plan) -> Optional[Trade]:  # pragma: no cover
        """Post a passive maker bid at plan.limit_price and poll for a fill up to
        ``maker_timeout_seconds``. Returns a filled Trade, or None (after canceling
        the resting order) so the caller can fall back to a taker order."""
        import time as _t

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            args = OrderArgs(
                token_id=order.token_id, price=round_to_tick(plan.limit_price),
                size=order.size, side=BUY,
            )
            resp = client.post_order(client.create_order(args), OrderType.GTC)
        except Exception as e:
            self.log.error("Maker post failed (%s); will try taker.", e)
            return None

        result = _parse_execution(resp)
        if not result.accepted:
            self.log.warning("Maker order not accepted: %s", result.raw)
            return None
        if result.filled_size > 0:  # crossed immediately
            self.log.info("Maker filled immediately: %.1f sh @ %s", result.filled_size, result.avg_price)
            return self._trade_from(order, result, "maker")

        order_id = result.order_id
        timeout = float(getattr(self.settings, "maker_timeout_seconds", 60.0))
        deadline = _t.time() + max(0.0, timeout)
        self.log.info("Maker resting (id %s) up to %.0fs at %.3f", order_id, timeout, plan.limit_price)
        while _t.time() < deadline:
            _t.sleep(min(3.0, max(0.5, timeout / 10.0)))
            status = self._order_status(client, order_id)
            if status is not None and status.filled_size > 0:
                self.log.info("Maker filled: %.1f sh @ %s", status.filled_size, status.avg_price)
                return self._trade_from(order, status, "maker")

        # Timed out unfilled: cancel so the taker leg cannot double-fill.
        self._cancel(client, order_id)
        return None

    def _order_status(self, client, order_id) -> Optional["ExecutionResult"]:  # pragma: no cover
        if not order_id:
            return None
        for name in ("get_order", "get_order_status"):
            fn = getattr(client, name, None)
            if fn is None:
                continue
            try:
                return _parse_execution(fn(order_id))
            except Exception as e:
                self.log.warning("Maker status check failed (%s): %s", name, e)
                return None
        return None

    def _cancel(self, client, order_id) -> None:  # pragma: no cover
        if not order_id:
            return
        for name in ("cancel", "cancel_order"):
            fn = getattr(client, name, None)
            if fn is None:
                continue
            try:
                fn(order_id)
                self.log.info("Canceled resting maker order %s", order_id)
                return
            except Exception as e:
                self.log.warning("Cancel of maker order %s failed (%s): %s", order_id, name, e)
                return

    def _trade_from(self, order: Order, result: "ExecutionResult", style: str) -> Trade:  # pragma: no cover
        return Trade(
            market_id=order.market_id, token_id=order.token_id, question=order.question,
            side=order.side, is_yes=order.is_yes,
            entry_price=result.avg_price if result.avg_price is not None else order.price,
            size=result.filled_size, mode=Mode.LIVE, status="open", exec_style=style,
        )

    def settle(
        self,
        trade: Trade,
        force_yes: Optional[bool] = None,
        resolution: Optional[Resolution] = None,
    ) -> Optional[Trade]:
        if force_yes is not None:
            return mark_yes_no(trade, bool(force_yes))
        res = resolution if resolution is not None else self.gamma.get_resolution(trade.market_id)
        return settle_from_resolution(trade, res, self.log)

    def close(self, trade: Trade, market: Market, reason: str = "time") -> Optional[Trade]:
        """Scalp exit on the live book: SELL the position back at the current price.

        The trade is marked resolved ONLY after a confirmed SELL (or in dry-run).
        If the client is missing, the SELL raises, or the response is not accepted,
        the trade is left OPEN and None is returned — never a phantom close."""
        cur = market.yes_price if trade.is_yes else 1.0 - market.yes_price
        # Floor the spread at one tick (realistic minimum), and cap the loss at the
        # stake — a long can never lose more than it paid (price floors at 0).
        spread = max(market.spread, get_tick_size(trade.entry_price))
        pnl = trade.size * (cur - trade.entry_price - spread)
        pnl = max(pnl, -trade.size * trade.entry_price)

        if self.dry_run:
            self.log.info(
                "[DRY-RUN] would SELL to close (%s): %s %.0f sh @ %.3f (pnl %+.2f) — not sent",
                reason, "YES" if trade.is_yes else "NO", trade.size, cur, pnl,
            )
        else:  # pragma: no cover - needs live deps/keys
            client = self._client_or_none()
            if client is None:
                self.log.error(
                    "Live close skipped: no client; trade %s remains OPEN.", trade.market_id
                )
                return None
            try:
                from py_clob_client.clob_types import OrderArgs, OrderType
                from py_clob_client.order_builder.constants import SELL

                args = OrderArgs(
                    token_id=trade.token_id, price=round_to_tick(cur),
                    size=trade.size, side=SELL,
                )
                resp = client.post_order(client.create_order(args), OrderType.GTC)
            except Exception as e:
                self.log.error(
                    "Live close failed; trade %s remains OPEN: %s", trade.market_id, e
                )
                return None
            if not _is_order_accepted_or_filled(resp):
                self.log.error(
                    "Live close not accepted; trade %s remains OPEN: %s", trade.market_id, resp
                )
                return None
            self.log.info("Live SELL posted (%s): %s", reason, resp)

        trade.exit_price = round(cur, 4)
        trade.pnl = pnl
        trade.won = pnl > 0
        trade.kind = "scalp"
        trade.status = "resolved"
        trade.resolved_at = datetime.now(timezone.utc)
        return trade


# Statuses py-clob-client may report for an accepted/working/filled order.
_OK_STATUSES = {"matched", "open", "live", "filled", "delayed", "success"}


def _parse_execution(resp: Any) -> ExecutionResult:
    """Best-effort normalization of a py-clob-client order response.

    Deliberately permissive about shape (dict or object) but conservative about
    the verdict: an unrecognized response with no success flag, no known status
    and no reported fill is treated as NOT accepted."""
    d: dict = resp if isinstance(resp, dict) else dict(getattr(resp, "__dict__", {}) or {})
    status = str(d.get("status", "")).lower()
    filled = _to_float(
        d.get("filled_size", d.get("filledSize", d.get("size_matched", d.get("sizeMatched", 0.0))))
    )
    avg = _to_float(d.get("avg_price", d.get("avgPrice", d.get("price", 0.0)))) or None
    order_id = d.get("orderID") or d.get("order_id") or d.get("id")
    success = d.get("success")
    accepted = (status in _OK_STATUSES) or (success is True) or (filled > 0.0)
    return ExecutionResult(
        accepted=accepted,
        filled_size=filled,
        avg_price=avg,
        order_id=str(order_id) if order_id is not None else None,
        raw=d,
    )


def _is_order_accepted_or_filled(resp: Any) -> bool:
    """True only if the API positively acknowledges the order (status/success/fill)."""
    result = _parse_execution(resp)
    return result.accepted or result.filled_size > 0.0


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _derive(client):  # pragma: no cover - needs live deps/keys
    for name in ("create_or_derive_api_creds", "create_or_derive_api_key"):
        fn = getattr(client, name, None)
        if fn is not None:
            return fn()
    raise RuntimeError("py-clob-client: no credential-derivation method found")


def derive_api_creds(settings, log) -> Optional[dict]:
    """Derive Polymarket API creds from the wallet private key (one-time setup)."""
    try:  # pragma: no cover - needs live deps/keys
        from py_clob_client.client import ClobClient

        client = ClobClient(
            host=PolymarketExchange.HOST, chain_id=PolymarketExchange.CHAIN_ID,
            key=settings.polymarket_private_key,
        )
        creds = _derive(client)
        return {
            "api_key": getattr(creds, "api_key", ""),
            "api_secret": getattr(creds, "api_secret", ""),
            "api_passphrase": getattr(creds, "api_passphrase", ""),
        }
    except Exception as e:
        log.error("Could not derive creds: %s", e)
        return None

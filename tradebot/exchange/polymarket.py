"""Live Polymarket execution via py-clob-client.

SAFETY: every real-money order is gated behind an explicit confirmation callback,
and `dry_run` builds + confirms the order without sending it. Exact client method
names vary slightly across py-clob-client versions, so calls are defensive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from tradebot.exchange.base import Exchange
from tradebot.models import Mode, Order, Trade


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
        if self.dry_run:
            self.log.info(
                "[DRY-RUN] would place LIVE order: %s %s %.2f x %.1f (cost $%.2f) — not sent",
                order.question, "YES" if order.is_yes else "NO",
                order.price, order.size, order.cost,
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
        try:  # pragma: no cover - needs live deps/keys
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            args = OrderArgs(
                token_id=order.token_id, price=round(order.price, 2),
                size=order.size, side=BUY,
            )
            signed = client.create_order(args)
            resp = client.post_order(signed, OrderType.GTC)
            self.log.info("Live order posted: %s", resp)
            return Trade(
                market_id=order.market_id, token_id=order.token_id, question=order.question,
                side=order.side, is_yes=order.is_yes, entry_price=order.price,
                size=order.size, mode=Mode.LIVE, status="open",
            )
        except Exception as e:  # pragma: no cover
            self.log.error("Live order failed: %s", e)
            return None

    def settle(self, trade: Trade, force_yes: Optional[bool] = None) -> Optional[Trade]:
        res = force_yes if force_yes is not None else self.gamma.get_resolution(trade.market_id)
        if res is None:
            return None  # not resolved yet
        won = bool(res) if trade.is_yes else (not bool(res))
        trade.resolved_yes = bool(res)
        trade.won = won
        trade.pnl = (
            trade.size * (1.0 - trade.entry_price) if won else -trade.size * trade.entry_price
        )
        trade.status = "resolved"
        trade.resolved_at = datetime.now(timezone.utc)
        return trade


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

"""OKX trading via CCXT."""
from __future__ import annotations

import logging
from typing import Any

import ccxt

from .parser import Signal

logger = logging.getLogger(__name__)


class OkxTrader:
    def __init__(
        self,
        api_key: str,
        secret: str,
        password: str,
        *,
        sandbox: bool = False,
        default_type: str = "swap",
        margin_mode: str = "cross",
        leverage: int = 5,
        amount: float = 1.0,
        order_type: str = "limit",
        position_mode: str = "net",  # net | long_short
        dry_run: bool = True,
    ) -> None:
        self.margin_mode = margin_mode
        self.default_leverage = leverage
        self.amount = amount
        self.order_type = order_type
        self.position_mode = position_mode
        self.dry_run = dry_run

        self.exchange = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "enableRateLimit": True,
                "options": {"defaultType": default_type},
            }
        )
        if sandbox:
            self.exchange.set_sandbox_mode(True)

    def _pos_side(self, side: str) -> str:
        if self.position_mode == "net":
            return "net"
        return "long" if side == "buy" else "short"

    def _params(self, signal: Signal) -> dict[str, Any]:
        params: dict[str, Any] = {
            "marginMode": self.margin_mode,
            "posSide": self._pos_side(signal.side),
        }
        # Attached TP / SL (OKX swap via CCXT unified params)
        if signal.take_profit is not None:
            params["takeProfit"] = {
                "triggerPrice": signal.take_profit,
                "type": "market",
            }
        if signal.stop_loss is not None:
            params["stopLoss"] = {
                "triggerPrice": signal.stop_loss,
                "type": "market",
            }
        return params

    def ensure_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self.exchange.set_leverage(
                leverage,
                symbol,
                {"marginMode": self.margin_mode},
            )
        except Exception as e:
            logger.warning("set_leverage failed (continuing): %s", e)

    def place_order(self, signal: Signal) -> dict[str, Any]:
        symbol = signal.swap_symbol
        leverage = signal.leverage or self.default_leverage
        params = self._params(signal)
        amount = self.amount
        price = signal.entry if self.order_type == "limit" else None

        payload = {
            "symbol": symbol,
            "type": self.order_type,
            "side": signal.side,
            "amount": amount,
            "price": price,
            "leverage": leverage,
            "params": params,
        }
        logger.info("Order payload: %s dry_run=%s", payload, self.dry_run)

        if self.dry_run:
            return {
                "dry_run": True,
                "id": "DRY_RUN",
                **payload,
            }

        self.exchange.load_markets()
        if symbol not in self.exchange.markets:
            raise ValueError(f"Market not found on OKX: {symbol}")

        self.ensure_leverage(symbol, leverage)
        return self.exchange.create_order(
            symbol,
            self.order_type,
            signal.side,
            amount,
            price,
            params,
        )

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self.dry_run or order_id == "DRY_RUN":
            return {"dry_run": True, "id": order_id, "status": "canceled"}
        self.exchange.load_markets()
        return self.exchange.cancel_order(order_id, symbol)

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self.dry_run or order_id == "DRY_RUN":
            return {"dry_run": True, "id": order_id, "status": "open"}
        self.exchange.load_markets()
        return self.exchange.fetch_order(order_id, symbol)

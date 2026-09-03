"""Exchange trading via CCXT (OKX, Bybit)."""
from __future__ import annotations

import logging
from typing import Any, Protocol

import ccxt

from .parser import Signal

logger = logging.getLogger(__name__)


class Trader(Protocol):
    exchange_name: str
    sandbox: bool
    amount: float
    equity_pct: float
    dry_run: bool

    def place_order(self, signal: Signal) -> dict[str, Any]: ...

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...


def _resolve_amount(
    exchange: ccxt.Exchange,
    *,
    dry_run: bool,
    equity_pct: float,
    amount: float,
    equity_dry_usdt: float,
    signal: Signal,
    leverage: int,
    symbol: str,
    exchange_label: str,
) -> float:
    """Fixed amount, or % of USDT equity when equity_pct > 0."""
    if equity_pct <= 0:
        return amount

    if dry_run:
        equity = equity_dry_usdt
    else:
        bal = exchange.fetch_balance()
        usdt = bal.get("USDT") or {}
        equity = None
        for key in ("total", "free"):
            val = usdt.get(key)
            if val is not None and float(val) > 0:
                equity = float(val)
                break
        if equity is None:
            total = bal.get("total") or {}
            if "USDT" in total:
                equity = float(total["USDT"])
        if equity is None:
            raise ValueError(f"No USDT balance found on {exchange_label}")

    margin = equity * (equity_pct / 100.0)
    notional = margin * leverage
    raw = notional / signal.entry
    logger.info(
        "Size from equity: %.2f USDT × %.1f%% × %sx / %s = %s",
        equity,
        equity_pct,
        leverage,
        signal.entry,
        raw,
    )
    if dry_run:
        return round(raw, 8)
    return float(exchange.amount_to_precision(symbol, raw))


class OkxTrader:
    exchange_name = "okx"

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
        equity_pct: float = 0.0,
        equity_dry_usdt: float = 5000.0,
        order_type: str = "limit",
        position_mode: str = "net",  # net | long_short
        dry_run: bool = True,
    ) -> None:
        self.sandbox = sandbox
        self.margin_mode = margin_mode
        self.default_leverage = leverage
        self.amount = amount
        self.equity_pct = equity_pct
        self.equity_dry_usdt = equity_dry_usdt
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

    def resolve_amount(self, signal: Signal, *, leverage: int, symbol: str) -> float:
        return _resolve_amount(
            self.exchange,
            dry_run=self.dry_run,
            equity_pct=self.equity_pct,
            amount=self.amount,
            equity_dry_usdt=self.equity_dry_usdt,
            signal=signal,
            leverage=leverage,
            symbol=symbol,
            exchange_label="OKX",
        )

    def place_order(self, signal: Signal) -> dict[str, Any]:
        symbol = signal.swap_symbol
        leverage = signal.leverage or self.default_leverage
        params = self._params(signal)
        price = signal.entry if self.order_type == "limit" else None

        if not self.dry_run:
            self.exchange.load_markets()
            if symbol not in self.exchange.markets:
                raise ValueError(f"Market not found on OKX: {symbol}")

        amount = self.resolve_amount(signal, leverage=leverage, symbol=symbol)

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
            return {"dry_run": True, "id": "DRY_RUN", **payload}

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


class BybitTrader:
    exchange_name = "bybit"

    def __init__(
        self,
        api_key: str,
        secret: str,
        *,
        sandbox: bool = False,
        demo: bool = False,
        default_type: str = "swap",
        margin_mode: str = "cross",
        leverage: int = 5,
        amount: float = 1.0,
        equity_pct: float = 0.0,
        equity_dry_usdt: float = 5000.0,
        order_type: str = "limit",
        position_mode: str = "net",  # net | long_short
        dry_run: bool = True,
    ) -> None:
        self.sandbox = sandbox
        self.demo = demo
        self.margin_mode = margin_mode
        self.default_leverage = leverage
        self.amount = amount
        self.equity_pct = equity_pct
        self.equity_dry_usdt = equity_dry_usdt
        self.order_type = order_type
        self.position_mode = position_mode
        self.dry_run = dry_run

        self.exchange = ccxt.bybit(
            {
                "apiKey": api_key,
                "secret": secret,
                "enableRateLimit": True,
                "options": {"defaultType": default_type},
            }
        )
        if demo:
            self.exchange.enable_demo_trading(True)
        elif sandbox:
            self.exchange.set_sandbox_mode(True)

    def _params(self, signal: Signal) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.position_mode == "long_short":
            params["hedged"] = True
        if signal.take_profit is not None:
            params["takeProfitPrice"] = signal.take_profit
        if signal.stop_loss is not None:
            params["stopLossPrice"] = signal.stop_loss
        return params

    def ensure_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self.exchange.set_leverage(leverage, symbol)
        except Exception as e:
            logger.warning("set_leverage failed (continuing): %s", e)

    def ensure_margin_mode(self, symbol: str, leverage: int) -> None:
        try:
            self.exchange.set_margin_mode(
                self.margin_mode,
                symbol,
                {"leverage": leverage},
            )
        except Exception as e:
            logger.warning("set_margin_mode failed (continuing): %s", e)

    def resolve_amount(self, signal: Signal, *, leverage: int, symbol: str) -> float:
        return _resolve_amount(
            self.exchange,
            dry_run=self.dry_run,
            equity_pct=self.equity_pct,
            amount=self.amount,
            equity_dry_usdt=self.equity_dry_usdt,
            signal=signal,
            leverage=leverage,
            symbol=symbol,
            exchange_label="Bybit",
        )

    def place_order(self, signal: Signal) -> dict[str, Any]:
        symbol = signal.swap_symbol
        leverage = signal.leverage or self.default_leverage
        params = self._params(signal)
        price = signal.entry if self.order_type == "limit" else None

        if not self.dry_run:
            self.exchange.load_markets()
            if symbol not in self.exchange.markets:
                raise ValueError(f"Market not found on Bybit: {symbol}")

        amount = self.resolve_amount(signal, leverage=leverage, symbol=symbol)

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
            return {"dry_run": True, "id": "DRY_RUN", **payload}

        self.ensure_margin_mode(symbol, leverage)
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


def make_trader(cfg: dict) -> Trader:
    """Build OKX or Bybit trader from env/config dict."""
    exchange = (cfg.get("EXCHANGE") or "okx").lower().strip()
    dry_run = cfg.get("TRADE_DRY_RUN", "true").lower() in ("1", "true", "yes")
    common = {
        "margin_mode": cfg.get("TRADE_MARGIN_MODE", "cross"),
        "leverage": int(cfg.get("TRADE_LEVERAGE", "5")),
        "amount": float(cfg.get("TRADE_AMOUNT", "1")),
        "equity_pct": float(cfg.get("TRADE_EQUITY_PCT", "0")),
        "equity_dry_usdt": float(cfg.get("TRADE_EQUITY_DRY_USDT", "5000")),
        "order_type": cfg.get("TRADE_ORDER_TYPE", "limit"),
        "position_mode": cfg.get("TRADE_POSITION_MODE", "net"),
        "dry_run": dry_run,
    }

    if exchange == "bybit":
        demo = cfg.get("BYBIT_DEMO", "false").lower() in ("1", "true", "yes")
        sandbox = cfg.get("BYBIT_SANDBOX", "false").lower() in ("1", "true", "yes")
        if demo and sandbox:
            raise ValueError("BYBIT_DEMO and BYBIT_SANDBOX are mutually exclusive")
        return BybitTrader(
            api_key=cfg.get("BYBIT_API_KEY", ""),
            secret=cfg.get("BYBIT_SECRET", ""),
            sandbox=sandbox,
            demo=demo,
            **common,
        )

    if exchange == "okx":
        return OkxTrader(
            api_key=cfg.get("OKX_API_KEY", ""),
            secret=cfg.get("OKX_SECRET", ""),
            password=cfg.get("OKX_PASSWORD", ""),
            sandbox=cfg.get("OKX_SANDBOX", "false").lower() in ("1", "true", "yes"),
            **common,
        )

    raise ValueError(f"Unknown EXCHANGE={exchange!r} — use okx or bybit")


def required_credentials(cfg: dict, *, dry_run: bool) -> list[str]:
    """Env keys required for live trading on the selected exchange."""
    if dry_run:
        return []
    exchange = (cfg.get("EXCHANGE") or "okx").lower().strip()
    if exchange == "bybit":
        return [k for k in ("BYBIT_API_KEY", "BYBIT_SECRET") if not cfg.get(k)]
    return [k for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSWORD") if not cfg.get(k)]

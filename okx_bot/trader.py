"""Exchange trading via CCXT (OKX, Bybit, Binance)."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Protocol

import ccxt

from .parser import Signal
from .risk_limits import split_order_qty

logger = logging.getLogger(__name__)

# USDT-M only — skip spot/inverse on load_markets (demo spot host often breaks).
_BINANCE_CCXT_OPTIONS = {
    "defaultType": "future",
    "fetchMarkets": ["linear"],
}


def _collect_income_pages(
    fetch: Callable[[dict[str, Any]], Any],
    *,
    start_ms: int,
    end_ms: int | None = None,
    max_pages: int = 8,
) -> list[dict[str, Any]]:
    """Page Binance income until a short page. Dedupe by tranId so overlaps are not double-counted."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    cursor = int(start_ms)
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "incomeType": "REALIZED_PNL",
            "startTime": cursor,
            "limit": 1000,
        }
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        rows = fetch(params) or []
        if isinstance(rows, dict):
            break
        if not rows:
            break
        added = 0
        last_time = cursor
        for row in rows:
            tid = str(row.get("tranId") or "")
            key = tid or f"{row.get('time')}:{row.get('symbol')}:{row.get('income')}:{added}"
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
            added += 1
            try:
                last_time = max(last_time, int(row.get("time") or 0))
            except (TypeError, ValueError):
                pass
        if len(rows) < 1000:
            break
        nxt = last_time if last_time > cursor else cursor + 1
        if nxt <= cursor:
            nxt = cursor + 1
        cursor = nxt
        if added == 0:
            break
    return out


def _binance_load_markets(exchange: ccxt.Exchange) -> None:
    if exchange.markets:
        return
    exchange.load_markets()


def _normalize_positions(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize CCXT fetch_positions() rows to a common shape."""
    out: list[dict[str, Any]] = []
    for p in raw or []:
        try:
            contracts = float(p.get("contracts") or 0)
        except (TypeError, ValueError):
            contracts = 0.0
        if abs(contracts) < 1e-12:
            try:
                notional = float(p.get("notional") or 0)
            except (TypeError, ValueError):
                notional = 0.0
            if abs(notional) < 1e-12:
                continue
        side = (p.get("side") or "").lower() or "net"
        entry = p.get("entryPrice")
        mark = p.get("markPrice")
        upnl = p.get("unrealizedPnl")
        lev = p.get("leverage")
        symbol = p.get("symbol") or "?"
        out.append(
            {
                "symbol": symbol,
                "side": side,
                "contracts": contracts,
                "entry": float(entry) if entry is not None else None,
                "mark": float(mark) if mark is not None else None,
                "unrealized_pnl": float(upnl) if upnl is not None else None,
                "leverage": float(lev) if lev is not None else None,
                "notional": (
                    float(p["notional"]) if p.get("notional") is not None else None
                ),
                "liquidation": (
                    float(p["liquidationPrice"])
                    if p.get("liquidationPrice") is not None
                    else None
                ),
            }
        )
    return out


class Trader(Protocol):
    exchange_name: str
    sandbox: bool
    amount: float
    equity_pct: float
    dry_run: bool

    def place_order(
        self, signal: Signal, *, order_type: str | None = None
    ) -> dict[str, Any]: ...

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...


def _order_filled_qty(order: dict[str, Any]) -> float:
    filled = order.get("filled")
    if filled is not None:
        try:
            qty = float(filled)
            if qty > 0:
                return qty
        except (TypeError, ValueError):
            pass
    info = order.get("info") or {}
    for key in ("executedQty", "cumQty"):
        raw = info.get(key)
        if raw is not None and str(raw) not in ("", "0"):
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return 0.0


def _fetch_mark_price(exchange: ccxt.Exchange, symbol: str) -> float | None:
    try:
        ticker = exchange.fetch_ticker(symbol)
    except Exception:
        return None
    for key in ("mark", "last", "close"):
        val = ticker.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _binance_exit_trigger_valid(
    *, entry_side: str, mark: float, trigger: float, kind: str
) -> bool:
    """Return False if a STOP/TAKE_PROFIT would fire immediately (Binance -2021)."""
    if mark <= 0 or trigger <= 0:
        return True
    closing_buy = entry_side == "sell"
    if kind == "sl":
        if closing_buy:
            return trigger > mark
        return trigger < mark
    # take profit
    if closing_buy:
        return trigger < mark
    return trigger > mark


def _market_min_amount(exchange: ccxt.Exchange, symbol: str) -> float:
    """Minimum order size in base/contracts (limits + precision step)."""
    market = exchange.market(symbol)
    candidates: list[float] = []
    min_amt = ((market.get("limits") or {}).get("amount") or {}).get("min")
    if min_amt is not None:
        candidates.append(float(min_amt))
    prec = (market.get("precision") or {}).get("amount")
    if prec is not None:
        pf = float(prec)
        if pf > 0:
            candidates.append(pf)
    return max(candidates) if candidates else 0.0


def _market_min_cost(exchange: ccxt.Exchange, symbol: str) -> float:
    """Minimum order notional (quote), e.g. Binance USDT-M min ~5 USDT."""
    market = exchange.market(symbol)
    min_cost = ((market.get("limits") or {}).get("cost") or {}).get("min")
    if min_cost is not None:
        return float(min_cost)
    return 0.0


def _amount_step(exchange: ccxt.Exchange, symbol: str, min_lot: float) -> float:
    if min_lot > 0:
        return min_lot
    prec = (exchange.market(symbol).get("precision") or {}).get("amount")
    if prec is not None:
        pf = float(prec)
        if pf > 0:
            return pf
    return 1e-8


def _finalize_amount(
    exchange: ccxt.Exchange,
    symbol: str,
    raw: float,
    *,
    dry_run: bool,
    price: float | None = None,
) -> float:
    """Round to market precision; bump to min lot and min notional (quote)."""
    if dry_run:
        return round(raw, 8)
    exchange.load_markets()
    min_f = _market_min_amount(exchange, symbol)
    min_cost = _market_min_cost(exchange, symbol)
    if price and price > 0 and min_cost > 0:
        need_amt = min_cost / price
        if raw < need_amt:
            logger.info(
                "Amount %s below min notional %.2f USDT at price %s for %s",
                raw,
                min_cost,
                price,
                symbol,
            )
            raw = need_amt
    if min_f > 0 and raw < min_f:
        logger.info(
            "Amount %s below min %s for %s — using minimum",
            raw,
            min_f,
            symbol,
        )
        raw = max(raw, min_f)
    amount = float(exchange.amount_to_precision(symbol, raw))
    if min_f > 0 and amount < min_f:
        amount = float(exchange.amount_to_precision(symbol, min_f))
        if amount < min_f:
            amount = min_f
    if price and price > 0 and min_cost > 0:
        notional = amount * price
        if notional + 1e-12 < min_cost:
            step = _amount_step(exchange, symbol, min_f)
            raw = max(amount, min_cost / price)
            for _ in range(64):
                try:
                    amount = float(exchange.amount_to_precision(symbol, raw))
                except ccxt.InvalidOrder:
                    amount = raw
                if amount * price + 1e-12 >= min_cost:
                    logger.info(
                        "Raised size to %s for min notional %.2f USDT on %s",
                        amount,
                        min_cost,
                        symbol,
                    )
                    break
                raw += step
    return amount


def _usdt_balances(exchange: ccxt.Exchange) -> tuple[float, float]:
    """Return (total, free) USDT; missing side falls back to the other."""
    bal = exchange.fetch_balance()
    usdt = bal.get("USDT") or {}
    total = free = None
    for key, dest in (("total", "total"), ("free", "free")):
        val = usdt.get(key)
        if val is not None:
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            if f >= 0:
                if dest == "total":
                    total = f
                else:
                    free = f
    if total is None:
        tot = bal.get("total") or {}
        if "USDT" in tot:
            total = float(tot["USDT"])
    if free is None:
        fr = bal.get("free") or {}
        if "USDT" in fr:
            free = float(fr["USDT"])
    if total is None and free is None:
        raise ValueError("No USDT balance found")
    if total is None:
        total = free  # type: ignore[assignment]
    if free is None:
        free = total  # type: ignore[assignment]
    return float(total), float(free)


# Fraction of free USDT we may lock as initial margin (fees / buffer).
_MARGIN_UTILIZATION = 0.85


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
    """Fixed amount, or % of USDT equity as 1R risk when equity_pct > 0.

    Full 1R only — if required margin exceeds free×0.85, skip (do not shrink).
    Example: equity 5000 × 5% → risk 250 USDT at SL; qty = 250 / |entry−SL|.
    """
    if equity_pct <= 0:
        raw = amount
    else:
        if dry_run:
            equity = free = equity_dry_usdt
        else:
            try:
                equity, free = _usdt_balances(exchange)
            except ValueError as e:
                raise ValueError(f"{e} on {exchange_label}") from e

        # TRADE_EQUITY_PCT = target max loss at SL (1R), not margin %.
        if signal.stop_loss is None:
            logger.warning(
                "%s: equity_pct set but signal has no stop_loss — "
                "falling back to fixed amount=%s",
                exchange_label,
                amount,
            )
            raw = amount
        else:
            stop_dist = abs(float(signal.entry) - float(signal.stop_loss))
            if stop_dist <= 0:
                raise ValueError(
                    f"{exchange_label}: stop_loss equals entry — cannot size 1R"
                )
            entry = float(signal.entry)
            risk_usdt = equity * (equity_pct / 100.0)
            raw = risk_usdt / stop_dist
            # Affordable notional from free margin (not full equity × lev).
            max_margin = max(free, 0.0) * _MARGIN_UTILIZATION
            max_notional = max_margin * max(leverage, 1)
            notional = raw * entry
            if max_notional <= 0:
                raise ValueError(
                    f"{exchange_label}: no free USDT margin "
                    f"(free={free:.4f}, total={equity:.4f})"
                )
            if notional > max_notional and entry > 0:
                need_margin = notional / max(leverage, 1)
                raise ValueError(
                    f"{exchange_label}: skip — full 1R needs margin "
                    f"~{need_margin:.2f} USDT but free×{_MARGIN_UTILIZATION:.0%}="
                    f"{max_margin:.2f} (target risk {risk_usdt:.2f}, "
                    f"notional {notional:.2f}). Close positions or lower "
                    f"TRADE_EQUITY_PCT."
                )
            logger.info(
                "Size from 1R risk: equity %.2f free %.2f × %.1f%% "
                "= %.2f risk / stop_dist %s = %s (notional %.2f)",
                equity,
                free,
                equity_pct,
                risk_usdt,
                stop_dist,
                raw,
                notional,
            )
    return _finalize_amount(
        exchange, symbol, raw, dry_run=dry_run, price=signal.entry
    )


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

    def place_order(
        self, signal: Signal, *, order_type: str | None = None
    ) -> dict[str, Any]:
        symbol = signal.swap_symbol
        leverage = signal.leverage or self.default_leverage
        params = self._params(signal)
        otype = order_type or self.order_type
        price = signal.entry if otype == "limit" else None

        if not self.dry_run:
            self.exchange.load_markets()
            if symbol not in self.exchange.markets:
                raise ValueError(f"Market not found on OKX: {symbol}")

        amount = self.resolve_amount(signal, leverage=leverage, symbol=symbol)

        payload = {
            "symbol": symbol,
            "type": otype,
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
            otype,
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

    def fetch_open_positions(self) -> list[dict[str, Any]]:
        """Return non-zero OKX swap positions (normalized)."""
        self.exchange.load_markets()
        return _normalize_positions(self.exchange.fetch_positions())


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

    def place_order(
        self, signal: Signal, *, order_type: str | None = None
    ) -> dict[str, Any]:
        symbol = signal.swap_symbol
        leverage = signal.leverage or self.default_leverage
        params = self._params(signal)
        otype = order_type or self.order_type
        price = signal.entry if otype == "limit" else None

        if not self.dry_run:
            self.exchange.load_markets()
            if symbol not in self.exchange.markets:
                raise ValueError(f"Market not found on Bybit: {symbol}")

        amount = self.resolve_amount(signal, leverage=leverage, symbol=symbol)

        payload = {
            "symbol": symbol,
            "type": otype,
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
            otype,
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


class BinanceTrader:
    exchange_name = "binance"

    def __init__(
        self,
        api_key: str,
        secret: str,
        *,
        sandbox: bool = False,
        demo: bool = False,
        default_type: str = "future",
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

        opts = dict(_BINANCE_CCXT_OPTIONS)
        if default_type != "future":
            opts["defaultType"] = default_type
        self.exchange = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": secret,
                "enableRateLimit": True,
                "options": opts,
            }
        )
        if demo and sandbox:
            raise ValueError("BINANCE_DEMO and BINANCE_SANDBOX are mutually exclusive")
        if demo:
            self.exchange.enable_demo_trading(True)
        elif sandbox:
            self.exchange.set_sandbox_mode(True)

    def _params(self, signal: Signal) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.position_mode == "long_short":
            params["positionSide"] = "LONG" if signal.side == "buy" else "SHORT"
        # Do not pass takeProfitPrice/stopLossPrice on entry: CCXT turns LIMIT+SL into
        # a STOP order and Binance returns -2021 when the stop would trigger immediately.
        return params

    def _position_side_params(self, entry_side: str) -> dict[str, Any]:
        if self.position_mode != "long_short":
            return {}
        return {
            "positionSide": "LONG" if entry_side == "buy" else "SHORT",
        }

    def _clamp_amount(self, symbol: str, amount: float) -> float:
        """Clamp to market min/max lot so conditional/market exits don't -4005."""
        try:
            market = self.exchange.market(symbol)
        except Exception:
            market = (self.exchange.markets or {}).get(symbol) or {}
        limits = (market.get("limits") or {}).get("amount") or {}
        min_a = limits.get("min")
        max_a = limits.get("max")
        amt = float(amount)
        if min_a is not None and amt < float(min_a):
            amt = float(min_a)
        if max_a is not None and float(max_a) > 0 and amt > float(max_a):
            logger.info(
                "Clamp amount %s → max %s for %s",
                amt,
                max_a,
                symbol,
            )
            amt = float(max_a)
        try:
            return float(self.exchange.amount_to_precision(symbol, amt))
        except Exception:
            return amt

    def _has_open_protective_orders(self, symbol: str) -> bool:
        """True if STOP / TAKE_PROFIT (or algo) already working on symbol."""
        try:
            open_orders = self.exchange.fetch_open_orders(symbol) or []
        except Exception:
            open_orders = []
        for o in open_orders:
            typ = str(o.get("type") or "").upper()
            if any(x in typ for x in ("STOP", "TAKE_PROFIT", "TRAILING")):
                return True
            info = o.get("info") or {}
            info_typ = str(info.get("type") or info.get("orderType") or "").upper()
            if any(x in info_typ for x in ("STOP", "TAKE_PROFIT", "TRAILING")):
                return True
        # Binance USDT-M conditional / algo open list (best-effort).
        try:
            if hasattr(self.exchange, "fapiPrivateGetOpenAlgoOrders"):
                rows = self.exchange.fapiPrivateGetOpenAlgoOrders({"symbol": self.exchange.market(symbol)["id"]})
                if rows:
                    return True
        except Exception:
            pass
        try:
            if hasattr(self.exchange, "fapiPrivateGetOpenOrderStrategy"):
                rows = self.exchange.fapiPrivateGetOpenOrderStrategy(
                    {"symbol": self.exchange.market(symbol)["id"]}
                )
                if rows:
                    return True
        except Exception:
            pass
        return False

    def _max_order_qty(self, symbol: str) -> float | None:
        try:
            market = self.exchange.market(symbol)
        except Exception:
            market = (self.exchange.markets or {}).get(symbol) or {}
        max_a = ((market.get("limits") or {}).get("amount") or {}).get("max")
        if max_a is None:
            return None
        try:
            max_f = float(max_a)
        except (TypeError, ValueError):
            return None
        return max_f if max_f > 0 else None

    def _place_tp_cover(
        self,
        symbol: str,
        signal: Signal,
        amount: float,
        *,
        close_side: str,
        reduce_params: dict[str, Any],
        placed: list[str],
        skipped: list[str],
    ) -> None:
        """Place take-profit for the whole qty. Naked remainder is market-closed."""
        if signal.take_profit is None or amount <= 0:
            return
        stop = float(self.exchange.price_to_precision(symbol, signal.take_profit))
        chunks = split_order_qty(amount, self._max_order_qty(symbol))
        covered = 0.0
        n_orders = 0
        for raw in chunks:
            try:
                qty = float(self.exchange.amount_to_precision(symbol, raw))
            except Exception:
                qty = raw
            if qty <= 0:
                continue
            try:
                self.exchange.create_order(
                    symbol,
                    "TAKE_PROFIT_MARKET",
                    close_side,
                    qty,
                    None,
                    {**reduce_params, "stopPrice": stop},
                )
            except Exception as e:
                logger.warning("Binance tp chunk failed: %s", e)
                break
            covered += qty
            n_orders += 1
        if n_orders:
            placed.append(f"TP({n_orders})" if n_orders > 1 else "TP")
        leftover = amount - covered
        try:
            dust = _market_min_amount(self.exchange, symbol)
        except Exception:
            dust = 0.0
        if leftover <= max(dust, 0.0) * 1.01:
            if not n_orders:
                skipped.append("TP")
            return
        try:
            qty = float(self.exchange.amount_to_precision(symbol, leftover))
        except Exception:
            qty = leftover
        if qty <= 0:
            return
        try:
            self.exchange.create_order(
                symbol, "market", close_side, qty, None, {**reduce_params}
            )
            placed.append("NAKED→MARKET")
        except Exception as e:
            logger.warning("Naked remainder close failed: %s", e)
            skipped.append(f"TP({e})")

    def _place_binance_protective_orders(
        self,
        symbol: str,
        signal: Signal,
        amount: float,
        *,
        close_if_sl_breached: bool = True,
    ) -> str:
        """Place reduce-only TP/SL after entry fill. Returns a short status note."""
        if signal.take_profit is None and signal.stop_loss is None:
            return ""
        if amount <= 0:
            return "TP/SL skipped (amount=0)"
        _binance_load_markets(self.exchange)
        close_side = "buy" if signal.side == "sell" else "sell"
        amount = self._clamp_amount(symbol, amount)
        pos_params = self._position_side_params(signal.side)
        # closePosition closes whole position (no qty) — Binance allows only ONE such order.
        # Prefer it for SL; use qty reduceOnly for TP.
        close_all_params: dict[str, Any] = {
            "workingType": "MARK_PRICE",
            "closePosition": True,
            **pos_params,
        }
        reduce_params: dict[str, Any] = {
            "reduceOnly": True,
            "workingType": "MARK_PRICE",
            **pos_params,
        }
        mark = _fetch_mark_price(self.exchange, symbol)
        placed: list[str] = []
        skipped: list[str] = []

        # If mark already through SL, market-close immediately (STOP would -2021).
        # Do this BEFORE the "already open" skip so breached positions still close.
        if (
            close_if_sl_breached
            and signal.stop_loss is not None
            and mark is not None
            and not _binance_exit_trigger_valid(
                entry_side=signal.side,
                mark=mark,
                trigger=signal.stop_loss,
                kind="sl",
            )
        ):
            try:
                self.exchange.create_order(
                    symbol,
                    "market",
                    close_side,
                    amount,
                    None,
                    {**reduce_params},
                )
                return (
                    f"SL breached (mark {mark} vs SL {signal.stop_loss}) "
                    "— closed market"
                )
            except Exception as e:
                logger.warning("Emergency SL close failed: %s", e)
                return f"SL breached but close failed: {e}"

        if self._has_open_protective_orders(symbol):
            return "TP/SL already open — skip"

        def _place(
            kind: str,
            otype: str,
            trigger: float,
            *,
            close_all: bool,
        ) -> None:
            if mark is not None and not _binance_exit_trigger_valid(
                entry_side=signal.side,
                mark=mark,
                trigger=trigger,
                kind=kind,
            ):
                skipped.append(kind.upper())
                return
            stop = float(self.exchange.price_to_precision(symbol, trigger))
            params = {**(close_all_params if close_all else reduce_params), "stopPrice": stop}
            qty = amount
            try:
                self.exchange.create_order(
                    symbol, otype, close_side, qty, None, params
                )
                placed.append(kind.upper() + ("(all)" if close_all else ""))
                return
            except Exception as e:
                err = str(e)
                # Qty too large → clamp again or switch SL to closePosition.
                if "-4005" in err or "max quantity" in err.lower():
                    if not close_all and kind == "sl":
                        try:
                            self.exchange.create_order(
                                symbol,
                                otype,
                                close_side,
                                amount,
                                None,
                                {**close_all_params, "stopPrice": stop},
                            )
                            placed.append("SL(all)")
                            return
                        except Exception as e2:
                            err = str(e2)
                            e = e2
                    elif not close_all:
                        try:
                            half = self._clamp_amount(symbol, amount * 0.5)
                            if half > 0 and half < amount:
                                self.exchange.create_order(
                                    symbol,
                                    otype,
                                    close_side,
                                    half,
                                    None,
                                    {**reduce_params, "stopPrice": stop},
                                )
                                placed.append(f"{kind.upper()}(half)")
                                return
                        except Exception as e2:
                            err = str(e2)
                            e = e2
                if (
                    kind == "sl"
                    and close_if_sl_breached
                    and ("-2021" in err or "immediately trigger" in err.lower())
                ):
                    try:
                        self.exchange.create_order(
                            symbol,
                            "market",
                            close_side,
                            amount,
                            None,
                            {**reduce_params},
                        )
                        placed.append("SL→MARKET")
                        return
                    except Exception as e2:
                        skipped.append(f"SL({e2})")
                        return
                logger.warning("Binance %s order failed: %s", kind, e)
                skipped.append(f"{kind.upper()}({e})")

        # Price already through TP: a resting TP would be rejected. Take the win.
        if (
            signal.take_profit is not None
            and mark is not None
            and not _binance_exit_trigger_valid(
                entry_side=signal.side,
                mark=mark,
                trigger=signal.take_profit,
                kind="tp",
            )
        ):
            try:
                self.exchange.create_order(
                    symbol, "market", close_side, amount, None, {**reduce_params}
                )
                return (
                    f"TP already through (mark {mark} vs TP {signal.take_profit}) "
                    "— closed market"
                )
            except Exception as e:
                logger.warning("Take-profit market close failed: %s", e)
                return f"TP already through but close failed: {e}"

        # SL first with close-all; TP covers the full qty (split if above max lot).
        if signal.stop_loss is not None:
            _place("sl", "STOP_MARKET", signal.stop_loss, close_all=True)
        if signal.take_profit is not None:
            self._place_tp_cover(
                symbol,
                signal,
                amount,
                close_side=close_side,
                reduce_params=reduce_params,
                placed=placed,
                skipped=skipped,
            )

        if placed and not skipped:
            return f"TP/SL: {', '.join(placed)} placed"
        if placed:
            return f"TP/SL: {', '.join(placed)}; skipped {', '.join(skipped)}"
        if skipped:
            return f"TP/SL not placed ({', '.join(skipped)})"
        return ""

    def attach_protective_orders(
        self, signal: Signal, *, symbol: str | None = None, amount: float
    ) -> str:
        """Public wrapper to attach TP/SL (or emergency close) for an open position."""
        if self.dry_run:
            return "dry-run: skip protective"
        sym = symbol or signal.swap_symbol
        _binance_load_markets(self.exchange)
        return self._place_binance_protective_orders(sym, signal, amount)

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

    def ensure_position_mode(self) -> None:
        if self.position_mode != "long_short":
            return
        try:
            self.exchange.set_position_mode(True)
        except Exception as e:
            logger.warning("set_position_mode failed (continuing): %s", e)

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
            exchange_label="Binance",
        )

    def place_order(
        self, signal: Signal, *, order_type: str | None = None
    ) -> dict[str, Any]:
        symbol = signal.swap_symbol
        leverage = signal.leverage or self.default_leverage
        params = self._params(signal)
        otype = order_type or self.order_type
        price = signal.entry if otype == "limit" else None

        if not self.dry_run:
            _binance_load_markets(self.exchange)
            if symbol not in self.exchange.markets:
                raise ValueError(f"Market not found on Binance: {symbol}")

        amount = self.resolve_amount(signal, leverage=leverage, symbol=symbol)

        payload = {
            "symbol": symbol,
            "type": otype,
            "side": signal.side,
            "amount": amount,
            "price": price,
            "leverage": leverage,
            "params": params,
        }
        logger.info("Order payload: %s dry_run=%s", payload, self.dry_run)

        if self.dry_run:
            note = ""
            if signal.take_profit is not None or signal.stop_loss is not None:
                note = "TP/SL: on Binance, placed after entry fill (not on entry ticket)"
            return {
                "dry_run": True,
                "id": "DRY_RUN",
                "protective_note": note,
                **payload,
            }

        self.ensure_position_mode()
        self.ensure_margin_mode(symbol, leverage)
        self.ensure_leverage(symbol, leverage)
        order = self.exchange.create_order(
            symbol,
            otype,
            signal.side,
            amount,
            price,
            params,
        )
        filled = _order_filled_qty(order)
        if filled > 0:
            order["protective_note"] = self._place_binance_protective_orders(
                symbol, signal, filled
            )
        elif signal.take_profit is not None or signal.stop_loss is not None:
            order["protective_note"] = (
                "TP/SL pending — entry limit still open (Binance sets exits after fill)"
            )
        return order

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self.dry_run or order_id == "DRY_RUN":
            return {"dry_run": True, "id": order_id, "status": "canceled"}
        _binance_load_markets(self.exchange)
        return self.exchange.cancel_order(order_id, symbol)

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self.dry_run or order_id == "DRY_RUN":
            return {"dry_run": True, "id": order_id, "status": "open"}
        _binance_load_markets(self.exchange)
        return self.exchange.fetch_order(order_id, symbol)

    def fetch_open_positions(self) -> list[dict[str, Any]]:
        """Return non-zero Binance USDT-M positions (normalized)."""
        if self.dry_run:
            return []
        _binance_load_markets(self.exchange)
        return _normalize_positions(self.exchange.fetch_positions())

    def fetch_realized_pnl(
        self,
        *,
        days: int = 7,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """Sum REALIZED_PNL income. `since`/`until` override the rolling `days` window."""
        if self.dry_run:
            return {"days": days, "total": 0.0, "by_symbol": {}, "count": 0}
        _binance_load_markets(self.exchange)
        if since is not None:
            start_ms = int(since.timestamp() * 1000)
        else:
            start_ms = int((time.time() - max(1, days) * 86400) * 1000)
        end_ms = int(until.timestamp() * 1000) if until is not None else None
        rows = _collect_income_pages(
            self.exchange.fapiPrivateGetIncome,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        total = 0.0
        by_symbol: dict[str, float] = {}
        for r in rows:
            try:
                income = float(r.get("income") or 0)
            except (TypeError, ValueError):
                continue
            total += income
            sym = str(r.get("symbol") or "?")
            by_symbol[sym] = by_symbol.get(sym, 0.0) + income
        return {
            "days": days,
            "total": total,
            "by_symbol": by_symbol,
            "count": len(rows),
        }


def make_trader(cfg: dict) -> Trader:
    """Build OKX, Bybit, or Binance trader from env/config dict."""
    exchange = (cfg.get("EXCHANGE") or "okx").lower().strip()
    return make_trader_for_exchange(cfg, exchange)


def make_trader_for_exchange(cfg: dict, exchange: str) -> Trader:
    """Build one trader for the given exchange using env keys for that venue."""
    exchange = exchange.lower().strip()
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

    if exchange == "binance":
        demo = cfg.get("BINANCE_DEMO", "false").lower() in ("1", "true", "yes")
        sandbox = cfg.get("BINANCE_SANDBOX", "false").lower() in ("1", "true", "yes")
        if demo and sandbox:
            raise ValueError("BINANCE_DEMO and BINANCE_SANDBOX are mutually exclusive")
        return BinanceTrader(
            api_key=cfg.get("BINANCE_API_KEY", ""),
            secret=cfg.get("BINANCE_SECRET", ""),
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

    raise ValueError(f"Unknown EXCHANGE={exchange!r} — use okx, bybit, or binance")


def required_credentials(cfg: dict, *, dry_run: bool) -> list[str]:
    """Env keys required for live trading on the selected exchange."""
    if dry_run:
        return []
    exchange = (cfg.get("EXCHANGE") or "okx").lower().strip()
    if exchange == "bybit":
        return [k for k in ("BYBIT_API_KEY", "BYBIT_SECRET") if not cfg.get(k)]
    if exchange == "binance":
        return [k for k in ("BINANCE_API_KEY", "BINANCE_SECRET") if not cfg.get(k)]
    return [k for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSWORD") if not cfg.get(k)]

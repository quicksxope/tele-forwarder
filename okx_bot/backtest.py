"""Backtest DEX VIP signals against OKX OHLCV (CCXT)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import ccxt

from .metrics import PeriodMetrics, compute_metrics
from .parser import Signal, parse_signal
from .trade_store import TradeStore

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    amount: float = 1.0
    risk_usdt: float = 10.0          # 1R = this USDT risk (for PnL from R)
    starting_equity: float = 1000.0
    timeframe: str = "1m"
    sandbox: bool = True


def r_multiple(side: str, entry: float, exit_price: float, stop_loss: float | None) -> float | None:
    if stop_loss is None:
        return None
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    if side == "buy":
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def pnl_from_r(r: float | None, risk_usdt: float) -> float:
    if r is None:
        return 0.0
    return r * risk_usdt


def simulate_signal(
    signal: Signal,
    candles: list[list],  # [ts_ms, o, h, l, c, vol]
    *,
    cfg: BacktestConfig,
) -> dict:
    """Simulate limit entry inside window, then TP/SL or window exit.

    candles: ascending OKX OHLCV rows from ccxt.fetch_ohlcv
    """
    if not candles:
        return {
            "status": "expired",
            "exit_reason": "no_candles",
            "exit_price": None,
            "filled": False,
            "r_multiple": None,
            "pnl": 0.0,
        }

    side = signal.side
    entry = signal.entry
    tp = signal.take_profit
    sl = signal.stop_loss

    filled = False
    fill_price = entry
    exit_price = None
    status = "expired"
    exit_reason = "window_end_no_fill"

    for row in candles:
        _ts, _o, high, low, close, _v = row[0], row[1], row[2], row[3], row[4], row[5]

        if not filled:
            if side == "buy" and low <= entry <= high:
                filled = True
                fill_price = entry
            elif side == "sell" and low <= entry <= high:
                filled = True
                fill_price = entry
            else:
                continue

        # After fill (same candle allowed): conservative SL before TP
        if side == "buy":
            hit_sl = sl is not None and low <= sl
            hit_tp = tp is not None and high >= tp
            if hit_sl and hit_tp:
                exit_price, status, exit_reason = sl, "sl", "sl_before_tp_same_candle"
                break
            if hit_sl:
                exit_price, status, exit_reason = sl, "sl", "stop_loss"
                break
            if hit_tp:
                exit_price, status, exit_reason = tp, "tp", "take_profit"
                break
        else:
            hit_sl = sl is not None and high >= sl
            hit_tp = tp is not None and low <= tp
            if hit_sl and hit_tp:
                exit_price, status, exit_reason = sl, "sl", "sl_before_tp_same_candle"
                break
            if hit_sl:
                exit_price, status, exit_reason = sl, "sl", "stop_loss"
                break
            if hit_tp:
                exit_price, status, exit_reason = tp, "tp", "take_profit"
                break

    if filled and exit_price is None:
        exit_price = float(candles[-1][4])
        status = "window_exit"
        exit_reason = "window_end_mark"

    if not filled:
        return {
            "status": "expired",
            "exit_reason": exit_reason,
            "exit_price": None,
            "filled": False,
            "r_multiple": None,
            "pnl": 0.0,
        }

    r = r_multiple(side, fill_price, float(exit_price), sl)
    return {
        "status": status,
        "exit_reason": exit_reason,
        "exit_price": float(exit_price),
        "filled": True,
        "fill_price": fill_price,
        "r_multiple": r,
        "pnl": pnl_from_r(r, cfg.risk_usdt),
    }


class Backtester:
    def __init__(
        self,
        store: TradeStore,
        *,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        sandbox: bool = True,
        cfg: BacktestConfig | None = None,
    ) -> None:
        self.store = store
        self.cfg = cfg or BacktestConfig(sandbox=sandbox)
        self.exchange = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
        )
        if sandbox:
            self.exchange.set_sandbox_mode(True)

    def _fetch_candles(self, symbol: str, start: datetime, end: datetime) -> list[list]:
        self.exchange.load_markets()
        if symbol not in self.exchange.markets:
            logger.warning("Market missing: %s", symbol)
            return []
        since = int(start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        out: list[list] = []
        while since < end_ms:
            batch = self.exchange.fetch_ohlcv(
                symbol, timeframe=self.cfg.timeframe, since=since, limit=300
            )
            if not batch:
                break
            for row in batch:
                if row[0] > end_ms:
                    break
                if row[0] >= since:
                    out.append(row)
            next_since = batch[-1][0] + 60_000
            if next_since <= since:
                break
            since = next_since
            if len(batch) < 300:
                break
        return out

    def run_signal(self, signal: Signal, *, signal_time: datetime | None = None) -> int:
        start = signal.window_start or signal_time or datetime.now(timezone.utc)
        end = signal.window_end or (start + timedelta(hours=4))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        candles = self._fetch_candles(signal.swap_symbol, start, end)
        result = simulate_signal(signal, candles, cfg=self.cfg)

        opened = start.isoformat()
        closed = end.isoformat() if result["filled"] or result["status"] == "expired" else None
        trade_id = self.store.add_trade(
            source="backtest",
            pair=signal.pair,
            symbol=signal.swap_symbol,
            side=signal.side,
            entry=signal.entry,
            leverage=signal.leverage,
            take_profit=signal.take_profit,
            stop_loss=signal.stop_loss,
            amount=self.cfg.amount,
            status="open" if False else result["status"],
            exit_price=result.get("exit_price"),
            exit_reason=result.get("exit_reason"),
            pnl=result.get("pnl"),
            r_multiple=result.get("r_multiple"),
            opened_at=opened,
            closed_at=closed or end.isoformat(),
            window_start=signal.window_start.isoformat() if signal.window_start else None,
            window_end=signal.window_end.isoformat() if signal.window_end else None,
            timeframe_raw=signal.timeframe_raw,
        )
        return trade_id

    def run_messages(
        self,
        messages: Iterable[tuple[datetime, str]],
    ) -> PeriodMetrics:
        """messages: iterable of (message_utc_time, text)."""
        self.store.snapshot_equity(
            self.cfg.starting_equity, source="backtest", note="backtest_start"
        )
        ids: list[int] = []
        times: list[datetime] = []
        for ts, text in messages:
            signal = parse_signal(text)
            if not signal:
                continue
            # Re-anchor window to message date if parser used "today"
            signal = _reanchor_window(signal, ts)
            tid = self.run_signal(signal, signal_time=ts)
            ids.append(tid)
            times.append(ts)

        if not times:
            now = datetime.now(timezone.utc)
            return compute_metrics([], start=now, end=now, equity_start=self.cfg.starting_equity)

        start = min(times)
        end = max(times) + timedelta(days=1)
        trades = self.store.trades_between(start, end, source="backtest", closed_only=True)
        total_pnl = sum((t.pnl or 0.0) for t in trades)
        equity_end = self.cfg.starting_equity + total_pnl
        self.store.snapshot_equity(equity_end, source="backtest", note="backtest_end")
        return compute_metrics(
            trades,
            start=start,
            end=end,
            equity_start=self.cfg.starting_equity,
            equity_end=equity_end,
        )


def _reanchor_window(signal: Signal, message_time: datetime) -> Signal:
    """Shift HH:MM window onto the message's local WIB date."""
    if not signal.window_start or not signal.window_end:
        return signal
    from .parser import WIB

    local = message_time.astimezone(WIB)
    day = local.date()

    def combine(src: datetime) -> datetime:
        s = src.astimezone(WIB)
        return datetime(day.year, day.month, day.day, s.hour, s.minute, tzinfo=WIB)

    start = combine(signal.window_start)
    end = combine(signal.window_end)
    if end <= start:
        end = end + timedelta(days=1)
    return Signal(
        pair=signal.pair,
        side=signal.side,
        entry=signal.entry,
        raw_pair=signal.raw_pair,
        leverage=signal.leverage,
        take_profit=signal.take_profit,
        stop_loss=signal.stop_loss,
        window_start=start,
        window_end=end,
        timeframe_raw=signal.timeframe_raw,
        exec_pair=signal.exec_pair,
        order_id=signal.order_id,
        valid_until=signal.valid_until,
        has_signal=signal.has_signal,
        has_order_confirm=signal.has_order_confirm,
    )


def default_store(data_dir: Path) -> TradeStore:
    return TradeStore(data_dir / "okx_trades.db")

"""Unit tests for pnl helpers + store close parity + 1R sizing."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import ccxt

from okx_bot.parser import Signal
from okx_bot.pnl import (
    close_metrics,
    infer_exit_status,
    is_position_flat,
    pnl_usdt,
    r_multiple,
)
from okx_bot.trade_store import TradeStore
from okx_bot.trader import _resolve_amount


def test_r_multiple_long_short() -> None:
    assert r_multiple("buy", 100.0, 90.0, 95.0) == -2.0
    assert r_multiple("buy", 100.0, 110.0, 95.0) == 2.0
    assert r_multiple("sell", 100.0, 110.0, 105.0) == -2.0
    assert r_multiple("sell", 100.0, 90.0, 105.0) == 2.0


def test_pnl_usdt_and_close_metrics() -> None:
    assert pnl_usdt("buy", 100.0, 90.0, 10.0) == -100.0
    pnl, r = close_metrics(
        side="buy", entry=100.0, exit_price=90.0, amount=10.0, stop_loss=95.0
    )
    assert pnl == -100.0
    assert r == -2.0


def test_infer_exit_status() -> None:
    assert (
        infer_exit_status(
            side="buy", entry=100.0, exit_price=95.0, stop_loss=95.0, take_profit=110.0
        )
        == "sl"
    )
    assert (
        infer_exit_status(
            side="buy", entry=100.0, exit_price=110.0, stop_loss=95.0, take_profit=110.0
        )
        == "tp"
    )


def test_is_position_flat() -> None:
    assert is_position_flat(None)
    assert is_position_flat({"contracts": 0})
    assert not is_position_flat({"contracts": 1.5})


def test_close_trade_by_order_id_sqlite() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = TradeStore(Path(td) / "t.db")
        tid = store.add_trade(
            pair="BTC/USDT",
            symbol="BTC/USDT:USDT",
            side="buy",
            entry=100.0,
            amount=1.0,
            order_id="oid-1",
            exchange="binance",
            stop_loss=95.0,
        )
        row = store.list_open_trades()[0]
        assert row.exchange == "binance"
        assert row.id == tid
        store.close_trade_by_order_id(
            "oid-1",
            status="canceled",
            exit_reason="window_end",
            exit_price=100.0,
            pnl=0.0,
            r_multiple=0.0,
        )
        assert store.list_open_trades() == []


def test_close_trade_idempotent_only_open() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = TradeStore(Path(td) / "t.db")
        tid = store.add_trade(
            pair="ETH/USDT",
            symbol="ETH/USDT:USDT",
            side="buy",
            entry=2000.0,
            amount=1.0,
            stop_loss=1900.0,
        )
        store.close_trade(
            tid,
            status="sl",
            exit_price=1900.0,
            exit_reason="test",
            pnl=-100.0,
            r_multiple=-1.0,
        )
        store.close_trade(
            tid,
            status="tp",
            exit_price=2100.0,
            exit_reason="overwrite",
            pnl=999.0,
            r_multiple=9.0,
        )
        rows = store.trades_between(
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
            source="live",
            closed_only=True,
        )
        assert len(rows) == 1
        assert rows[0].status == "sl"
        assert rows[0].pnl == -100.0


class _FakeEqExchange(ccxt.Exchange):
    id = "binance"

    def __init__(self, equity: float = 5000.0) -> None:
        super().__init__()
        self._equity = equity
        self.precisionMode = 4
        self.markets = {
            "AAA/USDT:USDT": {
                "symbol": "AAA/USDT:USDT",
                "precision": {"amount": 0.001},
                "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.0}},
            },
        }

    def load_markets(self, reload: bool = False) -> dict:
        return self.markets

    def fetch_balance(self) -> dict:
        return {"USDT": {"total": self._equity, "free": self._equity}}


def test_resolve_amount_1r_risk() -> None:
    ex = _FakeEqExchange(5000.0)
    sig = Signal(
        pair="AAA/USDT",
        side="buy",
        entry=100.0,
        raw_pair="AAA/USDT",
        stop_loss=90.0,  # stop_dist=10 → risk 500 → qty 50
    )
    amt = _resolve_amount(
        ex,
        dry_run=False,
        equity_pct=10.0,
        amount=1.0,
        equity_dry_usdt=5000.0,
        signal=sig,
        leverage=5,
        symbol="AAA/USDT:USDT",
        exchange_label="binance",
    )
    assert abs(amt - 50.0) < 1e-6


def test_resolve_amount_1r_dry_run() -> None:
    ex = _FakeEqExchange(1.0)
    sig = Signal(
        pair="AAA/USDT",
        side="buy",
        entry=50.0,
        raw_pair="AAA/USDT",
        stop_loss=40.0,  # dist=10; risk=500 → qty 50
    )
    amt = _resolve_amount(
        ex,
        dry_run=True,
        equity_pct=10.0,
        amount=1.0,
        equity_dry_usdt=5000.0,
        signal=sig,
        leverage=10,
        symbol="AAA/USDT:USDT",
        exchange_label="binance",
    )
    assert abs(amt - 50.0) < 1e-6

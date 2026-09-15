"""Binance entry vs protective order behavior."""
from __future__ import annotations

from okx_bot.bot import _sl_breached
from okx_bot.parser import Signal
from okx_bot.trader import BinanceTrader, _binance_exit_trigger_valid


def test_binance_entry_params_omit_ccxt_bracket_keys() -> None:
    trader = BinanceTrader(
        api_key="x",
        secret="y",
        dry_run=True,
    )
    sig = Signal(
        pair="XMR/USDT",
        side="sell",
        entry=515.65,
        raw_pair="XMR/USDT",
        take_profit=509.24,
        stop_loss=522.06,
    )
    params = trader._params(sig)
    assert "takeProfitPrice" not in params
    assert "stopLossPrice" not in params


def test_binance_short_sl_tp_vs_mark() -> None:
    mark = 515.0
    assert _binance_exit_trigger_valid(
        entry_side="sell", mark=mark, trigger=522.06, kind="sl"
    )
    assert _binance_exit_trigger_valid(
        entry_side="sell", mark=mark, trigger=509.24, kind="tp"
    )
    assert not _binance_exit_trigger_valid(
        entry_side="sell", mark=523.0, trigger=522.06, kind="sl"
    )


def test_sl_breached_long_short() -> None:
    assert _sl_breached(side="buy", mark=0.10, stop_loss=0.102)
    assert not _sl_breached(side="buy", mark=0.103, stop_loss=0.102)
    assert _sl_breached(side="sell", mark=522.0, stop_loss=520.0)
    assert not _sl_breached(side="sell", mark=515.0, stop_loss=520.0)

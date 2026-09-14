"""Order size rounding — must bump before amount_to_precision (CCXT truncates to 0)."""
from __future__ import annotations

import ccxt

from okx_bot.trader import _finalize_amount, _market_min_amount


class _FakeBinanceLike(ccxt.Exchange):
    id = "binance"

    def __init__(self) -> None:
        super().__init__()
        self.precisionMode = 4  # TICK_SIZE (Binance futures)
        self.markets = {
            "TAKE/USDT:USDT": {
                "symbol": "TAKE/USDT:USDT",
                "precision": {"amount": 1.0},
                "limits": {"amount": {"min": 1.0}, "cost": {"min": 5.0}},
            },
            "PENDLE/USDT:USDT": {
                "symbol": "PENDLE/USDT:USDT",
                "precision": {"amount": 0.1},
                "limits": {"amount": {"min": 0.1}, "cost": {"min": 5.0}},
            },
        }

    def load_markets(self, reload: bool = False) -> dict:
        return self.markets


def test_finalize_bumps_before_precision_truncates_to_zero() -> None:
    ex = _FakeBinanceLike()
    assert _market_min_amount(ex, "TAKE/USDT:USDT") == 1.0
    assert _finalize_amount(ex, "TAKE/USDT:USDT", 0.001, dry_run=False) == 1.0


def test_finalize_bumps_for_min_notional() -> None:
    ex = _FakeBinanceLike()
    price = 2.127
    amt = _finalize_amount(
        ex, "PENDLE/USDT:USDT", 1.0, dry_run=False, price=price
    )
    assert amt * price >= 5.0


def test_finalize_dry_run_unchanged() -> None:
    ex = _FakeBinanceLike()
    assert _finalize_amount(ex, "TAKE/USDT:USDT", 0.001, dry_run=True) == 0.001

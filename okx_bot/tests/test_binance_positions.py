"""Binance positions / realized PnL helpers."""
from __future__ import annotations

from okx_bot.settings_menu import _positions_text
from okx_bot.trader import BinanceTrader, _normalize_positions


def test_normalize_positions_skips_zero() -> None:
    raw = [
        {
            "symbol": "AVAAI/USDT:USDT",
            "side": "long",
            "contracts": 454,
            "entryPrice": 0.01089,
            "markPrice": 0.0109,
            "unrealizedPnl": 0.004,
            "leverage": 10,
            "notional": 4.95,
            "liquidationPrice": None,
        },
        {"symbol": "EMPTY/USDT:USDT", "contracts": 0, "notional": 0},
    ]
    out = _normalize_positions(raw)
    assert len(out) == 1
    assert out[0]["symbol"] == "AVAAI/USDT:USDT"
    assert out[0]["unrealized_pnl"] == 0.004


def test_binance_fetch_open_positions_dry_run() -> None:
    t = BinanceTrader(api_key="x", secret="y", dry_run=True)
    assert t.fetch_open_positions() == []
    assert t.fetch_realized_pnl(days=7)["total"] == 0.0


def test_positions_text_binance() -> None:
    class FakeBinance:
        exchange_name = "binance"
        sandbox = False
        demo = True

        def fetch_open_positions(self):
            return [
                {
                    "symbol": "AVAAI/USDT:USDT",
                    "side": "long",
                    "contracts": 454.0,
                    "entry": 0.01089,
                    "mark": 0.0109,
                    "unrealized_pnl": 0.004,
                    "leverage": 10.0,
                    "notional": 4.95,
                    "liquidation": None,
                }
            ]

    class FakeStore:
        def list_open_trades(self, **kwargs):
            return []

    text = _positions_text(FakeStore(), [("binance", FakeBinance())])
    assert "BINANCE" in text
    assert "AVAAI/USDT:USDT" in text
    assert "uPnL" in text

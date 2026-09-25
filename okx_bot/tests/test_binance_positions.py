"""Binance positions / realized PnL helpers."""
from __future__ import annotations

from okx_bot.desk_view import classify_book, format_book
from okx_bot.trader import BinanceTrader, _collect_income_pages, _normalize_positions
from datetime import datetime, timezone


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
    book = classify_book(
        [
            {
                "symbol": "AVAAI/USDT:USDT",
                "side": "long",
                "contracts": 454.0,
                "entry": 0.01089,
                "mark": 0.0109,
                "unrealized_pnl": 0.004,
                "leverage": 10.0,
                "notional": 4.95,
            }
        ],
        [],
        {},
    )
    text = format_book(book, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert "AVAAI" in text
    assert "no signal" in text
    assert "+0.00 USDT" in text


def test_income_pages_past_one_thousand() -> None:
    rows = [
        {"tranId": str(i), "time": 1_000 + i, "income": "1", "symbol": "X"}
        for i in range(1002)
    ]

    def fetch(params):
        start = int(params["startTime"])
        return [r for r in rows if int(r["time"]) >= start][:1000]

    got = _collect_income_pages(fetch, start_ms=0)
    assert len(got) == 1002
    assert sum(float(r["income"]) for r in got) == 1002

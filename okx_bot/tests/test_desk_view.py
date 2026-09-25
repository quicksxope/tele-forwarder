"""Desk screens: wallet fields, slots, and the three views."""
from __future__ import annotations

from datetime import datetime, timezone

from okx_bot.desk_view import (
    HistoryBlock,
    classify_book,
    format_book,
    format_desk,
    format_history,
    format_today,
    wallet_from_balance,
    ClosedRow,
    WalletView,
)


class _Trade:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_wallet_prefers_margin_balance() -> None:
    view = wallet_from_balance(
        {
            "info": {
                "totalMarginBalance": "1046.2",
                "availableBalance": "720",
                "totalInitialMargin": "326",
            },
            "USDT": {"total": 1, "free": 1},
        }
    )
    assert view.label == "Equity"
    assert view.equity == 1046.2
    assert view.free == 720
    assert view.locked == 326


def test_wallet_falls_back_to_usdt_total() -> None:
    view = wallet_from_balance({"USDT": {"total": 100, "free": 40}})
    assert view.label == "Wallet"
    assert view.equity == 100
    assert view.locked == 60


def test_slots_count_position_and_resting_limit_only() -> None:
    zen = _Trade(
        symbol="ZEN/USDT:USDT",
        pair="ZEN/USDT",
        side="sell",
        entry=7.368,
        stop_loss=7.572,
        take_profit=7.164,
        order_id="9",
        channel_key="dex_vip",
        window_end=None,
        leverage=10,
    )
    fart = _Trade(
        symbol="FARTCOIN/USDT:USDT",
        pair="FARTCOIN/USDT",
        side="buy",
        entry=0.1545,
        stop_loss=0.1465,
        take_profit=0.1705,
        order_id="1",
        channel_key="cryptocium",
        window_end="2026-09-26T06:30:00+00:00",
        leverage=10,
    )
    filled = _Trade(
        symbol="ARB/USDT:USDT",
        pair="ARB/USDT",
        side="buy",
        entry=0.2,
        stop_loss=0.19,
        take_profit=0.22,
        order_id="2",
        channel_key="cryptocium",
        window_end=None,
        leverage=10,
    )
    book = classify_book(
        [
            {
                "symbol": "ZEN/USDT:USDT",
                "side": "short",
                "entry": 7.4,
                "mark": 7.382,
                "unrealized_pnl": -2.04,
                "leverage": 10,
            }
        ],
        [zen, fart, filled],
        {
            "9": {"status": "closed", "filled": 1},
            "1": {"status": "open", "filled": 0},
            "2": {"status": "filled", "filled": 3},
        },
        {"FARTCOIN/USDT": 0.153},
    )
    assert book.symbols == {"ZEN/USDT", "FARTCOIN/USDT"}
    assert book.positions[0].matched
    assert book.positions[0].entry == 7.368
    text = format_book(
        book, now=datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc), invalid_r=0.3
    )
    assert "ZEN short 10x" in text
    assert "0.9R to SL" in text
    assert "FARTCOIN buy limit" in text
    assert "cancels in" in text
    assert "invalid at 0.3R" in text
    assert "orphan" not in text.lower()


def test_desk_and_today_layout() -> None:
    book = classify_book([], [], {})
    text = format_desk(
        now=datetime(2026, 9, 25, 7, 14, tzinfo=timezone.utc),
        wallet=WalletView(1046, 720, 326, "Equity"),
        today_r=0.0,
        limit_r=5,
        slots=1,
        max_slots=6,
        book=book,
        venue_line="BINANCE · live",
    )
    assert "Signal Bot · 25 Sep 14:14 WIB" in text
    assert "Equity 1,046 USDT" in text
    assert "Free 720 · Locked 326" in text
    assert "Today +0.0R / -5R" in text
    assert "Slots 1/6" in text
    assert "No open positions" in text
    today = format_today(
        now=datetime(2026, 9, 25, 7, 14, tzinfo=timezone.utc),
        realized_r=1.0,
        limit_r=5,
        wins=1,
        losses=0,
        rows=[ClosedRow("PARTI/USDT", "tp", 2.94)],
        cash=244,
    )
    assert "+1.0R / stop -5R" in today
    assert "Cash +244 USDT" in today
    assert "PARTI  TP  +2.9R" in today


def test_history_keeps_cash_off_channel_filter() -> None:
    text = format_history(
        blocks=[HistoryBlock("Today", 0.0, 0, 0, 244.0)],
        baseline_equity=5000,
        baseline_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
        live_equity=1046,
    )
    assert "cash +244" in text
    assert "5,000 → 1,046" in text
    assert "-79%" in text
    filtered = format_history(
        blocks=[HistoryBlock("Today", 1.0, 1, 0, 244.0)],
        channel_key="dex_vip",
        baseline_equity=5000,
        live_equity=1046,
    )
    assert "cash" not in filtered
    assert "dex_vip" in filtered

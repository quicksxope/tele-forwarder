"""Owner DMs stay short and keep order ids on failures only."""
from __future__ import annotations

from datetime import datetime, timezone

from okx_bot.notify_text import (
    cancel_failed,
    canceled,
    closed,
    filled,
    signal_notice,
    skip_outcome,
)


def test_signal_notice_has_no_order_id() -> None:
    text = signal_notice(
        channel="dex_vip",
        pair="PARTI/USDT",
        side="buy",
        entry=0.02367,
        stop_loss=0.02313,
        take_profit=0.02475,
        window_end=datetime(2026, 9, 25, 11, 45, tzinfo=timezone.utc),
        slots=2,
        max_slots=6,
        outcomes=[skip_outcome("PARTI/USDT", "Sudah ada posisi atau limit PARTI/USDT.")],
    )
    assert "Order ID" not in text
    assert "swap" not in text.lower()
    assert "Slots 2/6" in text
    assert "Skip · PARTI" in text
    assert "Window until 25 Sep 18:45 WIB" in text


def test_close_and_cancel_copy() -> None:
    text = closed(
        pair="PARTI/USDT",
        status="tp",
        r_multiple=2.944,
        pnl=244,
        today_r=2.944,
        limit_r=5,
    )
    assert text.startswith("Closed · PARTI · TP")
    assert "+2.94R" in text
    assert "+244 USDT" in text
    assert "Today +2.9R / -5R" in text
    assert "reason=" not in text
    assert "Window ended" in canceled("FARTCOIN/USDT", "Window ended")
    fail = cancel_failed(pair="ARB/USDT", order_id="335", error="timeout")
    assert "Order 335" in fail


def test_fill_is_one_message() -> None:
    text = filled("ZEN/USDT", "TP/SL: SL(all), TP placed")
    assert text.startswith("Filled · ZEN")
    assert "Order" not in text

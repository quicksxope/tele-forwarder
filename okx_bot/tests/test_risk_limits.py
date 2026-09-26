"""Daily stop, position cap, and prefill invalidation."""
from datetime import datetime, timezone

from okx_bot.risk_limits import (
    clamp_to_baseline,
    daily_loss_hit,
    day_bounds_wib,
    prefill_invalidated,
    split_order_qty,
)


def test_day_bounds_are_wib_midnight() -> None:
    # 2026-09-25 02:00 UTC = 09:00 WIB same calendar day
    now = datetime(2026, 9, 24, 17, 30, tzinfo=timezone.utc)  # 00:30 WIB Sep 25
    start, end = day_bounds_wib(now)
    assert start.isoformat() == "2026-09-24T17:00:00+00:00"
    assert end.isoformat() == "2026-09-25T17:00:00+00:00"


def test_clamp_today_to_reset() -> None:
    midnight, _end = day_bounds_wib(datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc))
    reset = datetime(2026, 9, 25, 7, 34, tzinfo=timezone.utc)
    assert clamp_to_baseline(midnight, reset) == reset
    assert clamp_to_baseline(reset, midnight) == reset


def test_prefill_invalid_long_and_short() -> None:
    # entry 100, SL 90, 0.3R = 3 points
    assert prefill_invalidated(side="buy", entry=100, stop_loss=90, mark=97, fraction=0.3)
    assert not prefill_invalidated(side="buy", entry=100, stop_loss=90, mark=98, fraction=0.3)
    assert prefill_invalidated(side="sell", entry=100, stop_loss=110, mark=103, fraction=0.3)
    assert not prefill_invalidated(side="sell", entry=100, stop_loss=110, mark=102, fraction=0.3)
    assert not prefill_invalidated(side="buy", entry=100, stop_loss=None, mark=90, fraction=0.3)


def test_daily_loss_hit() -> None:
    assert daily_loss_hit(-5, 5)
    assert daily_loss_hit(-5.1, 5)
    assert not daily_loss_hit(-4.9, 5)
    assert not daily_loss_hit(-9, 0)


def test_split_order_qty() -> None:
    assert split_order_qty(250, 100) == [100, 100, 50]
    assert split_order_qty(40, 100) == [40]
    assert split_order_qty(40, None) == [40]
    assert split_order_qty(0, 100) == []

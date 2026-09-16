"""ROI menu uses baseline + today (WIB), not pre-reset Binance income."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from okx_bot.settings_menu import _roi_text
from okx_bot.weekly_report import period_bounds_today_wib


@dataclass
class _FakeTrade:
    id: int = 1
    closed_at: datetime | None = None
    pnl: float | None = 10.0
    r_multiple: float | None = None
    status: str = "tp"
    channel_key: str | None = None
    exchange: str | None = "binance"


class _FakeStore:
    def __init__(self) -> None:
        self.snaps: list[tuple[float, str]] = []
        self._baseline = (
            datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc),
            5000.0,
        )

    def snapshot_equity(self, equity: float, *, source: str = "live", note: str = "") -> None:
        self.snaps.append((equity, note))

    def latest_equity_before(self, ts: datetime, *, source: str = "live") -> float | None:
        return None

    def latest_equity_baseline(self, *, source: str = "live", note_prefix: str = "roi_baseline"):
        return self._baseline

    def trades_between(self, start, end, **kwargs):
        # Only count trades after baseline.
        if end <= self._baseline[0]:
            return []
        return [
            _FakeTrade(closed_at=datetime.now(timezone.utc), pnl=0.0, status="tp"),
        ]


class _FakeEx:
    def fetch_balance(self):
        return {"USDT": {"total": 5000.0, "free": 5000.0, "used": 0.0}}


class _FakeTrader:
    exchange_name = "binance"
    sandbox = False
    demo = True
    exchange = _FakeEx()


def test_period_bounds_today_wib_starts_at_midnight() -> None:
    start, end = period_bounds_today_wib(
        now=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    )
    # 10:00 UTC = 17:00 WIB → day start 00:00 WIB = 17:00 previous UTC day? 
    # Sep 15 10:00 UTC = Sep 15 17:00 WIB → start Sep 15 00:00 WIB = Sep 14 17:00 UTC
    assert start.hour == 17 and start.day == 14
    assert end > start


def test_roi_text_uses_baseline_not_n_a() -> None:
    store = _FakeStore()
    text = _roi_text(store, traders=[("binance", _FakeTrader())])
    assert "Baseline ROI: 5000.0000 USDT" in text
    assert "Hari ini (WIB)" in text
    assert "Sejak baseline" in text
    assert "n/a" not in text.split("ROI:")[1].split("\n")[0]

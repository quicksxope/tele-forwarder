"""ROI menu uses baseline + today (WIB), not pre-reset Binance income."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from okx_bot.desk_view import HistoryBlock, format_history
from okx_bot.settings_menu import _history_text, _load_wallet
from okx_bot.weekly_report import period_bounds_today_wib


@dataclass
class _FakeTrade:
    id: int = 1
    pair: str = "AAA/USDT"
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
        self.queried = (start, end)
        # Only count trades after baseline.
        if end <= self._baseline[0]:
            return []
        if start < self._baseline[0]:
            raise AssertionError(f"query starts before reset: {start}")
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


def test_history_uses_baseline_not_old_income() -> None:
    store = _FakeStore()
    trader = _FakeTrader()

    def fetch_realized_pnl(*, days=7, since=None, until=None):
        return {"total": 10.0, "count": 1}

    trader.fetch_realized_pnl = fetch_realized_pnl
    wallet = _load_wallet([("binance", trader)])
    assert wallet.equity == 5000.0
    text = _history_text(
        store,
        [("binance", trader)],
        wallet,
        None,
        datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc),
    )
    assert "Since baseline" in text
    assert "5,000 → 5,000" in text
    assert "No baseline" not in text
    assert store.snaps == []
    plain = format_history(
        blocks=[HistoryBlock("Today", 0.0, 0, 0, None)],
        baseline_equity=5000,
        live_equity=1046,
    )
    assert "-79%" in plain

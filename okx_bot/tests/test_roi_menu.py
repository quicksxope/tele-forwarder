"""ROI menu uses live wallet equity when snapshots are missing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from okx_bot.settings_menu import _roi_text


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

    def snapshot_equity(self, equity: float, *, source: str = "live", note: str = "") -> None:
        self.snaps.append((equity, note))

    def latest_equity_before(self, ts: datetime, *, source: str = "live") -> float | None:
        return None

    def trades_between(self, start, end, **kwargs):
        now = datetime.now(timezone.utc)
        return [
            _FakeTrade(closed_at=now, pnl=25.0, status="tp"),
            _FakeTrade(id=2, closed_at=now, pnl=-5.0, status="sl"),
        ]


class _FakeEx:
    def fetch_balance(self):
        return {"USDT": {"total": 5000.0, "free": 4500.0, "used": 500.0}}


class _FakeTrader:
    exchange_name = "binance"
    sandbox = False
    demo = True
    exchange = _FakeEx()

    def fetch_realized_pnl(self, *, days: int = 7):
        return {"days": days, "total": 20.0, "by_symbol": {}, "count": 2}


def test_roi_text_uses_live_equity() -> None:
    store = _FakeStore()
    text = _roi_text(store, traders=[("binance", _FakeTrader())])
    assert "Live equity · BINANCE: 5000.0000 USDT" in text
    assert "ROI:" in text
    assert "n/a" not in text.split("ROI:")[1].split("\n")[0]
    assert store.snaps and store.snaps[0][0] == 5000.0

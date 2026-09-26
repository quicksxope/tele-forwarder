"""Position and daily-loss gates. Pure functions, no exchange I/O."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")


def day_bounds_wib(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return [start, end) of the current Asia/Jakarta calendar day, in UTC."""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(WIB)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def clamp_to_baseline(start: datetime, baseline_at: datetime | None) -> datetime:
    """Do not score trades from before the latest ROI reset."""
    if baseline_at is None:
        return start
    if baseline_at.tzinfo is None:
        baseline_at = baseline_at.replace(tzinfo=timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return max(start, baseline_at.astimezone(timezone.utc))


def prefill_invalidated(
    *,
    side: str,
    entry: float,
    stop_loss: float | None,
    mark: float | None,
    fraction: float = 0.3,
) -> bool:
    """True when mark has moved `fraction` of the stop distance toward SL before fill."""
    if stop_loss is None or mark is None or fraction <= 0:
        return False
    risk = abs(float(entry) - float(stop_loss))
    if risk <= 0:
        return False
    side_l = (side or "").lower()
    if side_l == "buy":
        return float(mark) <= float(entry) - fraction * risk
    if side_l == "sell":
        return float(mark) >= float(entry) + fraction * risk
    return False


def daily_loss_hit(realized_r: float, limit_r: float) -> bool:
    """True when today's realized R is at or past the daily stop."""
    if limit_r <= 0:
        return False
    return realized_r <= -abs(limit_r)


def split_order_qty(amount: float, max_qty: float | None) -> list[float]:
    """Split `amount` into chunks that each fit under max_qty. No max → one chunk."""
    if amount <= 0:
        return []
    if max_qty is None or max_qty <= 0 or amount <= max_qty + 1e-12:
        return [float(amount)]
    out: list[float] = []
    left = float(amount)
    cap = float(max_qty)
    while left > 1e-12 and len(out) < 50:
        qty = min(left, cap)
        out.append(qty)
        left -= qty
    return out

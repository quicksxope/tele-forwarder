"""Shared helpers for signal format parsers."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

PAIR_RE = re.compile(r"Pair\s*:\s*([A-Za-z0-9_\-/]+)", re.I)
POSITION_RE = re.compile(
    r"Position\s*:\s*(?:🟢|🔴|🟡)?\s*(Long|Short|Buy|Sell)",
    re.I,
)
SIDE_RE = re.compile(r"Side\s*:\s*(buy|sell|long|short)", re.I)
ENTRY_RE = re.compile(r"Entry(?:\s*Price)?\s*:\s*([0-9]+(?:\.[0-9]+)?)", re.I)
LEVERAGE_RE = re.compile(r"Leverage\s*:\s*([0-9]+)\s*x?", re.I)
TP_RE = re.compile(r"Take\s*Profit\s*:\s*([0-9]+(?:\.[0-9]+)?)", re.I)
SL_RE = re.compile(r"Stop\s*Loss\s*:\s*([0-9]+(?:\.[0-9]+)?)", re.I)
TF_WINDOW_RE = re.compile(
    r"Timeframe\s*:\s*(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*(WIB|UTC)?",
    re.I,
)
ORDER_ID_RE = re.compile(r"Order\s*ID\s*:\s*([0-9A-Za-z\-]+)", re.I)
VALID_UNTIL_RE = re.compile(
    r"Berlaku\s+sampai(?:\s*\(UTC\))?\s*:\s*"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:]{5,8}(?:\.[0-9]+)?(?:\+\d{2}:\d{2}|Z)?)",
    re.I,
)


def norm_side(pos: str) -> str:
    p = pos.lower()
    if p in ("long", "buy"):
        return "buy"
    if p in ("short", "sell"):
        return "sell"
    raise ValueError(f"Unsupported position/side: {pos}")


def norm_spot_pair(raw: str) -> str:
    p = raw.strip().upper().replace("-SWAP", "")
    if "/" not in p and "-" in p:
        a, b, *_ = p.split("-")
        return f"{a}/{b}"
    return p.replace("-", "/") if "/" not in p else p


def parse_wib_window(text: str) -> tuple[datetime | None, datetime | None, str | None]:
    m = TF_WINDOW_RE.search(text)
    if not m:
        return None, None, None
    start_s, end_s, tz_name = m.group(1), m.group(2), (m.group(3) or "WIB").upper()
    tz = WIB if tz_name == "WIB" else timezone.utc
    raw = m.group(0).split(":", 1)[-1].strip() if ":" in m.group(0) else m.group(0)
    today = datetime.now(tz).date()

    def combine(hhmm: str) -> datetime:
        h, mi = map(int, hhmm.split(":"))
        return datetime(today.year, today.month, today.day, h, mi, tzinfo=tz)

    start = combine(start_s)
    end = combine(end_s)
    if end <= start:
        end = end + timedelta(days=1)
    return start, end, raw


def parse_valid_until(text: str) -> datetime | None:
    m = VALID_UNTIL_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip().replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

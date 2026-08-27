"""Parse DEX VIP signal format (primary) + okx bot confirm (fallback).

Primary example:
    Pair: GTC/USDT
    Position: 🟢 Long
    Entry Price: 0.09086
    Leverage: 10x
    Take Profit: 0.09224
    Stop Loss: 0.08948
    Timeframe: 15:07-19:07 WIB
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


WIB = timezone(timedelta(hours=7))


@dataclass(frozen=True)
class Signal:
    pair: str
    side: str                 # buy | sell
    entry: float
    raw_pair: str
    leverage: int | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    window_start: datetime | None = None  # WIB parsed → aware
    window_end: datetime | None = None
    timeframe_raw: str | None = None
    # legacy confirm fields
    exec_pair: str | None = None
    order_id: str | None = None
    valid_until: datetime | None = None
    has_signal: bool = True
    has_order_confirm: bool = False

    @property
    def swap_symbol(self) -> str:
        if self.exec_pair:
            return _to_ccxt_swap(self.exec_pair)
        return _to_ccxt_swap(self.pair)

    @property
    def okx_inst_id(self) -> str:
        if self.exec_pair and "SWAP" in self.exec_pair.upper():
            return self.exec_pair.upper().replace("/", "-")
        return _to_okx_swap(self.pair)

    @property
    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.window_end is not None:
            end = self.window_end
            if end.tzinfo is None:
                end = end.replace(tzinfo=WIB)
            return now >= end.astimezone(timezone.utc)
        if self.valid_until is not None:
            vu = self.valid_until
            if vu.tzinfo is None:
                vu = vu.replace(tzinfo=timezone.utc)
            return now >= vu
        return False

    @property
    def is_before_window(self) -> bool:
        if self.window_start is None:
            return False
        now = datetime.now(timezone.utc)
        start = self.window_start
        if start.tzinfo is None:
            start = start.replace(tzinfo=WIB)
        return now < start.astimezone(timezone.utc)


def _to_ccxt_swap(pair: str) -> str:
    p = pair.strip().upper().replace("_", "-")
    if p.endswith("-SWAP"):
        core = p[: -len("-SWAP")].replace("-", "/")
        return f"{core}:USDT" if core.count("/") == 1 else f"{core}/USDT:USDT"
    p = p.replace("-", "/")
    if ":USDT" in p:
        return p
    if p.endswith("/USDT"):
        return f"{p}:USDT"
    return f"{p}:USDT"


def _to_okx_swap(pair: str) -> str:
    p = pair.strip().upper().replace("/", "-")
    if p.endswith("-SWAP"):
        return p
    if p.endswith("-USDT"):
        return f"{p}-SWAP"
    return f"{p}-SWAP"


_PAIR_RE = re.compile(r"Pair\s*:\s*([A-Za-z0-9_\-/]+)", re.I)
_POSITION_RE = re.compile(
    r"Position\s*:\s*(?:🟢|🔴|🟡)?\s*(Long|Short|Buy|Sell)",
    re.I,
)
_SIDE_RE = re.compile(r"Side\s*:\s*(buy|sell|long|short)", re.I)
_ENTRY_RE = re.compile(
    r"Entry(?:\s*Price)?\s*:\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
_LEVERAGE_RE = re.compile(r"Leverage\s*:\s*([0-9]+)\s*x?", re.I)
_TP_RE = re.compile(
    r"Take\s*Profit\s*:\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
_SL_RE = re.compile(
    r"Stop\s*Loss\s*:\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
_TF_WINDOW_RE = re.compile(
    r"Timeframe\s*:\s*(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*(WIB|UTC)?",
    re.I,
)
_ORDER_ID_RE = re.compile(r"Order\s*ID\s*:\s*([0-9A-Za-z\-]+)", re.I)
_VALID_UNTIL_RE = re.compile(
    r"Berlaku\s+sampai(?:\s*\(UTC\))?\s*:\s*"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:]{5,8}(?:\.[0-9]+)?(?:\+\d{2}:\d{2}|Z)?)",
    re.I,
)


def _norm_side_from_position(pos: str) -> str:
    p = pos.lower()
    if p in ("long", "buy"):
        return "buy"
    if p in ("short", "sell"):
        return "sell"
    raise ValueError(f"Unsupported position: {pos}")


def _norm_spot_pair(raw: str) -> str:
    p = raw.strip().upper().replace("-SWAP", "")
    if "/" not in p and "-" in p:
        a, b, *_ = p.split("-")
        return f"{a}/{b}"
    return p.replace("-", "/") if "/" not in p else p


def _parse_wib_window(text: str) -> tuple[datetime | None, datetime | None, str | None]:
    m = _TF_WINDOW_RE.search(text)
    if not m:
        return None, None, None
    start_s, end_s, tz_name = m.group(1), m.group(2), (m.group(3) or "WIB").upper()
    tz = WIB if tz_name == "WIB" else timezone.utc
    raw = m.group(0).split(":", 1)[-1].strip() if ":" in m.group(0) else m.group(0)

    # Anchor to "today" in that timezone; if end < start, window crosses midnight
    today = datetime.now(tz).date()

    def combine(hhmm: str) -> datetime:
        h, mi = map(int, hhmm.split(":"))
        return datetime(today.year, today.month, today.day, h, mi, tzinfo=tz)

    start = combine(start_s)
    end = combine(end_s)
    if end <= start:
        end = end + timedelta(days=1)
    return start, end, raw


def _parse_valid_until(text: str) -> datetime | None:
    m = _VALID_UNTIL_RE.search(text)
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


def parse_signal(text: str) -> Signal | None:
    if not text or not text.strip():
        return None

    pair_m = _PAIR_RE.search(text)
    entry_m = _ENTRY_RE.search(text)
    pos_m = _POSITION_RE.search(text)
    side_m = _SIDE_RE.search(text)

    if not pair_m or not entry_m:
        return None
    if not pos_m and not side_m:
        return None

    side = (
        _norm_side_from_position(pos_m.group(1))
        if pos_m
        else _norm_side_from_position(side_m.group(1))
    )

    lev_m = _LEVERAGE_RE.search(text)
    tp_m = _TP_RE.search(text)
    sl_m = _SL_RE.search(text)
    window_start, window_end, tf_raw = _parse_wib_window(text)

    # Legacy confirm block
    exec_pair = None
    order_id = None
    has_confirm = bool(re.search(r"Order\s+berhasil", text, re.I))
    if has_confirm:
        # second Pair: line often ETH-USDT-SWAP
        pairs = _PAIR_RE.findall(text)
        if len(pairs) >= 2:
            exec_pair = pairs[1].strip().upper()
        oid = _ORDER_ID_RE.search(text)
        if oid:
            order_id = oid.group(1).strip()

    raw_pair = pair_m.group(1).strip()
    return Signal(
        pair=_norm_spot_pair(raw_pair),
        side=side,
        entry=float(entry_m.group(1)),
        raw_pair=raw_pair,
        leverage=int(lev_m.group(1)) if lev_m else None,
        take_profit=float(tp_m.group(1)) if tp_m else None,
        stop_loss=float(sl_m.group(1)) if sl_m else None,
        window_start=window_start,
        window_end=window_end,
        timeframe_raw=tf_raw,
        exec_pair=exec_pair,
        order_id=order_id,
        valid_until=_parse_valid_until(text),
        has_signal=bool(re.search(r"Sinyal\s+diterima|Position\s*:", text, re.I)),
        has_order_confirm=has_confirm,
    )

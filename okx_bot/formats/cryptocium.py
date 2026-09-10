"""Cryptocium SETUP / SWING SETUP format. TP is always 2R from stop distance.

Chart ``Time frame`` (e.g. 30m, 4H) is informational only — not an entry window.
Orders are always limit (see bot: cryptocium forces limit regardless of env).
"""
from __future__ import annotations

import re

from ..parser import Signal
from . import common as C

SETUP_RE = re.compile(
    r"(?:SWING\s+)?SETUP\s*[-–:]\s*(LONG|SHORT|BUY|SELL)(?:\s*/\s*(?:BUY|SELL))?",
    re.I,
)
PAIR_RE = re.compile(
    r"Pair\s*:\s*\$?([A-Za-z0-9]+)(?:\s*[/\-]\s*([A-Za-z0-9]+))?",
    re.I,
)
ENTRY_RE = re.compile(
    r"Entry(?:\s+limit)?\s*:\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
SL_RE = re.compile(r"Stop\s*loss\s*:\s*([0-9]+(?:\.[0-9]+)?)", re.I)
TF_RE = re.compile(r"Time\s*frame\s*:\s*(\S+)", re.I)
KNOWN_QUOTES = ("USDT", "USDC", "BTC", "ETH")
LEVERAGE = 10
R_MULTIPLE = 2.0


def _quote_from_text(text: str, explicit: str | None) -> str:
    if explicit:
        q = explicit.upper()
        if q in KNOWN_QUOTES:
            return q
    upper = text.upper()
    for q in KNOWN_QUOTES:
        if re.search(rf"(?:/|-)\s*{q}\b", upper):
            return q
    return "USDT"


def _tp_2r(side: str, entry: float, sl: float) -> float | None:
    r = abs(entry - sl)
    if r <= 0:
        return None
    if side == "buy":
        if sl >= entry:
            return None
        return entry + R_MULTIPLE * r
    if sl <= entry:
        return None
    return entry - R_MULTIPLE * r


def parse(text: str) -> Signal | None:
    if not text or not text.strip():
        return None
    side_m = SETUP_RE.search(text)
    pair_m = PAIR_RE.search(text)
    entry_m = ENTRY_RE.search(text)
    sl_m = SL_RE.search(text)
    if not side_m or not pair_m or not entry_m or not sl_m:
        return None

    side = C.norm_side(side_m.group(1))
    entry = float(entry_m.group(1))
    sl = float(sl_m.group(1))
    tp = _tp_2r(side, entry, sl)
    if tp is None:
        return None

    base = pair_m.group(1).strip().upper()
    quote = _quote_from_text(text, pair_m.group(2))
    raw_pair = f"{base}/{quote}"
    tf_m = TF_RE.search(text)

    return Signal(
        pair=C.norm_spot_pair(raw_pair),
        side=side,
        entry=entry,
        raw_pair=raw_pair,
        leverage=LEVERAGE,
        take_profit=tp,
        stop_loss=sl,
        # Chart TF only — no window_start/window_end (unlike dex_vip WIB window).
        timeframe_raw=tf_m.group(1) if tf_m else None,
        has_signal=True,
        has_order_confirm=False,
    )

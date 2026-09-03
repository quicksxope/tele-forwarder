"""DEX VIP | Bitcoin & Crypto signal format."""
from __future__ import annotations

from ..parser import Signal
from . import common as C


def parse(text: str) -> Signal | None:
    """
    Pair: GTC/USDT
    Position: 🟢 Long
    Entry Price: 0.09086
    Leverage: 10x
    Take Profit: 0.09224
    Stop Loss: 0.08948
    Timeframe: 15:07-19:07 WIB
    """
    if not text or not text.strip():
        return None

    pair_m = C.PAIR_RE.search(text)
    entry_m = C.ENTRY_RE.search(text)
    pos_m = C.POSITION_RE.search(text)
    if not pair_m or not entry_m or not pos_m:
        return None

    lev_m = C.LEVERAGE_RE.search(text)
    tp_m = C.TP_RE.search(text)
    sl_m = C.SL_RE.search(text)
    window_start, window_end, tf_raw = C.parse_wib_window(text)
    raw_pair = pair_m.group(1).strip()

    return Signal(
        pair=C.norm_spot_pair(raw_pair),
        side=C.norm_side(pos_m.group(1)),
        entry=float(entry_m.group(1)),
        raw_pair=raw_pair,
        leverage=int(lev_m.group(1)) if lev_m else None,
        take_profit=float(tp_m.group(1)) if tp_m else None,
        stop_loss=float(sl_m.group(1)) if sl_m else None,
        window_start=window_start,
        window_end=window_end,
        timeframe_raw=tf_raw,
        has_signal=True,
        has_order_confirm=False,
    )

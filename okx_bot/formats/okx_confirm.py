"""Legacy okx_trading_bot_sinyaldex confirm/notification format."""
from __future__ import annotations

import re

from ..parser import Signal
from . import common as C


def parse(text: str) -> Signal | None:
    """
    📥 Sinyal diterima
    Pair: ETH/USDT
    Side: buy
    Entry: 2983.0

    ✅ Order berhasil dipasang!
    Pair: ETH-USDT-SWAP
    ...
    ⏱ Berlaku sampai (UTC): ...
    """
    if not text or not text.strip():
        return None

    signal_block = text
    m = re.search(
        r"Sinyal\s+diterima(.*?)(?:Order\s+berhasil|$)",
        text,
        re.I | re.DOTALL,
    )
    if m:
        signal_block = m.group(1)

    pair_m = C.PAIR_RE.search(signal_block) or C.PAIR_RE.search(text)
    side_m = C.SIDE_RE.search(signal_block) or C.SIDE_RE.search(text)
    entry_m = C.ENTRY_RE.search(signal_block) or C.ENTRY_RE.search(text)
    if not (pair_m and side_m and entry_m):
        return None

    exec_pair = None
    order_id = None
    has_confirm = bool(re.search(r"Order\s+berhasil", text, re.I))
    if has_confirm:
        pairs = C.PAIR_RE.findall(text)
        if len(pairs) >= 2:
            exec_pair = pairs[1].strip().upper()
        oid = C.ORDER_ID_RE.search(text)
        if oid:
            order_id = oid.group(1).strip()

    raw_pair = pair_m.group(1).strip()
    return Signal(
        pair=C.norm_spot_pair(raw_pair),
        side=C.norm_side(side_m.group(1)),
        entry=float(entry_m.group(1)),
        raw_pair=raw_pair,
        exec_pair=exec_pair,
        order_id=order_id,
        valid_until=C.parse_valid_until(text),
        has_signal=bool(re.search(r"Sinyal\s+diterima", text, re.I)),
        has_order_confirm=has_confirm,
    )

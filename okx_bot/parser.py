"""Signal model + parse facade.

Format-specific parsers live in `okx_bot/formats/`.
Active channel (chat_id + parser name) is chosen via `channels.yaml`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .formats.common import WIB


@dataclass(frozen=True)
class Signal:
    pair: str
    side: str  # buy | sell
    entry: float
    raw_pair: str
    leverage: int | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    timeframe_raw: str | None = None
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


def parse_signal(text: str, parser: str | None = None) -> Signal | None:
    """Parse with an explicit format name, or the active channel's parser."""
    if parser:
        from .formats import get_parser

        return get_parser(parser)(text)
    try:
        from .channels import parse_for_active_channel

        return parse_for_active_channel(text)
    except FileNotFoundError:
        from .formats import get_parser

        return get_parser("dex_vip")(text)

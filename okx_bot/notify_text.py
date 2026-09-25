"""Short owner DMs. Order ids appear only on failures."""
from __future__ import annotations

from datetime import datetime

from .desk_view import format_price, format_r, format_usdt, format_wib, short_name


def signal_notice(
    *,
    channel: str,
    pair: str,
    side: str,
    entry: float,
    stop_loss: float | None,
    take_profit: float | None,
    window_end: datetime | None,
    slots: int,
    max_slots: int,
    outcomes: list[str],
) -> str:
    lines = [
        channel,
        f"{short_name(pair)} {side} @ {format_price(entry)}",
        f"SL {format_price(stop_loss)} · TP {format_price(take_profit)}",
    ]
    if window_end is not None:
        lines.append(f"Window until {format_wib(window_end)}")
    lines.append(f"Slots {slots}/{max_slots}")
    if outcomes:
        lines.append("")
        lines.append("\n\n".join(outcomes))
    return "\n".join(lines)


def placed_outcome(exchange: str, risk_usdt: float | None) -> str:
    if risk_usdt is None:
        return f"Placed · {exchange.upper()}"
    return f"Placed · {exchange.upper()} · 1R = {format_usdt(risk_usdt)} USDT"


def skip_outcome(pair: str, reason: str) -> str:
    return f"Skip · {short_name(pair)}\n{reason}"


def failed_outcome(exchange: str, error: str) -> str:
    return f"Failed · {exchange.upper()}\n{error}"


def filled(pair: str, note: str) -> str:
    text = f"Filled · {short_name(pair)}"
    if note:
        text += f"\n{note}"
    return text


def closed(
    *,
    pair: str,
    status: str,
    r_multiple: float | None,
    pnl: float | None,
    today_r: float | None = None,
    limit_r: float | None = None,
) -> str:
    label = (status or "?").upper()
    lines = [f"Closed · {short_name(pair)} · {label}"]
    bits = []
    if r_multiple is not None:
        bits.append(format_r(r_multiple, 2))
    if pnl is not None:
        bits.append(f"{format_usdt(pnl, signed=True)} USDT")
    if bits:
        lines.append(" · ".join(bits))
    if today_r is not None and limit_r is not None:
        lines.append(f"Today {format_r(today_r)} / -{limit_r:g}R")
    return "\n".join(lines)


def canceled(pair: str, reason: str) -> str:
    return f"Canceled · {short_name(pair)}\n{reason}"


def cancel_failed(*, pair: str, order_id: str, error: str) -> str:
    return f"Cancel failed · {short_name(pair)}\nOrder {order_id}\n{error}"


def sl_still_open(
    *,
    trade_id: int,
    pair: str,
    side: str,
    mark: float | None,
    stop_loss: float | None,
    attempt: int,
    limit: int,
    note: str,
) -> str:
    lines = [
        f"SL still open · {short_name(pair)}",
        f"#{trade_id} {side}",
        f"mark {format_price(mark)} · SL {format_price(stop_loss)}",
        f"Attempt {attempt}/{limit}",
    ]
    if note:
        lines.append(note)
    return "\n".join(lines)


def orphan_position(
    *,
    symbol: str,
    side: str,
    contracts: float | None,
    entry: float | None,
    mark: float | None,
    unrealized: float | None,
) -> str:
    return (
        f"No signal · {symbol} {(side or '?').upper()}\n"
        f"size {contracts}\n"
        f"entry {format_price(entry)} · mark {format_price(mark)}\n"
        f"uPnL {format_usdt(unrealized, signed=True)} USDT"
    )

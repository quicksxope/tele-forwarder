"""Desk, Book, Today, and History text. No network and no database writes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .risk_limits import WIB

_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def norm_sym(s: str | None) -> str:
    if not s:
        return ""
    return s.replace(":USDT", "").replace("-SWAP", "").replace("-", "/").upper()


def entry_still_working(order: dict | None) -> bool:
    """True when an entry limit is still resting and unfilled."""
    if not order:
        return False
    status = (order.get("status") or "").lower()
    if status in ("closed", "canceled", "cancelled", "expired", "rejected", "filled"):
        return False
    try:
        filled = float(order.get("filled") or 0)
    except (TypeError, ValueError):
        filled = 0.0
    return filled <= 0


def short_name(symbol: str | None) -> str:
    raw = (symbol or "?").replace(":USDT", "").replace("-SWAP", "")
    base = raw.split("/")[0].split("-")[0]
    return base or raw or "?"


def format_usdt(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "?"
    v = float(value)
    mag = abs(v)
    body = f"{mag:,.0f}" if mag >= 100 else f"{mag:,.2f}"
    if v < 0:
        return f"-{body}"
    if signed and v > 0:
        return f"+{body}"
    if signed and v == 0:
        return f"+{body}"
    return body


def format_r(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "?"
    v = float(value)
    body = f"{abs(v):.{digits}f}"
    if v < 0:
        return f"-{body}R"
    return f"+{body}R"


def format_r_abs(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "?"
    return f"{abs(float(value)):.{digits}f}R"


def format_price(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{float(value):.8g}"


def format_wib(dt: datetime, *, with_time: bool = True) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(WIB)
    stamp = f"{local.day} {_MONTHS[local.month - 1]}"
    if with_time:
        stamp += f" {local:%H:%M}"
    return f"{stamp} WIB"


def is_long(side: str | None) -> bool:
    return (side or "").lower() in ("buy", "long")


def side_word(side: str | None) -> str:
    s = (side or "").lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s or "?"


def order_side(side: str | None) -> str:
    s = (side or "").lower()
    if s in ("buy", "long"):
        return "buy"
    if s in ("sell", "short"):
        return "sell"
    return s or "?"


def _risk(entry: float | None, stop_loss: float | None) -> float | None:
    if entry is None or stop_loss is None:
        return None
    risk = abs(float(entry) - float(stop_loss))
    return risk if risk > 0 else None


def r_to_sl(
    side: str | None,
    entry: float | None,
    stop_loss: float | None,
    mark: float | None,
) -> float | None:
    """Remaining fraction of the original risk from mark to the stop."""
    risk = _risk(entry, stop_loss)
    if risk is None or mark is None or stop_loss is None:
        return None
    if is_long(side):
        return (float(mark) - float(stop_loss)) / risk
    return (float(stop_loss) - float(mark)) / risk


def r_to_tp(
    side: str | None,
    entry: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    mark: float | None,
) -> float | None:
    risk = _risk(entry, stop_loss)
    if risk is None or mark is None or take_profit is None:
        return None
    if is_long(side):
        return (float(take_profit) - float(mark)) / risk
    return (float(mark) - float(take_profit)) / risk


def r_toward_sl(
    side: str | None,
    entry: float | None,
    stop_loss: float | None,
    mark: float | None,
) -> float | None:
    """How far mark has moved from entry toward the stop, in R. Negative is away from SL."""
    risk = _risk(entry, stop_loss)
    if risk is None or mark is None or entry is None:
        return None
    if is_long(side):
        return (float(entry) - float(mark)) / risk
    return (float(mark) - float(entry)) / risk


def wallet_from_balance(balance: dict | None) -> "WalletView":
    """Prefer Binance margin fields. Fall back to USDT total/free."""
    info = (balance or {}).get("info")
    if isinstance(info, dict):
        equity = _num(info.get("totalMarginBalance"))
        free = _num(info.get("availableBalance"))
        locked = _num(info.get("totalInitialMargin"))
        if equity is not None or free is not None or locked is not None:
            if locked is None and equity is not None and free is not None:
                locked = max(equity - free, 0.0)
            label = "Equity" if equity is not None else "Wallet"
            return WalletView(
                equity=equity if equity is not None else _usdt_total(balance),
                free=free,
                locked=locked,
                label=label,
            )
    total = _usdt_total(balance)
    free = _usdt_free(balance)
    locked = None
    if total is not None and free is not None:
        locked = max(total - free, 0.0)
    return WalletView(equity=total, free=free, locked=locked, label="Wallet")


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usdt_total(balance: dict | None) -> float | None:
    usdt = (balance or {}).get("USDT") or {}
    total = _num(usdt.get("total"))
    if total is not None:
        return total
    tot = (balance or {}).get("total") or {}
    return _num(tot.get("USDT"))


def _usdt_free(balance: dict | None) -> float | None:
    usdt = (balance or {}).get("USDT") or {}
    free = _num(usdt.get("free"))
    if free is not None:
        return free
    fr = (balance or {}).get("free") or {}
    return _num(fr.get("USDT"))


@dataclass(frozen=True)
class WalletView:
    equity: float | None
    free: float | None
    locked: float | None
    label: str


@dataclass(frozen=True)
class PositionView:
    symbol: str
    display: str
    side: str
    leverage: float | None
    unrealized: float | None
    entry: float | None
    mark: float | None
    stop_loss: float | None
    take_profit: float | None
    channel: str | None
    matched: bool


@dataclass(frozen=True)
class LimitView:
    symbol: str
    display: str
    side: str
    entry: float | None
    stop_loss: float | None
    window_end: datetime | None
    mark: float | None
    channel: str | None


@dataclass(frozen=True)
class BookView:
    positions: tuple[PositionView, ...]
    limits: tuple[LimitView, ...]
    symbols: frozenset[str]


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _trade_get(trade: Any, name: str, default: Any = None) -> Any:
    if isinstance(trade, dict):
        return trade.get(name, default)
    return getattr(trade, name, default)


def classify_book(
    positions: list[dict] | None,
    trades: list[Any] | None,
    orders: dict[str, dict] | None = None,
    marks: dict[str, float] | None = None,
) -> BookView:
    """Split live positions and resting entry limits. Slot symbols are the union."""
    orders = orders or {}
    marks = marks or {}
    pos_rows: list[PositionView] = []
    pos_syms: set[str] = set()
    trade_list = list(trades or [])

    for raw in positions or []:
        symbol = str(raw.get("symbol") or "")
        key = norm_sym(symbol)
        if not key:
            continue
        pos_syms.add(key)
        hits = [
            t
            for t in trade_list
            if norm_sym(_trade_get(t, "symbol") or _trade_get(t, "pair")) == key
        ]
        trade = hits[0] if hits else None
        signal_entry = _num(_trade_get(trade, "entry")) if trade is not None else None
        entry = signal_entry if signal_entry is not None else _num(raw.get("entry"))
        sl = _num(_trade_get(trade, "stop_loss")) if trade is not None else None
        tp = _num(_trade_get(trade, "take_profit")) if trade is not None else None
        lev = _num(raw.get("leverage"))
        if lev is None and trade is not None:
            lev = _num(_trade_get(trade, "leverage"))
        pos_rows.append(
            PositionView(
                symbol=symbol,
                display=short_name(symbol),
                side=str(raw.get("side") or (_trade_get(trade, "side") if trade else "") or ""),
                leverage=lev,
                unrealized=_num(raw.get("unrealized_pnl")),
                entry=entry,
                mark=_num(raw.get("mark")),
                stop_loss=sl,
                take_profit=tp,
                channel=_trade_get(trade, "channel_key") if trade is not None else None,
                matched=trade is not None,
            )
        )

    limits: list[LimitView] = []
    for trade in trade_list:
        symbol = str(_trade_get(trade, "symbol") or _trade_get(trade, "pair") or "")
        key = norm_sym(symbol)
        if not key or key in pos_syms:
            continue
        order_id = _trade_get(trade, "order_id")
        if not order_id:
            continue
        if not entry_still_working(orders.get(str(order_id))):
            continue
        limits.append(
            LimitView(
                symbol=symbol,
                display=short_name(symbol),
                side=str(_trade_get(trade, "side") or ""),
                entry=_num(_trade_get(trade, "entry")),
                stop_loss=_num(_trade_get(trade, "stop_loss")),
                window_end=_parse_dt(_trade_get(trade, "window_end")),
                mark=_num(marks.get(key)),
                channel=_trade_get(trade, "channel_key"),
            )
        )

    symbols = set(pos_syms)
    symbols.update(norm_sym(item.symbol) for item in limits)
    symbols.discard("")
    return BookView(
        positions=tuple(pos_rows),
        limits=tuple(limits),
        symbols=frozenset(symbols),
    )


def filter_book(book: BookView, channel_key: str | None) -> BookView:
    """Display filter. Slot symbols stay account-wide on the unfiltered book."""
    if not channel_key:
        return book
    return BookView(
        positions=tuple(p for p in book.positions if p.matched and p.channel == channel_key),
        limits=tuple(item for item in book.limits if item.channel == channel_key),
        symbols=book.symbols,
    )


def position_summary(row: PositionView) -> str:
    bits = [f"{row.display} {side_word(row.side)}"]
    if row.unrealized is not None:
        bits.append(f"{format_usdt(row.unrealized, signed=True)} USDT")
    dist = r_to_sl(row.side, row.entry, row.stop_loss, row.mark)
    if dist is not None:
        bits.append(f"{format_r_abs(dist)} to SL")
    if not row.matched:
        bits.append("no signal")
    return " · ".join(bits)


def format_desk(
    *,
    now: datetime,
    wallet: WalletView,
    today_r: float | None,
    limit_r: float,
    slots: int,
    max_slots: int,
    book: BookView,
    venue_line: str,
) -> str:
    lines = [
        f"Signal Bot · {format_wib(now)}",
        "",
        f"{wallet.label} {format_usdt(wallet.equity)} USDT",
        f"Free {format_usdt(wallet.free)} · Locked {format_usdt(wallet.locked)}",
        f"Today {format_r(today_r)} / -{limit_r:g}R",
        f"Slots {slots}/{max_slots}",
        "",
    ]
    summaries = [position_summary(p) for p in book.positions]
    if not summaries:
        summaries = [f"{item.display} {order_side(item.side)} limit" for item in book.limits]
    if not summaries:
        lines.append("No open positions")
    else:
        lines.extend(summaries[:6])
        extra = len(summaries) - 6
        if extra > 0:
            lines.append(f"+{extra} more")
    lines.append("")
    lines.append(venue_line or "No venue")
    return "\n".join(lines)


def _position_block(row: PositionView) -> str:
    if row.leverage is not None:
        head = f"{row.display} {side_word(row.side)} {row.leverage:g}x"
    else:
        head = f"{row.display} {side_word(row.side)}"
    lines = [head]
    if row.unrealized is not None:
        lines.append(f"{format_usdt(row.unrealized, signed=True)} USDT")
    lines.append(f"entry {format_price(row.entry)} · mark {format_price(row.mark)}")
    to_sl = r_to_sl(row.side, row.entry, row.stop_loss, row.mark)
    to_tp = r_to_tp(row.side, row.entry, row.stop_loss, row.take_profit, row.mark)
    dist = []
    if to_sl is not None:
        dist.append(f"{format_r_abs(to_sl)} to SL")
    if to_tp is not None:
        dist.append(f"{format_r_abs(to_tp)} to TP")
    if dist:
        lines.append(" · ".join(dist))
    if not row.matched:
        lines.append("no signal")
    return "\n".join(lines)


def _remaining(end: datetime | None, now: datetime) -> str:
    if end is None:
        return "no window"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = (end - now).total_seconds()
    if seconds <= 0:
        return "window ended"
    if seconds >= 48 * 3600:
        return f"cancels in {int(seconds // 86400)}d"
    if seconds >= 3600:
        return f"cancels in {int(seconds // 3600)}h"
    return f"cancels in {max(1, int(seconds // 60))}m"


def _limit_block(row: LimitView, *, now: datetime, invalid_r: float) -> str:
    lines = [
        f"{row.display} {order_side(row.side)} limit",
        f"entry {format_price(row.entry)}",
        _remaining(row.window_end, now),
    ]
    moved = r_toward_sl(row.side, row.entry, row.stop_loss, row.mark)
    if moved is not None:
        lines.append(f"{format_r(moved)} toward SL · invalid at {invalid_r:g}R")
    else:
        lines.append(f"invalid at {invalid_r:g}R")
    return "\n".join(lines)


def format_book(
    book: BookView,
    *,
    now: datetime,
    invalid_r: float = 0.3,
    channel_key: str | None = None,
) -> str:
    shown = filter_book(book, channel_key)
    npos = len(shown.positions)
    nlim = len(shown.limits)
    pos_word = "position" if npos == 1 else "positions"
    lim_word = "limit" if nlim == 1 else "limits"
    head = f"Book · {npos} {pos_word} · {nlim} {lim_word}"
    if channel_key:
        head += f" · {channel_key}"
    lines = [head]
    if not shown.positions and not shown.limits:
        lines.append("")
        lines.append("No open positions")
        return "\n".join(lines)
    for row in shown.positions:
        lines.append("")
        lines.append(_position_block(row))
    for row in shown.limits:
        lines.append("")
        lines.append(_limit_block(row, now=now, invalid_r=invalid_r))
    return "\n".join(lines)


@dataclass(frozen=True)
class ClosedRow:
    pair: str
    status: str
    r_multiple: float | None


def format_today(
    *,
    now: datetime,
    realized_r: float | None,
    limit_r: float,
    wins: int,
    losses: int,
    rows: list[ClosedRow],
    cash: float | None,
    channel_key: str | None = None,
) -> str:
    head = f"Today · {format_wib(now, with_time=False)}"
    if channel_key:
        head += f" · {channel_key}"
    lines = [
        head,
        "",
        f"{format_r(realized_r)} / stop -{limit_r:g}R",
        f"Closed {wins + losses} · W{wins} / L{losses}",
    ]
    if cash is not None and not channel_key:
        lines.append("")
        lines.append(f"Cash {format_usdt(cash, signed=True)} USDT")
    if rows:
        lines.append("")
        for row in rows[:15]:
            rbit = format_r(row.r_multiple) if row.r_multiple is not None else ""
            lines.append(f"{short_name(row.pair)}  {row.status.upper()}  {rbit}".rstrip())
        extra = len(rows) - 15
        if extra > 0:
            lines.append(f"+{extra} more")
    return "\n".join(lines)


@dataclass(frozen=True)
class HistoryBlock:
    label: str
    total_r: float
    wins: int
    losses: int
    cash: float | None


def format_history(
    *,
    blocks: list[HistoryBlock],
    baseline_equity: float | None = None,
    baseline_at: datetime | None = None,
    live_equity: float | None = None,
    channel_key: str | None = None,
) -> str:
    lines = ["History"]
    if channel_key:
        lines[0] += f" · {channel_key}"
    lines.append("")
    for block in blocks:
        bits = [f"bot {format_r(block.total_r)}", f"W{block.wins}/L{block.losses}"]
        if block.cash is not None and not channel_key:
            bits.append(f"cash {format_usdt(block.cash, signed=True)}")
        lines.append(f"{block.label}  " + " · ".join(bits))
    lines.append("")
    if baseline_equity is not None and live_equity is not None and baseline_equity > 0:
        pct = (live_equity - baseline_equity) / baseline_equity * 100.0
        lines.append(
            f"Baseline  {format_usdt(baseline_equity)} → {format_usdt(live_equity)} · {pct:+.0f}%"
        )
        if baseline_at is not None:
            lines.append(f"since {format_wib(baseline_at, with_time=False)}")
    else:
        lines.append("No baseline")
    return "\n".join(lines)

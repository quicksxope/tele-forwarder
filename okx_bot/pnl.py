"""Shared PnL / R-multiple helpers for live close + backtest."""
from __future__ import annotations


def r_multiple(
    side: str,
    entry: float,
    exit_price: float,
    stop_loss: float | None,
) -> float | None:
    if stop_loss is None:
        return None
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    if side == "buy":
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def pnl_from_r(r: float | None, risk_usdt: float) -> float:
    if r is None:
        return 0.0
    return r * risk_usdt


def pnl_usdt(side: str, entry: float, exit_price: float, amount: float) -> float:
    """Linear USDT-M: qty × price delta (fees ignored)."""
    if amount <= 0:
        return 0.0
    if side == "buy":
        return amount * (exit_price - entry)
    return amount * (entry - exit_price)


def close_metrics(
    *,
    side: str,
    entry: float,
    exit_price: float,
    amount: float,
    stop_loss: float | None,
) -> tuple[float, float]:
    """Return (pnl_usdt, r_multiple) with r=0.0 when SL unknown."""
    pnl = pnl_usdt(side, entry, exit_price, amount)
    r = r_multiple(side, entry, exit_price, stop_loss)
    return pnl, float(r) if r is not None else 0.0


def infer_exit_status(
    *,
    side: str,
    entry: float,
    exit_price: float,
    stop_loss: float | None,
    take_profit: float | None,
) -> str:
    """Best-effort tp/sl/window_exit from exit vs levels."""
    if stop_loss is not None and take_profit is not None:
        d_sl = abs(exit_price - stop_loss)
        d_tp = abs(exit_price - take_profit)
        if d_sl <= d_tp:
            return "sl"
        return "tp"
    if stop_loss is not None:
        # Within 0.5R of SL → sl; otherwise use sign of PnL
        risk = abs(entry - stop_loss)
        if risk > 0 and abs(exit_price - stop_loss) <= risk * 0.5:
            return "sl"
    if take_profit is not None:
        risk_tp = abs(take_profit - entry)
        if risk_tp > 0 and abs(exit_price - take_profit) <= risk_tp * 0.5:
            return "tp"
    pnl = pnl_usdt(side, entry, exit_price, 1.0)
    if pnl < 0 and stop_loss is not None:
        return "sl"
    if pnl > 0 and take_profit is not None:
        return "tp"
    return "window_exit"


def position_contracts(pos: dict | None) -> float:
    if not pos:
        return 0.0
    try:
        return float(pos.get("contracts") or 0)
    except (TypeError, ValueError):
        return 0.0


def is_position_flat(pos: dict | None) -> bool:
    return position_contracts(pos) <= 1e-12

"""Period performance metrics: win rate, ROI, average R."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .trade_store import TradeRow


@dataclass(frozen=True)
class PeriodMetrics:
    start: datetime
    end: datetime
    n_signals: int
    n_closed: int
    n_wins: int
    n_losses: int
    n_skipped: int          # expired / canceled without fill PnL
    win_rate: float | None  # None if no closed decisive trades
    roi_pct: float | None
    avg_r: float | None
    total_pnl: float
    total_r: float
    equity_start: float | None
    equity_end: float | None

    def to_telegram(self, title: str = "Weekly performance") -> str:
        wr = f"{self.win_rate * 100:.1f}%" if self.win_rate is not None else "n/a"
        roi = f"{self.roi_pct:+.2f}%" if self.roi_pct is not None else "n/a"
        avg_r = f"{self.avg_r:+.2f}R" if self.avg_r is not None else "n/a"
        start_s = self.start.strftime("%Y-%m-%d")
        end_s = self.end.strftime("%Y-%m-%d")
        return (
            f"📊 {title}\n"
            f"Period: {start_s} → {end_s}\n"
            f"\n"
            f"Trades closed: {self.n_closed}\n"
            f"Wins / Losses: {self.n_wins} / {self.n_losses}\n"
            f"Skipped (no fill/expire): {self.n_skipped}\n"
            f"\n"
            f"Win rate: {wr}\n"
            f"ROI: {roi}\n"
            f"Avg R: {avg_r}\n"
            f"Total PnL: {self.total_pnl:+.4f} USDT\n"
            f"Total R: {self.total_r:+.2f}R"
        )


def _is_win(t: TradeRow) -> bool | None:
    if t.status in ("expired", "canceled") and (t.pnl is None or abs(t.pnl) < 1e-12):
        return None  # skipped
    if t.r_multiple is not None:
        if t.r_multiple > 0:
            return True
        if t.r_multiple < 0:
            return False
    if t.pnl is not None:
        if t.pnl > 0:
            return True
        if t.pnl < 0:
            return False
    if t.status == "tp":
        return True
    if t.status == "sl":
        return False
    return None


def compute_metrics(
    trades: list[TradeRow],
    *,
    start: datetime,
    end: datetime,
    equity_start: float | None = None,
    equity_end: float | None = None,
) -> PeriodMetrics:
    closed = [t for t in trades if t.closed_at]
    wins = losses = skipped = 0
    r_vals: list[float] = []
    total_pnl = 0.0
    total_r = 0.0

    for t in closed:
        outcome = _is_win(t)
        if outcome is None and t.status in ("expired", "canceled"):
            skipped += 1
            continue
        if outcome is True:
            wins += 1
        elif outcome is False:
            losses += 1
        else:
            skipped += 1
            continue
        if t.pnl is not None:
            total_pnl += t.pnl
        if t.r_multiple is not None:
            r_vals.append(t.r_multiple)
            total_r += t.r_multiple

    decisive = wins + losses
    win_rate = (wins / decisive) if decisive else None
    avg_r = (sum(r_vals) / len(r_vals)) if r_vals else None

    roi_pct = None
    if equity_start is not None and equity_start > 0:
        # Prefer mark-to-market equity if available
        if equity_end is not None:
            roi_pct = (equity_end - equity_start) / equity_start * 100.0
        else:
            roi_pct = total_pnl / equity_start * 100.0

    return PeriodMetrics(
        start=start,
        end=end,
        n_signals=len(trades),
        n_closed=decisive,
        n_wins=wins,
        n_losses=losses,
        n_skipped=skipped,
        win_rate=win_rate,
        roi_pct=roi_pct,
        avg_r=avg_r,
        total_pnl=total_pnl,
        total_r=total_r,
        equity_start=equity_start,
        equity_end=equity_end,
    )

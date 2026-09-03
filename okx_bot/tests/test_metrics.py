"""Unit tests for period metrics."""
from datetime import datetime, timedelta, timezone

from okx_bot.metrics import compute_metrics
from okx_bot.trade_store import TradeRow


def test_win_rate_roi_avg_r():
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    trades = [
        TradeRow(
            1, "backtest", "BTC/USDT", "BTC/USDT:USDT", "buy", 100, 5, 110, 90, 1,
            "tp", 110, "take_profit", 10.0, 1.0, None,
            base.isoformat(), (base + timedelta(hours=1)).isoformat(), None, None, None,
        ),
        TradeRow(
            2, "backtest", "ETH/USDT", "ETH/USDT:USDT", "buy", 100, 5, 110, 90, 1,
            "sl", 90, "stop_loss", -10.0, -1.0, None,
            base.isoformat(), (base + timedelta(hours=2)).isoformat(), None, None, None,
        ),
        TradeRow(
            3, "backtest", "SOL/USDT", "SOL/USDT:USDT", "buy", 100, 5, 110, 90, 1,
            "expired", None, "window_end_no_fill", 0.0, None, None,
            base.isoformat(), (base + timedelta(hours=3)).isoformat(), None, None, None,
        ),
    ]
    m = compute_metrics(
        trades,
        start=base,
        end=base + timedelta(days=7),
        equity_start=1000,
        equity_end=1000,
    )
    assert m.n_wins == 1
    assert m.n_losses == 1
    assert m.n_skipped == 1
    assert abs(m.win_rate - 0.5) < 1e-9
    assert abs(m.avg_r - 0.0) < 1e-9
    text = m.to_telegram()
    assert "Win rate" in text and "ROI" in text and "Avg R" in text


if __name__ == "__main__":
    test_win_rate_roi_avg_r()
    print("OK")

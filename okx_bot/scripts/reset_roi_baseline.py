"""Reset ROI baseline after Binance demo wipe / fresh start.

Usage (on host or in container):
  uv run python -m okx_bot.scripts.reset_roi_baseline
  uv run python -m okx_bot.scripts.reset_roi_baseline --equity 5000
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from okx_bot.bot import _cfg
from okx_bot.supabase_store import make_store


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset ROI baseline in trade DB")
    parser.add_argument(
        "--equity",
        type=float,
        default=None,
        help="Baseline equity USDT (default: TRADE_EQUITY_DRY_USDT or 5000)",
    )
    parser.add_argument(
        "--keep-opens",
        action="store_true",
        help="Do not cancel open trades in DB",
    )
    parser.add_argument(
        "--keep-snapshots",
        action="store_true",
        help="Do not delete prior equity_snapshots",
    )
    args = parser.parse_args()

    data = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
    cfg = _cfg()
    equity = args.equity
    if equity is None:
        equity = float(cfg.get("TRADE_EQUITY_DRY_USDT") or cfg.get("ROI_BASELINE_EQUITY") or 5000)

    store = make_store(cfg, data)
    if not hasattr(store, "reset_roi_baseline"):
        raise SystemExit(f"Store {type(store).__name__} does not support reset_roi_baseline")

    result = store.reset_roi_baseline(
        equity,
        source="live",
        close_open_trades=not args.keep_opens,
        clear_prior_snapshots=not args.keep_snapshots,
    )
    print(f"ROI baseline set to {equity} USDT")
    print(result)
    if hasattr(store, "latest_equity_baseline"):
        print("baseline=", store.latest_equity_baseline())


if __name__ == "__main__":
    main()

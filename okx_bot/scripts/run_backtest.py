"""CLI: run backtest from sample signals or a text file.

Usage:
  PYTHONPATH=. uv run python -m okx_bot.backtest_cli
  PYTHONPATH=. uv run python -m okx_bot.backtest_cli --file signals.txt
"""
from __future__ import annotations

import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import argparse
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from okx_bot.backtest import BacktestConfig, Backtester, default_store
from okx_bot.weekly_report import build_weekly_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
OKX_BOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = OKX_BOT_DIR.parent
DATA = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", REPO_ROOT / "data")).resolve()


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = OKX_BOT_DIR / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _sample_messages(exchange) -> list[tuple[datetime, str]]:
    """Build 3 sample signals near current market for a realisable backtest."""
    exchange.load_markets()
    day = datetime.now(timezone.utc) - timedelta(days=1)
    out: list[tuple[datetime, str]] = []
    specs = [
        ("BTC/USDT:USDT", "BTC/USDT", "buy", day.replace(hour=3, minute=0)),
        ("ETH/USDT:USDT", "ETH/USDT", "sell", day.replace(hour=4, minute=0)),
        ("SOL/USDT:USDT", "SOL/USDT", "buy", day.replace(hour=5, minute=0)),
    ]
    from okx_bot.parser import WIB

    for symbol, pair, side, ts in specs:
        if symbol not in exchange.markets:
            continue
        # Use candles around that local WIB afternoon window
        local = ts.astimezone(WIB)
        start = local.replace(hour=15, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=4)
        since = int(start.astimezone(timezone.utc).timestamp() * 1000)
        candles = exchange.fetch_ohlcv(symbol, timeframe="1m", since=since, limit=120)
        if not candles:
            continue
        # pick mid candle close as entry reference
        mid = candles[len(candles) // 2]
        px = float(mid[4])
        if side == "buy":
            entry = round(px * 0.999, 4 if px < 1000 else 1)
            tp = round(px * 1.004, 4 if px < 1000 else 1)
            sl = round(px * 0.996, 4 if px < 1000 else 1)
            pos = "🟢 Long"
        else:
            entry = round(px * 1.001, 4 if px < 1000 else 1)
            tp = round(px * 0.996, 4 if px < 1000 else 1)
            sl = round(px * 1.004, 4 if px < 1000 else 1)
            pos = "🔴 Short"
        tf = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} WIB"
        text = (
            f"Pair: {pair}\n"
            f"Position: {pos}\n"
            f"Entry Price: {entry}\n"
            f"Leverage: 5x\n"
            f"Take Profit: {tp}\n"
            f"Stop Loss: {sl}\n"
            f"Timeframe: {tf}"
        )
        out.append((start.astimezone(timezone.utc), text))
    return out


def _messages_from_file(path: Path) -> list[tuple[datetime, str]]:
    """File format: blocks separated by --- ; optional first line ISO ts."""
    raw = path.read_text()
    blocks = re.split(r"\n---+\n", raw)
    out: list[tuple[datetime, str]] = []
    base = datetime.now(timezone.utc) - timedelta(days=7)
    for i, block in enumerate(blocks):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        ts = base + timedelta(hours=i)
        if lines and re.match(r"\d{4}-\d{2}-\d{2}", lines[0]):
            ts = datetime.fromisoformat(lines[0].replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            block = "\n".join(lines[1:]).strip()
        out.append((ts, block))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, default=None)
    parser.add_argument("--equity", type=float, default=1000.0)
    parser.add_argument("--risk", type=float, default=10.0, help="USDT risk per 1R")
    parser.add_argument("--sandbox", action="store_true", default=True)
    args = parser.parse_args()

    env = _load_env()
    store = default_store(DATA)
    cfg = BacktestConfig(
        starting_equity=args.equity,
        risk_usdt=args.risk,
        sandbox=args.sandbox or env.get("OKX_SANDBOX", "true").lower() in ("1", "true", "yes"),
    )
    bt = Backtester(
        store,
        api_key=env.get("OKX_API_KEY", ""),
        secret=env.get("OKX_SECRET", ""),
        password=env.get("OKX_PASSWORD", ""),
        sandbox=cfg.sandbox,
        cfg=cfg,
    )
    if args.file:
        messages = _messages_from_file(Path(args.file))
    else:
        messages = _sample_messages(bt.exchange)
    metrics = bt.run_messages(messages)
    print(metrics.to_telegram("Backtest performance"))
    print()
    print(build_weekly_metrics(store, weeks=2, source="backtest"))


if __name__ == "__main__":
    main()

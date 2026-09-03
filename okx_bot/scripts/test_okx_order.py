#!/usr/bin/env python3
"""Test OKX demo connection + optional one dry/live order from sample signal."""
from __future__ import annotations

import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os
from pathlib import Path

import ccxt

from okx_bot.parser import parse_signal
from okx_bot.trader import OkxTrader


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path(__file__).resolve().parents[1] / ".env"
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("OKX_") or k.startswith("TRADE_")})
    return env


SAMPLE = """Pair: BTC/USDT
Position: 🟢 Long
Entry Price: 95000
Leverage: 10x
Take Profit: 96000
Stop Loss: 94000
Timeframe: 17:00-21:00 WIB"""


def main() -> None:
    cfg = load_env()
    sandbox = cfg.get("OKX_SANDBOX", "true").lower() in ("1", "true", "yes")
    dry_run = cfg.get("TRADE_DRY_RUN", "true").lower() in ("1", "true", "yes")

    print(f"sandbox={sandbox} dry_run={dry_run}")

    ex = ccxt.okx(
        {
            "apiKey": cfg["OKX_API_KEY"],
            "secret": cfg["OKX_SECRET"],
            "password": cfg["OKX_PASSWORD"],
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    if sandbox:
        ex.set_sandbox_mode(True)

    print("1) load_markets ...")
    markets = ex.load_markets()
    sym = "BTC/USDT:USDT"
    print(f"   markets={len(markets)} | {sym} exists={sym in markets}")

    print("2) fetch_balance ...")
    bal = ex.fetch_balance()
    usdt = bal.get("USDT") or {}
    print(f"   USDT free={usdt.get('free')} total={usdt.get('total')}")

    print("3) parse sample signal ...")
    signal = parse_signal(SAMPLE)
    assert signal is not None
    print(
        f"   {signal.side} {signal.pair} @ {signal.entry} "
        f"lev={signal.leverage} TP={signal.take_profit} SL={signal.stop_loss}"
    )

    print("4) place_order ...")
    trader = OkxTrader(
        cfg["OKX_API_KEY"],
        cfg["OKX_SECRET"],
        cfg["OKX_PASSWORD"],
        sandbox=sandbox,
        amount=float(cfg.get("TRADE_AMOUNT", "1")),
        margin_mode=cfg.get("TRADE_MARGIN_MODE", "cross"),
        order_type=cfg.get("TRADE_ORDER_TYPE", "limit"),
        position_mode=cfg.get("TRADE_POSITION_MODE", "net"),
        dry_run=dry_run,
    )
    order = trader.place_order(signal)
    print(f"   id={order.get('id')} symbol={order.get('symbol')} dry_run={order.get('dry_run')}")
    if not dry_run:
        print(f"   raw status={order.get('status')} info_keys={list((order.get('info') or {}).keys())[:8]}")
    print("DONE")


if __name__ == "__main__":
    main()

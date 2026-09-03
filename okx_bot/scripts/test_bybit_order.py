#!/usr/bin/env python3
"""Test Bybit testnet connection + optional one dry/live order from sample signal."""
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
from okx_bot.trader import BybitTrader


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = Path(__file__).resolve().parents[1] / ".env"
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    env.update(
        {
            k: v
            for k, v in os.environ.items()
            if k.startswith(("BYBIT_", "TRADE_", "EXCHANGE"))
        }
    )
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
    demo = cfg.get("BYBIT_DEMO", "false").lower() in ("1", "true", "yes")
    sandbox = cfg.get("BYBIT_SANDBOX", "true").lower() in ("1", "true", "yes") and not demo
    dry_run = cfg.get("TRADE_DRY_RUN", "true").lower() in ("1", "true", "yes")

    print(f"exchange=bybit demo={demo} sandbox={sandbox} dry_run={dry_run}")

    ex = ccxt.bybit(
        {
            "apiKey": cfg["BYBIT_API_KEY"],
            "secret": cfg["BYBIT_SECRET"],
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    if demo:
        ex.enable_demo_trading(True)
    elif sandbox:
        ex.set_sandbox_mode(True)

    host = ex.implode_hostname(ex.urls["api"]["private"])
    print(f"   endpoint={host}")
    print(f"   key_len={len(cfg['BYBIT_API_KEY'])} secret_len={len(cfg['BYBIT_SECRET'])}")

    print("1) load_markets (public) ...")
    # Avoid private /v5/asset/coin/query-info during market load
    markets = ex.load_markets(params={"type": "swap"})
    # If currencies fetch poisoned load, fall back to public instruments only
    if not markets:
        markets = ex.fetch_markets({"type": "swap"})
        ex.set_markets(markets)
    sym = "BTC/USDT:USDT"
    print(f"   markets={len(ex.markets)} | {sym} exists={sym in ex.markets}")

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
    trader = BybitTrader(
        cfg["BYBIT_API_KEY"],
        cfg["BYBIT_SECRET"],
        sandbox=sandbox,
        demo=demo,
        amount=float(cfg.get("TRADE_AMOUNT", "1")),
        equity_pct=float(cfg.get("TRADE_EQUITY_PCT", "0")),
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

# OKX Signal Bot

Parse **DEX VIP**-style Telegram signals and trade on **OKX** via [CCXT](https://github.com/ccxt/ccxt).  
Includes backtest, period metrics (win rate / ROI / avg R), and weekly Telegram reports.

## Layout

```
okx_bot/
  parser.py           # signal text → Signal
  trader.py           # CCXT OKX place/cancel/fetch
  bot.py              # Telegram listener + auto-cancel on timeframe end
  trade_store.py      # SQLite trades + equity snapshots
  metrics.py          # win rate, ROI, avg R
  backtest.py         # replay signals on OKX OHLCV
  weekly_report.py    # weekly summary → print / Telegram
  backtest_cli.py     # shim to scripts/run_backtest.py
  scripts/
    run_backtest.py   # backtest CLI
    test_okx_order.py # demo API smoke test
  tests/
    test_parser.py
    test_metrics.py
  .env.example
```

## Setup

```bash
cp okx_bot/.env.example okx_bot/.env
# fill OKX_API_KEY / OKX_SECRET / OKX_PASSWORD
# OKX_SANDBOX=true for demo trading
```

```bash
uv sync --extra tui
```

## Run

```bash
# Live / demo listener (needs Telegram session — see login_user.py)
PYTHONPATH=. uv run python -m okx_bot

# Backtest
PYTHONPATH=. uv run python okx_bot/scripts/run_backtest.py

# Weekly report (print)
PYTHONPATH=. uv run python -m okx_bot.weekly_report --source backtest

# Weekly report → Telegram
PYTHONPATH=. uv run python -m okx_bot.weekly_report --source live --send

# Unit tests
PYTHONPATH=. uv run python okx_bot/tests/test_parser.py
PYTHONPATH=. uv run python okx_bot/tests/test_metrics.py
```

## Signal format (DEX VIP)

```
Pair: GTC/USDT
Position: 🟢 Long
Entry Price: 0.09086
Leverage: 10x
Take Profit: 0.09224
Stop Loss: 0.08948
Timeframe: 15:07-19:07 WIB
```

## Weekly metrics

Per period the bot reports:

- **Win rate** — profitable closed trades ÷ decisive trades  
- **ROI** — equity change over the period (`fetch_balance` / snapshots)  
- **Avg R** — mean R-multiple vs stop distance  

Cron example (Mondays 09:00 WIB):

```bash
0 9 * * 1 cd /path/to/repo && PYTHONPATH=. uv run python -m okx_bot.weekly_report --source live --send
```

## Safety

- Default `TRADE_DRY_RUN=true`  
- Use `OKX_SANDBOX=true` for demo keys  
- Never commit `okx_bot/.env` or `data/`

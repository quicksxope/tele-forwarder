# OKX Signal Bot

Parse Telegram signal channels and trade on **OKX** via [CCXT](https://github.com/ccxt/ccxt).  
Includes backtest, period metrics (win rate / ROI / avg R), and weekly Telegram reports.

## Layout

```
okx_bot/
  channels.example.yaml # channel profiles (chat_id + parser)
  channels.py           # resolve ACTIVE_CHANNEL
  formats/              # one module per message layout
    dex_vip.py
    okx_confirm.py
  parser.py             # Signal model + parse facade
  trader.py / bot.py
  trade_store.py / metrics.py / backtest.py / weekly_report.py
  scripts/  tests/  .env.example
```

## Switch signal channel (no bot rewrite)

You usually **do not** change parser code — only config.

```bash
cp okx_bot/channels.example.yaml okx_bot/channels.yaml
```

```yaml
active: dex_vip          # ← switch here

channels:
  dex_vip:
    name: "DEX VIP | Bitcoin & Crypto"
    chat_id: -1002290536326
    parser: dex_vip        # message layout

  other_vip:
    name: "Another channel"
    chat_id: -1009999999999
    parser: dex_vip        # reuse same layout
```

Or from `.env`:

```bash
ACTIVE_CHANNEL=other_vip
```

| Goal | What to change |
|---|---|
| Same text format, new Telegram channel | New entry in `channels.yaml` + `active` / `ACTIVE_CHANNEL` |
| Different message layout | Add `formats/my_format.py`, register in `formats/__init__.py`, set `parser: my_format` |

Built-in parsers: `dex_vip`, `okx_confirm`.

## Setup

```bash
cp okx_bot/.env.example okx_bot/.env
cp okx_bot/channels.example.yaml okx_bot/channels.yaml
# fill OKX keys; OKX_SANDBOX=true for demo
uv sync --extra tui
```

## Run

```bash
PYTHONPATH=. uv run python -m okx_bot
PYTHONPATH=. uv run python okx_bot/scripts/run_backtest.py
PYTHONPATH=. uv run python -m okx_bot.weekly_report --source backtest
PYTHONPATH=. uv run python -m okx_bot.weekly_report --source live --send
PYTHONPATH=. uv run python okx_bot/tests/test_parser.py
PYTHONPATH=. uv run python okx_bot/tests/test_metrics.py
```

## Weekly metrics

- **Win rate** — profitable closed trades ÷ decisive trades  
- **ROI** — equity change over the period  
- **Avg R** — mean R-multiple vs stop distance  

## Safety

- Default `TRADE_DRY_RUN=true`  
- Use `OKX_SANDBOX=true` for demo keys  
- Never commit `okx_bot/.env` or `data/`

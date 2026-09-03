# OKX Signal Bot

Parse Telegram signal channels and trade on **OKX** or **Bybit** via [CCXT](https://github.com/ccxt/ccxt).  
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

## Exchange (OKX or Bybit)

Set in `.env`:

```bash
EXCHANGE=bybit          # default: okx
BYBIT_API_KEY=...
BYBIT_SECRET=...
BYBIT_SANDBOX=true      # testnet — separate API keys from mainnet
```

Parser stays `dex_vip` — only the execution venue changes. Test connection:

```bash
PYTHONPATH=. uv run python okx_bot/scripts/test_bybit_order.py
```

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

## Supabase

1. Run `okx_bot/supabase/schema.sql` in SQL Editor (once).
2. Add to `okx_bot/.env` (pick one):

```bash
# Preferred — Database connection string (URI)
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.xxxx.supabase.co:5432/postgres

# Or REST
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
```

Bot + weekly report then use Postgres/Supabase. Without these → local SQLite.

## Safety

- Default `TRADE_DRY_RUN=true`  
- Use `OKX_SANDBOX=true` for demo keys  
- Never commit `okx_bot/.env` or `data/`

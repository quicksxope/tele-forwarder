-- OKX Signal Bot — Supabase / Postgres schema
-- Paste into: Supabase → SQL Editor → New query → Run
-- Mirrors okx_bot/trade_store.py (SQLite) with timestamptz + channel support.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- trades
-- ---------------------------------------------------------------------------
create table if not exists public.trades (
  id              bigserial primary key,
  source          text not null
                    check (source in ('live', 'backtest')),
  channel_key     text,                         -- e.g. dex_vip from channels.yaml
  pair            text not null,                -- BTC/USDT
  symbol          text not null,                -- BTC/USDT:USDT (OKX swap)
  side            text not null
                    check (side in ('buy', 'sell')),
  entry           numeric not null,
  leverage        integer,
  take_profit     numeric,
  stop_loss       numeric,
  amount          numeric not null default 1,
  status          text not null default 'open'
                    check (status in (
                      'open', 'tp', 'sl', 'expired',
                      'canceled', 'window_exit', 'error'
                    )),
  exit_price      numeric,
  exit_reason     text,
  pnl             numeric,                      -- absolute PnL in quote (USDT)
  r_multiple      numeric,                      -- PnL / risk (R)
  order_id        text,                         -- OKX order id
  opened_at       timestamptz not null default now(),
  closed_at       timestamptz,
  window_start    timestamptz,                  -- signal validity window (WIB→UTC)
  window_end      timestamptz,
  timeframe_raw   text,                         -- original "15:07-19:07 WIB"
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists idx_trades_source_closed
  on public.trades (source, closed_at);

create index if not exists idx_trades_channel_opened
  on public.trades (channel_key, opened_at desc);

create index if not exists idx_trades_status_open
  on public.trades (status)
  where status = 'open';

create index if not exists idx_trades_order_id
  on public.trades (order_id)
  where order_id is not null;

-- ---------------------------------------------------------------------------
-- equity_snapshots (for ROI between two points in time)
-- ---------------------------------------------------------------------------
create table if not exists public.equity_snapshots (
  id          bigserial primary key,
  source      text not null
                check (source in ('live', 'backtest')),
  channel_key text,
  equity      numeric not null,
  ts          timestamptz not null default now(),
  note        text
);

create index if not exists idx_equity_source_ts
  on public.equity_snapshots (source, ts desc);

-- ---------------------------------------------------------------------------
-- signal_events (optional audit: raw Telegram message → parse result)
-- ---------------------------------------------------------------------------
create table if not exists public.signal_events (
  id              bigserial primary key,
  channel_key     text,
  chat_id         bigint,
  message_id      bigint,
  raw_text        text not null,
  parsed          boolean not null default false,
  parse_error     text,
  trade_id        bigint references public.trades (id) on delete set null,
  received_at     timestamptz not null default now()
);

create unique index if not exists uq_signal_chat_msg
  on public.signal_events (chat_id, message_id)
  where chat_id is not null and message_id is not null;

-- ---------------------------------------------------------------------------
-- updated_at helper
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_trades_updated_at on public.trades;
create trigger trg_trades_updated_at
  before update on public.trades
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Weekly metrics view (last 7 days, live only)
-- ---------------------------------------------------------------------------
create or replace view public.v_trade_stats_7d as
select
  channel_key,
  count(*) filter (where status != 'open')                         as closed_trades,
  count(*) filter (where pnl > 0)                                  as wins,
  count(*) filter (where pnl < 0)                                  as losses,
  round(
    100.0 * count(*) filter (where pnl > 0)
    / nullif(count(*) filter (where pnl is not null and status != 'open'), 0),
    2
  )                                                                as win_rate_pct,
  round(coalesce(sum(pnl), 0), 4)                                  as total_pnl,
  round(avg(r_multiple) filter (where r_multiple is not null), 4)  as avg_r
from public.trades
where source = 'live'
  and coalesce(closed_at, opened_at) >= now() - interval '7 days'
group by channel_key;

-- ---------------------------------------------------------------------------
-- RLS: bot uses service_role (bypasses RLS). Anon/authenticated denied by default.
-- ---------------------------------------------------------------------------
alter table public.trades enable row level security;
alter table public.equity_snapshots enable row level security;
alter table public.signal_events enable row level security;

-- No public policies → only service_role / postgres can read/write.
-- If you later add a dashboard with anon key, add explicit SELECT policies.

comment on table public.trades is 'OKX signal bot fills (live + backtest)';
comment on table public.equity_snapshots is 'Equity marks for ROI reporting';
comment on table public.signal_events is 'Raw Telegram signals audit log';

"""Weekly performance report → Telegram."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from telethon import TelegramClient

from .metrics import compute_metrics
from .trade_store import TradeStore

logger = logging.getLogger("okx_bot.weekly")

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", ROOT.parent / "data")).resolve()


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in (ROOT / ".env", DATA / "okx_bot.env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k.startswith(("OKX_", "TELEGRAM_", "NOTIF_", "REPORT_")):
            env[k] = v
    return env


def period_bounds(weeks: int = 1, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    end = now
    start = end - timedelta(weeks=weeks)
    return start, end


def build_weekly_metrics(
    store: TradeStore,
    *,
    weeks: int = 1,
    source: str | None = None,
) -> str:
    start, end = period_bounds(weeks)
    trades = store.trades_between(start, end, source=source, closed_only=True)
    equity_start = store.latest_equity_before(start, source=source or "live")
    equity_end = store.latest_equity_before(end, source=source or "live")
    # Fallback ROI from sum pnl if no snapshots
    if equity_start is None:
        # try backtest default snapshot
        equity_start = store.latest_equity_before(start, source="backtest")
        equity_end = store.latest_equity_before(end, source="backtest")
    metrics = compute_metrics(
        trades,
        start=start,
        end=end,
        equity_start=equity_start,
        equity_end=equity_end,
    )
    title = "Weekly signal performance"
    if source:
        title += f" ({source})"
    return metrics.to_telegram(title)


async def send_report(text: str, cfg: dict[str, str]) -> None:
    notif = int(cfg.get("NOTIF_CHAT_ID", "6878724303"))
    secrets_path = DATA / "secrets.yaml"
    api_id = cfg.get("TELEGRAM_API_ID")
    api_hash = cfg.get("TELEGRAM_API_HASH")
    bot_token = cfg.get("TELEGRAM_BOT_TOKEN")
    if secrets_path.exists():
        with open(secrets_path) as f:
            secrets = yaml.safe_load(f) or {}
        api_id = api_id or str(secrets.get("api_id", ""))
        api_hash = api_hash or secrets.get("api_hash", "")
        bot_token = bot_token or secrets.get("bot_token", "")

    session = DATA / "okx_report_bot"
    client = TelegramClient(str(session), int(api_id), api_hash)
    await client.start(bot_token=bot_token)
    await client.send_message(notif, text)
    await client.disconnect()
    logger.info("Weekly report sent to %s", notif)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Send/print weekly OKX signal metrics")
    parser.add_argument("--weeks", type=int, default=1)
    parser.add_argument("--source", default=None, help="live | backtest | all(None)")
    parser.add_argument("--send", action="store_true", help="Send via Telegram bot")
    parser.add_argument("--db", default=str(DATA / "okx_trades.db"))
    args = parser.parse_args()

    cfg = _load_env()
    store = TradeStore(Path(args.db))
    text = build_weekly_metrics(store, weeks=args.weeks, source=args.source)
    print(text)
    if args.send:
        asyncio.run(send_report(text, cfg))


if __name__ == "__main__":
    main()

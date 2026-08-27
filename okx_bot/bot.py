"""Telegram listener: parse signal text → place OKX order via CCXT."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from telethon import TelegramClient, events

from .channels import get_active_channel
from .parser import Signal
from .trader import OkxTrader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("okx_bot")

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", ROOT.parent / "data")).resolve()


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _cfg() -> dict:
    env = {}
    env.update(_load_env_file(ROOT / ".env"))
    env.update(_load_env_file(DATA / "okx_bot.env"))
    for k, v in os.environ.items():
        if k.startswith(("OKX_", "TELEGRAM_", "SIGNAL_", "NOTIF_", "TRADE_", "ACTIVE_")):
            env[k] = v
    return env


async def _cancel_when_window_ends(
    *,
    client: TelegramClient,
    trader: OkxTrader,
    notif_chat: int,
    order_id: str,
    symbol: str,
    signal: Signal,
) -> None:
    """Sleep until timeframe end, then cancel if order still open."""
    end = signal.window_end or signal.valid_until
    if end is None or not order_id:
        return

    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end_utc = end.astimezone(timezone.utc)

    delay = (end_utc - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        logger.info(
            "Scheduled cancel for order %s at %s (in %.0fs)",
            order_id,
            end_utc.isoformat(),
            delay,
        )
        await asyncio.sleep(delay)
    else:
        logger.info("Window already ended; canceling order %s now", order_id)

    try:
        order = await asyncio.to_thread(trader.fetch_order, order_id, symbol)
        status = (order.get("status") or "").lower()
        if status in ("closed", "canceled", "cancelled", "filled", "expired"):
            msg = (
                f"ℹ️ Window ended — order already {status}\n"
                f"Pair: {signal.pair}\n"
                f"Order ID: {order_id}\n"
                f"Window: {signal.timeframe_raw or end_utc}"
            )
            await client.send_message(notif_chat, msg)
            return

        result = await asyncio.to_thread(trader.cancel_order, order_id, symbol)
        logger.info("Canceled order %s: %s", order_id, result.get("status"))
        msg = (
            f"⏱ Window ended — order dibatalkan\n"
            f"Pair: {signal.pair} → {signal.okx_inst_id}\n"
            f"Order ID: {order_id}\n"
            f"Window: {signal.timeframe_raw or end_utc}\n"
            f"Status: {result.get('status') or 'canceled'}"
        )
        await client.send_message(notif_chat, msg)
    except Exception as e:
        logger.exception("Cancel-on-window-end failed for %s", order_id)
        try:
            await client.send_message(
                notif_chat,
                f"⚠️ Gagal cancel order saat window habis\n"
                f"Order ID: {order_id}\n"
                f"Error: {type(e).__name__}: {e}",
            )
        except Exception:
            pass


async def main() -> None:
    cfg = _cfg()

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

    channel = get_active_channel(cfg)
    signal_chat = channel.chat_id
    notif_chat = int(cfg.get("NOTIF_CHAT_ID", "6878724303"))
    logger.info("Active channel: %s (%s) parser=%s chat_id=%s",
                channel.key, channel.name, channel.parser, channel.chat_id)

    required_okx = ["OKX_API_KEY", "OKX_SECRET", "OKX_PASSWORD"]
    missing = [k for k in required_okx if not cfg.get(k)]
    dry_run = cfg.get("TRADE_DRY_RUN", "true").lower() in ("1", "true", "yes")
    if missing and not dry_run:
        raise SystemExit(f"Missing env for live trading: {', '.join(missing)}")

    trader = OkxTrader(
        api_key=cfg.get("OKX_API_KEY", ""),
        secret=cfg.get("OKX_SECRET", ""),
        password=cfg.get("OKX_PASSWORD", ""),
        sandbox=cfg.get("OKX_SANDBOX", "false").lower() in ("1", "true", "yes"),
        margin_mode=cfg.get("TRADE_MARGIN_MODE", "cross"),
        leverage=int(cfg.get("TRADE_LEVERAGE", "5")),
        amount=float(cfg.get("TRADE_AMOUNT", "1")),
        order_type=cfg.get("TRADE_ORDER_TYPE", "limit"),
        position_mode=cfg.get("TRADE_POSITION_MODE", "net"),
        dry_run=dry_run,
    )

    user_session = DATA / "forwarder"
    bot_session = DATA / "okx_signal_bot"
    use_user = (DATA / "forwarder.session").exists()

    if use_user:
        client = TelegramClient(str(user_session), int(api_id), api_hash)
        await client.start()
        logger.info("Using user session")
    else:
        if not bot_token:
            raise SystemExit("Need user session (login_user.py) or TELEGRAM_BOT_TOKEN")
        client = TelegramClient(str(bot_session), int(api_id), api_hash)
        await client.start(bot_token=bot_token)
        logger.info("Using bot session — bot must be member of signal group")

    me = await client.get_me()
    logger.info("Logged in as %s | dry_run=%s | watch=%s", me.id, dry_run, signal_chat)

    @client.on(events.NewMessage(chats=signal_chat))
    async def on_signal(event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        signal = channel.parse(text)
        if not signal:
            logger.debug("No signal parsed from msg %s", event.id)
            return

        logger.info(
            "Signal: %s %s @ %s lev=%sx TP=%s SL=%s window=%s→%s → %s",
            signal.side,
            signal.pair,
            signal.entry,
            signal.leverage,
            signal.take_profit,
            signal.stop_loss,
            signal.window_start,
            signal.window_end,
            signal.swap_symbol,
        )
        if signal.is_expired:
            msg = (
                f"⏭ Signal expired, skip\n"
                f"Pair: {signal.pair}\n"
                f"Side: {signal.side}\n"
                f"Entry: {signal.entry}\n"
                f"Window: {signal.timeframe_raw or signal.valid_until}"
            )
            try:
                await client.send_message(notif_chat, msg)
            except Exception as e:
                logger.error("Notify failed: %s", e)
            return

        order_id = None
        try:
            order = await asyncio.to_thread(trader.place_order, signal)
            order_id = order.get("id") or order.get("info", {}).get("ordId")
            msg = (
                f"✅ Order {'(dry-run) ' if dry_run else ''}berhasil\n"
                f"Pair: {signal.pair} → {signal.okx_inst_id}\n"
                f"Side: {signal.side}\n"
                f"Entry: {signal.entry}\n"
                f"Leverage: {signal.leverage or '-'}x\n"
                f"TP: {signal.take_profit or '-'}\n"
                f"SL: {signal.stop_loss or '-'}\n"
                f"Timeframe: {signal.timeframe_raw or '-'}\n"
                f"Amount: {trader.amount}\n"
                f"Order ID: {order_id}"
            )
            if signal.window_end or signal.valid_until:
                msg += "\n⏳ Auto-cancel saat window habis"
        except Exception as e:
            logger.exception("Order failed")
            msg = (
                f"❌ Order gagal\n"
                f"Pair: {signal.pair}\n"
                f"Side: {signal.side}\n"
                f"Entry: {signal.entry}\n"
                f"Error: {type(e).__name__}: {e}"
            )

        try:
            await client.send_message(notif_chat, msg)
        except Exception as e:
            logger.error("Notify failed: %s", e)

        if order_id and (signal.window_end or signal.valid_until):
            asyncio.create_task(
                _cancel_when_window_ends(
                    client=client,
                    trader=trader,
                    notif_chat=notif_chat,
                    order_id=str(order_id),
                    symbol=signal.swap_symbol,
                    signal=signal,
                )
            )

    print(f"Listening for signals in {signal_chat} (Ctrl+C to stop)")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())

"""Telegram listener: parse signal text → place exchange order via CCXT."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from telethon import TelegramClient, events

from .channels import get_active_channel
from .parser import Signal
from .supabase_store import make_store
from .trader import Trader, make_trader, required_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("okx_bot")

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", ROOT.parent / "data")).resolve()

RESTART_DELAY_S = int(os.environ.get("OKX_BOT_RESTART_DELAY", "15"))


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
        if k.startswith(
            (
                "OKX_",
                "BYBIT_",
                "EXCHANGE",
                "TELEGRAM_",
                "SIGNAL_",
                "NOTIF_",
                "TRADE_",
                "ACTIVE_",
                "SUPABASE_",
                "DATABASE_",
            )
        ):
            env[k] = v
    return env


async def _run_sync(fn, /, *args, **kwargs):
    """Run blocking OKX/DB work without touching the Telethon client."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _notify(
    client: TelegramClient | None,
    chat_id: int,
    text: str,
    *,
    cfg: dict | None = None,
    secrets: dict | None = None,
) -> None:
    if client is not None and client.is_connected():
        try:
            await client.send_message(chat_id, text)
            return
        except Exception:
            logger.exception("Notify via main client failed")
    if not cfg or not secrets:
        return
    bot_token = cfg.get("TELEGRAM_BOT_TOKEN") or secrets.get("bot_token")
    api_id = cfg.get("TELEGRAM_API_ID") or secrets.get("api_id")
    api_hash = cfg.get("TELEGRAM_API_HASH") or secrets.get("api_hash")
    if not bot_token or not api_id or not api_hash:
        return
    alert = TelegramClient(str(DATA / "okx_alert_bot"), int(api_id), api_hash)
    try:
        await alert.connect()
        if not await alert.is_user_authorized():
            await alert.start(bot_token=bot_token)
        await alert.send_message(chat_id, text)
    except Exception:
        logger.exception("Notify via alert bot failed")
    finally:
        await alert.disconnect()


async def _cancel_when_window_ends(
    *,
    client: TelegramClient,
    trader: Trader,
    store,
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
        order = await _run_sync(trader.fetch_order, order_id, symbol)
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

        result = await _run_sync(trader.cancel_order, order_id, symbol)
        logger.info("Canceled order %s: %s", order_id, result.get("status"))
        if hasattr(store, "close_trade_by_order_id"):
            try:
                await _run_sync(
                    store.close_trade_by_order_id,
                    order_id,
                    status="canceled",
                    exit_reason="window_end",
                )
            except Exception:
                logger.exception("Failed to close trade in store for %s", order_id)
        msg = (
            f"⏱ Window ended — order dibatalkan\n"
            f"Pair: {signal.pair} → {signal.swap_symbol}\n"
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


async def _cmd_start(
    event, *, channel, signal_chat: int, dry_run: bool, sandbox: bool, exchange: str
) -> None:
    mode = "dry-run" if dry_run else "live"
    venue = "sandbox/testnet" if sandbox else "production"
    await event.reply(
        f"🤖 {exchange.upper()} Signal Bot aktif\n\n"
        f"Channel: {channel.name}\n"
        f"Parser: {channel.parser}\n"
        f"Watch: {signal_chat}\n"
        f"Exchange: {exchange}\n"
        f"Trading: {mode} ({venue})\n\n"
        "Commands:\n"
        "/start — pesan ini\n"
        "/status — cek status bot"
    )


async def _cmd_status(
    event,
    *,
    channel,
    signal_chat: int,
    dry_run: bool,
    sandbox: bool,
    store,
    trader: Trader,
    session_started: float,
    exchange: str,
) -> None:
    uptime_s = int(time.monotonic() - session_started)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    store_name = type(store).__name__
    if trader.equity_pct > 0:
        size_line = f"Size: {trader.equity_pct}% of USDT equity\n"
    else:
        size_line = f"Size: fixed {trader.amount} (base coin)\n"
    await event.reply(
        f"📊 {exchange.upper()} Signal Bot status\n\n"
        f"Uptime: {h}h {m}m {s}s\n"
        f"Channel: {channel.key} ({channel.name})\n"
        f"Watch chat: {signal_chat}\n"
        f"Parser: {channel.parser}\n"
        f"Exchange: {exchange}\n"
        f"TRADE_DRY_RUN: {dry_run}\n"
        f"Sandbox/testnet: {sandbox}\n"
        f"{size_line}"
        f"Store: {store_name}\n"
        f"Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _register_bot_commands(
    bot_client: TelegramClient,
    *,
    owner_id: int,
    channel,
    signal_chat: int,
    dry_run: bool,
    sandbox: bool,
    store,
    trader: Trader,
    session_started: float,
    exchange: str,
) -> None:
    @bot_client.on(events.NewMessage(pattern=r"^/(start|status)(@\w+)?$"))
    async def on_bot_command(event: events.NewMessage.Event) -> None:
        if event.sender_id != owner_id:
            return
        cmd = event.pattern_match.group(1)
        if cmd == "start":
            await _cmd_start(
                event,
                channel=channel,
                signal_chat=signal_chat,
                dry_run=dry_run,
                sandbox=sandbox,
                exchange=exchange,
            )
        elif cmd == "status":
            await _cmd_status(
                event,
                channel=channel,
                signal_chat=signal_chat,
                dry_run=dry_run,
                sandbox=sandbox,
                store=store,
                trader=trader,
                session_started=session_started,
                exchange=exchange,
            )


async def _run_session(cfg: dict, secrets: dict) -> None:
    """One Telethon connection lifecycle — never reuse client across loops."""
    channel = get_active_channel(cfg)
    signal_chat = channel.chat_id
    notif_chat = int(cfg.get("NOTIF_CHAT_ID", "6878724303"))
    owner_id = int(cfg.get("OWNER_ID", notif_chat))
    session_started = time.monotonic()
    logger.info(
        "Active channel: %s (%s) parser=%s chat_id=%s",
        channel.key,
        channel.name,
        channel.parser,
        channel.chat_id,
    )

    dry_run = cfg.get("TRADE_DRY_RUN", "true").lower() in ("1", "true", "yes")
    exchange = (cfg.get("EXCHANGE") or "okx").lower().strip()
    missing = required_credentials(cfg, dry_run=dry_run)
    if missing:
        raise SystemExit(f"Missing env for live trading on {exchange}: {', '.join(missing)}")

    trader = make_trader(cfg)
    sandbox = trader.sandbox
    store = make_store(cfg, DATA)

    api_id = cfg.get("TELEGRAM_API_ID") or str(secrets.get("api_id", ""))
    api_hash = cfg.get("TELEGRAM_API_HASH") or secrets.get("api_hash", "")
    bot_token = cfg.get("TELEGRAM_BOT_TOKEN") or secrets.get("bot_token", "")

    user_session = DATA / "forwarder"
    bot_session = DATA / "okx_signal_bot"
    cmd_bot_session = DATA / "okx_cmd_bot"
    use_user = (DATA / "forwarder.session").exists()

    client = TelegramClient(
        str(user_session if use_user else bot_session),
        int(api_id),
        api_hash,
    )
    bot_client: TelegramClient | None = None
    background_tasks: set[asyncio.Task] = set()

    def _track(task: asyncio.Task) -> None:
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    try:
        if use_user:
            await client.connect()
            if not await client.is_user_authorized():
                raise SystemExit("User session missing — run: uv run python login_user.py")
            logger.info("Using user session")
            if not bot_token:
                raise SystemExit("Need TELEGRAM_BOT_TOKEN for /start and /status commands")
            bot_client = TelegramClient(str(cmd_bot_session), int(api_id), api_hash)
            await bot_client.start(bot_token=bot_token)
            logger.info("Command bot online for /start /status")
        else:
            if not bot_token:
                raise SystemExit("Need user session (login_user.py) or TELEGRAM_BOT_TOKEN")
            await client.start(bot_token=bot_token)
            logger.info("Using bot session — bot must be member of signal group")
            bot_client = client

        _register_bot_commands(
            bot_client,
            owner_id=owner_id,
            channel=channel,
            signal_chat=signal_chat,
            dry_run=dry_run,
            sandbox=sandbox,
            store=store,
            trader=trader,
            session_started=session_started,
            exchange=exchange,
        )

        me = await client.get_me()
        logger.info(
            "Logged in as %s | exchange=%s | dry_run=%s | sandbox=%s | watch=%s",
            me.id,
            exchange,
            dry_run,
            sandbox,
            signal_chat,
        )
        notify_client = bot_client if bot_client is not None else client

        async def _dm(text_msg: str) -> None:
            try:
                await notify_client.send_message(notif_chat, text_msg)
            except Exception as e:
                logger.error("Notify failed: %s", e)

        @client.on(events.NewMessage(chats=signal_chat))
        async def on_signal(event: events.NewMessage.Event) -> None:
            text = event.raw_text or ""
            signal = channel.parse(text)
            if not signal:
                preview = (text[:80] or "").replace("\n", " ")
                logger.info("Skip msg %s (not a signal): %s", event.id, preview)
                if hasattr(store, "add_signal_event"):
                    await _run_sync(
                        store.add_signal_event,
                        raw_text=text[:4000],
                        channel_key=channel.key,
                        chat_id=signal_chat,
                        message_id=event.id,
                        parsed=False,
                    )
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
                await _dm(msg)
                if hasattr(store, "add_signal_event"):
                    await _run_sync(
                        store.add_signal_event,
                        raw_text=text[:4000],
                        channel_key=channel.key,
                        chat_id=signal_chat,
                        message_id=event.id,
                        parsed=True,
                        parse_error="expired",
                    )
                return

            order_id = None
            trade_id = None
            try:
                order = await _run_sync(trader.place_order, signal)
                order_id = order.get("id") or order.get("info", {}).get("ordId")
                try:
                    trade_id = await _run_sync(
                        store.add_trade,
                        source="live",
                        channel_key=channel.key,
                        pair=signal.pair,
                        symbol=signal.swap_symbol,
                        side=signal.side,
                        entry=signal.entry,
                        leverage=signal.leverage,
                        take_profit=signal.take_profit,
                        stop_loss=signal.stop_loss,
                        amount=order.get("amount", trader.amount),
                        status="open",
                        order_id=str(order_id) if order_id else None,
                        window_start=signal.window_start,
                        window_end=signal.window_end or signal.valid_until,
                        timeframe_raw=signal.timeframe_raw,
                    )
                except Exception:
                    logger.exception("Failed to persist trade")
                msg = (
                    f"✅ Order {'(dry-run) ' if dry_run else ''}berhasil\n"
                    f"Pair: {signal.pair} → {signal.swap_symbol}\n"
                    f"Side: {signal.side}\n"
                    f"Entry: {signal.entry}\n"
                    f"Leverage: {signal.leverage or '-'}x\n"
                    f"TP: {signal.take_profit or '-'}\n"
                    f"SL: {signal.stop_loss or '-'}\n"
                    f"Timeframe: {signal.timeframe_raw or '-'}\n"
                    f"Amount: {order.get('amount', trader.amount)}\n"
                    f"Order ID: {order_id}"
                )
                if signal.window_end or signal.valid_until:
                    msg += "\n⏳ Auto-cancel saat window habis"
            except Exception as e:
                logger.exception("Order failed")
                err = str(e)
                if "SSL" in err or "CERTIFICATE" in err or "NetworkError" in type(e).__name__:
                    err = (
                        f"Network/SSL ke {exchange.upper()} gagal (sering karena Telkomsel Internet Baik). "
                        "Pakai VPN / WiFi lain / VPS."
                    )
                msg = (
                    f"❌ Order gagal\n"
                    f"Pair: {signal.pair}\n"
                    f"Side: {signal.side}\n"
                    f"Entry: {signal.entry}\n"
                    f"Error: {err}"
                )

            if hasattr(store, "add_signal_event"):
                await _run_sync(
                    store.add_signal_event,
                    raw_text=text[:4000],
                    channel_key=channel.key,
                    chat_id=signal_chat,
                    message_id=event.id,
                    parsed=True,
                    trade_id=trade_id,
                )

            await _dm(msg)

            if order_id and (signal.window_end or signal.valid_until):
                _track(
                    asyncio.create_task(
                        _cancel_when_window_ends(
                            client=notify_client,
                            trader=trader,
                            store=store,
                            notif_chat=notif_chat,
                            order_id=str(order_id),
                            symbol=signal.swap_symbol,
                            signal=signal,
                        )
                    )
                )

        print(f"Listening for signals in {signal_chat} (Ctrl+C to stop)")
        if bot_client is not client:
            await asyncio.gather(
                client.run_until_disconnected(),
                bot_client.run_until_disconnected(),
            )
        else:
            await client.run_until_disconnected()
    finally:
        for task in list(background_tasks):
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        if bot_client is not None and bot_client is not client and bot_client.is_connected():
            await bot_client.disconnect()
        if client.is_connected():
            await client.disconnect()


async def main() -> None:
    """Single event loop for the process — reconnect inside, never asyncio.run() again."""
    cfg = _cfg()
    secrets_path = DATA / "secrets.yaml"
    secrets: dict = {}
    if secrets_path.exists():
        with open(secrets_path) as f:
            secrets = yaml.safe_load(f) or {}

    api_id = cfg.get("TELEGRAM_API_ID") or secrets.get("api_id")
    api_hash = cfg.get("TELEGRAM_API_HASH") or secrets.get("api_hash")
    if not api_id or not api_hash:
        raise SystemExit("Missing TELEGRAM api_id/api_hash in secrets.yaml or .env")

    notif_chat = int(cfg.get("NOTIF_CHAT_ID", "6878724303"))
    auto_restart = cfg.get("OKX_BOT_AUTO_RESTART", "true").lower() in ("1", "true", "yes")

    while True:
        try:
            await _run_session(cfg, secrets)
            if not auto_restart:
                break
            logger.info("Disconnected — reconnecting in %ss", RESTART_DELAY_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Bot crashed")
            err = f"🚨 Bot Crashed!\n\nError: {type(e).__name__}: {e}"
            try:
                await _notify(None, notif_chat, err, cfg=cfg, secrets=secrets)
            except Exception:
                logger.exception("Failed to send crash alert")
            if not auto_restart:
                raise
            logger.info("Restarting in %ss", RESTART_DELAY_S)
        await asyncio.sleep(RESTART_DELAY_S)

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

from .channels import get_active_channel, list_enabled_channels, match_channel
from .crypto import decrypt, encrypt
from .exchange_runtime import (
    enabled_exchange_names,
    format_enabled_line,
    load_enabled,
    runtime_path,
    toggle_exchange,
)
from .parser import Signal
from .settings_menu import register_settings_menu
from .supabase_store import make_store
from .trader import BinanceTrader, BybitTrader, OkxTrader, Trader, make_trader, make_trader_for_exchange, required_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("okx_bot")

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", ROOT.parent / "data")).resolve()

RESTART_DELAY_S = int(os.environ.get("OKX_BOT_RESTART_DELAY", "15"))


def _message_topic_id(message) -> int | None:
    """Forum topic id. Named topics use reply_to_top_id or reply_to_msg_id."""
    rt = getattr(message, "reply_to", None)
    if rt is None:
        return 1
    top = getattr(rt, "reply_to_top_id", None)
    if top:
        return int(top)
    mid = getattr(rt, "reply_to_msg_id", None)
    if mid:
        return int(mid)
    return 1


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
                "BINANCE_",
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


def _load_trader_for_exchange(
    store, telegram_id: int, cfg: dict, exchange: str
) -> Trader | None:
    """Load trader for one exchange (DB credentials or env for that venue)."""
    exchange = exchange.lower().strip()
    dry_run = cfg.get("TRADE_DRY_RUN", "true").lower() in ("1", "true", "yes")
    common = {
        "margin_mode": cfg.get("TRADE_MARGIN_MODE", "cross"),
        "leverage": int(cfg.get("TRADE_LEVERAGE", "5")),
        "amount": float(cfg.get("TRADE_AMOUNT", "1")),
        "equity_pct": float(cfg.get("TRADE_EQUITY_PCT", "0")),
        "equity_dry_usdt": float(cfg.get("TRADE_EQUITY_DRY_USDT", "5000")),
        "order_type": cfg.get("TRADE_ORDER_TYPE", "limit"),
        "position_mode": cfg.get("TRADE_POSITION_MODE", "net"),
        "dry_run": dry_run,
    }
    prefer_db = cfg.get("PREFER_DB_CREDENTIALS", "true").lower() in ("1", "true", "yes")
    if prefer_db and hasattr(store, "load_credentials"):
        try:
            row = store.load_credentials(telegram_id, exchange)
        except Exception as e:
            logger.warning(
                "load_credentials failed for %s user %s (%s)",
                exchange,
                telegram_id,
                e,
            )
            row = None
        if row:
            try:
                api_key = decrypt(row["api_key_enc"])
                secret = decrypt(row["secret_enc"])
                extra = decrypt(row["extra_enc"]) if row.get("extra_enc") else ""
            except Exception:
                logger.exception("Failed to decrypt credentials for %s", exchange)
                return None
            if exchange == "bybit":
                return BybitTrader(
                    api_key=api_key,
                    secret=secret,
                    sandbox=row.get("sandbox", False),
                    demo=row.get("demo", False),
                    **common,
                )
            if exchange == "binance":
                return BinanceTrader(
                    api_key=api_key,
                    secret=secret,
                    sandbox=row.get("sandbox", False),
                    demo=row.get("demo", False),
                    **common,
                )
            return OkxTrader(
                api_key=api_key,
                secret=secret,
                password=extra,
                sandbox=row.get("sandbox", False),
                **common,
            )
    try:
        return make_trader_for_exchange(cfg, exchange)
    except ValueError:
        return None


def _load_user_trader(store, telegram_id: int, cfg: dict) -> Trader | None:
    """Legacy: trader for EXCHANGE env only."""
    exchange = (cfg.get("EXCHANGE") or "okx").lower().strip()
    return _load_trader_for_exchange(store, telegram_id, cfg, exchange)


def _resolve_order_traders(
    store, telegram_id: int, cfg: dict, *, rt_path: Path
) -> list[tuple[str, Trader]]:
    """All enabled exchanges that have a usable trader."""
    enabled = enabled_exchange_names(rt_path, cfg=cfg)
    out: list[tuple[str, Trader]] = []
    for ex in enabled:
        t = _load_trader_for_exchange(store, telegram_id, cfg, ex)
        if t is None:
            logger.warning("Exchange %s enabled but no credentials/env keys", ex)
            continue
        out.append((ex, t))
    return out


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
    cfg: dict,
    watch_channels=None,
) -> None:
    @bot_client.on(events.NewMessage(pattern=r"^/status(@\w+)?$"))
    async def on_bot_command(event: events.NewMessage.Event) -> None:
        if event.sender_id != owner_id:
            return
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

    @bot_client.on(events.NewMessage(pattern=r"^/setkey\b"))
    async def on_setkey(event: events.NewMessage.Event) -> None:
        """
        /setkey okx API_KEY SECRET PASSWORD
        /setkey bybit API_KEY SECRET
        /setkey bybit API_KEY SECRET --demo
        Only works in private chat (not in groups). Owner only.
        """
        # Delete message immediately to avoid key exposure in chat history
        try:
            await event.delete()
        except Exception:
            pass

        if event.sender_id != owner_id:
            return

        if event.is_group or event.is_channel:
            await bot_client.send_message(
                event.sender_id,
                "⚠️ Gunakan /setkey di private chat dengan bot ini saja, bukan di grup.",
            )
            return

        parts = event.raw_text.strip().split()
        # parts: ['/setkey', exchange, key, secret, (password|--demo)]
        if len(parts) < 4:
            await event.respond(
                "❌ Format salah.\n\n"
                "Lebih mudah: /settings → 🔑 Set API Key\n\n"
                "OKX: `/setkey okx API_KEY SECRET PASSWORD`\n"
                "Bybit: `/setkey bybit API_KEY SECRET`\n"
                "Bybit demo: `/setkey bybit API_KEY SECRET --demo`\n"
                "Binance: `/setkey binance API_KEY SECRET`\n"
                "Binance demo: `/setkey binance API_KEY SECRET --demo`",
            )
            return

        exch = parts[1].lower()
        if exch not in ("okx", "bybit", "binance"):
            await event.respond("❌ Exchange harus `okx`, `bybit`, atau `binance`.")
            return

        api_key_plain = parts[2]
        secret_plain = parts[3]
        extra_plain = ""
        is_demo = False
        is_sandbox = False

        if exch == "okx":
            if len(parts) < 5:
                await event.respond("❌ OKX butuh PASSWORD (passphrase). `/setkey okx KEY SECRET PASSWORD`")
                return
            extra_plain = parts[4]
        elif exch in ("bybit", "binance"):
            flags = [p.lower() for p in parts[4:]]
            if "--demo" in flags:
                is_demo = True
            elif "--sandbox" in flags:
                is_sandbox = True

        if not hasattr(store, "save_credentials"):
            await event.respond("❌ Store tidak support penyimpanan credentials.")
            return

        try:
            api_key_enc = encrypt(api_key_plain)
            secret_enc = encrypt(secret_plain)
            extra_enc = encrypt(extra_plain) if extra_plain else None
        except RuntimeError as e:
            await event.respond(f"❌ Enkripsi gagal: {e}")
            return

        try:
            await asyncio.to_thread(
                store.save_credentials,
                event.sender_id,
                exch,
                api_key_enc=api_key_enc,
                secret_enc=secret_enc,
                extra_enc=extra_enc,
                sandbox=is_sandbox,
                demo=is_demo,
            )
        except Exception as e:
            logger.exception("save_credentials failed")
            await event.respond(f"❌ Gagal simpan: {e}")
            return

        mode = "demo" if is_demo else ("sandbox" if is_sandbox else "live")
        await event.respond(
            f"✅ API key {exch.upper()} berhasil disimpan (mode: {mode}).\n"
            "Key dienkripsi dan tidak bisa dibaca kembali.\n"
            "Atau pakai /settings untuk menu tombol."
        )
        logger.info("Credentials saved for user %s exchange=%s mode=%s", event.sender_id, exch, mode)

    @bot_client.on(events.NewMessage(pattern=r"^/delkey\b"))
    async def on_delkey(event: events.NewMessage.Event) -> None:
        """Delete stored credentials: /delkey okx"""
        if event.sender_id != owner_id:
            return
        parts = event.raw_text.strip().split()
        if len(parts) < 2:
            await event.respond("❌ Format: `/delkey okx` atau `/delkey bybit` atau `/delkey binance`")
            return
        exch = parts[1].lower()
        if exch not in ("okx", "bybit", "binance"):
            await event.respond("❌ Exchange harus `okx`, `bybit`, atau `binance`.")
            return
        if not hasattr(store, "delete_credentials"):
            await event.respond("❌ Store tidak support hapus credentials.")
            return
        deleted = await asyncio.to_thread(store.delete_credentials, event.sender_id, exch)
        if deleted:
            await event.respond(f"🗑 API key {exch.upper()} berhasil dihapus.")
        else:
            await event.respond(f"ℹ️ Tidak ada key {exch.upper()} yang tersimpan.")

    @bot_client.on(events.NewMessage(pattern=r"^/mykeys$"))
    async def on_mykeys(event: events.NewMessage.Event) -> None:
        """List registered exchanges (without showing key values)."""
        if event.sender_id != owner_id:
            return
        if not hasattr(store, "list_credentials"):
            await event.respond("ℹ️ Fitur multi-user belum tersedia di store ini.")
            return
        rows = await asyncio.to_thread(store.list_credentials, event.sender_id)
        if not rows:
            await event.respond(
                "ℹ️ Belum ada API key yang terdaftar.\n\n"
                "Pakai /settings → 🔑 Set API Key\n"
                "atau:\n"
                "`/setkey okx API_KEY SECRET PASSWORD`\n"
                "`/setkey bybit API_KEY SECRET`"
            )
            return
        lines = ["🔑 API key terdaftar:\n"]
        for r in rows:
            exch = r["exchange"].upper()
            mode = "demo" if r.get("demo") else ("sandbox" if r.get("sandbox") else "live")
            lines.append(f"• {exch} — {mode}")
        await event.respond("\n".join(lines))


async def _run_session(cfg: dict, secrets: dict) -> None:
    """One Telethon connection lifecycle — never reuse client across loops."""
    channel = get_active_channel(cfg)
    watch_channels = list_enabled_channels(cfg)
    if not watch_channels:
        watch_channels = [channel]
    signal_chat = channel.chat_id
    watch_chats = sorted({c.chat_id for c in watch_channels})
    notif_chat = int(cfg.get("NOTIF_CHAT_ID", "6878724303"))
    owner_id = int(cfg.get("OWNER_ID", notif_chat))
    session_started = time.monotonic()
    logger.info(
        "Active channel: %s (%s) parser=%s chat_id=%s topic=%s",
        channel.key,
        channel.name,
        channel.parser,
        channel.chat_id,
        channel.topic_id,
    )
    for c in watch_channels:
        logger.info(
            "Watch: %s parser=%s chat_id=%s topic=%s parse_only=%s",
            c.key,
            c.parser,
            c.chat_id,
            c.topic_id,
            c.parse_only,
        )

    dry_run = cfg.get("TRADE_DRY_RUN", "true").lower() in ("1", "true", "yes")
    exchange = (cfg.get("EXCHANGE") or "okx").lower().strip()
    rt_path = runtime_path(DATA)
    load_enabled(rt_path, cfg=cfg)
    missing = required_credentials(cfg, dry_run=dry_run)
    if missing and not dry_run:
        logger.warning(
            "Env missing for legacy EXCHANGE=%s: %s — per-venue keys / DB may still work",
            exchange,
            ", ".join(missing),
        )

    trader = make_trader(cfg)
    sandbox = trader.sandbox
    store = make_store(cfg, DATA)

    api_id = cfg.get("TELEGRAM_API_ID") or str(secrets.get("api_id", ""))
    api_hash = cfg.get("TELEGRAM_API_HASH") or secrets.get("api_hash", "")
    bot_token = cfg.get("TELEGRAM_BOT_TOKEN") or secrets.get("bot_token", "")

    # Dedicated user session — do NOT share data/forwarder.session with forwarder.py
    user_session = DATA / "okx_user"
    bot_session = DATA / "okx_signal_bot"
    cmd_bot_session = DATA / "okx_cmd_bot"
    use_user = (DATA / "okx_user.session").exists()

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
                raise SystemExit(
                    "okx_user.session missing or unauthorized. "
                    "Run: uv run python login_user.py --name okx_user --send-otp "
                    "then --name okx_user --keep-session --code OTP"
                )
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

        register_settings_menu(
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
            cfg=cfg,
            watch_channels=watch_channels,
            rt_path=rt_path,
            resolve_traders=lambda: _resolve_order_traders(
                store, owner_id, cfg, rt_path=rt_path
            ),
        )

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
            cfg=cfg,
            watch_channels=watch_channels,
        )

        me = await client.get_me()
        logger.info(
            "Logged in as %s | dry_run=%s | enabled=%s | watch=%s",
            me.id,
            dry_run,
            enabled_exchange_names(rt_path, cfg=cfg),
            watch_chats,
        )
        notify_client = bot_client if bot_client is not None else client

        async def _dm(text_msg: str) -> None:
            try:
                await notify_client.send_message(notif_chat, text_msg)
            except Exception as e:
                logger.error("Notify failed: %s", e)

        @client.on(events.NewMessage(chats=watch_chats))
        async def on_signal(event: events.NewMessage.Event) -> None:
            src_chat = event.chat_id
            topic = _message_topic_id(event.message)
            src = match_channel(watch_channels, src_chat, topic)
            if src is None:
                for c in watch_channels:
                    if c.chat_id == src_chat and c.topic_id is not None:
                        logger.debug(
                            "Ignore msg %s chat=%s topic=%s (want topic=%s for %s)",
                            event.id,
                            src_chat,
                            topic,
                            c.topic_id,
                            c.key,
                        )
                return
            text = event.raw_text or ""
            if src.parser == "cryptocium" and "SETUP" not in text.upper():
                return
            signal = src.parse(text)
            if not signal:
                preview = (text[:80] or "").replace("\n", " ")
                logger.info(
                    "Skip msg %s chat=%s topic=%s (%s): %s",
                    event.id,
                    src_chat,
                    topic,
                    src.key,
                    preview,
                )
                if hasattr(store, "add_signal_event"):
                    await _run_sync(
                        store.add_signal_event,
                        raw_text=text[:4000],
                        channel_key=src.key,
                        chat_id=src_chat,
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
            parse_only = src.parse_only or cfg.get("SIGNAL_PARSE_ONLY", "false").lower() in (
                "1",
                "true",
                "yes",
            )
            parse_msg = (
                f"📥 Parsed ({src.key})\n"
                f"Chat: {src_chat} topic={topic}\n"
                f"Pair: {signal.pair} → {signal.swap_symbol}\n"
                f"Side: {signal.side}\n"
                f"Entry: {signal.entry}\n"
                f"SL: {signal.stop_loss or '-'}\n"
                f"TP: {signal.take_profit or '-'}\n"
                f"Leverage: {signal.leverage or '-'}x\n"
                f"Timeframe: {signal.timeframe_raw or '-'}\n"
                f"Window: {signal.window_start or '-'} → {signal.window_end or '-'}"
            )
            if parse_only:
                parse_msg += "\n\n⏸ parse_only — belum order"
                await _dm(parse_msg)
                if hasattr(store, "add_signal_event"):
                    await _run_sync(
                        store.add_signal_event,
                        raw_text=text[:4000],
                        channel_key=src.key,
                        chat_id=src_chat,
                        message_id=event.id,
                        parsed=True,
                        parse_error="parse_only",
                    )
                return

            if signal.is_expired:
                msg = (
                    f"⏭ Signal expired, skip\n"
                    f"Channel: {src.key}\n"
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
                        channel_key=src.key,
                        chat_id=src_chat,
                        message_id=event.id,
                        parsed=True,
                        parse_error="expired",
                    )
                return

            order_traders = await _run_sync(
                _resolve_order_traders, store, owner_id, cfg, rt_path=rt_path
            )
            if not order_traders:
                await _dm(
                    "⏸ Tidak ada venue aktif / key kosong.\n"
                    f"{format_enabled_line(rt_path, cfg=cfg)}\n"
                    "Buka /settings → Venue ON/OFF"
                )
                return

            order_kwargs = {"order_type": "limit"} if src.parser == "cryptocium" else {}
            result_lines: list[str] = []
            trade_ids: list[int] = []
            cancel_jobs: list[tuple[Trader, str]] = []

            for ex_name, active_trader in order_traders:
                logger.info("Placing order on %s", ex_name)
                try:
                    order = await _run_sync(
                        active_trader.place_order, signal, **order_kwargs
                    )
                    order_id = order.get("id") or (order.get("info") or {}).get("ordId")
                    trade_id = None
                    try:
                        trade_id = await _run_sync(
                            store.add_trade,
                            source="live",
                            channel_key=src.key,
                            exchange=ex_name,
                            pair=signal.pair,
                            symbol=signal.swap_symbol,
                            side=signal.side,
                            entry=signal.entry,
                            leverage=signal.leverage,
                            take_profit=signal.take_profit,
                            stop_loss=signal.stop_loss,
                            amount=order.get("amount", active_trader.amount),
                            status="open",
                            order_id=str(order_id) if order_id else None,
                            window_start=signal.window_start,
                            window_end=signal.window_end or signal.valid_until,
                            timeframe_raw=signal.timeframe_raw,
                        )
                    except Exception:
                        logger.exception("Failed to persist trade for %s", ex_name)
                    if trade_id:
                        trade_ids.append(trade_id)
                    dry_tag = " (dry-run)" if dry_run else ""
                    result_lines.append(
                        f"✅ {ex_name.upper()}{dry_tag}\n"
                        f"Amount: {order.get('amount', active_trader.amount)}\n"
                        f"Order ID: {order_id or '-'}"
                        + (
                            f"\n{order['protective_note']}"
                            if order.get("protective_note")
                            else ""
                        )
                    )
                    if order_id and (signal.window_end or signal.valid_until):
                        cancel_jobs.append((active_trader, str(order_id)))
                except Exception as e:
                    logger.exception("Order failed on %s", ex_name)
                    err = str(e)
                    if "SSL" in err or "CERTIFICATE" in err or "NetworkError" in type(e).__name__:
                        err = (
                            f"Network/SSL ke {ex_name.upper()} gagal. "
                            "Pakai VPN / WiFi lain / VPS."
                        )
                    result_lines.append(f"❌ {ex_name.upper()}\nError: {err}")

            lev = signal.leverage or "-"
            size_rule = ""
            if order_traders:
                _t = order_traders[0][1]
                if _t.equity_pct > 0:
                    size_rule = (
                        f"Size: {_t.equity_pct}% USDT equity (margin) × {lev}x notional\n"
                    )
                else:
                    size_rule = f"Size: fixed {_t.amount} (base coin)\n"
            header = (
                f"📤 Order results — {src.key}\n"
                f"Pair: {signal.pair} → {signal.swap_symbol}\n"
                f"Side: {signal.side} @ {signal.entry}\n"
                f"TP: {signal.take_profit or '-'} SL: {signal.stop_loss or '-'}\n"
                f"Leverage: {lev}x\n"
                f"{size_rule}\n"
            )
            msg = header + "\n\n".join(result_lines)
            if signal.window_end or signal.valid_until:
                msg += "\n\n⏳ Auto-cancel saat window habis (per venue)"

            if hasattr(store, "add_signal_event"):
                await _run_sync(
                    store.add_signal_event,
                    raw_text=text[:4000],
                    channel_key=src.key,
                    chat_id=src_chat,
                    message_id=event.id,
                    parsed=True,
                    trade_id=trade_ids[0] if trade_ids else None,
                )

            await _dm(msg)

            for active_trader, order_id in cancel_jobs:
                _track(
                    asyncio.create_task(
                        _cancel_when_window_ends(
                            client=notify_client,
                            trader=active_trader,
                            store=store,
                            notif_chat=notif_chat,
                            order_id=order_id,
                            symbol=signal.swap_symbol,
                            signal=signal,
                        )
                    )
                )
            return

        print(f"Listening for signals in {watch_chats} (Ctrl+C to stop)")
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

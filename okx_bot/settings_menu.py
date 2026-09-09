"""Owner-only Telegram settings menu: set/list/delete exchange API keys."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from telethon import Button, TelegramClient, events

from .crypto import decrypt, encrypt
from .metrics import compute_metrics
from .trader import BybitTrader, OkxTrader
from .weekly_report import period_bounds

logger = logging.getLogger("okx_bot.settings")

# In-memory wizard state keyed by telegram user id (single-process bot).
_wizards: dict[int, "WizardState"] = {}


@dataclass
class WizardState:
    step: str  # mode | api_key | secret | password
    exchange: str = ""
    sandbox: bool = False
    demo: bool = False
    api_key: str = ""
    secret: str = ""
    password: str = ""
    prompt_ids: list[int] = field(default_factory=list)


def _main_keyboard() -> list[list[Any]]:
    return [
        [
            Button.inline("💼 Assets", b"menu:assets"),
            Button.inline("📍 Positions", b"menu:positions"),
        ],
        [
            Button.inline("📈 PnL", b"menu:pnl"),
            Button.inline("📉 ROI", b"menu:roi"),
        ],
        [Button.inline("🔑 Set API Key", b"menu:set")],
        [Button.inline("📋 My Keys", b"menu:list"), Button.inline("🗑 Delete Key", b"menu:del")],
        [Button.inline("🔌 Test Connection", b"menu:test"), Button.inline("📊 Status", b"menu:status")],
    ]


def _back_keyboard() -> list[list[Any]]:
    return [[Button.inline("« Menu", b"menu:main"), Button.inline("✕ Cancel", b"menu:cancel")]]


def _exchange_keyboard(prefix: str) -> list[list[Any]]:
    return [
        [Button.inline("OKX", f"{prefix}:okx".encode()), Button.inline("Bybit", f"{prefix}:bybit".encode())],
        [Button.inline("« Menu", b"menu:main"), Button.inline("✕ Cancel", b"menu:cancel")],
    ]


def _mode_keyboard(exchange: str) -> list[list[Any]]:
    rows = [
        [Button.inline("🟢 Live", b"mode:live")],
        [Button.inline("🟡 Sandbox / Testnet", b"mode:sandbox")],
    ]
    if exchange == "bybit":
        rows.append([Button.inline("🔵 Demo Trading", b"mode:demo")])
    rows.append([Button.inline("« Menu", b"menu:main"), Button.inline("✕ Cancel", b"menu:cancel")])
    return rows


def _clear_wizard(uid: int) -> None:
    _wizards.pop(uid, None)


def _menu_text(*, channel, signal_chat: int, dry_run: bool, sandbox: bool, exchange: str) -> str:
    mode = "dry-run" if dry_run else "live"
    venue = "sandbox/testnet" if sandbox else "production"
    return (
        f"🤖 {exchange.upper()} Signal Bot\n\n"
        f"Channel: {channel.name}\n"
        f"Parser: {channel.parser}\n"
        f"Watch: {signal_chat}\n"
        f"Exchange (default): {exchange}\n"
        f"Trading: {mode} ({venue})\n\n"
        "Menu: Assets · PnL · ROI · API keys\n"
        "Hanya owner · private chat saja."
    )


def _period_metrics_text(store, *, weeks: int, source: str, title: str) -> str:
    if not hasattr(store, "trades_between"):
        return "ℹ️ Store tidak support metrics."
    start, end = period_bounds(weeks)
    trades = store.trades_between(start, end, source=source, closed_only=True)
    equity_start = None
    equity_end = None
    if hasattr(store, "latest_equity_before"):
        equity_start = store.latest_equity_before(start, source=source)
        equity_end = store.latest_equity_before(end, source=source)
        if equity_start is None and source == "live":
            equity_start = store.latest_equity_before(start, source="backtest")
            equity_end = store.latest_equity_before(end, source="backtest")
    metrics = compute_metrics(
        trades,
        start=start,
        end=end,
        equity_start=equity_start,
        equity_end=equity_end,
    )
    return metrics.to_telegram(title)


def _assets_text(store, trader, cfg: dict) -> str:
    lines = ["💼 Asset tracker\n"]
    now = datetime.now(timezone.utc)

    # Live exchange balance (env / default trader)
    try:
        bal = trader.exchange.fetch_balance()
        usdt = bal.get("USDT") or {}
        free = usdt.get("free")
        used = usdt.get("used")
        total = usdt.get("total")
        exch = getattr(trader, "exchange_name", "?").upper()
        sandbox = getattr(trader, "sandbox", False)
        demo = getattr(trader, "demo", False)
        mode = "demo" if demo else ("sandbox" if sandbox else "live")
        dry = getattr(trader, "dry_run", True)
        lines.append(f"Exchange: {exch} ({mode})" + (" · dry-run" if dry else ""))
        lines.append(f"USDT free: {free}")
        lines.append(f"USDT used: {used}")
        lines.append(f"USDT total: {total}")
    except Exception as e:
        lines.append(f"⚠️ Balance fetch gagal: {type(e).__name__}: {e}")

    if hasattr(store, "latest_equity_before"):
        eq = store.latest_equity_before(now, source="live")
        if eq is None:
            eq = store.latest_equity_before(now, source="backtest")
        if eq is not None:
            lines.append(f"\nDB equity snapshot: {eq:.4f} USDT")
        else:
            lines.append("\nDB equity snapshot: (belum ada)")

    opens = []
    if hasattr(store, "list_open_trades"):
        try:
            opens = store.list_open_trades(source="live", limit=15)
        except Exception as e:
            lines.append(f"\n⚠️ Open trades: {type(e).__name__}: {e}")
            opens = []

    lines.append(f"\nOpen positions (DB): {len(opens)}")
    if not opens:
        lines.append("• (kosong)")
    else:
        for t in opens[:10]:
            side = (t.side or "?").upper()
            lev = f"{t.leverage}x" if t.leverage else "?"
            lines.append(f"• {t.pair} {side} @ {t.entry} · {lev} · amt {t.amount}")
        if len(opens) > 10:
            lines.append(f"• … +{len(opens) - 10} lagi")

    return "\n".join(lines)


def _norm_sym(s: str | None) -> str:
    if not s:
        return ""
    return s.replace(":USDT", "").replace("-SWAP", "").replace("-", "/").upper()


def _positions_text(store, trader) -> str:
    """OKX live positions + DB open-trade match (OKX-first)."""
    exch = getattr(trader, "exchange_name", "").lower()
    if exch != "okx" or not hasattr(trader, "fetch_open_positions"):
        return (
            "📍 Active positions\n\n"
            "Saat ini hanya OKX yang didukung.\n"
            f"Trader aktif: {exch or '?'}."
        )

    sandbox = getattr(trader, "sandbox", False)
    mode = "sandbox" if sandbox else "live"
    lines = [f"📍 Active positions · OKX ({mode})\n"]

    try:
        positions = trader.fetch_open_positions()
    except Exception as e:
        return f"📍 Active positions\n\n⚠️ Gagal fetch OKX: {type(e).__name__}: {e}"

    db_opens = []
    if hasattr(store, "list_open_trades"):
        try:
            db_opens = store.list_open_trades(source="live", limit=50)
        except Exception as e:
            lines.append(f"⚠️ DB open trades: {type(e).__name__}: {e}\n")

    by_sym: dict[str, list] = {}
    for t in db_opens:
        key = _norm_sym(t.symbol or t.pair)
        by_sym.setdefault(key, []).append(t)

    matched_ids: set[int] = set()
    if not positions:
        lines.append("Tidak ada posisi terbuka di OKX.")
    else:
        lines.append(f"OKX open: {len(positions)}\n")
        for i, p in enumerate(positions, 1):
            side = (p.get("side") or "?").upper()
            entry = p.get("entry")
            mark = p.get("mark")
            upnl = p.get("unrealized_pnl")
            lev = p.get("leverage")
            contracts = p.get("contracts")
            entry_s = f"{entry:.6g}" if entry is not None else "?"
            mark_s = f"{mark:.6g}" if mark is not None else "?"
            upnl_s = f"{upnl:+.4f}" if upnl is not None else "?"
            lev_s = f"{lev:g}x" if lev is not None else "?"
            lines.append(
                f"{i}. {p.get('symbol')}\n"
                f"   {side} · size {contracts} · lev {lev_s}\n"
                f"   entry {entry_s} · mark {mark_s}\n"
                f"   uPnL {upnl_s} USDT"
            )
            key = _norm_sym(p.get("symbol"))
            hits = by_sym.get(key) or []
            if hits:
                t = hits[0]
                matched_ids.add(t.id)
                lines.append(
                    f"   DB: #{t.id} {t.pair} {(t.side or '').upper()} "
                    f"signal entry {t.entry}"
                    + (f" TP {t.take_profit}" if t.take_profit else "")
                    + (f" SL {t.stop_loss}" if t.stop_loss else "")
                )
            else:
                lines.append("   DB: (tidak ada open trade cocok)")
            lines.append("")

    stale = [t for t in db_opens if t.id not in matched_ids]
    if stale:
        lines.append(f"DB stale (open di DB, tidak di OKX): {len(stale)}")
        for t in stale[:8]:
            lines.append(
                f"• #{t.id} {t.pair} {(t.side or '').upper()} @ {t.entry} "
                f"(order {t.order_id or '-'})"
            )
        if len(stale) > 8:
            lines.append(f"• … +{len(stale) - 8} lagi")

    return "\n".join(lines).rstrip()


def _pnl_text(store) -> str:
    text_7 = _period_metrics_text(store, weeks=1, source="live", title="PnL · 7 hari (live)")
    text_30 = _period_metrics_text(store, weeks=4, source="live", title="PnL · 30 hari (live)")
    return f"{text_7}\n\n————\n\n{text_30}"


def _roi_text(store) -> str:
    """ROI-focused view from equity snapshots + trade PnL (same metrics engine)."""
    chunks: list[str] = []
    for weeks, label in ((1, "7 hari"), (4, "30 hari")):
        start, end = period_bounds(weeks)
        equity_start = equity_end = None
        if hasattr(store, "latest_equity_before"):
            equity_start = store.latest_equity_before(start, source="live")
            equity_end = store.latest_equity_before(end, source="live")
        trades = (
            store.trades_between(start, end, source="live", closed_only=True)
            if hasattr(store, "trades_between")
            else []
        )
        m = compute_metrics(
            trades,
            start=start,
            end=end,
            equity_start=equity_start,
            equity_end=equity_end,
        )
        roi = f"{m.roi_pct:+.2f}%" if m.roi_pct is not None else "n/a"
        eq_s = f"{m.equity_start:.4f}" if m.equity_start is not None else "n/a"
        eq_e = f"{m.equity_end:.4f}" if m.equity_end is not None else "n/a"
        chunks.append(
            f"📉 ROI · {label}\n"
            f"Period: {start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}\n"
            f"Equity start: {eq_s}\n"
            f"Equity end: {eq_e}\n"
            f"ROI: {roi}\n"
            f"Total PnL (closed): {m.total_pnl:+.4f} USDT\n"
            f"Closed trades: {m.n_closed} (W{m.n_wins}/L{m.n_losses})"
        )
    return "\n\n————\n\n".join(chunks)


async def _safe_delete(client: TelegramClient, chat_id: int, msg_id: int) -> None:
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


def test_exchange_connection(
    exchange: str,
    *,
    api_key: str,
    secret: str,
    password: str = "",
    sandbox: bool = False,
    demo: bool = False,
) -> str:
    """Hit fetch_balance; raise on auth/network failure."""
    common = {
        "margin_mode": "cross",
        "leverage": 1,
        "amount": 1.0,
        "equity_pct": 0.0,
        "order_type": "limit",
        "position_mode": "net",
        "dry_run": True,
    }
    if exchange == "bybit":
        trader = BybitTrader(
            api_key=api_key,
            secret=secret,
            sandbox=sandbox,
            demo=demo,
            **common,
        )
    else:
        trader = OkxTrader(
            api_key=api_key,
            secret=secret,
            password=password,
            sandbox=sandbox,
            **common,
        )
    bal = trader.exchange.fetch_balance()
    usdt = bal.get("USDT") or {}
    free = usdt.get("free")
    total = usdt.get("total")
    mode = "demo" if demo else ("sandbox" if sandbox else "live")
    return f"{exchange.upper()} {mode} OK — USDT free={free} total={total}"


def register_settings_menu(
    bot_client: TelegramClient,
    *,
    owner_id: int,
    channel,
    signal_chat: int,
    dry_run: bool,
    sandbox: bool,
    store,
    trader,
    session_started: float,
    exchange: str,
    cfg: dict,
) -> None:
    """Register /settings, callback buttons, and wizard reply handlers."""

    async def _show_main(event, *, edit: bool = False) -> None:
        text = _menu_text(
            channel=channel,
            signal_chat=signal_chat,
            dry_run=dry_run,
            sandbox=sandbox,
            exchange=exchange,
        )
        buttons = _main_keyboard()
        if edit and hasattr(event, "edit"):
            try:
                await event.edit(text, buttons=buttons)
                return
            except Exception:
                pass
        await event.respond(text, buttons=buttons)

    async def _list_keys(event, *, edit: bool = False) -> None:
        if not hasattr(store, "list_credentials"):
            msg = "ℹ️ Store belum support multi-user credentials."
            if edit:
                await event.edit(msg, buttons=_back_keyboard())
            else:
                await event.respond(msg, buttons=_back_keyboard())
            return
        rows = await asyncio.to_thread(store.list_credentials, owner_id)
        if not rows:
            msg = (
                "ℹ️ Belum ada API key tersimpan.\n"
                "Tekan 🔑 Set API Key untuk menambah."
            )
        else:
            lines = ["🔑 API key terdaftar:\n"]
            for r in rows:
                exch = r["exchange"].upper()
                mode = "demo" if r.get("demo") else ("sandbox" if r.get("sandbox") else "live")
                lines.append(f"• {exch} — {mode}")
            msg = "\n".join(lines)
        if edit:
            await event.edit(msg, buttons=_back_keyboard())
        else:
            await event.respond(msg, buttons=_back_keyboard())

    async def _save_wizard(uid: int, event) -> None:
        w = _wizards.get(uid)
        if not w or not w.exchange or not w.api_key or not w.secret:
            await event.respond("❌ Wizard tidak lengkap. Mulai lagi dari menu.")
            _clear_wizard(uid)
            return
        if not hasattr(store, "save_credentials"):
            await event.respond("❌ Store tidak support penyimpanan credentials.")
            _clear_wizard(uid)
            return
        try:
            api_key_enc = encrypt(w.api_key)
            secret_enc = encrypt(w.secret)
            extra_enc = encrypt(w.password) if w.password else None
        except RuntimeError as e:
            await event.respond(f"❌ Enkripsi gagal: {e}")
            _clear_wizard(uid)
            return

        try:
            await asyncio.to_thread(
                store.save_credentials,
                uid,
                w.exchange,
                api_key_enc=api_key_enc,
                secret_enc=secret_enc,
                extra_enc=extra_enc,
                sandbox=w.sandbox,
                demo=w.demo,
            )
        except Exception as e:
            logger.exception("save_credentials failed")
            await event.respond(f"❌ Gagal simpan: {e}")
            _clear_wizard(uid)
            return

        mode = "demo" if w.demo else ("sandbox" if w.sandbox else "live")
        try:
            result = await asyncio.to_thread(
                test_exchange_connection,
                w.exchange,
                api_key=w.api_key,
                secret=w.secret,
                password=w.password,
                sandbox=w.sandbox,
                demo=w.demo,
            )
            test_line = f"\n🔌 {result}"
        except Exception as e:
            test_line = f"\n⚠️ Key tersimpan, tapi test gagal: {type(e).__name__}: {e}"

        _clear_wizard(uid)
        await event.respond(
            f"✅ API key {w.exchange.upper()} disimpan (mode: {mode})."
            f"{test_line}\n\n"
            "Key dienkripsi; tidak bisa dibaca ulang dari bot.",
            buttons=_main_keyboard(),
        )
        logger.info("Credentials saved via menu for user %s exchange=%s", uid, w.exchange)

    @bot_client.on(events.NewMessage(pattern=r"^/(start|settings)(@\w+)?$"))
    async def on_start_settings(event: events.NewMessage.Event) -> None:
        if event.sender_id != owner_id:
            return
        if event.is_group or event.is_channel:
            await event.respond("⚠️ Settings hanya di private chat dengan bot.")
            return
        _clear_wizard(owner_id)
        await _show_main(event)

    @bot_client.on(events.CallbackQuery)
    async def on_settings_callback(event: events.CallbackQuery.Event) -> None:
        if event.sender_id != owner_id:
            await event.answer("Unauthorized", alert=True)
            return
        data = (event.data or b"").decode("utf-8", errors="replace")
        await event.answer()

        if data == "menu:main":
            _clear_wizard(owner_id)
            await _show_main(event, edit=True)
            return

        if data == "menu:cancel":
            _clear_wizard(owner_id)
            await event.edit("Dibatalkan.", buttons=_main_keyboard())
            return

        if data == "menu:list":
            await _list_keys(event, edit=True)
            return

        if data == "menu:assets":
            await event.edit("⏳ Loading assets…")
            text = await asyncio.to_thread(_assets_text, store, trader, cfg)
            await event.edit(text, buttons=_back_keyboard())
            return

        if data == "menu:positions":
            await event.edit("⏳ Loading OKX positions…")
            text = await asyncio.to_thread(_positions_text, store, trader)
            # Telegram hard limit 4096
            if len(text) > 4000:
                text = text[:3990] + "\n…"
            await event.edit(text, buttons=_back_keyboard())
            return

        if data == "menu:pnl":
            await event.edit("⏳ Loading PnL…")
            text = await asyncio.to_thread(_pnl_text, store)
            await event.edit(text, buttons=_back_keyboard())
            return

        if data == "menu:roi":
            await event.edit("⏳ Loading ROI…")
            text = await asyncio.to_thread(_roi_text, store)
            await event.edit(text, buttons=_back_keyboard())
            return

        if data == "menu:status":
            uptime_s = int(time.monotonic() - session_started)
            h, rem = divmod(uptime_s, 3600)
            m, s = divmod(rem, 60)
            store_name = type(store).__name__
            text = (
                f"📊 Status\n\n"
                f"Uptime: {h}h {m}m {s}s\n"
                f"Channel: {channel.key} ({channel.name})\n"
                f"Watch: {signal_chat}\n"
                f"Parser: {channel.parser}\n"
                f"Exchange: {exchange}\n"
                f"TRADE_DRY_RUN: {dry_run}\n"
                f"Sandbox/testnet: {sandbox}\n"
                f"Store: {store_name}\n"
                f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await event.edit(text, buttons=_back_keyboard())
            return

        if data == "menu:set":
            _clear_wizard(owner_id)
            await event.edit("Pilih exchange:", buttons=_exchange_keyboard("set"))
            return

        if data.startswith("set:"):
            exch = data.split(":", 1)[1]
            if exch not in ("okx", "bybit"):
                return
            _wizards[owner_id] = WizardState(step="mode", exchange=exch)
            await event.edit(
                f"Exchange: {exch.upper()}\nPilih mode:",
                buttons=_mode_keyboard(exch),
            )
            return

        if data.startswith("mode:"):
            w = _wizards.get(owner_id)
            if not w or w.step not in ("mode", "exchange"):
                await event.edit("Session expired. Buka /settings lagi.", buttons=_main_keyboard())
                return
            kind = data.split(":", 1)[1]
            w.sandbox = kind == "sandbox"
            w.demo = kind == "demo"
            w.step = "api_key"
            msg = await event.edit(
                f"{w.exchange.upper()} · "
                f"{'demo' if w.demo else 'sandbox' if w.sandbox else 'live'}\n\n"
                "Kirim **API Key** sekarang (satu pesan).\n"
                "Pesan akan dihapus otomatis.",
                buttons=_back_keyboard(),
            )
            if msg:
                w.prompt_ids.append(msg.id)
            return

        if data == "menu:del":
            await event.edit("Hapus key untuk exchange mana?", buttons=_exchange_keyboard("del"))
            return

        if data.startswith("del:"):
            exch = data.split(":", 1)[1]
            if exch not in ("okx", "bybit"):
                return
            if not hasattr(store, "delete_credentials"):
                await event.edit("❌ Store tidak support hapus credentials.", buttons=_back_keyboard())
                return
            deleted = await asyncio.to_thread(store.delete_credentials, owner_id, exch)
            if deleted:
                text = f"🗑 API key {exch.upper()} dihapus."
            else:
                text = f"ℹ️ Tidak ada key {exch.upper()} tersimpan."
            await event.edit(text, buttons=_main_keyboard())
            return

        if data == "menu:test":
            await event.edit("Test koneksi exchange mana?", buttons=_exchange_keyboard("test"))
            return

        if data.startswith("test:"):
            exch = data.split(":", 1)[1]
            if exch not in ("okx", "bybit") or not hasattr(store, "load_credentials"):
                await event.edit("❌ Tidak bisa test.", buttons=_back_keyboard())
                return
            row = await asyncio.to_thread(store.load_credentials, owner_id, exch)
            if not row:
                await event.edit(
                    f"ℹ️ Belum ada key {exch.upper()}. Set dulu via menu.",
                    buttons=_back_keyboard(),
                )
                return
            try:
                api_key = decrypt(row["api_key_enc"])
                secret = decrypt(row["secret_enc"])
                password = decrypt(row["extra_enc"]) if row.get("extra_enc") else ""
                result = await asyncio.to_thread(
                    test_exchange_connection,
                    exch,
                    api_key=api_key,
                    secret=secret,
                    password=password,
                    sandbox=bool(row.get("sandbox")),
                    demo=bool(row.get("demo")),
                )
                await event.edit(f"🔌 {result}", buttons=_back_keyboard())
            except Exception as e:
                await event.edit(
                    f"❌ Test gagal: {type(e).__name__}: {e}",
                    buttons=_back_keyboard(),
                )
            return

    @bot_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_wizard_reply(event: events.NewMessage.Event) -> None:
        if event.sender_id != owner_id:
            return
        text = (event.raw_text or "").strip()
        if text.startswith("/"):
            return
        w = _wizards.get(owner_id)
        if not w or w.step not in ("api_key", "secret", "password"):
            return

        # Delete user's secret-bearing message ASAP
        await _safe_delete(bot_client, event.chat_id, event.id)

        if w.step == "api_key":
            w.api_key = text
            w.step = "secret"
            prompt = await event.respond(
                "API Key diterima.\nKirim **Secret** sekarang.",
                buttons=_back_keyboard(),
            )
            w.prompt_ids.append(prompt.id)
            return

        if w.step == "secret":
            w.secret = text
            if w.exchange == "okx":
                w.step = "password"
                prompt = await event.respond(
                    "Secret diterima.\nKirim **Passphrase** (OKX password) sekarang.",
                    buttons=_back_keyboard(),
                )
                w.prompt_ids.append(prompt.id)
                return
            await _save_wizard(owner_id, event)
            return

        if w.step == "password":
            w.password = text
            await _save_wizard(owner_id, event)
            return

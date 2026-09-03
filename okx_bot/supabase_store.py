"""Supabase (PostgREST) trade store — same surface as TradeStore."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .trade_store import TradeRow

logger = logging.getLogger("okx_bot.supabase")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat()
    return str(v)


class SupabaseStore:
    """Writes/reads public.trades + equity_snapshots + signal_events via REST."""

    def __init__(self, url: str, service_role_key: str) -> None:
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = service_role_key

    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        h = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | list | None = None,
        query: dict[str, str] | None = None,
        prefer: str | None = None,
    ) -> Any:
        qs = f"?{urllib.parse.urlencode(query)}" if query else ""
        req = urllib.request.Request(
            f"{self.base}/{path.lstrip('/')}{qs}",
            data=None if body is None else json.dumps(body).encode(),
            headers=self._headers(prefer=prefer),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode() or "null"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"Supabase {method} {path}: {e.code} {detail}") from e

    def add_trade(self, **fields: Any) -> int:
        row = {
            "source": fields.get("source", "live"),
            "channel_key": fields.get("channel_key"),
            "pair": fields["pair"],
            "symbol": fields["symbol"],
            "side": fields["side"],
            "entry": fields["entry"],
            "leverage": fields.get("leverage"),
            "take_profit": fields.get("take_profit"),
            "stop_loss": fields.get("stop_loss"),
            "amount": fields.get("amount", 1.0),
            "status": fields.get("status", "open"),
            "exit_price": fields.get("exit_price"),
            "exit_reason": fields.get("exit_reason"),
            "pnl": fields.get("pnl"),
            "r_multiple": fields.get("r_multiple"),
            "order_id": fields.get("order_id"),
            "opened_at": _iso(fields.get("opened_at")) or _utc_now(),
            "closed_at": _iso(fields.get("closed_at")),
            "window_start": _iso(fields.get("window_start")),
            "window_end": _iso(fields.get("window_end")),
            "timeframe_raw": fields.get("timeframe_raw"),
        }
        data = self._request(
            "POST",
            "trades",
            body=row,
            prefer="return=representation",
        )
        return int(data[0]["id"])

    def close_trade(
        self,
        trade_id: int,
        *,
        status: str,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        r_multiple: float,
    ) -> None:
        self._request(
            "PATCH",
            "trades",
            query={"id": f"eq.{trade_id}"},
            body={
                "status": status,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl": pnl,
                "r_multiple": r_multiple,
                "closed_at": _utc_now(),
            },
            prefer="return=minimal",
        )

    def close_trade_by_order_id(
        self,
        order_id: str,
        *,
        status: str,
        exit_reason: str,
        exit_price: float | None = None,
        pnl: float | None = None,
        r_multiple: float | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "status": status,
            "exit_reason": exit_reason,
            "closed_at": _utc_now(),
        }
        if exit_price is not None:
            body["exit_price"] = exit_price
        if pnl is not None:
            body["pnl"] = pnl
        if r_multiple is not None:
            body["r_multiple"] = r_multiple
        self._request(
            "PATCH",
            "trades",
            query={"order_id": f"eq.{order_id}", "status": "eq.open"},
            body=body,
            prefer="return=minimal",
        )

    def snapshot_equity(
        self,
        equity: float,
        *,
        source: str = "live",
        note: str = "",
        channel_key: str | None = None,
    ) -> None:
        self._request(
            "POST",
            "equity_snapshots",
            body={
                "source": source,
                "channel_key": channel_key,
                "equity": equity,
                "ts": _utc_now(),
                "note": note or None,
            },
            prefer="return=minimal",
        )

    def add_signal_event(
        self,
        *,
        raw_text: str,
        channel_key: str | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
        parsed: bool = False,
        parse_error: str | None = None,
        trade_id: int | None = None,
    ) -> None:
        try:
            self._request(
                "POST",
                "signal_events",
                body={
                    "channel_key": channel_key,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "raw_text": raw_text,
                    "parsed": parsed,
                    "parse_error": parse_error,
                    "trade_id": trade_id,
                },
                prefer="return=minimal",
            )
        except Exception:
            logger.exception("Failed to log signal_event")

    def trades_between(
        self,
        start: datetime,
        end: datetime,
        *,
        source: str | None = None,
        closed_only: bool = True,
    ) -> list[TradeRow]:
        # PostgREST and-filters
        params: dict[str, str] = {
            "select": "*",
            "and": (
                f"(or(closed_at.gte.{_iso(start)},and(closed_at.is.null,opened_at.gte.{_iso(start)})),"
                f"or(closed_at.lt.{_iso(end)},and(closed_at.is.null,opened_at.lt.{_iso(end)})))"
            ),
            "order": "opened_at.asc",
        }
        # Simpler filter: use opened_at/closed_at range via or — PostgREST and() is awkward.
        # Use: coalesce logic client-side after broader fetch.
        params = {
            "select": "*",
            "opened_at": f"lt.{_iso(end)}",
            "order": "opened_at.asc",
        }
        if source:
            params["source"] = f"eq.{source}"
        if closed_only:
            params["status"] = "neq.open"
            params["closed_at"] = f"not.is.null"
        rows = self._request("GET", "trades", query=params) or []
        start_s = start.astimezone(timezone.utc)
        end_s = end.astimezone(timezone.utc)
        out: list[TradeRow] = []
        for r in rows:
            ts_raw = r.get("closed_at") or r.get("opened_at")
            if not ts_raw:
                continue
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if start_s <= ts < end_s:
                out.append(self._row(r))
        return out

    def latest_equity_before(self, ts: datetime, *, source: str = "live") -> float | None:
        rows = self._request(
            "GET",
            "equity_snapshots",
            query={
                "select": "equity",
                "source": f"eq.{source}",
                "ts": f"lte.{_iso(ts)}",
                "order": "ts.desc",
                "limit": "1",
            },
        ) or []
        if not rows:
            return None
        return float(rows[0]["equity"])

    @staticmethod
    def _row(r: dict[str, Any]) -> TradeRow:
        return TradeRow(
            id=int(r["id"]),
            source=r["source"],
            pair=r["pair"],
            symbol=r["symbol"],
            side=r["side"],
            entry=float(r["entry"]),
            leverage=r.get("leverage"),
            take_profit=float(r["take_profit"]) if r.get("take_profit") is not None else None,
            stop_loss=float(r["stop_loss"]) if r.get("stop_loss") is not None else None,
            amount=float(r["amount"]),
            status=r["status"],
            exit_price=float(r["exit_price"]) if r.get("exit_price") is not None else None,
            exit_reason=r.get("exit_reason"),
            pnl=float(r["pnl"]) if r.get("pnl") is not None else None,
            r_multiple=float(r["r_multiple"]) if r.get("r_multiple") is not None else None,
            order_id=r.get("order_id"),
            opened_at=r.get("opened_at"),
            closed_at=r.get("closed_at"),
            window_start=r.get("window_start"),
            window_end=r.get("window_end"),
            timeframe_raw=r.get("timeframe_raw"),
        )


class PostgresStore:
    """Direct Postgres via DATABASE_URL (Supabase connection string)."""

    def __init__(self, database_url: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._psycopg = psycopg
        self._dict_row = dict_row
        url = database_url.strip()
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        self.dsn = url

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def add_trade(self, **fields: Any) -> int:
        cols = {
            "source": fields.get("source", "live"),
            "channel_key": fields.get("channel_key"),
            "pair": fields["pair"],
            "symbol": fields["symbol"],
            "side": fields["side"],
            "entry": fields["entry"],
            "leverage": fields.get("leverage"),
            "take_profit": fields.get("take_profit"),
            "stop_loss": fields.get("stop_loss"),
            "amount": fields.get("amount", 1.0),
            "status": fields.get("status", "open"),
            "exit_price": fields.get("exit_price"),
            "exit_reason": fields.get("exit_reason"),
            "pnl": fields.get("pnl"),
            "r_multiple": fields.get("r_multiple"),
            "order_id": fields.get("order_id"),
            "opened_at": _iso(fields.get("opened_at")) or _utc_now(),
            "closed_at": _iso(fields.get("closed_at")),
            "window_start": _iso(fields.get("window_start")),
            "window_end": _iso(fields.get("window_end")),
            "timeframe_raw": fields.get("timeframe_raw"),
        }
        names = ", ".join(cols)
        placeholders = ", ".join(f"%({k})s" for k in cols)
        with self._connect() as conn:
            row = conn.execute(
                f"INSERT INTO trades ({names}) VALUES ({placeholders}) RETURNING id",
                cols,
            ).fetchone()
            conn.commit()
        return int(row["id"])

    def close_trade(
        self,
        trade_id: int,
        *,
        status: str,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        r_multiple: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE trades
                SET status=%s, exit_price=%s, exit_reason=%s,
                    pnl=%s, r_multiple=%s, closed_at=%s
                WHERE id=%s
                """,
                (status, exit_price, exit_reason, pnl, r_multiple, _utc_now(), trade_id),
            )
            conn.commit()

    def close_trade_by_order_id(
        self,
        order_id: str,
        *,
        status: str,
        exit_reason: str,
        exit_price: float | None = None,
        pnl: float | None = None,
        r_multiple: float | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE trades
                SET status=%s, exit_reason=%s, exit_price=%s,
                    pnl=%s, r_multiple=%s, closed_at=%s
                WHERE order_id=%s AND status='open'
                """,
                (status, exit_reason, exit_price, pnl, r_multiple, _utc_now(), order_id),
            )
            conn.commit()

    def snapshot_equity(
        self,
        equity: float,
        *,
        source: str = "live",
        note: str = "",
        channel_key: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO equity_snapshots (source, channel_key, equity, ts, note)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (source, channel_key, equity, _utc_now(), note or None),
            )
            conn.commit()

    def add_signal_event(
        self,
        *,
        raw_text: str,
        channel_key: str | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
        parsed: bool = False,
        parse_error: str | None = None,
        trade_id: int | None = None,
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO signal_events
                      (channel_key, chat_id, message_id, raw_text, parsed, parse_error, trade_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (channel_key, chat_id, message_id, raw_text, parsed, parse_error, trade_id),
                )
                conn.commit()
        except Exception:
            logger.exception("Failed to log signal_event")

    def trades_between(
        self,
        start: datetime,
        end: datetime,
        *,
        source: str | None = None,
        closed_only: bool = True,
    ) -> list[TradeRow]:
        q = """
            SELECT * FROM trades
            WHERE COALESCE(closed_at, opened_at) >= %s
              AND COALESCE(closed_at, opened_at) < %s
        """
        params: list[Any] = [
            start.astimezone(timezone.utc),
            end.astimezone(timezone.utc),
        ]
        if source:
            q += " AND source=%s"
            params.append(source)
        if closed_only:
            q += " AND closed_at IS NOT NULL AND status <> 'open'"
        q += " ORDER BY COALESCE(closed_at, opened_at)"
        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [self._row(r) for r in rows]

    def latest_equity_before(self, ts: datetime, *, source: str = "live") -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT equity FROM equity_snapshots
                WHERE source=%s AND ts <= %s
                ORDER BY ts DESC LIMIT 1
                """,
                (source, ts.astimezone(timezone.utc)),
            ).fetchone()
        return float(row["equity"]) if row else None

    @staticmethod
    def _row(r: dict[str, Any]) -> TradeRow:
        def _f(v: Any) -> float | None:
            return float(v) if v is not None else None

        def _s(v: Any) -> str | None:
            if v is None:
                return None
            if isinstance(v, datetime):
                return v.isoformat()
            return str(v)

        return TradeRow(
            id=int(r["id"]),
            source=r["source"],
            pair=r["pair"],
            symbol=r["symbol"],
            side=r["side"],
            entry=float(r["entry"]),
            leverage=r.get("leverage"),
            take_profit=_f(r.get("take_profit")),
            stop_loss=_f(r.get("stop_loss")),
            amount=float(r["amount"]),
            status=r["status"],
            exit_price=_f(r.get("exit_price")),
            exit_reason=r.get("exit_reason"),
            pnl=_f(r.get("pnl")),
            r_multiple=_f(r.get("r_multiple")),
            order_id=r.get("order_id"),
            opened_at=_s(r.get("opened_at")),
            closed_at=_s(r.get("closed_at")),
            window_start=_s(r.get("window_start")),
            window_end=_s(r.get("window_end")),
            timeframe_raw=r.get("timeframe_raw"),
        )


def make_store(env: dict[str, str], data_dir) -> Any:
    """Prefer Supabase REST → DATABASE_URL → local SQLite."""
    from pathlib import Path

    from .trade_store import TradeStore

    url = (
        env.get("SUPABASE_URL")
        or env.get("NEXT_PUBLIC_SUPABASE_URL")
        or ""
    ).strip()
    key = (
        env.get("SUPABASE_SECRET_KEY")
        or env.get("SUPABASE_SERVICE_ROLE_KEY")
        or env.get("SUPABASE_PUBLISHABLE_KEY")
        or env.get("SUPABASE_ANON_KEY")
        or env.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
        or env.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
        or ""
    ).strip()
    if url and key:
        kind = (
            "secret"
            if (env.get("SUPABASE_SECRET_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY"))
            else "publishable/anon"
        )
        logger.info("Using Supabase REST store (%s): %s", kind, url)
        return SupabaseStore(url, key)

    db_url = (env.get("DATABASE_URL") or "").strip()
    if db_url and "[YOUR-PASSWORD]" not in db_url:
        logger.info("Using Postgres store (DATABASE_URL)")
        return PostgresStore(db_url)

    return TradeStore(Path(data_dir) / "okx_trades.db")

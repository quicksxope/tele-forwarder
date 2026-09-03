"""SQLite store for live + backtest trades and equity snapshots."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TradeRow:
    id: int
    source: str
    pair: str
    symbol: str
    side: str
    entry: float
    leverage: int | None
    take_profit: float | None
    stop_loss: float | None
    amount: float
    status: str
    exit_price: float | None
    exit_reason: str | None
    pnl: float | None
    r_multiple: float | None
    order_id: str | None
    opened_at: str | None
    closed_at: str | None
    window_start: str | None
    window_end: str | None
    timeframe_raw: str | None


class TradeStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,          -- live | backtest
                    pair TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry REAL NOT NULL,
                    leverage INTEGER,
                    take_profit REAL,
                    stop_loss REAL,
                    amount REAL NOT NULL,
                    status TEXT NOT NULL,          -- open|tp|sl|expired|canceled|window_exit
                    exit_price REAL,
                    exit_reason TEXT,
                    pnl REAL,
                    r_multiple REAL,
                    order_id TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    window_start TEXT,
                    window_end TEXT,
                    timeframe_raw TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    equity REAL NOT NULL,
                    ts TEXT NOT NULL,
                    note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_trades_closed
                    ON trades(source, closed_at);
                """
            )

    def add_trade(self, **fields: Any) -> int:
        cols = {
            "source": fields.get("source", "live"),
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
            "opened_at": fields.get("opened_at") or _utc_now(),
            "closed_at": fields.get("closed_at"),
            "window_start": fields.get("window_start"),
            "window_end": fields.get("window_end"),
            "timeframe_raw": fields.get("timeframe_raw"),
            "created_at": _utc_now(),
        }
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO trades ({', '.join(cols)})
                VALUES ({', '.join('?' for _ in cols)})
                """,
                tuple(cols.values()),
            )
            return int(cur.lastrowid)

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
                SET status=?, exit_price=?, exit_reason=?, pnl=?, r_multiple=?, closed_at=?
                WHERE id=?
                """,
                (status, exit_price, exit_reason, pnl, r_multiple, _utc_now(), trade_id),
            )

    def snapshot_equity(self, equity: float, *, source: str = "live", note: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO equity_snapshots (source, equity, ts, note) VALUES (?,?,?,?)",
                (source, equity, _utc_now(), note),
            )

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
            WHERE COALESCE(closed_at, opened_at) >= ?
              AND COALESCE(closed_at, opened_at) < ?
        """
        params: list[Any] = [start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()]
        if source:
            q += " AND source=?"
            params.append(source)
        if closed_only:
            q += " AND closed_at IS NOT NULL AND status != 'open'"
        q += " ORDER BY COALESCE(closed_at, opened_at)"
        with self._connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [self._row(r) for r in rows]

    def latest_equity_before(self, ts: datetime, *, source: str = "live") -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT equity FROM equity_snapshots
                WHERE source=? AND ts <= ?
                ORDER BY ts DESC LIMIT 1
                """,
                (source, ts.astimezone(timezone.utc).isoformat()),
            ).fetchone()
        return float(row["equity"]) if row else None

    @staticmethod
    def _row(r: sqlite3.Row) -> TradeRow:
        return TradeRow(
            id=r["id"],
            source=r["source"],
            pair=r["pair"],
            symbol=r["symbol"],
            side=r["side"],
            entry=r["entry"],
            leverage=r["leverage"],
            take_profit=r["take_profit"],
            stop_loss=r["stop_loss"],
            amount=r["amount"],
            status=r["status"],
            exit_price=r["exit_price"],
            exit_reason=r["exit_reason"],
            pnl=r["pnl"],
            r_multiple=r["r_multiple"],
            order_id=r["order_id"],
            opened_at=r["opened_at"],
            closed_at=r["closed_at"],
            window_start=r["window_start"],
            window_end=r["window_end"],
            timeframe_raw=r["timeframe_raw"],
        )

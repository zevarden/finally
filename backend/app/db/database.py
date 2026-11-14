"""SQLite data access for FinAlly (TEAM_CONTRACT.md §3).

One shared ``sqlite3`` connection (``check_same_thread=False``) guarded by a
re-entrant lock, so the class is safe to use from FastAPI's threadpool and from
background tasks at the same time. The connection runs in autocommit mode and
transactions are managed explicitly with ``BEGIN``/``SAVEPOINT``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_USER = "default"
DEFAULT_CASH = 10000.0
DEFAULT_TICKERS: tuple[str, ...] = (
    "AAPL",
    "GOOGL",
    "MSFT",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
    "JPM",
    "V",
    "NFLX",
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# <backend dir>/../db/finally.db  (app/db/database.py -> parents[2] is the backend dir)
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2].parent / "db" / "finally.db"


def _now() -> str:
    """Current time as an ISO-8601 UTC string, e.g. ``2026-11-13T12:00:00.123456+00:00``."""
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class Database:
    """Thread-safe SQLite store. All public methods return plain dicts / lists of dicts."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0  # transaction nesting depth; only touched while holding _lock
        self._conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; transactions are explicit
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self.init()

    # ------------------------------------------------------------------ lifecycle

    def init(self) -> None:
        """Create tables (if missing) and seed default data. Idempotent.

        The default user profile is created if absent. The default watchlist is
        seeded only when the profile is created, so a user who deliberately
        empties their watchlist doesn't get it refilled on restart.
        """
        with self.transaction():
            # executescript would COMMIT our transaction; run statements one by one.
            for statement in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    self._conn.execute(statement)
            if self._ensure_user(DEFAULT_USER):
                now = _now()
                self._conn.executemany(
                    "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) "
                    "VALUES (?, ?, ?, ?)",
                    [(_new_id(), DEFAULT_USER, t, now) for t in DEFAULT_TICKERS],
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_user(self, user_id: str) -> bool:
        """Create the profile row if missing. Returns True if it was created."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
            (user_id, DEFAULT_CASH, _now()),
        )
        return cur.rowcount > 0

    # ---------------------------------------------------------------- transactions

    @contextmanager
    def transaction(self) -> Iterator[Database]:
        """Atomic block. Holds the lock for its whole duration.

        The outermost block uses ``BEGIN IMMEDIATE``/``COMMIT``/``ROLLBACK``. Nested
        blocks (including the ones every write method opens internally) join the
        outer transaction via a SAVEPOINT: an exception escaping an inner block
        rolls back just that block, and an exception escaping the outer block
        rolls back everything.
        """
        with self._lock:
            depth = self._depth
            savepoint = f"sp_{depth}"
            if depth == 0:
                self._conn.execute("BEGIN IMMEDIATE")
            else:
                self._conn.execute(f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if depth == 0:
                    self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO {savepoint}")
                    self._conn.execute(f"RELEASE {savepoint}")
                raise
            else:
                self._depth -= 1
                if depth == 0:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute(f"RELEASE {savepoint}")

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------------ cash

    def get_cash(self, *, user_id: str = DEFAULT_USER) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
            ).fetchone()
            if row is None:
                with self.transaction():
                    self._ensure_user(user_id)
                return DEFAULT_CASH
            return float(row["cash_balance"])

    def set_cash(self, amount: float, *, user_id: str = DEFAULT_USER) -> None:
        with self.transaction():
            self._ensure_user(user_id)
            self._conn.execute(
                "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (float(amount), user_id)
            )

    # ------------------------------------------------------------------- watchlist

    def list_watchlist(self, *, user_id: str = DEFAULT_USER) -> list[dict[str, Any]]:
        return self._query(
            "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
            (user_id,),
        )

    def add_watchlist(self, ticker: str, *, user_id: str = DEFAULT_USER) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) "
                "VALUES (?, ?, ?, ?)",
                (_new_id(), user_id, ticker, _now()),
            )
            return cur.rowcount > 0

    def remove_watchlist(self, ticker: str, *, user_id: str = DEFAULT_USER) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------- positions

    def list_positions(self, *, user_id: str = DEFAULT_USER) -> list[dict[str, Any]]:
        return self._query(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        )

    def get_position(self, ticker: str, *, user_id: str = DEFAULT_USER) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        )
        return rows[0] if rows else None

    def upsert_position(
        self, ticker: str, quantity: float, avg_cost: float, *, user_id: str = DEFAULT_USER
    ) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (user_id, ticker) DO UPDATE SET "
                "quantity = excluded.quantity, avg_cost = excluded.avg_cost, "
                "updated_at = excluded.updated_at",
                (_new_id(), user_id, ticker, float(quantity), float(avg_cost), _now()),
            )

    def delete_position(self, ticker: str, *, user_id: str = DEFAULT_USER) -> None:
        with self.transaction():
            self._conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )

    # ---------------------------------------------------------------------- trades

    def insert_trade(
        self, ticker: str, side: str, quantity: float, price: float, *, user_id: str = DEFAULT_USER
    ) -> dict[str, Any]:
        row = {
            "id": _new_id(),
            "user_id": user_id,
            "ticker": ticker,
            "side": side,
            "quantity": float(quantity),
            "price": float(price),
            "executed_at": _now(),
        }
        with self.transaction():
            self._conn.execute(
                "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
                "VALUES (:id, :user_id, :ticker, :side, :quantity, :price, :executed_at)",
                row,
            )
        return row

    def list_trades(self, limit: int = 100, *, user_id: str = DEFAULT_USER) -> list[dict[str, Any]]:
        return self._query(
            "SELECT id, user_id, ticker, side, quantity, price, executed_at FROM trades "
            "WHERE user_id = ? ORDER BY executed_at DESC, rowid DESC LIMIT ?",
            (user_id, int(limit)),
        )

    def last_trade_prices(self, *, user_id: str = DEFAULT_USER) -> dict[str, float]:
        rows = self._query(
            "SELECT ticker, price FROM trades WHERE user_id = ? ORDER BY executed_at, rowid",
            (user_id,),
        )
        # Later rows overwrite earlier ones, leaving the latest price per ticker.
        return {r["ticker"]: float(r["price"]) for r in rows}

    # ------------------------------------------------------------------- snapshots

    def insert_snapshot(self, total_value: float, *, user_id: str = DEFAULT_USER) -> dict[str, Any]:
        row = {
            "id": _new_id(),
            "user_id": user_id,
            "total_value": float(total_value),
            "recorded_at": _now(),
        }
        with self.transaction():
            self._conn.execute(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
                "VALUES (:id, :user_id, :total_value, :recorded_at)",
                row,
            )
        return row

    def list_snapshots(
        self, limit: int = 1000, *, user_id: str = DEFAULT_USER
    ) -> list[dict[str, Any]]:
        """The most recent ``limit`` snapshots, oldest first."""
        rows = self._query(
            "SELECT total_value, recorded_at FROM portfolio_snapshots WHERE user_id = ? "
            "ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
            (user_id, int(limit)),
        )
        rows.reverse()
        return rows

    # ------------------------------------------------------------------------ chat

    def insert_chat_message(
        self,
        role: str,
        content: str,
        actions: dict[str, Any] | None = None,
        *,
        user_id: str = DEFAULT_USER,
    ) -> dict[str, Any]:
        row = {
            "id": _new_id(),
            "user_id": user_id,
            "role": role,
            "content": content,
            "actions": actions,
            "created_at": _now(),
        }
        with self.transaction():
            self._conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    user_id,
                    role,
                    content,
                    json.dumps(actions) if actions is not None else None,
                    row["created_at"],
                ),
            )
        return row

    def list_chat_messages(
        self, limit: int = 50, *, user_id: str = DEFAULT_USER
    ) -> list[dict[str, Any]]:
        """The most recent ``limit`` messages, oldest first, with ``actions`` decoded."""
        rows = self._query(
            "SELECT id, user_id, role, content, actions, created_at FROM chat_messages "
            "WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, int(limit)),
        )
        rows.reverse()
        for r in rows:
            r["actions"] = json.loads(r["actions"]) if r["actions"] is not None else None
        return rows


# ------------------------------------------------------------------------ singleton

_instance: Database | None = None
_instance_lock = threading.Lock()


def default_db_path() -> Path:
    """``DB_PATH`` env var if set and non-empty, else ``<backend dir>/../db/finally.db``."""
    env = os.environ.get("DB_PATH", "").strip()
    return Path(env) if env else DEFAULT_DB_PATH


def get_database() -> Database:
    """Process-wide ``Database`` singleton (created on first call)."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = Database(default_db_path())
    return _instance


def reset_database() -> None:
    """Close and forget the singleton (tests; or after changing ``DB_PATH``)."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            _instance.close()
        _instance = None

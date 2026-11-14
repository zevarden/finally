"""Tests for app.db (TEAM_CONTRACT.md §3)."""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest

import app.db.database as dbmod
from app.db import DEFAULT_TICKERS, Database, get_database, reset_database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def _count(db: Database, table: str) -> int:
    return db._query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


# ---------------------------------------------------------------- init / seeding


class TestInit:
    def test_creates_all_tables(self, db):
        names = {r["name"] for r in db._query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "users_profile",
            "watchlist",
            "positions",
            "trades",
            "portfolio_snapshots",
            "chat_messages",
        } <= names

    def test_seeds_default_user_and_watchlist(self, db):
        assert db.get_cash() == 10000.0
        assert [w["ticker"] for w in db.list_watchlist()] == list(DEFAULT_TICKERS)
        assert len(DEFAULT_TICKERS) == 10
        assert _count(db, "users_profile") == 1

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "finally.db"
        d = Database(path)
        assert path.exists()
        d.close()

    def test_reinit_is_idempotent_and_preserves_data(self, tmp_path):
        path = tmp_path / "test.db"
        d = Database(path)
        d.set_cash(1234.5)
        d.remove_watchlist("AAPL")
        d.add_watchlist("PYPL")
        d.upsert_position("MSFT", 2, 400.0)
        d.init()  # explicit re-init on the same connection
        d.close()

        d2 = Database(path)
        assert d2.get_cash() == 1234.5
        tickers = [w["ticker"] for w in d2.list_watchlist()]
        assert "AAPL" not in tickers and "PYPL" in tickers
        assert len(tickers) == 10
        assert _count(d2, "users_profile") == 1
        assert d2.get_position("MSFT")["quantity"] == 2
        d2.close()

    def test_emptied_watchlist_is_not_reseeded(self, tmp_path):
        path = tmp_path / "test.db"
        d = Database(path)
        for t in DEFAULT_TICKERS:
            d.remove_watchlist(t)
        d.close()
        d2 = Database(path)
        assert d2.list_watchlist() == []
        d2.close()

    def test_empty_existing_file_gets_initialised(self, tmp_path):
        path = tmp_path / "empty.db"
        path.touch()
        d = Database(path)
        assert d.get_cash() == 10000.0
        assert len(d.list_watchlist()) == 10
        d.close()

    def test_wal_mode(self, db):
        assert db._query("PRAGMA journal_mode")[0]["journal_mode"] == "wal"

    def test_timestamps_are_iso_utc(self, db):
        ts = db.list_watchlist()[0]["added_at"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.utcoffset().total_seconds() == 0


# -------------------------------------------------------------------------- cash


class TestCash:
    def test_set_and_get(self, db):
        db.set_cash(42.25)
        assert db.get_cash() == 42.25

    def test_other_user_gets_default_profile(self, db):
        assert db.get_cash(user_id="alice") == 10000.0
        db.set_cash(5.0, user_id="alice")
        assert db.get_cash(user_id="alice") == 5.0
        assert db.get_cash() == 10000.0

    def test_set_cash_for_new_user(self, db):
        db.set_cash(7.0, user_id="bob")
        assert db.get_cash(user_id="bob") == 7.0


# --------------------------------------------------------------------- watchlist


class TestWatchlist:
    def test_add_returns_true_then_false(self, db):
        assert db.add_watchlist("PYPL") is True
        assert db.add_watchlist("PYPL") is False
        assert [w["ticker"] for w in db.list_watchlist()].count("PYPL") == 1

    def test_added_goes_last(self, db):
        db.add_watchlist("PYPL")
        assert db.list_watchlist()[-1]["ticker"] == "PYPL"

    def test_entry_shape(self, db):
        assert set(db.list_watchlist()[0]) == {"ticker", "added_at"}

    def test_remove(self, db):
        assert db.remove_watchlist("AAPL") is True
        assert db.remove_watchlist("AAPL") is False
        assert "AAPL" not in [w["ticker"] for w in db.list_watchlist()]

    def test_per_user(self, db):
        assert db.add_watchlist("AAPL", user_id="alice") is True
        assert [w["ticker"] for w in db.list_watchlist(user_id="alice")] == ["AAPL"]

    def test_unique_constraint_enforced_by_schema(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db._conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES ('x', 'default', 'AAPL', 'now')"
            )


# --------------------------------------------------------------------- positions


class TestPositions:
    def test_empty(self, db):
        assert db.list_positions() == []
        assert db.get_position("AAPL") is None

    def test_upsert_insert_then_update(self, db):
        db.upsert_position("AAPL", 10, 190.0)
        p = db.get_position("AAPL")
        assert p["ticker"] == "AAPL" and p["quantity"] == 10 and p["avg_cost"] == 190.0
        assert set(p) == {"ticker", "quantity", "avg_cost", "updated_at"}

        db.upsert_position("AAPL", 15.5, 192.0)
        p = db.get_position("AAPL")
        assert p["quantity"] == 15.5 and p["avg_cost"] == 192.0
        assert len(db.list_positions()) == 1
        assert _count(db, "positions") == 1

    def test_list_and_delete(self, db):
        db.upsert_position("MSFT", 1, 400.0)
        db.upsert_position("AAPL", 2, 190.0)
        assert [p["ticker"] for p in db.list_positions()] == ["AAPL", "MSFT"]
        db.delete_position("AAPL")
        assert [p["ticker"] for p in db.list_positions()] == ["MSFT"]
        db.delete_position("NOPE")  # no-op

    def test_fractional(self, db):
        db.upsert_position("TSLA", 0.123456, 250.0)
        assert db.get_position("TSLA")["quantity"] == pytest.approx(0.123456)


# ------------------------------------------------------------------------ trades


class TestTrades:
    def test_insert_returns_full_row(self, db):
        t = db.insert_trade("AAPL", "buy", 5, 190.12)
        assert set(t) >= {"id", "ticker", "side", "quantity", "price", "executed_at"}
        assert t["ticker"] == "AAPL" and t["side"] == "buy"
        assert t["quantity"] == 5.0 and t["price"] == 190.12
        assert db.list_trades()[0] == t

    def test_list_newest_first_and_limit(self, db):
        ids = [db.insert_trade("AAPL", "buy", i + 1, 100.0 + i)["id"] for i in range(5)]
        listed = db.list_trades()
        assert [t["id"] for t in listed] == ids[::-1]
        assert [t["id"] for t in db.list_trades(limit=2)] == ids[::-1][:2]

    def test_invalid_side_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_trade("AAPL", "hold", 1, 1.0)

    def test_last_trade_prices(self, db):
        assert db.last_trade_prices() == {}
        db.insert_trade("AAPL", "buy", 1, 190.0)
        db.insert_trade("MSFT", "buy", 1, 400.0)
        db.insert_trade("AAPL", "sell", 1, 195.5)
        assert db.last_trade_prices() == {"AAPL": 195.5, "MSFT": 400.0}
        assert db.last_trade_prices(user_id="alice") == {}


# --------------------------------------------------------------------- snapshots


class TestSnapshots:
    def test_insert_and_list_oldest_first(self, db):
        rows = [db.insert_snapshot(10000.0 + i) for i in range(5)]
        assert set(rows[0]) >= {"id", "total_value", "recorded_at"}
        listed = db.list_snapshots()
        assert [s["total_value"] for s in listed] == [10000.0 + i for i in range(5)]
        assert set(listed[0]) == {"total_value", "recorded_at"}

    def test_limit_keeps_most_recent(self, db):
        for i in range(5):
            db.insert_snapshot(float(i))
        assert [s["total_value"] for s in db.list_snapshots(limit=3)] == [2.0, 3.0, 4.0]


# -------------------------------------------------------------------------- chat


class TestChat:
    def test_user_message_without_actions(self, db):
        m = db.insert_chat_message("user", "hello")
        assert m["actions"] is None and m["role"] == "user" and m["content"] == "hello"
        assert set(m) >= {"id", "role", "content", "actions", "created_at"}
        assert db.list_chat_messages() == [m]

    def test_actions_json_round_trip(self, db):
        actions = {
            "trades": [
                {"ticker": "AAPL", "side": "buy", "quantity": 5, "status": "executed",
                 "price": 190.12, "error": None}
            ],
            "watchlist_changes": [
                {"ticker": "PYPL", "action": "add", "status": "failed", "error": "dup"}
            ],
        }
        db.insert_chat_message("assistant", "done", actions)
        stored = db.list_chat_messages()[0]
        assert stored["actions"] == actions
        raw = db._query("SELECT actions FROM chat_messages")[0]["actions"]
        assert isinstance(raw, str)

    def test_order_and_limit(self, db):
        for i in range(6):
            db.insert_chat_message("user" if i % 2 == 0 else "assistant", f"m{i}")
        assert [m["content"] for m in db.list_chat_messages()] == [f"m{i}" for i in range(6)]
        assert [m["content"] for m in db.list_chat_messages(limit=2)] == ["m4", "m5"]

    def test_invalid_role_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_chat_message("system", "nope")


# ------------------------------------------------------------------ transactions


class TestTransactions:
    def test_commit(self, db):
        with db.transaction():
            db.set_cash(1.0)
            db.upsert_position("AAPL", 1, 1.0)
        assert db.get_cash() == 1.0
        assert db.get_position("AAPL") is not None

    def test_rollback_on_exception(self, db):
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.set_cash(1.0)
                db.insert_trade("AAPL", "buy", 1, 1.0)
                db.upsert_position("AAPL", 1, 1.0)
                raise RuntimeError("boom")
        assert db.get_cash() == 10000.0
        assert db.list_trades() == []
        assert db.get_position("AAPL") is None
        assert db._depth == 0

    def test_nested_joins_outer(self, db):
        with pytest.raises(ValueError):
            with db.transaction():
                db.set_cash(1.0)
                with db.transaction():
                    db.upsert_position("AAPL", 1, 1.0)
                raise ValueError
        assert db.get_cash() == 10000.0
        assert db.get_position("AAPL") is None

    def test_inner_failure_caught_rolls_back_only_inner(self, db):
        with db.transaction():
            db.set_cash(1.0)
            try:
                with db.transaction():
                    db.upsert_position("AAPL", 1, 1.0)
                    raise ValueError
            except ValueError:
                pass
            db.add_watchlist("PYPL")
        assert db.get_cash() == 1.0
        assert db.get_position("AAPL") is None
        assert "PYPL" in [w["ticker"] for w in db.list_watchlist()]

    def test_integrity_error_inside_transaction_rolls_back(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction():
                db.set_cash(5.0)
                db.insert_trade("AAPL", "bad", 1, 1.0)
        assert db.get_cash() == 10000.0

    def test_usable_after_rollback(self, db):
        with pytest.raises(RuntimeError):
            with db.transaction():
                raise RuntimeError
        db.set_cash(3.0)
        assert db.get_cash() == 3.0

    def test_transaction_isolated_from_other_threads(self, db):
        """Another thread's write waits until the transaction finishes."""
        entered = threading.Event()
        order: list[str] = []

        def writer():
            entered.wait()
            db.set_cash(2.0)
            order.append("writer")

        t = threading.Thread(target=writer)
        t.start()
        with db.transaction():
            db.set_cash(1.0)
            entered.set()
            t.join(timeout=0.2)  # writer must be blocked on the lock
            assert t.is_alive()
            order.append("txn")
        t.join(timeout=5)
        assert order == ["txn", "writer"]
        assert db.get_cash() == 2.0


# ----------------------------------------------------------------- thread safety


def test_concurrent_writes(db):
    errors: list[BaseException] = []

    def worker(n: int):
        try:
            for i in range(50):
                db.insert_trade(f"T{n}", "buy", 1, float(i))
                db.insert_snapshot(float(i))
                with db.transaction():
                    cash = db.get_cash()
                    db.set_cash(cash - 1)
                db.list_trades(limit=5)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert _count(db, "trades") == 400
    assert _count(db, "portfolio_snapshots") == 400
    assert db.get_cash() == 10000.0 - 400


# --------------------------------------------------------------------- singleton


class TestSingleton:
    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_database()
        yield
        reset_database()

    def test_honours_db_path(self, tmp_path, monkeypatch):
        path = tmp_path / "sub" / "env.db"
        monkeypatch.setenv("DB_PATH", str(path))
        d = get_database()
        assert d is get_database()
        assert d.path == path
        assert path.exists()

    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("DB_PATH", raising=False)
        backend_dir = Path(dbmod.__file__).resolve().parents[2]
        assert dbmod.default_db_path() == backend_dir.parent / "db" / "finally.db"

    def test_empty_db_path_uses_default(self, monkeypatch):
        monkeypatch.setenv("DB_PATH", "  ")
        assert dbmod.default_db_path() == dbmod.DEFAULT_DB_PATH

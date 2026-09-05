"""Tests for backfill_balance_cents_from_points(conn).

Verifies the explicit legacy points → balance_cents bridge function.
Uses isolated temporary SQLite databases only — never touches production.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import importlib.util

_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot", _BOT_FILE, submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod
gb = _mod

backfill_balance_cents_from_points = gb.backfill_balance_cents_from_points


def _make_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _create_legacy_users_table(conn):
    """Create the minimal users table as init_db() would have before
    balance_cents existed (legacy schema without balance_cents or
    balance_migrated_at)."""
    conn.execute("""
        CREATE TABLE users (
            user_id      INTEGER PRIMARY KEY,
            first_name   TEXT    NOT NULL,
            points       INTEGER  DEFAULT 0,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            balance_migrated_at DATETIME
        )
    """)
    conn.commit()


class TestBackfillPositive(unittest.TestCase):
    """TEST 1 — POSITIVE BACKFILL: points=500, balance_cents=0, timestamp=NULL"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = os.path.join(self._tmpdir, "test.db")
        self._conn = _make_conn(self._db)
        _create_legacy_users_table(self._conn)
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (1, 'Alice', 500)"
        )
        self._conn.commit()

    def tearDown(self):
        self._conn.close()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_balance_cents_copied_from_points(self):
        rows = backfill_balance_cents_from_points(self._conn)
        self.assertEqual(rows, 1)
        user = self._conn.execute(
            "SELECT balance_cents, balance_migrated_at FROM users WHERE user_id = 1"
        ).fetchone()
        self.assertEqual(user["balance_cents"], 500)
        self.assertIsNotNone(user["balance_migrated_at"])


class TestBackfillZeroBalance(unittest.TestCase):
    """TEST 2 — ZERO BALANCE: points=0, balance_cents=0, timestamp=NULL"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = os.path.join(self._tmpdir, "test.db")
        self._conn = _make_conn(self._db)
        _create_legacy_users_table(self._conn)
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (2, 'Bob', 0)"
        )
        self._conn.commit()

    def tearDown(self):
        self._conn.close()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_zero_balance_backfilled(self):
        rows = backfill_balance_cents_from_points(self._conn)
        self.assertEqual(rows, 1)
        user = self._conn.execute(
            "SELECT balance_cents, balance_migrated_at FROM users WHERE user_id = 2"
        ).fetchone()
        self.assertEqual(user["balance_cents"], 0)
        self.assertIsNotNone(user["balance_migrated_at"])


class TestBackfillExistingBalanceNotOverwritten(unittest.TestCase):
    """TEST 3 — EXISTING BALANCE: already migrated rows untouched."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = os.path.join(self._tmpdir, "test.db")
        self._conn = _make_conn(self._db)
        _create_legacy_users_table(self._conn)
        # User already migrated with balance_cents=123
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points, balance_cents, balance_migrated_at) "
            "VALUES (3, 'Charlie', 500, 123, '2025-01-01T00:00:00')"
        )
        self._conn.commit()

    def tearDown(self):
        self._conn.close()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_existing_balance_preserved(self):
        rows = backfill_balance_cents_from_points(self._conn)
        self.assertEqual(rows, 0)  # nothing to backfill
        user = self._conn.execute(
            "SELECT balance_cents FROM users WHERE user_id = 3"
        ).fetchone()
        self.assertEqual(user["balance_cents"], 123)  # untouched


class TestBackfillIdempotent(unittest.TestCase):
    """TEST 4 — IDEMPOTENCY: second call does nothing."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = os.path.join(self._tmpdir, "test.db")
        self._conn = _make_conn(self._db)
        _create_legacy_users_table(self._conn)
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (4, 'Dana', 200)"
        )
        self._conn.commit()

    def tearDown(self):
        self._conn.close()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_idempotent(self):
        # First call
        rows1 = backfill_balance_cents_from_points(self._conn)
        self.assertEqual(rows1, 1)
        # Second call
        rows2 = backfill_balance_cents_from_points(self._conn)
        self.assertEqual(rows2, 0)  # no rows eligible

        user = self._conn.execute(
            "SELECT balance_cents, balance_migrated_at FROM users WHERE user_id = 4"
        ).fetchone()
        self.assertEqual(user["balance_cents"], 200)
        self.assertIsNotNone(user["balance_migrated_at"])


class TestBackfillMultipleUsers(unittest.TestCase):
    """TEST 5 — MULTIPLE USERS with mixed states."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = os.path.join(self._tmpdir, "test.db")
        self._conn = _make_conn(self._db)
        _create_legacy_users_table(self._conn)
        # Eligible: points=300, balance_cents=0, timestamp=NULL
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (10, 'Eve', 300)"
        )
        # Eligible: points=0, balance_cents=0, timestamp=NULL
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (11, 'Frank', 0)"
        )
        # Already migrated
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points, balance_cents, balance_migrated_at) "
            "VALUES (12, 'Grace', 500, 99, '2025-06-01T12:00:00')"
        )
        # Eligible: points=150, balance_cents=0, timestamp=NULL
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (13, 'Hank', 150)"
        )
        self._conn.commit()

    def tearDown(self):
        self._conn.close()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_only_eligible_rows_changed(self):
        rows = backfill_balance_cents_from_points(self._conn)
        self.assertEqual(rows, 3)  # users 10, 11, 13

        # Eve: 300
        eve = self._conn.execute(
            "SELECT balance_cents, balance_migrated_at FROM users WHERE user_id = 10"
        ).fetchone()
        self.assertEqual(eve["balance_cents"], 300)
        self.assertIsNotNone(eve["balance_migrated_at"])

        # Frank: 0
        frank = self._conn.execute(
            "SELECT balance_cents, balance_migrated_at FROM users WHERE user_id = 11"
        ).fetchone()
        self.assertEqual(frank["balance_cents"], 0)
        self.assertIsNotNone(frank["balance_migrated_at"])

        # Grace: untouched
        grace = self._conn.execute(
            "SELECT balance_cents, balance_migrated_at FROM users WHERE user_id = 12"
        ).fetchone()
        self.assertEqual(grace["balance_cents"], 99)
        self.assertEqual(grace["balance_migrated_at"], "2025-06-01T12:00:00")

        # Hank: 150
        hank = self._conn.execute(
            "SELECT balance_cents, balance_migrated_at FROM users WHERE user_id = 13"
        ).fetchone()
        self.assertEqual(hank["balance_cents"], 150)
        self.assertIsNotNone(hank["balance_migrated_at"])


class TestBackfillNoUSDMigration(unittest.TestCase):
    """TEST 6 — NO USD MIGRATION: must not create financial migration objects."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = os.path.join(self._tmpdir, "test.db")
        self._conn = _make_conn(self._db)
        _create_legacy_users_table(self._conn)
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (20, 'Iris', 100)"
        )
        self._conn.commit()

    def tearDown(self):
        self._conn.close()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_no_usd_columns_created(self):
        backfill_balance_cents_from_points(self._conn)

        # Check no balance_usd_nano columns
        columns = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(users)")
        }
        self.assertNotIn("balance_usd_nano", columns)
        self.assertNotIn("balance_usd_nano_rate", columns)
        self.assertNotIn("balance_usd_nano_migrated_at", columns)

        # Check no migration_meta table
        tables = {
            r[0]
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertNotIn("migration_meta", tables)


if __name__ == "__main__":
    unittest.main()

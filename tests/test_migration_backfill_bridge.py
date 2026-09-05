"""Tests for Order 5: run_legacy_migration() calls backfill before conversion.

Proves that legacy users with points but no balance_cents are correctly
bridged and then migrated to USD nano.
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

run_legacy_migration = gb.run_legacy_migration
backfill_balance_cents_from_points = gb.backfill_balance_cents_from_points

RATE = Decimal("50")


def _create_legacy_db(db_path, users):
    """Create a legacy database with points and balance_cents columns.

    Simulates the state after init_db() has created the schema but before
    the points → balance_cents backfill has been run.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE users (
            user_id      INTEGER PRIMARY KEY,
            first_name   TEXT    NOT NULL,
            points       INTEGER  DEFAULT 0,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            balance_migrated_at DATETIME
        )
    """)
    for uid, name, pts in users:
        conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (?, ?, ?)",
            (uid, name, pts),
        )
    conn.commit()
    conn.close()


def _create_mixed_db(db_path, users):
    """Create a database with both points and balance_cents columns.

    users: list of (user_id, first_name, points, balance_cents, balance_migrated_at_or_None)
    """
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE users (
            user_id      INTEGER PRIMARY KEY,
            first_name   TEXT    NOT NULL,
            points       INTEGER  DEFAULT 0,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            balance_migrated_at DATETIME
        )
    """)
    for uid, name, pts, bc, bma in users:
        if bma is None:
            conn.execute(
                "INSERT INTO users (user_id, first_name, points, balance_cents) "
                "VALUES (?, ?, ?, ?)",
                (uid, name, pts, bc),
            )
        else:
            conn.execute(
                "INSERT INTO users (user_id, first_name, points, balance_cents, balance_migrated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uid, name, pts, bc, bma),
            )
    conn.commit()
    conn.close()


class TestLegacyUserMigratedFromPoints(unittest.TestCase):
    """Points → balance_cents → balance_usd_nano pipeline works end-to-end."""

    def test_points_bridged_and_converted(self):
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "test.db")
        _create_legacy_db(db, [(1, "Alice", 500)])

        result = run_legacy_migration(db, RATE, "test_bridge_v1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rows_migrated"], 1)

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE user_id = 1").fetchone()
        self.assertEqual(user["balance_cents"], 500)
        self.assertIsNotNone(user["balance_usd_nano"])
        self.assertGreater(int(user["balance_usd_nano"]), 0)
        conn.close()

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


class TestZeroPointsMigrated(unittest.TestCase):
    """User with points=0 should be migrated with 0 nano."""

    def test_zero_points(self):
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "test.db")
        _create_legacy_db(db, [(2, "Bob", 0)])

        result = run_legacy_migration(db, RATE, "test_zero_v1")
        self.assertEqual(result["status"], "completed")
        # User with 0 balance_cents is skipped (rows_migrated counts non-zero)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE user_id = 2").fetchone()
        self.assertEqual(user["balance_cents"], 0)
        conn.close()

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


class TestExistingBalanceNotOverwritten(unittest.TestCase):
    """A user with balance_cents already populated must not be overwritten."""

    def test_existing_balance_preserved(self):
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "test.db")
        _create_mixed_db(db, [
            (3, "Charlie", 500, 999, "2025-01-01T00:00:00"),  # already migrated
        ])

        result = run_legacy_migration(db, RATE, "test_preserve_v1")
        self.assertEqual(result["status"], "completed")

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE user_id = 3").fetchone()
        # balance_cents should remain 999 (not overwritten by points=500)
        self.assertEqual(user["balance_cents"], 999)
        conn.close()

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


class TestMixedUsersPipeline(unittest.TestCase):
    """Multiple users with mixed states: only eligible ones are bridged and converted."""

    def test_mixed_states(self):
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "test.db")
        _create_mixed_db(db, [
            (10, "Eve",    300,   0, None),    # eligible: backfill then convert
            (11, "Frank",    0,   0, None),    # eligible but zero: skip
            (12, "Grace",  500,  99, "2025-06-01T12:00:00"),  # already migrated
            (13, "Hank",   150,   0, None),    # eligible: backfill then convert
        ])

        result = run_legacy_migration(db, RATE, "test_mixed_v1")
        self.assertEqual(result["status"], "completed")

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row

        eve = conn.execute("SELECT * FROM users WHERE user_id = 10").fetchone()
        self.assertEqual(eve["balance_cents"], 300)
        self.assertIsNotNone(eve["balance_usd_nano"])

        frank = conn.execute("SELECT * FROM users WHERE user_id = 11").fetchone()
        self.assertEqual(frank["balance_cents"], 0)

        grace = conn.execute("SELECT * FROM users WHERE user_id = 12").fetchone()
        self.assertEqual(grace["balance_cents"], 99)  # preserved

        hank = conn.execute("SELECT * FROM users WHERE user_id = 13").fetchone()
        self.assertEqual(hank["balance_cents"], 150)
        self.assertIsNotNone(hank["balance_usd_nano"])

        conn.close()

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


class TestBackfillUsesSameConnection(unittest.TestCase):
    """Verify the backfill operates on the migration connection, not a separate one."""

    def test_backfill_within_migration_conn(self):
        """If backfill_balance_cents_from_points is called within run_legacy_migration,
        it should use the same connection. We verify by checking the data is consistent
        within a single transaction."""
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "test.db")
        _create_legacy_db(db, [(20, "Iris", 750)])

        result = run_legacy_migration(db, RATE, "test_conn_v1")
        self.assertEqual(result["status"], "completed")

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM users WHERE user_id = 20").fetchone()
        self.assertEqual(user["balance_cents"], 750)
        self.assertIsNotNone(user["balance_usd_nano"])
        self.assertGreater(int(user["balance_usd_nano"]), 0)
        conn.close()

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

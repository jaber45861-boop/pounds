"""Regression tests: init_db() must NOT create financial migration schema.

Verifies the micro-order 2 change:
- init_db() must not create users.balance_usd_nano
- init_db() must not create users.balance_usd_nano_rate
- init_db() must not create users.balance_usd_nano_migrated_at
- init_db() must not create migration_meta table
- debit_usd_nano remains (part of V2 withdrawal, not migration)
- Explicit migration path still creates all financial schema correctly
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

init_db = gb.init_db
_get_connection = gb.get_connection
_CreateMigrationMetadataTable = gb._create_migration_metadata_table
_AddBalanceUsdNanoColumn = gb._add_balance_usd_nano_column
_AddBalanceUsdNanoMigrationSnapshotColumns = gb._add_balance_usd_nano_migration_snapshot_columns
RunLegacyMigration = gb.run_legacy_migration
EGP_PER_USD = gb.EGP_PER_USD


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r[1] == column_name for r in rows)


class TestInitDbNoFinancialSchema(unittest.TestCase):
    """Verify init_db() does not create financial migration objects."""

    def setUp(self):
        # Use a temporary database with a minimal legacy schema
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        conn = sqlite3.connect(self._db_path)
        # Create ONLY the base users table — no financial migration columns
        conn.execute("""
            CREATE TABLE users (
                user_id      INTEGER PRIMARY KEY,
                first_name   TEXT    NOT NULL,
                last_name    TEXT,
                username     TEXT,
                joined_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                points       INTEGER  DEFAULT 0,
                referred_by  INTEGER,
                activation_status INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_init_db_does_not_create_balance_usd_nano(self):
        """TEST A: init_db() must NOT create balance_usd_nano column."""
        # Patch get_connection to use our test database
        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        original_get_connection = gb.get_connection
        gb.get_connection = _test_conn
        try:
            init_db()
            conn = sqlite3.connect(self._db_path)
            self.assertFalse(
                _column_exists(conn, "users", "balance_usd_nano"),
                "init_db() should NOT create users.balance_usd_nano"
            )
            conn.close()
        finally:
            gb.get_connection = original_get_connection

    def test_init_db_does_not_create_balance_usd_nano_rate(self):
        """TEST A: init_db() must NOT create balance_usd_nano_rate column."""
        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        original_get_connection = gb.get_connection
        gb.get_connection = _test_conn
        try:
            init_db()
            conn = sqlite3.connect(self._db_path)
            self.assertFalse(
                _column_exists(conn, "users", "balance_usd_nano_rate"),
                "init_db() should NOT create users.balance_usd_nano_rate"
            )
            conn.close()
        finally:
            gb.get_connection = original_get_connection

    def test_init_db_does_not_create_balance_usd_nano_migrated_at(self):
        """TEST A: init_db() must NOT create balance_usd_nano_migrated_at column."""
        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        original_get_connection = gb.get_connection
        gb.get_connection = _test_conn
        try:
            init_db()
            conn = sqlite3.connect(self._db_path)
            self.assertFalse(
                _column_exists(conn, "users", "balance_usd_nano_migrated_at"),
                "init_db() should NOT create users.balance_usd_nano_migrated_at"
            )
            conn.close()
        finally:
            gb.get_connection = original_get_connection

    def test_init_db_does_not_create_migration_meta(self):
        """TEST A: init_db() must NOT create migration_meta table."""
        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        original_get_connection = gb.get_connection
        gb.get_connection = _test_conn
        try:
            init_db()
            conn = sqlite3.connect(self._db_path)
            self.assertFalse(
                _table_exists(conn, "migration_meta"),
                "init_db() should NOT create migration_meta table"
            )
            conn.close()
        finally:
            gb.get_connection = original_get_connection

    def test_init_db_still_creates_base_tables(self):
        """TEST B: init_db() still creates base application tables."""
        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        original_get_connection = gb.get_connection
        gb.get_connection = _test_conn
        try:
            init_db()
            conn = sqlite3.connect(self._db_path)
            for table in ["users", "referrals", "task_completions",
                          "smm_orders", "service_price_settings",
                          "withdrawal_requests", "processed_transactions"]:
                self.assertTrue(
                    _table_exists(conn, table),
                    f"init_db() should create {table} table"
                )
            conn.close()
        finally:
            gb.get_connection = original_get_connection

    def test_init_db_still_creates_debit_usd_nano(self):
        """debit_usd_nano is V2 withdrawal schema, not migration — should remain."""
        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        original_get_connection = gb.get_connection
        gb.get_connection = _test_conn
        try:
            init_db()
            conn = sqlite3.connect(self._db_path)
            self.assertTrue(
                _column_exists(conn, "withdrawal_requests", "debit_usd_nano"),
                "init_db() should still create debit_usd_nano (V2 withdrawal)"
            )
            conn.close()
        finally:
            gb.get_connection = original_get_connection

    def test_init_db_does_not_backfill_balance_cents(self):
        """TEST: init_db() must NOT copy points into balance_cents."""
        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        # Create a legacy user with points=500 but balance_cents=0
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO users (user_id, first_name, points) VALUES (100, 'Legacy', 500)"
        )
        conn.commit()
        conn.close()

        original_get_connection = gb.get_connection
        gb.get_connection = _test_conn
        try:
            init_db()
            conn = sqlite3.connect(self._db_path)
            user = conn.execute(
                "SELECT points, balance_cents, balance_migrated_at "
                "FROM users WHERE user_id = 100"
            ).fetchone()
            # balance_cents must remain 0 (not copied from points)
            self.assertEqual(user[1], 0,
                "init_db() must NOT copy points into balance_cents")
            # balance_migrated_at must remain NULL
            self.assertIsNone(user[2],
                "init_db() must NOT set balance_migrated_at")
            conn.close()
        finally:
            gb.get_connection = original_get_connection


class TestExplicitMigrationStillWorks(unittest.TestCase):
    """TEST C: Verify explicit migration path creates financial schema."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "migration_test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_explicit_migration_creates_financial_columns(self):
        """run_legacy_migration() should create balance_usd_nano columns."""
        # Create minimal schema WITHOUT financial columns
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE users (
                user_id      INTEGER PRIMARY KEY,
                first_name   TEXT    NOT NULL,
                points       INTEGER  DEFAULT 0,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                balance_migrated_at DATETIME,
                is_verified  INTEGER  NOT NULL DEFAULT 0
            )
        """)
        conn.execute("INSERT INTO users (user_id, first_name, points, balance_cents) "
                      "VALUES (1, 'Test', 100, 100)")
        conn.commit()
        conn.close()

        rate = Decimal("50")
        result = RunLegacyMigration(self._db_path, rate, "test_regression_v1")

        self.assertEqual(result["status"], "completed",
                         f"Migration should complete, got: {result}")

        conn = sqlite3.connect(self._db_path)
        self.assertTrue(
            _column_exists(conn, "users", "balance_usd_nano"),
            "run_legacy_migration() must create balance_usd_nano"
        )
        self.assertTrue(
            _column_exists(conn, "users", "balance_usd_nano_rate"),
            "run_legacy_migration() must create balance_usd_nano_rate"
        )
        self.assertTrue(
            _column_exists(conn, "users", "balance_usd_nano_migrated_at"),
            "run_legacy_migration() must create balance_usd_nano_migrated_at"
        )
        self.assertTrue(
            _table_exists(conn, "migration_meta"),
            "run_legacy_migration() must create migration_meta"
        )
        conn.close()

    def test_explicit_migration_creates_migration_meta(self):
        """_create_migration_metadata_table still works standalone."""
        conn = sqlite3.connect(self._db_path)
        _CreateMigrationMetadataTable(conn)
        self.assertTrue(_table_exists(conn, "migration_meta"))
        conn.close()

    def test_explicit_migration_adds_balance_column_standalone(self):
        """_add_balance_usd_nano_column still works standalone."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL
            )
        """)
        conn.commit()
        added = _AddBalanceUsdNanoColumn(conn)
        self.assertTrue(added, "Should add balance_usd_nano column")
        self.assertTrue(_column_exists(conn, "users", "balance_usd_nano"))
        # Second call should return False (already exists)
        added2 = _AddBalanceUsdNanoColumn(conn)
        self.assertFalse(added2, "Second call should return False")
        conn.close()

    def test_explicit_migration_adds_snapshot_columns_standalone(self):
        """_add_balance_usd_nano_migration_snapshot_columns still works."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                balance_usd_nano INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        _AddBalanceUsdNanoMigrationSnapshotColumns(conn)
        self.assertTrue(_column_exists(conn, "users", "balance_usd_nano_rate"))
        self.assertTrue(_column_exists(conn, "users", "balance_usd_nano_migrated_at"))
        conn.close()


if __name__ == "__main__":
    unittest.main()

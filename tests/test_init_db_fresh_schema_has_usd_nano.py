"""
Order 14 — regression tests:
init_db() creates balance_usd_nano as part of the fresh users schema.

Verifies:
- A completely fresh SQLite DB runs init_db() successfully.
- users table contains balance_usd_nano column.
- balance_usd_nano has DEFAULT 0.
- A newly inserted user has balance_usd_nano = 0.
- init_db() remains idempotent (can be called multiple times).
- No financial backfill occurs during init_db().
"""
import os
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import sqlite3
import tempfile
import unittest
from decimal import Decimal

import importlib.util

_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)


def _fresh_import_and_init(db_path: str):
    """Import ganaihat_bot fresh, point it at db_path, run init_db()."""
    os.environ["BOT_DB_PATH"] = db_path
    spec = importlib.util.spec_from_file_location(
        "ganaihat_bot_order14", _BOT_FILE, submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.EGP_PER_USD = Decimal("50")
    spec.loader.exec_module(mod)
    mod.init_db()
    return mod


class TestFreshSchemaHasUsdNano(unittest.TestCase):
    """init_db() must create balance_usd_nano in the fresh users schema."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "fresh_test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_columns(self):
        with self._conn() as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(users)")}

    # --- Test 1: fresh DB runs init_db() successfully ---

    def test_fresh_db_runs_init_db(self):
        """A completely fresh SQLite DB can run init_db() without errors."""
        _fresh_import_and_init(self._db_path)
        conn = self._conn()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("users", tables)
        conn.close()

    # --- Test 2: users table contains balance_usd_nano ---

    def test_users_table_has_balance_usd_nano(self):
        """After init_db(), users table must contain balance_usd_nano column."""
        _fresh_import_and_init(self._db_path)
        cols = self._get_columns()
        self.assertIn("balance_usd_nano", cols,
                       "balance_usd_nano must exist in fresh users schema")

    # --- Test 3: balance_usd_nano has DEFAULT 0 ---

    def test_balance_usd_nano_has_default_zero(self):
        """balance_usd_nano must have DEFAULT 0 in the fresh schema."""
        _fresh_import_and_init(self._db_path)
        conn = self._conn()
        cols_info = {
            r["name"]: r for r in conn.execute("PRAGMA table_info(users)")
        }
        col = cols_info["balance_usd_nano"]
        self.assertEqual(col["dflt_value"], "0",
                         "balance_usd_nano must have DEFAULT 0")
        self.assertEqual(col["notnull"], 1,
                         "balance_usd_nano must be NOT NULL")
        conn.close()

    # --- Test 4: newly inserted user has balance_usd_nano = 0 ---

    def test_new_user_has_zero_balance_usd_nano(self):
        """A newly inserted user must have balance_usd_nano = 0."""
        _fresh_import_and_init(self._db_path)
        conn = self._conn()
        conn.execute(
            "INSERT INTO users (user_id, first_name) VALUES (1, 'Test')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["balance_usd_nano"], 0,
                         "New user must start with balance_usd_nano = 0")
        conn.close()

    # --- Test 5: init_db() is idempotent ---

    def test_init_db_idempotent(self):
        """Calling init_db() twice does not fail or alter the schema."""
        _fresh_import_and_init(self._db_path)
        cols_before = self._get_columns()

        # Import and call init_db() again
        _fresh_import_and_init(self._db_path)
        cols_after = self._get_columns()

        self.assertEqual(cols_before, cols_after,
                         "init_db() must be idempotent")

    # --- Test 6: no financial backfill during init_db() ---

    def test_no_financial_backfill_during_init_db(self):
        """init_db() must not modify balance_usd_nano of existing rows."""
        _fresh_import_and_init(self._db_path)
        conn = self._conn()
        # Insert a user with a specific balance_usd_nano value
        conn.execute(
            "INSERT INTO users (user_id, first_name, balance_usd_nano) "
            "VALUES (1, 'Existing', 42)"
        )
        conn.commit()

        # Run init_db() again
        _fresh_import_and_init(self._db_path)

        # Verify the value is unchanged
        row = conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 1"
        ).fetchone()
        self.assertEqual(row["balance_usd_nano"], 42,
                         "init_db() must not backfill existing balance_usd_nano")
        conn.close()

    # --- Test 7: balance_usd_nano is in the right position ---

    def test_balance_usd_nano_position_in_schema(self):
        """balance_usd_nano should be in the CREATE TABLE definition."""
        _fresh_import_and_init(self._db_path)
        conn = self._conn()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        # balance_usd_nano should come after activation_status
        idx_activation = cols.index("activation_status")
        idx_balance = cols.index("balance_usd_nano")
        self.assertEqual(idx_balance, idx_activation + 1,
                         "balance_usd_nano should immediately follow activation_status")
        conn.close()


if __name__ == "__main__":
    unittest.main()

"""
Order 11 — regression tests:
init_db() no longer backfills ad_reviews.reward_cents.

Verifies:
- init_db() no longer changes ad_reviews.reward_cents for any existing row.
- reward_cents = 0 remains 0 after init_db().
- reward_cents IS NULL remains NULL after init_db().
- An existing positive reward_cents value is preserved.
- The approval path still reads the stored reward_cents value.
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
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot", _BOT_FILE, submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = Decimal("50")
_spec.loader.exec_module(_mod)

import sys
sys.modules["ganaihat_bot"] = _mod
gb = _mod

import reward_api as _reward_api
_reward_api._live_egp_per_usd = Decimal("50")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> tuple[str, sqlite3.Connection]:
    """Create a fresh DB via init_db() and return (path, conn)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["BOT_DB_PATH"] = path
    bot = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("ganaihat_bot", _BOT_FILE,
                                                submodule_search_locations=[])
    )
    bot.EGP_PER_USD = Decimal("50")
    importlib.util.spec_from_file_location(
        "ganaihat_bot", _BOT_FILE, submodule_search_locations=[]
    ).loader.exec_module(bot)
    bot.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return path, conn


def _fresh_db() -> tuple[str, sqlite3.Connection]:
    """Create a fresh test DB with full base schema, then patch get_connection."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["BOT_DB_PATH"] = path

    # Import fresh module to avoid stale DB_PATH
    spec = importlib.util.spec_from_file_location(
        "ganaihat_bot_fresh", _BOT_FILE, submodule_search_locations=[]
    )
    mod = importlib.util.module_from_spec(spec)
    mod.EGP_PER_USD = Decimal("50")
    spec.loader.exec_module(mod)
    mod.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return path, conn, mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInitDbNoAdRewardBackfill(unittest.TestCase):
    """init_db() must NOT backfill ad_reviews.reward_cents."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test_ad_review.db")
        # Create the schema via init_db on a patched get_connection
        fd = os.open(self._db_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        self._original_get_connection = gb.get_connection

        def _test_conn():
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            return c

        gb.get_connection = _test_conn
        gb.init_db()
        gb.get_connection = self._original_get_connection

    def tearDown(self):
        gb.get_connection = self._original_get_connection
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _conn(self):
        c = sqlite3.connect(self._db_path)
        c.row_factory = sqlite3.Row
        return c

    def _get_user_id(self, conn):
        """Insert a minimal user and return user_id."""
        conn.execute(
            "INSERT INTO users (user_id, first_name, username, points, balance_cents) "
            "VALUES (1, 'Tester', 'tester', 0, 0)"
        )
        conn.commit()
        return 1

    # --- Test 1: init_db() no longer changes ad_reviews.reward_cents ---

    def test_init_db_no_longer_changes_ad_review_reward_cents(self):
        """init_db() must NOT UPDATE ad_reviews.reward_cents at all."""
        conn = self._conn()
        user_id = self._get_user_id(conn)

        # Insert an ad review with reward_cents = 42
        conn.execute(
            "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status) "
            "VALUES (?, 'file_A', 42, 'approved')",
            (user_id,),
        )
        conn.commit()

        # Verify pre-condition
        row = conn.execute(
            "SELECT reward_cents FROM ad_reviews WHERE file_id = 'file_A'"
        ).fetchone()
        self.assertEqual(row["reward_cents"], 42)

        # Run init_db() on the same database
        gb.get_connection = lambda: sqlite3.connect(self._db_path) if not hasattr(self, '_rc') else self._rc()
        try:
            self._rc = lambda: self._conn()
            gb.get_connection = self._rc
            gb.init_db()
        finally:
            gb.get_connection = self._original_get_connection

        # Verify reward_cents is unchanged
        row = conn.execute(
            "SELECT reward_cents FROM ad_reviews WHERE file_id = 'file_A'"
        ).fetchone()
        self.assertEqual(row["reward_cents"], 42,
                         "init_db() must not change ad_reviews.reward_cents")

    # --- Test 2: reward_cents = 0 remains 0 ---

    def test_reward_cents_zero_remains_zero(self):
        """An ad review with reward_cents = 0 must stay 0 after init_db()."""
        conn = self._conn()
        user_id = self._get_user_id(conn)

        # Manually insert with reward_cents = 0 (override DEFAULT 50)
        conn.execute(
            "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status) "
            "VALUES (?, 'file_zero', 0, 'approved')",
            (user_id,),
        )
        conn.commit()

        row = conn.execute(
            "SELECT reward_cents FROM ad_reviews WHERE file_id = 'file_zero'"
        ).fetchone()
        self.assertEqual(row["reward_cents"], 0)

        # Run init_db()
        try:
            self._rc = lambda: self._conn()
            gb.get_connection = self._rc
            gb.init_db()
        finally:
            gb.get_connection = self._original_get_connection

        row = conn.execute(
            "SELECT reward_cents FROM ad_reviews WHERE file_id = 'file_zero'"
        ).fetchone()
        self.assertEqual(row["reward_cents"], 0,
                         "reward_cents = 0 must NOT be changed by init_db()")

    # --- Test 3: reward_cents column is NOT NULL ---

    def test_reward_cents_column_is_not_null(self):
        """The ad_reviews.reward_cents column is NOT NULL DEFAULT 50,
        so NULL values are structurally impossible. This test verifies
        the column constraint exists, confirming the old startup UPDATE's
        NULL branch is no longer reachable."""
        conn = self._conn()
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(ad_reviews)")}
        self.assertIn("reward_cents", cols)
        self.assertEqual(cols["reward_cents"]["notnull"], 1,
                         "reward_cents must be NOT NULL")
        self.assertEqual(cols["reward_cents"]["dflt_value"], "50",
                         "reward_cents must have DEFAULT 50")

        # Verify inserting NULL is rejected by SQLite
        user_id = self._get_user_id(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status) "
                "VALUES (?, 'file_null_test', NULL, 'approved')",
                (user_id,),
            )
        conn.rollback()

    # --- Test 4: positive reward_cents remains unchanged ---

    def test_positive_reward_cents_unchanged(self):
        """An ad review with a positive reward_cents must remain unchanged."""
        conn = self._conn()
        user_id = self._get_user_id(conn)

        conn.execute(
            "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status) "
            "VALUES (?, 'file_pos', 75, 'approved')",
            (user_id,),
        )
        conn.commit()

        row = conn.execute(
            "SELECT reward_cents FROM ad_reviews WHERE file_id = 'file_pos'"
        ).fetchone()
        self.assertEqual(row["reward_cents"], 75)

        # Run init_db()
        try:
            self._rc = lambda: self._conn()
            gb.get_connection = self._rc
            gb.init_db()
        finally:
            gb.get_connection = self._original_get_connection

        row = conn.execute(
            "SELECT reward_cents FROM ad_reviews WHERE file_id = 'file_pos'"
        ).fetchone()
        self.assertEqual(row["reward_cents"], 75,
                         "Positive reward_cents must not be changed by init_db()")

    # --- Test 5: init_db() does not silently change payable reward ---

    def test_payable_reward_not_silently_changed(self):
        """init_db() must not silently change the payable reward of any
        existing ad review, regardless of its current reward_cents value."""
        conn = self._conn()
        user_id = self._get_user_id(conn)

        # Insert multiple reviews with different reward_cents values
        test_values = [0, 1, 42, 50, 100, 999]
        for i, val in enumerate(test_values):
            conn.execute(
                "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status) "
                "VALUES (?, ?, ?, 'approved')",
                (user_id, f"file_{i}", val),
            )
        conn.commit()

        # Snapshot all reward_cents before init_db
        before = {
            r["file_id"]: r["reward_cents"]
            for r in conn.execute(
                "SELECT file_id, reward_cents FROM ad_reviews ORDER BY id"
            ).fetchall()
        }

        # Run init_db()
        try:
            self._rc = lambda: self._conn()
            gb.get_connection = self._rc
            gb.init_db()
        finally:
            gb.get_connection = self._original_get_connection

        # Verify every reward_cents is unchanged
        after = {
            r["file_id"]: r["reward_cents"]
            for r in conn.execute(
                "SELECT file_id, reward_cents FROM ad_reviews ORDER BY id"
            ).fetchall()
        }

        for file_id in before:
            self.assertEqual(
                before[file_id], after[file_id],
                f"reward_cents for {file_id} changed from "
                f"{before[file_id]} to {after[file_id]} — "
                f"init_db() must not modify ad_reviews.reward_cents",
            )

    # --- Test 6: approval path still reads stored reward_cents ---

    def test_approval_path_reads_stored_reward_cents(self):
        """approve_ad_review() must still credit using the stored reward_cents
        value, not AD_REWARD or any other constant."""
        conn = self._conn()
        user_id = self._get_user_id(conn)

        # Add balance_usd_nano column if missing (not created by init_db per Order 2)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "balance_usd_nano" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN balance_usd_nano INTEGER NOT NULL DEFAULT 0"
            )
            conn.commit()

        # Set up user with zero balance
        conn.execute(
            "UPDATE users SET balance_usd_nano = 0 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()

        # Insert ad review with a custom reward_cents (not AD_REWARD=50)
        custom_reward = 75
        cursor = conn.execute(
            "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status) "
            "VALUES (?, 'file_custom', ?, 'pending')",
            (user_id, custom_reward),
        )
        review_id = cursor.lastrowid
        conn.commit()

        # Verify the stored reward_cents
        review = conn.execute(
            "SELECT reward_cents FROM ad_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        self.assertEqual(review["reward_cents"], custom_reward)

        # Approve via the actual approve_ad_review function
        _reward_api._live_egp_per_usd = Decimal("50")
        gb.get_connection = lambda: self._conn()
        try:
            result = gb.approve_ad_review(review_id)
        finally:
            gb.get_connection = self._original_get_connection

        self.assertIsNotNone(result, "Approval should succeed")
        self.assertEqual(result["reward_points"], custom_reward,
                         "Approval must return the stored reward_cents value")

        # Verify the wallet was credited with the stored reward_cents
        user = conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        # The credit should be egp_cents_to_wallet_nano(custom_reward)
        expected_nano = gb.egp_cents_to_wallet_nano(custom_reward)
        self.assertEqual(
            user["balance_usd_nano"], expected_nano,
            f"Wallet should be credited with egp_cents_to_wallet_nano({custom_reward}) "
            f"= {expected_nano}, got {user['balance_usd_nano']}",
        )


if __name__ == "__main__":
    unittest.main()

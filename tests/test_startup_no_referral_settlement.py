"""Tests for Order 8: init_db() must not release referral rewards or credit wallets.

Proves that startup no longer performs the referral reward settlement
that previously mutated user balances.
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
REFERRAL_REWARD_USD_NANO = gb.REFERRAL_REWARD_USD_NANO


def _make_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class TestInitDbNoReferralSettlement(unittest.TestCase):
    """init_db() must NOT release pending referral rewards."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db = os.path.join(self._tmpdir, "test.db")
        self._conn = _make_conn(self._db)

        # Create minimal schema matching what init_db() creates
        self._conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                points INTEGER DEFAULT 0,
                referred_by INTEGER,
                activation_status INTEGER NOT NULL DEFAULT 0,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                balance_migrated_at DATETIME,
                is_verified INTEGER NOT NULL DEFAULT 0,
                balance_usd_nano INTEGER NOT NULL DEFAULT 0,
                withdrawal_blocked INTEGER NOT NULL DEFAULT 0,
                fraud_reason TEXT,
                fraud_marked_at DATETIME
            );
            CREATE TABLE referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL REFERENCES users(user_id),
                referred_id INTEGER NOT NULL REFERENCES users(user_id),
                reward_status TEXT NOT NULL DEFAULT 'pending',
                reward_points INTEGER NOT NULL DEFAULT 10,
                rewarded_at DATETIME,
                eligible_at DATETIME,
                last_checked_at DATETIME
            );
        """)
        # Referrer (user 1) with 0 balance
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, activation_status, balance_usd_nano) "
            "VALUES (1, 'Referrer', 1, 0)"
        )
        # Referred user (user 2) who is activated
        self._conn.execute(
            "INSERT INTO users (user_id, first_name, activation_status, balance_usd_nano, referred_by) "
            "VALUES (2, 'Referred', 1, 0, 1)"
        )
        # Pending referral
        self._conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, reward_points) "
            "VALUES (1, 2, 'pending', ?)",
            (REFERRAL_REWARD_USD_NANO,),
        )
        self._conn.commit()
        self._conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _get_balance(self, user_id):
        conn = _make_conn(self._db)
        row = conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        return row["balance_usd_nano"] if row else 0

    def _get_referral_status(self, referral_id):
        conn = _make_conn(self._db)
        row = conn.execute(
            "SELECT reward_status FROM referrals WHERE id = ?", (referral_id,)
        ).fetchone()
        conn.close()
        return row["reward_status"] if row else None

    def test_init_db_does_not_credit_balance(self):
        """TEST 1+2: init_db() must NOT increase balance_usd_nano."""
        original_get_connection = gb.get_connection
        gb.get_connection = lambda: _make_conn(self._db)
        try:
            balance_before = self._get_balance(1)
            init_db()
            balance_after = self._get_balance(1)
            self.assertEqual(balance_before, balance_after,
                "init_db() must NOT change balance_usd_nano")
            self.assertEqual(balance_after, 0,
                "Referrer balance must remain 0 after init_db()")
        finally:
            gb.get_connection = original_get_connection

    def test_pending_referral_stays_pending(self):
        """TEST 3: Pending referral must remain pending after init_db()."""
        original_get_connection = gb.get_connection
        gb.get_connection = lambda: _make_conn(self._db)
        try:
            status_before = self._get_referral_status(1)
            self.assertEqual(status_before, "pending")
            init_db()
            status_after = self._get_referral_status(1)
            self.assertEqual(status_after, "pending",
                "Pending referral must NOT be released by init_db()")
        finally:
            gb.get_connection = original_get_connection

    def test_referrer_balance_unchanged_after_startup(self):
        """TEST 2: Referrer balance is 0 before and after init_db()."""
        original_get_connection = gb.get_connection
        gb.get_connection = lambda: _make_conn(self._db)
        try:
            self.assertEqual(self._get_balance(1), 0)
            init_db()
            self.assertEqual(self._get_balance(1), 0,
                "Referrer balance must remain 0 — no startup settlement")
        finally:
            gb.get_connection = original_get_connection


class TestActivationStillReleasesReward(unittest.TestCase):
    """TEST 4+5: The explicit activation flow still releases referral rewards."""

    def test_activate_user_releases_referral(self):
        """When a referred user activates, the referrer gets credited."""
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "test.db")
        conn = _make_conn(db)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                points INTEGER DEFAULT 0,
                referred_by INTEGER,
                activation_status INTEGER NOT NULL DEFAULT 0,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                balance_migrated_at DATETIME,
                is_verified INTEGER NOT NULL DEFAULT 0,
                balance_usd_nano INTEGER NOT NULL DEFAULT 0,
                withdrawal_blocked INTEGER NOT NULL DEFAULT 0,
                fraud_reason TEXT,
                fraud_marked_at DATETIME
            );
            CREATE TABLE referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL REFERENCES users(user_id),
                referred_id INTEGER NOT NULL REFERENCES users(user_id),
                reward_status TEXT NOT NULL DEFAULT 'pending',
                reward_points INTEGER NOT NULL DEFAULT 10,
                rewarded_at DATETIME,
                eligible_at DATETIME,
                last_checked_at DATETIME
            );
            CREATE TABLE task_completions (
                user_id INTEGER NOT NULL,
                task_key TEXT NOT NULL,
                done_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, task_key)
            );
        """)
        # Referrer
        conn.execute(
            "INSERT INTO users (user_id, first_name, activation_status, balance_usd_nano) "
            "VALUES (1, 'Referrer', 1, 0)"
        )
        # Referred user — NOT yet activated
        conn.execute(
            "INSERT INTO users (user_id, first_name, activation_status, balance_usd_nano, referred_by) "
            "VALUES (2, 'Referred', 0, 0, 1)"
        )
        # Pending referral
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, reward_points) "
            "VALUES (1, 2, 'pending', ?)",
            (REFERRAL_REWARD_USD_NANO,),
        )
        conn.commit()
        conn.close()

        # Verify referrer starts at 0
        conn = _make_conn(db)
        referrer = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id = 1").fetchone()
        self.assertEqual(referrer["balance_usd_nano"], 0)
        conn.close()

        # Simulate activation: release_referral_reward is called inside activate_user
        # We call it directly to prove the mechanism works
        conn = _make_conn(db)
        gb.release_referral_reward(conn, 2)  # referred_id=2
        conn.commit()

        # Verify referrer was credited
        conn = _make_conn(db)
        referrer = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id = 1").fetchone()
        self.assertEqual(referrer["balance_usd_nano"], REFERRAL_REWARD_USD_NANO,
            "Referrer should be credited after referral reward release")

        # Verify referral status changed
        referral = conn.execute("SELECT reward_status FROM referrals WHERE id = 1").fetchone()
        self.assertEqual(referral["reward_status"], "rewarded")
        conn.close()

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


class TestRewardNotDuplicated(unittest.TestCase):
    """TEST 6: Calling release_referral_reward twice does not double-credit."""

    def test_idempotent_release(self):
        tmpdir = tempfile.mkdtemp()
        db = os.path.join(tmpdir, "test.db")
        conn = _make_conn(db)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                activation_status INTEGER NOT NULL DEFAULT 0,
                balance_usd_nano INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL REFERENCES users(user_id),
                referred_id INTEGER NOT NULL REFERENCES users(user_id),
                reward_status TEXT NOT NULL DEFAULT 'pending',
                reward_points INTEGER NOT NULL DEFAULT 10,
                rewarded_at DATETIME,
                last_checked_at DATETIME
            );
        """)
        conn.execute(
            "INSERT INTO users (user_id, first_name, activation_status, balance_usd_nano) "
            "VALUES (1, 'Referrer', 1, 0)"
        )
        conn.execute(
            "INSERT INTO users (user_id, first_name, activation_status, balance_usd_nano) "
            "VALUES (2, 'Referred', 1, 0)"
        )
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, reward_points) "
            "VALUES (1, 2, 'pending', ?)",
            (REFERRAL_REWARD_USD_NANO,),
        )
        conn.commit()

        # First release
        gb.release_referral_reward(conn, 2)
        conn.commit()
        balance1 = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id = 1").fetchone()["balance_usd_nano"]

        # Second release — should be idempotent
        gb.release_referral_reward(conn, 2)
        conn.commit()
        balance2 = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id = 1").fetchone()["balance_usd_nano"]

        self.assertEqual(balance1, REFERRAL_REWARD_USD_NANO)
        self.assertEqual(balance2, REFERRAL_REWARD_USD_NANO,
            "Second release must not double-credit")
        conn.close()

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

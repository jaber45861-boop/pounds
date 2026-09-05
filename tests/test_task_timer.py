"""Tests for admin-internal task expiration timer.

All test classes share one DB (module-level DB_PATH).
"""
import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import importlib.util

_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot", _BOT_FILE, submodule_search_locations=[]
)
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod
gb = _mod

_fd, _TEST_DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
gb.DB_PATH = _TEST_DB
gb.init_db()

# Initialize FX rate required by egp_cents_to_wallet_nano (used by create_referral_task)
import reward_api as _ra
_ra._live_egp_per_usd = Decimal("50")


def _add_user(uid):
    with gb.get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, first_name, balance_usd_nano, activation_status) "
            "VALUES (?, 'Test', 100000000, 1)", (uid,),
        )
        conn.commit()


def _cleanup_task(table, task_id):
    try:
        with gb.get_connection() as conn:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (task_id,))
            conn.commit()
    except Exception:
        pass


class TestSchema(unittest.TestCase):
    def test_referral_tasks_has_expires_at(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(referral_tasks)").fetchall()}
        self.assertIn("expires_at", cols)

    def test_referral_tasks_has_task_state(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(referral_tasks)").fetchall()}
        self.assertIn("task_state", cols)

    def test_manual_tasks_has_expires_at(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("expires_at", cols)

    def test_manual_tasks_has_task_state(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("task_state", cols)


class TestTaskCreation(unittest.TestCase):
    def test_referral_task_sets_expires_at_and_state(self):
        uid = 20001
        _add_user(uid)
        with gb.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO service_price_settings (service_key, price_points, price_cents) VALUES ('referral_boost', 100, 100)")
            conn.execute("UPDATE users SET balance_usd_nano = 50000000 WHERE user_id = ?", (uid,))
            conn.commit()
        tid = gb.create_referral_task(uid, "https://t.me/TestBot?start=abc")
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute("SELECT expires_at, task_state FROM referral_tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        self.assertIsNotNone(row["expires_at"])
        _cleanup_task("referral_tasks", tid)

    def test_manual_task_sets_expires_at_and_state(self):
        tid = gb.create_manual_task(title="Test task", task_link="https://example.com", reward_points=5000, quantity=5)
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute("SELECT expires_at, task_state FROM manual_tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        self.assertIsNotNone(row["expires_at"])
        _cleanup_task("manual_tasks", tid)


class TestExpiryFiltering(unittest.TestCase):
    def setUp(self):
        _add_user(30001)
        _add_user(30999)

    def _insert(self, tid, state="AVAILABLE", expires_at=None, qty=5, buyer=30001):
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, ?, 'https://t.me/X?start=1', ?, ?, 100, 100, 'active', ?, ?)",
                (tid, buyer, qty, qty, state, expires_at),
            )
            conn.commit()

    def test_expired_task_hidden(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(3001, state="AVAILABLE", expires_at=past)
        tasks = gb.get_active_referral_tasks(30999)
        self.assertNotIn(3001, [t["id"] for t in tasks])

    def test_deleted_task_hidden(self):
        self._insert(3002, state="DELETED")
        tasks = gb.get_active_referral_tasks(30999)
        self.assertNotIn(3002, [t["id"] for t in tasks])

    def test_available_task_visible(self):
        future = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(3003, state="AVAILABLE", expires_at=future)
        tasks = gb.get_active_referral_tasks(30999)
        self.assertIn(3003, [t["id"] for t in tasks])

    def test_legacy_task_without_expires_at_visible(self):
        _add_user(30998)
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status) VALUES (?, 30998, 'https://t.me/Y?start=2', 5, 5, 100, 100, 'active')", (3004,))
            conn.commit()
        tasks = gb.get_active_referral_tasks(30999)
        self.assertIn(3004, [t["id"] for t in tasks])
        _cleanup_task("referral_tasks", 3004)

    def test_manual_task_expired_hidden(self):
        tid = gb.create_manual_task("Expired manual", "https://x.com", 1000, 1)
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute("UPDATE manual_tasks SET expires_at = ? WHERE id = ?", (past, tid))
            conn.commit()
        tasks = gb.get_active_manual_tasks()
        self.assertNotIn(tid, [t["id"] for t in tasks])
        _cleanup_task("manual_tasks", tid)


class TestClaimGuard(unittest.TestCase):
    def setUp(self):
        _add_user(40001)
        _add_user(40002)

    def _insert(self, tid, state="AVAILABLE", expires_at=None, buyer=40002, qty=5):
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, ?, 'https://t.me/Z?start=3', ?, ?, 100, 100, 'active', ?, ?)",
                (tid, buyer, qty, qty, state, expires_at),
            )
            conn.commit()

    def test_claim_expired_returns_unavailable(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(4001, state="AVAILABLE", expires_at=past)
        self.assertEqual(gb.claim_referral_task(4001, 40001), "unavailable")

    def test_claim_deleted_returns_unavailable(self):
        self._insert(4002, state="DELETED")
        self.assertEqual(gb.claim_referral_task(4002, 40001), "unavailable")

    def test_claim_available_succeeds(self):
        future = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(4003, state="AVAILABLE", expires_at=future)
        self.assertEqual(gb.claim_referral_task(4003, 40001), "pending_client")

    def test_existing_submissions_preserved_after_expiry(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM referral_task_claims WHERE task_id = 4004")
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, 40002, 'https://t.me/U?start=6', 5, 0, 100, 100, 'completed', 'EXPIRED', ?)", (4004, past))
            conn.execute("INSERT INTO referral_task_claims (task_id, worker_id, buyer_id, status) VALUES (?, 40001, 40002, 'pending_client')", (4004,))
            conn.commit()
        claim = gb.get_referral_task_claim_for_worker(4004, 40001)
        self.assertIsNotNone(claim)
        self.assertEqual(claim["status"], "pending_client")


class TestAdminFunctions(unittest.TestCase):
    def setUp(self):
        _add_user(50001)

    def test_extend_task_timer(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, 50001, 'https://t.me/A?start=7', 5, 5, 100, 100, 'active', 'EXPIRED', ?)", (5001, past))
            conn.commit()
        ok = gb.extend_task_timer(5001, "referral_tasks", extra_hours=24)
        self.assertTrue(ok)
        with gb.get_connection() as conn:
            row = conn.execute("SELECT task_state FROM referral_tasks WHERE id = 5001").fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        _cleanup_task("referral_tasks", 5001)

    def test_delete_task_admin(self):
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO manual_tasks (id, title, task_link, reward_points, quantity_requested, quantity_remaining, status, task_state) VALUES (?, 'Del task', 'https://x.com', 1000, 10, 10, 'active', 'AVAILABLE')", (5002,))
            conn.commit()
        self.assertTrue(gb.delete_task_admin(5002, "manual_tasks"))
        with gb.get_connection() as conn:
            row = conn.execute("SELECT task_state FROM manual_tasks WHERE id = 5002").fetchone()
        self.assertEqual(row["task_state"], "DELETED")
        _cleanup_task("manual_tasks", 5002)

    def test_reactivate_task_admin(self):
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state) VALUES (?, 50001, 'https://t.me/B?start=8', 5, 5, 100, 100, 'active', 'EXPIRED')", (5003,))
            conn.commit()
        self.assertTrue(gb.reactivate_task_admin(5003, "referral_tasks"))
        with gb.get_connection() as conn:
            row = conn.execute("SELECT task_state FROM referral_tasks WHERE id = 5003").fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        _cleanup_task("referral_tasks", 5003)

    def test_get_all_tasks_for_admin(self):
        tasks = gb.get_all_tasks_for_admin(limit=50)
        self.assertIsInstance(tasks, list)
        for t in tasks:
            self.assertIn("task_source", t)

    def test_admin_timer_summary_no_expires(self):
        row = {"id": 1, "task_state": "AVAILABLE", "expires_at": None, "quantity_remaining": 3}
        summary = gb._admin_task_timer_summary(row, "referral_tasks")
        self.assertIn("AVAILABLE", summary)
        self.assertIn("Remaining: 3", summary)


class TestTimerConstant(unittest.TestCase):
    def test_task_expiry_hours_exists(self):
        self.assertEqual(gb.TASK_EXPIRY_HOURS, 24)

    def test_no_user_facing_timer_leak(self):
        import inspect
        src = inspect.getsource(gb.build_tasks_text)
        for word in ["expires_at", "deadline", "countdown", "timer"]:
            self.assertNotIn(word.lower(), src.lower(), f"build_tasks_text leaks '{word}'")


class TestBackwardCompatibility(unittest.TestCase):
    def test_legacy_referral_task_without_timer_is_available(self):
        _add_user(60001)
        _add_user(60002)
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status) VALUES (?, 60001, 'https://t.me/Legacy?start=9', 5, 3, 100, 100, 'active')", (6001,))
            conn.commit()
        tasks = gb.get_active_referral_tasks(60002)
        self.assertIn(6001, [t["id"] for t in tasks])
        _cleanup_task("referral_tasks", 6001)


if __name__ == "__main__":
    unittest.main()

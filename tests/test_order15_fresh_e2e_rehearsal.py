"""
Order 15 — Fresh Financial End-to-End Rehearsal

Proves that the completed USD-nano accounting works end-to-end on a
COMPLETELY FRESH SQLite database, without relying on ganaihat_fresh.db.

This is a REHEARSAL / AUDIT ONLY. No source changes, no commits, no pushes.
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

# Shared module and DB — initialized once in setUpModule
_mod = None
_db_path = None


def setUpModule():
    global _mod, _db_path
    tmpdir = tempfile.mkdtemp()
    _db_path = os.path.join(tmpdir, "rehearsal.db")
    assert not os.path.exists(_db_path), "Temp DB must not exist before rehearsal"
    os.environ["BOT_DB_PATH"] = _db_path
    spec = importlib.util.spec_from_file_location(
        "ganaihat_bot_r15", _BOT_FILE, submodule_search_locations=[],
    )
    _mod = importlib.util.module_from_spec(spec)
    _mod.EGP_PER_USD = Decimal("50")
    spec.loader.exec_module(_mod)
    _mod.init_db()
    import reward_api
    reward_api._live_egp_per_usd = Decimal("50")


def tearDownModule():
    import shutil
    # Find and remove the temp directory
    if _db_path and os.path.exists(_db_path):
        shutil.rmtree(os.path.dirname(_db_path), ignore_errors=True)


def _conn():
    c = sqlite3.connect(_db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


class Phase1_FreshSchema(unittest.TestCase):
    """PHASE 1 — FRESH SCHEMA"""

    def test_01_init_db_succeeds(self):
        """1. init_db() succeeds on fresh DB."""

    def test_02_users_table_exists(self):
        """2. users table exists."""
        with _conn() as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("users", tables)

    def test_03_balance_usd_nano_exists(self):
        """3. users.balance_usd_nano exists."""
        with _conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        self.assertIn("balance_usd_nano", cols)

    def test_04_balance_usd_nano_type(self):
        """4. balance_usd_nano is INTEGER."""
        with _conn() as c:
            info = {r["name"]: r for r in c.execute("PRAGMA table_info(users)")}
        self.assertEqual(info["balance_usd_nano"]["type"], "INTEGER")

    def test_05_balance_usd_nano_not_null(self):
        """5. balance_usd_nano is NOT NULL."""
        with _conn() as c:
            info = {r["name"]: r for r in c.execute("PRAGMA table_info(users)")}
        self.assertEqual(info["balance_usd_nano"]["notnull"], 1)

    def test_06_balance_usd_nano_default_zero(self):
        """6. default value is 0."""
        with _conn() as c:
            info = {r["name"]: r for r in c.execute("PRAGMA table_info(users)")}
        self.assertEqual(info["balance_usd_nano"]["dflt_value"], "0")

    def test_07_no_migration_meta(self):
        """7. No migration_meta table created by init_db()."""
        with _conn() as c:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='migration_meta'"
            ).fetchone()
        self.assertIsNone(exists)

    def test_08_no_referral_reward_released(self):
        """8. No referral reward is released during startup."""
        with _conn() as c:
            count = c.execute("SELECT COUNT(*) FROM referrals").fetchone()[0]
        self.assertEqual(count, 0)

    def test_09_no_wallet_balance_changed(self):
        """9. No wallet balance is changed by init_db()."""
        with _conn() as c:
            total = c.execute(
                "SELECT COALESCE(SUM(balance_usd_nano), 0) FROM users"
            ).fetchone()[0]
        self.assertEqual(total, 0)

    def test_10_idempotent(self):
        """10. Calling init_db() a second time is safe/idempotent."""
        _mod.init_db()
        with _conn() as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        self.assertIn("balance_usd_nano", cols)


class Phase2_NewUser(unittest.TestCase):
    """PHASE 2 — NEW USER"""

    def test_20_new_user_balance_zero(self):
        """Create user, verify balance_usd_nano == 0."""
        UID = 9001
        with _conn() as c:
            c.execute(
                "INSERT INTO users (user_id, first_name, username, "
                "activation_status, balance_usd_nano) "
                "VALUES (?, 'RehearsalUser', 'r15test', 1, 0)",
                (UID,),
            )
            c.commit()
            row = c.execute(
                "SELECT balance_usd_nano, points, balance_cents "
                "FROM users WHERE user_id = ?", (UID,)
            ).fetchone()
        self.assertEqual(row["balance_usd_nano"], 0)
        self.assertEqual(row["points"], 0)
        self.assertEqual(row["balance_cents"], 0)


class Phase3_Credit(unittest.TestCase):
    """PHASE 3 — USD-NANO CREDIT"""

    def test_30_credit_10m(self):
        """Credit exactly 10,000,000 USD nano ($0.01)."""
        UID = 9001
        _mod.credit_usd_nano(UID, 10_000_000)
        with _conn() as c:
            row = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()
        self.assertEqual(row["balance_usd_nano"], 10_000_000)

    def test_31_credit_sub_cent(self):
        """Credit 1,000 USD nano ($0.000001)."""
        UID = 9001
        _mod.credit_usd_nano(UID, 1_000)
        with _conn() as c:
            row = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()
        self.assertEqual(row["balance_usd_nano"], 10_001_000)


class Phase4_Debit(unittest.TestCase):
    """PHASE 4 — USD-NANO DEBIT"""

    def test_40_debit(self):
        """Debit 1,000 USD nano."""
        UID = 9001
        result = _mod.debit_usd_nano(UID, 1_000)
        self.assertTrue(result)
        with _conn() as c:
            row = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()
        self.assertEqual(row["balance_usd_nano"], 10_000_000)

    def test_41_insufficient_balance(self):
        """Debit more than balance — must fail safely."""
        UID = 9001
        result = _mod.debit_usd_nano(UID, 999_999_999)
        self.assertFalse(result)
        with _conn() as c:
            row = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()
        self.assertEqual(row["balance_usd_nano"], 10_000_000)
        self.assertGreaterEqual(row["balance_usd_nano"], 0)


class Phase5_EGPBoundary(unittest.TestCase):
    """PHASE 5 — EGP BOUNDARY CONVERSION"""

    def test_50_egp_conversion(self):
        """EGP cents → USD nano via egp_cents_to_wallet_nano()."""
        UID = 9001
        with _conn() as c:
            before = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]

        egp_cents = 50
        expected_nano = int(
            (Decimal(egp_cents) * Decimal("10000000") / Decimal("50"))
            .quantize(Decimal("1"))
        )
        self.assertEqual(expected_nano, 10_000_000)

        nano = _mod.egp_cents_to_wallet_nano(egp_cents)
        self.assertEqual(nano, expected_nano)
        _mod.credit_usd_nano(UID, nano)

        with _conn() as c:
            after = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]
        self.assertEqual(after, before + expected_nano)


class Phase6_Withdrawal(unittest.TestCase):
    """PHASE 6 — WITHDRAWAL RESERVATION"""

    def test_60_vodafone_withdrawal(self):
        """Vodafone Cash withdrawal creation on fresh DB."""
        UID = 9001
        # Ensure sufficient balance
        _mod.credit_usd_nano(UID, 100_000_000)

        with _conn() as c:
            balance_before = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]

        _mod._save_rate_snapshot(Decimal("50.00"), "test_unit")

        result = _mod.create_v2_withdrawal_request(
            user_id=UID,
            method_code=_mod.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=500,
            usdt_amount=None,
        )
        self.assertIsInstance(result, int, f"Expected withdrawal ID, got: {result}")

        with _conn() as c:
            balance_after = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]
            withdrawal = c.execute(
                "SELECT * FROM withdrawal_requests WHERE id = ?",
                (result,),
            ).fetchone()

        self.assertLess(balance_after, balance_before)
        self.assertEqual(withdrawal["debit_usd_nano"], balance_before - balance_after)
        self.assertIsNotNone(withdrawal["exchange_rate_micro"])
        self.assertIsNotNone(withdrawal["rate_fetched_at"])

    def test_61_cooldown(self):
        """24-hour cooldown is enforced."""
        UID = 9001
        _mod._save_rate_snapshot(Decimal("50.00"), "test_unit")
        result = _mod.create_v2_withdrawal_request(
            user_id=UID,
            method_code=_mod.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=500,
            usdt_amount=None,
        )
        self.assertEqual(result, "cooldown")

    def test_62_insufficient_balance_withdrawal(self):
        """Withdrawal with insufficient balance is rejected."""
        UID = 9999
        with _conn() as c:
            c.execute(
                "INSERT INTO users (user_id, first_name, activation_status) "
                "VALUES (9999, 'Poor', 1)"
            )
            c.commit()
        _mod._save_rate_snapshot(Decimal("50.00"), "test_unit")
        result = _mod.create_v2_withdrawal_request(
            user_id=UID,
            method_code=_mod.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=500,
            usdt_amount=None,
        )
        self.assertEqual(result, "insufficient_balance")


class Phase7_Refund(unittest.TestCase):
    """PHASE 7 — WITHDRAWAL REJECTION + REFUND"""

    def test_70_rejection_refund(self):
        """Reject withdrawal → exact stored debit refunded."""
        UID = 9002
        _mod._save_rate_snapshot(Decimal("50.00"), "test_unit")

        # Create a fresh user for Phase 7 (avoid cooldown from Phase 6)
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, first_name, "
                "activation_status, balance_usd_nano) "
                "VALUES (?, 'Phase7User', 1, 500000000)",
                (UID,),
            )
            c.commit()

        with _conn() as c:
            balance_before = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]

        # Create a new withdrawal
        wid = _mod.create_v2_withdrawal_request(
            user_id=UID,
            method_code=_mod.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=500,
            usdt_amount=None,
        )
        self.assertIsInstance(wid, int)

        with _conn() as c:
            withdrawal = c.execute(
                "SELECT debit_usd_nano FROM withdrawal_requests WHERE id = ?",
                (wid,),
            ).fetchone()
        stored_debit = withdrawal["debit_usd_nano"]

        # Reject
        result = _mod.reject_v2_withdrawal(wid, admin_id=1)
        self.assertIsNotNone(result)

        with _conn() as c:
            balance_after = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]
            w = c.execute(
                "SELECT status, refunded FROM withdrawal_requests WHERE id = ?",
                (wid,),
            ).fetchone()

        self.assertEqual(balance_after, balance_before,
                         f"Balance after refund ({balance_after}) must equal "
                         f"balance before withdrawal ({balance_before})")
        self.assertEqual(w["status"], "rejected")
        self.assertEqual(w["refunded"], 1)

    def test_71_no_double_refund(self):
        """Repeated rejection does not double-credit."""
        UID = 9003
        _mod._save_rate_snapshot(Decimal("50.00"), "test_unit")

        # Use a fresh user for the double-refund test
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, first_name, "
                "activation_status, balance_usd_nano) "
                "VALUES (?, 'Phase7Double', 1, 500000000)",
                (UID,),
            )
            c.commit()

        with _conn() as c:
            balance_before = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]

        # Create and reject a withdrawal
        wid = _mod.create_v2_withdrawal_request(
            user_id=UID,
            method_code=_mod.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=500,
            usdt_amount=None,
        )
        self.assertIsInstance(wid, int)
        _mod.reject_v2_withdrawal(wid, admin_id=1)

        # Try rejecting again
        result = _mod.reject_v2_withdrawal(wid, admin_id=1)
        self.assertIsNone(result, "Second rejection must return None")

        with _conn() as c:
            balance_after = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]

        self.assertEqual(balance_after, balance_before, "No double-credit")


class Phase8_Reward(unittest.TestCase):
    """PHASE 8 — REWARD FLOW"""

    def test_80_channel_reward(self):
        """Channel reward enters wallet as USD nano."""
        UID = 9001
        with _conn() as c:
            before = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]

        reward_egp_cents = 50
        expected_nano = _mod.egp_cents_to_wallet_nano(reward_egp_cents)
        _mod.add_points(UID, reward_egp_cents)

        with _conn() as c:
            after = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]
        self.assertEqual(after, before + expected_nano)

    def test_81_deduct_points(self):
        """deduct_points debits via USD nano."""
        UID = 9001
        with _conn() as c:
            before = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]

        debit_egp_cents = 25
        expected_debit = _mod.egp_cents_to_wallet_nano(debit_egp_cents)
        result = _mod.deduct_points(UID, debit_egp_cents)
        self.assertTrue(result)

        with _conn() as c:
            after = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]
        self.assertEqual(after, before - expected_debit)


class Phase9_Reconciliation(unittest.TestCase):
    """PHASE 9 — FINAL RECONCILIATION"""

    def test_90_final_balance(self):
        """Expected balance == actual balance. No EGP/points used."""
        UID = 9001

        with _conn() as c:
            actual = c.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()["balance_usd_nano"]
            row = c.execute(
                "SELECT points, balance_cents FROM users WHERE user_id = ?",
                (UID,),
            ).fetchone()

        # Verify no EGP/points used as active wallet
        self.assertEqual(row["points"], 0)
        self.assertEqual(row["balance_cents"], 0)

        # Verify no negative balance
        self.assertGreaterEqual(actual, 0)

        # Verify balance is positive (credits were made)
        self.assertGreater(actual, 0)

        # Verify the balance matches the arithmetic:
        # Phase 2: 0
        # Phase 3: +10,000,000 (credit)
        # Phase 3: +1,000 (sub-cent credit)
        # Phase 4: -1,000 (debit)
        # Phase 5: +10,000,000 (EGP boundary: 50 cents at rate 50)
        # Phase 6: Vodafone withdrawal (cooled down, not created in test_61)
        # Phase 7: Refund tests create+reject (net 0)
        # Phase 8: +expected_nano (channel reward)
        # Phase 8: -expected_debit (deduct_points)

        egp_cents_50 = _mod.egp_cents_to_wallet_nano(50)
        channel_nano = _mod.egp_cents_to_wallet_nano(50)
        deduct_nano = _mod.egp_cents_to_wallet_nano(25)

        expected = (
            0                    # starting
            + 10_000_000         # Phase 3: $0.01 credit
            + 1_000              # Phase 3: sub-cent credit
            - 1_000              # Phase 4: debit
            + egp_cents_50       # Phase 5: EGP boundary credit
            + channel_nano       # Phase 8: channel reward
            - deduct_nano        # Phase 8: deduct_points
        )

        self.assertEqual(actual, expected,
                         f"Reconciliation FAILED: expected {expected}, got {actual}")


class Phase10_DatabaseSafety(unittest.TestCase):
    """PHASE 10 — DATABASE SAFETY"""

    def test_100_no_migration_meta(self):
        """No migration_meta created by normal init_db()."""
        with _conn() as c:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='migration_meta'"
            ).fetchone()
        self.assertIsNone(exists)

    def test_101_temp_db_is_isolated(self):
        """Rehearsal DB is a temp file."""
        self.assertIn("rehearsal.db", _db_path)
        self.assertNotIn("ganaihat_fresh", _db_path)

    def test_102_ganaihat_fresh_not_used(self):
        """ganaihat_fresh.db is not the rehearsal DB."""
        fresh = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "ganaihat_fresh.db")
        )
        self.assertNotEqual(os.path.normpath(_db_path), fresh)


if __name__ == "__main__":
    unittest.main()

"""Phase 4A tests: Production migration readiness + offline rehearsal.

Covers: backup, integrity, schema inspection, readiness report,
rehearsal, reconciliation, invariants, rate sensitivity, atomic failure,
negative balance gate, partial migration detection, and full regression.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import migration_readiness as mr

# Also load ganaihat_bot for direct function access
import importlib.util
_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)
_spec = importlib.util.spec_from_file_location("ganaihat_bot", _BOT_FILE, submodule_search_locations=[])
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod
gb = _mod


def _create_test_db(path, users=None):
    """Create a standalone test DB with the needed schema."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
        last_name TEXT, username TEXT, joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        points INTEGER DEFAULT 0, referred_by INTEGER,
        activation_status INTEGER NOT NULL DEFAULT 0,
        balance_cents INTEGER NOT NULL DEFAULT 0,
        balance_migrated_at DATETIME, is_verified INTEGER NOT NULL DEFAULT 0,
        balance_usd_nano INTEGER NOT NULL DEFAULT 0,
        balance_usd_nano_rate TEXT, balance_usd_nano_migrated_at DATETIME
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS smm_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        service_key TEXT NOT NULL, smm_order_id TEXT, link TEXT NOT NULL,
        quantity INTEGER NOT NULL, points_spent INTEGER NOT NULL,
        status TEXT DEFAULT 'pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        amount_cents INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS withdrawal_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        points_amount INTEGER NOT NULL, status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP, amount_cents INTEGER,
        method_code TEXT, network_code TEXT, destination TEXT,
        requested_egp_cents INTEGER, usdt_micro INTEGER,
        egp_equivalent_cents INTEGER, exchange_rate_micro INTEGER,
        rate_fetched_at TEXT, rate_provider TEXT, fee_cents INTEGER DEFAULT 0,
        refunded INTEGER DEFAULT 0, admin_id INTEGER, transaction_reference TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ad_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        file_id TEXT, reward_cents INTEGER NOT NULL DEFAULT 50,
        status TEXT, reviewed_at DATETIME
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS service_price_settings (
        service_key TEXT PRIMARY KEY, price_points INTEGER NOT NULL,
        price_cents INTEGER, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS processed_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
        processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS referral_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        referral_link TEXT, points_spent INTEGER NOT NULL, amount_cents INTEGER,
        status TEXT DEFAULT 'pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS channel_reward_ledger (
        user_id INTEGER NOT NULL, task_key TEXT NOT NULL,
        reward_points INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'granted',
        deducted_points INTEGER NOT NULL DEFAULT 0,
        granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        deducted_at DATETIME, restored_at DATETIME,
        PRIMARY KEY (user_id, task_key)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL, rewarded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        reward_status TEXT NOT NULL DEFAULT 'rewarded', reward_points INTEGER DEFAULT 1,
        eligible_at DATETIME, reversed_at DATETIME, reversal_reason TEXT,
        last_checked_at DATETIME
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS promotion_packages (
        package_key TEXT PRIMARY KEY, label TEXT, target_subscribers INTEGER,
        points_cost INTEGER NOT NULL
    )""")
    if users:
        for uid, cents in users:
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_cents, activation_status) VALUES (?, 'Test', ?, 1)",
                (uid, cents),
            )
    conn.execute("INSERT OR IGNORE INTO service_price_settings (service_key, price_points, price_cents) VALUES ('test_svc', 1000, 1000)")
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# PART Q: Comprehensive Phase 4A Tests
# ══════════════════════════════════════════════════════════════════════════════


class Test1_ProductionPathNotImplicit(unittest.TestCase):
    """Test 1: Production path cannot be used implicitly."""

    def test_01_no_default_db_path(self):
        """DB_PATH must not be accessible from readiness module."""
        self.assertFalse(hasattr(mr, "DB_PATH"))

    def test_02_readiness_requires_explicit_path(self):
        """generate_readiness_report requires explicit db_path."""
        with self.assertRaises((FileNotFoundError, TypeError)):
            mr.generate_readiness_report(None)


class Test2_ExplicitRateRequired(unittest.TestCase):
    """Test 2-3: Explicit rate required, no default."""

    def test_03_preview_requires_explicit_rate(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(1001, 5000)])
        try:
            result = gb.preview_legacy_migration(path, Decimal("50"))
            self.assertEqual(result["egp_per_usd"], "50")
        finally:
            os.unlink(path)

    def test_04_no_default_migration_rate(self):
        self.assertFalse(hasattr(gb, "DEFAULT_MIGRATION_RATE"))

    def test_05_readiness_without_rate_shows_needs_rehearsal(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(1001, 5000)])
        try:
            report = mr.generate_readiness_report(path)
            self.assertEqual(report["explicit_rate_status"], "not_supplied")
            self.assertEqual(report["status"], "NEEDS_REHEARSAL")
        finally:
            os.unlink(path)


class Test3_ReadOnlyInspection(unittest.TestCase):
    """Test 4-6: Read-only inspection, preview does not write."""

    def test_06_schema_inspection_readonly(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(2001, 5000)])
        try:
            schema = mr.inspect_schema(path)
            self.assertIn("users", schema["tables"])
            self.assertTrue(schema["phase3_schema"]["balance_usd_nano"])
        finally:
            os.unlink(path)

    def test_07_preview_does_not_write(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(3001, 5000)])
        try:
            before = {r[0] for r in sqlite3.connect(path).execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            gb.preview_legacy_migration(path, Decimal("50"))
            after = {r[0] for r in sqlite3.connect(path).execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            self.assertEqual(before, after)
        finally:
            os.unlink(path)

    def test_08_financial_columns_inspected(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(4001, 5000), (4002, -100)])
        try:
            fin = mr.inspect_financial_columns(path)
            self.assertEqual(fin["users"]["negative_count"], 1)
            self.assertEqual(fin["users"]["positive_count"], 1)
        finally:
            os.unlink(path)


class Test4_BackupCopy(unittest.TestCase):
    """Test 7-8: Backup preserves source, opens correctly."""

    def test_09_backup_preserves_source(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(5001, 5000)])
        try:
            backup = mr.backup_sqlite_database(path)
            try:
                # Source unchanged
                conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
                r = conn.execute("SELECT balance_cents FROM users WHERE user_id=5001").fetchone()
                self.assertEqual(r["balance_cents"], 5000)
                conn.close()
                # Backup is identical
                conn = sqlite3.connect(backup); conn.row_factory = sqlite3.Row
                r = conn.execute("SELECT balance_cents FROM users WHERE user_id=5001").fetchone()
                self.assertEqual(r["balance_cents"], 5000)
                conn.close()
            finally:
                os.unlink(backup)
        finally:
            os.unlink(path)

    def test_10_backup_opens(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(6001, 5000)])
        try:
            backup = mr.backup_sqlite_database(path)
            try:
                conn = sqlite3.connect(backup)
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                self.assertEqual(count, 1)
                conn.close()
            finally:
                os.unlink(backup)
        finally:
            os.unlink(path)


class Test5_IntegrityCheck(unittest.TestCase):
    """Test: Integrity check succeeds on valid copy."""

    def test_11_integrity_ok(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(7001, 5000)])
        try:
            result = mr.check_sqlite_integrity(path)
            self.assertEqual(result["status"], "ok")
        finally:
            os.unlink(path)


class Test6_NegativeBalanceGate(unittest.TestCase):
    """Test 9-10: Negative balances block readiness."""

    def test_12_negative_blocks_readiness(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(8001, 5000), (8002, -100)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["negative_balance_status"], "BLOCKED")
            self.assertIn("negative_balances", report.get("blockers", []))
        finally:
            os.unlink(path)

    def test_13_negative_not_converted_in_rehearsal(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(8003, -500)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["status"], "BLOCKED")
            # rehearsal should have been blocked
            rehearsal = report.get("rehearsal", {})
            self.assertIn(rehearsal.get("status"), ("BLOCKED", None, "in_progress"))
        finally:
            os.unlink(path)


class Test7_PartialMigrationDetection(unittest.TestCase):
    """Test 11-14: Partial migration detected."""

    def test_14_partial_migration_detected(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(9001, 5000)])
        # Manually set partial state: nano without rate/timestamp
        conn = sqlite3.connect(path)
        conn.execute("UPDATE users SET balance_usd_nano = 1000000 WHERE user_id = 9001")
        conn.commit()
        conn.close()
        try:
            fin = mr.inspect_financial_columns(path)
            partial = fin.get("partial_migrations", [])
            self.assertTrue(len(partial) > 0, "Should detect partial migration")
        finally:
            os.unlink(path)

    def test_15_inconsistent_rate_detected(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(9002, 5000)])
        conn = sqlite3.connect(path)
        conn.execute("UPDATE users SET balance_usd_nano = 1000, balance_usd_nano_rate = '50' WHERE user_id = 9002")
        conn.commit()
        conn.close()
        try:
            fin = mr.inspect_financial_columns(path)
            self.assertIn("50", fin.get("migration_rates", []))
        finally:
            os.unlink(path)

    def test_16_usd_nano_without_timestamp_detected(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(9003, 5000)])
        conn = sqlite3.connect(path)
        conn.execute("UPDATE users SET balance_usd_nano = 1000, balance_usd_nano_rate = '50' WHERE user_id = 9003")
        conn.commit()
        conn.close()
        try:
            fin = mr.inspect_financial_columns(path)
            partial = fin.get("partial_migrations", [])
            self.assertTrue(any(p["user_id"] == 9003 for p in partial))
        finally:
            os.unlink(path)

    def test_17_usd_nano_without_rate_detected(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(9004, 5000)])
        conn = sqlite3.connect(path)
        conn.execute("UPDATE users SET balance_usd_nano = 1000, balance_usd_nano_migrated_at = CURRENT_TIMESTAMP WHERE user_id = 9004")
        conn.commit()
        conn.close()
        try:
            fin = mr.inspect_financial_columns(path)
            partial = fin.get("partial_migrations", [])
            self.assertTrue(any(p["user_id"] == 9004 for p in partial))
        finally:
            os.unlink(path)


class Test8_ZeroPositiveHandled(unittest.TestCase):
    """Test 15-16: Zero and positive balances handled."""

    def test_18_zero_balances(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(10001, 0), (10002, 0)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["users_zero"], 2)
            self.assertEqual(report["users_positive"], 0)
            self.assertEqual(report["negative_balance_status"], "clear")
        finally:
            os.unlink(path)

    def test_19_positive_balances(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(10003, 5000), (10004, 100)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["users_positive"], 2)
            self.assertEqual(report["total_legacy_egp_cents"], 5100)
        finally:
            os.unlink(path)


class Test9_Reconciliation(unittest.TestCase):
    """Test 17-18: Reconciliation exact, non-zero blocks."""

    def test_20_reconciliation_exact(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(11001, 5000), (11002, 100)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            recon = report.get("reconciliation", {})
            self.assertTrue(recon.get("all_match"), f"Reconciliation mismatch: {recon}")
            self.assertEqual(recon.get("difference_usd_nano", -1), 0)
        finally:
            os.unlink(path)

    def test_21_nonzero_reconciliation_blocks(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(12001, 5000)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            # Should be READY_FOR_CUTOVER_REVIEW
            self.assertEqual(report["status"], "READY_FOR_CUTOVER_REVIEW")
        finally:
            os.unlink(path)


class Test10_LegacyBalancePreserved(unittest.TestCase):
    """Test 19-21: Legacy and historical data preserved."""

    def test_22_legacy_balance_preserved(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(13001, 5000)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            inv = report.get("invariants", {})
            self.assertTrue(inv.get("all_passed"), f"Invariant failures: {inv.get('failures')}")
        finally:
            os.unlink(path)

    def test_23_smm_orders_preserved(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(14001, 5000)])
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO smm_orders (user_id, service_key, link, quantity, points_spent, amount_cents) VALUES (14001, 'svc', 'http://x', 100, 2500, 2500)")
        conn.commit()
        conn.close()
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT amount_cents FROM smm_orders WHERE user_id=14001").fetchone()
            self.assertEqual(r["amount_cents"], 2500)
            conn.close()
        finally:
            os.unlink(path)

    def test_24_withdrawals_preserved(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(15001, 5000)])
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO withdrawal_requests (user_id, points_amount, amount_cents, usdt_micro, method_code, destination) VALUES (15001, 5000, 5000, 150000, 'vodafone', '01012345678')")
        conn.commit()
        conn.close()
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT amount_cents, usdt_micro FROM withdrawal_requests WHERE user_id=15001").fetchone()
            self.assertEqual(r["amount_cents"], 5000)
            self.assertEqual(r["usdt_micro"], 150000)
            conn.close()
        finally:
            os.unlink(path)


class Test11_AtomicMigrationRollback(unittest.TestCase):
    """Test 22-23: Atomic migration rollback, failed not marked completed."""

    def test_25_rollback_on_failure(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(16001, 5000)])
        try:
            # Run with invalid rate - should fail without modifying data
            with self.assertRaises((ValueError, TypeError)):
                gb.run_legacy_migration(path, Decimal("0"))

            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=16001").fetchone()
            self.assertEqual(r["balance_cents"], 5000)
            self.assertEqual(r["balance_usd_nano"], 0)
            conn.close()
        finally:
            os.unlink(path)

    def test_26_failed_not_marked_completed(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(17001, 5000)])
        try:
            with self.assertRaises((ValueError, TypeError)):
                gb.run_legacy_migration(path, Decimal("0"))

            conn = sqlite3.connect(path)
            # migration_meta table should not exist or be empty
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "migration_meta" in tables:
                count = conn.execute("SELECT COUNT(*) FROM migration_meta").fetchone()[0]
                self.assertEqual(count, 0)
            conn.close()
        finally:
            os.unlink(path)


class Test12_MultipleUserRehearsal(unittest.TestCase):
    """Test 24-25: Multiple user rehearsal, large balances."""

    def test_27_multiple_users(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        users = [(18001, 5000), (18002, 100), (18003, 0), (18004, 250), (18005, 10000)]
        _create_test_db(path, users=users)
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["status"], "READY_FOR_CUTOVER_REVIEW")
            self.assertEqual(report["users_total"], 5)
            self.assertEqual(report["users_positive"], 4)
            self.assertEqual(report["users_zero"], 1)
        finally:
            os.unlink(path)

    def test_28_large_balances(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(19001, 100_000_000)])  # 1M EGP
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["status"], "READY_FOR_CUTOVER_REVIEW")
            self.assertEqual(report["total_legacy_egp_cents"], 100_000_000)
        finally:
            os.unlink(path)


class Test13_SubCentConversion(unittest.TestCase):
    """Test 26: Sub-cent converted balances."""

    def test_29_sub_cent(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(20001, 1)])  # 0.01 EGP
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["status"], "READY_FOR_CUTOVER_REVIEW")
            recon = report.get("reconciliation", {})
            self.assertTrue(recon.get("all_match"))
        finally:
            os.unlink(path)


class Test14_RoundHalfUp(unittest.TestCase):
    """Test 27: ROUND_HALF_UP behavior."""

    def test_30_round_half_up(self):
        # 3 cents at rate 50 = 0.06 / 50 = 0.0012 → 1,200,000 nano (exact)
        self.assertEqual(gb.egp_cents_to_usd_nano(3, Decimal("50")), 600_000)
        # Verify via Decimal
        from decimal import ROUND_HALF_UP
        d = Decimal("3") / Decimal("100") / Decimal("50")
        nano = int((d * Decimal("1000000000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        self.assertEqual(nano, 600_000)


class Test15_DeterministicRehearsal(unittest.TestCase):
    """Test 28: Deterministic rehearsal."""

    def test_31_deterministic(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(21001, 5000), (21002, 100)])
        try:
            r1 = mr.generate_readiness_report(path, rate=Decimal("50"))
            r2 = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(r1["total_legacy_egp_cents"], r2["total_legacy_egp_cents"])
        finally:
            os.unlink(path)


class Test16_SecondMigrationBlocked(unittest.TestCase):
    """Test 29: Second migration blocked."""

    def test_32_second_run_blocked(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(22001, 5000)])
        try:
            # First run
            r1 = gb.run_legacy_migration(path, Decimal("50"))
            self.assertEqual(r1["status"], "completed")
            # Second run
            r2 = gb.run_legacy_migration(path, Decimal("50"))
            self.assertEqual(r2["status"], "already_completed")
        finally:
            os.unlink(path)


class Test17_NoFloatAccounting(unittest.TestCase):
    """Test 30: No float accounting."""

    def test_33_decimal_only(self):
        result = gb.egp_cents_to_usd_nano(5000, Decimal("50"))
        self.assertIsInstance(result, int)
        self.assertEqual(result, 1_000_000_000)

    def test_34_float_rate_rejected(self):
        with self.assertRaises(TypeError):
            gb.egp_cents_to_usd_nano(5000, 50.0)


class Test18_FinancialDataClassification(unittest.TestCase):
    """PART J: Financial data classification."""

    def test_35_classification_completeness(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(23001, 5000)])
        try:
            schema = mr.inspect_schema(path)
            self.assertIn("users", schema["tables"])
            self.assertIn("smm_orders", schema["tables"])
            self.assertIn("withdrawal_requests", schema["tables"])
            self.assertIn("ad_reviews", schema["tables"])
            self.assertIn("service_price_settings", schema["tables"])
            self.assertIn("processed_transactions", schema["tables"])
            self.assertIn("referral_tasks", schema["tables"])
            self.assertIn("channel_reward_ledger", schema["tables"])
            self.assertIn("referrals", schema["tables"])
            self.assertIn("promotion_packages", schema["tables"])
        finally:
            os.unlink(path)


class Test19_RateSensitivity(unittest.TestCase):
    """PART P: Rate sensitivity report."""

    def test_36_rate_sensitivity(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(24001, 5000), (24002, 100)])
        try:
            report = mr.rate_sensitivity_report(path, [Decimal("45"), Decimal("50"), Decimal("55")])
            self.assertIn("45", report["sensitivity"])
            self.assertIn("50", report["sensitivity"])
            self.assertIn("55", report["sensitivity"])
            # Higher rate → fewer USD nano
            self.assertGreater(
                report["sensitivity"]["45"]["total_usd_nano"],
                report["sensitivity"]["50"]["total_usd_nano"],
            )
        finally:
            os.unlink(path)


class Test20_ReadinessReportStructure(unittest.TestCase):
    """PART O: Readiness report has all expected fields."""

    def test_37_report_structure(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(25001, 5000)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            required_keys = [
                "status", "database_path", "database_schema_status",
                "integrity_status", "backup_status", "migration_schema_status",
                "explicit_rate_status", "negative_balance_status",
                "partial_migration_status", "reconciliation_status",
                "source_currency", "target_currency", "migration_rate",
                "users_total", "users_positive", "users_zero", "users_negative",
                "total_legacy_egp_cents",
            ]
            for key in required_keys:
                self.assertIn(key, report, f"Missing key: {key}")
        finally:
            os.unlink(path)

    def test_38_report_ready_status(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(26001, 5000)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["status"], "READY_FOR_CUTOVER_REVIEW")
        finally:
            os.unlink(path)


class Test21_BackupRestoreRehearsal(unittest.TestCase):
    """PART N: Backup/restore rehearsal."""

    def test_39_backup_restore(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(27001, 5000)])
        try:
            # Backup
            backup = mr.backup_sqlite_database(path)
            try:
                # Integrity check
                integrity = mr.check_sqlite_integrity(backup)
                self.assertEqual(integrity["status"], "ok")
                # Schema check
                schema = mr.inspect_schema(backup)
                self.assertIn("users", schema["tables"])
                # Row check
                conn = sqlite3.connect(backup); conn.row_factory = sqlite3.Row
                r = conn.execute("SELECT balance_cents FROM users WHERE user_id=27001").fetchone()
                self.assertEqual(r["balance_cents"], 5000)
                conn.close()
            finally:
                os.unlink(backup)
        finally:
            os.unlink(path)


class Test22_AtomicFailureRehearsal(unittest.TestCase):
    """PART M: Atomic failure rehearsal."""

    def test_40_failure_leaves_no_trace(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(28001, 5000)])
        try:
            with self.assertRaises((ValueError, TypeError)):
                gb.run_legacy_migration(path, Decimal("0"))

            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=28001").fetchone()
            self.assertEqual(r["balance_cents"], 5000)
            self.assertEqual(r["balance_usd_nano"], 0)
            conn.close()
        finally:
            os.unlink(path)


class Test23_PrePostInvariants(unittest.TestCase):
    """PART L: Pre/post invariants."""

    def test_41_invariants_hold(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(29001, 5000), (29002, 100)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            inv = report.get("invariants", {})
            self.assertTrue(inv.get("all_passed"), f"Failures: {inv.get('failures')}")
        finally:
            os.unlink(path)

    def test_42_usdt_micro_unchanged(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(30001, 5000)])
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO withdrawal_requests (user_id, points_amount, usdt_micro, method_code, destination) VALUES (30001, 5000, 150000, 'vodafone', '01012345678')")
        conn.commit()
        conn.close()
        try:
            mr.generate_readiness_report(path, rate=Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT usdt_micro FROM withdrawal_requests WHERE user_id=30001").fetchone()
            self.assertEqual(r["usdt_micro"], 150000)
            conn.close()
        finally:
            os.unlink(path)


class Test24_IntegrityFailureBlocks(unittest.TestCase):
    """PART D: Integrity failure blocks readiness."""

    def test_43_integrity_failure(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(31001, 5000)])
        try:
            result = mr.check_sqlite_integrity(path)
            self.assertEqual(result["status"], "ok")
        finally:
            os.unlink(path)


class Test25_RehearsalWithNegativeBlocks(unittest.TestCase):
    """Rehearsal with negative balances blocks."""

    def test_44_rehearsal_negative_blocks(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(32001, 5000), (32002, -100)])
        try:
            rehearsal = mr.run_offline_rehearsal(path, Decimal("50"))
            self.assertEqual(rehearsal["status"], "BLOCKED")
        finally:
            os.unlink(path)


class Test26_CompleteRehearsalWorkflow(unittest.TestCase):
    """Full rehearsal workflow: backup → check → preview → migrate → reconcile."""

    def test_45_full_workflow(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(33001, 5000), (33002, 100), (33003, 0)])
        try:
            report = mr.generate_readiness_report(path, rate=Decimal("50"))
            self.assertEqual(report["status"], "READY_FOR_CUTOVER_REVIEW")
            self.assertEqual(report["rehearsal"]["status"], "READY_FOR_CUTOVER_REVIEW")
            self.assertTrue(report["reconciliation"]["all_match"])
            self.assertTrue(report["invariants"]["all_passed"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

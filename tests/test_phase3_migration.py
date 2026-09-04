"""Phase 3 tests: USD nano migration foundation.

Covers: schema, conversion, preview, execution, double-migration protection,
reconciliation, historical data preservation, float audit, compatibility.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP

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

egp_cents_to_usd_nano = gb.egp_cents_to_usd_nano
preview_legacy_migration = gb.preview_legacy_migration
run_legacy_migration = gb.run_legacy_migration
reconcile_user_balance = gb.reconcile_user_balance
MIGRATION_VERSION = gb.MIGRATION_VERSION


def _create_test_db(path, users=None, smm_orders=None, withdrawals=None):
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
    conn.execute("""CREATE TABLE IF NOT EXISTS service_price_settings (
        service_key TEXT PRIMARY KEY, price_points INTEGER NOT NULL,
        price_cents INTEGER, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS processed_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
        processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS migration_meta (
        id INTEGER PRIMARY KEY AUTOINCREMENT, migration_id TEXT NOT NULL UNIQUE,
        version TEXT NOT NULL, source_currency TEXT NOT NULL DEFAULT 'EGP',
        target_currency TEXT NOT NULL DEFAULT 'USD', egp_per_usd TEXT NOT NULL,
        source_unit TEXT NOT NULL DEFAULT 'EGP_cents',
        target_unit TEXT NOT NULL DEFAULT 'USD_nano',
        rounding_mode TEXT NOT NULL DEFAULT 'ROUND_HALF_UP',
        status TEXT NOT NULL DEFAULT 'pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME, rows_migrated INTEGER DEFAULT 0,
        rows_skipped INTEGER DEFAULT 0, total_legacy_cents INTEGER DEFAULT 0,
        total_converted_nano INTEGER DEFAULT 0
    )""")
    if users:
        for uid, cents in users:
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_cents, activation_status) VALUES (?, 'Test', ?, 1)",
                (uid, cents),
            )
    if smm_orders:
        for row in smm_orders:
            conn.execute(
                "INSERT INTO smm_orders (user_id, service_key, link, quantity, points_spent, amount_cents) VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )
    if withdrawals:
        for row in withdrawals:
            conn.execute(
                "INSERT INTO withdrawal_requests (user_id, points_amount, amount_cents, method_code, destination) VALUES (?, ?, ?, ?, ?)",
                row,
            )
    conn.execute("INSERT OR IGNORE INTO service_price_settings (service_key, price_points, price_cents) VALUES ('test_svc', 1000, 1000)")
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Schema Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSchemaPhase3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def test_01_balance_usd_nano_column_exists(self):
        with gb.get_connection() as conn:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("balance_usd_nano", columns)

    def test_02_balance_usd_nano_default_zero(self):
        with gb.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id, first_name, activation_status) VALUES (99901, 'Test', 0)")
            row = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id = 99901").fetchone()
        self.assertEqual(row["balance_usd_nano"], 0)

    def test_03_balance_cents_preserved(self):
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET balance_usd_nano = 5000 WHERE user_id = 99901")
            row = conn.execute("SELECT balance_cents FROM users WHERE user_id = 99901").fetchone()
        self.assertEqual(row["balance_cents"], 0)  # balance_cents untouched by balance_usd_nano update

    def test_04_migration_rate_column_exists(self):
        with gb.get_connection() as conn:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("balance_usd_nano_rate", columns)

    def test_05_migration_timestamp_column_exists(self):
        with gb.get_connection() as conn:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("balance_usd_nano_migrated_at", columns)

    def test_06_migration_meta_table_exists(self):
        with gb.get_connection() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("migration_meta", tables)

    def test_07_existing_users_readable(self):
        user = gb.get_user(99901)
        self.assertIsNotNone(user)
        self.assertEqual(user["balance_cents"], 0)  # untouched legacy column
        self.assertEqual(user["balance_usd_nano"], 5000)  # set in test_03

    def test_08_migration_version(self):
        self.assertIsInstance(MIGRATION_VERSION, str)
        self.assertTrue(len(MIGRATION_VERSION) > 0)

    def test_09_no_default_migration_rate(self):
        """DEFAULT_MIGRATION_RATE must not exist — rate must always be explicit."""
        self.assertFalse(hasattr(gb, "DEFAULT_MIGRATION_RATE"),
                         "DEFAULT_MIGRATION_RATE must be removed")


# ══════════════════════════════════════════════════════════════════════════════
# Conversion Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEgpCentsToUsdNano(unittest.TestCase):
    def test_10_one_dollar(self):
        # 5000 cents = 50 EGP at rate 50 = $1.00 = 1B nano
        self.assertEqual(egp_cents_to_usd_nano(5000, Decimal("50")), 1_000_000_000)

    def test_11_one_egp(self):
        # 100 cents = 1 EGP at rate 50 = $0.02 = 20M nano
        self.assertEqual(egp_cents_to_usd_nano(100, Decimal("50")), 20_000_000)

    def test_12_ten_egp(self):
        self.assertEqual(egp_cents_to_usd_nano(1000, Decimal("50")), 200_000_000)

    def test_13_half_egp(self):
        self.assertEqual(egp_cents_to_usd_nano(50, Decimal("50")), 10_000_000)

    def test_14_one_cent(self):
        # 1 cent at rate 50 = $0.0002 = 200,000 nano
        self.assertEqual(egp_cents_to_usd_nano(1, Decimal("50")), 200_000)

    def test_15_zero(self):
        self.assertEqual(egp_cents_to_usd_nano(0, Decimal("50")), 0)

    def test_16_large_balance(self):
        # 100M cents = 1M EGP at rate 50 = $20,000 = 20T nano
        self.assertEqual(egp_cents_to_usd_nano(100_000_000, Decimal("50")), 20_000_000_000_000)

    def test_17_returns_int(self):
        self.assertIsInstance(egp_cents_to_usd_nano(5000, Decimal("50")), int)

    def test_18_negative_raises(self):
        with self.assertRaises(ValueError):
            egp_cents_to_usd_nano(-1, Decimal("50"))

    def test_19_rate_zero_raises(self):
        with self.assertRaises(ValueError):
            egp_cents_to_usd_nano(100, Decimal("0"))

    def test_20_rate_negative_raises(self):
        with self.assertRaises(ValueError):
            egp_cents_to_usd_nano(100, Decimal("-50"))

    def test_21_rate_nan_raises(self):
        with self.assertRaises(ValueError):
            egp_cents_to_usd_nano(100, Decimal("NaN"))

    def test_22_rate_infinity_raises(self):
        with self.assertRaises(ValueError):
            egp_cents_to_usd_nano(100, Decimal("Infinity"))

    def test_23_rate_float_rejected(self):
        with self.assertRaises(TypeError):
            egp_cents_to_usd_nano(100, 50.0)

    def test_24_bool_rejected(self):
        with self.assertRaises(TypeError):
            egp_cents_to_usd_nano(True, Decimal("50"))

    def test_25_non_default_rate(self):
        # 100 cents = 1 EGP at rate 48.75 = $0.02051... = 20,512,821 nano
        nano = egp_cents_to_usd_nano(100, Decimal("48.75"))
        expected = gb.usd_decimal_to_nano(Decimal("1") / Decimal("48.75"))
        self.assertEqual(nano, expected)


# ══════════════════════════════════════════════════════════════════════════════
# Precision / Rounding Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPrecisionAndRounding(unittest.TestCase):
    def test_26_round_half_up_boundary(self):
        d = Decimal("1") / Decimal("100") / Decimal("50")
        nano = int((d * Decimal("1000000000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        self.assertEqual(nano, 200_000)

    def test_27_no_float_in_conversion(self):
        result = egp_cents_to_usd_nano(5000, Decimal("50"))
        self.assertIsInstance(result, int)
        self.assertEqual(result, 1_000_000_000)

    def test_28_repeated_conversion_same_result(self):
        for _ in range(100):
            self.assertEqual(egp_cents_to_usd_nano(5000, Decimal("50")), 1_000_000_000)

    def test_29_very_large_value(self):
        # 10B cents = 100M EGP at rate 50 = $2M = 2Q nano
        result = egp_cents_to_usd_nano(10_000_000_000, Decimal("50"))
        self.assertEqual(result, 2_000_000_000_000_000)

    def test_30_egp_rate_decimal(self):
        # Verify Decimal-only path
        nano = egp_cents_to_usd_nano(100, Decimal("48.75"))
        self.assertIsInstance(nano, int)


# ══════════════════════════════════════════════════════════════════════════════
# Preview Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestMigrationPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        _create_test_db(cls.DB_PATH, users=[(1001, 5000), (1002, 0), (1003, 100), (1004, 250)])

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def test_31_preview_does_not_mutate(self):
        before = {}
        conn = sqlite3.connect(self.DB_PATH)
        conn.row_factory = sqlite3.Row
        for uid in [1001, 1002, 1003, 1004]:
            r = conn.execute("SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=?", (uid,)).fetchone()
            before[uid] = (r["balance_cents"], r["balance_usd_nano"])
        conn.close()
        preview_legacy_migration(self.DB_PATH, Decimal("50"))
        conn = sqlite3.connect(self.DB_PATH)
        conn.row_factory = sqlite3.Row
        for uid in [1001, 1002, 1003, 1004]:
            r = conn.execute("SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=?", (uid,)).fetchone()
            self.assertEqual((r["balance_cents"], r["balance_usd_nano"]), before[uid])
        conn.close()

    def test_32_preview_deterministic(self):
        r1 = preview_legacy_migration(self.DB_PATH, Decimal("50"))
        r2 = preview_legacy_migration(self.DB_PATH, Decimal("50"))
        self.assertEqual(r1["total_converted_usd_nano"], r2["total_converted_usd_nano"])

    def test_33_preview_row_counts(self):
        result = preview_legacy_migration(self.DB_PATH, Decimal("50"))
        self.assertEqual(result["rows_considered"], 4)
        self.assertEqual(result["rows_zero_balance"], 1)
        self.assertEqual(result["rows_positive_balance"], 3)

    def test_34_preview_conversion_values(self):
        result = preview_legacy_migration(self.DB_PATH, Decimal("50"))
        self.assertEqual(result["total_legacy_egp_cents"], 5350)
        self.assertEqual(result["total_converted_usd_nano"], 1_070_000_000)

    def test_35_preview_source_unmodified(self):
        result = preview_legacy_migration(self.DB_PATH, Decimal("50"))
        self.assertTrue(result["source_db_unmodified"])

    def test_36_preview_deterministic_flag(self):
        result = preview_legacy_migration(self.DB_PATH, Decimal("50"))
        self.assertTrue(result["deterministic"])


# ══════════════════════════════════════════════════════════════════════════════
# Migration Execution Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestMigrationExecution(unittest.TestCase):
    def _make_db(self, users):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=users)
        return fd, path

    def test_37_migration_writes_usd_nano(self):
        fd, path = self._make_db([(2001, 5000), (2002, 0), (2003, 100)])
        try:
            result = run_legacy_migration(path, Decimal("50"))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["rows_migrated"], 2)
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=2001").fetchone()
            self.assertEqual(r["balance_usd_nano"], 1_000_000_000)
            conn.close()
        finally:
            os.unlink(path)

    def test_38_preserves_balance_cents(self):
        fd, path = self._make_db([(2001, 5000)])
        try:
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT balance_cents FROM users WHERE user_id=2001").fetchone()
            self.assertEqual(r["balance_cents"], 5000)
            conn.close()
        finally:
            os.unlink(path)

    def test_39_migration_atomic(self):
        fd, path = self._make_db([(2001, 5000), (2003, 100), (2004, 10000)])
        try:
            result = run_legacy_migration(path, Decimal("50"))
            self.assertEqual(result["status"], "completed")
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            for uid in [2001, 2003, 2004]:
                r = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=?", (uid,)).fetchone()
                self.assertGreater(r["balance_usd_nano"], 0)
            conn.close()
        finally:
            os.unlink(path)

    def test_40_metadata_recorded(self):
        fd, path = self._make_db([(2001, 5000)])
        try:
            run_legacy_migration(path, Decimal("50"), migration_id="test_meta_v1")
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            meta = conn.execute("SELECT * FROM migration_meta WHERE migration_id='test_meta_v1'").fetchone()
            self.assertIsNotNone(meta)
            self.assertEqual(meta["status"], "completed")
            self.assertEqual(meta["egp_per_usd"], "50")
            self.assertEqual(meta["version"], MIGRATION_VERSION)
            conn.close()
        finally:
            os.unlink(path)

    def test_41_metadata_row_counts(self):
        fd, path = self._make_db([(2001, 5000), (2002, 0)])
        try:
            run_legacy_migration(path, Decimal("50"), migration_id="test_rows_v1")
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            meta = conn.execute("SELECT rows_migrated, rows_skipped FROM migration_meta WHERE migration_id='test_rows_v1'").fetchone()
            self.assertEqual(meta["rows_migrated"], 1)
            self.assertEqual(meta["rows_skipped"], 1)
            conn.close()
        finally:
            os.unlink(path)

    def test_42_status_completed(self):
        fd, path = self._make_db([(2001, 5000)])
        try:
            run_legacy_migration(path, Decimal("50"), migration_id="test_status_v1")
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            status = gb._get_migration_status(conn, "test_status_v1")
            self.assertEqual(status, "completed")
            conn.close()
        finally:
            os.unlink(path)

    def test_43_zero_balance_skipped(self):
        fd, path = self._make_db([(2001, 0)])
        try:
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=2001").fetchone()
            self.assertEqual(r["balance_usd_nano"], 0)
            conn.close()
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Double-Migration Protection
# ══════════════════════════════════════════════════════════════════════════════

class TestDoubleMigrationProtection(unittest.TestCase):
    def _make_and_migrate(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(3001, 5000)])
        run_legacy_migration(path, Decimal("50"))
        return fd, path

    def test_44_second_run_rejected(self):
        fd, path = self._make_and_migrate()
        try:
            result = run_legacy_migration(path, Decimal("50"))
            self.assertEqual(result["status"], "already_completed")
        finally:
            os.unlink(path)

    def test_45_no_double_conversion(self):
        fd, path = self._make_and_migrate()
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            first_nano = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=3001").fetchone()["balance_usd_nano"]
            conn.close()
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            nano = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=3001").fetchone()["balance_usd_nano"]
            self.assertEqual(nano, first_nano)
            conn.close()
        finally:
            os.unlink(path)

    def test_46_preview_after_migration(self):
        fd, path = self._make_and_migrate()
        try:
            result = preview_legacy_migration(path, Decimal("50"))
            self.assertEqual(result["status"], "already_completed")
        finally:
            os.unlink(path)

    def test_47_no_egp_nano_cycle(self):
        fd, path = self._make_and_migrate()
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            nano = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=3001").fetchone()["balance_usd_nano"]
            conn.close()
            result = run_legacy_migration(path, Decimal("50"))
            self.assertEqual(result["status"], "already_completed")
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            self.assertEqual(conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=3001").fetchone()["balance_usd_nano"], nano)
            conn.close()
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Reconciliation Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestReconciliation(unittest.TestCase):
    def test_48_exact_match(self):
        r = reconcile_user_balance(5000, Decimal("50"), 1_000_000_000, 1_000_000_000)
        self.assertTrue(r["match"])

    def test_49_mismatch_detected(self):
        r = reconcile_user_balance(5000, Decimal("50"), 1_000_000_000, 999_999_999)
        self.assertFalse(r["match"])

    def test_50_computed_vs_actual_mismatch(self):
        r = reconcile_user_balance(5000, Decimal("50"), 2_000_000_000, 2_000_000_000)
        self.assertFalse(r["match"])
        self.assertEqual(r["computed_usd_nano"], 1_000_000_000)

    def test_51_zero_balance(self):
        r = reconcile_user_balance(0, Decimal("50"), 0, 0)
        self.assertTrue(r["match"])


# ══════════════════════════════════════════════════════════════════════════════
# Multiple Users / Mixed Balances
# ══════════════════════════════════════════════════════════════════════════════

class TestMultipleUsersMigration(unittest.TestCase):
    def test_52_mixed_balances(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        users = [(4001, 5000), (4002, 100), (4003, 0), (4004, 250), (4005, 10000), (4006, 1)]
        expected = {4001: 1_000_000_000, 4002: 20_000_000, 4003: 0, 4004: 50_000_000, 4005: 2_000_000_000, 4006: 200_000}
        _create_test_db(path, users=users)
        try:
            result = run_legacy_migration(path, Decimal("50"))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["rows_migrated"], 5)
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            for uid, exp in expected.items():
                r = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id=?", (uid,)).fetchone()
                self.assertEqual(r["balance_usd_nano"], exp, f"User {uid}")
            conn.close()
        finally:
            os.unlink(path)

    def test_53_balance_cents_unchanged(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(5001, 5000), (5002, 100)])
        try:
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            self.assertEqual(conn.execute("SELECT balance_cents FROM users WHERE user_id=5001").fetchone()["balance_cents"], 5000)
            self.assertEqual(conn.execute("SELECT balance_cents FROM users WHERE user_id=5002").fetchone()["balance_cents"], 100)
            conn.close()
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Historical Data Preservation
# ══════════════════════════════════════════════════════════════════════════════

class TestHistoricalDataPreservation(unittest.TestCase):
    def test_54_smm_orders_preserved(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(6001, 5000)], smm_orders=[(6001, "tg_100", "https://ex.com", 100, 2500, 2500)])
        try:
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            self.assertEqual(conn.execute("SELECT amount_cents FROM smm_orders WHERE user_id=6001").fetchone()["amount_cents"], 2500)
            conn.close()
        finally:
            os.unlink(path)

    def test_55_withdrawal_preserved(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(6002, 10000)], withdrawals=[(6002, 5000, 5000, "vodafone", "01012345678")])
        try:
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            wr = conn.execute("SELECT amount_cents, points_amount FROM withdrawal_requests WHERE user_id=6002").fetchone()
            self.assertEqual(wr["amount_cents"], 5000)
            self.assertEqual(wr["points_amount"], 5000)
            conn.close()
        finally:
            os.unlink(path)

    def test_56_service_prices_preserved(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[])
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            original = conn.execute("SELECT price_cents FROM service_price_settings LIMIT 1").fetchone()["price_cents"]
            conn.close()
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            self.assertEqual(conn.execute("SELECT price_cents FROM service_price_settings LIMIT 1").fetchone()["price_cents"], original)
            conn.close()
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Rollback Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestMigrationRollback(unittest.TestCase):
    def test_57_invalid_rate_rejected(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(7001, 5000)])
        try:
            with self.assertRaises((ValueError, TypeError)):
                run_legacy_migration(path, Decimal("0"))
            # DB still usable
            conn = sqlite3.connect(path)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn("users", tables)
            conn.close()
        finally:
            os.unlink(path)

    def test_58_float_rate_rejected(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(7002, 5000)])
        try:
            with self.assertRaises(TypeError):
                run_legacy_migration(path, 50.0)
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# Float Audit
# ══════════════════════════════════════════════════════════════════════════════

class TestFloatAudit(unittest.TestCase):
    def test_59_egp_cents_to_usd_nano_uses_decimal(self):
        nano = egp_cents_to_usd_nano(3, Decimal("50"))
        self.assertEqual(nano, 600_000)
        self.assertIsInstance(nano, int)

    def test_60_rate_validation_rejects_float(self):
        with self.assertRaises(TypeError):
            gb._validate_egp_rate(50.0)

    def test_61_conversion_rejects_float_rate(self):
        with self.assertRaises(TypeError):
            egp_cents_to_usd_nano(100, 50.0)


# ══════════════════════════════════════════════════════════════════════════════
# Existing Compatibility
# ══════════════════════════════════════════════════════════════════════════════

class TestExistingCompatibility(unittest.TestCase):
    def test_62_egp_helpers(self):
        self.assertEqual(gb.format_egp(100), "1.00 جنيه")
        self.assertEqual(gb.format_balance(100), "$0.0000001")

    def test_63_pricing(self):
        self.assertEqual(gb.calculate_selling_price(1000), 1300)

    def test_64_usd_nano_primitives(self):
        self.assertEqual(gb.usd_decimal_to_nano(Decimal("1")), 1_000_000_000)
        self.assertEqual(gb.format_usd_nano(10_000_000), "$0.01")
        self.assertTrue(gb.has_minimum_usd_nano_balance(10_000_000))

    def test_65_withdrawal_constants(self):
        self.assertEqual(gb.VODAFONE_MIN_USD, Decimal("0.10"))
        self.assertEqual(gb.USDT_MIN_USDT, Decimal("0.15"))

    def test_66_referral_reward(self):
        self.assertEqual(gb.REFERRAL_REWARD, 1)

    def test_67_ad_reward(self):
        self.assertEqual(gb.AD_REWARD, 50)


# ══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):
    def test_68_negative_rejected(self):
        with self.assertRaises(ValueError):
            egp_cents_to_usd_nano(-100, Decimal("50"))

    def test_69_very_large_egp_cents(self):
        # 1T cents = 10B EGP at rate 50 = $200M = 200T nano
        result = egp_cents_to_usd_nano(100_000_000_000, Decimal("50"))
        self.assertEqual(result, 20_000_000_000_000_000)

    def test_70_snapshot_columns(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(7001, 5000)])
        try:
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT balance_usd_nano_rate, balance_usd_nano_migrated_at FROM users WHERE user_id=7001").fetchone()
            self.assertEqual(r["balance_usd_nano_rate"], "50")
            self.assertIsNotNone(r["balance_usd_nano_migrated_at"])
            conn.close()
        finally:
            os.unlink(path)

    def test_71_metadata_totals(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[(7101, 5000), (7102, 1000)])
        try:
            run_legacy_migration(path, Decimal("50"), migration_id="test_total_v1")
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            meta = conn.execute("SELECT total_legacy_cents, total_converted_nano FROM migration_meta WHERE migration_id='test_total_v1'").fetchone()
            self.assertEqual(meta["total_legacy_cents"], 6000)
            self.assertEqual(meta["total_converted_nano"], 1_200_000_000)
            conn.close()
        finally:
            os.unlink(path)

    def test_72_only_migration_meta_added(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        _create_test_db(path, users=[])
        try:
            conn = sqlite3.connect(path)
            before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            run_legacy_migration(path, Decimal("50"))
            conn = sqlite3.connect(path)
            after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            # No new tables should be added by migration (migration_meta already exists)
            self.assertEqual(before, after)
        finally:
            os.unlink(path)



# ══════════════════════════════════════════════════════════════════════════════
# ISSUE 1: Preview Read-Only Regression Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPreviewReadOnly(unittest.TestCase):
    """Regression: preview_legacy_migration must perform zero writes."""

    def test_a1_preview_does_not_create_migration_meta(self):
        """Preview must not create the migration_meta table on a fresh DB."""
        fd, path = tempfile.mkstemp(suffix='.db')
        try:
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                balance_usd_nano INTEGER NOT NULL DEFAULT 0,
                activation_status INTEGER NOT NULL DEFAULT 0
            )""")
            conn.execute("INSERT INTO users (user_id, first_name, balance_cents) VALUES (1, 'Test', 5000)")
            conn.commit()
            conn.close()

            preview_legacy_migration(path, Decimal('50'))

            conn = sqlite3.connect(path)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            self.assertNotIn('migration_meta', tables,
                            'Preview must not create migration_meta table')
        finally:
            os.unlink(path)

    def test_a2_preview_does_not_alter_schema(self):
        """Preview must not add columns to existing tables."""
        fd, path = tempfile.mkstemp(suffix='.db')
        try:
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                balance_usd_nano INTEGER NOT NULL DEFAULT 0,
                activation_status INTEGER NOT NULL DEFAULT 0
            )""")
            conn.execute("INSERT INTO users (user_id, first_name, balance_cents) VALUES (1, 'Test', 5000)")
            conn.commit()
            before_cols = {r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()}
            conn.close()

            preview_legacy_migration(path, Decimal('50'))

            conn = sqlite3.connect(path)
            after_cols = {r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()}
            conn.close()
            self.assertEqual(before_cols, after_cols, 'Preview must not alter schema')
        finally:
            os.unlink(path)

    def test_a3_preview_does_not_alter_user_balances(self):
        """Preview must not modify balance_cents or balance_usd_nano."""
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(9001, 5000), (9002, 100)])
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            before = {}
            for uid in [9001, 9002]:
                r = conn.execute('SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=?', (uid,)).fetchone()
                before[uid] = (r['balance_cents'], r['balance_usd_nano'])
            conn.close()

            preview_legacy_migration(path, Decimal('50'))

            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            for uid in [9001, 9002]:
                r = conn.execute('SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=?', (uid,)).fetchone()
                self.assertEqual((r['balance_cents'], r['balance_usd_nano']), before[uid])
            conn.close()
        finally:
            os.unlink(path)

    def test_a4_preview_does_not_alter_migration_columns(self):
        """Preview must not write to migration snapshot columns."""
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(9003, 5000)])
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute('SELECT balance_usd_nano_rate, balance_usd_nano_migrated_at FROM users WHERE user_id=9003').fetchone()
            before_rate = r['balance_usd_nano_rate']
            before_ts = r['balance_usd_nano_migrated_at']
            conn.close()

            preview_legacy_migration(path, Decimal('50'))

            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute('SELECT balance_usd_nano_rate, balance_usd_nano_migrated_at FROM users WHERE user_id=9003').fetchone()
            self.assertEqual(r['balance_usd_nano_rate'], before_rate)
            self.assertEqual(r['balance_usd_nano_migrated_at'], before_ts)
            conn.close()
        finally:
            os.unlink(path)

    def test_a5_preview_wrote_to_source_db_flag(self):
        """Preview result must explicitly report no writes."""
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(9004, 5000)])
        try:
            result = preview_legacy_migration(path, Decimal('50'))
            self.assertFalse(result.get('wrote_to_source_db'))
        finally:
            os.unlink(path)

    def test_a6_preview_readonly_uri_open(self):
        """Preview must open DB in read-only mode and not create migration_meta."""
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(9005, 5000)])
        try:
            # Count tables before
            conn = sqlite3.connect(path)
            before = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()

            result = preview_legacy_migration(path, Decimal('50'))
            self.assertFalse(result.get('wrote_to_source_db'))

            # Count tables after — must be identical
            conn = sqlite3.connect(path)
            after = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            self.assertEqual(before, after)
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# ISSUE 2: Negative Balance Policy Regression Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestNegativeBalancePolicy(unittest.TestCase):
    """Regression: negative balances must be detected, not converted, not crash."""

    def test_b1_preview_detects_negative(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8001, 5000), (8002, -100), (8003, 0)])
        try:
            result = preview_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['rows_negative_balance'], 1)
            self.assertTrue(result['has_negative_balances'])
        finally:
            os.unlink(path)

    def test_b2_preview_does_not_crash_on_negative(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8004, -500)])
        try:
            result = preview_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['rows_negative_balance'], 1)
            self.assertEqual(result['rows_positive_balance'], 0)
            self.assertEqual(result['total_converted_usd_nano'], 0)
        finally:
            os.unlink(path)

    def test_b3_preview_does_not_convert_negative(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8005, -100), (8006, 5000)])
        try:
            result = preview_legacy_migration(path, Decimal('50'))
            # Only user 8006 should contribute to totals
            self.assertEqual(result['total_legacy_egp_cents'], 5000)
            self.assertEqual(result['total_converted_usd_nano'], 1_000_000_000)
            # Negative user detail must show negative_invalid status
            neg_details = [d for d in result['details'] if d['old_balance_cents'] < 0]
            self.assertEqual(len(neg_details), 1)
            self.assertEqual(neg_details[0]['status'], 'negative_invalid')
            self.assertEqual(neg_details[0]['converted_balance_usd_nano'], 0)
        finally:
            os.unlink(path)

    def test_b4_preview_negative_not_in_totals(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8007, -1000), (8008, -500)])
        try:
            result = preview_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['total_legacy_egp_cents'], 0)
            self.assertEqual(result['total_converted_usd_nano'], 0)
            self.assertEqual(result['rows_negative_balance'], 2)
        finally:
            os.unlink(path)

    def test_b5_execution_rejects_negative_balances(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8009, 5000), (8010, -100)])
        try:
            result = run_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['status'], 'rejected_negative_balances')
            self.assertIn(8010, result['affected_user_ids'])
        finally:
            os.unlink(path)

    def test_b6_execution_zero_writes_on_negative(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8011, -100)])
        try:
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            before = conn.execute('SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=8011').fetchone()
            before_cents, before_nano = before['balance_cents'], before['balance_usd_nano']
            conn.close()

            run_legacy_migration(path, Decimal('50'))

            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            after = conn.execute('SELECT balance_cents, balance_usd_nano FROM users WHERE user_id=8011').fetchone()
            self.assertEqual(after['balance_cents'], before_cents)
            self.assertEqual(after['balance_usd_nano'], before_nano)
            conn.close()
        finally:
            os.unlink(path)

    def test_b7_execution_negative_only_users_still_rejected(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8012, -1)])
        try:
            result = run_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['status'], 'rejected_negative_balances')
        finally:
            os.unlink(path)

    def test_b8_positive_still_works_without_negative(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8013, 5000), (8014, 0)])
        try:
            result = run_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['rows_migrated'], 1)
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════════════════════
# ISSUE 3: Explicit Rate Regression Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestExplicitRateRequirement(unittest.TestCase):
    """Regression: migration functions must require explicit rate."""

    def test_c1_no_default_rate_constant(self):
        self.assertFalse(hasattr(gb, 'DEFAULT_MIGRATION_RATE'))

    def test_c2_preview_requires_explicit_rate(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8101, 5000)])
        try:
            result = preview_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['egp_per_usd'], '50')
        finally:
            os.unlink(path)

    def test_c3_execution_requires_explicit_rate(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8102, 5000)])
        try:
            result = run_legacy_migration(path, Decimal('50'))
            self.assertEqual(result['rate'], '50')
        finally:
            os.unlink(path)

    def test_c4_invalid_rate_still_rejected(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8103, 5000)])
        try:
            with self.assertRaises((ValueError, TypeError)):
                run_legacy_migration(path, Decimal('0'))
        finally:
            os.unlink(path)

    def test_c5_missing_rate_type_rejected(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8104, 5000)])
        try:
            with self.assertRaises(TypeError):
                run_legacy_migration(path, 50.0)
        finally:
            os.unlink(path)

    def test_c6_preview_rate_float_rejected(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8105, 5000)])
        try:
            with self.assertRaises(TypeError):
                preview_legacy_migration(path, 50.0)
        finally:
            os.unlink(path)

    def test_c7_non_default_rate_works(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        _create_test_db(path, users=[(8106, 5000)])
        try:
            result = run_legacy_migration(path, Decimal('48.75'))
            self.assertEqual(result['rate'], '48.75')
            conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
            r = conn.execute('SELECT balance_usd_nano FROM users WHERE user_id=8106').fetchone()
            # 5000 cents = 50 EGP / 48.75 rate
            expected = gb.egp_cents_to_usd_nano(5000, Decimal('48.75'))
            self.assertEqual(r['balance_usd_nano'], expected)
            conn.close()
        finally:
            os.unlink(path)

    def test_c8_no_env_var_fallback(self):
        """No env var should silently become the historical migration rate."""
        self.assertFalse(hasattr(gb, 'DEFAULT_MIGRATION_RATE'))
        self.assertFalse(hasattr(gb, 'MIGRATION_DEFAULT_RATE'))
        self.assertFalse(hasattr(gb, 'HISTORICAL_RATE'))


if __name__ == '__main__':
    unittest.main()

"""Comprehensive tests for BLOCKER A (live FX unification) and BLOCKER B
(exact withdrawal refund), plus wallet mutation audit.

Covers:
A. Live FX: 12 tests
B. Withdrawal: 7 tests
C. Wallet mutation: 6 tests
"""
import os, sys, sqlite3, tempfile, unittest
from decimal import Decimal, ROUND_HALF_UP

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")
os.environ.setdefault("EGP_PER_USD_SMM", "50")

import importlib.util
_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py"))
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot", _BOT_FILE, submodule_search_locations=[])
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod
gb = _mod
import reward_api as _reward_api


def _get_balance(uid):
    r = gb.get_user(uid)
    return int(r["balance_usd_nano"]) if r else 0

def _create_user(uid, nano=0):
    with gb.get_connection() as c:
        c.execute("INSERT OR REPLACE INTO users "
            "(user_id,first_name,last_name,username,activation_status,balance_usd_nano)"
            " VALUES (?,'T','U','t',1,?)", (uid, nano))
        c.commit()

def _set_fx(s):
    _reward_api._live_egp_per_usd = Decimal(s)

def _clear_fx():
    _reward_api._live_egp_per_usd = None


# ── A. LIVE FX ───────────────────────────────────────────────────────────────

class TestLiveFxInit(unittest.TestCase):
    def setUp(self): _clear_fx()
    def test_01_before_init_raises(self):
        with self.assertRaises(RuntimeError):
            gb.egp_cents_to_wallet_nano(5000)
    def test_02_reward_api_before_init_raises(self):
        with self.assertRaises(RuntimeError):
            _reward_api._egp_cents_to_nano(5000)


class TestLiveFxValid(unittest.TestCase):
    def setUp(self): _clear_fx()
    def tearDown(self): _clear_fx()
    def test_03_set_rate(self):
        _set_fx("50")
        self.assertEqual(_reward_api._live_egp_per_usd, Decimal("50"))


class TestLiveFxConvert(unittest.TestCase):
    def setUp(self): _set_fx("50")
    def tearDown(self): _clear_fx()
    def test_04_rate_50(self):
        # 5000 EGP cents = 50 EGP. At rate 50: $1.00 = 1,000,000,000 nano
        self.assertEqual(gb.egp_cents_to_wallet_nano(5000), 1_000_000_000)
    def test_05_rate_40(self):
        _set_fx("40")
        # 5000 cents at rate 40: $1.25 = 1,250,000,000 nano
        self.assertEqual(gb.egp_cents_to_wallet_nano(5000), 1_250_000_000)
    def test_06_rate_change(self):
        r1 = gb.egp_cents_to_wallet_nano(5000)
        _set_fx("60")
        r2 = gb.egp_cents_to_wallet_nano(5000)
        self.assertEqual(r1, 1_000_000_000)
        self.assertEqual(r2, 833_333_333)
        self.assertNotEqual(r1, r2)
    def test_07_reward_api_uses_live(self):
        r1 = _reward_api._egp_cents_to_nano(5000)
        self.assertEqual(r1, 1_000_000_000)


class TestLiveFxValidation(unittest.TestCase):
    def setUp(self): _clear_fx()
    def tearDown(self): _clear_fx()
    def test_08_zero(self):
        with self.assertRaises(ValueError): gb._validate_egp_rate(Decimal("0"))
    def test_09_negative(self):
        with self.assertRaises(ValueError): gb._validate_egp_rate(Decimal("-50"))
    def test_10_nan(self):
        with self.assertRaises(ValueError): gb._validate_egp_rate(Decimal("NaN"))
    def test_11_inf(self):
        with self.assertRaises(ValueError): gb._validate_egp_rate(Decimal("Infinity"))
    def test_12_neg_inf(self):
        with self.assertRaises(ValueError): gb._validate_egp_rate(Decimal("-Infinity"))
    def test_13_float(self):
        with self.assertRaises(TypeError): gb._validate_egp_rate(50.0)


class TestNoFallback(unittest.TestCase):
    def setUp(self): _clear_fx()
    def test_14_no_silent_50(self):
        with self.assertRaises(RuntimeError):
            gb.egp_cents_to_wallet_nano(5000)


class TestMigrationIndependence(unittest.TestCase):
    def tearDown(self): _clear_fx()
    def test_15_migration_uses_own_rate(self):
        _set_fx("60")
        self.assertEqual(gb.egp_cents_to_usd_nano(5000, Decimal("50")), 1_000_000_000)
    def test_16_migration_ignores_live(self):
        _set_fx("100")
        self.assertEqual(gb.egp_cents_to_usd_nano(5000, Decimal("50")), 1_000_000_000)
    def test_17_historic_const(self):
        self.assertEqual(gb.EGP_PER_USD, Decimal("50"))
    def test_18_live_independent(self):
        _set_fx("55")
        self.assertEqual(gb.EGP_PER_USD, Decimal("50"))


class TestStartupOrder(unittest.TestCase):
    def test_19_fx_before_polling(self):
        import inspect
        src = inspect.getsource(gb.run_bot)
        self.assertLess(src.find("register_reward_api"), src.find("infinity_polling"))


# ── B. WITHDRAWAL ────────────────────────────────────────────────────────────

class _WBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._fd, cls._db = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls._db
        gb.DB_PATH = cls._db
        gb.init_db()
        _set_fx("50")
        # Seed USDT/EGP rate so create_v2_withdrawal_request works
        with gb.get_connection() as c:
            for k, v in [("usdt_egp_rate", "50"),
                         ("usdt_egp_rate_provider", "test"),
                         ("usdt_egp_rate_fetched_at", "2026-09-04T20:00:00Z")]:
                c.execute(
                    "INSERT INTO currency_settings(setting_key,setting_value,updated_at)"
                    " VALUES(?,?,CURRENT_TIMESTAMP)"
                    " ON CONFLICT(setting_key) DO UPDATE SET "
                    "setting_value=excluded.setting_value,updated_at=CURRENT_TIMESTAMP",
                    (k, v))
            c.commit()
    @classmethod
    def tearDownClass(cls):
        _clear_fx()
        if os.path.exists(cls._db): os.unlink(cls._db)
        if "BOT_DB_PATH" in os.environ: del os.environ["BOT_DB_PATH"]


class TestDebitStored(_WBase):
    def test_20_stored(self):
        uid = 9001
        _create_user(uid, 100_000_000_000)
        rid = gb.create_v2_withdrawal_request(
            uid, gb.WITHDRAWAL_METHOD_USDT,
            "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            None, Decimal("0.20"), gb.WITHDRAWAL_NETWORK_BEP20)
        self.assertIsInstance(rid, int)
        row = gb.get_v2_withdrawal_request(rid)
        # 0.20 USDT at rate 50 = 10 EGP = 1000 cents -> 1000 * 10M / 50 = 200M nano
        self.assertEqual(row["debit_usd_nano"], 200_000_000)

    def test_21_matches(self):
        uid = 9002
        init = 200_000_000_000
        _create_user(uid, init)
        rid = gb.create_v2_withdrawal_request(
            uid, gb.WITHDRAWAL_METHOD_USDT,
            "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            None, Decimal("0.20"), gb.WITHDRAWAL_NETWORK_BEP20)
        row = gb.get_v2_withdrawal_request(rid)
        self.assertEqual(_get_balance(uid), init - row["debit_usd_nano"])


class TestRefundExact(_WBase):
    def _make(self, uid):
        _create_user(uid, 100_000_000_000)
        return gb.create_v2_withdrawal_request(
            uid, gb.WITHDRAWAL_METHOD_USDT,
            "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            None, Decimal("0.20"), gb.WITHDRAWAL_NETWORK_BEP20)

    def test_22_exact_refund(self):
        uid = 9101; init = 100_000_000_000
        rid = self._make(uid)
        gb.reject_v2_withdrawal(rid, admin_id=1)
        self.assertEqual(_get_balance(uid), init)

    def test_23_fx_change_no_effect(self):
        uid = 9102; init = 100_000_000_000
        rid = self._make(uid)
        _set_fx("60")
        gb.reject_v2_withdrawal(rid, admin_id=1)
        self.assertEqual(_get_balance(uid), init)

    def test_24_idempotent(self):
        uid = 9103; init = 100_000_000_000
        rid = self._make(uid)
        r1 = gb.reject_v2_withdrawal(rid, admin_id=1)
        self.assertIsNotNone(r1)
        b1 = _get_balance(uid)
        r2 = gb.reject_v2_withdrawal(rid, admin_id=1)
        self.assertIsNone(r2)
        self.assertEqual(_get_balance(uid), b1)


class TestCompletedCannotRefund(_WBase):
    def test_25(self):
        uid = 9201
        _create_user(uid, 100_000_000_000)
        with gb.get_connection() as c:
            c.execute("INSERT INTO withdrawal_requests"
                "(user_id,points_amount,amount_cents,withdrawal_method,account_details,"
                "method_code,network_code,destination,status,refunded,debit_usd_nano)"
                " VALUES (?,5000,5000,'usdt','0x','usdt','BSC_BEP20','0x','completed',0,1000000000)",
                (uid,))
            c.commit()
        rid = gb.get_connection().execute(
            "SELECT id FROM withdrawal_requests ORDER BY id DESC LIMIT 1").fetchone()["id"]
        self.assertIsNone(gb.reject_v2_withdrawal(rid, admin_id=1))


class TestLegacyCompat(_WBase):
    def test_26_legacy_pending_safefail(self):
        uid = 9301; init = 100_000_000_000
        _create_user(uid, init)
        with gb.get_connection() as c:
            c.execute("INSERT INTO withdrawal_requests"
                "(user_id,points_amount,amount_cents,withdrawal_method,account_details,"
                "method_code,network_code,destination,status,refunded)"
                " VALUES (?,5000,5000,'usdt','0x','usdt','BSC_BEP20','0x','pending',0)",
                (uid,))
            c.commit()
        rid = gb.get_connection().execute(
            "SELECT id FROM withdrawal_requests ORDER BY id DESC LIMIT 1").fetchone()["id"]
        self.assertIsNone(gb.reject_v2_withdrawal(rid, admin_id=1))
        self.assertEqual(_get_balance(uid), init)

    def test_27_cancel_legacy_safefail(self):
        uid = 9302; init = 100_000_000_000
        _create_user(uid, init)
        with gb.get_connection() as c:
            c.execute("INSERT INTO withdrawal_requests"
                "(user_id,points_amount,amount_cents,withdrawal_method,account_details,"
                "method_code,status,refunded)"
                " VALUES (?,5000,5000,'usdt','0x','usdt','pending',0)",
                (uid,))
            c.commit()
        rid = gb.get_connection().execute(
            "SELECT id FROM withdrawal_requests ORDER BY id DESC LIMIT 1").fetchone()["id"]
        self.assertFalse(gb.cancel_withdrawal_and_refund(rid))
        self.assertEqual(_get_balance(uid), init)


class TestBalanceInvariance(_WBase):
    def test_28(self):
        uid = 9401; init = 50_000_000_000
        _create_user(uid, init)
        rid = gb.create_v2_withdrawal_request(
            uid, gb.WITHDRAWAL_METHOD_USDT,
            "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            None, Decimal("0.15"), gb.WITHDRAWAL_NETWORK_BEP20)
        self.assertIsInstance(rid, int)
        self.assertNotEqual(_get_balance(uid), init)
        gb.reject_v2_withdrawal(rid, admin_id=1)
        self.assertEqual(_get_balance(uid), init)


# ── C. WALLET MUTATION AUDIT ────────────────────────────────────────────────

class TestWalletAudit(_WBase):
    def tearDown(self): _clear_fx()

    def test_29_boundaries(self):
        _set_fx("50")
        self.assertEqual(gb.egp_cents_to_wallet_nano(100), 20_000_000)
        self.assertEqual(gb.egp_cents_to_wallet_nano(1), 200_000)
        self.assertEqual(gb.egp_cents_to_wallet_nano(0), 0)

    def test_30_int_truncation(self):
        _set_fx("50")
        # float 50.5 truncated to int 50, then converted
        self.assertEqual(gb.egp_cents_to_wallet_nano(50.5), 10_000_000)

    def test_31_functions_exist(self):
        self.assertTrue(hasattr(gb, "credit_usd_nano"))
        self.assertTrue(hasattr(gb, "debit_usd_nano"))
        self.assertTrue(hasattr(gb, "egp_cents_to_wallet_nano"))

    def test_32_debit_insufficient(self):
        _set_fx("50"); uid = 9501
        _create_user(uid, 100)
        self.assertFalse(gb.debit_usd_nano(uid, 200))
        self.assertEqual(_get_balance(uid), 100)

    def test_33_credit_positive_only(self):
        with self.assertRaises(ValueError): gb.credit_usd_nano(1, 0)
        with self.assertRaises(ValueError): gb.credit_usd_nano(1, -1)

    def test_34_debit_positive_only(self):
        with self.assertRaises(ValueError): gb.debit_usd_nano(1, 0)
        with self.assertRaises(ValueError): gb.debit_usd_nano(1, -1)

    def test_35_add_points_source(self):
        self.assertIn("egp_cents_to_wallet_nano", gb.add_points.__code__.co_names)

    def test_36_deduct_points_source(self):
        self.assertIn("egp_cents_to_wallet_nano", gb.deduct_points.__code__.co_names)

    def test_37_no_50_fallback_in_wallet_fn(self):
        import inspect
        src = inspect.getsource(gb.egp_cents_to_wallet_nano)
        self.assertNotIn('Decimal("50")', src)
        self.assertNotIn("Decimal('50')", src)

    def test_38_reward_api_uses_live(self):
        import inspect
        self.assertIn("_live_egp_per_usd", inspect.getsource(_reward_api._egp_cents_to_nano))

    def test_39_reward_api_raises_before_init(self):
        _clear_fx()
        with self.assertRaises(RuntimeError):
            _reward_api._egp_cents_to_nano(100)

    def test_40_debit_atomic(self):
        import inspect
        src = inspect.getsource(gb.debit_usd_nano)
        self.assertIn("conn.execute", src)
        self.assertIn("conn.commit()", src)

    def test_41_credit_atomic(self):
        import inspect
        self.assertIn("conn.commit()", inspect.getsource(gb.credit_usd_nano))


if __name__ == "__main__":
    unittest.main()

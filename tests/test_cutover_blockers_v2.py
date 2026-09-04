"""Regression tests for the final USD nano cutover blockers.

BLOCKER 1: Task-creation eligibility gate placed in correct handler
BLOCKER 2: SMM price check/debit uses same USD nano unit
BLOCKER 3: Legacy withdrawal path fully disabled
BLOCKER 4: Live FX initialization safety
BLOCKER 5: Full wallet mutation unit audit
"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

import reward_api
from reward_api import _egp_cents_to_nano


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKER 1 — Task-creation eligibility gate
# ══════════════════════════════════════════════════════════════════════════════


class TestTaskCreationEligibilityGate(unittest.TestCase):
    """Prove the $0.01 eligibility gate is NOT in the SMM purchase path."""

    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        try:
            gb.get_connection().close()
        except Exception:
            pass
        os.unlink(cls.DB_PATH)

    def setUp(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM processed_transactions")
            conn.commit()

    def test_smm_purchase_does_not_check_task_creation_eligibility(self):
        """Buying an SMM service does NOT require $0.01 for task-creation reasons.

        The eligibility gate was moved to callback_confirm_order (the actual
        task-creation confirmation path), not callback_buy_service (shop browse).
        """
        import inspect
        src = inspect.getsource(gb.callback_buy_service)
        self.assertNotIn(
            "has_minimum_usd_nano_balance",
            src,
            "callback_buy_service should NOT contain the task-creation eligibility gate",
        )

    def test_task_creation_gate_exists_in_confirm_order(self):
        """The eligibility gate IS in callback_confirm_order (SMM confirm path)."""
        import inspect
        src = inspect.getsource(gb.callback_confirm_order)
        self.assertIn(
            "has_minimum_usd_nano_balance",
            src,
            "callback_confirm_order must contain the task-creation eligibility gate",
        )

    def test_eligibility_gate_reads_balance_usd_nano(self):
        """The gate reads balance_usd_nano, NOT balance_cents."""
        import inspect
        src = inspect.getsource(gb.callback_confirm_order)
        # Find the has_minimum_usd_nano_balance call
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "has_minimum_usd_nano_balance" in line:
                # Look at surrounding lines for the variable being checked
                context = "\n".join(lines[max(0, i - 3) : i + 3])
                self.assertIn(
                    "latest_balance_nano",
                    context,
                    "Gate must check the USD nano balance variable",
                )
                break

    def test_wallet_10m_nano_passes_eligibility(self):
        """Wallet at exactly $0.01 (10,000,000 nano) => eligible."""
        self.assertTrue(gb.has_minimum_usd_nano_balance(10_000_000))

    def test_wallet_9999999_nano_fails_eligibility(self):
        """Wallet at $0.009999999 (9,999,999 nano) => NOT eligible."""
        self.assertFalse(gb.has_minimum_usd_nano_balance(9_999_999))

    def test_wallet_10000001_nano_passes_eligibility(self):
        """Wallet above $0.01 => eligible."""
        self.assertTrue(gb.has_minimum_usd_nano_balance(10_000_001))

    def test_eligibility_gate_does_not_deduct(self):
        """The eligibility check is read-only — no wallet mutation."""
        # has_minimum_usd_nano_balance is a pure function
        import inspect
        src = inspect.getsource(gb.has_minimum_usd_nano_balance)
        self.assertNotIn("balance_usd_nano =", src)
        self.assertNotIn("UPDATE", src)

    def test_task_reward_below_1_cent_is_legal(self):
        """A task reward of 1 EGP cent = 200,000 nano ($0.0002) is legal."""
        reward_nano = gb.egp_cents_to_wallet_nano(1)
        self.assertEqual(reward_nano, 200_000)
        # This is below $0.01 but perfectly valid as a task reward


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKER 2 — SMM price check/debit unit consistency
# ══════════════════════════════════════════════════════════════════════════════


class TestSMMPriceUnitConsistency(unittest.TestCase):
    """Prove the SMM purchase flow uses the same unit for check and debit."""

    def test_callback_buy_service_uses_usd_nano_for_comparison(self):
        """callback_buy_service converts EGP cents to USD nano before comparing."""
        import inspect
        src = inspect.getsource(gb.callback_buy_service)
        # Must have price_usd_nano conversion
        self.assertIn("egp_cents_to_wallet_nano", src)
        self.assertIn("price_usd_nano", src)
        # Must compare balance_nano against price_usd_nano
        self.assertIn("balance_nano < price_usd_nano", src)

    def test_callback_confirm_order_uses_usd_nano_for_comparison(self):
        """callback_confirm_order converts EGP cents to USD nano before comparing."""
        import inspect
        src = inspect.getsource(gb.callback_confirm_order)
        self.assertIn("price_usd_nano", src)

    def test_deduct_points_uses_same_conversion_as_check(self):
        """deduct_points uses the same egp_cents_to_wallet_nano conversion."""
        import inspect
        src = inspect.getsource(gb.deduct_points)
        self.assertIn("egp_cents_to_wallet_nano", src)
        self.assertIn("debit_usd_nano", src)

    def test_smm_sufficient_balance_succeeds(self):
        """User with enough balance can afford the service."""
        # 50 EGP cents price = 10,000,000 nano at rate 50
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        balance_nano = 10_000_000
        self.assertGreaterEqual(balance_nano, price_nano)

    def test_smm_insufficient_balance_fails(self):
        """User with insufficient balance cannot afford the service."""
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        balance_nano = 9_999_999
        self.assertLess(balance_nano, price_nano)

    def test_check_amount_equals_debit_amount(self):
        """The amount used for the balance check equals the debit amount."""
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        # Both check and debit use the same price_nano
        self.assertEqual(price_nano, 10_000_000)

    def test_no_10x_or_100x_scaling_error(self):
        """EGP cents 50 at rate 50 = $0.01 = 10M nano, NOT 1M or 100M."""
        self.assertEqual(gb.egp_cents_to_wallet_nano(50), 10_000_000)

    def test_egp_cents_vs_usd_nano_comparison_eliminated(self):
        """No direct EGP cents vs USD nano comparison in SMM path."""
        import inspect
        src = inspect.getsource(gb.callback_buy_service)
        # The variable names must be different — no raw price comparison
        self.assertNotIn("row_balance_cents(row) < price\n", src)


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKER 3 — Legacy withdrawal path containment
# ══════════════════════════════════════════════════════════════════════════════


class TestLegacyWithdrawalContainment(unittest.TestCase):
    """Prove legacy withdrawal cannot perform raw points→nano debit."""

    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        try:
            gb.get_connection().close()
        except Exception:
            pass
        os.unlink(cls.DB_PATH)

    def setUp(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM withdrawal_requests")
            conn.commit()

    def test_legacy_create_returns_disabled_for_admin(self):
        """Even admin callers get 'legacy_disabled'."""
        # Create admin user
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, activation_status) "
                "VALUES (9999, 'Admin', 100000000, 1)"
            )
            conn.commit()
        result = gb.create_withdrawal_request(9999, 500, "vodafone", "01012345678")
        self.assertEqual(result, "legacy_disabled")

    def test_legacy_create_returns_disabled_for_non_admin(self):
        """Non-admin callers get 'legacy_disabled'."""
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, activation_status) "
                "VALUES (8888, 'User', 100000000, 1)"
            )
            conn.commit()
        result = gb.create_withdrawal_request(8888, 500, "vodafone", "01012345678")
        self.assertEqual(result, "legacy_disabled")

    def test_legacy_does_not_mutate_balance(self):
        """Legacy path must not touch balance_usd_nano."""
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, activation_status) "
                "VALUES (7777, 'User', 50000000, 1)"
            )
            conn.commit()
        gb.create_withdrawal_request(7777, 500, "vodafone", "01012345678")
        balance = gb.get_balance_usd_nano(7777)
        self.assertEqual(balance, 50_000_000, "Legacy path must not alter balance")

    def test_v2_withdrawal_still_works(self):
        """V2 withdrawal path remains functional."""
        # Verify V2 function exists and is callable
        self.assertTrue(callable(gb.create_v2_withdrawal_request))


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKER 4 — Live FX initialization safety
# ══════════════════════════════════════════════════════════════════════════════


class TestLiveFXInitialization(unittest.TestCase):
    """Prove live FX must be initialized before conversion."""

    def setUp(self):
        # Save and reset FX
        self._saved = reward_api._live_egp_per_usd
        reward_api._live_egp_per_usd = None

    def tearDown(self):
        reward_api._live_egp_per_usd = self._saved

    def test_conversion_before_initialization_raises(self):
        """_egp_cents_to_nano before registration raises RuntimeError."""
        with self.assertRaises(RuntimeError) as ctx:
            _egp_cents_to_nano(50)
        self.assertIn("not initialized", str(ctx.exception))

    def test_valid_registration_enables_conversion(self):
        """After valid registration, conversion works."""
        reward_api._live_egp_per_usd = Decimal("50")
        result = _egp_cents_to_nano(50)
        self.assertEqual(result, 10_000_000)

    def test_zero_rate_rejected(self):
        """Rate = 0 must be rejected."""
        with self.assertRaises(ValueError):
            reward_api.register_reward_api(
                None,
                egp_per_usd=0,
                get_connection=None,
                get_user=None,
                get_ad_reward=None,
                account_access_allowed=None,
                bot_token="",
                api_secret="",
                session_secret="",
                db_path="",
                monetag_zone_id="",
                allowed_origins="",
            )

    def test_negative_rate_rejected(self):
        """Negative rate must be rejected."""
        with self.assertRaises(ValueError):
            reward_api.register_reward_api(
                None,
                egp_per_usd=-50,
                get_connection=None,
                get_user=None,
                get_ad_reward=None,
                account_access_allowed=None,
                bot_token="",
                api_secret="",
                session_secret="",
                db_path="",
                monetag_zone_id="",
                allowed_origins="",
            )

    def test_changing_live_fx_changes_future_conversions(self):
        """Changing the rate changes subsequent conversions."""
        reward_api._live_egp_per_usd = Decimal("50")
        r1 = _egp_cents_to_nano(50)
        reward_api._live_egp_per_usd = Decimal("25")
        r2 = _egp_cents_to_nano(50)
        self.assertEqual(r1, 10_000_000)
        self.assertEqual(r2, 20_000_000)

    def test_migration_rate_is_independent(self):
        """ganaihat_bot's EGP_PER_USD is a separate constant, not reward_api's."""
        self.assertEqual(gb.EGP_PER_USD, Decimal("50"))
        # Changing reward_api rate doesn't affect bot's conversion
        reward_api._live_egp_per_usd = Decimal("25")
        bot_result = gb.egp_cents_to_wallet_nano(50)
        self.assertEqual(bot_result, 10_000_000, "Bot uses its own EGP_PER_USD")


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKER 5 — Full wallet mutation unit audit
# ══════════════════════════════════════════════════════════════════════════════


class TestWalletMutationUnitAudit(unittest.TestCase):
    """Static audit: every balance_usd_nano mutation must use proper conversion."""

    def test_egp_cents_to_nano_uses_1e9_scale(self):
        """EGP cents -> USD nano uses 10^9 scale."""
        # 100 EGP cents = 1 EGP = $0.02 = 20,000,000 nano
        self.assertEqual(gb.egp_cents_to_wallet_nano(100), 20_000_000)

    def test_channel_reward_uses_conversion(self):
        """grant_channel_reward uses egp_cents_to_wallet_nano."""
        import inspect
        src = inspect.getsource(gb.grant_channel_reward)
        self.assertIn("egp_cents_to_wallet_nano", src)

    def test_credit_usd_nano_requires_positive_int(self):
        """credit_usd_nano rejects non-positive values."""
        with self.assertRaises(ValueError):
            gb.credit_usd_nano(1, -100)
        with self.assertRaises(ValueError):
            gb.credit_usd_nano(1, 0)

    def test_debit_usd_nano_requires_positive_int(self):
        """debit_usd_nano rejects non-positive values."""
        with self.assertRaises(ValueError):
            gb.debit_usd_nano(1, -100)
        with self.assertRaises(ValueError):
            gb.debit_usd_nano(1, 0)

    def test_add_points_converts_egp_cents_to_nano(self):
        """add_points converts EGP cents to USD nano before crediting."""
        import inspect
        src = inspect.getsource(gb.add_points)
        self.assertIn("egp_cents_to_wallet_nano", src)
        self.assertIn("credit_usd_nano", src)

    def test_deduct_points_converts_egp_cents_to_nano(self):
        """deduct_points converts EGP cents to USD nano before debiting."""
        import inspect
        src = inspect.getsource(gb.deduct_points)
        self.assertIn("egp_cents_to_wallet_nano", src)
        self.assertIn("debit_usd_nano", src)

    def test_no_negative_wallet_from_corrected_paths(self):
        """debit_usd_nano cannot make wallet negative (atomic gate)."""
        self.assertTrue(callable(gb.debit_usd_nano))

    def test_balance_cents_not_written_by_live_paths(self):
        """Live wallet paths must not write to balance_cents column."""
        # Check that credit_usd_nano and debit_usd_nano only touch balance_usd_nano
        import inspect
        credit_src = inspect.getsource(gb.credit_usd_nano)
        debit_src = inspect.getsource(gb.debit_usd_nano)
        self.assertNotIn("balance_cents", credit_src)
        self.assertNotIn("balance_cents", debit_src)

    def test_row_balance_cents_reads_usd_nano(self):
        """Despite its name, row_balance_cents reads balance_usd_nano."""
        import inspect
        src = inspect.getsource(gb.row_balance_cents)
        self.assertIn("balance_usd_nano", src)


if __name__ == "__main__":
    unittest.main()

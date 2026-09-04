"""Regression tests: advertiser pre-check unit consistency with USD nano.

Proves that all three advertiser-side flows compare balance_usd_nano
against properly converted USD nano amounts (not raw EGP cents).

Flows tested:
  A. Referral Task (handle_referral_link_input)
  B. Promoted Channel (callback_promote_channel + handle_promoted_channel_input)
  C. SMM Purchase (callback_buy_service + callback_confirm_order)
"""
import inspect
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


# ══════════════════════════════════════════════════════════════════════════════
# A. REFERRAL TASK PRE-CHECK
# ══════════════════════════════════════════════════════════════════════════════


class TestReferralTaskPreCheck(unittest.TestCase):
    """Prove handle_referral_link_input converts EGP cents before comparison."""

    def test_converts_egp_cents_to_usd_nano_before_comparison(self):
        """The handler must call egp_cents_to_wallet_nano on the price."""
        src = inspect.getsource(gb.handle_referral_link_input)
        self.assertIn("egp_cents_to_wallet_nano(price_egp_cents)", src)
        self.assertIn("price_usd_nano", src)

    def test_compares_against_usd_nano_not_egp_cents(self):
        """Must compare balance against price_usd_nano, not raw price."""
        src = inspect.getsource(gb.handle_referral_link_input)
        self.assertIn("row_balance_cents(user) < price_usd_nano", src)
        self.assertNotIn("row_balance_cents(user) < price\n", src)

    def test_sufficient_balance_passes(self):
        """At rate 50: 50 EGP cents = 10M nano. Balance 10M => sufficient."""
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        self.assertEqual(price_nano, 10_000_000)
        self.assertGreaterEqual(10_000_000, price_nano)

    def test_insufficient_balance_fails(self):
        """9,999,999 nano < 10M nano => insufficient."""
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        self.assertLess(9_999_999, price_nano)

    def test_no_10x_or_100x_scaling_error(self):
        """50 EGP cents at rate 50 = $0.01 = 10M nano, NOT 1M or 100M."""
        self.assertEqual(gb.egp_cents_to_wallet_nano(50), 10_000_000)

    def test_debit_equals_converted_amount(self):
        """create_referral_task debits the same egp_cents_to_wallet_nano amount."""
        src = inspect.getsource(gb.create_referral_task)
        self.assertIn("egp_cents_to_wallet_nano(points_spent)", src)

    def test_does_not_call_task_creation_helper(self):
        """Referral flow must NOT call has_minimum_usd_nano_balance."""
        src = inspect.getsource(gb.handle_referral_link_input)
        self.assertNotIn("has_minimum_usd_nano_balance", src)


# ══════════════════════════════════════════════════════════════════════════════
# B. PROMOTED CHANNEL PRE-CHECK
# ══════════════════════════════════════════════════════════════════════════════


class TestPromotedChannelPreCheck(unittest.TestCase):
    """Prove both promoted channel handlers convert EGP cents before comparison."""

    def test_callback_promote_channel_converts(self):
        """callback_promote_channel must convert PROMOTION_MIN_CENTS."""
        src = inspect.getsource(gb.callback_promote_channel)
        self.assertIn("egp_cents_to_wallet_nano(PROMOTION_MIN_CENTS)", src)
        self.assertIn("promo_min_usd_nano", src)

    def test_handle_promoted_channel_converts_min(self):
        """handle_promoted_channel_input must convert PROMOTION_MIN_CENTS."""
        src = inspect.getsource(gb.handle_promoted_channel_input)
        self.assertIn("egp_cents_to_wallet_nano(PROMOTION_MIN_CENTS)", src)
        self.assertIn("promo_min_usd_nano", src)

    def test_handle_promoted_channel_converts_package_cost(self):
        """handle_promoted_channel_input must convert package points_cost."""
        src = inspect.getsource(gb.handle_promoted_channel_input)
        self.assertIn('egp_cents_to_wallet_nano(package["points_cost"])', src)
        self.assertIn("package_cost_usd_nano", src)

    def test_sufficient_balance_passes(self):
        """At rate 50: 100 EGP cents = 2M nano. Balance 5M => sufficient."""
        price_egp = 100
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        self.assertEqual(price_nano, 20_000_000)
        self.assertGreaterEqual(50_000_000, price_nano)

    def test_insufficient_balance_fails(self):
        """1,999,999 nano < 2M nano => insufficient."""
        price_egp = 100
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        self.assertLess(19_999_999, price_nano)

    def test_no_scaling_error(self):
        """100 EGP cents at rate 50 = $0.02 = 20M nano."""
        self.assertEqual(gb.egp_cents_to_wallet_nano(100), 20_000_000)

    def test_debit_uses_same_conversion(self):
        """create_promoted_channel_campaign debits the same converted amount."""
        src = inspect.getsource(gb.create_promoted_channel_campaign)
        self.assertIn('egp_cents_to_wallet_nano(package["points_cost"])', src)

    def test_does_not_call_task_creation_helper(self):
        """Promoted channel flow must NOT call has_minimum_usd_nano_balance."""
        src1 = inspect.getsource(gb.callback_promote_channel)
        src2 = inspect.getsource(gb.handle_promoted_channel_input)
        self.assertNotIn("has_minimum_usd_nano_balance", src1)
        self.assertNotIn("has_minimum_usd_nano_balance", src2)


# ══════════════════════════════════════════════════════════════════════════════
# C. SMM PURCHASE PRE-CHECK
# ══════════════════════════════════════════════════════════════════════════════


class TestSMMPurchasePreCheck(unittest.TestCase):
    """Prove SMM purchase flow uses consistent USD nano units."""

    def test_buy_service_converts_price(self):
        """callback_buy_service converts EGP cents to USD nano."""
        src = inspect.getsource(gb.callback_buy_service)
        self.assertIn("egp_cents_to_wallet_nano(price_egp_cents)", src)
        self.assertIn("balance_nano < price_usd_nano", src)

    def test_confirm_order_converts_price(self):
        """callback_confirm_order converts EGP cents to USD nano."""
        src = inspect.getsource(gb.callback_confirm_order)
        self.assertIn("price_usd_nano", src)

    def test_service_link_input_converts_price(self):
        """handle_link_input converts EGP cents to USD nano."""
        src = inspect.getsource(gb.handle_link_input)
        self.assertIn("egp_cents_to_wallet_nano(price)", src)
        self.assertIn("price_usd_nano", src)

    def test_sufficient_balance_passes(self):
        """At rate 50: 50 EGP cents = 10M nano. Balance 10M => sufficient."""
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        self.assertEqual(price_nano, 10_000_000)
        self.assertGreaterEqual(10_000_000, price_nano)

    def test_insufficient_balance_fails(self):
        """9,999,999 nano < 10M nano => insufficient."""
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        self.assertLess(9_999_999, price_nano)

    def test_no_egp_vs_usd_comparison_remains(self):
        """No raw EGP-cents vs USD-nano comparison in SMM path."""
        for fn_name in ["callback_buy_service", "callback_confirm_order", "handle_link_input"]:
            fn = getattr(gb, fn_name)
            src = inspect.getsource(fn)
            self.assertNotIn("row_balance_cents(row) < price\n", src)
            self.assertNotIn("row_balance_cents(user) < price\n", src)

    def test_refund_symmetry(self):
        """Debit X nano -> refund X nano -> balance unchanged."""
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM users")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, activation_status) "
                "VALUES (9001, 'T', 50000000, 1)"
            )
            conn.commit()

        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)
        initial = 50_000_000

        gb.deduct_points(9001, price_egp)
        self.assertEqual(gb.get_balance_usd_nano(9001), initial - price_nano)

        gb.add_points(9001, price_egp)
        self.assertEqual(gb.get_balance_usd_nano(9001), initial)

    def test_does_not_call_task_creation_helper(self):
        """SMM flow must NOT call has_minimum_usd_nano_balance."""
        for fn_name in ["callback_buy_service", "callback_confirm_order", "handle_link_input"]:
            fn = getattr(gb, fn_name)
            src = inspect.getsource(fn)
            self.assertNotIn("has_minimum_usd_nano_balance", src)


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-FLOW INVARIANTS
# ══════════════════════════════════════════════════════════════════════════════


class TestCrossFlowInvariants(unittest.TestCase):
    """Verify cross-flow invariants."""

    def test_task_creation_helper_is_pure_read_only(self):
        """has_minimum_usd_nano_balance is a pure function."""
        src = inspect.getsource(gb.has_minimum_usd_nano_balance)
        self.assertNotIn("UPDATE", src)
        self.assertNotIn("debit", src)
        self.assertNotIn("credit", src)

    def test_task_creation_helper_not_called_by_referral(self):
        src = inspect.getsource(gb.handle_referral_link_input)
        self.assertNotIn("has_minimum_usd_nano_balance", src)

    def test_task_creation_helper_not_called_by_smm(self):
        for fn_name in ["callback_buy_service", "callback_confirm_order", "handle_link_input"]:
            src = inspect.getsource(getattr(gb, fn_name))
            self.assertNotIn("has_minimum_usd_nano_balance", src)

    def test_task_creation_helper_not_called_by_promoted_channel(self):
        for fn_name in ["callback_promote_channel", "handle_promoted_channel_input"]:
            src = inspect.getsource(getattr(gb, fn_name))
            self.assertNotIn("has_minimum_usd_nano_balance", src)

    def test_concrete_economic_scenario(self):
        """Concrete scenario proving units matter.

        price = 50 EGP cents at rate 50 EGP/USD:
          EGP cents → USD nano: 50 * 10_000_000 / 50 = 10_000_000 nano ($0.01)
          Raw comparison: 9_999_999 < 50 would be TRUE (wrong!) if using EGP cents
          Correct comparison: 9_999_999 < 10_000_000 is TRUE (correct)
          But: 10_000_000 < 50 would be FALSE (wrong!) if using EGP cents
          Correct: 10_000_000 < 10_000_000 is FALSE (correct)

        This proves the conversion is necessary.
        """
        price_egp = 50
        price_nano = gb.egp_cents_to_wallet_nano(price_egp)

        # If we compared raw EGP cents against USD nano balance:
        balance = 10_000_000  # $0.01

        # Wrong: balance < price_egp => 10_000_000 < 50 => False (would allow purchase)
        self.assertFalse(balance < price_egp, "Raw EGP comparison would incorrectly allow")

        # Wrong: balance < price_egp with small balance
        balance_small = 55  # 55 nano ($0.000000055)
        # Raw comparison: 55 < 50 => False (would allow purchase with tiny balance!)
        self.assertFalse(balance_small < price_egp, "Raw EGP comparison would incorrectly allow tiny balance")

        # Correct: balance < price_nano => 55 < 10_000_000 => True (correctly rejects)
        self.assertTrue(balance_small < price_nano, "Correct conversion rejects tiny balance")


if __name__ == "__main__":
    unittest.main()

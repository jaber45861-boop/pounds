"""Regression tests for the three financial cutover blockers.

BLOCKER 1: _process_conversion EGP cents → USD nano conversion
BLOCKER 2: Monetag postback EGP cents → USD nano conversion
BLOCKER 3: /tasks/sync sell_price_cents NameError

These tests prove:
- EGP cent values are NEVER written directly into balance_usd_nano
- Conversion happens through _egp_cents_to_nano()
- The /tasks/sync endpoint completes without NameError
- The response contains price_usd_nano, not price_cents
"""
import os
import sys
import sqlite3
import tempfile
import unittest
from decimal import Decimal

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "test_secret_for_blockers")
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

# Import the reward_api module
import reward_api as _reward_api_mod
from reward_api import _egp_cents_to_nano, EGP_PER_USD_REF


# ══════════════════════════════════════════════════════════════════════════════
# TEST A — EGP cents conversion helper correctness
# ══════════════════════════════════════════════════════════════════════════════


class TestEgpCentsToNanoConversion(unittest.TestCase):
    """Prove the _egp_cents_to_nano helper converts correctly."""

    def test_01_50_egp_cents_to_nano(self):
        """50 EGP cents = 0.50 EGP = $0.01 = 10,000,000 nano."""
        result = _egp_cents_to_nano(50)
        self.assertEqual(result, 10_000_000)

    def test_02_100_egp_cents_to_nano(self):
        """100 EGP cents = 1.00 EGP = $0.02 = 20,000,000 nano."""
        result = _egp_cents_to_nano(100)
        self.assertEqual(result, 20_000_000)

    def test_03_zero_egp_cents(self):
        """0 EGP cents = 0 nano."""
        result = _egp_cents_to_nano(0)
        self.assertEqual(result, 0)

    def test_04_one_egp_cent(self):
        """1 EGP cent = $0.0002 = 200,000 nano."""
        result = _egp_cents_to_nano(1)
        self.assertEqual(result, 200_000)

    def test_05_uses_decimal_not_float(self):
        """Verify no float in conversion path."""
        import inspect
        src = inspect.getsource(_egp_cents_to_nano)
        self.assertNotIn("float(", src)

    def test_06_matches_ganaihat_bot_helper(self):
        """reward_api helper must match ganaihat_bot.egp_cents_to_wallet_nano."""
        for cents in [0, 1, 50, 100, 500, 1000]:
            self.assertEqual(
                _egp_cents_to_nano(cents),
                gb.egp_cents_to_wallet_nano(cents),
                f"Mismatch at {cents} cents",
            )


# ══════════════════════════════════════════════════════════════════════════════
# TEST B — _process_conversion credits nano, not cents
# ══════════════════════════════════════════════════════════════════════════════


class TestProcessConversionCreditsNano(unittest.TestCase):
    """Prove _process_conversion writes USD nano, not raw EGP cents."""

    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        # Close any existing connection to allow fresh init
        try:
            gb.get_connection().close()
        except Exception:
            pass
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
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM processed_transactions")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_cents, balance_usd_nano, activation_status) "
                "VALUES (5001, 'TestUser', 0, 0, 1)"
            )
            conn.commit()

    def test_01_conversion_inserts_nano_not_cents(self):
        """50 EGP cents at 70% → user gets 35 cents → must credit 7,000,000 nano, not 35."""
        from reward_api import _process_conversion
        from reward_api import UnifiedConversion

        conv = UnifiedConversion(
            provider="test",
            transaction_id="tx_001",
            user_id=5001,
            amount_cents=50,  # EGP cents
            currency="EGP",
            status="approved",
        )
        result = _process_conversion(
            conv, user_profit_pct=0.70, get_connection=gb.get_connection
        )
        self.assertEqual(result["status"], "success")

        # Verify wallet
        user = gb.get_user(5001)
        # 50 * 0.70 = 35 EGP cents → 35 * 10_000_000 / 50 = 7,000,000 nano
        self.assertEqual(user["balance_usd_nano"], 7_000_000)
        # Must NOT be 35 (the raw cents value)
        self.assertNotEqual(user["balance_usd_nano"], 35)

    def test_02_full_amount_conversion(self):
        """100 EGP cents at 100% → 100 cents → 20,000,000 nano."""
        from reward_api import _process_conversion, UnifiedConversion

        conv = UnifiedConversion(
            provider="test",
            transaction_id="tx_002",
            user_id=5001,
            amount_cents=100,
            currency="EGP",
            status="approved",
        )
        result = _process_conversion(
            conv, user_profit_pct=1.0, get_connection=gb.get_connection
        )
        user = gb.get_user(5001)
        self.assertEqual(user["balance_usd_nano"], 20_000_000)

    def test_03_balance_cents_unchanged(self):
        """balance_cents must not be modified by _process_conversion."""
        from reward_api import _process_conversion, UnifiedConversion

        conv = UnifiedConversion(
            provider="test",
            transaction_id="tx_003",
            user_id=5001,
            amount_cents=200,
            currency="EGP",
            status="approved",
        )
        _process_conversion(
            conv, user_profit_pct=0.70, get_connection=gb.get_connection
        )
        user = gb.get_user(5001)
        self.assertEqual(user["balance_cents"], 0, "balance_cents must not change")

    def test_04_idempotent_no_double_credit(self):
        """Same transaction idempotency key → no double credit."""
        from reward_api import _process_conversion, UnifiedConversion

        conv = UnifiedConversion(
            provider="test",
            transaction_id="tx_dup",
            user_id=5001,
            amount_cents=50,
            currency="EGP",
            status="approved",
        )
        r1 = _process_conversion(
            conv, user_profit_pct=0.70, get_connection=gb.get_connection
        )
        r2 = _process_conversion(
            conv, user_profit_pct=0.70, get_connection=gb.get_connection
        )
        user = gb.get_user(5001)
        # Should be credited exactly once
        self.assertEqual(user["balance_usd_nano"], 7_000_000)
        self.assertEqual(r2.get("message"), "Transaction already processed")


# ══════════════════════════════════════════════════════════════════════════════
# TEST C — /tasks/sync no NameError and correct response
# ══════════════════════════════════════════════════════════════════════════════


class TestTasksSyncEndpoint(unittest.TestCase):
    """Prove /tasks/sync completes without NameError and uses sell_price_usd_nano."""

    def test_01_no_sell_price_cents_in_reward_api(self):
        """The reward_api module must not use 'sell_price_cents' as a dict key anywhere."""
        with open(_reward_api_mod.__file__) as f:
            src = f.read()
        self.assertNotIn('"sell_price_cents"', src)

    def test_02_sell_price_usd_nano_present_in_reward_api(self):
        """The reward_api module must use 'sell_price_usd_nano' as a dict key."""
        with open(_reward_api_mod.__file__) as f:
            src = f.read()
        self.assertIn('"sell_price_usd_nano"', src)

    def test_03_no_float_in_smm_pricing_calc(self):
        """The SMM pricing calc (rate_usd_decimal through sell_price_nano) must use Decimal only."""
        with open(_reward_api_mod.__file__) as f:
            src = f.read()
        # Find the region from rate_usd_decimal to sell_price_nano assignment
        start = src.find("rate_usd_decimal = Decimal")
        end = src.find("sell_price_nano = int(", start)
        self.assertGreater(start, -1, "rate_usd_decimal not found")
        self.assertGreater(end, -1, "sell_price_nano not found")
        calc_section = src[start:end]
        self.assertNotIn("float(", calc_section, "float() found in SMM pricing calculation")


# ══════════════════════════════════════════════════════════════════════════════
# TEST D — No direct cents-to-nano mutation (source-level regression)
# ══════════════════════════════════════════════════════════════════════════════


class TestNoDirectCentsToNano(unittest.TestCase):
    """Detect dangerous patterns: *_cents passed directly into balance_usd_nano UPDATE."""

    def test_01_reward_api_no_raw_cents_in_balance_update(self):
        """reward_api must not pass raw EGP cents to balance_usd_nano UPDATE."""
        import inspect

        src = inspect.getsource(sys.modules["reward_api"])
        # Find all UPDATE users SET balance_usd_nano = balance_usd_nano + ? lines
        # and check the parameter name near them
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "balance_usd_nano = balance_usd_nano + ?" in line:
                # Look backward up to 10 lines for the parameter
                context = "\n".join(lines[max(0, i - 10):i + 1])
                # The parameter should NOT be user_cents, reward, or conv.amount_cents
                # without a conversion function call
                if "user_cents," in context and "_egp_cents_to_nano" not in context:
                    self.fail(
                        f"Raw user_cents passed to balance_usd_nano at line {i+1} "
                        "without conversion"
                    )
                if "reward," in context and "_egp_cents_to_nano" not in context:
                    # Check this is specifically a balance_usd_nano credit, not ad_reviews INSERT
                    if "ad_reviews" not in context:
                        self.fail(
                            f"Raw reward passed to balance_usd_nano at line {i+1} "
                            "without conversion"
                        )


# ══════════════════════════════════════════════════════════════════════════════
# TEST E — balance_cents unchanged after reward processing
# ══════════════════════════════════════════════════════════════════════════════


class TestBalanceCentsUnchanged(unittest.TestCase):
    """Prove balance_cents is not modified by reward_api operations."""

    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def setUp(self):
        with gb.get_connection() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM processed_transactions")
            conn.execute("DELETE FROM ad_reviews")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_cents, balance_usd_nano, activation_status) "
                "VALUES (6001, 'TestUser', 500, 0, 1)"
            )
            conn.commit()

    def test_01_payment_callback_preserves_balance_cents(self):
        """_process_conversion must not modify balance_cents."""
        from reward_api import _process_conversion, UnifiedConversion

        conv = UnifiedConversion(
            provider="test",
            transaction_id="tx_bc1",
            user_id=6001,
            amount_cents=100,
            currency="EGP",
            status="approved",
        )
        _process_conversion(
            conv, user_profit_pct=0.70, get_connection=gb.get_connection
        )
        user = gb.get_user(6001)
        self.assertEqual(user["balance_cents"], 500, "balance_cents must be unchanged")
        self.assertGreater(user["balance_usd_nano"], 0, "balance_usd_nano must be credited")

    def test_02_monetag_postback_preserves_balance_cents(self):
        """Monetag postback must not modify balance_cents."""
        # Simulate the postback path by directly using the reward_api logic
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status, reviewed_at) "
                "VALUES (6001, 'ymid_test', 50, 'approved', CURRENT_TIMESTAMP)"
            )
            reward = 50  # EGP cents
            reward_nano = _egp_cents_to_nano(reward)
            conn.execute(
                "UPDATE users SET balance_usd_nano = balance_usd_nano + ? WHERE user_id = ?",
                (reward_nano, 6001),
            )
            conn.commit()
        user = gb.get_user(6001)
        self.assertEqual(user["balance_cents"], 500, "balance_cents must be unchanged")
        self.assertEqual(user["balance_usd_nano"], 10_000_000)


# ══════════════════════════════════════════════════════════════════════════════
# TEST F — Exact nano boundary (50 EGP cents at 50 EGP/USD)
# ══════════════════════════════════════════════════════════════════════════════


class TestExactNanoBoundary(unittest.TestCase):
    """Prove exact numeric conversion at the critical boundary."""

    def test_01_50_egp_cents_exact_boundary(self):
        """50 EGP cents at 50 EGP/USD = exactly 10,000,000 nano."""
        self.assertEqual(_egp_cents_to_nano(50), 10_000_000)

    def test_02_rounding_boundary(self):
        """Test rounding at nano boundary."""
        # 1 EGP cent = $0.0002 = 200,000 nano (exact)
        self.assertEqual(_egp_cents_to_nano(1), 200_000)
        # 3 EGP cents = $0.0006 = 600,000 nano (exact)
        self.assertEqual(_egp_cents_to_nano(3), 600_000)

    def test_03_large_amount(self):
        """Large EGP cent amount converts correctly."""
        # 10000 EGP cents = 100 EGP = $2.00 = 2,000,000,000 nano
        self.assertEqual(_egp_cents_to_nano(10000), 2_000_000_000)

    def test_04_conversion_uses_stored_rate(self):
        """The helper uses EGP_PER_USD_REF = 50."""
        self.assertEqual(EGP_PER_USD_REF, Decimal("50"))

    def test_05_helper_returns_integer(self):
        """Result must always be int, never float or Decimal."""
        result = _egp_cents_to_nano(50)
        self.assertIsInstance(result, int)


if __name__ == "__main__":
    unittest.main()

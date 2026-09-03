"""Tests for USD nano money primitives (Phase 1).

Covers:
- usd_decimal_to_nano / nano_to_usd_decimal
- EGP ↔ USD-nano conversion helpers
- format_usd_nano
- is_valid_usd_nano_amount / require_valid_usd_nano_amount
- Round-trip fidelity
- Existing EGP-cent helpers remain unchanged
"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP

# Safe test env vars
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import importlib.util

# Portable project-relative path
_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot",
    _BOT_FILE,
    submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod

gb = _mod

# ─── Convenience aliases ──────────────────────────────────────────────────────
usd_decimal_to_nano = gb.usd_decimal_to_nano
nano_to_usd_decimal = gb.nano_to_usd_decimal
egp_decimal_to_usd_nano = gb.egp_decimal_to_usd_nano
usd_nano_to_egp_decimal = gb.usd_nano_to_egp_decimal
format_usd_nano = gb.format_usd_nano
is_valid_usd_nano_amount = gb.is_valid_usd_nano_amount
require_valid_usd_nano_amount = gb.require_valid_usd_nano_amount
USD_NANO_SCALE = gb.USD_NANO_SCALE
USD_NANO_PER_USD = gb.USD_NANO_PER_USD
EGP_PER_USD = gb.EGP_PER_USD
MONEY_SCALE = gb.MONEY_SCALE


class TestUsdNanoConstants(unittest.TestCase):
    """Verify the core USD nano constants are correct."""

    def test_nano_per_usd(self):
        self.assertEqual(USD_NANO_PER_USD, 1_000_000_000)

    def test_nano_scale_decimal(self):
        self.assertEqual(USD_NANO_SCALE, Decimal("1000000000"))


class TestUsdDecimalToNano(unittest.TestCase):
    """USD → nano conversion tests."""

    def test_one_dollar(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("1")), 1_000_000_000)

    def test_ten_cents(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("0.10")), 100_000_000)

    def test_one_cent(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("0.01")), 10_000_000)

    def test_half_cent(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("0.005")), 5_000_000)

    def test_one_micro_dollar(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("0.000001")), 1_000)

    def test_one_nano_unit(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("0.000000001")), 1)

    def test_zero(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("0")), 0)

    def test_string_input(self):
        self.assertEqual(usd_decimal_to_nano("0.01"), 10_000_000)

    def test_integer_input(self):
        self.assertEqual(usd_decimal_to_nano(0), 0)
        self.assertEqual(usd_decimal_to_nano(1), 1_000_000_000)

    def test_large_value(self):
        self.assertEqual(usd_decimal_to_nano(Decimal("1000000")), 1_000_000_000_000_000)

    def test_round_half_up(self):
        # 0.0000000005 → should round up to 1
        self.assertEqual(usd_decimal_to_nano(Decimal("0.0000000005")), 1)
        # 0.00000000049 → should round down to 0
        self.assertEqual(usd_decimal_to_nano(Decimal("0.00000000049")), 0)

    def test_invalid_string_raises(self):
        with self.assertRaises(ValueError):
            usd_decimal_to_nano("not_a_number")

    def test_nan_raises(self):
        with self.assertRaises(ValueError):
            usd_decimal_to_nano(Decimal("NaN"))

    def test_infinity_raises(self):
        with self.assertRaises(ValueError):
            usd_decimal_to_nano(Decimal("Infinity"))
        with self.assertRaises(ValueError):
            usd_decimal_to_nano(Decimal("-Infinity"))

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            usd_decimal_to_nano(Decimal("-0.01"))

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            usd_decimal_to_nano("")


class TestNanoToUsdDecimal(unittest.TestCase):
    """nano → USD conversion tests."""

    def test_one_billion_is_one_dollar(self):
        self.assertEqual(nano_to_usd_decimal(1_000_000_000), Decimal("1"))

    def test_100_million_is_10_cents(self):
        self.assertEqual(nano_to_usd_decimal(100_000_000), Decimal("0.10"))

    def test_10_million_is_one_cent(self):
        self.assertEqual(nano_to_usd_decimal(10_000_000), Decimal("0.01"))

    def test_5_million_is_half_cent(self):
        self.assertEqual(nano_to_usd_decimal(5_000_000), Decimal("0.005"))

    def test_1000_is_micro_dollar(self):
        self.assertEqual(nano_to_usd_decimal(1_000), Decimal("0.000001"))

    def test_1_is_nano_unit(self):
        self.assertEqual(nano_to_usd_decimal(1), Decimal("0.000000001"))

    def test_zero(self):
        self.assertEqual(nano_to_usd_decimal(0), Decimal("0"))

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            nano_to_usd_decimal(-1)


class TestRoundTrip(unittest.TestCase):
    """USD → nano → USD round-trip fidelity."""

    def _check_round_trip(self, usd_str: str):
        nano = usd_decimal_to_nano(Decimal(usd_str))
        result = nano_to_usd_decimal(nano)
        self.assertEqual(result, Decimal(usd_str))

    def test_one_dollar(self):
        self._check_round_trip("1")

    def test_10_cents(self):
        self._check_round_trip("0.10")

    def test_1_cent(self):
        self._check_round_trip("0.01")

    def test_half_cent(self):
        self._check_round_trip("0.005")

    def test_micro_dollar(self):
        self._check_round_trip("0.000001")

    def test_nano_unit(self):
        self._check_round_trip("0.000000001")

    def test_complex_value(self):
        self._check_round_trip("0.0025")

    def test_large_value(self):
        self._check_round_trip("10000")


class TestEgpUsdNanoConversion(unittest.TestCase):
    """EGP ↔ USD-nano conversion tests."""

    def test_egp_50_to_usd_nano_at_rate_50(self):
        # 50 EGP / 50 = $1.00 = 1,000,000,000 nano
        nano = egp_decimal_to_usd_nano(Decimal("50"), rate=Decimal("50"))
        self.assertEqual(nano, 1_000_000_000)

    def test_egp_5_to_usd_nano_at_rate_50(self):
        # 5 EGP / 50 = $0.10 = 100,000,000 nano
        nano = egp_decimal_to_usd_nano(Decimal("5"), rate=Decimal("50"))
        self.assertEqual(nano, 100_000_000)

    def test_egp_0_50_to_usd_nano_at_rate_50(self):
        # 0.50 EGP / 50 = $0.01 = 10,000,000 nano
        nano = egp_decimal_to_usd_nano(Decimal("0.50"), rate=Decimal("50"))
        self.assertEqual(nano, 10_000_000)

    def test_usd_nano_to_egp_at_rate_50(self):
        # 1,000,000,000 nano = $1.00 × 50 = 50.00 EGP
        egp = usd_nano_to_egp_decimal(1_000_000_000, rate=Decimal("50"))
        self.assertEqual(egp, Decimal("50.00"))

    def test_usd_nano_to_egp_at_rate_51(self):
        # 100,000,000 nano = $0.10 × 51 = 5.10 EGP
        egp = usd_nano_to_egp_decimal(100_000_000, rate=Decimal("51"))
        self.assertEqual(egp, Decimal("5.10"))

    def test_egp_round_trip_at_rate_50(self):
        egp = Decimal("25")
        nano = egp_decimal_to_usd_nano(egp, rate=Decimal("50"))
        result = usd_nano_to_egp_decimal(nano, rate=Decimal("50"))
        self.assertEqual(result, egp)

    def test_egp_round_trip_at_rate_51(self):
        egp = Decimal("10.20")
        nano = egp_decimal_to_usd_nano(egp, rate=Decimal("51"))
        result = usd_nano_to_egp_decimal(nano, rate=Decimal("51"))
        # 10.20 / 51 = $0.20 = 200000000 nano → $0.20 × 51 = 10.20
        self.assertEqual(result, egp)

    def test_default_rate_is_50(self):
        # Without explicit rate, should use EGP_PER_USD = 50
        nano = egp_decimal_to_usd_nano(Decimal("50"))
        self.assertEqual(nano, 1_000_000_000)

    def test_egp_zero(self):
        nano = egp_decimal_to_usd_nano(Decimal("0"), rate=Decimal("50"))
        self.assertEqual(nano, 0)


class TestFormatUsdNano(unittest.TestCase):
    """USD nano formatting tests."""

    def test_format_one_cent(self):
        self.assertEqual(format_usd_nano(10_000_000), "$0.01")

    def test_format_half_cent(self):
        self.assertEqual(format_usd_nano(5_000_000), "$0.005")

    def test_format_micro_dollar(self):
        self.assertEqual(format_usd_nano(1_000), "$0.000001")

    def test_format_one_nano_unit(self):
        self.assertEqual(format_usd_nano(1), "$0.000000001")

    def test_format_one_dollar(self):
        self.assertEqual(format_usd_nano(1_000_000_000), "$1")

    def test_format_zero(self):
        self.assertEqual(format_usd_nano(0), "$0")

    def test_format_10_cents(self):
        self.assertEqual(format_usd_nano(100_000_000), "$0.1")

    def test_format_large(self):
        result = format_usd_nano(1_500_000_000)
        self.assertEqual(result, "$1.5")


class TestValidation(unittest.TestCase):
    """Validation primitive tests."""

    def test_valid_integer(self):
        self.assertTrue(is_valid_usd_nano_amount(10_000_000))

    def test_valid_decimal_string(self):
        self.assertTrue(is_valid_usd_nano_amount("0.01"))

    def test_valid_zero(self):
        self.assertTrue(is_valid_usd_nano_amount(0))

    def test_valid_decimal_object(self):
        self.assertTrue(is_valid_usd_nano_amount(Decimal("1.00")))

    def test_invalid_string(self):
        self.assertFalse(is_valid_usd_nano_amount("abc"))

    def test_invalid_nan(self):
        self.assertFalse(is_valid_usd_nano_amount(float("nan")))

    def test_invalid_inf(self):
        self.assertFalse(is_valid_usd_nano_amount(float("inf")))

    def test_invalid_neg_inf(self):
        self.assertFalse(is_valid_usd_nano_amount(float("-inf")))

    def test_invalid_none(self):
        self.assertFalse(is_valid_usd_nano_amount(None))

    def test_valid_float_zero(self):
        self.assertTrue(is_valid_usd_nano_amount(0.0))

    def test_overflow_raises(self):
        # SQLite max is 2^63 - 1 = 9223372036854775807
        # That's $9.22... — anything that nano-converts beyond that is invalid
        with self.assertRaises((ValueError, OverflowError)):
            require_valid_usd_nano_amount(Decimal("10000000000"))

    def test_require_valid_returns_int(self):
        result = require_valid_usd_nano_amount("0.01")
        self.assertEqual(result, 10_000_000)
        self.assertIsInstance(result, int)

    def test_require_valid_negative_raises(self):
        with self.assertRaises(ValueError):
            require_valid_usd_nano_amount(Decimal("-1"))

    def test_require_valid_nan_raises(self):
        with self.assertRaises(ValueError):
            require_valid_usd_nano_amount(Decimal("NaN"))


class TestExistingEgpCentHelpersUnchanged(unittest.TestCase):
    """Regression: existing EGP-cent helpers must remain identical."""

    def test_format_egp_basic(self):
        self.assertEqual(gb.format_egp(100), "1.00 جنيه")

    def test_format_egp_zero(self):
        self.assertEqual(gb.format_egp(0), "0.00 جنيه")

    def test_format_usd_basic(self):
        # 100 EGP cents = 1 EGP = $0.02 at EGP_PER_USD=50
        self.assertEqual(gb.format_usd(100), "$0.02")

    def test_format_balance_basic(self):
        # 100 EGP cents = 1 EGP = $0.02 at EGP_PER_USD=50
        self.assertEqual(gb.format_balance(100), "1.00 جنيه ($0.02)")

    def test_format_balance_zero(self):
        self.assertEqual(gb.format_balance(0), "0.00 جنيه ($0.00)")

    def test_parse_money_to_cents_basic(self):
        self.assertEqual(gb.parse_money_to_cents("1.50"), 150)

    def test_parse_money_to_cents_zero(self):
        self.assertIsNone(gb.parse_money_to_cents("0"))

    def test_parse_money_to_cents_invalid(self):
        self.assertIsNone(gb.parse_money_to_cents("abc"))

    def test_parse_currency_input_egp(self):
        self.assertEqual(gb.parse_currency_input("50"), 5000)

    def test_parse_currency_input_usd(self):
        # $1 = 50 EGP = 5000 cents
        self.assertEqual(gb.parse_currency_input("$1"), 5000)

    def test_calculate_selling_price_10_pounds(self):
        self.assertEqual(gb.calculate_selling_price(1000), 1300)

    def test_calculate_selling_price_zero(self):
        self.assertEqual(gb.calculate_selling_price(0), 0)

    def test_margin_multiplier(self):
        self.assertEqual(gb.MARGIN_MULTIPLIER, Decimal("1.30"))

    def test_egp_per_usd(self):
        self.assertEqual(EGP_PER_USD, Decimal("50"))

    def test_money_scale(self):
        self.assertEqual(MONEY_SCALE, Decimal("100"))


if __name__ == "__main__":
    unittest.main()

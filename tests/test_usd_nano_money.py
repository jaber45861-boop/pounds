"""Tests for USD nano money primitives (Phase 1 + boundary hardening).

Covers:
- usd_decimal_to_nano / nano_to_usd_decimal
- EGP ↔ USD-nano conversion helpers
- format_usd_nano
- is_valid_usd_nano_amount / require_valid_usd_nano_amount
- Round-trip fidelity
- Existing EGP-cent helpers remain unchanged
- FIX 1: strict float rejection in usd_decimal_to_nano
- FIX 2: strict integer-only in nano_to_usd_decimal
- FIX 3: rate validation in EGP/USD helpers
- FIX 4: format_usd_nano display convention
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

    # ── FIX 1: Strict float rejection ────────────────────────────────────────

    def test_float_rejected(self):
        """FIX 1: usd_decimal_to_nano must reject float input."""
        with self.assertRaises(TypeError):
            usd_decimal_to_nano(0.005)

    def test_float_zero_rejected(self):
        """FIX 1: even float(0.0) must be rejected."""
        with self.assertRaises(TypeError):
            usd_decimal_to_nano(0.0)

    def test_float_one_rejected(self):
        """FIX 1: float(1.0) must be rejected."""
        with self.assertRaises(TypeError):
            usd_decimal_to_nano(1.0)


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

    # ── FIX 2: Strict integer-only storage boundary ──────────────────────────

    def test_float_rejected(self):
        """FIX 2: nano_to_usd_decimal must reject float."""
        with self.assertRaises(TypeError):
            nano_to_usd_decimal(1.5)

    def test_float_zero_rejected(self):
        """FIX 2: even float(0.0) must be rejected."""
        with self.assertRaises(TypeError):
            nano_to_usd_decimal(0.0)

    def test_fractional_decimal_rejected(self):
        """FIX 2: Decimal(\"1.5\") must be rejected."""
        with self.assertRaises(ValueError):
            nano_to_usd_decimal(Decimal("1.5"))

    def test_fractional_string_rejected(self):
        """FIX 2: \"1.5\" must be rejected."""
        with self.assertRaises(ValueError):
            nano_to_usd_decimal("1.5")

    def test_none_rejected(self):
        """FIX 2: None must be rejected."""
        with self.assertRaises(ValueError):
            nano_to_usd_decimal(None)

    def test_nan_decimal_rejected(self):
        """FIX 2: Decimal(\"NaN\") must be rejected."""
        with self.assertRaises(ValueError):
            nano_to_usd_decimal(Decimal("NaN"))

    def test_inf_string_rejected(self):
        """FIX 2: \"Infinity\" must be rejected."""
        with self.assertRaises((ValueError, OverflowError)):
            nano_to_usd_decimal("Infinity")

    def test_integer_decimal_accepted(self):
        """FIX 2: Decimal(\"5\") (integer-valued) should be accepted."""
        self.assertEqual(nano_to_usd_decimal(Decimal("5")), Decimal("0.000000005"))

    def test_integer_string_accepted(self):
        """FIX 2: \"5\" (integer string) should be accepted."""
        self.assertEqual(nano_to_usd_decimal("5"), Decimal("0.000000005"))

    def test_empty_string_rejected(self):
        """FIX 2: empty string must be rejected."""
        with self.assertRaises(ValueError):
            nano_to_usd_decimal("")


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
        nano = egp_decimal_to_usd_nano(Decimal("50"), rate=Decimal("50"))
        self.assertEqual(nano, 1_000_000_000)

    def test_egp_5_to_usd_nano_at_rate_50(self):
        nano = egp_decimal_to_usd_nano(Decimal("5"), rate=Decimal("50"))
        self.assertEqual(nano, 100_000_000)

    def test_egp_0_50_to_usd_nano_at_rate_50(self):
        nano = egp_decimal_to_usd_nano(Decimal("0.50"), rate=Decimal("50"))
        self.assertEqual(nano, 10_000_000)

    def test_usd_nano_to_egp_at_rate_50(self):
        egp = usd_nano_to_egp_decimal(1_000_000_000, rate=Decimal("50"))
        self.assertEqual(egp, Decimal("50.00"))

    def test_usd_nano_to_egp_at_rate_51(self):
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
        self.assertEqual(result, egp)

    def test_default_rate_is_50(self):
        nano = egp_decimal_to_usd_nano(Decimal("50"))
        self.assertEqual(nano, 1_000_000_000)

    def test_egp_zero(self):
        nano = egp_decimal_to_usd_nano(Decimal("0"), rate=Decimal("50"))
        self.assertEqual(nano, 0)

    # ── FIX 3: Rate validation tests ─────────────────────────────────────────

    def test_rate_zero_rejected(self):
        """FIX 3: rate=0 must be rejected."""
        with self.assertRaises(ValueError):
            egp_decimal_to_usd_nano(Decimal("50"), rate=Decimal("0"))

    def test_rate_negative_rejected(self):
        """FIX 3: negative rate must be rejected."""
        with self.assertRaises(ValueError):
            egp_decimal_to_usd_nano(Decimal("50"), rate=Decimal("-50"))

    def test_rate_nan_rejected(self):
        """FIX 3: NaN rate must be rejected."""
        with self.assertRaises(ValueError):
            egp_decimal_to_usd_nano(Decimal("50"), rate=Decimal("NaN"))

    def test_rate_infinity_rejected(self):
        """FIX 3: Infinity rate must be rejected."""
        with self.assertRaises(ValueError):
            egp_decimal_to_usd_nano(Decimal("50"), rate=Decimal("Infinity"))

    def test_rate_zero_rejected_reverse(self):
        """FIX 3: rate=0 must be rejected in reverse conversion too."""
        with self.assertRaises(ValueError):
            usd_nano_to_egp_decimal(1_000_000_000, rate=Decimal("0"))

    def test_rate_negative_rejected_reverse(self):
        """FIX 3: negative rate must be rejected in reverse conversion."""
        with self.assertRaises(ValueError):
            usd_nano_to_egp_decimal(1_000_000_000, rate=Decimal("-50"))

    def test_rate_nan_rejected_reverse(self):
        """FIX 3: NaN rate must be rejected in reverse conversion."""
        with self.assertRaises(ValueError):
            usd_nano_to_egp_decimal(1_000_000_000, rate=Decimal("NaN"))

    def test_rate_infinity_rejected_reverse(self):
        """FIX 3: Infinity rate must be rejected in reverse conversion."""
        with self.assertRaises(ValueError):
            usd_nano_to_egp_decimal(1_000_000_000, rate=Decimal("Infinity"))

    def test_rate_decimal_accepted(self):
        """FIX 3: a valid Decimal rate like 48.75 should work."""
        nano = egp_decimal_to_usd_nano(Decimal("48.75"), rate=Decimal("48.75"))
        self.assertEqual(nano, 1_000_000_000)

    def test_rate_float_rejected(self):
        """FIX 3: float rate must be rejected (via Decimal conversion)."""
        with self.assertRaises((ValueError, TypeError)):
            egp_decimal_to_usd_nano(Decimal("50"), rate=50.0)


class TestFormatUsdNano(unittest.TestCase):
    """USD nano formatting tests — FIX 4 display convention."""

    # ── Values >= $0.01: exactly two decimal places ──────────────────────────

    def test_format_one_cent(self):
        self.assertEqual(format_usd_nano(10_000_000), "$0.01")

    def test_format_10_cents(self):
        self.assertEqual(format_usd_nano(100_000_000), "$0.10")

    def test_format_one_dollar(self):
        self.assertEqual(format_usd_nano(1_000_000_000), "$1.00")

    def test_format_large(self):
        self.assertEqual(format_usd_nano(125_300_000_000), "$125.30")

    def test_format_1_50(self):
        self.assertEqual(format_usd_nano(1_500_000_000), "$1.50")

    # ── Values > 0 and < $0.01: exact representation, no trailing zeros ──────

    def test_format_half_cent(self):
        self.assertEqual(format_usd_nano(5_000_000), "$0.005")

    def test_format_micro_dollar(self):
        self.assertEqual(format_usd_nano(1_000), "$0.000001")

    def test_format_one_nano_unit(self):
        self.assertEqual(format_usd_nano(1), "$0.000000001")

    def test_format_25_cents_subcent(self):
        # 2,500,000 nano = $0.0025
        self.assertEqual(format_usd_nano(2_500_000), "$0.0025")

    # ── Zero ──────────────────────────────────────────────────────────────────

    def test_format_zero(self):
        self.assertEqual(format_usd_nano(0), "$0.00")


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
        self.assertEqual(gb.format_usd(100), "$0.02")

    def test_format_balance_basic(self):
        self.assertEqual(gb.format_balance(100_000_000), "$0.10")

    def test_format_balance_zero(self):
        self.assertEqual(gb.format_balance(0), "$0.00")

    def test_parse_money_to_cents_basic(self):
        self.assertEqual(gb.parse_money_to_cents("1.50"), 150)

    def test_parse_money_to_cents_zero(self):
        self.assertIsNone(gb.parse_money_to_cents("0"))

    def test_parse_money_to_cents_invalid(self):
        self.assertIsNone(gb.parse_money_to_cents("abc"))

    def test_parse_currency_input_egp(self):
        self.assertEqual(gb.parse_currency_input("50"), 5000)

    def test_parse_currency_input_usd(self):
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


# ══════════════════════════════════════════════════════════════════════════════
# ─── Phase 2: USD Nano Pricing / Domain Primitives ────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

parse_money_to_usd_nano = gb.parse_money_to_usd_nano
calculate_usd_nano_selling_price = gb.calculate_usd_nano_selling_price
smm_rate_to_usd_nano_per_unit = gb.smm_rate_to_usd_nano_per_unit
smm_sell_price_usd_nano = gb.smm_sell_price_usd_nano
has_minimum_usd_nano_balance = gb.has_minimum_usd_nano_balance
TASK_CREATION_MIN_BALANCE_USD_NANO = gb.TASK_CREATION_MIN_BALANCE_USD_NANO
MARGIN_MULTIPLIER_LOCAL = gb.MARGIN_MULTIPLIER


class TestParseMoneyToUsdNano(unittest.TestCase):
    """USD money parsing into nano-units (PART E)."""

    def test_one_dollar(self):
        self.assertEqual(parse_money_to_usd_nano(Decimal("1")), 1_000_000_000)

    def test_one_cent(self):
        self.assertEqual(parse_money_to_usd_nano(Decimal("0.01")), 10_000_000)

    def test_half_cent(self):
        self.assertEqual(parse_money_to_usd_nano(Decimal("0.005")), 5_000_000)

    def test_one_micro_dollar(self):
        self.assertEqual(parse_money_to_usd_nano(Decimal("0.000001")), 1_000)

    def test_one_nano_unit(self):
        self.assertEqual(parse_money_to_usd_nano(Decimal("0.000000001")), 1)

    def test_string_input(self):
        self.assertEqual(parse_money_to_usd_nano("0.01"), 10_000_000)

    def test_zero(self):
        self.assertEqual(parse_money_to_usd_nano(Decimal("0")), 0)

    def test_float_rejected(self):
        with self.assertRaises(TypeError):
            parse_money_to_usd_nano(0.01)

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            parse_money_to_usd_nano(Decimal("NaN"))

    def test_infinity_rejected(self):
        with self.assertRaises(ValueError):
            parse_money_to_usd_nano(Decimal("Infinity"))


class TestUsdNanoSellingPrice(unittest.TestCase):
    """USD nano selling price calculation (PART D)."""

    def test_one_dollar_times_1_30(self):
        # $1.00 × 1.30 = $1.30 = 1_300_000_000 nano
        self.assertEqual(
            calculate_usd_nano_selling_price(1_000_000_000),
            1_300_000_000,
        )

    def test_one_cent_times_1_30(self):
        # $0.01 × 1.30 = $0.013 = 13_000_000 nano
        self.assertEqual(
            calculate_usd_nano_selling_price(10_000_000),
            13_000_000,
        )

    def test_half_cent_times_1_30(self):
        # $0.005 × 1.30 = $0.0065 = 6_500_000 nano
        self.assertEqual(
            calculate_usd_nano_selling_price(5_000_000),
            6_500_000,
        )

    def test_zero_base(self):
        self.assertEqual(calculate_usd_nano_selling_price(0), 0)

    def test_large_value(self):
        # $100 × 1.30 = $130 = 130_000_000_000 nano
        self.assertEqual(
            calculate_usd_nano_selling_price(100_000_000_000),
            130_000_000_000,
        )

    def test_margin_multiplier_is_1_30(self):
        self.assertEqual(MARGIN_MULTIPLIER_LOCAL, Decimal("1.30"))

    def test_no_float_arithmetic(self):
        """Verify the result is an integer nano amount, not a float."""
        result = calculate_usd_nano_selling_price(5_000_000)
        self.assertIsInstance(result, int)
        self.assertEqual(result, 6_500_000)


class TestSmmRateToUsdNano(unittest.TestCase):
    """SMM provider rate conversion (PART C)."""

    def test_rate_5_per_1k(self):
        # $5 per 1000 = $0.005 per unit = 5_000_000 nano
        self.assertEqual(
            smm_rate_to_usd_nano_per_unit(Decimal("5")),
            5_000_000,
        )

    def test_rate_string(self):
        self.assertEqual(
            smm_rate_to_usd_nano_per_unit("0.50"),
            500_000,
        )

    def test_rate_zero(self):
        self.assertEqual(smm_rate_to_usd_nano_per_unit(Decimal("0")), 0)

    def test_rate_negative_rejected(self):
        with self.assertRaises(ValueError):
            smm_rate_to_usd_nano_per_unit(Decimal("-5"))

    def test_rate_float_rejected(self):
        with self.assertRaises(TypeError):
            smm_rate_to_usd_nano_per_unit(5.0)

    def test_rate_nan_rejected(self):
        with self.assertRaises(ValueError):
            smm_rate_to_usd_nano_per_unit(Decimal("NaN"))

    def test_rate_infinity_rejected(self):
        with self.assertRaises(ValueError):
            smm_rate_to_usd_nano_per_unit(Decimal("Infinity"))


class TestTaskCreationEligibility(unittest.TestCase):
    """Task-creation balance eligibility primitive (PART F)."""

    def test_zero_balance(self):
        self.assertFalse(has_minimum_usd_nano_balance(0))

    def test_below_threshold(self):
        self.assertFalse(has_minimum_usd_nano_balance(9_999_999))

    def test_exact_threshold(self):
        self.assertTrue(has_minimum_usd_nano_balance(10_000_000))

    def test_above_threshold(self):
        self.assertTrue(has_minimum_usd_nano_balance(10_000_001))

    def test_large_balance(self):
        self.assertTrue(has_minimum_usd_nano_balance(1_000_000_000_000))

    def test_threshold_is_10_million(self):
        self.assertEqual(TASK_CREATION_MIN_BALANCE_USD_NANO, 10_000_000)


class TestSmmSellPriceUsdNano(unittest.TestCase):
    """Full SMM sell price in USD nano (PART C combined)."""

    def test_rate_5_margin_30(self):
        # rate $5/1k, margin 30%: cost=$0.005/unit, sell=$0.005/(1-0.30)=$0.00714...
        nano = smm_sell_price_usd_nano(Decimal("5"), margin_pct=Decimal("30"))
        cost_usd = Decimal("5") / Decimal("1000")
        expected_usd = cost_usd / (Decimal("1") - Decimal("0.30"))
        expected_nano = gb.usd_decimal_to_nano(expected_usd)
        self.assertEqual(nano, expected_nano)

    def test_rate_float_rejected(self):
        with self.assertRaises(TypeError):
            smm_sell_price_usd_nano(5.0)

    def test_margin_float_rejected(self):
        with self.assertRaises(TypeError):
            smm_sell_price_usd_nano(Decimal("5"), margin_pct=30.0)


class TestPrecisionInvariants(unittest.TestCase):
    """Precision invariant tests (PART M)."""

    def test_no_fractional_nano_output(self):
        """All nano outputs must be exact integers."""
        self.assertIsInstance(usd_decimal_to_nano(Decimal("0.005")), int)
        self.assertIsInstance(usd_decimal_to_nano(Decimal("0.001")), int)
        self.assertIsInstance(usd_decimal_to_nano(Decimal("0.0001")), int)
        self.assertIsInstance(calculate_usd_nano_selling_price(5_000_000), int)

    def test_round_half_up_boundary(self):
        # Exactly halfway between nano units: 0.0000000005 → rounds UP to 1
        self.assertEqual(usd_decimal_to_nano(Decimal("0.0000000005")), 1)
        # Just below: 0.00000000049 → rounds DOWN to 0
        self.assertEqual(usd_decimal_to_nano(Decimal("0.00000000049")), 0)

    def test_sub_cent_selling_price_preserved(self):
        """$0.005 × 1.30 must not lose precision."""
        nano = calculate_usd_nano_selling_price(5_000_000)
        # $0.005 × 1.30 = $0.0065 = 6,500,000 nano
        self.assertEqual(nano, 6_500_000)
        usd_back = gb.nano_to_usd_decimal(nano)
        self.assertEqual(usd_back, Decimal("0.0065"))

    def test_no_float_in_pricing(self):
        """Verify Decimal-only arithmetic by checking result types."""
        self.assertIsInstance(calculate_usd_nano_selling_price(10_000_000), int)
        self.assertIsInstance(smm_rate_to_usd_nano_per_unit(Decimal("5")), int)
        self.assertIsInstance(parse_money_to_usd_nano(Decimal("0.01")), int)

    def test_overflow_protection(self):
        """Values exceeding SQLite INTEGER max must be caught."""
        with self.assertRaises((ValueError, OverflowError)):
            require_valid_usd_nano_amount(Decimal("10000000000"))


if __name__ == "__main__":
    unittest.main()

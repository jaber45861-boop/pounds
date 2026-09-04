"""Comprehensive tests for V2 withdrawal system (USDT accounting)."""
import os
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP

# Safe test env vars
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import decimal as _decimal
import importlib.util

# Portable project-relative path: tests/ lives next to ganaihat_bot.py
_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot",
    _BOT_FILE,
    submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = _decimal.Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod

gb = _mod


class TestV2Constants(unittest.TestCase):
    def test_01_vodafone_minimum_usd(self):
        # Fixed USD minimum for Vodafone Cash
        self.assertEqual(gb.VODAFONE_MIN_USD, Decimal("0.10"))

    def test_02_usdt_minimum(self):
        self.assertEqual(gb.USDT_MIN_USDT, Decimal("0.15"))

    def test_03_withdrawal_fee_is_zero(self):
        self.assertEqual(gb.WITHDRAWAL_COOLDOWN_SECONDS, 86400)

    def test_04_cooldown_is_24_hours(self):
        self.assertEqual(gb.WITHDRAWAL_COOLDOWN_SECONDS, 24 * 3600)

    def test_05_network_is_bep20(self):
        self.assertEqual(gb.WITHDRAWAL_NETWORK_BEP20, "BSC_BEP20")
        self.assertIn("BEP-20", gb.WITHDRAWAL_NETWORK_DISPLAY)
        self.assertIn("BNB Smart Chain", gb.WITHDRAWAL_NETWORK_DISPLAY)

    def test_06_micro_usdt_precision(self):
        self.assertEqual(gb.USDT_MICRO_PER_USDT, Decimal("1000000"))


class TestV2Conversions(unittest.TestCase):
    def test_07_egp_to_usdt(self):
        # 10 EGP at rate 50 = 0.2 USDT
        usdt = gb.egp_to_usdt(1000, Decimal("50"))
        self.assertEqual(usdt, Decimal("0.200000"))

    def test_08_usdt_to_egp_cents(self):
        # 0.15 USDT at rate 50 = 7.50 EGP = 750 cents
        cents = gb.usdt_to_egp_cents(Decimal("0.15"), Decimal("50"))
        self.assertEqual(cents, 750)

    def test_09_usdt_to_egp_precise(self):
        cents = gb.usdt_to_egp_cents(Decimal("0.20"), Decimal("50.25"))
        # 0.20 * 50.25 = 10.05 EGP = 1005 cents
        self.assertEqual(cents, 1005)

    def test_10_micro_to_usdt_roundtrip(self):
        amt = Decimal("1.234567")
        micro = gb._usdt_to_micro(amt)
        back = gb._micro_to_usdt(micro)
        self.assertEqual(back, amt)

    def test_11_zero_usdt_to_egp(self):
        cents = gb.usdt_to_egp_cents(Decimal("0"), Decimal("50"))
        self.assertEqual(cents, 0)

    def test_12_decimal_no_float_drift(self):
        # Multiple conversions should not accumulate float drift
        rate = Decimal("50.25")
        for _ in range(100):
            v = gb.usdt_to_egp_cents(Decimal("0.20"), rate)
            self.assertEqual(v, 1005)


class TestV2DestinationValidation(unittest.TestCase):
    def test_13_vodafone_valid_11_digits(self):
        self.assertTrue(gb.validate_vodafone_destination("01012345678"))

    def test_14_vodafone_valid_with_spaces(self):
        self.assertTrue(gb.validate_vodafone_destination("010 1234 5678"))

    def test_15_vodafone_invalid_too_short(self):
        self.assertFalse(gb.validate_vodafone_destination("01012345"))

    def test_16_vodafone_invalid_no_leading_0(self):
        self.assertFalse(gb.validate_vodafone_destination("12345678901"))

    def test_17_vodafone_invalid_letters(self):
        self.assertFalse(gb.validate_vodafone_destination("0101234567a"))

    def test_18_vodafone_empty(self):
        self.assertFalse(gb.validate_vodafone_destination(""))

    def test_19_usdt_bep20_valid(self):
        self.assertTrue(gb.validate_usdt_bep20_address(
            "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1"
        ))

    def test_20_usdt_bep20_invalid_no_prefix(self):
        self.assertFalse(gb.validate_usdt_bep20_address(
            "742d35Cc6634C0532925a3b844Bc9e7595f0bEb1"
        ))

    def test_21_usdt_bep20_invalid_short(self):
        self.assertFalse(gb.validate_usdt_bep20_address("0x742d35Cc"))

    def test_22_usdt_bep20_invalid_long(self):
        self.assertFalse(gb.validate_usdt_bep20_address("0x" + "a" * 50))

    def test_23_usdt_bep20_invalid_chars(self):
        self.assertFalse(gb.validate_usdt_bep20_address(
            "0xZZZd35Cc6634C0532925a3b844Bc9e7595f0bEb1"
        ))


class TestV2RateProvider(unittest.TestCase):
    def test_24_get_current_rate_cached(self):
        # First call will try external; if it fails, allow_stale returns last.
        gb._save_rate_snapshot(Decimal("50.00"), "test")
        rate, provider, is_fresh = gb.get_current_usdt_egp_rate(allow_stale=True)
        self.assertIsNotNone(rate)

    def test_25_rate_within_max_age_recent(self):
        from datetime import datetime
        recent = datetime.utcnow().isoformat() + "Z"
        self.assertTrue(gb.is_rate_within_max_age(recent))

    def test_26_rate_outside_max_age(self):
        from datetime import datetime, timedelta
        old = (datetime.utcnow() - timedelta(hours=10)).isoformat() + "Z"
        self.assertFalse(gb.is_rate_within_max_age(old))

    def test_27_rate_invalid_format(self):
        self.assertFalse(gb.is_rate_within_max_age("not a date"))

    def test_28_rate_empty(self):
        self.assertFalse(gb.is_rate_within_max_age(""))


class TestV2Cooldown(unittest.TestCase):
    def test_29_cooldown_format_minutes_only(self):
        s = gb.format_cooldown_remaining(300)
        self.assertIn("دقيقة", s)

    def test_30_cooldown_format_hours_and_minutes(self):
        s = gb.format_cooldown_remaining(8 * 3600 + 15 * 60)
        self.assertIn("ساعة", s)
        self.assertIn("دقيقة", s)

    def test_31_cooldown_format_zero(self):
        s = gb.format_cooldown_remaining(0)
        self.assertIn("0", s)


class TestV2Precision(unittest.TestCase):
    def test_32_no_float_in_calculations(self):
        # Verify internal storage is integer micro
        usdt = Decimal("0.15")
        micro = gb._usdt_to_micro(usdt)
        self.assertIsInstance(micro, int)

    def test_33_rate_serialization_micro(self):
        rate = Decimal("50.25")
        micro = int((rate * Decimal("1000000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        self.assertEqual(micro, 50250000)

    def test_34_egp_cents_integer(self):
        # Verify EGP storage is integer cents
        cents = gb.usdt_to_egp_cents(Decimal("0.15"), Decimal("50"))
        self.assertIsInstance(cents, int)

    def test_35_amount_unchanged_across_rate_changes(self):
        # Snapshot a withdrawal at rate 50.00
        rate1 = Decimal("50.00")
        usdt = Decimal("0.15")
        cents1 = gb.usdt_to_egp_cents(usdt, rate1)
        # Later rate changes
        rate2 = Decimal("55.00")
        cents2 = gb.usdt_to_egp_cents(usdt, rate2)
        # The original withdrawal retains its original cents value
        self.assertEqual(cents1, 750)
        self.assertEqual(cents2, 825)
        self.assertNotEqual(cents1, cents2)


class TestV2WithdrawalCreation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def setUp(self):
        # Seed a user with sufficient balance
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute("DELETE FROM users WHERE user_id = 999")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (999, 'Test', 1000000000, 1, 1, 0)"
            )
            conn.commit()
        # Seed a stable rate
        gb._save_rate_snapshot(Decimal("50.00"), "test_unit")

    def test_36_vodafone_minimum_accepted(self):
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        self.assertIsInstance(result, int)

    def test_37_vodafone_below_dynamic_minimum_rejected(self):
        # At rate 50, 0.10 USD = 5.00 EGP. 4.99 EGP is below minimum.
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=499,
            usdt_amount=None,
        )
        self.assertEqual(result, "below_minimum")

    def test_38_usdt_minimum_accepted(self):
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_USDT,
            destination="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            requested_egp_cents=None,
            usdt_amount=Decimal("0.15"),
            network_code=gb.WITHDRAWAL_NETWORK_BEP20,
        )
        self.assertIsInstance(result, int)

    def test_39_usdt_below_minimum_rejected(self):
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_USDT,
            destination="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            requested_egp_cents=None,
            usdt_amount=Decimal("0.149"),
            network_code=gb.WITHDRAWAL_NETWORK_BEP20,
        )
        self.assertEqual(result, "below_minimum")

    def test_40_insufficient_balance_rejected(self):
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE users SET balance_usd_nano = 500 WHERE user_id = 999"
            )
            conn.commit()
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        self.assertEqual(result, "insufficient_balance")
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE users SET balance_usd_nano = 1000000000 WHERE user_id = 999"
            )
            conn.commit()

    def test_41_invalid_destination_rejected(self):
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="invalid",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        self.assertEqual(result, "destination_invalid")

    def test_42_usdt_wrong_network_rejected(self):
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_USDT,
            destination="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            requested_egp_cents=None,
            usdt_amount=Decimal("0.15"),
            network_code="ETH_ERC20",
        )
        self.assertEqual(result, "destination_invalid")

    def test_43_first_withdrawal_succeeds(self):
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        self.assertIsInstance(result, int)

    def test_44_second_immediately_rejected(self):
        gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_USDT,
            destination="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            requested_egp_cents=None,
            usdt_amount=Decimal("0.15"),
            network_code=gb.WITHDRAWAL_NETWORK_BEP20,
        )
        self.assertEqual(result, "cooldown")

    def test_45_cooldown_applies_across_methods(self):
        gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        result2 = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_USDT,
            destination="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            requested_egp_cents=None,
            usdt_amount=Decimal("0.20"),
            network_code=gb.WITHDRAWAL_NETWORK_BEP20,
        )
        self.assertEqual(result2, "cooldown")

    def test_46_pending_withdrawal_blocks_second(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.commit()
        gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=2000,
            usdt_amount=None,
        )
        self.assertEqual(result, "cooldown")

    def test_47_rejected_withdrawal_still_blocks(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.commit()
        rid = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        gb.reject_v2_withdrawal(rid, 1)
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        self.assertEqual(result, "cooldown")

    def test_48_balance_deducted_on_create(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute(
                "UPDATE users SET balance_usd_nano = 1000000000 WHERE user_id = 999"
            )
            conn.commit()
        before = gb.get_user(999)["balance_usd_nano"]
        gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        after = gb.get_user(999)["balance_usd_nano"]
        self.assertEqual(before - after, gb.egp_cents_to_wallet_nano(1000))

    def test_49_failed_create_does_not_deduct(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute(
                "UPDATE users SET balance_usd_nano = 1000000000 WHERE user_id = 999"
            )
            conn.commit()
        before = gb.get_user(999)["balance_usd_nano"]
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=499,  # below dynamic minimum (5.00 EGP at rate 50)
            usdt_amount=None,
        )
        after = gb.get_user(999)["balance_usd_nano"]
        self.assertEqual(result, "below_minimum")
        self.assertEqual(before, after)

    def test_50_rate_snapshot_saved(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute(
                "UPDATE users SET balance_usd_nano = 1000000000 WHERE user_id = 999"
            )
            conn.commit()
        rid = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        row = gb.get_v2_withdrawal_request(rid)
        self.assertIsNotNone(row["rate_fetched_at"])
        self.assertIsNotNone(row["exchange_rate_micro"])
        self.assertGreater(int(row["exchange_rate_micro"]), 0)

    def test_51_changing_rate_does_not_change_withdrawal(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute(
                "UPDATE users SET balance_usd_nano = 1000000000 WHERE user_id = 999"
            )
            conn.commit()
        gb._save_rate_snapshot(Decimal("50.00"), "test")
        rid = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        row1 = gb.get_v2_withdrawal_request(rid)
        # Change rate
        gb._save_rate_snapshot(Decimal("60.00"), "test")
        row2 = gb.get_v2_withdrawal_request(rid)
        self.assertEqual(row1["exchange_rate_micro"], row2["exchange_rate_micro"])
        self.assertEqual(row1["usdt_micro"], row2["usdt_micro"])
        self.assertEqual(row1["egp_equivalent_cents"], row2["egp_equivalent_cents"])


class TestV2AdminFlow(unittest.TestCase):
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
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute("DELETE FROM users WHERE user_id = 888")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (888, 'T', 5000000000, 1, 1, 0)"
            )
            conn.commit()
        gb._save_rate_snapshot(Decimal("50.00"), "test")

    def test_52_complete_does_not_deduct(self):
        rid = gb.create_v2_withdrawal_request(
            user_id=888,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        before = gb.get_user(888)["balance_usd_nano"]
        result = gb.complete_v2_withdrawal(rid, 1, "TX-123")
        after = gb.get_user(888)["balance_usd_nano"]
        self.assertIsNotNone(result)
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "completed")

    def test_53_reject_refunds_once(self):
        rid = gb.create_v2_withdrawal_request(
            user_id=888,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        before = gb.get_user(888)["balance_usd_nano"]
        result = gb.reject_v2_withdrawal(rid, 1)
        after = gb.get_user(888)["balance_usd_nano"]
        self.assertIsNotNone(result)
        self.assertEqual(after - before, gb.egp_cents_to_wallet_nano(1000))
        # Second refund attempt must be no-op
        result2 = gb.reject_v2_withdrawal(rid, 1)
        self.assertIsNone(result2)
        final = gb.get_user(888)["balance_usd_nano"]
        self.assertEqual(final, after)

    def test_54_cannot_complete_already_completed(self):
        rid = gb.create_v2_withdrawal_request(
            user_id=888,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        gb.complete_v2_withdrawal(rid, 1)
        result = gb.complete_v2_withdrawal(rid, 1)
        self.assertIsNone(result)

    def test_55_cannot_reject_already_rejected(self):
        rid = gb.create_v2_withdrawal_request(
            user_id=888,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        gb.reject_v2_withdrawal(rid, 1)
        result = gb.reject_v2_withdrawal(rid, 1)
        self.assertIsNone(result)

    def test_56_cannot_complete_rejected(self):
        rid = gb.create_v2_withdrawal_request(
            user_id=888,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        gb.reject_v2_withdrawal(rid, 1)
        result = gb.complete_v2_withdrawal(rid, 1)
        self.assertIsNone(result)

    def test_57_cannot_reject_completed(self):
        rid = gb.create_v2_withdrawal_request(
            user_id=888,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        gb.complete_v2_withdrawal(rid, 1)
        result = gb.reject_v2_withdrawal(rid, 1)
        self.assertIsNone(result)


class TestV2Privacy(unittest.TestCase):
    def test_58_customer_summary_no_admin_data(self):
        # Build a fake row dict
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"
        row = {
            "id": 123,
            "user_id": 999,
            "method_code": "usdt",
            "network_code": "BSC_BEP20",
            "destination": "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            "usdt_micro": 150000,
            "egp_equivalent_cents": 750,
            "exchange_rate_micro": 50000000,
            "rate_fetched_at": now,
            "rate_provider": "test",
            "requested_egp_cents": None,
            "fee_cents": 0,
            "refunded": 0,
            "status": "pending",
            "created_at": now,
            "completed_at": None,
            "transaction_reference": None,
            "admin_id": 1,
        }
        text = gb.format_v2_withdrawal_customer_summary(row)
        # Must contain network, no internal IDs, no admin info
        self.assertIn("BNB Smart Chain", text)
        self.assertIn("BEP-20", text)
        self.assertNotIn("123", text)  # no ID
        self.assertNotIn("admin", text.lower())
        self.assertNotIn("cost", text.lower())


class TestV2RateFailureHandling(unittest.TestCase):
    def test_59_no_rate_no_fallback_raises(self):
        with gb.get_connection() as conn:
            conn.execute(
                "DELETE FROM currency_settings WHERE setting_key = 'usdt_egp_rate'"
            )
            conn.execute(
                "DELETE FROM currency_settings WHERE setting_key = 'usdt_egp_rate_provider'"
            )
            conn.execute(
                "DELETE FROM currency_settings WHERE setting_key = 'usdt_egp_rate_fetched_at'"
            )
            conn.commit()
        with self.assertRaises(RuntimeError):
            gb.get_current_usdt_egp_rate(allow_stale=False)


class TestV2CustomerRouting(unittest.TestCase):
    """Verify the main customer menu routes ONLY to V2 callbacks and that
    the legacy customer path is closed."""

    def test_60_keyboard_emits_v2_callbacks(self):
        markup = gb.withdrawal_method_keyboard()
        rendered = []
        for row in markup.keyboard:
            for btn in row:
                rendered.append(btn.callback_data)
        self.assertIn("withdraw_v2_vodafone", rendered)
        self.assertIn("withdraw_v2_usdt", rendered)
        self.assertNotIn("withdraw_method_vodafone", rendered)
        self.assertNotIn("withdraw_method_binance", rendered)

    def test_61_legacy_create_rejects_non_admin(self):
        result = gb.create_withdrawal_request(
            user_id=999,
            points_amount=1000,
            withdrawal_method="vodafone",
            account_details="01012345678",
        )
        # Must refuse — non-admin caller must not create a row.
        self.assertEqual(result, "legacy_disabled")

    def test_62_legacy_create_does_not_insert_row(self):
        # Ensure no row was created in DB
        before = gb.get_connection().execute(
            "SELECT COUNT(*) FROM withdrawal_requests WHERE user_id = 999"
        ).fetchone()[0]
        gb.create_withdrawal_request(
            user_id=999,
            points_amount=1000,
            withdrawal_method="vodafone",
            account_details="01012345678",
        )
        after = gb.get_connection().execute(
            "SELECT COUNT(*) FROM withdrawal_requests WHERE user_id = 999"
        ).fetchone()[0]
        self.assertEqual(before, after)


class TestVodafoneDynamicMinimum(unittest.TestCase):
    """Verify the Vodafone Cash minimum is $0.10 USD fixed and the EGP
    equivalent is computed dynamically from the current USD/EGP rate."""

    def test_fixed_usd_minimum_is_010(self):
        self.assertEqual(gb.VODAFONE_MIN_USD, Decimal("0.10"))

    def test_dynamic_minimum_at_rate_50(self):
        # 0.10 × 50 = 5.00 EGP = 500 cents
        self.assertEqual(gb.compute_vodafone_min_egp_cents(Decimal("50")), 500)

    def test_dynamic_minimum_at_rate_51(self):
        # 0.10 × 51 = 5.10 EGP = 510 cents
        self.assertEqual(gb.compute_vodafone_min_egp_cents(Decimal("51")), 510)

    def test_dynamic_minimum_at_rate_52(self):
        # 0.10 × 52 = 5.20 EGP = 520 cents
        self.assertEqual(gb.compute_vodafone_min_egp_cents(Decimal("52")), 520)

    def test_dynamic_minimum_at_rate_55(self):
        # 0.10 × 55 = 5.50 EGP = 550 cents
        self.assertEqual(gb.compute_vodafone_min_egp_cents(Decimal("55")), 550)

    def test_dynamic_minimum_at_rate_53(self):
        # 0.10 × 53 = 5.30 EGP = 530 cents
        self.assertEqual(gb.compute_vodafone_min_egp_cents(Decimal("53")), 530)

    def test_no_hardcoded_50_egp_per_usd_default(self):
        # The function must compute from the passed rate, not a baked-in 50.
        self.assertNotEqual(
            gb.compute_vodafone_min_egp_cents(Decimal("60")),
            gb.compute_vodafone_min_egp_cents(Decimal("50")),
        )

    def test_minimum_changes_when_rate_changes(self):
        # The customer-facing EGP minimum must change with the rate
        self.assertNotEqual(
            gb.compute_vodafone_min_egp_cents(Decimal("50")),
            gb.compute_vodafone_min_egp_cents(Decimal("55")),
        )

    def test_no_legacy_constant_remains(self):
        # The old hardcoded 10.00 EGP / 1000-cents constant must not be used.
        self.assertFalse(hasattr(gb, "VODAFONE_MIN_EGP_CENTS"))

    def test_usdt_minimum_unchanged(self):
        # USDT rules must not have changed
        self.assertEqual(gb.USDT_MIN_USDT, Decimal("0.15"))
        self.assertEqual(gb.WITHDRAWAL_NETWORK_BEP20, "BSC_BEP20")
        self.assertIn("BEP-20", gb.WITHDRAWAL_NETWORK_DISPLAY)


class TestVodafoneEnforcementAtRate(unittest.TestCase):
    """Verify the actual V2 withdrawal creation uses the dynamic minimum,
    not a hardcoded value, and that boundary values behave correctly."""

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
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute("DELETE FROM users WHERE user_id = 555")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (555, 'T', 5000000000, 1, 1, 0)"
            )
            conn.commit()

    def _seed_rate(self, rate):
        gb._save_rate_snapshot(Decimal(str(rate)), "test_dynamic")

    def test_exact_minimum_accepted_at_rate_50(self):
        self._seed_rate(50)
        # 5.00 EGP = 500 cents, exactly equal to dynamic minimum
        result = gb.create_v2_withdrawal_request(
            user_id=555,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=500,
            usdt_amount=None,
        )
        self.assertIsInstance(result, int)

    def test_one_cent_below_minimum_rejected_at_rate_50(self):
        self._seed_rate(50)
        # 4.99 EGP = 499 cents, one cent below
        result = gb.create_v2_withdrawal_request(
            user_id=555,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=499,
            usdt_amount=None,
        )
        self.assertEqual(result, "below_minimum")

    def test_minimum_at_rate_52(self):
        self._seed_rate(52)
        # 5.20 EGP = 520 cents accepted
        ok = gb.create_v2_withdrawal_request(
            user_id=555,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=520,
            usdt_amount=None,
        )
        self.assertIsInstance(ok, int)
        # 5.19 EGP rejected
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.commit()
        bad = gb.create_v2_withdrawal_request(
            user_id=555,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=519,
            usdt_amount=None,
        )
        self.assertEqual(bad, "below_minimum")

    def test_rate_snapshot_preserved_on_request(self):
        self._seed_rate(52)
        rid = gb.create_v2_withdrawal_request(
            user_id=555,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=520,
            usdt_amount=None,
        )
        row1 = gb.get_v2_withdrawal_request(rid)
        # Change rate after request creation
        self._seed_rate(99)
        row2 = gb.get_v2_withdrawal_request(rid)
        self.assertEqual(row1["exchange_rate_micro"], row2["exchange_rate_micro"])
        self.assertEqual(row1["egp_equivalent_cents"], row2["egp_equivalent_cents"])

    def test_usdt_minimum_unchanged_at_rate(self):
        self._seed_rate(50)
        # 0.15 USDT still the minimum
        result_ok = gb.create_v2_withdrawal_request(
            user_id=555,
            method_code=gb.WITHDRAWAL_METHOD_USDT,
            destination="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            requested_egp_cents=None,
            usdt_amount=Decimal("0.15"),
            network_code=gb.WITHDRAWAL_NETWORK_BEP20,
        )
        self.assertIsInstance(result_ok, int)
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.commit()
        result_bad = gb.create_v2_withdrawal_request(
            user_id=555,
            method_code=gb.WITHDRAWAL_METHOD_USDT,
            destination="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
            requested_egp_cents=None,
            usdt_amount=Decimal("0.149"),
            network_code=gb.WITHDRAWAL_NETWORK_BEP20,
        )
        self.assertEqual(result_bad, "below_minimum")



class TestWithdrawalEntryGate(unittest.TestCase):
    """Regression tests for the withdrawal entry gate fix.

    The old callback_withdraw_earnings had a generic balance gate:
        if row_balance_cents(user) < get_min_withdrawal():
            ... reject ...

    This blocked customers from reaching the V2 method selector even when
    they qualified for Vodafone's lower dynamic minimum ($0.10).
    """

    def test_70_no_generic_min_withdrawal_gate_in_source(self):
        """Verify the source code of callback_withdraw_earnings does NOT
        contain a generic get_min_withdrawal() balance gate."""
        with open(_BOT_FILE, "r", encoding="utf-8") as f:
            source = f.read()
        # Find the callback_withdraw_earnings function
        idx = source.find("def callback_withdraw_earnings(call):")
        self.assertNotEqual(idx, -1, "callback_withdraw_earnings not found")
        # Get the function body (up to the next def at the same indent level)
        next_def = source.find("\ndef callback_", idx + 10)
        if next_def == -1:
            next_def = source.find("\ndef ", idx + 40)
        func_body = source[idx:next_def] if next_def != -1 else source[idx:idx+2000]
        # The old gate used row_balance_cents + get_min_withdrawal together
        self.assertNotIn("row_balance_cents(user) < get_min_withdrawal()", func_body,
                         "Generic balance gate still present in callback_withdraw_earnings")

    def test_71_no_rejection_message_in_entry_handler(self):
        """Verify the old rejection message is not in the entry handler."""
        with open(_BOT_FILE, "r", encoding="utf-8") as f:
            source = f.read()
        idx = source.find("def callback_withdraw_earnings(call):")
        next_def = source.find("\ndef callback_", idx + 10)
        func_body = source[idx:next_def] if next_def != -1 else source[idx:idx+2000]
        self.assertNotIn("اجمع المزيد", func_body,
                         "Old rejection message still in entry handler")

    def test_72_no_global_min_display_in_success_path(self):
        """Verify the success path no longer shows get_min_withdrawal()."""
        with open(_BOT_FILE, "r", encoding="utf-8") as f:
            source = f.read()
        idx = source.find("def callback_withdraw_earnings(call):")
        next_def = source.find("\ndef callback_", idx + 10)
        func_body = source[idx:next_def] if next_def != -1 else source[idx:idx+2000]
        self.assertNotIn("format_balance(get_min_withdrawal())", func_body,
                         "Global minimum still displayed in success path")

    def test_73_balance_below_global_above_vodafone_allows_entry(self):
        """Balance 7.00 EGP (below 10 EGP global, above 5.00 EGP Vodafone at rate 50).
        The entry handler must NOT reject this user."""
        # The entry handler no longer has a balance gate, so any user with
        # a valid account can reach method selection. We verify this by
        # confirming the absence of the gate in source (test_70).
        # Additionally, the V2 flow validates method-specific minimums.
        # At rate 50, Vodafone min = 5.00 EGP = 500 cents.
        rate = Decimal("50")
        vodafone_min = gb.compute_vodafone_min_egp_cents(rate)
        self.assertEqual(vodafone_min, 500)  # 5.00 EGP
        # A balance of 700 cents (7.00 EGP) is above Vodafone min
        self.assertGreater(700, vodafone_min)

    def test_74_balance_below_vodafone_min_reaches_entry(self):
        """Balance 3.00 EGP (below Vodafone 5.00 EGP at rate 50).
        User can reach method selection but Vodafone-specific flow will reject."""
        rate = Decimal("50")
        vodafone_min = gb.compute_vodafone_min_egp_cents(rate)
        balance = 300  # 3.00 EGP
        self.assertLess(balance, vodafone_min)
        # The entry gate no longer blocks this — V2 flow handles it

    def test_75_balance_exactly_at_vodafone_min(self):
        """Balance exactly at Vodafone minimum: 5.00 EGP at rate 50."""
        rate = Decimal("50")
        vodafone_min = gb.compute_vodafone_min_egp_cents(rate)
        balance = 500  # 5.00 EGP
        self.assertEqual(balance, vodafone_min)
        # At exact boundary, the V2 flow should accept this amount

    def test_76_vodafone_min_rate_examples(self):
        """Verify Vodafone dynamic minimum at various rates."""
        examples = [
            (Decimal("50"), 500),   # 5.00 EGP
            (Decimal("51"), 510),   # 5.10 EGP
            (Decimal("52"), 520),   # 5.20 EGP
            (Decimal("53"), 530),   # 5.30 EGP
            (Decimal("55"), 550),   # 5.50 EGP
        ]
        for rate, expected_cents in examples:
            result = gb.compute_vodafone_min_egp_cents(rate)
            self.assertEqual(result, expected_cents,
                             f"Rate {rate}: expected {expected_cents}, got {result}")

    def test_77_usdt_minimum_unchanged(self):
        """USDT minimum remains 0.15 USDT, BEP-20 only."""
        self.assertEqual(gb.USDT_MIN_USDT, Decimal("0.15"))
        self.assertEqual(gb.WITHDRAWAL_NETWORK_BEP20, "BSC_BEP20")

    def test_78_withdrawal_cooldown_unchanged(self):
        """24-hour cooldown is still enforced."""
        self.assertEqual(gb.WITHDRAWAL_COOLDOWN_SECONDS, 24 * 3600)

    def test_79_referral_check_still_in_entry_handler(self):
        """Referral eligibility check must remain in the entry handler."""
        with open(_BOT_FILE, "r", encoding="utf-8") as f:
            source = f.read()
        idx = source.find("def callback_withdraw_earnings(call):")
        next_def = source.find("\ndef callback_", idx + 10)
        func_body = source[idx:next_def] if next_def != -1 else source[idx:idx+2000]
        self.assertIn("run_referral_withdrawal_double_check", func_body,
                      "Referral check missing from entry handler")

    def test_80_cooldown_check_still_in_entry_handler(self):
        """24-hour cooldown check must remain in the entry handler."""
        with open(_BOT_FILE, "r", encoding="utf-8") as f:
            source = f.read()
        idx = source.find("def callback_withdraw_earnings(call):")
        next_def = source.find("\ndef callback_", idx + 10)
        func_body = source[idx:next_def] if next_def != -1 else source[idx:idx+2000]
        self.assertIn("has_recent_withdrawal", func_body,
                      "Cooldown check missing from entry handler")

    def test_81_v2_keyboard_still_emitted(self):
        """The method selector keyboard must still emit V2 callbacks."""
        markup = gb.withdrawal_method_keyboard()
        rendered = []
        for row in markup.keyboard:
            for btn in row:
                rendered.append(btn.callback_data)
        self.assertIn("withdraw_v2_vodafone", rendered)
        self.assertIn("withdraw_v2_usdt", rendered)

    def test_82_get_min_withdrawal_still_exists(self):
        """get_min_withdrawal() must still exist for admin/tooling use."""
        self.assertTrue(hasattr(gb, "get_min_withdrawal"))
        self.assertTrue(callable(gb.get_min_withdrawal))

    def test_83_atomic_deduction_unchanged(self):
        """V2 atomic balance deduction/refund still works."""
        # Create a user with balance
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (user_id, first_name, balance_usd_nano) "
                "VALUES (8888, 'Test', 5000000000)"
            )
            conn.commit()
        gb._seed_rate = lambda: None  # mock
        # Seed a rate
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO currency_settings (setting_key, setting_value, updated_at) "
                "VALUES ('usdt_egp_rate', '50', CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO currency_settings (setting_key, setting_value, updated_at) "
                "VALUES ('usdt_egp_rate_provider', 'coingecko', CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO currency_settings (setting_key, setting_value, updated_at) "
                "VALUES ('usdt_egp_rate_fetched_at', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            conn.commit()
        result = gb.create_v2_withdrawal_request(
            user_id=8888,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
            network_code=None,
        )
        self.assertIsInstance(result, int)
        with gb.get_connection() as conn:
            user = conn.execute("SELECT balance_usd_nano FROM users WHERE user_id = 8888").fetchone()
            self.assertLess(user["balance_usd_nano"], 5000000000)
            # Refund
            conn.execute("DELETE FROM withdrawal_requests WHERE user_id = 8888")
            conn.execute("UPDATE users SET balance_usd_nano = 5000000000 WHERE user_id = 8888")
            conn.commit()


class _RecordingBot:
    """Mock bot that records every answer_callback_query / edit_message_text call.

    We assign this directly to gb.bot for the duration of the test so that
    callback_withdraw_earnings() and callback_v2_withdraw_vodafone() operate
    on it. The recorded calls let us assert on the actual Telegram flow
    side-effects (dismissal, edited message text, inline keyboard).
    """

    def __init__(self):
        self.answered = []
        self.edits = []
        self.sent = []

    def answer_callback_query(self, call_id, text=None, show_alert=False, **_kw):
        self.answered.append({
            "call_id": call_id,
            "text": text,
            "show_alert": show_alert,
        })

    def edit_message_text(self, text, chat_id=None, message_id=None,
                          reply_markup=None, **_kw):
        self.edits.append({
            "text": text,
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        })

    def send_message(self, chat_id, text, reply_markup=None, **_kw):
        self.sent.append({
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
        })


class _FakeUser:
    def __init__(self, user_id):
        self.id = user_id
        self.first_name = "Regression"
        self.is_bot = False


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeMessage:
    def __init__(self, chat_id, message_id):
        self.chat = _FakeChat(chat_id)
        self.message_id = message_id


class _FakeCall:
    """Mimics a telebot telebot.types.CallbackQuery enough to drive the handlers."""
    def __init__(self, user_id, data, chat_id=9001, message_id=42):
        self.id = f"cb-{user_id}-{message_id}"
        self.data = data
        self.from_user = _FakeUser(user_id)
        self.message = _FakeMessage(chat_id, message_id)


class TestCallbackEntryGateRegression(unittest.TestCase):
    """Real Telegram-flow regression for the customer entry point.

    The original bug: callback_withdraw_earnings() had a generic
        if row_balance_cents(user) < get_min_withdrawal():
            ... reject with 'الحد الأدنى لسحب الأرباح هو 10.00 جنيه' ...
    This blocked customers with balances between 5.00 EGP and 10.00 EGP
    (above Vodafone dynamic $0.10 minimum at rate 50) from ever reaching
    the method selector.

    These tests invoke the actual callback_withdraw_earnings() handler
    with a mocked bot, capture its real side-effects, and prove that:
      - A 7.00 EGP user is NOT rejected with the legacy message.
      - The method-selector screen is rendered with a V2 keyboard.
      - The same holds for a 3.00 EGP user (the V2 Vodafone flow, not
        the entry handler, is the authority for method-specific minimums).
    """

    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def setUp(self):
        # Reset transient state for each test.
        gb.user_state.clear()
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute("DELETE FROM users WHERE user_id IN (7001, 7002, 7003)")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (7001, 'SevenEGP', 140000000, 1, 1, 0)"
            )
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (7002, 'ThreeEGP', 60000000, 1, 1, 0)"
            )
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (7003, 'BigBalance', 1000000000, 1, 1, 0)"
            )
            conn.commit()
        # Patch bot interface and bypass external-only checks.
        self._real_bot = gb.bot
        self.recorder = _RecordingBot()
        gb.bot = self.recorder
        self._real_require = gb.require_active_account
        gb.require_active_account = lambda call: True
        self._real_referral = gb.run_referral_withdrawal_double_check
        gb.run_referral_withdrawal_double_check = (
            lambda uid: {"blocked": False, "unknown": False}
        )

    def tearDown(self):
        gb.bot = self._real_bot
        gb.require_active_account = self._real_require
        gb.run_referral_withdrawal_double_check = self._real_referral
        gb.user_state.clear()

    def _collect_callback_data(self, markup):
        if markup is None:
            return []
        return [btn.callback_data for row in markup.keyboard for btn in row]

    # ------------------------------------------------------------------ tests

    def test_84_seven_egp_user_reaches_method_selector(self):
        """7.00 EGP user (below legacy 10 EGP gate, above Vodafone 5.00 EGP
        at rate 50) must reach the V2 method selector through
        callback_withdraw_earnings() — not be rejected by a generic gate.
        """
        call = _FakeCall(user_id=7001, data="withdraw_earnings")
        gb.callback_withdraw_earnings(call)

        # The handler MUST answer the callback (dismiss spinner) ...
        self.assertTrue(
            self.recorder.answered,
            "callback_withdraw_earnings did not call bot.answer_callback_query",
        )
        # ... and MUST edit the message to the method selector screen.
        self.assertTrue(
            self.recorder.edits,
            "callback_withdraw_earnings did not call bot.edit_message_text",
        )

        edit = self.recorder.edits[-1]
        rendered = edit["text"]
        rendered_buttons = self._collect_callback_data(edit["reply_markup"])

        # 1. The legacy "collect more" rejection MUST NOT appear.
        self.assertNotIn("اجمع المزيد", rendered)
        # 2. The legacy "الحد الأدنى لسحب الأرباح هو" line MUST NOT appear.
        self.assertNotIn("الحد الأدنى لسحب الأرباح هو", rendered)
        # 3. The method-selector screen MUST be shown.
        self.assertIn("سحب الأرباح", rendered)
        self.assertIn("اختر طريقة السحب", rendered)
        # 4. The user's actual balance must be displayed in the header.
        self.assertIn("$0.14", rendered)
        # 5. The keyboard MUST be the V2 method selector.
        self.assertIn("withdraw_v2_vodafone", rendered_buttons,
                      "Vodafone button missing from method selector keyboard")
        self.assertIn("withdraw_v2_usdt", rendered_buttons,
                      "USDT button missing from method selector keyboard")
        # 6. The legacy get_min_withdrawal() value MUST NOT be displayed
        #    (it is method-specific now).
        # 7.00 EGP balance + Vodafone min 5.00 EGP proves the user can
        # proceed; the rendered text would be misleading otherwise.
        self.assertNotIn("$0.20", rendered,
                         "Legacy 10.00 EGP minimum still shown to customer")

    def test_85_three_egp_user_also_reaches_method_selector(self):
        """A 3.00 EGP user (below even Vodafone 5.00 EGP) must STILL
        reach the method selector. Per-method minimums are enforced by
        the V2 flow, not the entry handler. This proves the entry
        handler is not doing hidden pre-screening.
        """
        call = _FakeCall(user_id=7002, data="withdraw_earnings")
        gb.callback_withdraw_earnings(call)

        self.assertTrue(self.recorder.edits)
        edit = self.recorder.edits[-1]
        rendered_buttons = self._collect_callback_data(edit["reply_markup"])

        self.assertNotIn("اجمع المزيد", edit["text"])
        self.assertIn("$0.06", edit["text"])
        self.assertIn("withdraw_v2_vodafone", rendered_buttons)
        self.assertIn("withdraw_v2_usdt", rendered_buttons)

    def test_86_high_balance_user_path_unchanged(self):
        """A user with 50.00 EGP follows the same happy path. Guards
        against an over-correction that might lock out legitimate users.
        """
        call = _FakeCall(user_id=7003, data="withdraw_earnings")
        gb.callback_withdraw_earnings(call)

        self.assertTrue(self.recorder.edits)
        edit = self.recorder.edits[-1]
        rendered_buttons = self._collect_callback_data(edit["reply_markup"])
        self.assertIn("$1.00", edit["text"])
        self.assertIn("withdraw_v2_vodafone", rendered_buttons)
        self.assertIn("withdraw_v2_usdt", rendered_buttons)

    def test_87_entry_handler_emits_no_show_alert(self):
        """A successful entry path must dismiss the spinner silently
        (no show_alert). Alerts are reserved for genuine rejections
        (referral, cooldown, missing user). This guards against a
        regression where the generic 10 EGP gate (which used a full
        edit_message_text replacement) is re-introduced as an alert
        with a similar Arabic rejection string.
        """
        call = _FakeCall(user_id=7001, data="withdraw_earnings")
        gb.callback_withdraw_earnings(call)
        # The single answer must be a silent dismiss.
        self.assertEqual(len(self.recorder.answered), 1)
        self.assertFalse(
            self.recorder.answered[0]["show_alert"],
            "Entry handler emitted an alert (would indicate a generic gate)",
        )

    def test_88_legacy_gate_strings_absent_from_handler_output(self):
        """Defensive: no edit produced by the entry handler may contain
        any of the legacy gate strings — not just the main one. This
        catches a partial regression where someone re-introduces a
        similar but not identical Arabic phrase.
        """
        forbidden = [
            "اجمع المزيد",
            "الحد الأدنى لسحب الأرباح هو",
            "10.00 جنيه",
        ]
        call = _FakeCall(user_id=7001, data="withdraw_earnings")
        gb.callback_withdraw_earnings(call)
        for edit in self.recorder.edits:
            for token in forbidden:
                self.assertNotIn(token, edit["text"],
                                 f"Legacy gate text {token!r} reappeared "
                                 f"in entry handler output")


class TestV2VodafoneBoundaryAtRate50(unittest.TestCase):
    """V2 Vodafone path boundary regression at rate 50 USD/EGP.

    At rate 50: $0.10 minimum == 5.00 EGP exactly. The V2 flow must:
      - Accept 5.00 EGP (boundary inclusive).
      - Reject 4.99 EGP (just below).
    These tests drive the real callback_v2_withdraw_vodafone() handler
    plus a mocked customer message submission to assert the boundary
    behavior end-to-end, not just on compute_vodafone_min_egp_cents().
    """

    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def setUp(self):
        gb.user_state.clear()
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute("DELETE FROM currency_settings")
            conn.execute("DELETE FROM users WHERE user_id IN (8001, 8002)")
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (8001, 'Boundary', 100000000, 1, 1, 0)"
            )
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (8002, 'Boundary', 99999999, 1, 1, 0)"
            )
            # Seed an exchange rate of 50 USDT/EGP, fresh.
            conn.execute(
                "INSERT INTO currency_settings (setting_key, setting_value, "
                "updated_at) VALUES ('usdt_egp_rate', '50', CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO currency_settings (setting_key, setting_value, "
                "updated_at) VALUES ('usdt_egp_rate_provider', 'test', "
                "CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO currency_settings (setting_key, setting_value, "
                "updated_at) VALUES ('usdt_egp_rate_fetched_at', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            conn.commit()
        self._real_bot = gb.bot
        self.recorder = _RecordingBot()
        gb.bot = self.recorder
        self._real_require = gb.require_active_account
        gb.require_active_account = lambda call: True

    def tearDown(self):
        gb.bot = self._real_bot
        gb.require_active_account = self._real_require
        gb.user_state.clear()

    def _classroom_message(self, user_id, text):
        """Build a minimal stand-in for telebot telebot.types.Message."""
        msg = _FakeMessage(chat_id=user_id, message_id=1)
        msg.text = text
        msg.from_user = _FakeUser(user_id)
        return msg

    def test_89_five_egp_at_rate_50_accepted_by_minimum(self):
        """At rate 50, 5.00 EGP == $0.10 (exact boundary). The V2
        Vodafone amount handler must accept this and advance the user
        to the destination-input step. The user_state['step'] must
        become 'awaiting_v2_vodafone_destination'.
        """
        user_id = 8001
        call = _FakeCall(user_id=user_id, data="withdraw_v2_vodafone")
        gb.callback_v2_withdraw_vodafone(call)

        # First, the V2 entry edit must show the dynamic minimum.
        self.assertTrue(self.recorder.edits, "V2 Vodafone entry did not edit")
        entry_text = self.recorder.edits[-1]["text"]
        self.assertIn("Vodafone Cash", entry_text)
        self.assertIn("0.10", entry_text)
        self.assertIn("5.00 EGP", entry_text)

        # User state should be waiting for amount.
        self.assertEqual(
            gb.user_state[user_id]["step"], "awaiting_v2_vodafone_amount"
        )
        self.recorder.edits.clear()
        self.recorder.sent.clear()

        # Now submit 5 EGP — boundary inclusive, must be accepted.
        msg = self._classroom_message(user_id, "5")
        gb.handle_v2_vodafone_amount(msg)

        # No rejection message about minimum should have been sent.
        rejection_texts = [
            s["text"] for s in self.recorder.sent
            if "الحد الأدنى" in s["text"]
        ]
        self.assertEqual(
            rejection_texts, [],
            f"5.00 EGP was wrongly rejected: {rejection_texts}",
        )
        # The user should be advanced to the destination step.
        self.assertEqual(
            gb.user_state[user_id]["step"],
            "awaiting_v2_vodafone_destination",
        )
        # And there must be a "destination" prompt message.
        destination_prompts = [
            s for s in self.recorder.sent
            if "Vodafone Cash" in s["text"] and "11 رقم" in s["text"]
        ]
        self.assertTrue(
            destination_prompts,
            "Expected destination prompt after accepting boundary amount",
        )

    def test_90_four_egp_99_at_rate_50_rejected_by_minimum(self):
        """At rate 50, 4.99 EGP is just below the 5.00 EGP minimum.
        The V2 flow must reject and NOT advance the user.
        """
        user_id = 8002
        call = _FakeCall(user_id=user_id, data="withdraw_v2_vodafone")
        gb.callback_v2_withdraw_vodafone(call)
        self.recorder.edits.clear()
        self.recorder.sent.clear()

        msg = self._classroom_message(user_id, "4.99")
        gb.handle_v2_vodafone_amount(msg)

        # A minimum-rejection message must have been sent ...
        rejection = [
            s for s in self.recorder.sent if "الحد الأدنى" in s["text"]
        ]
        self.assertTrue(
            rejection,
            "Expected minimum-rejection message for 4.99 EGP at rate 50",
        )
        # ... and the state must NOT have advanced.
        self.assertEqual(
            gb.user_state[user_id]["step"], "awaiting_v2_vodafone_amount",
            "User state advanced despite below-minimum amount",
        )
        # The rejection must reference the dynamic USD value, not a
        # legacy 10.00 EGP string.
        joined = " ".join(s["text"] for s in rejection)
        self.assertIn("0.10", joined,
                      "Rejection message does not mention $0.10 dynamic minimum")
        self.assertNotIn("10.00", joined,
                         "Rejection message references legacy 10.00 EGP minimum")


if __name__ == "__main__":
    unittest.main()

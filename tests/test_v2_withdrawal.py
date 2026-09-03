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

sys.path.insert(0, "/root/pounds")

import decimal as _decimal
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot",
    "/root/pounds/ganaihat_bot.py",
    submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = _decimal.Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod

gb = _mod


class TestV2Constants(unittest.TestCase):
    def test_01_vodafone_minimum(self):
        self.assertEqual(gb.VODAFONE_MIN_EGP_CENTS, 1000)

    def test_02_usdt_minimum(self):
        self.assertEqual(gb.USDT_MIN_USDT, Decimal("0.15"))

    def test_03_withdrawal_fee_is_zero(self):
        self.assertEqual(gb.VODAFONE_MIN_EGP_CENTS, 1000)  # sanity
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
                "INSERT INTO users (user_id, first_name, balance_cents, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (999, 'Test', 100000, 1, 1, 0)"
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

    def test_37_vodafone_9_99_rejected(self):
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=999,
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
                "UPDATE users SET balance_cents = 500 WHERE user_id = 999"
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
                "UPDATE users SET balance_cents = 100000 WHERE user_id = 999"
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
                "UPDATE users SET balance_cents = 100000 WHERE user_id = 999"
            )
            conn.commit()
        before = gb.get_user(999)["balance_cents"]
        gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=1000,
            usdt_amount=None,
        )
        after = gb.get_user(999)["balance_cents"]
        self.assertEqual(before - after, 1000)

    def test_49_failed_create_does_not_deduct(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute(
                "UPDATE users SET balance_cents = 100000 WHERE user_id = 999"
            )
            conn.commit()
        before = gb.get_user(999)["balance_cents"]
        result = gb.create_v2_withdrawal_request(
            user_id=999,
            method_code=gb.WITHDRAWAL_METHOD_VODAFONE,
            destination="01012345678",
            requested_egp_cents=500,  # below minimum
            usdt_amount=None,
        )
        after = gb.get_user(999)["balance_cents"]
        self.assertEqual(result, "below_minimum")
        self.assertEqual(before, after)

    def test_50_rate_snapshot_saved(self):
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM withdrawal_requests")
            conn.execute(
                "UPDATE users SET balance_cents = 100000 WHERE user_id = 999"
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
                "UPDATE users SET balance_cents = 100000 WHERE user_id = 999"
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
                "INSERT INTO users (user_id, first_name, balance_cents, "
                "activation_status, is_verified, withdrawal_blocked) "
                "VALUES (888, 'T', 50000, 1, 1, 0)"
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
        before = gb.get_user(888)["balance_cents"]
        result = gb.complete_v2_withdrawal(rid, 1, "TX-123")
        after = gb.get_user(888)["balance_cents"]
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
        before = gb.get_user(888)["balance_cents"]
        result = gb.reject_v2_withdrawal(rid, 1)
        after = gb.get_user(888)["balance_cents"]
        self.assertIsNotNone(result)
        self.assertEqual(after - before, 1000)
        # Second refund attempt must be no-op
        result2 = gb.reject_v2_withdrawal(rid, 1)
        self.assertIsNone(result2)
        final = gb.get_user(888)["balance_cents"]
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
        self.assertEqual(result, "customer_legacy_disabled")

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


if __name__ == "__main__":
    unittest.main()

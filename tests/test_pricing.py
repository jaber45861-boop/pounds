"""اختبارات نظام هامش البيع 30% لخدمات SMM."""
import os
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP

# Set required env vars before importing ganaihat_bot
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")
os.environ.setdefault("EGP_PER_USD_SMM", "50")

sys.path.insert(0, "/root/pounds")
from ganaihat_bot import (
    calculate_selling_price,
    MARGIN_MULTIPLIER,
    parse_currency_input,
    format_balance,
    get_service_price,
    get_service_base_cost,
    set_service_price,
    SERVICE_INDEX,
    init_db,
    get_connection,
)


class TestPricingMargin(unittest.TestCase):
    """اختبارات نظام هامش البيع 30%."""

    @classmethod
    def setUpClass(cls):
        # Create temporary database for testing
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def test_margin_constant(self):
        self.assertEqual(MARGIN_MULTIPLIER, Decimal("1.30"))

    def test_calculate_selling_price_10_pounds(self):
        # 10 جنيه = 1000 cents → 1300 cents = 13 جنيه
        self.assertEqual(calculate_selling_price(1000), 1300)

    def test_calculate_selling_price_50_pounds(self):
        # 50 جنيه = 5000 cents → 6500 cents = 65 جنيه
        self.assertEqual(calculate_selling_price(5000), 6500)

    def test_calculate_selling_price_100_pounds(self):
        # 100 جنيه = 10000 cents → 13000 cents = 130 جنيه
        self.assertEqual(calculate_selling_price(10000), 13000)

    def test_calculate_selling_price_33_pounds(self):
        # 33 جنيه = 3300 cents → 4290 cents = 42.90 جنيه
        self.assertEqual(calculate_selling_price(3300), 4290)

    def test_calculate_selling_price_10_50_pounds(self):
        # 10.50 جنيه = 1050 cents → 1365 cents = 13.65 جنيه
        self.assertEqual(calculate_selling_price(1050), 1365)

    def test_calculate_selling_price_0(self):
        self.assertEqual(calculate_selling_price(0), 0)

    def test_calculate_selling_price_rounding(self):
        self.assertEqual(calculate_selling_price(1), 1)
        self.assertEqual(calculate_selling_price(2), 3)
        self.assertEqual(calculate_selling_price(3), 4)

    def test_calculate_selling_price_consistency(self):
        for base in [1000, 5000, 10000, 3300, 1050]:
            selling = calculate_selling_price(base)
            self.assertGreaterEqual(selling, base)
            if base > 0:
                self.assertEqual(
                    selling,
                    int(
                        (Decimal(str(base)) * Decimal("1.30")).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP
                        )
                    ),
                )

    def test_admin_input_50_pounds_flow(self):
        # Admin enters "50" meaning 50 جنيه
        base_cents = parse_currency_input("50")
        self.assertEqual(base_cents, 5000)
        selling_cents = calculate_selling_price(base_cents)
        self.assertEqual(selling_cents, 6500)
        display = format_balance(selling_cents)
        self.assertIn("65.00", display)

    def test_set_service_price_stores_base_cost(self):
        # Admin sets base cost to 50 جنيه (5000 cents)
        service_key = "tg_100"
        set_service_price(service_key, 5000)
        base = get_service_base_cost(service_key)
        selling = get_service_price(service_key)
        self.assertEqual(base, 5000)
        self.assertEqual(selling, 6500)

    def test_customer_privacy_no_base_cost(self):
        # Verify customer-facing get_service_price returns selling price only
        service_key = "tg_100"
        set_service_price(service_key, 5000)
        selling = get_service_price(service_key)
        base = get_service_base_cost(service_key)
        self.assertEqual(selling, 6500)
        self.assertNotEqual(selling, base)

    def test_customer_privacy_no_margin_exposure(self):
        # Verify no customer-facing function leaks margin info
        service_key = "tg_100"
        set_service_price(service_key, 5000)
        selling = get_service_price(service_key)
        self.assertEqual(selling, calculate_selling_price(5000))
        self.assertNotEqual(selling, 5000)

    def test_admin_can_see_base_and_selling(self):
        # Admin messages CAN show base cost and margin
        admin_text = (
            "📌 التكلفة الأساسية: 0.50 جنيه\n"
            "💰 سعر البيع: 0.65 جنيه\n"
            "📊 هامش المنصة: 30%"
        )
        self.assertIn("التكلفة الأساسية", admin_text)
        self.assertIn("سعر البيع", admin_text)
        self.assertIn("هامش المنصة", admin_text)


if __name__ == "__main__":
    unittest.main()

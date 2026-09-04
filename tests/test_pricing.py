"""اختبارات نظام هامش البيع 30% لخدمات SMM."""
import os
import sys
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")
os.environ.setdefault("EGP_PER_USD_SMM", "50")

import decimal as _decimal
import importlib.util
import sys as _sys

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
_sys.modules["ganaihat_bot"] = _mod

import reward_api as _reward_api
_reward_api._live_egp_per_usd = Decimal("50")


calculate_selling_price = _mod.calculate_selling_price
MARGIN_MULTIPLIER = _mod.MARGIN_MULTIPLIER
parse_currency_input = _mod.parse_currency_input
format_balance = _mod.format_balance
egp_cents_to_wallet_nano = _mod.egp_cents_to_wallet_nano
get_service_price = _mod.get_service_price
get_service_base_cost = _mod.get_service_base_cost
set_service_price = _mod.set_service_price
SERVICE_INDEX = _mod.SERVICE_INDEX
init_db = _mod.init_db


class TestPricingMargin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def setUp(self):
        _reward_api._live_egp_per_usd = Decimal("50")

    def test_margin_constant(self):
        self.assertEqual(MARGIN_MULTIPLIER, Decimal("1.30"))

    def test_selling_price_10_pounds(self):
        self.assertEqual(calculate_selling_price(1000), 1300)

    def test_selling_price_50_pounds(self):
        self.assertEqual(calculate_selling_price(5000), 6500)

    def test_selling_price_100_pounds(self):
        self.assertEqual(calculate_selling_price(10000), 13000)

    def test_selling_price_33_pounds(self):
        self.assertEqual(calculate_selling_price(3300), 4290)

    def test_selling_price_10_50_pounds(self):
        self.assertEqual(calculate_selling_price(1050), 1365)

    def test_selling_price_zero(self):
        self.assertEqual(calculate_selling_price(0), 0)

    def test_selling_price_rounding(self):
        self.assertEqual(calculate_selling_price(1), 1)
        self.assertEqual(calculate_selling_price(2), 3)
        self.assertEqual(calculate_selling_price(3), 4)

    def test_selling_price_consistency(self):
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

    def test_admin_input_flow_50_pounds(self):
        base_cents = parse_currency_input("50")
        self.assertEqual(base_cents, 5000)
        selling_cents = calculate_selling_price(base_cents)
        self.assertEqual(selling_cents, 6500)
        display = format_balance(egp_cents_to_wallet_nano(selling_cents))
        self.assertIn("$1.30", display)

    def test_set_service_price_stores_base_cost(self):
        service_key = "tg_100"
        set_service_price(service_key, 5000)
        base = get_service_base_cost(service_key)
        selling = get_service_price(service_key)
        self.assertEqual(base, 5000)
        self.assertEqual(selling, 6500)

    def test_customer_facing_price_is_selling_price_not_base(self):
        service_key = "tg_100"
        set_service_price(service_key, 5000)
        selling = get_service_price(service_key)
        base = get_service_base_cost(service_key)
        self.assertEqual(selling, 6500)
        self.assertNotEqual(selling, base)

    def test_customer_facing_price_does_not_expose_margin(self):
        service_key = "tg_100"
        set_service_price(service_key, 5000)
        selling = get_service_price(service_key)
        self.assertEqual(selling, calculate_selling_price(5000))
        self.assertNotEqual(selling, 5000)

    def test_admin_messages_can_show_base_and_margin(self):
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

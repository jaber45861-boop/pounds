import os
from flask import Flask, jsonify
from threading import Thread
import logging
from waitress import serve

app = Flask(__name__)
_http_thread: Thread | None = None

@app.route('/')
def home():
    return "I am alive!"

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "telegram-bot-reward-api"})

def run_http_server():
    """Serve Flask and the Telegram bot from the same Python process."""
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    logging.getLogger("telegram_reward_api").info(
        "HTTP API listening on %s:%s", host, port
    )
    serve(
        app,
        host=host,
        port=port,
        threads=int(os.environ.get("HTTP_THREADS", "8")),
    )

def keep_alive():
    """Backward-compatible name used by the uploaded bot runtime."""
    global _http_thread
    if _http_thread is not None and _http_thread.is_alive():
        return _http_thread
    _http_thread = Thread(
        target=run_http_server,
        name="telegram-reward-api",
        daemon=True,
    )
    _http_thread.start()
    return _http_thread

import os
import sqlite3
import html
import requests as http
import telebot
import re
import sys
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from telebot.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from urllib.parse import parse_qs, urlsplit
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from reward_api import register_reward_api

# ══════════════════════════════════════════════════════════════════════════════
# ─── إعدادات البوت ────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
TOKEN        = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
API_SECRET = os.environ.get("API_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
REWARD_API_ORIGINS = os.environ.get("REWARD_API_ORIGINS", "*")
MONETAG_ZONE_ID = os.environ.get("MONETAG_ZONE_ID", "11487222")
PROVIDER_WEBHOOK_SECRET = os.environ.get("PROVIDER_WEBHOOK_SECRET", "")
_raw_pct = float(os.environ.get("USER_PROFIT_PERCENTAGE", 70))
if not (0 <= _raw_pct <= 100):
    raise ValueError(
        f"USER_PROFIT_PERCENTAGE must be between 0 and 100, got {_raw_pct}"
    )
USER_PROFIT_PCT = _raw_pct / 100.0
TELEGRAM_MINI_APP_URL = os.environ.get("TELEGRAM_MINI_APP_URL", "").strip()
MONETAG_SDK_URL = os.environ.get("MONETAG_SDK_URL", "https://libtl.com/sdk.js").strip()
MONETAG_SDK_NAME = os.environ.get(
    "MONETAG_SDK_NAME",
    f"show_{MONETAG_ZONE_ID}",
).strip()

BOT_USERNAME    = "GanaihatBot"         # ← اسم مستخدم البوت الجديد
ADMIN_ID        = 6175354851           # ← معرّف حساب المشرف
AD_REWARD       = 50
ADVERTISING_URL = os.environ.get(
    "ADVERTISING_URL",
    "https://omg10.com/4/11487232",
).strip()

# ─── إعدادات سيرفر الرشق (SMM Panel) ─────────────────────────────────────────
SMM_API_URL = os.environ.get(
    "SMM_API_URL",
    "https://justanotherpanel.com/api/v2",
).strip()  # ← رابط API لسيرفر JAP
SMM_API_KEY = os.environ.get("SMM_API_KEY", "")      # ← يُقرأ من المتغيرات السرية

# ─── إعدادات SMMCPAN (مورد خدمات السوشيال ميديا) ──────────────────────────────
SMMCPAN_API_KEY  = os.environ.get("SMMCPAN_API_KEY", "")
SMMCPAN_API_URL  = os.environ.get("SMMCPAN_API_URL", "https://smmcpan.com/api/v2").strip()
# Normalize: strip trailing slash, ensure /api/v2 suffix, prevent duplication
if SMMCPAN_API_URL.endswith("/"): SMMCPAN_API_URL = SMMCPAN_API_URL.rstrip("/")
if not SMMCPAN_API_URL.endswith("/api/v2"):
    if SMMCPAN_API_URL.endswith("/api"):
        SMMCPAN_API_URL += "/v2"
    elif SMMCPAN_API_URL.endswith("/api/v2/"):
        SMMCPAN_API_URL = SMMCPAN_API_URL.rstrip("/")
    else:
        SMMCPAN_API_URL += "/api/v2"
SMM_MARGIN_PCT   = float(os.environ.get("SMM_MARGIN_PCT", "30"))
EGP_PER_USD_SMM  = float(os.environ.get("EGP_PER_USD_SMM", "50"))
MARGIN_MULTIPLIER = Decimal("1.30")

# ─── إعدادات CPAGrip (مهام CPA) ────────────────────────────────────────────────
CPAGRIP_USER_ID         = os.environ.get("CPAGRIP_USER_ID", "")
CPAGRIP_KEY             = os.environ.get("CPAGRIP_KEY", "")
CPAGRIP_RSS_URL         = "https://www.cpagrip.com/common/offer_feed_rss.php"

REFERRAL_SERVICE_KEY = "referral_boost"
REFERRAL_COST = 500
REFERRAL_QUANTITY = 25
REFERRAL_REWARD = 1   # 0.01 جنيه ($0.01) – مكافأة إحالة م팎رة عند تفعيل الحساب
WITHDRAWAL_MIN_POINTS = 5000
DEFAULT_ORDER_MIN_POINTS = 100
ABSOLUTE_ORDER_MIN_POINTS = 1
PROMOTION_MIN_POINTS = 100
EGP_PER_USD = Decimal("50")
LEGACY_POINT_EGP = Decimal("0.01")
MONEY_SCALE = Decimal("100")
MONEY_CURRENCY = "جنيه"
WITHDRAWAL_MIN_CENTS = WITHDRAWAL_MIN_POINTS
PROMOTION_MIN_CENTS = PROMOTION_MIN_POINTS

# ══════════════════════════════════════════════════════════════════════════════
# ─── قائمة القنوات الإجبارية — أضف أو احذف قنوات من هنا بحرية ───────────────
# ══════════════════════════════════════════════════════════════════════════════
BASE_REQUIRED_CHANNELS: list[dict] = [
    {
        "username": "@Crypto1583",
        "name":     "قناة كوين كرافت الرسمية 🏆",
        "reward":   50,
        "task_key": "channel_Crypto1583",
    },
    # أضف قنوات الداعمين والمعلنين هنا، مثال:
    # {
    #     "username": "@sponsor_channel2",
    #     "name":     "قناة الراعي الثاني",
    #     "reward":   30,
    #     "task_key": "channel_sponsor2",
    # },
]

# تُملأ عند تشغيل البوت من القنوات الثابتة والحملات المدفوعة النشطة.
REQUIRED_CHANNELS: list[dict] = [dict(channel) for channel in BASE_REQUIRED_CHANNELS]
# ← للتوافق مع نظام الإحالات (يشير دائماً للقناة الأولى في القائمة)
SPONSOR_CHANNEL   = BASE_REQUIRED_CHANNELS[0]["username"]
# ← مكافأة إضافية تُمنح مرة واحدة فقط عند أول تفعيل للحساب
ACTIVATION_REWARD = 50

# ══════════════════════════════════════════════════════════════════════════════
# ─── USD Nano Money Primitives (Phase 1) ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# 1 USD = 1,000,000,000 USD nano-units (10^9).
# Stored as SQLite INTEGER.  Arithmetic uses Decimal exclusively.
# These primitives are NOT wired into existing EGP-cent accounting yet.

USD_NANO_PER_USD = 1_000_000_000
USD_NANO_SCALE   = Decimal("1000000000")


def usd_decimal_to_nano(amount) -> int:
    """Convert a USD Decimal/string value to integer USD nano-units.

    Uses ROUND_HALF_UP quantization.  Rejects NaN, Infinity, and
    non-finite values.  Returns 0 for zero input.
    """
    try:
        d = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        raise ValueError(f"Cannot convert {amount!r} to Decimal")
    if d.is_nan() or d.is_infinite():
        raise ValueError(f"Non-finite value: {d}")
    if d < 0:
        raise ValueError(f"Negative USD amount not supported: {d}")
    return int((d * USD_NANO_SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def nano_to_usd_decimal(nano: int | float | str | Decimal) -> Decimal:
    """Convert integer USD nano-units back to a USD Decimal."""
    try:
        n = int(nano)
    except (ValueError, TypeError):
        raise ValueError(f"Cannot convert {nano!r} to int")
    if n < 0:
        raise ValueError(f"Negative nano amount not supported: {n}")
    return (Decimal(n) / USD_NANO_SCALE).quantize(
        Decimal("0.000000001"), rounding=ROUND_HALF_UP
    )


def egp_decimal_to_usd_nano(egp_amount, rate=None) -> int:
    """Convert an EGP Decimal amount to USD nano-units via a given rate.

    ``rate`` is EGP per 1 USD (Decimal).  Defaults to EGP_PER_USD.
    This is a pure helper for future migration — not wired into accounting.
    """
    if rate is None:
        rate = EGP_PER_USD
    try:
        egp = Decimal(str(egp_amount).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        raise ValueError(f"Cannot convert {egp_amount!r} to Decimal")
    if egp.is_nan() or egp.is_infinite():
        raise ValueError(f"Non-finite EGP value: {egp}")
    usd = egp / Decimal(str(rate))
    return usd_decimal_to_nano(usd)


def usd_nano_to_egp_decimal(nano: int, rate=None) -> Decimal:
    """Convert USD nano-units to an EGP Decimal via a given rate.

    ``rate`` is EGP per 1 USD (Decimal).  Defaults to EGP_PER_USD.
    This is a pure helper for future migration — not wired into accounting.
    """
    if rate is None:
        rate = EGP_PER_USD
    usd = nano_to_usd_decimal(nano)
    egp = usd * Decimal(str(rate))
    return egp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_usd_nano(nano: int) -> str:
    """Format integer USD nano-units as a human-readable USD string.

    Preserves sub-cent precision without unnecessary trailing zeros.
    Avoids scientific notation (e.g. '1E-9') by using fixed-width
    format for sub-cent values.
    """
    d = nano_to_usd_decimal(nano)
    if d == 0:
        return "$0"
    if d < Decimal("0.01"):
        # For sub-cent values, format with up to 9 decimal places,
        # stripping trailing zeros but never using scientific notation.
        # e.g. 1 → "$0.000000001", 5000000 → "$0.005"
        normalized = d.normalize()
        # If normalize() produced scientific notation, use fixed format
        text = str(normalized)
        if "E" in text or "e" in text:
            text = f"{d:f}"
        # Strip trailing zeros after decimal point
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return f"${text}"
    # For >= $0.01, normalize and remove unnecessary trailing zeros
    normalized = d.normalize()
    return f"${normalized}"


def is_valid_usd_nano_amount(value) -> bool:
    """Return True if *value* can be safely stored as a USD nano-integer.

    Accepts int, Decimal, str, or float (float only for zero/non-zero
    check — never used as authoritative money value).
    """
    try:
        if isinstance(value, float):
            if value != value or value == float("inf") or value == float("-inf"):
                return False
            # float is accepted but only for convenience; convert via Decimal
            value = str(value)
        n = usd_decimal_to_nano(value)
        return 0 <= n <= 2**63 - 1  # SQLite INTEGER range
    except (ValueError, OverflowError):
        return False


def require_valid_usd_nano_amount(value) -> int:
    """Like ``usd_decimal_to_nano`` but also checks SQLite INTEGER range.

    Raises ``ValueError`` for invalid, negative, or overflow values.
    """
    n = usd_decimal_to_nano(value)
    if n < 0:
        raise ValueError(f"Negative nano amount: {n}")
    if n > 2**63 - 1:
        raise ValueError(f"Nano amount exceeds SQLite INTEGER max: {n}")
    return n
WITHDRAWAL_COOLDOWN_MESSAGE = (
    "⚠️ تنبيه: مسموح بطلب سحب واحد فقط كل 24 ساعة لمنع الضغط! "
    "يمكنك تقديم طلب جديد غداً."
)

# أسعار الترويج محفوظة بالقروش داخلياً، مع إبقاء أسماء الحقول القديمة للتوافق.
PROMOTED_CHANNEL_REWARD = 50
PROMOTION_PACKAGES: dict[str, dict] = {
    "1000": {
        "label": "1000 مشترك جديد",
        "target_subscribers": 1000,
        "usd_price": 5,
        "points_cost": 25000,
    },
}

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def setup_bot_commands():
    """يسجل الأوامر الرسمية التي تظهر في زر القائمة داخل تليجرام."""
    bot.set_my_commands([
        BotCommand("start", "تشغيل البوت والعودة للقائمة الرئيسية"),
        BotCommand("admin", "فتح لوحة تحكم المشرف"),
        BotCommand("help", "المساعدة والتواصل مع الإدارة"),
    ])


def parse_money_to_cents(value: str | int | float | Decimal) -> int | None:
    """يحوّل الجنيه أو الدولار النصي إلى قروش مصرية بدقة."""
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return None
    if amount <= 0:
        return None
    cents = (amount * MONEY_SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def parse_currency_input(value: str | int | float | Decimal) -> int | None:
    """يقبل مبلغاً بالجنيه أو الدولار ويعيده بالقروش المصرية."""
    if value is None:
        return None
    raw = str(value).strip().replace(",", ".")
    lowered = raw.lower()
    is_usd = lowered.startswith("$") or lowered.endswith("usd")
    if is_usd:
        raw = lowered.removeprefix("$").removesuffix("usd").strip()
        return parse_usd_to_egp_cents(raw)
    for suffix in ("egp", "جنيه", "جنيهًا", "ج"):
        if lowered.endswith(suffix):
            raw = raw[: -len(suffix)].strip()
            break
    return parse_money_to_cents(raw)


def parse_usd_to_egp_cents(value: str | int | float | Decimal) -> int | None:
    """يحوّل الدولار إلى قروش مصرية، مع دعم السنتات."""
    try:
        usd = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return None
    if usd <= 0:
        return None
    return parse_money_to_cents(usd * EGP_PER_USD)


def format_egp(cents: int | None) -> str:
    amount = Decimal(int(cents or 0)) / MONEY_SCALE
    return f"{amount:.2f} جنيه"


def format_usd(cents: int | None) -> str:
    amount = (Decimal(int(cents or 0)) / MONEY_SCALE) / EGP_PER_USD
    return f"${amount:.2f}"


def format_balance(cents: int | None) -> str:
    return f"{format_egp(cents)} ({format_usd(cents)})"


def format_money_input(cents: int | None) -> str:
    return format_egp(cents)


def row_balance_cents(row) -> int:
    """يقرأ الرصيد المالي مع توافق آمن مع الصفوف القديمة."""
    if row is None:
        return 0
    try:
        return max(0, int(row["balance_cents"]))
    except (KeyError, IndexError, TypeError):
        return max(0, int(row["points"] or 0))


def balance_text(row) -> str:
    return format_balance(row_balance_cents(row))


# ══════════════════════════════════════════════════════════════════════════════
# ─── كتالوج الخدمات ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# الهيكل: category_key → {name, services: {service_key → service}}
# IDs خدمات JAP الرسمية المزوّدة للمستخدم. قيمة cost هي الاسم الداخلي
# المستخدم في الحسابات الحالية، وpoints_cost محفوظة أيضاً للتوافق مع الكتالوج.
SERVICES: dict[str, dict] = {
    "telegram": {
        "name": "تليجرام 📱",
        "services": {
            "tg_100": {
                "name": "متابع تليجرام",
                "description": "متابعون حقيقيون — تسليم تدريجي",
                "smm_service_id": "1803",
                "quantity": 100,
                "cost": 500,
                "emoji": "📱",
            },
            "tg_views_1k": {
                "name": "مشاهدة تليجرام",
                "description": "مشاهدات لآخر منشور في قناتك",
                "smm_service_id": "1150",
                "quantity": 1000,
                "cost": 200,
                "emoji": "👁",
            },
        },
    },
    "facebook": {
        "name": "فيسبوك 🔵",
        "services": {
            "fb_followers_100": {
                "name": "متابعو الملف الشخصي على فيسبوك",
                "description": "متابعون للملف الشخصي على فيسبوك",
                "smm_service_id": "2840",
                "quantity": 100,
                "points_cost": 850,
                "cost": 850,
                "emoji": "👥",
            },
            "fb_page_likes_100": {
                "name": "إعجابات/متابعو صفحة فيسبوك",
                "description": "إعجابات ومتابعون لصفحة فيسبوك",
                "smm_service_id": "2410",
                "quantity": 100,
                "points_cost": 900,
                "cost": 900,
                "emoji": "👍",
            },
        },
    },
    "tiktok": {
        "name": "تيك توك 🖤",
        "services": {
            "tt_followers_100": {
                "name": "متابعو تيك توك",
                "description": "متابعون لحساب تيك توك",
                "smm_service_id": "3120",
                "quantity": 100,
                "points_cost": 800,
                "cost": 800,
                "emoji": "👥",
            },
            "tt_likes_100": {
                "name": "إعجابات تيك توك",
                "description": "إعجابات لمنشور تيك توك",
                "smm_service_id": "4251",
                "quantity": 100,
                "points_cost": 400,
                "cost": 400,
                "emoji": "❤️",
            },
            "tt_views_1k": {
                "name": "مشاهدات تيك توك",
                "description": "مشاهدات لفيديو تيك توك",
                "smm_service_id": "1050",
                "quantity": 1000,
                "points_cost": 150,
                "cost": 150,
                "emoji": "👁",
            },
        },
    },
    "instagram": {
        "name": "إنستجرام 🟣",
        "services": {
            "ig_followers_100": {
                "name": "متابعو إنستجرام",
                "description": "متابعون لحساب إنستجرام",
                "smm_service_id": "5120",
                "quantity": 100,
                "points_cost": 500,
                "cost": 500,
                "emoji": "👥",
            },
            "ig_likes_100": {
                "name": "إعجابات إنستجرام",
                "description": "إعجابات لمنشور إنستجرام",
                "smm_service_id": "3310",
                "quantity": 100,
                "points_cost": 250,
                "cost": 250,
                "emoji": "❤️",
            },
        },
    },
    "twitter": {
        "name": "تويتر (X) ⬛",
        "services": {
            "x_followers_100": {
                "name": "متابع تويتر (X)",
                "description": "متابعون لحساب تويتر (X)",
                "smm_service_id": "9008",
                "quantity": 100,
                "cost": 500,
                "emoji": "👥",
            },
            "x_likes_100": {
                "name": "100 لايك تويتر (X)",
                "description": "إعجابات لمنشور تويتر (X)",
                "smm_service_id": "9009",
                "quantity": 100,
                "cost": 300,
                "emoji": "❤️",
            },
        },
    },
    "referrals": {
        "name": "إحالات البوتات 👥",
        "services": {
            REFERRAL_SERVICE_KEY: {
                "name": "رشق 25 إحالة لبوت آخر 👥",
                "description": "25 إحالة لبوتك عبر رابط Start",
                "quantity": REFERRAL_QUANTITY,
                "cost": REFERRAL_COST,
                "emoji": "",
                "kind": "referral",
            },
        },
    },
}


def flatten_services() -> dict[str, dict]:
    """يبني فهرساً مسطحاً للتوافق مع الطلبات المحفوظة في قاعدة البيانات."""
    return {
        service_key: {
            **service,
            "category_key": category_key,
            "category_name": category["name"],
        }
        for category_key, category in SERVICES.items()
        for service_key, service in category["services"].items()
    }


SERVICE_INDEX = flatten_services()


def service_display_name(service_key: str, service: dict | None = None) -> str:
    """يعيد اسم العرض بدون أرقام الكمية المكررة داخل اسم الخدمة."""
    service = service or SERVICE_INDEX.get(service_key, {})
    name = str(service.get("name", service_key))
    if service_key in {"tg_100", "tg_views_1k", "x_followers_100"}:
        name = re.sub(r"^\s*\d+\s*", "", name)
    return name.strip()

# ══════════════════════════════════════════════════════════════════════════════
# ─── حالة المحادثة في الذاكرة ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# user_state[user_id] = {
#     "step":        "awaiting_link" | "awaiting_confirm" | "awaiting_referral_link"
#                    | "awaiting_withdrawal_amount" | "awaiting_withdrawal_account",
#     "service_key": str,
#     "link":        str | None,
# }
user_state: dict[int, dict] = {}

# ══════════════════════════════════════════════════════════════════════════════
# ─── نظام مكافحة البوتات (Anti-Bot Verification) ──────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
import random as _random

ANTI_BOT_SESSION_TTL = 300  # 5 دقائق
ANTI_BOT_MAX_ATTEMPTS = 3

# جلسة التحقق المؤقتة: user_id → {answer, expires_at, attempts}
anti_bot_sessions: dict[int, dict] = {}


def _generate_math_challenge() -> tuple[str, int, list[int]]:
    """يولّد عملية حسابية عشوائية ويعيد (السؤال, الإجابة الصحيحة, الخيارات)."""
    op = _random.choice(["+", "-", "*"])
    if op == "+":
        a, b = _random.randint(2, 50), _random.randint(2, 50)
        answer = a + b
        symbol = "+"
    elif op == "-":
        a = _random.randint(10, 60)
        b = _random.randint(2, a - 1)
        answer = a - b
        symbol = "-"
    else:
        a, b = _random.randint(2, 12), _random.randint(2, 12)
        answer = a * b
        symbol = "×"

    question = f"{a} {symbol} {b}"

    # توليد 3 إجابات خاطئة مختلفة
    wrong_answers: set[int] = set()
    while len(wrong_answers) < 3:
        offset = _random.choice([-3, -2, -1, 1, 2, 3, 4, 5])
        wrong = answer + offset
        if wrong != answer and wrong > 0 and wrong not in wrong_answers:
            wrong_answers.add(wrong)

    options = [answer] + list(wrong_answers)
    _random.shuffle(options)
    return question, answer, options


def start_anti_bot_verification(user_id: int) -> tuple[str, list[int]]:
    """يبدأ جلسة تحقق جديدة ويعيد (السؤال, الخيارات)."""
    question, answer, options = _generate_math_challenge()
    anti_bot_sessions[user_id] = {
        "answer": answer,
        "expires_at": time.time() + ANTI_BOT_SESSION_TTL,
        "attempts": 0,
        "question": question,
        "options": options,
    }
    return question, options


def verify_anti_bot_answer(user_id: int, chosen: int) -> tuple[bool, str]:
    """
    يتحقق من إجابة المستخدم.
    يعيد (نجاح, رسالة).
    """
    session = anti_bot_sessions.get(user_id)
    if session is None:
        return False, "expired"

    if time.time() > session["expires_at"]:
        del anti_bot_sessions[user_id]
        return False, "expired"

    session["attempts"] += 1

    if chosen == session["answer"]:
        del anti_bot_sessions[user_id]
        return True, "correct"

    if session["attempts"] >= ANTI_BOT_MAX_ATTEMPTS:
        del anti_bot_sessions[user_id]
        return False, "max_attempts"

    return False, "wrong"


def is_user_verified(user_id: int) -> bool:
    """يتحقق هل المستخدم أتم التحقق بنجاح."""
    user = get_user(user_id)
    if user is None:
        return False
    return bool(user["is_verified"])


def mark_user_verified(user_id: int):
    """يُعلّم المستخدم كमتحقق منه."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_verified = 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()


def build_verification_keyboard(options: list[int]) -> InlineKeyboardMarkup:
    """يُنشئ أزرار الإجابات."""
    markup = InlineKeyboardMarkup()
    # صفوف من 2 أزرار
    for i in range(0, len(options), 2):
        row = []
        for j in range(i, min(i + 2, len(options))):
            row.append(InlineKeyboardButton(
                str(options[j]),
                callback_data=f"antibot_{options[j]}",
            ))
        markup.row(*row)
    return markup


def show_anti_bot_challenge(chat_id: int, user_id: int):
    """يعرض اختبار مكافحة البوتات."""
    question, options = start_anti_bot_verification(user_id)
    text = (
        "🤖 <b>للتحقق من أنك لست روبوتًا</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"احسب العملية التالية:\n\n"
        f"🧮 <b>{question} = ؟</b>\n\n"
        "اختر الإجابة الصحيحة:"
    )
    bot.send_message(
        chat_id, text,
        reply_markup=build_verification_keyboard(options),
    )


def require_verified_user(call_or_message) -> bool:
    """
    دالة حماية مركّزة.
    تمنع الوصول إذا لم يكمل المستخدم التحقق.
    تُعيد True إذا كان المستخدم متحققًا.
    """
    if hasattr(call_or_message, "from_user"):
        uid = call_or_message.from_user.id
    elif hasattr(call_or_message, "from_user"):
        uid = call_or_message.from_user.id
    else:
        return True

    if is_user_verified(uid):
        return True

    # المستخدم غير متحقق — يُمنع من الوصول
    if hasattr(call_or_message, "id"):
        # CallbackQuery
        bot.answer_callback_query(
            call_or_message.id,
            "🤖 أكمل التحقق أولاً.",
            show_alert=True,
        )
        chat_id = call_or_message.message.chat.id
        msg_id = call_or_message.message.message_id
    else:
        chat_id = call_or_message.chat.id
        msg_id = None

    show_anti_bot_challenge(chat_id, uid)
    return False


# ══════════════════════════════════════════════════════════════════════════════
# ─── قاعدة البيانات ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# Use the uploaded database by default.  BOT_DB_PATH can be used by the
# workflow to point at a different copy without changing the bot code.
DB_PATH = os.environ.get(
    "BOT_DB_PATH",
    str(Path(__file__).with_name("ganaihat_fresh.db")),
)


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def refresh_promotion_packages() -> dict[str, dict]:
    """يحمّل باقات الترويج النشطة من قاعدة البيانات مع الحفاظ على الافتراضي."""
    global PROMOTION_PACKAGES
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT package_key, label, target_subscribers, points_cost "
                "FROM promotion_packages WHERE active = 1 "
                "ORDER BY target_subscribers, package_key"
            ).fetchall()
    except sqlite3.OperationalError:
        return PROMOTION_PACKAGES

    PROMOTION_PACKAGES = {
        str(row["package_key"]): {
            "label": row["label"],
            "target_subscribers": int(row["target_subscribers"]),
            "points_cost": int(row["points_cost"]),
            "usd_price": float(row["points_cost"]) / 100 / float(EGP_PER_USD),
        }
        for row in rows
    }
    return PROMOTION_PACKAGES


def get_all_promotion_packages() -> list[dict]:
    """يعيد كل الباقات للمشرف، بما فيها الباقات المعطلة."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT package_key, label, target_subscribers, points_cost, active "
            "FROM promotion_packages "
            "ORDER BY active DESC, target_subscribers, package_key"
        ).fetchall()
    return [dict(row) for row in rows]


def save_promotion_package(
    package_key: str,
    label: str,
    target_subscribers: int,
    points_cost: int,
) -> bool:
    """ينشئ أو يحدّث باقة ترويج مع إبقائها متاحة للمستخدمين."""
    if (
        not package_key
        or not label
        or target_subscribers < 1
        or points_cost < 1
    ):
        return False
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO promotion_packages "
            "(package_key, label, target_subscribers, points_cost, active, updated_at) "
            "VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP) "
            "ON CONFLICT(package_key) DO UPDATE SET "
            "label = excluded.label, "
            "target_subscribers = excluded.target_subscribers, "
            "points_cost = excluded.points_cost, "
            "active = 1, updated_at = CURRENT_TIMESTAMP",
            (package_key, label[:100], target_subscribers, points_cost),
        )
        conn.commit()
    refresh_promotion_packages()
    return True


def set_promotion_package_active(package_key: str, active: bool) -> bool:
    """يفعّل أو يعطّل باقة دون حذف الحملات القديمة المرتبطة بها."""
    with get_connection() as conn:
        updated = conn.execute(
            "UPDATE promotion_packages SET active = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE package_key = ?",
            (1 if active else 0, package_key),
        ).rowcount
        conn.commit()
    refresh_promotion_packages()
    return updated == 1


def promotion_package_price(package: dict) -> str:
    """يعرض سعر الباقة بالعملة المصرية والدولار من قيمة القروش المحفوظة."""
    return format_balance(int(package["points_cost"]))


def next_promotion_package_key(label: str, target_subscribers: int, points_cost: int) -> str:
    """ينشئ مفتاحاً داخلياً ثابتاً وآمناً للباقة الجديدة."""
    base = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower()
    if not base:
        base = f"package_{target_subscribers}_{points_cost}"
    base = f"custom_{base[:28]}"
    key = base
    suffix = 2
    with get_connection() as conn:
        while conn.execute(
            "SELECT 1 FROM promotion_packages WHERE package_key = ?", (key,)
        ).fetchone():
            key = f"{base[:36]}_{suffix}"
            suffix += 1
    return key


def refresh_required_channels() -> list[dict]:
    """يبني قائمة القنوات من القنوات الثابتة والحملات المدفوعة النشطة."""
    channels = [dict(channel) for channel in BASE_REQUIRED_CHANNELS]
    try:
        with get_connection() as conn:
            campaigns = conn.execute(
                "SELECT id, channel_username, channel_title, target_subscribers "
                "FROM promoted_channel_campaigns "
                "WHERE status = 'active' AND subscribers_count < target_subscribers "
                "ORDER BY id"
            ).fetchall()
    except sqlite3.OperationalError:
        campaigns = []

    for campaign in campaigns:
        channels.append({
            "username": campaign["channel_username"],
            "name": f"{campaign['channel_title']} 📣",
            "reward": PROMOTED_CHANNEL_REWARD,
            "task_key": f"promotion_{campaign['id']}",
            "promotion_id": campaign["id"],
            "target_subscribers": campaign["target_subscribers"],
        })

    REQUIRED_CHANNELS[:] = channels
    return REQUIRED_CHANNELS


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                first_name   TEXT    NOT NULL,
                last_name    TEXT,
                username     TEXT,
                joined_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                points       INTEGER  DEFAULT 0,
                referred_by  INTEGER  REFERENCES users(user_id),
                activation_status INTEGER NOT NULL DEFAULT 0
            )
        """)
        # ترقية قواعد البيانات القديمة دون حذف المستخدمين أو النقاط.
        # بعض النسخ الأولى أنشأت جدول users قبل إضافة نظام الإحالات.
        user_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)")
        }
        if "referred_by" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN referred_by INTEGER "
                "REFERENCES users(user_id)"
            )
        if "activation_status" not in user_columns:
            # الحسابات القديمة سبق أن دخلت النظام، فلا تُقفل بأثر رجعي.
            conn.execute(
                "ALTER TABLE users ADD COLUMN activation_status INTEGER "
                "NOT NULL DEFAULT 1"
            )
        if "withdrawal_blocked" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN withdrawal_blocked INTEGER "
                "NOT NULL DEFAULT 0"
            )
        if "fraud_reason" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN fraud_reason TEXT"
            )
        if "fraud_marked_at" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN fraud_marked_at DATETIME"
            )
        if "balance_cents" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN balance_cents INTEGER NOT NULL DEFAULT 0"
            )
        if "balance_migrated_at" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN balance_migrated_at DATETIME"
            )
        if "is_verified" not in user_columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_verified INTEGER "
                "NOT NULL DEFAULT 0"
            )
        conn.execute(
            "UPDATE users SET balance_cents = points, "
            "balance_migrated_at = CURRENT_TIMESTAMP "
            "WHERE balance_migrated_at IS NULL"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id  INTEGER NOT NULL REFERENCES users(user_id),
                referred_id  INTEGER NOT NULL REFERENCES users(user_id),
                rewarded_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        referral_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(referrals)")
        }
        if "reward_status" not in referral_columns:
            # الإحالات الموجودة قبل نظام التعليق تُعد مكافآتها مصروفة،
            # حتى لا تتغير أرصدة المستخدمين بأثر رجعي قبل الفحص.
            conn.execute(
                "ALTER TABLE referrals ADD COLUMN reward_status TEXT "
                "NOT NULL DEFAULT 'rewarded'"
            )
        if "reward_points" not in referral_columns:
            conn.execute(
                "ALTER TABLE referrals ADD COLUMN reward_points INTEGER "
                "NOT NULL DEFAULT 10"
            )
        if "eligible_at" not in referral_columns:
            conn.execute(
                "ALTER TABLE referrals ADD COLUMN eligible_at DATETIME"
            )
        if "reversed_at" not in referral_columns:
            conn.execute(
                "ALTER TABLE referrals ADD COLUMN reversed_at DATETIME"
            )
        if "reversal_reason" not in referral_columns:
            conn.execute(
                "ALTER TABLE referrals ADD COLUMN reversal_reason TEXT"
            )
        if "last_checked_at" not in referral_columns:
            conn.execute(
                "ALTER TABLE referrals ADD COLUMN last_checked_at DATETIME"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_referrals_referrer "
            "ON referrals(referrer_id)"
        )
        # مكافآت الإحالة المعلقة لم تعد تنتظر 24 ساعة؛ قيمتها التشغيلية
        # الحالية هي 0.50 جنيه، وتُصرف عند فتح الحساب المحال فقط.
        conn.execute(
            "UPDATE referrals SET reward_points = ? "
            "WHERE reward_status = 'pending'",
            (REFERRAL_REWARD,),
        )
        # إزالة الحظر القديم الذي سببه فحص احتجاز الإحالات فقط.
        conn.execute(
            "UPDATE users SET withdrawal_blocked = 0, "
            "fraud_reason = NULL, fraud_marked_at = NULL "
            "WHERE fraud_reason = 'invalid_referral_membership'"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_completions (
                user_id   INTEGER NOT NULL,
                task_key  TEXT    NOT NULL,
                done_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, task_key)
            )
        """)
        # سجل مستقل لمكافآت القنوات حتى نستطيع خصم المبلغ الفعلي وإرجاعه
        # مرة واحدة فقط عند عودة المستخدم، حتى لو كانت نقاطه قد استُخدمت.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_reward_ledger (
                user_id          INTEGER NOT NULL REFERENCES users(user_id),
                task_key         TEXT    NOT NULL,
                reward_points    INTEGER NOT NULL,
                status           TEXT    NOT NULL DEFAULT 'granted',
                deducted_points  INTEGER NOT NULL DEFAULT 0,
                granted_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                deducted_at      DATETIME,
                restored_at      DATETIME,
                PRIMARY KEY (user_id, task_key)
            )
        """)
        # ترحيل مكافآت القنوات القديمة إلى السجل دون تغيير الأرصدة.
        for channel in REQUIRED_CHANNELS:
            conn.execute(
                "INSERT OR IGNORE INTO channel_reward_ledger "
                "(user_id, task_key, reward_points, status) "
                "SELECT user_id, ?, ?, 'granted' FROM task_completions "
                "WHERE task_key = ?",
                (channel["task_key"], channel["reward"], channel["task_key"]),
            )
        # جدول طلبات المتجر
        conn.execute("""
            CREATE TABLE IF NOT EXISTS smm_orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(user_id),
                service_key     TEXT    NOT NULL,
                smm_order_id    TEXT,               -- رقم الطلب من السيرفر
                link            TEXT    NOT NULL,
                quantity        INTEGER NOT NULL,
                points_spent    INTEGER NOT NULL,
                status          TEXT    DEFAULT 'pending',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        order_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(smm_orders)")
        }
        if "amount_cents" not in order_columns:
            conn.execute(
                "ALTER TABLE smm_orders ADD COLUMN amount_cents INTEGER"
            )
        conn.execute(
            "UPDATE smm_orders SET amount_cents = points_spent "
            "WHERE amount_cents IS NULL"
        )
        # جدول الأسعار القابلة للتعديل لكل خدمة. تبقى تكلفة الخدمة الفعلية
        # مستقلة عن حد الإعلان العام البالغ 100 نقطة.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS service_price_settings (
                service_key TEXT PRIMARY KEY,
                price_points INTEGER NOT NULL,
                price_cents INTEGER,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        service_price_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(service_price_settings)")
        }
        if "price_cents" not in service_price_columns:
            conn.execute(
                "ALTER TABLE service_price_settings ADD COLUMN price_cents INTEGER"
            )
        # جدول الكميات القابلة للتعديل لكل خدمة. تبقى الكمية مستقلة عن السعر،
        # حتى يستطيع الأدمن مثلاً بيع متابع واحد مقابل 50 نقطة.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS service_quantity_settings (
                service_key   TEXT PRIMARY KEY,
                quantity      INTEGER NOT NULL,
                updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for service_key, service in SERVICE_INDEX.items():
            conn.execute(
                "INSERT OR IGNORE INTO service_price_settings "
                "(service_key, price_points, price_cents) VALUES (?, ?, ?)",
                (service_key, service["cost"], service["cost"]),
            )
        conn.execute(
            "UPDATE service_price_settings SET price_cents = price_points "
            "WHERE price_cents IS NULL"
        )
        for service_key, service in SERVICE_INDEX.items():
            conn.execute(
                "INSERT OR IGNORE INTO service_quantity_settings "
                "(service_key, quantity) VALUES (?, ?)",
                (service_key, service["quantity"]),
            )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS currency_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for setting_key, setting_value in {
            "currency_mode": "dual",
            "point_to_egp": "0.01",
            "egp_per_usd": "50",
            "ad_reward_cents": str(AD_REWARD),
            "min_withdrawal_cents": str(WITHDRAWAL_MIN_POINTS),
        }.items():
            conn.execute(
                "INSERT OR IGNORE INTO currency_settings "
                "(setting_key, setting_value) VALUES (?, ?)",
                (setting_key, setting_value),
            )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_ad_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL UNIQUE,
                title       TEXT NOT NULL DEFAULT 'إعلان مشاهدة',
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if ADVERTISING_URL:
            conn.execute(
                "INSERT OR IGNORE INTO watch_ad_links (url, title) "
                "VALUES (?, 'الإعلان الأساسي')",
                (ADVERTISING_URL,),
            )
        # إيصالات شحن النقاط وربط إشعار المشرف بالعميل صاحب الإيصال
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_receipts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(user_id),
                file_id           TEXT    NOT NULL,
                admin_message_id  INTEGER,
                status            TEXT    DEFAULT 'pending',
                created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # طلبات رشق الإحالات التي ينفذها مستخدمون آخرون عبر المهام اليومية.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_tasks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_id            INTEGER NOT NULL REFERENCES users(user_id),
                referral_link       TEXT    NOT NULL,
                quantity_requested  INTEGER NOT NULL,
                quantity_remaining  INTEGER NOT NULL,
                points_spent        INTEGER NOT NULL,
                amount_cents        INTEGER,
                status              TEXT    NOT NULL DEFAULT 'active',
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        referral_task_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(referral_tasks)")
        }
        if "amount_cents" not in referral_task_columns:
            conn.execute(
                "ALTER TABLE referral_tasks ADD COLUMN amount_cents INTEGER"
            )
        conn.execute(
            "UPDATE referral_tasks SET amount_cents = points_spent "
            "WHERE amount_cents IS NULL"
        )
        # طلبات تنفيذ المهام المدفوعة: لا تُصرف المكافأة قبل موافقة العميل.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_task_claims (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id              INTEGER NOT NULL REFERENCES referral_tasks(id),
                worker_id            INTEGER NOT NULL REFERENCES users(user_id),
                buyer_id             INTEGER NOT NULL REFERENCES users(user_id),
                status               TEXT    NOT NULL DEFAULT 'pending_client',
                slot_reserved        INTEGER NOT NULL DEFAULT 1,
                created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
                client_decided_at    DATETIME,
                resolved_at          DATETIME
            )
        """)
        claim_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(referral_task_claims)")
        }
        if "slot_reserved" not in claim_columns:
            conn.execute(
                "ALTER TABLE referral_task_claims ADD COLUMN "
                "slot_reserved INTEGER NOT NULL DEFAULT 1"
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_referral_task_claim_worker "
            "ON referral_task_claims(task_id, worker_id)"
        )
        # شكاوى المؤدين المرفقة بإثبات عند رفض العميل أو عدم دفع المستحق.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_task_complaints (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id          INTEGER NOT NULL REFERENCES referral_task_claims(id),
                task_id           INTEGER NOT NULL REFERENCES referral_tasks(id),
                worker_id         INTEGER NOT NULL REFERENCES users(user_id),
                file_id           TEXT    NOT NULL,
                admin_message_id  INTEGER,
                claim_status_before TEXT  NOT NULL DEFAULT 'pending_client',
                status            TEXT    NOT NULL DEFAULT 'pending',
                created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at       DATETIME
            )
        """)
        complaint_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(referral_task_complaints)")
        }
        if "claim_status_before" not in complaint_columns:
            conn.execute(
                "ALTER TABLE referral_task_complaints ADD COLUMN "
                "claim_status_before TEXT NOT NULL DEFAULT 'pending_client'"
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_referral_task_complaint_pending "
            "ON referral_task_complaints(claim_id) WHERE status = 'pending'"
        )
        # المهام اليدوية التي يضيفها المشرف وتظهر لجميع المستخدمين.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_tasks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                title               TEXT    NOT NULL,
                task_link           TEXT    NOT NULL,
                task_type           TEXT    NOT NULL DEFAULT 'social_manual',
                target_reference    TEXT,
                task_instructions   TEXT    NOT NULL DEFAULT '',
                reward_points       INTEGER NOT NULL,
                quantity_requested  INTEGER NOT NULL,
                quantity_remaining  INTEGER NOT NULL,
                status              TEXT    NOT NULL DEFAULT 'active',
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        manual_task_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(manual_tasks)")
        }
        if "task_type" not in manual_task_columns:
            conn.execute(
                "ALTER TABLE manual_tasks ADD COLUMN task_type TEXT "
                "NOT NULL DEFAULT 'social_manual'"
            )
        if "target_reference" not in manual_task_columns:
            conn.execute(
                "ALTER TABLE manual_tasks ADD COLUMN target_reference TEXT"
            )
        if "task_instructions" not in manual_task_columns:
            conn.execute(
                "ALTER TABLE manual_tasks ADD COLUMN task_instructions TEXT "
                "NOT NULL DEFAULT ''"
            )
        conn.execute(
            "UPDATE manual_tasks SET target_reference = task_link "
            "WHERE target_reference IS NULL OR TRIM(target_reference) = ''"
        )
        # إثباتات المهام الخارجية التي يراجعها المشرف قبل منح المكافأة.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_task_reviews (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(user_id),
                task_id           INTEGER NOT NULL REFERENCES manual_tasks(id),
                file_id           TEXT    NOT NULL,
                admin_message_id  INTEGER,
                status            TEXT    NOT NULL DEFAULT 'pending',
                created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at       DATETIME
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_manual_task_reviews_pending "
            "ON manual_task_reviews(user_id, task_id) "
            "WHERE status = 'pending'"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_reviews (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(user_id),
                file_id           TEXT    NOT NULL,
                ad_link_id        INTEGER,
                admin_message_id  INTEGER,
                reward_cents      INTEGER NOT NULL DEFAULT 50,
                status            TEXT    NOT NULL DEFAULT 'pending',
                created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at       DATETIME
            )
        """)
        ad_review_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(ad_reviews)")
        }
        if "reward_cents" not in ad_review_columns:
            conn.execute(
                "ALTER TABLE ad_reviews ADD COLUMN reward_cents INTEGER "
                "NOT NULL DEFAULT 50"
            )
        if "ad_link_id" not in ad_review_columns:
            conn.execute(
                "ALTER TABLE ad_reviews ADD COLUMN ad_link_id INTEGER"
            )
        conn.execute(
            "UPDATE ad_reviews SET reward_cents = ? WHERE reward_cents IS NULL OR reward_cents < 1",
            (AD_REWARD,),
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_ad_reviews_pending_user "
            "ON ad_reviews(user_id) WHERE status = 'pending'"
        )
        # طلبات سحب الأرباح وربطها برسالة إشعار المشرف.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_ads (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(user_id),
                title             TEXT NOT NULL,
                description       TEXT NOT NULL,
                link              TEXT NOT NULL,
                price_cents       INTEGER NOT NULL DEFAULT 0,
                admin_message_id  INTEGER,
                published_task_id INTEGER,
                status            TEXT NOT NULL DEFAULT 'pending',
                created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_at       DATETIME
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_user_ads_pending_user "
            "ON user_ads(user_id) WHERE status = 'pending'"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawal_requests (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL REFERENCES users(user_id),
                points_amount     INTEGER NOT NULL,
                withdrawal_method TEXT    NOT NULL,
                account_details   TEXT    NOT NULL,
                admin_message_id  INTEGER,
                status            TEXT    NOT NULL DEFAULT 'pending',
                created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at      DATETIME
            )
        """)
        withdrawal_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(withdrawal_requests)")
        }
        if "amount_cents" not in withdrawal_columns:
            conn.execute(
                "ALTER TABLE withdrawal_requests ADD COLUMN amount_cents INTEGER"
            )
        conn.execute(
            "UPDATE withdrawal_requests SET amount_cents = points_amount "
            "WHERE amount_cents IS NULL"
        )
        # V2 withdrawal fields (USDT-based accounting)
        for col_sql in [
            "ALTER TABLE withdrawal_requests ADD COLUMN method_code TEXT",
            "ALTER TABLE withdrawal_requests ADD COLUMN network_code TEXT",
            "ALTER TABLE withdrawal_requests ADD COLUMN destination TEXT",
            "ALTER TABLE withdrawal_requests ADD COLUMN requested_egp_cents INTEGER",
            "ALTER TABLE withdrawal_requests ADD COLUMN usdt_micro INTEGER",
            "ALTER TABLE withdrawal_requests ADD COLUMN egp_equivalent_cents INTEGER",
            "ALTER TABLE withdrawal_requests ADD COLUMN exchange_rate_micro INTEGER",
            "ALTER TABLE withdrawal_requests ADD COLUMN rate_fetched_at TEXT",
            "ALTER TABLE withdrawal_requests ADD COLUMN rate_provider TEXT",
            "ALTER TABLE withdrawal_requests ADD COLUMN fee_cents INTEGER DEFAULT 0",
            "ALTER TABLE withdrawal_requests ADD COLUMN refunded INTEGER DEFAULT 0",
            "ALTER TABLE withdrawal_requests ADD COLUMN admin_id INTEGER",
            "ALTER TABLE withdrawal_requests ADD COLUMN transaction_reference TEXT",
        ]:
            col_name = col_sql.split("ADD COLUMN ")[1].split(" ")[0]
            if col_name not in withdrawal_columns:
                conn.execute(col_sql)
        # Backfill amount_cents for legacy rows lacking method_code
        conn.execute(
            "UPDATE withdrawal_requests SET method_code = 'legacy' "
            "WHERE method_code IS NULL"
        )
        conn.execute(
            "UPDATE withdrawal_requests SET refunded = 0 WHERE refunded IS NULL"
        )
        conn.execute(
            "UPDATE withdrawal_requests SET fee_cents = 0 WHERE fee_cents IS NULL"
        )
        # الحملات المدفوعة التي تضيف قناة المعلن إلى شروط التفعيل.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promoted_channel_campaigns (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                advertiser_id       INTEGER NOT NULL REFERENCES users(user_id),
                channel_username    TEXT NOT NULL,
                channel_title       TEXT NOT NULL,
                channel_id          INTEGER,
                package_key         TEXT NOT NULL,
                target_subscribers  INTEGER NOT NULL,
                subscribers_count   INTEGER NOT NULL DEFAULT 0,
                points_cost         INTEGER NOT NULL,
                status              TEXT NOT NULL DEFAULT 'active',
                activated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at        DATETIME
            )
        """)
        campaign_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(promoted_channel_campaigns)"
            )
        }
        if "amount_cents" not in campaign_columns:
            conn.execute(
                "ALTER TABLE promoted_channel_campaigns "
                "ADD COLUMN amount_cents INTEGER"
            )
        conn.execute(
            "UPDATE promoted_channel_campaigns SET amount_cents = points_cost "
            "WHERE amount_cents IS NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_promoted_channel_active "
            "ON promoted_channel_campaigns(channel_username) "
            "WHERE status = 'active'"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promoted_channel_members (
                campaign_id   INTEGER NOT NULL
                    REFERENCES promoted_channel_campaigns(id),
                user_id       INTEGER NOT NULL REFERENCES users(user_id),
                subscribed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (campaign_id, user_id)
            )
        """)
        # باقات الترويج التي يديرها المشرف من لوحة التحكم.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promotion_packages (
                package_key        TEXT PRIMARY KEY,
                label              TEXT NOT NULL,
                target_subscribers INTEGER NOT NULL,
                points_cost        INTEGER NOT NULL,
                active             INTEGER NOT NULL DEFAULT 1,
                created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for package_key, package in PROMOTION_PACKAGES.items():
            conn.execute(
                "INSERT OR IGNORE INTO promotion_packages "
                "(package_key, label, target_subscribers, points_cost) "
                "VALUES (?, ?, ?, ?)",
                (
                    package_key,
                    package["label"],
                    package["target_subscribers"],
                    package["points_cost"],
                ),
            )

        # ─── جدول عروض CPAGrip ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cpagrip_offers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(user_id),
                offer_id        TEXT    NOT NULL,
                tracking_id     TEXT    NOT NULL UNIQUE,
                title           TEXT,
                payout_raw      TEXT,
                offerlink       TEXT    NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'pending',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at    DATETIME
            )
        """)
        
        # ─── جدول منع تكرار المعاملات (Idempotency) ──────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_transactions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key    TEXT    NOT NULL UNIQUE,
                user_id            INTEGER NOT NULL,
                amount_cents       INTEGER NOT NULL,
                processed_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    refresh_promotion_packages()
    refresh_required_channels()
    release_pending_referrals_for_activated_users()


def normalize_channel_input(value: str) -> str | None:
    """يحوّل @channel أو رابط t.me العام إلى معرف قناة قابل للفحص."""
    raw = (value or "").strip()
    if not raw:
        return None

    if raw.startswith("@"):
        username = raw[1:].strip()
    else:
        parsed = urlsplit(raw)
        host = (parsed.netloc or "").lower().split(":")[0]
        path = parsed.path.strip("/")
        if parsed.scheme not in ("http", "https") or host not in {
            "t.me", "telegram.me", "www.t.me", "www.telegram.me",
        } or not path or "/" in path:
            return None
        username = path

    if username.startswith("@"):
        username = username[1:]
    if not username or not username.replace("_", "").isalnum():
        return None
    return f"@{username}"


def validate_promoted_channel(channel_username: str, advertiser_id: int) -> dict:
    """
    يفحص القناة قبل الخصم:
    - القناة عامة ويمكن الوصول إليها.
    - المعلن مشرف/مالك فيها.
    - البوت مشرف فيها حتى يستطيع فحص الاشتراك.
    """
    try:
        chat = bot.get_chat(channel_username)
        if getattr(chat, "type", None) != "channel":
            return {"ok": False, "error": "أرسل معرف قناة Telegram عامة، وليس حساباً شخصياً أو مجموعة."}

        advertiser_member = bot.get_chat_member(channel_username, advertiser_id)
        if getattr(advertiser_member, "status", None) not in {
            "administrator", "creator",
        }:
            return {"ok": False, "error": "يجب أن تكون مشرفاً أو مالكاً للقناة المرسلة."}

        bot_member = bot.get_chat_member(channel_username, bot.get_me().id)
        if getattr(bot_member, "status", None) not in {
            "administrator", "creator",
        }:
            return {
                "ok": False,
                "error": "يجب إضافة البوت مشرفاً في القناة أولاً حتى يتمكن من التحقق من الاشتراكات.",
            }
    except Exception:
        return {
            "ok": False,
            "error": (
                "تعذر الوصول إلى القناة. تأكد من صحة المعرف وأن القناة عامة، "
                "ثم أضف البوت مشرفاً فيها."
            ),
        }

    title = getattr(chat, "title", None) or channel_username
    return {
        "ok": True,
        "username": channel_username,
        "title": title[:200],
        "channel_id": getattr(chat, "id", None),
    }


def create_promoted_channel_campaign(
    advertiser_id: int,
    channel: dict,
    package_key: str,
) -> dict | None:
    """ينشئ حملة نشطة ويخصم تكلفتها من المحفظة في معاملة واحدة."""
    package = PROMOTION_PACKAGES.get(package_key)
    if package is None:
        return None

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT 1 FROM promoted_channel_campaigns "
            "WHERE channel_username = ? AND status = 'active'",
            (channel["username"],),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            return None

        deducted = conn.execute(
            "UPDATE users SET balance_cents = balance_cents - ? "
            "WHERE user_id = ? AND balance_cents >= ?",
            (package["points_cost"], advertiser_id, package["points_cost"]),
        ).rowcount
        if deducted != 1:
            conn.rollback()
            return None

        cursor = conn.execute(
            "INSERT INTO promoted_channel_campaigns "
            "(advertiser_id, channel_username, channel_title, channel_id, "
            "package_key, target_subscribers, points_cost, amount_cents) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                advertiser_id,
                channel["username"],
                channel["title"],
                channel.get("channel_id"),
                package_key,
                package["target_subscribers"],
                package["points_cost"],
                package["points_cost"],
            ),
        )
        campaign_id = cursor.lastrowid
        campaign = conn.execute(
            "SELECT * FROM promoted_channel_campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        conn.commit()

    refresh_required_channels()
    return dict(campaign) if campaign else None


def get_active_promoted_campaign(channel_username: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM promoted_channel_campaigns "
            "WHERE channel_username = ? AND status = 'active'",
            (channel_username,),
        ).fetchone()


def record_promoted_subscriber(campaign_id: int, user_id: int) -> dict | None:
    """
    يسجل مشتركاً جديداً من مستخدمي البوت مرة واحدة.
    المستخدمون الذين دخلوا البوت قبل تفعيل الحملة لا يُحتسبون ضمن العدد المدفوع.
    """
    completed_campaign = None
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute(
            "INSERT OR IGNORE INTO promoted_channel_members "
            "(campaign_id, user_id) "
            "SELECT c.id, u.user_id "
            "FROM promoted_channel_campaigns c "
            "JOIN users u ON u.user_id = ? "
            "WHERE c.id = ? AND c.status = 'active' "
            "AND c.subscribers_count < c.target_subscribers "
            "AND c.advertiser_id != u.user_id "
            "AND datetime(u.joined_at) >= datetime(c.activated_at)",
            (user_id, campaign_id),
        ).rowcount
        if inserted == 1:
            conn.execute(
                "UPDATE promoted_channel_campaigns "
                "SET subscribers_count = subscribers_count + 1 "
                "WHERE id = ? AND status = 'active' "
                "AND subscribers_count < target_subscribers",
                (campaign_id,),
            )

        campaign = conn.execute(
            "SELECT * FROM promoted_channel_campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        if (
            campaign is not None
            and campaign["status"] == "active"
            and campaign["subscribers_count"] >= campaign["target_subscribers"]
        ):
            conn.execute(
                "UPDATE promoted_channel_campaigns "
                "SET status = 'completed', completed_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'active'",
                (campaign_id,),
            )
            completed_campaign = conn.execute(
                "SELECT * FROM promoted_channel_campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()
        conn.commit()

    if completed_campaign is not None:
        refresh_required_channels()
        return dict(completed_campaign)
    return None


def notify_promotion_completed(campaign: dict):
    """يبلغ المعلن باكتمال حملته بعد إزالة القناة من شروط التفعيل."""
    try:
        bot.send_message(
            campaign["advertiser_id"],
            "✅ <b>اكتملت حملتك الإعلانية!</b>\n\n"
            f"وصلت قناة <b>{html.escape(campaign['channel_title'])}</b> "
            f"إلى {campaign['target_subscribers']} مشتركاً جديداً من مستخدمي البوت.\n"
            "تم إيقاف الإعلان وإزالة القناة تلقائياً من شروط التفعيل.",
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# ─── دوال المستخدمين ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def get_user(user_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def add_user(user_id: int, first_name: str,
             last_name: str | None, username: str | None,
             referred_by: int | None = None) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, first_name, last_name, username, referred_by, "
            "activation_status) VALUES (?, ?, ?, ?, ?, 0)",
            (user_id, first_name, last_name, username, referred_by),
        )
        conn.commit()
        return cur.rowcount == 1


def add_points(user_id: int, amount: int):
    """يضيف مبلغاً بالقروش إلى الرصيد المالي."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()


def is_account_active(user_id: int) -> bool:
    row = get_user(user_id)
    return row is not None and bool(row["activation_status"])


def get_channels_status(user_id: int) -> list[dict]:
    """يُعيد قائمة القنوات مع حالة الاشتراك الفعلي ومدى استلام المكافأة لكل قناة."""
    refresh_required_channels()
    result = []
    for ch in list(REQUIRED_CHANNELS):
        subscribed = is_subscribed(user_id, ch["username"])
        if subscribed and ch.get("promotion_id"):
            completed_campaign = record_promoted_subscriber(
                ch["promotion_id"], user_id
            )
            if completed_campaign:
                notify_promotion_completed(completed_campaign)
        result.append({
            **ch,
            "subscribed": subscribed,
            "rewarded":   is_task_done(user_id, ch["task_key"]),
        })
    return result


def enforce_channel_subscriptions(user_id: int, channels: list[dict] | None = None) -> list[dict]:
    """
    يفحص جميع القنوات ويجمّد الحساب عند فقدان أي اشتراك إلزامي:
    - يعمل حتى لو لم تكن مكافأة القناة قد مُنحت سابقاً.
    - يخصم الجزء المتاح من مكافأة القناة فقط، ويسجله للاسترداد عند العودة.
    """
    if channels is None:
        channels = get_channels_status(user_id)
    penalized = []
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT balance_cents FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if user is None:
            conn.rollback()
            return penalized
        current_points = int(user["balance_cents"] or 0)
        missing_channel = False
        for ch in channels:
            if ch["subscribed"]:
                continue
            missing_channel = True
            ledger = conn.execute(
                "SELECT status FROM channel_reward_ledger "
                "WHERE user_id = ? AND task_key = ?",
                (user_id, ch["task_key"]),
            ).fetchone()
            if ledger is not None and ledger["status"] == "deducted":
                penalized.append({**ch, "deducted_points": 0})
                continue

            deducted_points = (
                min(current_points, int(ch["reward"]))
                if ch["rewarded"] else 0
            )
            if deducted_points:
                conn.execute(
                    "UPDATE users SET balance_cents = balance_cents - ? WHERE user_id = ?",
                    (deducted_points, user_id),
                )
                current_points -= deducted_points
            if ch["rewarded"]:
                conn.execute(
                    "INSERT OR IGNORE INTO channel_reward_ledger "
                    "(user_id, task_key, reward_points, status, deducted_points) "
                    "VALUES (?, ?, ?, 'granted', 0)",
                    (user_id, ch["task_key"], ch["reward"]),
                )
                conn.execute(
                    "UPDATE channel_reward_ledger SET status = 'deducted', "
                    "deducted_points = ?, deducted_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = ? AND task_key = ?",
                    (deducted_points, user_id, ch["task_key"]),
                )
                conn.execute(
                    "DELETE FROM task_completions WHERE user_id = ? AND task_key = ?",
                    (user_id, ch["task_key"]),
                )
            penalized.append({**ch, "deducted_points": deducted_points})
        if missing_channel:
            conn.execute(
                "UPDATE users SET activation_status = 0 WHERE user_id = ?",
                (user_id,),
            )
        conn.commit()
    return penalized


def grant_channel_reward(user_id: int, channel: dict) -> dict | None:
    """يمنح مكافأة القناة أو يسترد الخصم السابق مرة واحدة بشكل ذري."""
    task_key = channel["task_key"]
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if user is None:
            conn.rollback()
            return None

        ledger = conn.execute(
            "SELECT status, deducted_points FROM channel_reward_ledger "
            "WHERE user_id = ? AND task_key = ?",
            (user_id, task_key),
        ).fetchone()
        if ledger is not None and ledger["status"] == "deducted":
            restored = int(ledger["deducted_points"] or 0)
            if restored:
                conn.execute(
                    "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
                    (restored, user_id),
                )
            conn.execute(
                "UPDATE channel_reward_ledger SET status = 'granted', "
                "deducted_points = 0, restored_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND task_key = ?",
                (user_id, task_key),
            )
            conn.execute(
                "INSERT OR IGNORE INTO task_completions (user_id, task_key) "
                "VALUES (?, ?)",
                (user_id, task_key),
            )
            conn.commit()
            return {"kind": "restored", "points": restored}

        if ledger is not None and ledger["status"] == "granted":
            conn.rollback()
            return None

        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (channel["reward"], user_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO channel_reward_ledger "
            "(user_id, task_key, reward_points, status, deducted_points, granted_at, "
            "deducted_at, restored_at) VALUES (?, ?, ?, 'granted', 0, CURRENT_TIMESTAMP, "
            "NULL, NULL)",
            (user_id, task_key, channel["reward"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO task_completions (user_id, task_key) "
            "VALUES (?, ?)",
            (user_id, task_key),
        )
        conn.commit()
        return {"kind": "granted", "points": int(channel["reward"])}


def restore_channel_rewards(
    user_id: int, channels: list[dict] | None = None
) -> list[dict]:
    """يعيد الخصومات المسجلة للقنوات التي عاد المستخدم للاشتراك بها."""
    if channels is None:
        channels = get_channels_status(user_id)
    restored = []
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for channel in channels:
            if not channel["subscribed"]:
                continue
            ledger = conn.execute(
                "SELECT deducted_points FROM channel_reward_ledger "
                "WHERE user_id = ? AND task_key = ? AND status = 'deducted'",
                (user_id, channel["task_key"]),
            ).fetchone()
            if ledger is None:
                continue
            amount = int(ledger["deducted_points"] or 0)
            if amount:
                conn.execute(
                    "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
                    (amount, user_id),
                )
            conn.execute(
                "UPDATE channel_reward_ledger SET status = 'granted', "
                "deducted_points = 0, restored_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND task_key = ?",
                (user_id, channel["task_key"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO task_completions (user_id, task_key) "
                "VALUES (?, ?)",
                (user_id, channel["task_key"]),
            )
            restored.append({**channel, "deducted_points": amount})
        conn.commit()
    return restored


def sync_channel_rewards(
    user_id: int, channels: list[dict] | None = None
) -> list[dict]:
    """يمنح مكافآت القنوات عند أول تفاعل حتى دون chat_member updates."""
    if channels is None:
        channels = get_channels_status(user_id)
    synced = []
    for channel in channels:
        if not channel["subscribed"] or channel["rewarded"]:
            continue
        result = grant_channel_reward(user_id, channel)
        if result is not None:
            synced.append({**channel, **result})
    return synced


def get_activation_requirements(user_id: int) -> tuple[bool, list]:
    """ينفذ فحص جميع القنوات والمهام اليدوية المطلوبة لفتح الحساب."""
    channels = get_channels_status(user_id)
    sync_channel_rewards(user_id, channels)
    channels = get_channels_status(user_id)
    all_subscribed = all(ch["subscribed"] for ch in channels)
    return all_subscribed, get_pending_manual_tasks(user_id)


def account_access_allowed(user_id: int) -> bool:
    """
    يسمح بالواجهة فقط عند التفعيل واجتياز شروط جميع القنوات.
    يطبّق عقوبة مغادرة القنوات تلقائياً عند كل فحص.
    """
    if is_admin(user_id):
        return True
    if get_user(user_id) is None:
        return False
    # نفحص القنوات حتى للحساب المجمد، لكي يكون السجل محدثاً دائماً.
    channels = get_channels_status(user_id)
    sync_channel_rewards(user_id, channels)
    channels = get_channels_status(user_id)
    enforce_channel_subscriptions(user_id, channels)
    channels = get_channels_status(user_id)
    # إعادة التحقق من الحالة بعد تطبيق العقوبات
    if not is_account_active(user_id):
        return False
    if not all(ch["subscribed"] for ch in channels):
        return False
    return not bool(get_pending_manual_tasks(user_id))


def activate_user(user_id: int) -> bool:
    """يفعّل الحساب ويصرف إحالة المُحال له مرة واحدة بشكل ذري."""
    all_subbed, pending_tasks = get_activation_requirements(user_id)
    if not all_subbed or pending_tasks:
        return False

    with get_connection() as conn:
        updated = conn.execute(
            "UPDATE users SET activation_status = 1, balance_cents = balance_cents + ? "
            "WHERE user_id = ? AND activation_status = 0",
            (ACTIVATION_REWARD, user_id),
        ).rowcount
        if updated != 1:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO task_completions (user_id, task_key) "
            "VALUES (?, 'join_channel')",
            (user_id,),
        )
        release_referral_reward(conn, user_id)
        return True


def reactivate_after_penalty(user_id: int) -> bool:
    """
    يعيد تفعيل حساب مجمّد بعد عودة المستخدم للاشتراك في جميع القنوات.
    يُعيد True إذا نجح فك التجميد، False إذا لم تكتمل الشروط بعد.
    """
    channels = get_channels_status(user_id)
    if not all(ch["subscribed"] for ch in channels):
        return False
    if get_pending_manual_tasks(user_id):
        return False
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET activation_status = 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
    return True


def deduct_points(user_id: int, amount: int) -> bool:
    """يخصم مبلغاً بالقروش ذرياً."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE users SET balance_cents = balance_cents - ? "
            "WHERE user_id = ? AND balance_cents >= ?",
            (amount, user_id, amount),
        )
        conn.commit()
        return cur.rowcount == 1


def calculate_selling_price(base_cost: int) -> int:
    """يحسب سعر البيع للعميل من التكلفة الأساسية بعد إضافة هامش الربح."""
    if base_cost <= 0:
        return 0
    return int((Decimal(str(base_cost)) * MARGIN_MULTIPLIER).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def get_service_price(service_key: str) -> int:
    """يعيد سعر البيع الحالي للعميل بالقروش (التكلفة الأساسية + هامش 30%)."""
    base_cost = 0
    with get_connection() as conn:
        row = conn.execute(
            "SELECT price_cents, price_points FROM service_price_settings "
            "WHERE service_key = ?",
            (service_key,),
        ).fetchone()
    if row is None:
        service = SERVICE_INDEX.get(service_key)
        base_cost = int(service["cost"]) if service else DEFAULT_ORDER_MIN_POINTS
    else:
        base_cost = int(row["price_cents"] or row["price_points"])
    return calculate_selling_price(base_cost)


def get_service_base_cost(service_key: str) -> int:
    """يعيد التكلفة الأساسية للخدمة بدون هامش ربح."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT price_cents, price_points FROM service_price_settings "
            "WHERE service_key = ?",
            (service_key,),
        ).fetchone()
    if row is None:
        service = SERVICE_INDEX.get(service_key)
        return int(service["cost"]) if service else DEFAULT_ORDER_MIN_POINTS
    return max(1, int(row["price_cents"] or row["price_points"]))


def set_service_price(service_key: str, price_cents: int) -> bool:
    """يحفظ سعر الخدمة بالقروش."""
    if service_key not in SERVICE_INDEX or price_cents < 1:
        return False
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO service_price_settings "
            "(service_key, price_points, price_cents, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(service_key) DO UPDATE SET "
            "price_points = excluded.price_points, price_cents = excluded.price_cents, "
            "updated_at = CURRENT_TIMESTAMP",
            (service_key, price_cents, price_cents),
        )
        conn.commit()
    return True


def get_ad_reward() -> int:
    """يعيد مكافأة مشاهدة الإعلان الحالية بالقروش."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT setting_value FROM currency_settings "
                "WHERE setting_key = 'ad_reward_cents'"
            ).fetchone()
        if row is not None:
            return max(1, int(row["setting_value"]))
    except (sqlite3.Error, TypeError, ValueError):
        pass
    return AD_REWARD


def set_ad_reward(reward_cents: int) -> bool:
    """يحفظ مكافأة مشاهدة الإعلان بالقروش."""
    if reward_cents < 1:
        return False
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO currency_settings "
            "(setting_key, setting_value, updated_at) "
            "VALUES ('ad_reward_cents', ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(setting_key) DO UPDATE SET "
            "setting_value = excluded.setting_value, "
            "updated_at = CURRENT_TIMESTAMP",
            (str(reward_cents),),
        )
        conn.commit()
    return True


def get_min_withdrawal() -> int:
    """يُعيد الحد الأدنى للسحب بالقروش من قاعدة البيانات."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT setting_value FROM currency_settings "
                "WHERE setting_key = 'min_withdrawal_cents'"
            ).fetchone()
        if row is not None:
            return max(1, int(row["setting_value"]))
    except (sqlite3.Error, TypeError, ValueError):
        pass
    return WITHDRAWAL_MIN_POINTS


def set_min_withdrawal(amount_cents: int) -> bool:
    """يُحفظ الحد الأدنى للسحب بالقروش."""
    if amount_cents < 1:
        return False
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO currency_settings "
            "(setting_key, setting_value, updated_at) "
            "VALUES ('min_withdrawal_cents', ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(setting_key) DO UPDATE SET "
            "setting_value = excluded.setting_value, "
            "updated_at = CURRENT_TIMESTAMP",
            (str(amount_cents),),
        )
        conn.commit()
    return True


def get_watch_ad_links(active_only: bool = True) -> list[dict]:
    """يعيد روابط إعلانات المشاهدة التي أضافها الأدمن."""
    with get_connection() as conn:
        query = (
            "SELECT id, url, title, active, created_at "
            "FROM watch_ad_links "
        )
        if active_only:
            query += "WHERE active = 1 "
        query += "ORDER BY id"
        return [dict(row) for row in conn.execute(query).fetchall()]


def get_watch_ad_link(link_id: int | None) -> dict | None:
    """يعيد بيانات رابط إعلان محدد."""
    if link_id is None:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, url, title, active FROM watch_ad_links WHERE id = ?",
            (link_id,),
        ).fetchone()
    return dict(row) if row else None


def add_watch_ad_link(url: str, title: str = "إعلان مشاهدة") -> dict | None:
    """يحفظ رابط إعلان مشاهدة جديداً ويعيد بياناته."""
    parsed = urlsplit(url.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or len(url.strip()) > 1000
    ):
        return None
    with get_connection() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO watch_ad_links (url, title) VALUES (?, ?)",
                (url.strip(), title.strip()[:100] or "إعلان مشاهدة"),
            )
        except sqlite3.IntegrityError:
            return None
        row = conn.execute(
            "SELECT id, url, title, active FROM watch_ad_links WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        conn.commit()
    return dict(row) if row else None


def set_watch_ad_link_active(link_id: int, active: bool) -> bool:
    """يخفي أو يظهر رابط إعلان دون حذف سجل الإثباتات القديمة."""
    with get_connection() as conn:
        updated = conn.execute(
            "UPDATE watch_ad_links SET active = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (1 if active else 0, link_id),
        ).rowcount
        conn.commit()
    return updated == 1


def get_service_quantity(service_key: str) -> int:
    """يعيد كمية الخدمة الحالية التي حددها الأدمن، مع كمية الكتالوج كاحتياط."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT quantity FROM service_quantity_settings "
            "WHERE service_key = ?",
            (service_key,),
        ).fetchone()
    if row is None:
        service = SERVICE_INDEX.get(service_key)
        return max(1, int(service["quantity"])) if service else 1
    return max(1, int(row["quantity"]))


def set_service_quantity(service_key: str, quantity: int) -> bool:
    """يحفظ كمية الخدمة التي يحددها الأدمن."""
    if service_key not in SERVICE_INDEX or quantity < 1:
        return False
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO service_quantity_settings "
            "(service_key, quantity, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(service_key) DO UPDATE SET "
            "quantity = excluded.quantity, updated_at = CURRENT_TIMESTAMP",
            (service_key, quantity),
        )
        conn.commit()
    return True


def service_price_message(service_key: str, balance: int) -> str:
    """رسالة موحدة عند عدم كفاية الرصيد لسعر الخدمة الحالي."""
    price = get_service_price(service_key)
    return (
        f"❌ سعر هذه الخدمة هو <b>{format_balance(price)}</b>.\n"
        f"رصيدك الحالي: <b>{format_balance(balance)}</b>."
    )


def record_referral(referrer_id: int, referred_id: int) -> bool:
    """يسجل إحالة حتى تُصرف عند فتح الحساب المحال مرة واحدة."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute(
            "INSERT INTO referrals "
            "(referrer_id, referred_id, rewarded_at, reward_status, "
            "reward_points, eligible_at) "
            "SELECT ?, ?, NULL, 'pending', ?, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM referrals WHERE referrer_id = ? AND referred_id = ?"
            ")",
            (
                referrer_id,
                referred_id,
                REFERRAL_REWARD,
                referrer_id,
                referred_id,
            ),
        )
        conn.commit()
        return inserted.rowcount == 1


def release_referral_reward(conn, referred_id: int) -> int:
    """يصرف إحالات المستخدم بعد تفعيله، بدون مؤقت أو فحص عضوية لاحق."""
    pending = conn.execute(
        "SELECT id, referrer_id, reward_points FROM referrals "
        "WHERE referred_id = ? AND reward_status = 'pending'",
        (referred_id,),
    ).fetchall()
    released = 0
    for referral in pending:
        changed = conn.execute(
            "UPDATE referrals SET reward_status = 'rewarded', "
            "rewarded_at = CURRENT_TIMESTAMP, last_checked_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND reward_status = 'pending'",
            (referral["id"],),
        ).rowcount
        if changed != 1:
            continue
        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (REFERRAL_REWARD, referral["referrer_id"]),
        )
        released += 1
    return released


def release_pending_referrals_for_activated_users() -> int:
    """يرحل الإحالات القديمة المعلقة للمستخدمين المفتوحين بالفعل."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        activated = conn.execute(
            "SELECT DISTINCT referred_id FROM referrals "
            "WHERE reward_status = 'pending' "
            "AND referred_id IN (SELECT user_id FROM users WHERE activation_status = 1)"
        ).fetchall()
        released = sum(
            release_referral_reward(conn, row["referred_id"])
            for row in activated
        )
        conn.commit()
        return released


def get_referral_count(user_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM referrals "
            "WHERE referrer_id = ? AND reward_status = 'rewarded'",
            (user_id,),
        ).fetchone()
        return row["cnt"] if row else 0


def referral_membership_status(referred_id: int) -> str:
    """يعيد active / invalid / unknown من خلال Telegram getChatMember."""
    try:
        member = bot.get_chat_member(SPONSOR_CHANNEL, referred_id)
    except Exception:
        return "unknown"

    status = getattr(member, "status", None)
    if status in ("member", "administrator", "creator"):
        return "active"
    if status == "restricted":
        return "active" if bool(getattr(member, "is_member", False)) else "invalid"
    if status in ("left", "kicked", "banned"):
        return "invalid"
    return "unknown"


def settle_referrals_for_user(user_id: int) -> dict:
    """توافق خلفي: الإحالة لا تحجز السحب ولا تتطلب فحص عضوية."""
    released = release_pending_referrals_for_activated_users()
    return {
        "blocked": False,
        "fraud": False,
        "newly_blocked": False,
        "unknown": False,
        "reversed": 0,
        "rewarded": released,
        "invalid_referred_ids": [],
    }


def notify_referral_fraud(user_id: int, invalid_referred_ids: list[int],
                          reversed_count: int):
    """يبلغ المالك مرة واحدة عند اكتشاف إحالة غادرت أو حُظرت."""
    invalid_text = ", ".join(str(value) for value in invalid_referred_ids)
    try:
        bot.send_message(
            ADMIN_ID,
            "🚨 <b>تنبيه أمني: اشتباه غش في الإحالات</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>المستخدم:</b> <code>{user_id}</code>\n"
            f"• <b>الإحالات غير الصالحة:</b> <code>{invalid_text}</code>\n"
            f"• <b>الإحالات المعكوسة:</b> {reversed_count}\n"
            "• <b>الإجراء:</b> تم عكس المكافآت المتاحة وحظر السحب.",
        )
    except Exception:
        pass


def run_referral_withdrawal_double_check(user_id: int) -> dict:
    """يشغل الفحص العكسي ويبلغ المالك عند اكتشاف غش مؤكد."""
    result = settle_referrals_for_user(user_id)
    if result["newly_blocked"]:
        notify_referral_fraud(
            user_id,
            result.get("invalid_referred_ids", []),
            result["reversed"],
        )
    return result


def save_order(user_id: int, service_key: str, smm_order_id: str,
               link: str, quantity: int, points_spent: int):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO smm_orders "
            "(user_id, service_key, smm_order_id, link, quantity, "
            "points_spent, amount_cents, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
            (user_id, service_key, smm_order_id, link, quantity, points_spent,
             points_spent),
        )
        conn.commit()


def get_user_orders(user_id: int, limit: int = 5):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM smm_orders WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def create_referral_task(
    buyer_id: int,
    referral_link: str,
    quantity: int | None = None,
    points_spent: int | None = None,
) -> int | None:
    """يخصم تكلفة الطلب ويحفظه في معاملة SQLite واحدة."""
    if quantity is None:
        quantity = get_service_quantity(REFERRAL_SERVICE_KEY)
    if points_spent is None:
        points_spent = get_service_price(REFERRAL_SERVICE_KEY)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE users SET balance_cents = balance_cents - ? "
            "WHERE user_id = ? AND balance_cents >= ?",
            (points_spent, buyer_id, points_spent),
        )
        if cur.rowcount != 1:
            return None

        task = conn.execute(
            "INSERT INTO referral_tasks "
            "(buyer_id, referral_link, quantity_requested, quantity_remaining, "
            "points_spent, amount_cents) VALUES (?, ?, ?, ?, ?, ?)",
            (
                buyer_id,
                referral_link,
                quantity,
                quantity,
                points_spent,
                points_spent,
            ),
        )
        task_id = int(task.lastrowid)
        conn.commit()
        return task_id


def get_active_referral_tasks(user_id: int, limit: int = 10):
    """يعيد المهام المدفوعة المتاحة، مع إخفاء طلب المستخدم عن صاحبه."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM referral_tasks "
            "WHERE status = 'active' AND quantity_remaining > 0 AND buyer_id != ? "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def claim_referral_task(task_id: int, worker_id: int) -> str:
    """
    يحجز حصة للمؤدي ويرسلها لموافقة العميل، ولا يصرف المكافأة هنا.
    يعيد: pending_client / already_done / unavailable / own_task.
    """
    task_key = f"referral_task:{task_id}"
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT buyer_id, quantity_remaining, status FROM referral_tasks "
            "WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task is None or task["status"] != "active" or task["quantity_remaining"] <= 0:
            return "unavailable"
        if task["buyer_id"] == worker_id:
            return "own_task"

        existing = conn.execute(
            "SELECT status FROM referral_task_claims "
            "WHERE task_id = ? AND worker_id = ?",
            (task_id, worker_id),
        ).fetchone()
        if existing is not None:
            if existing["status"] in {"client_rejected", "complaint_rejected"}:
                updated = conn.execute(
                    "UPDATE referral_tasks SET quantity_remaining = quantity_remaining - 1, "
                    "status = CASE WHEN quantity_remaining - 1 <= 0 "
                    "THEN 'completed' ELSE 'active' END "
                    "WHERE id = ? AND status = 'active' AND quantity_remaining > 0",
                    (task_id,),
                ).rowcount
                if updated != 1:
                    conn.rollback()
                    return "unavailable"
                conn.execute(
                    "UPDATE referral_task_claims SET status = 'pending_client', "
                    "slot_reserved = 1, client_decided_at = NULL, resolved_at = NULL "
                    "WHERE task_id = ? AND worker_id = ?",
                    (task_id, worker_id),
                )
                conn.commit()
                return "pending_client"
            if existing["status"] == "approved" or existing["status"] == "complaint_approved":
                return "already_done"
            return existing["status"]

        if conn.execute(
            "SELECT 1 FROM task_completions WHERE user_id = ? AND task_key = ?",
            (worker_id, task_key),
        ).fetchone() is not None:
            return "already_done"

        updated = conn.execute(
            "UPDATE referral_tasks SET quantity_remaining = quantity_remaining - 1, "
            "status = CASE WHEN quantity_remaining - 1 <= 0 "
            "THEN 'completed' ELSE 'active' END "
            "WHERE id = ? AND status = 'active' AND quantity_remaining > 0",
            (task_id,),
        ).rowcount
        if updated != 1:
            conn.rollback()
            return "unavailable"

        conn.execute(
            "INSERT INTO referral_task_claims "
            "(task_id, worker_id, buyer_id, status, slot_reserved) "
            "VALUES (?, ?, ?, 'pending_client', 1)",
            (task_id, worker_id, task["buyer_id"]),
        )
        conn.commit()
        return "pending_client"


def get_referral_task_claim(claim_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT c.*, t.referral_link, t.status AS task_status, "
            "t.quantity_remaining "
            "FROM referral_task_claims c "
            "JOIN referral_tasks t ON t.id = c.task_id "
            "WHERE c.id = ?",
            (claim_id,),
        ).fetchone()


def get_referral_task_claim_for_worker(task_id: int, worker_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT c.*, t.referral_link, t.status AS task_status, "
            "t.quantity_remaining "
            "FROM referral_task_claims c "
            "JOIN referral_tasks t ON t.id = c.task_id "
            "WHERE c.task_id = ? AND c.worker_id = ? "
            "ORDER BY c.id DESC LIMIT 1",
            (task_id, worker_id),
        ).fetchone()


def approve_referral_task_claim(claim_id: int, buyer_id: int):
    """يوافق العميل على التنفيذ ويصرف مكافأة المؤدي مرة واحدة."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        claim = conn.execute(
            "SELECT c.* FROM referral_task_claims c "
            "JOIN referral_tasks t ON t.id = c.task_id "
            "WHERE c.id = ? AND c.buyer_id = ?",
            (claim_id, buyer_id),
        ).fetchone()
        if claim is None or claim["status"] != "pending_client":
            return None
        task_key = f"referral_task:{claim['task_id']}"
        inserted = conn.execute(
            "INSERT OR IGNORE INTO task_completions (user_id, task_key) "
            "VALUES (?, ?)",
            (claim["worker_id"], task_key),
        ).rowcount
        if inserted != 1:
            conn.rollback()
            return None
        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (REFERRAL_REWARD, claim["worker_id"]),
        )
        conn.execute(
            "UPDATE referral_task_claims SET status = 'approved', "
            "slot_reserved = 0, "
            "client_decided_at = CURRENT_TIMESTAMP, resolved_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending_client'",
            (claim_id,),
        )
        conn.commit()
        result = dict(claim)
        result["reward_points"] = REFERRAL_REWARD
        return result


def reject_referral_task_claim(claim_id: int, buyer_id: int):
    """يرفض العميل التنفيذ ويعيد الحصة فوراً، مع إتاحة الشكوى للمؤدي."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        claim = conn.execute(
            "SELECT c.* FROM referral_task_claims c "
            "JOIN referral_tasks t ON t.id = c.task_id "
            "WHERE c.id = ? AND c.buyer_id = ? AND c.status = 'pending_client'",
            (claim_id, buyer_id),
        ).fetchone()
        if claim is None:
            return None
        conn.execute(
            "UPDATE referral_task_claims SET status = 'client_rejected', "
            "slot_reserved = 0, "
            "client_decided_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending_client'",
            (claim_id,),
        )
        # الرفض لا يحجز الحصة: تعود فوراً لتصبح متاحة لمؤدٍ آخر.
        conn.execute(
            "UPDATE referral_tasks SET quantity_remaining = quantity_remaining + 1, "
            "status = 'active' WHERE id = ? AND quantity_remaining >= 0",
            (claim["task_id"],),
        )
        conn.commit()
        return dict(claim)


def create_referral_task_complaint(claim_id: int, worker_id: int, file_id: str):
    """ينشئ شكوى للمؤدي بعد رفض العميل دون إعادة حجز الحصة."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        claim = conn.execute(
            "SELECT * FROM referral_task_claims "
            "WHERE id = ? AND worker_id = ? AND status = 'client_rejected'",
            (claim_id, worker_id),
        ).fetchone()
        if claim is None:
            conn.rollback()
            return None
        existing = conn.execute(
            "SELECT id FROM referral_task_complaints "
            "WHERE claim_id = ? AND status = 'pending'",
            (claim_id,),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            return None
        complaint = conn.execute(
            "INSERT INTO referral_task_complaints "
            "(claim_id, task_id, worker_id, file_id, claim_status_before) "
            "VALUES (?, ?, ?, ?, 'client_rejected')",
            (claim_id, claim["task_id"], worker_id, file_id),
        )
        conn.execute(
            "UPDATE referral_task_claims SET status = 'complaint_pending' "
            "WHERE id = ? AND status = 'client_rejected'",
            (claim_id,),
        )
        conn.commit()
        return int(complaint.lastrowid)


def set_referral_complaint_admin_message(complaint_id: int, admin_message_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE referral_task_complaints SET admin_message_id = ? WHERE id = ?",
            (admin_message_id, complaint_id),
        )
        conn.commit()


def cancel_referral_task_complaint(complaint_id: int):
    """يلغي الشكوى فقط عند فشل إرسالها، ويعيد حالة المطالبة للشكوى."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        complaint = conn.execute(
            "SELECT claim_id FROM referral_task_complaints "
            "WHERE id = ? AND status = 'pending'",
            (complaint_id,),
        ).fetchone()
        if complaint is None:
            conn.rollback()
            return
        conn.execute(
            "DELETE FROM referral_task_complaints WHERE id = ? AND status = 'pending'",
            (complaint_id,),
        )
        conn.execute(
            "UPDATE referral_task_claims SET status = 'client_rejected' "
            "WHERE id = ? AND status = 'complaint_pending'",
            (complaint["claim_id"],),
        )
        conn.commit()


def get_referral_complaint_by_admin_message(admin_message_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT c.*, t.referral_link, t.points_spent, "
            "r.buyer_id, r.status AS claim_status "
            "FROM referral_task_complaints c "
            "JOIN referral_tasks t ON t.id = c.task_id "
            "JOIN referral_task_claims r ON r.id = c.claim_id "
            "WHERE c.admin_message_id = ? ORDER BY c.id DESC LIMIT 1",
            (admin_message_id,),
        ).fetchone()


def approve_referral_task_complaint(complaint_id: int):
    """يعتمد الشكوى مرة واحدة، ويخصم مكافأة التنفيذ من المعلن ويدفعها للمؤدي."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        complaint = conn.execute(
            "SELECT c.*, r.buyer_id, t.points_spent FROM referral_task_complaints c "
            "JOIN referral_tasks t ON t.id = c.task_id "
            "JOIN referral_task_claims r ON r.id = c.claim_id "
            "WHERE c.id = ? AND c.status = 'pending' "
            "AND r.status = 'complaint_pending'",
            (complaint_id,),
        ).fetchone()
        if complaint is None:
            return None
        task_key = f"referral_task:{complaint['task_id']}"
        inserted = conn.execute(
            "INSERT OR IGNORE INTO task_completions (user_id, task_key) "
            "VALUES (?, ?)",
            (complaint["worker_id"], task_key),
        ).rowcount
        if inserted != 1:
            conn.rollback()
            return None
        # هذه الشكوى تعويض مستقل بعد رفض العميل، لذلك لا تُنقص حصة المهمة.
        # يُخصم المبلغ من المعلن مباشرةً وتُسجل العملية لصالح المؤدي.
        charged = conn.execute(
            "UPDATE users SET balance_cents = balance_cents - ? "
            "WHERE user_id = ? AND balance_cents >= ?",
            (
                REFERRAL_REWARD,
                complaint["buyer_id"],
                REFERRAL_REWARD,
            ),
        ).rowcount
        if charged != 1:
            conn.rollback()
            result = dict(complaint)
            result["error"] = "insufficient_buyer_balance"
            return result
        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (REFERRAL_REWARD, complaint["worker_id"]),
        )
        conn.execute(
            "UPDATE referral_task_complaints SET status = 'approved', "
            "reviewed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (complaint_id,),
        )
        conn.execute(
            "UPDATE referral_task_claims SET status = 'complaint_approved', "
            "resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (complaint["claim_id"],),
        )
        conn.commit()
        result = dict(complaint)
        result["reward_points"] = REFERRAL_REWARD
        return result


def reject_referral_task_complaint(complaint_id: int):
    """يرفض الأدمن الشكوى دون تغيير الحصة التي أعيدت عند رفض العميل."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        complaint = conn.execute(
            "SELECT c.claim_id, c.task_id, c.worker_id "
            "FROM referral_task_complaints c "
            "JOIN referral_task_claims r ON r.id = c.claim_id "
            "WHERE c.id = ? AND c.status = 'pending' "
            "AND r.status = 'complaint_pending'",
            (complaint_id,),
        ).fetchone()
        if complaint is None:
            return None
        conn.execute(
            "UPDATE referral_task_complaints SET status = 'rejected', "
            "reviewed_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (complaint_id,),
        )
        conn.execute(
            "UPDATE referral_task_claims SET status = 'complaint_rejected', "
            "resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (complaint["claim_id"],),
        )
        conn.commit()
        return dict(complaint)


def create_manual_task(
    title: str,
    task_link: str,
    reward_points: int,
    quantity: int,
    task_type: str = "social_manual",
    target_reference: str | None = None,
    task_instructions: str = "",
) -> int:
    if task_type not in {"social_manual", "telegram_channel"}:
        raise ValueError("Unsupported manual task type")
    target_reference = target_reference or task_link
    with get_connection() as conn:
        task = conn.execute(
            "INSERT INTO manual_tasks "
            "(title, task_link, task_type, target_reference, task_instructions, "
            "reward_points, quantity_requested, quantity_remaining) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                task_link,
                task_type,
                target_reference,
                task_instructions,
                reward_points,
                quantity,
                quantity,
            ),
        )
        return int(task.lastrowid)


def get_active_manual_tasks(limit: int = 10):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM manual_tasks "
            "WHERE status = 'active' AND quantity_remaining > 0 "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (limit,),
        ).fetchall()


def get_pending_manual_tasks(user_id: int, limit: int = 50):
    """يعيد المهام النشطة التي لم ينفذها هذا المستخدم بعد."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT t.* FROM manual_tasks t "
            "WHERE ("
            "  (t.status = 'active' AND t.quantity_remaining > 0) "
            "  OR EXISTS ("
            "    SELECT 1 FROM manual_task_reviews r "
            "    WHERE r.user_id = ? AND r.task_id = t.id "
            "      AND r.status = 'pending'"
            "  )"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM task_completions c "
            "  WHERE c.user_id = ? AND c.task_key = 'manual_task:' || t.id"
            ") "
            "ORDER BY t.created_at ASC, t.id ASC LIMIT ?",
            (user_id, user_id, limit),
        ).fetchall()


def get_manual_task(task_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM manual_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()


def manual_task_requires_proof(task) -> bool:
    """كل المهام الاجتماعية تحتاج صورة؛ الاستثناء الوحيد قناة Telegram."""
    task_type = task["task_type"] if "task_type" in task.keys() else "social_manual"
    return task_type != "telegram_channel"


def manual_task_target(task) -> str:
    """يعيد الرابط/اسم المستخدم الذي حدده المعلن."""
    if "target_reference" in task.keys() and task["target_reference"]:
        return str(task["target_reference"])
    return str(task["task_link"])


def manual_task_open_url(task) -> str | None:
    """يعيد رابط الفتح إن كان الهدف رابطاً صالحاً."""
    target = manual_task_target(task).strip()
    parsed = urlsplit(target)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return target
    if task["task_type"] == "telegram_channel":
        username = normalize_channel_input(target)
        if username:
            return f"https://t.me/{username.lstrip('@')}"
    return None


def manual_task_type_label(task) -> str:
    return (
        "انضمام قناة Telegram — تحقق آلي"
        if not manual_task_requires_proof(task)
        else "مهمة تواصل اجتماعي — مراجعة يدوية"
    )


def manual_task_instructions(task) -> str:
    instructions = str(task["task_instructions"] or "").strip()
    return instructions or "نفّذ المطلوب في المهمة وأرسل لقطة شاشة واضحة تثبت التنفيذ."


def get_pending_manual_review(user_id: int, task_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM manual_task_reviews "
            "WHERE user_id = ? AND task_id = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (user_id, task_id),
        ).fetchone()


def create_manual_task_review(user_id: int, task_id: int, file_id: str) -> int | None:
    """ينشئ طلب مراجعة صورة واحداً للمستخدم والمهمة."""
    with get_connection() as conn:
        task = conn.execute(
            "SELECT status, quantity_remaining FROM manual_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if (
            task is None
            or task["status"] != "active"
            or task["quantity_remaining"] <= 0
            or get_pending_manual_review(user_id, task_id) is not None
        ):
            return None

        review = conn.execute(
            "INSERT INTO manual_task_reviews (user_id, task_id, file_id) "
            "VALUES (?, ?, ?)",
            (user_id, task_id, file_id),
        )
        return int(review.lastrowid)


def set_manual_review_admin_message(review_id: int, admin_message_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE manual_task_reviews SET admin_message_id = ? WHERE id = ?",
            (admin_message_id, review_id),
        )


def cancel_manual_task_review(review_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM manual_task_reviews WHERE id = ? AND status = 'pending'",
            (review_id,),
        )


def get_manual_review_by_admin_message(admin_message_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT r.*, t.title, t.reward_points, t.quantity_remaining, "
            "t.status AS task_status "
            "FROM manual_task_reviews r "
            "JOIN manual_tasks t ON t.id = r.task_id "
            "WHERE r.admin_message_id = ? ORDER BY r.id DESC LIMIT 1",
            (admin_message_id,),
        ).fetchone()


def approve_manual_task_review(review_id: int):
    """يعتمد الصورة ويستهلك المهمة ويمنح مكافأتها مرة واحدة."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        review = conn.execute(
            "SELECT r.*, t.reward_points, t.quantity_remaining, t.status AS task_status "
            "FROM manual_task_reviews r "
            "JOIN manual_tasks t ON t.id = r.task_id "
            "WHERE r.id = ?",
            (review_id,),
        ).fetchone()
        if review is None or review["status"] != "pending":
            return None

        task_key = f"manual_task:{review['task_id']}"
        inserted = conn.execute(
            "INSERT OR IGNORE INTO task_completions (user_id, task_key) VALUES (?, ?)",
            (review["user_id"], task_key),
        ).rowcount
        if inserted != 1:
            conn.execute(
                "UPDATE manual_task_reviews SET status = 'approved', "
                "reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (review_id,),
            )
            return None

        updated = conn.execute(
            "UPDATE manual_tasks SET quantity_remaining = quantity_remaining - 1, "
            "status = CASE WHEN quantity_remaining - 1 <= 0 "
            "THEN 'completed' ELSE 'active' END "
            "WHERE id = ? AND status = 'active' AND quantity_remaining > 0",
            (review["task_id"],),
        ).rowcount
        if updated != 1:
            conn.rollback()
            return None

        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (review["reward_points"], review["user_id"]),
        )
        conn.execute(
            "UPDATE manual_task_reviews SET status = 'approved', "
            "reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (review_id,),
        )
        return {
            "user_id": review["user_id"],
            "task_id": review["task_id"],
            "reward_points": review["reward_points"],
        }


def has_pending_manual_review(user_id: int) -> bool:
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM manual_task_reviews "
            "WHERE user_id = ? AND status = 'pending' LIMIT 1",
            (user_id,),
        ).fetchone() is not None


def get_pending_ad_review(user_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM ad_reviews "
            "WHERE user_id = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()


def create_ad_review(
    user_id: int,
    file_id: str,
    ad_link_id: int | None = None,
) -> int | None:
    """ينشئ مراجعة إعلان واحدة معلّقة لكل مستخدم."""
    with get_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone() is None:
            return None
        if conn.execute(
            "SELECT 1 FROM ad_reviews "
            "WHERE user_id = ? AND status = 'pending' LIMIT 1",
            (user_id,),
        ).fetchone() is not None:
            return None
        if ad_link_id is not None and conn.execute(
            "SELECT 1 FROM watch_ad_links WHERE id = ? AND active = 1",
            (ad_link_id,),
        ).fetchone() is None:
            return None
        review = conn.execute(
            "INSERT INTO ad_reviews "
            "(user_id, file_id, ad_link_id, reward_cents) "
            "VALUES (?, ?, ?, ?)",
            (user_id, file_id, ad_link_id, get_ad_reward()),
        )
        review_id = int(review.lastrowid)
        conn.commit()
        return review_id


def set_ad_review_admin_message(review_id: int, admin_message_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE ad_reviews SET admin_message_id = ? WHERE id = ?",
            (admin_message_id, review_id),
        )
        conn.commit()


def cancel_ad_review(review_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM ad_reviews WHERE id = ? AND status = 'pending'",
            (review_id,),
        )
        conn.commit()


def get_ad_review_by_admin_message(admin_message_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM ad_reviews "
            "WHERE admin_message_id = ? ORDER BY id DESC LIMIT 1",
            (admin_message_id,),
        ).fetchone()


def get_ad_review(review_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM ad_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()


def approve_ad_review(review_id: int):
    """يعتمد مشاهدة الإعلان ويمنح قيمة الإثبات المحفوظة مرة واحدة."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        review = conn.execute(
            "SELECT * FROM ad_reviews WHERE id = ?", (review_id,)
        ).fetchone()
        if review is None or review["status"] != "pending":
            return None

        updated = conn.execute(
            "UPDATE ad_reviews SET status = 'approved', "
            "reviewed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (review_id,),
        ).rowcount
        if updated != 1:
            conn.rollback()
            return None

        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (review["reward_cents"], review["user_id"]),
        )
        result = {
            "user_id": review["user_id"],
            "reward_points": review["reward_cents"],
        }
        conn.commit()
        return result


def get_pending_user_ad(user_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM user_ads "
            "WHERE user_id = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()


def create_user_ad(
    user_id: int,
    title: str,
    description: str,
    link: str,
    price_cents: int,
) -> int | None:
    """يحفظ إعلاناً يدوياً واحداً قيد المراجعة لكل مستخدم."""
    with get_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone() is None:
            return None
        if conn.execute(
            "SELECT 1 FROM user_ads "
            "WHERE user_id = ? AND status = 'pending' LIMIT 1",
            (user_id,),
        ).fetchone() is not None:
            return None
        created = conn.execute(
            "INSERT INTO user_ads "
            "(user_id, title, description, link, price_cents) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, title, description, link, price_cents),
        )
        conn.commit()
        return int(created.lastrowid)


def set_user_ad_admin_message(ad_id: int, admin_message_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_ads SET admin_message_id = ? WHERE id = ?",
            (admin_message_id, ad_id),
        )
        conn.commit()


def delete_pending_user_ad(ad_id: int):
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM user_ads WHERE id = ? AND status = 'pending'",
            (ad_id,),
        )
        conn.commit()


def get_user_ad_by_admin_message(admin_message_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM user_ads "
            "WHERE admin_message_id = ? ORDER BY id DESC LIMIT 1",
            (admin_message_id,),
        ).fetchone()


def approve_user_ad(ad_id: int):
    """ينشر الإعلان مرة واحدة بعد موافقة المشرف."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ad = conn.execute(
            "SELECT * FROM user_ads WHERE id = ?", (ad_id,)
        ).fetchone()
        if ad is None or ad["status"] != "pending":
            return None
        updated = conn.execute(
            "UPDATE user_ads SET status = 'approved', "
            "reviewed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (ad_id,),
        ).rowcount
        if updated != 1:
            conn.rollback()
            return None
        conn.commit()
        return ad


def reject_user_ad(ad_id: int):
    """يرفض الإعلان مرة واحدة من غير نشره للمستخدمين."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ad = conn.execute(
            "SELECT * FROM user_ads WHERE id = ?", (ad_id,)
        ).fetchone()
        if ad is None or ad["status"] != "pending":
            return None
        updated = conn.execute(
            "UPDATE user_ads SET status = 'rejected', "
            "reviewed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (ad_id,),
        ).rowcount
        if updated != 1:
            conn.rollback()
            return None
        conn.commit()
        return ad


def claim_manual_task(task_id: int, worker_id: int) -> str:
    """يمنح مكافأة قناة Telegram بعد التحقق؛ المهام الاجتماعية تحتاج صورة."""
    task_key = f"manual_task:{task_id}"
    with get_connection() as conn:
        task = conn.execute(
            "SELECT reward_points, quantity_remaining, status, task_type, "
            "target_reference, task_link "
            "FROM manual_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    if task is None or task["status"] != "active" or task["quantity_remaining"] <= 0:
        return "unavailable"
    if manual_task_requires_proof(task):
        return "proof_required"
    channel = normalize_channel_input(task["target_reference"] or task["task_link"])
    if not channel:
        return "invalid_target"
    if not is_subscribed(worker_id, channel):
        return "not_subscribed"

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            "SELECT reward_points, quantity_remaining, status "
            "FROM manual_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task is None or task["status"] != "active" or task["quantity_remaining"] <= 0:
            return "unavailable"
        inserted = conn.execute(
            "INSERT OR IGNORE INTO task_completions (user_id, task_key) VALUES (?, ?)",
            (worker_id, task_key),
        ).rowcount
        if inserted != 1:
            return "already_done"

        updated = conn.execute(
            "UPDATE manual_tasks SET quantity_remaining = quantity_remaining - 1, "
            "status = CASE WHEN quantity_remaining - 1 <= 0 "
            "THEN 'completed' ELSE 'active' END "
            "WHERE id = ? AND status = 'active' AND quantity_remaining > 0",
            (task_id,),
        ).rowcount
        if updated != 1:
            conn.rollback()
            return "unavailable"

        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (task["reward_points"], worker_id),
        )
        return "claimed"


def create_withdrawal_request(
    user_id: int,
    points_amount: int,
    withdrawal_method: str,
    account_details: str,
) -> int | str | None:
    """Legacy create path. Customer flow is locked to V2.

    Refuses non-admin callers to prevent bypass. Kept for admin use only
    and for backwards compatibility with existing rows / admin tooling.
    """
    if not is_admin(user_id):
        return "customer_legacy_disabled"
    """يخصم النقاط وينشئ الطلب مع تطبيق حد طلب واحد كل 24 ساعة."""
    referral_check = run_referral_withdrawal_double_check(user_id)
    if referral_check["blocked"]:
        return "fraud"
    if referral_check["unknown"]:
        return "verification_unavailable"

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        blocked = conn.execute(
            "SELECT withdrawal_blocked FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if blocked is None or blocked["withdrawal_blocked"]:
            return "fraud"
        recent = conn.execute(
            "SELECT 1 FROM withdrawal_requests "
            "WHERE user_id = ? AND created_at >= datetime('now', '-24 hours') "
            "LIMIT 1",
            (user_id,),
        ).fetchone()
        if recent is not None:
            return "cooldown"

        cur = conn.execute(
            "UPDATE users SET balance_cents = balance_cents - ? "
            "WHERE user_id = ? AND balance_cents >= ?",
            (points_amount, user_id, points_amount),
        )
        if cur.rowcount != 1:
            return None

        request = conn.execute(
            "INSERT INTO withdrawal_requests "
            "(user_id, points_amount, amount_cents, withdrawal_method, account_details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                points_amount,
                points_amount,
                withdrawal_method,
                account_details,
            ),
        )
        return int(request.lastrowid)


def has_recent_withdrawal(user_id: int) -> bool:
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM withdrawal_requests "
            "WHERE user_id = ? AND created_at >= datetime('now', '-24 hours') "
            "LIMIT 1",
            (user_id,),
        ).fetchone() is not None


def set_withdrawal_admin_message(request_id: int, admin_message_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE withdrawal_requests SET admin_message_id = ? WHERE id = ?",
            (admin_message_id, request_id),
        )
        conn.commit()


def get_withdrawal_by_admin_message(admin_message_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM withdrawal_requests "
            "WHERE admin_message_id = ? ORDER BY id DESC LIMIT 1",
            (admin_message_id,),
        ).fetchone()


def complete_withdrawal(request_id: int):
    """يحوّل الطلب من pending إلى completed مرة واحدة."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM withdrawal_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            return None

        updated = conn.execute(
            "UPDATE withdrawal_requests SET status = 'completed', "
            "completed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (request_id,),
        ).rowcount
        return row if updated == 1 else None


def cancel_withdrawal_and_refund(request_id: int):
    """يسترد النقاط إذا تعذر إرسال طلب السحب للمشرف."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT user_id, points_amount, amount_cents FROM withdrawal_requests "
            "WHERE id = ? AND status = 'pending'",
            (request_id,),
        ).fetchone()
        if row is None:
            return False

        conn.execute(
            "UPDATE withdrawal_requests SET status = 'cancelled' WHERE id = ?",
            (request_id,),
        )
        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (row["amount_cents"] or row["points_amount"], row["user_id"]),
        )
        return True


# ══════════════════════════════════════════════════════════════════════════════
# ─── V2 WITHDRAWAL SYSTEM (USDT accounting) ────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# Internal accounting unit: USDT (6 decimal places → micro-USDT = 10^-6 USDT)
# Display unit: EGP (2 decimal places → cents)
# Reference rate is the current USDT/EGP rate, fetched from an external source.

WITHDRAWAL_METHOD_VODAFONE = "vodafone"
WITHDRAWAL_METHOD_USDT = "usdt"
WITHDRAWAL_NETWORK_BEP20 = "BSC_BEP20"
WITHDRAWAL_NETWORK_DISPLAY = "BNB Smart Chain (BEP-20)"

USDT_MICRO_PER_USDT = Decimal("1000000")
EGP_CENTS_PER_EGP = Decimal("100")

VODAFONE_MIN_USD = Decimal("0.10")
USDT_MIN_USDT = Decimal("0.15")

WITHDRAWAL_COOLDOWN_SECONDS = 24 * 60 * 60  # 24 hours
MAX_RATE_AGE_SECONDS = 6 * 60 * 60  # 6 hours

# Rate provider abstraction. Default uses CoinGecko's public API
# (no API key required). Override via env or code.
DEFAULT_RATE_PROVIDER = os.environ.get(
    "WITHDRAWAL_RATE_PROVIDER", "coingecko"
)


def _usdt_to_micro(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) * USDT_MICRO_PER_USDT)


def _micro_to_usdt(micro: int) -> Decimal:
    return (Decimal(micro) / USDT_MICRO_PER_USDT).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


def _egp_cents_to_egp(cents: int) -> Decimal:
    return (Decimal(cents) / EGP_CENTS_PER_EGP).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _egp_to_egp_cents(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * EGP_CENTS_PER_EGP)


def fetch_usdt_egp_rate(provider: str = DEFAULT_RATE_PROVIDER) -> tuple[Decimal, str]:
    """Fetch current USDT/EGP rate from the configured provider.

    Returns (rate, provider_name). Rate is Decimal EGP per 1 USDT.
    Raises RuntimeError on failure. Never returns silently.
    """
    if provider == "coingecko":
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "tether", "vs_currencies": "egp"}
        resp = http.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rate = data.get("tether", {}).get("egp")
        if rate is None:
            raise RuntimeError("coingecko_missing_field")
        return Decimal(str(rate)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "coingecko"
    raise RuntimeError(f"unknown_rate_provider:{provider}")


def _get_last_valid_rate() -> tuple[Decimal, str, str] | None:
    """Returns (rate, provider, fetched_at_iso) of last successful rate snapshot,
    or None if no valid rate has ever been recorded."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT setting_value FROM currency_settings "
            "WHERE setting_key = 'usdt_egp_rate'"
        ).fetchone()
        provider_row = conn.execute(
            "SELECT setting_value FROM currency_settings "
            "WHERE setting_key = 'usdt_egp_rate_provider'"
        ).fetchone()
        ts_row = conn.execute(
            "SELECT setting_value FROM currency_settings "
            "WHERE setting_key = 'usdt_egp_rate_fetched_at'"
        ).fetchone()
    if not row or not row["setting_value"]:
        return None
    try:
        return (
            Decimal(row["setting_value"]),
            provider_row["setting_value"] if provider_row else "unknown",
            ts_row["setting_value"] if ts_row else "",
        )
    except (InvalidOperation, TypeError):
        return None


def _save_rate_snapshot(rate: Decimal, provider: str) -> None:
    """Persist current rate to DB for fallback usage."""
    now = datetime.utcnow().isoformat() + "Z"
    with get_connection() as conn:
        for key, value in [
            ("usdt_egp_rate", str(rate)),
            ("usdt_egp_rate_provider", provider),
            ("usdt_egp_rate_fetched_at", now),
        ]:
            conn.execute(
                "INSERT INTO currency_settings (setting_key, setting_value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(setting_key) DO UPDATE SET "
                "setting_value = excluded.setting_value, updated_at = CURRENT_TIMESTAMP",
                (key, value),
            )
        conn.commit()


def get_current_usdt_egp_rate(allow_stale: bool = False) -> tuple[Decimal, str, bool]:
    """Return (rate, provider, is_fresh).

    If a fresh external fetch succeeds: returns live rate and saves snapshot.
    If external fails and allow_stale=False: raises RuntimeError.
    If external fails and allow_stale=True: returns last known rate with is_fresh=False.
    If no prior rate exists: raises RuntimeError.
    """
    last = _get_last_valid_rate()
    try:
        rate, provider = fetch_usdt_egp_rate()
        _save_rate_snapshot(rate, provider)
        return rate, provider, True
    except Exception as exc:
        if last is None:
            raise RuntimeError(f"no_rate_available:{exc}") from exc
        if not allow_stale:
            raise
        return last[0], last[1], False


def is_rate_within_max_age(fetched_at_iso: str) -> bool:
    """Check if a stored rate timestamp is within the maximum acceptable age."""
    if not fetched_at_iso:
        return False
    try:
        ts = datetime.fromisoformat(fetched_at_iso.replace("Z", ""))
    except (ValueError, TypeError):
        return False
    age = (datetime.utcnow() - ts).total_seconds()
    return 0 <= age <= MAX_RATE_AGE_SECONDS


def _safe_fetch_current_rate(allow_stale: bool = True):
    """Fetch the current USD/EGP rate using the existing V2 provider
    infrastructure. USDT is pegged 1:1 to USD so the same rate applies.

    Returns (rate: Decimal, is_fresh: bool). Raises RuntimeError if no rate
    can be determined and no usable cached snapshot exists.
    """
    try:
        rate, _provider, is_fresh = get_current_usdt_egp_rate(
            allow_stale=allow_stale
        )
    except RuntimeError:
        raise
    return rate, is_fresh


def compute_vodafone_min_egp_cents(rate: Decimal) -> int:
    """Compute the dynamic Vodafone Cash minimum in EGP cents.

    Formula: 0.10 USD × rate × 100 cents/EGP, rounded HALF_UP to integer cents.
    """
    if rate <= 0:
        raise ValueError("rate_must_be_positive")
    egp = (VODAFONE_MIN_USD * rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return int(egp * EGP_CENTS_PER_EGP)


def get_vodafone_min_egp_cents(allow_stale: bool = True) -> tuple[int, Decimal, bool]:
    """Get the current Vodafone Cash minimum in EGP cents, the rate used,
    and whether the rate was freshly fetched.

    Uses the same safe rate policy as the rest of V2: tries a fresh
    fetch first, then falls back to the cached snapshot if `allow_stale`
    is True. Raises RuntimeError if no rate is available.
    """
    rate, is_fresh = _safe_fetch_current_rate(allow_stale=allow_stale)
    if not is_fresh:
        last = _get_last_valid_rate()
        if last is None or not is_rate_within_max_age(last[2]):
            raise RuntimeError("no_valid_rate_available")
    return compute_vodafone_min_egp_cents(rate), rate, is_fresh


def validate_vodafone_destination(destination: str) -> bool:
    """Validate a Vodafone Cash mobile number.

    Accepts Egyptian mobile numbers in 01x format (10-11 digits).
    """
    if not destination:
        return False
    cleaned = destination.replace(" ", "").replace("-", "").replace("+", "")
    if not cleaned.isdigit():
        return False
    # Egyptian mobile: 01[0-9]{9} (11 digits) or 1[0-9]{9} (10 digits without leading 0)
    if len(cleaned) == 11 and cleaned.startswith("01"):
        return True
    if len(cleaned) == 10 and cleaned.startswith("1"):
        return True
    return False


def validate_usdt_bep20_address(address: str) -> bool:
    """Validate a BEP-20 (BSC) wallet address.

    A valid address is 42 characters, starts with '0x', and contains only
    hexadecimal characters. This is a basic format check; on-chain verification
    is out of scope.
    """
    if not address:
        return False
    address = address.strip()
    if not address.startswith("0x"):
        return False
    if len(address) != 42:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in address[2:])


def egp_to_usdt(egp_cents: int, rate: Decimal) -> Decimal:
    """Convert EGP cents to USDT using the given rate (EGP per 1 USDT)."""
    egp = Decimal(egp_cents) / EGP_CENTS_PER_EGP
    if rate <= 0:
        raise ValueError("rate_must_be_positive")
    return (egp / rate).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def usdt_to_egp_cents(usdt_amount: Decimal, rate: Decimal) -> int:
    """Convert USDT amount to EGP cents using the given rate."""
    if rate <= 0:
        raise ValueError("rate_must_be_positive")
    egp = (usdt_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(egp * EGP_CENTS_PER_EGP)


def get_withdrawal_cooldown_remaining(user_id: int) -> int:
    """Returns seconds until next allowed withdrawal, or 0 if available."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS last_at FROM withdrawal_requests "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or not row["last_at"]:
        return 0
    last_at_str = row["last_at"]
    try:
        # Parse SQLite UTC timestamp
        last_at = datetime.strptime(last_at_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return 0
    elapsed = (datetime.utcnow() - last_at).total_seconds()
    remaining = int(WITHDRAWAL_COOLDOWN_SECONDS - elapsed)
    return max(0, remaining)


def format_cooldown_remaining(seconds: int) -> str:
    """Format a seconds count into 'X ساعة Y دقيقة' for customer display."""
    if seconds <= 0:
        return "0 دقيقة"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} ساعة")
    if minutes > 0 or hours == 0:
        parts.append(f"{minutes} دقيقة")
    return " و".join(parts)


def create_v2_withdrawal_request(
    user_id: int,
    method_code: str,
    destination: str,
    requested_egp_cents: int | None,
    usdt_amount: Decimal | None,
    network_code: str | None = None,
) -> int | str:
    """Create a withdrawal request with V2 (USDT-based) accounting.

    Exactly one of requested_egp_cents or usdt_amount must be provided.
    Returns the new request id on success, or an error string.

    Errors:
      "method_not_supported"
      "destination_invalid"
      "below_minimum"
      "insufficient_balance"
      "cooldown"
      "rate_unavailable"
    """
    if method_code not in (WITHDRAWAL_METHOD_VODAFONE, WITHDRAWAL_METHOD_USDT):
        return "method_not_supported"

    # 1. Validate destination up front
    if method_code == WITHDRAWAL_METHOD_VODAFONE:
        if not validate_vodafone_destination(destination):
            return "destination_invalid"
    else:  # usdt
        if network_code != WITHDRAWAL_NETWORK_BEP20:
            return "destination_invalid"
        if not validate_usdt_bep20_address(destination):
            return "destination_invalid"

    # 2. Fetch current rate (allow stale as fallback)
    try:
        rate, rate_provider, is_fresh = get_current_usdt_egp_rate(allow_stale=True)
    except RuntimeError:
        return "rate_unavailable"

    if not is_fresh and not is_rate_within_max_age(_get_last_valid_rate()[2]):
        return "rate_unavailable"

    # 2b. Compute dynamic Vodafone minimum and validate against it.
    if method_code == WITHDRAWAL_METHOD_VODAFONE:
        vodafone_min_egp_cents = compute_vodafone_min_egp_cents(rate)
        if requested_egp_cents is None or requested_egp_cents < vodafone_min_egp_cents:
            return "below_minimum"
    else:  # usdt
        if usdt_amount is None or usdt_amount < USDT_MIN_USDT:
            return "below_minimum"

    now_iso = datetime.utcnow().isoformat() + "Z"

    # 3. Compute USDT accounting amount
    if method_code == WITHDRAWAL_METHOD_VODAFONE:
        usdt_amount = egp_to_usdt(requested_egp_cents, rate)
        egp_equiv_cents = requested_egp_cents
    else:
        egp_equiv_cents = usdt_to_egp_cents(usdt_amount, rate)

    usdt_micro = _usdt_to_micro(usdt_amount)
    exchange_rate_micro = int(
        (rate * Decimal("1000000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    # Store EGP cents using legacy amount_cents (for backward compatibility)
    amount_cents = egp_equiv_cents

    # 4. Atomic transaction: cooldown + balance + insert
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        # Block if user is fraud-blocked
        user_row = conn.execute(
            "SELECT balance_cents, withdrawal_blocked FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if user_row is None or user_row["withdrawal_blocked"]:
            conn.rollback()
            return "fraud"

        # 24h cooldown check (any withdrawal method)
        recent = conn.execute(
            "SELECT 1 FROM withdrawal_requests "
            "WHERE user_id = ? AND created_at >= "
            "datetime('now', ?)",
            (user_id, f"-{WITHDRAWAL_COOLDOWN_SECONDS} seconds"),
        ).fetchone()
        if recent is not None:
            conn.rollback()
            return "cooldown"

        # Atomic balance deduction
        cur = conn.execute(
            "UPDATE users SET balance_cents = balance_cents - ? "
            "WHERE user_id = ? AND balance_cents >= ?",
            (amount_cents, user_id, amount_cents),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return "insufficient_balance"

        cursor = conn.execute(
            "INSERT INTO withdrawal_requests ("
            "user_id, points_amount, amount_cents, withdrawal_method, "
            "account_details, method_code, network_code, destination, "
            "requested_egp_cents, usdt_micro, egp_equivalent_cents, "
            "exchange_rate_micro, rate_fetched_at, rate_provider, "
            "fee_cents, refunded, status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                user_id,
                amount_cents,
                amount_cents,
                method_code,  # legacy column
                destination,
                method_code,
                network_code,
                destination,
                requested_egp_cents,
                usdt_micro,
                egp_equiv_cents,
                exchange_rate_micro,
                now_iso,
                rate_provider,
                0,
                0,
            ),
        )
        new_id = int(cursor.lastrowid)
        conn.commit()
    return new_id


def get_v2_withdrawal_request(request_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM withdrawal_requests WHERE id = ?",
            (request_id,),
        ).fetchone()


def list_pending_v2_withdrawals():
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM withdrawal_requests "
            "WHERE status = 'pending' AND method_code IN (?, ?) "
            "ORDER BY created_at ASC",
            (WITHDRAWAL_METHOD_VODAFONE, WITHDRAWAL_METHOD_USDT),
        ).fetchall()


def complete_v2_withdrawal(request_id: int, admin_id: int,
                           transaction_reference: str | None = None):
    """Mark a V2 withdrawal as completed. Idempotent.

    Does NOT deduct balance (already deducted at request time).
    Refuses to complete a cancelled or already-completed request.
    """
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM withdrawal_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            conn.rollback()
            return None
        if row["method_code"] not in (WITHDRAWAL_METHOD_VODAFONE, WITHDRAWAL_METHOD_USDT):
            conn.rollback()
            return None
        conn.execute(
            "UPDATE withdrawal_requests SET "
            "status = 'completed', completed_at = CURRENT_TIMESTAMP, "
            "admin_id = ?, transaction_reference = ? "
            "WHERE id = ? AND status = 'pending'",
            (admin_id, transaction_reference, request_id),
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM withdrawal_requests WHERE id = ?",
            (request_id,),
        ).fetchone()


def reject_v2_withdrawal(request_id: int, admin_id: int,
                         reason: str | None = None):
    """Reject a pending V2 withdrawal and refund exactly once."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM withdrawal_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            conn.rollback()
            return None
        if row["refunded"]:
            conn.rollback()
            return None
        if row["method_code"] not in (WITHDRAWAL_METHOD_VODAFONE, WITHDRAWAL_METHOD_USDT):
            conn.rollback()
            return None
        # Atomic refund
        amount = int(row["amount_cents"] or row["points_amount"] or 0)
        conn.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (amount, row["user_id"]),
        )
        conn.execute(
            "UPDATE withdrawal_requests SET "
            "status = 'rejected', completed_at = CURRENT_TIMESTAMP, "
            "admin_id = ?, refunded = 1 "
            "WHERE id = ? AND status = 'pending'",
            (admin_id, request_id),
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM withdrawal_requests WHERE id = ?",
            (request_id,),
        ).fetchone()


def format_v2_withdrawal_customer_summary(row) -> str:
    """Format a customer-facing summary. Never includes internal IDs, admin
    notes, or supplier cost."""
    method = row["method_code"]
    dest = row["destination"]
    usdt_amount = _micro_to_usdt(int(row["usdt_micro"] or 0))
    rate = Decimal(int(row["exchange_rate_micro"] or 0)) / Decimal("1000000")
    egp_equiv = _egp_cents_to_egp(int(row["egp_equivalent_cents"] or 0))

    if method == WITHDRAWAL_METHOD_VODAFONE:
        return (
            "طريقة السحب:\nVodafone Cash\n\n"
            f"المبلغ:\n{egp_equiv:.2f} EGP\n\n"
            f"رسوم السحب:\n0 EGP\n\n"
            f"سعر الصرف المستخدم:\n{rate:.4f} EGP / USDT\n\n"
            f"القيمة المحاسبية:\n{usdt_amount:.6f} USDT\n\n"
            f"المحفظة:\n{dest}"
        )
    return (
        "طريقة السحب:\nUSDT\n\n"
        "الشبكة:\nBNB Smart Chain (BEP-20)\n\n"
        f"المبلغ:\n{usdt_amount:.6f} USDT\n\n"
        f"القيمة التقريبية:\n{egp_equiv:.2f} EGP\n\n"
        "رسوم السحب:\n0\n\n"
        f"العنوان:\n{dest}"
    )


def format_v2_withdrawal_admin_summary(row) -> str:
    """Format an admin-facing summary including rate, network, destination."""
    method = row["method_code"]
    dest = row["destination"]
    usdt_amount = _micro_to_usdt(int(row["usdt_micro"] or 0))
    rate = Decimal(int(row["exchange_rate_micro"] or 0)) / Decimal("1000000")
    egp_equiv = _egp_cents_to_egp(int(row["egp_equivalent_cents"] or 0))
    requested_egp = (
        _egp_cents_to_egp(int(row["requested_egp_cents"] or 0))
        if row["requested_egp_cents"] is not None
        else None
    )

    lines = [
        f"طلب سحب V2 #{row['id']}",
        f"المستخدم: {row['user_id']}",
        f"الطريقة: {method}",
        f"الحالة: {row['status']}",
        f"تاريخ الإنشاء: {row['created_at']}",
    ]
    if method == WITHDRAWAL_METHOD_VODAFONE:
        lines.append(f"المبلغ المطلوب: {requested_egp:.2f} EGP" if requested_egp is not None else "المبلغ المطلوب: ?")
        lines.append(f"ما يعادل USDT: {usdt_amount:.6f} USDT")
        lines.append(f"رقم Vodafone: {dest}")
    else:
        lines.append(f"الشبكة: {WITHDRAWAL_NETWORK_DISPLAY}")
        lines.append(f"المبلغ: {usdt_amount:.6f} USDT")
        lines.append(f"ما يعادل EGP: {egp_equiv:.2f} EGP")
        lines.append(f"عنوان المحفظة: {dest}")
    lines.append(f"سعر الصرف: {rate:.4f} EGP/USDT")
    lines.append(f"وقت جلب السعر: {row['rate_fetched_at']}")
    lines.append(f"مزود السعر: {row['rate_provider']}")
    lines.append(f"الرسوم: {_egp_cents_to_egp(int(row['fee_cents'] or 0))} EGP")
    lines.append(f"تم الاسترداد: {'نعم' if row['refunded'] else 'لا'}")
    if row["transaction_reference"]:
        lines.append(f"مرجع المعاملة: {row['transaction_reference']}")
    if row["completed_at"]:
        lines.append(f"تاريخ الإكمال: {row['completed_at']}")
    return "\n".join(lines)


def get_v2_withdrawal_by_admin_message(admin_message_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM withdrawal_requests "
            "WHERE admin_message_id = ? AND method_code IN (?, ?) "
            "ORDER BY id DESC LIMIT 1",
            (admin_message_id, WITHDRAWAL_METHOD_VODAFONE, WITHDRAWAL_METHOD_USDT),
        ).fetchone()


def create_payment_receipt(user_id: int, file_id: str) -> int:
    """يحفظ الإيصال ومعرف العميل قبل إرساله إلى المشرف."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO payment_receipts (user_id, file_id) VALUES (?, ?)",
            (user_id, file_id),
        )
        conn.commit()
        return int(cur.lastrowid)


def set_receipt_admin_message(receipt_id: int, admin_message_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE payment_receipts SET admin_message_id = ? WHERE id = ?",
            (admin_message_id, receipt_id),
        )
        conn.commit()


def get_receipt_by_admin_message(admin_message_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM payment_receipts "
            "WHERE admin_message_id = ? ORDER BY id DESC LIMIT 1",
            (admin_message_id,),
        ).fetchone()


def mark_receipt_replied(receipt_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE payment_receipts SET status = 'replied' WHERE id = ?",
            (receipt_id,),
        )
        conn.commit()


def get_all_user_ids() -> list[int]:
    """يُعيد قائمة بجميع معرّفات المستخدمين."""
    with get_connection() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [r["user_id"] for r in rows]


def get_stats() -> dict:
    """يُعيد إحصائيات عامة من قاعدة البيانات."""
    with get_connection() as conn:
        users_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM users"
        ).fetchone()["cnt"]
        orders_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM smm_orders"
        ).fetchone()["cnt"]
        balance_total = conn.execute(
            "SELECT COALESCE(SUM(balance_cents), 0) AS s FROM users"
        ).fetchone()["s"]
        referrals_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM referrals"
        ).fetchone()["cnt"]
        today_users = conn.execute(
            "SELECT COUNT(*) AS cnt FROM users "
            "WHERE DATE(joined_at) = DATE('now')"
        ).fetchone()["cnt"]
    return {
        "users":      users_count,
        "orders":     orders_count,
        "balance_cents": balance_total,
        "referrals":  referrals_count,
        "today":      today_users,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ─── دوال المهام ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def is_task_done(user_id: int, task_key: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM task_completions WHERE user_id = ? AND task_key = ?",
            (user_id, task_key),
        ).fetchone()
        return row is not None


def mark_task_done(user_id: int, task_key: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO task_completions (user_id, task_key) VALUES (?, ?)",
            (user_id, task_key),
        )
        conn.commit()


def is_subscribed(user_id: int, channel: str) -> bool:
    try:
        member = bot.get_chat_member(channel, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


def _member_is_active(member) -> bool:
    status = getattr(member, "status", None)
    if status in ("member", "administrator", "creator"):
        return True
    return status == "restricted" and bool(getattr(member, "is_member", False))


@bot.chat_member_handler()
def handle_required_channel_membership_update(update):
    """
    يعالج مغادرة المستخدم لقناة إلزامية فور وصول تحديث Telegram.

    يشترط Telegram أن يكون البوت مشرفاً في القناة لإرسال chat_member updates.
    لذلك يبقى account_access_allowed() فحصاً احتياطياً عند كل تفاعل مع البوت.
    """
    refresh_required_channels()
    chat_username = (getattr(update.chat, "username", None) or "").lower()
    if not chat_username:
        return
    channel = next(
        (
            item for item in REQUIRED_CHANNELS
            if item["username"].lstrip("@").lower() == chat_username
        ),
        None,
    )
    if channel is None:
        return

    user_id = getattr(update.new_chat_member.user, "id", None)
    if user_id is None or get_user(user_id) is None:
        return

    old_active = _member_is_active(update.old_chat_member)
    new_active = _member_is_active(update.new_chat_member)

    # انضمام جديد: امنح مكافأة القناة فوراً، من غير انتظار زر التحقق.
    if not old_active and new_active:
        if channel.get("promotion_id"):
            completed_campaign = record_promoted_subscriber(
                channel["promotion_id"], user_id
            )
            if completed_campaign:
                notify_promotion_completed(completed_campaign)
                refresh_required_channels()
        reward_result = grant_channel_reward(user_id, channel)
        if reward_result is None:
            return
        try:
            bot.send_message(
                user_id,
                "🎉 <b>تم رصد انضمامك تلقائياً!</b>\n\n"
                f"✅ القناة: <b>{html.escape(channel['name'])}</b>\n"
                f"💰 تمت إضافة <b>{format_balance(reward_result['points'])}</b> فوراً إلى رصيدك.\n\n"
                "أكمل الانضمام لبقية القنوات الإلزامية، ثم اضغط التحقق لفتح كل الميزات.",
            )
        except Exception:
            pass
        return

    if new_active:
        return

    channels = get_channels_status(user_id)
    penalized = enforce_channel_subscriptions(user_id, channels)

    lines = "\n".join(
        f"• {html.escape(item['name'])}: -{format_balance(item['deducted_points'])}"
        for item in penalized
    )
    try:
        bot.send_message(
            user_id,
            "🚫 <b>تم تجميد حسابك</b>\n\n"
            "غادرت أو تم حظرك من قناة إلزامية، لذلك توقفت جميع ميزات البوت.\n"
            + (f"تم خصم المكافأة المتاحة:\n{lines}\n\n" if lines else "")
            + "أعد الانضمام إلى جميع القنوات ثم اضغط «تحقق من الاشتراك» "
            "لفتح الحساب واستعادة الوصول.",
            reply_markup=activation_gate_keyboard(user_id),
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# ─── دالة إرسال الطلب لسيرفر الرشق ───────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def place_smm_order(service_id: str, link: str, quantity: int) -> dict:
    """
    ترسل طلب HTTP POST لسيرفر الرشق.
    تُعيد dict يحتوي على:
        {"success": True,  "order_id": "12345"}
        {"success": False, "error":    "رسالة الخطأ"}
    """
    try:
        resp = http.post(
            SMM_API_URL,
            data={
                "key":      SMM_API_KEY,
                "action":   "add",
                "service":  service_id,
                "link":     link,
                "quantity": quantity,
            },
            timeout=15,
        )
        data = resp.json()

        if "order" in data:
            return {"success": True, "order_id": str(data["order"])}
        elif "error" in data:
            return {"success": False, "error": data["error"]}
        else:
            return {"success": False, "error": f"استجابة غير متوقعة: {data}"}

    except http.exceptions.Timeout:
        return {"success": False, "error": "انتهت مهلة الاتصال بالسيرفر."}
    except http.exceptions.ConnectionError:
        return {"success": False, "error": "تعذّر الاتصال بسيرفر الرشق."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# ─── لوحات الأزرار ────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def main_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    if TELEGRAM_MINI_APP_URL.startswith("https://"):
        markup.add(InlineKeyboardButton(
            "🎁 شاهد واربح من التطبيق",
            web_app=WebAppInfo(url=TELEGRAM_MINI_APP_URL),
        ))
    markup.row(
        InlineKeyboardButton("👤 الملف الشخصي", callback_data="profile"),
        InlineKeyboardButton("🎯 شارك الرابط واربح",   callback_data="earn_points"),
    )
    markup.add(InlineKeyboardButton(
        "📣 تثبيت إعلان / روّج لقناتك",
        callback_data="promote_channel",
    ))
    markup.add(InlineKeyboardButton(
        "📢 إضافة إعلان",
        callback_data="add_user_ad",
    ))
    markup.add(InlineKeyboardButton(
        "مشاهدة إعلانات وفيديوهات 📺",
        callback_data="watch_ads",
    ))
    markup.row(
        InlineKeyboardButton("📋 المهام اليومية", callback_data="daily_tasks"),
        InlineKeyboardButton("🛒 متجر الخدمات",   callback_data="shop"),
    )
    markup.add(InlineKeyboardButton("💳 شحن الرصيد", callback_data="buy_points"))
    markup.add(InlineKeyboardButton("سحب الأرباح 💰", callback_data="withdraw_earnings"))
    return markup


def promotion_packages_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for package_key, package in PROMOTION_PACKAGES.items():
        markup.add(InlineKeyboardButton(
            f"📣 {package['label']} — {promotion_package_price(package)}",
            callback_data=f"promotion_package_{package_key}",
        ))
    markup.add(InlineKeyboardButton(
        "🔙 رجوع للقائمة الرئيسية",
        callback_data="back_main",
    ))
    return markup


def payment_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_main"))
    return markup


def withdrawal_method_keyboard() -> InlineKeyboardMarkup:
    """Customer-facing withdrawal method picker (V2 only)."""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(
            "فودافون كاش 🔴",
            callback_data="withdraw_v2_vodafone",
        ),
        InlineKeyboardButton(
            "USDT (BEP-20) 🪙",
            callback_data="withdraw_v2_usdt",
        ),
    )
    markup.add(InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_main"))
    return markup


def shop_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for category_key, category in SERVICES.items():
        markup.add(InlineKeyboardButton(
            category["name"],
            callback_data=f"shop_category_{category_key}",
        ))
    markup.add(InlineKeyboardButton("📦 طلباتي السابقة", callback_data="my_orders"))
    markup.add(InlineKeyboardButton("🔙 رجوع",           callback_data="back_main"))
    return markup


def category_keyboard(category_key: str) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    category = SERVICES[category_key]
    for service_key, svc in category["services"].items():
        label = f"{svc['emoji']} {service_display_name(service_key, svc)}".strip()
        markup.add(InlineKeyboardButton(
            f"{label} — {get_service_quantity(service_key)} وحدة / "
            f"{format_balance(get_service_price(service_key))}",
            callback_data=f"buy_{service_key}",
        ))
    markup.add(InlineKeyboardButton("🔙 العودة للمنصات", callback_data="shop"))
    return markup


def confirm_keyboard(service_key: str) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ تأكيد الطلب",  callback_data=f"confirm_{service_key}"),
        InlineKeyboardButton("❌ إلغاء",          callback_data="shop"),
    )
    return markup


def tasks_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for ch in channels:
        status = "✅" if ch["rewarded"] else "🔔"
        markup.add(InlineKeyboardButton(
            f"{status} {ch['name']} — {format_balance(ch['reward'])}",
            url=f"https://t.me/{ch['username'].lstrip('@')}",
        ))
        if not ch["rewarded"]:
            markup.add(InlineKeyboardButton(
                f"✔️ تحقق من الاشتراك في {ch['name']}",
                callback_data=f"check_channel_{ch['task_key']}",
            ))
    markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
    return markup


def referral_tasks_keyboard(tasks) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for task in tasks:
        markup.add(InlineKeyboardButton(
            f"🚀 فتح المهمة #{task['id']}",
            url=task["referral_link"],
        ))
        markup.add(InlineKeyboardButton(
            "✅ تأكيد تنفيذ المهمة",
            callback_data=f"claim_referral_{task['id']}",
        ))
    return markup


def referral_claim_decision_keyboard(claim_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(
            "✅ موافق — تم التنفيذ",
            callback_data=f"approve_referral_claim_{claim_id}",
        ),
        InlineKeyboardButton(
            "❌ رفض التنفيذ",
            callback_data=f"reject_referral_claim_{claim_id}",
        ),
    )
    return markup


def referral_complaint_keyboard(claim_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        "📷 رفع شكوى مع صورة إثبات",
        callback_data=f"complaint_referral_{claim_id}",
    ))
    return markup


def referral_complaint_review_keyboard(complaint_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(
            "✅ اعتماد الشكوى",
            callback_data=f"approve_referral_complaint_{complaint_id}",
        ),
        InlineKeyboardButton(
            "❌ رفض الشكوى",
            callback_data=f"reject_referral_complaint_{complaint_id}",
        ),
    )
    return markup


def user_ad_review_keyboard(ad_id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(
            "✅ موافقة",
            callback_data=f"approve_user_ad_{ad_id}",
        ),
        InlineKeyboardButton(
            "❌ رفض",
            callback_data=f"reject_user_ad_{ad_id}",
        ),
    )
    return markup


def manual_tasks_keyboard(tasks) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for task in tasks:
        markup.add(InlineKeyboardButton(
            f"📌 تفاصيل المهمة #{task['id']}",
            callback_data=f"open_manual_{task['id']}",
        ))
        if not manual_task_requires_proof(task):
            markup.add(InlineKeyboardButton(
                "✅ تأكيد تنفيذ المهمة",
                callback_data=f"claim_manual_{task['id']}",
            ))
    return markup


def activation_gate_keyboard(user_id: int) -> InlineKeyboardMarkup:
    channels      = get_channels_status(user_id)
    _, pending_tasks = get_activation_requirements(user_id)
    markup = InlineKeyboardMarkup()
    for ch in channels:
        status = "✅" if ch["subscribed"] else "🔔"
        markup.add(InlineKeyboardButton(
            f"{status} {ch['name']} — {format_balance(ch['reward'])}",
            url=f"https://t.me/{ch['username'].lstrip('@')}",
        ))
        if not ch["subscribed"]:
            markup.add(InlineKeyboardButton(
                f"✔️ تحقق من الاشتراك في {ch['name']}",
                callback_data=f"check_channel_{ch['task_key']}",
            ))
    for task in pending_tasks:
        markup.add(InlineKeyboardButton(
            f"🔗 {task['title'][:40]}",
            callback_data=f"open_manual_{task['id']}",
        ))
        if not manual_task_requires_proof(task):
            markup.add(InlineKeyboardButton(
                f"✅ تأكيد تنفيذ المهمة #{task['id']}",
                callback_data=f"claim_manual_{task['id']}",
            ))
    markup.add(InlineKeyboardButton(
        "✅ تحقق من إتمام كافة الشروط",
        callback_data="verify_activation",
    ))
    return markup


def build_activation_gate_text(user_id: int) -> str:
    channels      = get_channels_status(user_id)
    _, pending_tasks = get_activation_requirements(user_id)
    text = (
        "⚠️ <b>عذراً يا غالي!</b>\n"
        "حسابك غير نشط أو مجمّد حالياً.\n\n"
        "لفتح جميع ميزات البوت يجب الاشتراك في <b>جميع</b> القنوات التالية:\n\n"
    )
    for ch in channels:
        status = "✅ مشترك" if ch["subscribed"] else "⏳ مطلوب"
        text += (
            f"📢 <b>{html.escape(ch['name'])}</b>\n"
            f"   الحالة: {status} | المكافأة: <b>{format_balance(ch['reward'])}</b>\n\n"
        )
    if pending_tasks:
        text += "📝 <b>المهام اليدوية المتبقية:</b>\n"
        for task in pending_tasks:
            text += (
                f"• {html.escape(task['title'])} — "
                f"<b>{format_balance(task['reward_points'])}</b>\n"
            )
        text += "\n"
    else:
        text += "📝 <b>المهام اليدوية:</b> ✅ مكتملة\n\n"
    total_ch_reward = sum(ch["reward"] for ch in channels)
    text += (
        "⚠️ <b>تحذير:</b> مغادرة أي قناة يؤدي إلى خصم مكافأتها وتجميد حسابك فوراً!\n\n"
        "اضغط الزر أدناه بعد الاشتراك في جميع القنوات للتحقق وفتح حسابك.\n"
        f"🎁 <b>إجمالي مكافآت القنوات:</b> {format_balance(total_ch_reward)}"
    )
    return text


def show_activation_gate(
    chat_id: int,
    user_id: int,
    message_id: int | None = None,
):
    """يعرض شاشة القفل مع أزرار القناة والمهام المتبقية."""
    text = build_activation_gate_text(user_id)
    markup = activation_gate_keyboard(user_id)
    if message_id is None:
        return bot.send_message(chat_id, text, reply_markup=markup)
    try:
        return bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=markup,
        )
    except Exception:
        return bot.send_message(chat_id, text, reply_markup=markup)


def require_active_account(call) -> bool:
    """يمنع الوصول إلى واجهة المستخدم عند عدم إتمام التحقق أو عدم تفعيل الحساب."""
    # أولاً: التحقق من مكافحة البوتات
    if not is_user_verified(call.from_user.id):
        show_anti_bot_challenge(call.message.chat.id, call.from_user.id)
        bot.answer_callback_query(call.id, "🤖 أكمل التحقق أولاً.", show_alert=True)
        return False
    # ثانياً: تفعيل الحساب (اشتراك القنوات)
    if account_access_allowed(call.from_user.id):
        return True
    show_activation_gate(
        call.message.chat.id,
        call.from_user.id,
        call.message.message_id,
    )
    bot.answer_callback_query(
        call.id,
        "🔒 أكمل شروط التفعيل أولاً.",
        show_alert=True,
    )
    return False


def admin_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 إحصائيات البوت",      callback_data="admin_stats"),
        InlineKeyboardButton("📢 إذاعة رسالة",          callback_data="admin_broadcast"),
    )
    markup.add(InlineKeyboardButton("➕ شحن رصيد لمستخدم", callback_data="admin_topup"))
    markup.add(InlineKeyboardButton("➕ إضافة مهمة يدوياً", callback_data="admin_add_task"))
    markup.add(InlineKeyboardButton(
        "⚙️ أسعار وكميات الخدمات",
        callback_data="admin_service_prices",
    ))
    markup.add(InlineKeyboardButton(
        "📣 إدارة باقات ترويج القنوات",
        callback_data="admin_promotion_packages",
    ))
    markup.add(InlineKeyboardButton(
        "📺 إدارة إعلانات المشاهدة",
        callback_data="admin_watch_ads",
    ))
    markup.add(InlineKeyboardButton(
        "💰 تعديل الحد الأدنى للسحب",
        callback_data="admin_set_min_withdrawal",
    ))
    markup.add(InlineKeyboardButton(
        "💸 طلبات السحب V2 المعلقة",
        callback_data="admin_list_v2_withdrawals",
    ))
    markup.add(InlineKeyboardButton("🔙 إغلاق اللوحة", callback_data="admin_close"))
    return markup


def service_prices_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        f"📺 مكافأة مشاهدة الإعلان — {format_balance(get_ad_reward())}",
        callback_data="admin_set_ad_reward",
    ))
    for service_key, service in SERVICE_INDEX.items():
        selling = get_service_price(service_key)
        markup.add(InlineKeyboardButton(
            f"{service['emoji']} {service_display_name(service_key, service)} — "
            f"{get_service_quantity(service_key)} وحدة / "
            f"{format_balance(selling)}",
            callback_data=f"admin_service_settings_{service_key}",
        ))
    markup.add(InlineKeyboardButton(
        "🔙 لوحة التحكم",
        callback_data="admin_panel",
    ))
    return markup


def referral_keyboard(user_id: int) -> InlineKeyboardMarkup:
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    share_url = (
        f"https://t.me/share/url?url={referral_link}"
        "&text=انضم+معي+واحصل+على+نقاط+مجانية+الآن+🎁"
    )
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📤 شارك رابطك الآن!", url=share_url))
    markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
    return markup


# ══════════════════════════════════════════════════════════════════════════════
# ─── بانيات نصوص الشاشات ─────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def build_shop_text(balance_cents: int) -> str:
    lines = [
        "🛒 <b>متجر الخدمات</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>رصيدك الحالي:</b> {format_balance(balance_cents)}\n",
        "اختر المنصة أولاً لعرض خدماتها:",
        "",
    ]
    for category in SERVICES.values():
        lines.append(
            f"• <b>{category['name']}</b> "
            f"({len(category['services'])} خدمات)\n"
        )
    return "\n".join(lines)



def fetch_cpagrip_offers_for_user(user_id: int, limit: int = 5) -> list[dict]:
    """Fetch CPA offers from CPAGrip RSS for a specific user.

    - Fetches XML from CPAGrip RSS Offer Feed.
    - Generates a unique tracking_id (UUID4) per offer.
    - Appends &tracking_id= to offerlink.
    - Saves mapping to cpagrip_offers table.
    - Returns list of offer dicts (read-only, no balance changes).
    """
    if not CPAGRIP_USER_ID or not CPAGRIP_KEY:
        return []

    try:
        params = {
            "user_id": CPAGRIP_USER_ID,
            "key": CPAGRIP_KEY,
            "limit": str(limit + 5),
        }
        resp = http.get(CPAGRIP_RSS_URL, params=params, timeout=20)
        if resp.status_code != 200:
            logging.getLogger("telegram_reward_api").warning(
                "CPAGrip RSS returned HTTP %d", resp.status_code
            )
            return []
    except Exception as exc:
        logging.getLogger("telegram_reward_api").error(
            "CPAGrip RSS failed: %s", exc
        )
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return []

    offers = []
    with get_connection() as conn:
        for item in root.findall(".//item"):
            if len(offers) >= limit:
                break

            offer_id_el = item.find("offer_id")
            title_el = item.find("title")
            payout_el = item.find("payout")
            offerlink_el = item.find("offerlink")
            category_el = item.find("category")
            description_el = item.find("description")

            if offerlink_el is None or offerlink_el.text is None:
                continue

            offer_id = (offer_id_el.text or "").strip() if offer_id_el is not None else ""
            title = (title_el.text or "").strip() if title_el is not None else ""
            payout_raw = (payout_el.text or "").strip() if payout_el is not None else ""
            offerlink_base = (offerlink_el.text or "").strip()
            category = (category_el.text or "").strip() if category_el is not None else ""
            description = (description_el.text or "").strip() if description_el is not None else ""

            if not offer_id or not offerlink_base:
                continue

            # Generate unique tracking_id (UUID4 — not guessable, no user_id leak)
            tracking_id = uuid.uuid4().hex

            # Append tracking_id to offerlink (verified: CPAGrip accepts this)
            separator = "&" if "?" in offerlink_base else "?"
            offerlink_with_tracking = f"{offerlink_base}{separator}tracking_id={tracking_id}"

            # Persist mapping in DB (atomic within this connection)
            try:
                cursor = conn.cursor()
                # Verify user exists before inserting
                cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
                if not cursor.fetchone():
                    continue

                cursor.execute(
                    "INSERT INTO cpagrip_offers "
                    "(user_id, offer_id, tracking_id, title, payout_raw, offerlink, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                    (user_id, offer_id, tracking_id, title, payout_raw, offerlink_with_tracking),
                )
            except sqlite3.IntegrityError:
                # Extremely unlikely UUID4 collision — skip
                continue
            except sqlite3.Error:
                continue

            offers.append({
                "offer_id": offer_id,
                "title": title,
                "payout_raw": payout_raw,
                "category": category,
                "description": description[:120] if description else "",
                "offerlink": offerlink_with_tracking,
                "tracking_id": tracking_id,
            })

    return offers


def build_tasks_text(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    channels       = get_channels_status(user_id)
    referral_tasks = get_active_referral_tasks(user_id)
    manual_tasks   = get_active_manual_tasks()
    cpagrip_offers = fetch_cpagrip_offers_for_user(user_id, limit=5)

    text = (
        "📋 <b>المهام اليومية</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أنجز المهام التالية واكسب نقاطاً مجانية:\n\n"
        "📢 <b>قنوات الاشتراك الإجباري</b>\n"
    )
    for ch in channels:
        ch_status = (
            "✅ <b>تم الاشتراك</b> — استلمتَ مكافأتك!"
            if ch["rewarded"] else
            "⭕ <b>لم تشترك بعد</b>"
        )
        text += (
            f"  • {html.escape(ch['name'])} — <b>{format_balance(ch['reward'])}</b>\n"
            f"    الحالة: {ch_status}\n"
        )
    text += "\n<i>💡 اضغط على اسم القناة للاشتراك ثم اضغط «تحقق من الاشتراك».</i>"
    if referral_tasks:
        text += (
            "\n\n💰 <b>مهام مدفوعة</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "『قم بالدخول إلى هذا البوت واضغط Start واكسب 10 نقاط مجانية 🎁』\n\n"
        )
        for task in referral_tasks:
            text += (
                f"👥 <b>مهمة إحالة #{task['id']}</b>\n"
                f"🔗 <code>{html.escape(task['referral_link'])}</code>\n"
                f"🎁 المكافأة: <b>{format_balance(REFERRAL_REWARD)}</b>\n"
                f"📊 المتبقي: <b>{task['quantity_remaining']}</b> إحالة\n\n"
            )
    if manual_tasks:
        text += (
            "\n\n📝 <b>مهام إضافية</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "نفّذ شروط المعلن ثم أرسل الإثبات المطلوب:\n\n"
        )
        for task in manual_tasks:
            text += (
                f"📝 <b>{html.escape(task['title'])}</b>\n"
                f"⚙️ النوع: <b>{manual_task_type_label(task)}</b>\n"
                f"🎯 الهدف: <code>{html.escape(manual_task_target(task))}</code>\n"
                f"📋 الشروط: {html.escape(manual_task_instructions(task))}\n"
                f"🎁 المكافأة: <b>{format_balance(task['reward_points'])}</b>\n"
                f"📊 المتبقي: <b>{task['quantity_remaining']}</b> تنفيذ\n\n"
            )

    # ─── عروض CPAGrip (مهام CPA) ─────────────────────────────────────────
    if cpagrip_offers:
        text += (
            "\n\n🎯 <b>عروض CPA المتاحة</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "نفّذ العرض واكسب المكافأة المحددة:\n\n"
        )
        for i, offer in enumerate(cpagrip_offers, 1):
            desc_line = ""
            if offer["description"]:
                desc_line = f"    📝 {html.escape(offer['description'])}\n"
            text += (
                f"  {i}. <b>{html.escape(offer['title'])}</b>\n"
                f"    💰 المكافأة: <b>${html.escape(offer['payout_raw'])}</b>\n"
                f"{desc_line}"
            )
        text += "\n<i>💡 اضغط على الزر لفتح العرض ثم نفّذ الشروط المطلوبة.</i>"

    markup = InlineKeyboardMarkup()
    for task_group, keyboard_builder in (
        (referral_tasks, referral_tasks_keyboard),
        (manual_tasks, manual_tasks_keyboard),
    ):
        if task_group:
            task_markup = keyboard_builder(task_group)
            for row in task_markup.keyboard:
                markup.keyboard.append(row)
    for row in tasks_keyboard(channels).keyboard:
        markup.keyboard.append(row)
    # ─── أزرار عروض CPAGrip (فتح الرابط مباشرة) ──────────────────────────
    for offer in cpagrip_offers:
        markup.add(InlineKeyboardButton(
            f"🎯 {html.escape(offer['title'])} — ${html.escape(offer['payout_raw'])}",
            url=offer["offerlink"],
        ))
    return text, markup


def parse_referral_link(link: str) -> bool:
    """يتحقق من أن الرابط رابط بوت تيليجرام ويحمل معامل start غير فارغ."""
    try:
        parsed = urlsplit(link)
        host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
        start_values = parse_qs(parsed.query).get("start", [])
        return (
            parsed.scheme in ("http", "https")
            and host in ("t.me", "telegram.me", "www.t.me", "www.telegram.me")
            and bool(parsed.path.strip("/"))
            and bool(start_values and start_values[0].strip())
            and "?start=" in link
            and parsed.path.strip("/").lower() != BOT_USERNAME.lower()
        )
    except ValueError:
        return False


def is_valid_service_link(link: str, service: dict) -> bool:
    """يتحقق من روابط الحسابات والصفحات والمنشورات والفيديوهات."""
    if not link:
        return False
    if link.startswith("@"):
        return len(link) > 1 and "category_key" in service
    try:
        parsed = urlsplit(link)
        host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
        if parsed.scheme not in ("http", "https") or not host:
            return False
        if not parsed.path.strip("/"):
            return False

        category_key = service.get("category_key")
        allowed_hosts = {
            "telegram": ("t.me", "telegram.me", "www.t.me", "www.telegram.me"),
            "facebook": ("facebook.com", "www.facebook.com", "m.facebook.com"),
            "tiktok": ("tiktok.com", "www.tiktok.com", "vm.tiktok.com"),
            "instagram": ("instagram.com", "www.instagram.com"),
            "twitter": ("twitter.com", "www.twitter.com", "x.com", "www.x.com"),
        }
        hosts = allowed_hosts.get(category_key)
        return hosts is None or host in hosts
    except ValueError:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# ─── /admin ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if not is_admin(message.from_user.id):
        return   # تجاهل صامت لغير المشرف
    user_state.pop(message.from_user.id, None)
    bot.send_message(
        message.chat.id,
        "🔐 <b>لوحة تحكم المشرف</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "مرحباً بك يا مشرف! اختر أحد الخيارات:",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    help_markup = InlineKeyboardMarkup()
    help_markup.add(
        InlineKeyboardButton(
            "📩 التواصل مع الإدارة",
            url=f"tg://user?id={ADMIN_ID}",
        )
    )
    if is_account_active(message.from_user.id):
        help_markup.add(
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
        )
    else:
        help_markup.add(
            InlineKeyboardButton(
                "🔒 شروط تفعيل الحساب",
                callback_data="verify_activation",
            )
        )
    bot.send_message(
        message.chat.id,
        "ℹ️ <b>المساعدة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "• استخدم /start لفتح القائمة الرئيسية.\n"
        "• استخدم «💳 شراء نقاط» لشحن رصيدك وإرسال إيصال التحويل.\n"
        "• استخدم «🛒 متجر الخدمات» لاستبدال نقاطك بالخدمات.\n"
        "• استخدم «📋 المهام اليومية» لكسب نقاط إضافية.\n\n"
        "لأي استفسار أو مشكلة، اضغط زر التواصل مع الإدارة أدناه.",
        reply_markup=help_markup,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ─── /start ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user    = message.from_user
    payload = message.text.split(maxsplit=1)
    ref_id  = None

    if len(payload) > 1:
        try:
            ref_id = int(payload[1].strip())
            if ref_id == user.id:
                ref_id = None
        except ValueError:
            ref_id = None

    referral_has_username = bool((user.username or "").strip())
    stored_ref_id = ref_id if referral_has_username else None
    is_new = add_user(
        user.id,
        user.first_name,
        user.last_name,
        user.username,
        stored_ref_id,
    )

    if is_new and ref_id and not referral_has_username:
        try:
            bot.send_message(
                user.id,
                "⚠️ لا يتم احتساب النقاط للحسابات الوهمية بدون اسم مستخدم!",
            )
        except Exception:
            pass

    if is_new and stored_ref_id:
        referrer = get_user(stored_ref_id)
        if referrer:
            if record_referral(stored_ref_id, user.id):
                newcomer_name = user.first_name
                if user.last_name:
                    newcomer_name += f" {user.last_name}"
                try:
                    bot.send_message(
                        stored_ref_id,
                        f"🎉 <b>تم تسجيل إحالة معلّقة!</b>\n\n"
                        f"✅ انضم <b>{newcomer_name}</b> عبر رابطك.\n"
                        f"💰 ستُضاف مكافأة الإحالة <b>{format_balance(REFERRAL_REWARD)}</b> "
                        "فور تحقق شروط فتح الحساب.",
                    )
                except Exception:
                    pass

    # مسح أي حالة سابقة للمحادثة
    user_state.pop(user.id, None)

    # ─── فحص التحقق من م협حة البوتات ──────────────────────────────────────
    if not is_user_verified(user.id):
        show_anti_bot_challenge(message.chat.id, user.id)
        return
    current_user = get_user(user.id)
    if current_user is not None and not account_access_allowed(user.id):
        show_activation_gate(message.chat.id, user.id)
        return

    # ─── عرض القائمة الرئيسية ─────────────────────────────────────────────
    greeting = (
        f"🌟 <b>مرحباً يا {user.first_name}!</b>\n\n"
        "اختر أحد الخيارات أدناه:"
    )
    bot.send_message(message.chat.id, greeting, reply_markup=main_keyboard())


# ══════════════════════════════════════════════════════════════════════════════
# ─── معالج إجابات مكافحة البوتات ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data.startswith("antibot_"))
def callback_anti_bot(call):
    user_id = call.from_user.id
    try:
        chosen = int(call.data[len("antibot_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "خيارات غير صالحة.", show_alert=True)
        return

    success, result = verify_anti_bot_answer(user_id, chosen)

    if result == "expired":
        bot.answer_callback_query(call.id, "⏰ انتهت صلاحية الاختبار.", show_alert=True)
        show_anti_bot_challenge(call.message.chat.id, user_id)
        return

    if success:
        mark_user_verified(user_id)
        bot.answer_callback_query(call.id, "✅ تحقّقت بنجاح!", show_alert=True)
        # عرض القائمة الرئيسية
        current_user = get_user(user_id)
        greeting = (
            f"👋 <b>أهلاً وسهلاً يا {call.from_user.first_name}!</b>\n\n"
            "يسعدنا انضمامك إلينا. تم تسجيل حسابك بنجاح ✅\n\n"
            "اختر أحد الخيارات أدناه للبدء:"
        )
        bot.edit_message_text(
            greeting,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_keyboard(),
        )
        return

    # إجابة خاطئة
    session = anti_bot_sessions.get(user_id)
    if result == "max_attempts":
        bot.answer_callback_query(
            call.id,
            "❌ استنفدتم المحاولات. جرّب مجدداً.",
            show_alert=True,
        )
        # إنشاء اختبار جديد
        show_anti_bot_challenge(call.message.chat.id, user_id)
    else:
        remaining = ANTI_BOT_MAX_ATTEMPTS - (session["attempts"] if session else 0)
        bot.answer_callback_query(
            call.id,
            f"❌ إجابة خاطئة. متبقي {remaining} محاولات.",
            show_alert=True,
        )
        # عرض الاختبار مجدداً مع نفس السؤال
        session_data = anti_bot_sessions.get(user_id, {}); question = session_data.get("question", "?")
        options = session_data.get("options", [])
        if not options:
            question, options = start_anti_bot_verification(user_id)
        bot.edit_message_text(
            f"🤖 <b>للتحقق من أنك لست روبوتًا</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"احسب العملية التالية:\n\n"
            f"🧮 <b>{question} = ؟</b>\n\n"
            f"اختر الإجابة الصحيحة:\n"
            f"(متبقي {remaining} محاولات)",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=build_verification_keyboard(options),
        )

    greeting = (
        f"👋 <b>أهلاً وسهلاً يا {user.first_name}!</b>\n\n"
        "يسعدنا انضمامك إلينا. تم تسجيل حسابك بنجاح ✅\n\n"
        "اختر أحد الخيارات أدناه للبدء:"
        if is_new else
        f"🌟 <b>مرحباً مجدداً يا {user.first_name}!</b>\n\n"
        "اختر أحد الخيارات أدناه:"
    )
    bot.send_message(message.chat.id, greeting, reply_markup=main_keyboard())


# ══════════════════════════════════════════════════════════════════════════════
# ─── معالج الرسائل النصية (لاستقبال الروابط) ─────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and m.reply_to_message is not None
    and get_manual_review_by_admin_message(m.reply_to_message.message_id) is not None
    and (m.text or "").strip() in {"تم", "مقبول"},
    content_types=["text"],
)
def handle_admin_manual_review_reply(message):
    """يعتمد المشرف إثبات المهمة بالرد «تم» أو «مقبول»."""
    review = get_manual_review_by_admin_message(
        message.reply_to_message.message_id
    )
    if review is None:
        return

    approved = approve_manual_task_review(review["id"])
    if approved is None:
        bot.reply_to(
            message,
            "ℹ️ هذه المهمة تمت مراجعتها مسبقاً أو لم تعد متاحة.",
        )
        return

    user_id = approved["user_id"]
    was_active = is_account_active(user_id)
    activated = not was_active and activate_user(user_id)
    access_granted = activated or account_access_allowed(user_id)
    updated = get_user(user_id)
    try:
        bot.send_message(
            user_id,
            "🎉 <b>تم قبول إثبات المهمة!</b>\n\n"
            f"✅ تمت إضافة <b>{format_balance(approved['reward_points'])}</b> إلى رصيدك.\n"
            + (
                f"🔓 تم تفعيل حسابك وإضافة مكافأة التفعيل "
                f"<b>{format_balance(ACTIVATION_REWARD)}</b>."
                if activated
                else "سيتم تحديث واجهتك بعد اكتمال بقية الشروط."
            )
            + f"\n🏆 <b>رصيدك الحالي:</b> {balance_text(updated)}",
            reply_markup=(
                main_keyboard()
                if access_granted
                else activation_gate_keyboard(user_id)
            ),
        )
        bot.reply_to(
            message,
            "✅ تم قبول المهمة وإضافة الرصيد وإبلاغ المستخدم.",
        )
    except Exception:
        bot.reply_to(
            message,
            "✅ تم اعتماد المهمة، لكن تعذر إرسال إشعار المستخدم.",
        )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and m.reply_to_message is not None
    and get_ad_review_by_admin_message(m.reply_to_message.message_id) is not None
    and (m.text or "").strip() in {"تم", "مقبول"},
    content_types=["text"],
)
def handle_admin_ad_review_reply(message):
    """يعتمد المشرف إثبات مشاهدة الإعلان بالرد «تم» أو «مقبول»."""
    review = get_ad_review_by_admin_message(
        message.reply_to_message.message_id
    )
    if review is None:
        return

    approved = approve_ad_review(review["id"])
    if approved is None:
        bot.reply_to(
            message,
            "ℹ️ تمت مراجعة إثبات الإعلان مسبقاً أو لم يعد متاحاً.",
        )
        return

    updated = get_user(approved["user_id"])
    try:
        bot.send_message(
            approved["user_id"],
            "🎉 <b>تم قبول إثبات الإعلان!</b>\n\n"
            f"✅ تمت إضافة <b>{format_balance(approved['reward_points'])}</b> إلى رصيدك.\n"
            f"🏆 <b>رصيدك الحالي:</b> {balance_text(updated)}",
            reply_markup=main_keyboard(),
        )
        bot.reply_to(
            message,
            "✅ تم قبول إثبات الإعلان وإضافة المكافأة وإبلاغ المستخدم.",
        )
    except Exception:
        bot.reply_to(
            message,
            "✅ تم اعتماد الإعلان، لكن تعذر إرسال إشعار المستخدم.",
        )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and m.reply_to_message is not None
    and get_receipt_by_admin_message(m.reply_to_message.message_id) is not None,
    content_types=["text"],
)
def handle_admin_receipt_reply(message):
    """يرسل رد المشرف على إشعار الإيصال مباشرةً إلى العميل."""
    receipt = get_receipt_by_admin_message(message.reply_to_message.message_id)
    if receipt is None:
        return

    try:
        bot.send_message(
            receipt["user_id"],
            "📩 <b>رد من الإدارة بخصوص إيصالك:</b>\n\n"
            f"{html.escape(message.text)}",
        )
        mark_receipt_replied(receipt["id"])
        bot.reply_to(message, "✅ تم إرسال ردك إلى العميل بنجاح.")
    except Exception:
        bot.reply_to(
            message,
            "⚠️ تعذر إرسال الرد. ربما لم يبدأ العميل محادثة مع البوت أو قام بحظره.",
        )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and m.reply_to_message is not None
    and get_withdrawal_by_admin_message(m.reply_to_message.message_id) is not None
    and (m.text or "").strip() == "تم التحويل",
    content_types=["text"],
)
def handle_admin_withdrawal_reply(message):
    """يكمل طلب السحب عند رد المشرف بكلمة «تم التحويل»."""
    withdrawal = get_withdrawal_by_admin_message(
        message.reply_to_message.message_id
    )
    if withdrawal is None:
        return

    completed = complete_withdrawal(withdrawal["id"])
    if completed is None:
        bot.reply_to(
            message,
            "ℹ️ هذا الطلب مكتمل مسبقاً أو لم يعد قيد المراجعة.",
        )
        return

    try:
        bot.send_message(
            completed["user_id"],
            "🎉 <b>تم تحويل أرباحك بنجاح!</b>\n\n"
        f"تم اعتماد طلب سحب <b>{format_balance(completed['amount_cents'] or completed['points_amount'])}</b> "
            "وإرسال المبلغ إلى حسابك.\n"
            "شكراً لاستخدامك البوت 💰",
        )
        bot.reply_to(
            message,
            "✅ تم تحديث حالة طلب السحب إلى «مكتمل» وإبلاغ المستخدم.",
        )
    except Exception:
        bot.reply_to(
            message,
            "✅ تم تحديث حالة الطلب إلى «مكتمل»، لكن تعذر إرسال إشعار "
            "المستخدم. ربما قام بحظر البوت.",
        )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_referral_complaint_photo",
    content_types=["photo"],
)
def handle_referral_task_complaint_photo(message):
    """يستقبل إثبات المؤدي بعد رفض المعلن ويرسله للإدارة."""
    worker_id = message.from_user.id
    state = user_state.get(worker_id, {})
    claim_id = state.get("referral_claim_id")
    claim = get_referral_task_claim(claim_id) if claim_id else None
    if claim is None or claim["worker_id"] != worker_id:
        user_state.pop(worker_id, None)
        bot.send_message(message.chat.id, "⚠️ انتهت صلاحية طلب الشكوى.")
        return
    if claim["status"] != "client_rejected":
        user_state.pop(worker_id, None)
        bot.send_message(
            message.chat.id,
            "⚠️ لا يمكن رفع الشكوى الآن؛ تغيرت حالة التنفيذ.",
        )
        return

    file_id = message.photo[-1].file_id
    complaint_id = create_referral_task_complaint(
        claim_id, worker_id, file_id
    )
    if complaint_id is None:
        user_state.pop(worker_id, None)
        bot.send_message(
            message.chat.id,
            "⏳ توجد شكوى قيد المراجعة لهذا التنفيذ أو لم يعد متاحاً.",
        )
        return

    worker = get_user(worker_id)
    display_name = html.escape(
        " ".join(filter(None, [worker["first_name"], worker["last_name"]]))
        if worker else str(worker_id)
    )
    username = (
        f"@{html.escape(worker['username'])}"
        if worker and worker["username"] else "بدون معرف مستخدم"
    )
    caption = (
        "⚖️ <b>شكوى مهمة إحالة مدفوعة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>رقم الشكوى:</b> <code>{complaint_id}</code>\n"
        f"• <b>رقم المهمة:</b> <code>{claim['task_id']}</code>\n"
        f"• <b>المؤدي:</b> {display_name}\n"
        f"• <b>المعرف الرقمي:</b> <code>{worker_id}</code>\n"
        f"• <b>حساب Telegram:</b> {username}\n"
        f"• <b>المعلن:</b> <code>{claim['buyer_id']}</code>\n"
        f"• <b>التعويض عند الاعتماد:</b> "
        f"<b>{format_balance(REFERRAL_REWARD)}</b>\n\n"
        "راجع صورة الإثبات واختر اعتماد الشكوى أو رفضها."
    )
    try:
        admin_message = bot.send_photo(
            ADMIN_ID,
            file_id,
            caption=caption,
            reply_markup=referral_complaint_review_keyboard(complaint_id),
        )
        set_referral_complaint_admin_message(
            complaint_id, admin_message.message_id
        )
        user_state.pop(worker_id, None)
        bot.send_message(
            message.chat.id,
            "✅ تم إرسال الشكوى للإدارة للمراجعة. "
            "ستصلك النتيجة بعد اتخاذ القرار.",
        )
    except Exception:
        cancel_referral_task_complaint(complaint_id)
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر إرسال الشكوى للإدارة حالياً. حاول مرة أخرى لاحقاً.",
        )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_manual_proof",
    content_types=["photo"],
)
def handle_manual_task_proof(message):
    """يحفظ لقطة الشاشة ويرسلها للمشرف مع معرف المستخدم."""
    user_id = message.from_user.id
    state = user_state.get(user_id, {})
    task_id = state.get("manual_task_id")
    task = get_manual_task(task_id) if task_id else None
    user = get_user(user_id)
    if task is None or user is None or not manual_task_requires_proof(task):
        user_state.pop(user_id, None)
        bot.send_message(message.chat.id, "⚠️ انتهت صلاحية طلب إثبات المهمة.")
        return

    file_id = message.photo[-1].file_id
    review_id = create_manual_task_review(user_id, task_id, file_id)
    if review_id is None:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⏳ يوجد إثبات قيد المراجعة لهذه المهمة أو لم تعد متاحة.",
        )
        return

    username = (
        f"@{html.escape(user['username'])}"
        if user["username"] else "بدون معرف مستخدم"
    )
    display_name = html.escape(
        " ".join(filter(None, [user["first_name"], user["last_name"]]))
    )
    caption = (
        "🛡️ <b>مهمة خارجية معلقة للمراجعة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>رقم المراجعة:</b> <code>{review_id}</code>\n"
        f"• <b>المهمة:</b> {html.escape(task['title'])}\n"
        f"• <b>الهدف:</b> <code>{html.escape(manual_task_target(task))}</code>\n"
        f"• <b>الشروط:</b> {html.escape(manual_task_instructions(task))}\n"
        f"• <b>المعرف الرقمي:</b> <code>{user_id}</code>\n"
        f"• <b>اسم المستخدم:</b> {display_name}\n"
        f"• <b>حساب Telegram:</b> {username}\n"
        f"• <b>المكافأة:</b> <b>{format_balance(task['reward_points'])}</b>\n\n"
        "↩️ رد على هذه الرسالة بكلمة <b>تم</b> أو <b>مقبول</b> لاعتماد المهمة."
    )
    try:
        admin_message = bot.send_photo(ADMIN_ID, file_id, caption=caption)
        set_manual_review_admin_message(review_id, admin_message.message_id)
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "✅ تم استلام لقطة الشاشة وإرسالها للإدارة للمراجعة. "
            "سيتم احتساب النقاط بعد اعتمادها.",
        )
    except Exception:
        cancel_manual_task_review(review_id)
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر إرسال الإثبات للإدارة حالياً. حاول مرة أخرى لاحقاً.",
        )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_ad_proof",
    content_types=["photo"],
)
def handle_ad_proof(message):
    """يحفظ لقطة الإعلان ويرسلها إلى المشرف للمراجعة."""
    user_id = message.from_user.id
    user = get_user(user_id)
    if user is None:
        user_state.pop(user_id, None)
        bot.send_message(message.chat.id, "يرجى إرسال /start أولاً.")
        return

    file_id = message.photo[-1].file_id
    ad_link_id = user_state.get(user_id, {}).get("ad_link_id")
    if ad_link_id is None:
        active_ads = get_watch_ad_links()
        ad_link_id = active_ads[0]["id"] if active_ads else None
    review_id = create_ad_review(user_id, file_id, ad_link_id)
    if review_id is None:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⏳ يوجد إثبات إعلان قيد المراجعة بالفعل. "
            "انتظر رد الإدارة أولاً.",
        )
        return

    username = (
        f"@{html.escape(user['username'])}"
        if user["username"] else "بدون معرف مستخدم"
    )
    display_name = html.escape(
        " ".join(filter(None, [user["first_name"], user["last_name"]]))
    )
    review = get_ad_review(review_id)
    selected_ad = get_watch_ad_link(ad_link_id)
    caption = (
        "📺 <b>إثبات مشاهدة إعلان معلق</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>رقم المراجعة:</b> <code>{review_id}</code>\n"
        f"• <b>المعرف الرقمي:</b> <code>{user_id}</code>\n"
        f"• <b>اسم المستخدم:</b> {display_name}\n"
        f"• <b>حساب Telegram:</b> {username}\n"
        f"• <b>الإعلان:</b> "
        f"{html.escape(selected_ad['title']) if selected_ad else 'الإعلان الأساسي'}\n"
        f"• <b>المكافأة:</b> <b>{format_balance(review['reward_cents'] if review else get_ad_reward())}</b>\n"
        + (
            f"• <b>رابط الإعلان:</b> <code>{html.escape(selected_ad['url'])}</code>\n"
            if selected_ad is not None
            else ""
        )
        + "\n"
        "↩️ رد على هذه الرسالة بكلمة <b>تم</b> أو <b>مقبول</b> "
        "لاعتماد الإعلان."
    )
    try:
        admin_message = bot.send_photo(ADMIN_ID, file_id, caption=caption)
        set_ad_review_admin_message(review_id, admin_message.message_id)
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "✅ تم استلام لقطة الشاشة وإرسالها للإدارة للمراجعة. "
            "ستُضاف النقاط بعد اعتمادها.",
        )
    except Exception:
        cancel_ad_review(review_id)
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر إرسال إثبات الإعلان للإدارة حالياً. "
            "حاول مرة أخرى لاحقاً.",
        )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_withdrawal_amount"
)
def handle_withdrawal_amount(message):
    """يستقبل مبلغ السحب بالجنيه أو الدولار ويحفظه بالقروش."""
    user_id = message.from_user.id
    raw_amount = (message.text or "").strip()

    amount_cents = parse_currency_input(raw_amount)
    if amount_cents is None or amount_cents < get_min_withdrawal():
        bot.send_message(
            message.chat.id,
            f"⚠️ أدخل مبلغاً صحيحاً لا يقل عن "
            f"<b>{format_balance(get_min_withdrawal())}</b>.",
        )
        return

    user = get_user(user_id)
    if user is None:
        user_state.pop(user_id, None)
        bot.send_message(message.chat.id, "يرجى إرسال /start أولاً.")
        return
    if row_balance_cents(user) < amount_cents:
        bot.send_message(
            message.chat.id,
            f"❌ رصيدك الحالي هو <b>{balance_text(user)}</b>، "
            f"ولا يكفي لسحب <b>{format_balance(amount_cents)}</b>.",
        )
        return

    state = user_state[user_id]
    state["step"] = "awaiting_withdrawal_account"
    state["amount_cents"] = amount_cents
    method = state["withdrawal_method"]
    bot.send_message(
        message.chat.id,
        f"✅ طريقة السحب: <b>{method}</b>\n"
        f"✅ المبلغ: <b>{format_balance(amount_cents)}</b>\n\n"
        "📥 أرسل الآن رقم محفظتك أو حسابك لاستلام المبلغ:",
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_withdrawal_account"
)
def handle_withdrawal_account(message):
    """ينشئ طلب السحب ويبلغ المشرف مع ربط رسالة الإشعار بالطلب."""
    user_id = message.from_user.id
    account_details = (message.text or "").strip()
    state = user_state.get(user_id, {})
    amount_cents = state.get("amount_cents", state.get("points_amount"))
    method = state.get("withdrawal_method")

    if not account_details or len(account_details) > 250:
        bot.send_message(
            message.chat.id,
            "⚠️ أرسل رقم المحفظة أو الحساب بشكل صحيح "
            "(بحد أقصى 250 حرفاً).",
        )
        return
    if not isinstance(amount_cents, int) or not method:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⚠️ انتهت صلاحية طلب السحب. ابدأ العملية من جديد.",
        )
        return

    user = get_user(user_id)
    if user is None:
        user_state.pop(user_id, None)
        bot.send_message(message.chat.id, "يرجى إرسال /start أولاً.")
        return

    referral_check = run_referral_withdrawal_double_check(user_id)
    if referral_check["blocked"]:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "🚫 تم إيقاف طلب السحب بعد اكتشاف إحالة غير صالحة "
            "غادرت القناة أو تم حظرها.",
        )
        return
    if referral_check["unknown"]:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر التحقق من اشتراك بعض الإحالات حالياً. "
            "لن يتم إنشاء طلب السحب حتى يكتمل الفحص.",
        )
        return

    request_id = create_withdrawal_request(
        user_id,
        amount_cents,
        method,
        account_details,
    )
    if request_id == "cooldown":
        user_state.pop(user_id, None)
        bot.send_message(message.chat.id, WITHDRAWAL_COOLDOWN_MESSAGE)
        return
    if request_id == "fraud":
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "🚫 تم إيقاف طلب السحب بعد اكتشاف إحالة غير صالحة "
            "غادرت القناة أو تم حظرها.",
        )
        return
    if request_id == "verification_unavailable":
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر التحقق من اشتراك بعض الإحالات حالياً. "
            "لن يتم إنشاء طلب السحب حتى يكتمل الفحص.",
        )
        return
    if request_id is None:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "❌ تعذر إنشاء الطلب لأن رصيدك لم يعد كافياً. حاول مجدداً.",
        )
        return

    username = (
        f"@{html.escape(user['username'])}"
        if user["username"] else "بدون معرف مستخدم"
    )
    display_name = html.escape(
        " ".join(filter(None, [user["first_name"], user["last_name"]]))
    )
    admin_text = (
        "💰 <b>طلب سحب أرباح جديد</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>رقم الطلب:</b> <code>{request_id}</code>\n"
        f"• <b>اسم المستخدم:</b> {display_name}\n"
        f"• <b>المعرف:</b> <code>{user_id}</code>\n"
        f"• <b>حساب Telegram:</b> {username}\n"
        f"• <b>المبلغ المسحوب:</b> <b>{format_balance(amount_cents)}</b>\n"
        f"• <b>طريقة السحب:</b> {html.escape(method)}\n"
        f"• <b>رقم الحساب/المحفظة:</b> "
        f"<code>{html.escape(account_details)}</code>\n\n"
        "↩️ بعد التحويل، قم بالرد على هذه الرسالة بكلمة: "
        "<b>تم التحويل</b>"
    )

    try:
        admin_message = bot.send_message(ADMIN_ID, admin_text)
        set_withdrawal_admin_message(request_id, admin_message.message_id)
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "✨ تم استلام طلب السحب الخاص بك بنجاح وجاري مراجعته من قبل الإدارة، "
            "سيتم تحويل الأموال لحسابك خلال 24 ساعة كحد أقصى!",
        )
    except Exception:
        cancel_withdrawal_and_refund(request_id)
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر إرسال طلب السحب إلى الإدارة حالياً، "
            "وقد تمت إعادة نقاطك إلى رصيدك. يرجى المحاولة لاحقاً.",
        )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_receipt",
    content_types=["photo"],
)


# ══════════════════════════════════════════════════════════════════════════════
# ─── V2 Withdrawal Telegram Handlers ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(
    func=lambda c: c.data == "withdraw_v2_vodafone"
)
def callback_v2_withdraw_vodafone(call):
    user_id = call.from_user.id
    if not require_active_account(call):
        return
    cooldown = get_withdrawal_cooldown_remaining(user_id)
    if cooldown > 0:
        bot.answer_callback_query(
            call.id,
            f"⏳ لقد استخدمت طلب السحب الخاص بك بالفعل. "
            f"يمكنك طلب سحب جديد بعد: {format_cooldown_remaining(cooldown)}.",
            show_alert=True,
        )
        return
    user_state[user_id] = {
        "step": "awaiting_v2_vodafone_amount",
        "method_code": WITHDRAWAL_METHOD_VODAFONE,
    }
    bot.answer_callback_query(call.id)
    try:
        min_egp_cents, _rate, _is_fresh = get_vodafone_min_egp_cents(
            allow_stale=True
        )
        egp_equiv = _egp_cents_to_egp(min_egp_cents)
    except RuntimeError:
        bot.edit_message_text(
            "⚠️ تعذر الحصول على سعر الصرف الحالي. حاول لاحقاً.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
        return
    bot.edit_message_text(
        "💰 <b>سحب الأرباح — Vodafone Cash</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"الحد الأدنى: <b>${VODAFONE_MIN_USD:.2f}</b> "
        f"(≈ <b>{egp_equiv:.2f} EGP</b>)\n\n"
        "أرسل المبلغ بالجنيه المصري الذي تريد سحبه، مثل:\n"
        "<code>5</code> أو <code>25.50</code>\n\n"
        "<i>أرسل /start للإلغاء.</i>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "withdraw_v2_usdt"
)
def callback_v2_withdraw_usdt(call):
    user_id = call.from_user.id
    if not require_active_account(call):
        return
    cooldown = get_withdrawal_cooldown_remaining(user_id)
    if cooldown > 0:
        bot.answer_callback_query(
            call.id,
            f"⏳ لقد استخدمت طلب السحب الخاص بك بالفعل. "
            f"يمكنك طلب سحب جديد بعد: {format_cooldown_remaining(cooldown)}.",
            show_alert=True,
        )
        return
    user_state[user_id] = {
        "step": "awaiting_v2_usdt_amount",
        "method_code": WITHDRAWAL_METHOD_USDT,
    }
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "💰 <b>سحب الأرباح — USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "الشبكة: <b>BNB Smart Chain (BEP-20)</b>\n\n"
        f"الحد الأدنى: <b>0.15 USDT</b>\n\n"
        "أرسل كمية USDT التي تريد سحبها، مثل:\n"
        "<code>0.15</code> أو <code>1.5</code>\n\n"
        "<i>أرسل /start للإلغاء.</i>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_v2_vodafone_amount"
)
def handle_v2_vodafone_amount(message):
    user_id = message.from_user.id
    raw = (message.text or "").strip()
    egp_cents = parse_currency_input(raw)
    # Pre-fetch rate for display and dynamic minimum
    try:
        rate, provider, is_fresh = get_current_usdt_egp_rate(allow_stale=True)
    except RuntimeError:
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر الحصول على سعر الصرف حالياً. حاول لاحقاً.",
        )
        return
    if not is_fresh and not is_rate_within_max_age(_get_last_valid_rate()[2]):
        bot.send_message(
            message.chat.id,
            "⚠️ سعر الصرف قديم جداً. حاول لاحقاً.",
        )
        return
    min_egp_cents = compute_vodafone_min_egp_cents(rate)
    if egp_cents is None or egp_cents < min_egp_cents:
        bot.send_message(
            message.chat.id,
            f"⚠️ الحد الأدنى هو <b>${VODAFONE_MIN_USD:.2f}</b> "
            f"(≈ <b>{_egp_cents_to_egp(min_egp_cents):.2f} EGP</b>). "
            "أرسل المبلغ بالجنيه المصري.",
        )
        return
    usdt_amt = egp_to_usdt(egp_cents, rate)
    user_state[user_id]["step"] = "awaiting_v2_vodafone_destination"
    user_state[user_id]["requested_egp_cents"] = egp_cents
    user_state[user_id]["usdt_amount_str"] = str(usdt_amt)
    user_state[user_id]["rate"] = str(rate)
    bot.send_message(
        message.chat.id,
        f"✅ المبلغ: <b>{_egp_cents_to_egp(egp_cents):.2f} EGP</b>\n"
        f"💱 سعر الصرف: <b>{rate:.4f} EGP / USDT</b>\n"
        f"💰 ما يعادل: <b>{usdt_amt:.6f} USDT</b>\n\n"
        "📱 أرسل الآن رقم محفظة Vodafone Cash (11 رقم يبدأ بـ 01):",
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_v2_vodafone_destination"
)
def handle_v2_vodafone_destination(message):
    user_id = message.from_user.id
    dest = (message.text or "").strip()
    if not validate_vodafone_destination(dest):
        bot.send_message(
            message.chat.id,
            "⚠️ رقم Vodafone غير صالح. أرسل 11 رقم يبدأ بـ 01.",
        )
        return
    state = user_state[user_id]
    egp_cents = state["requested_egp_cents"]
    rate = Decimal(state["rate"])
    usdt_amt = egp_to_usdt(egp_cents, rate)
    state["destination"] = dest
    state["step"] = "awaiting_v2_vodafone_confirm"
    bot.send_message(
        message.chat.id,
        "<b>تأكيد طلب السحب</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "طريقة السحب:\nVodafone Cash\n\n"
        f"المبلغ:\n{_egp_cents_to_egp(egp_cents):.2f} EGP\n\n"
        "رسوم السحب:\n0 EGP\n\n"
        f"سعر الصرف المستخدم:\n{rate:.4f} EGP / USDT\n\n"
        f"القيمة المحاسبية:\n{usdt_amt:.6f} USDT\n\n"
        f"المحفظة:\n{dest}\n\n"
        "هل تريد تأكيد الطلب؟",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد", callback_data="v2_confirm_withdraw")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="back_main")],
        ]),
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_v2_usdt_amount"
)
def handle_v2_usdt_amount(message):
    user_id = message.from_user.id
    raw = (message.text or "").strip()
    try:
        usdt_amt = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        bot.send_message(
            message.chat.id,
            "⚠️ أرسل كمية USDT صالحة (رقم موجب).",
        )
        return
    if usdt_amt < USDT_MIN_USDT:
        bot.send_message(
            message.chat.id,
            f"⚠️ الحد الأدنى هو 0.15 USDT.",
        )
        return
    try:
        rate, provider, is_fresh = get_current_usdt_egp_rate(allow_stale=True)
    except RuntimeError:
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر الحصول على سعر الصرف حالياً. حاول لاحقاً.",
        )
        return
    if not is_fresh and not is_rate_within_max_age(_get_last_valid_rate()[2]):
        bot.send_message(
            message.chat.id,
            "⚠️ سعر الصرف قديم جداً. حاول لاحقاً.",
        )
        return
    user_state[user_id]["step"] = "awaiting_v2_usdt_destination"
    user_state[user_id]["usdt_amount_str"] = str(usdt_amt)
    user_state[user_id]["rate"] = str(rate)
    egp_equiv = usdt_to_egp_cents(usdt_amt, rate)
    bot.send_message(
        message.chat.id,
        f"✅ الكمية: <b>{usdt_amt:.6f} USDT</b>\n"
        f"💱 سعر الصرف: <b>{rate:.4f} EGP / USDT</b>\n"
        f"💰 ما يعادل: <b>{_egp_cents_to_egp(egp_equiv):.2f} EGP</b>\n\n"
        "🌐 الشبكة: <b>BNB Smart Chain (BEP-20)</b>\n\n"
        "📥 أرسل عنوان محفظتك على BEP-20 (يبدأ بـ 0x، 42 محرف):",
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_v2_usdt_destination"
)
def handle_v2_usdt_destination(message):
    user_id = message.from_user.id
    addr = (message.text or "").strip()
    if not validate_usdt_bep20_address(addr):
        bot.send_message(
            message.chat.id,
            "⚠️ عنوان BEP-20 غير صالح. يجب أن يبدأ بـ 0x ويكون 42 محرف hex.",
        )
        return
    state = user_state[user_id]
    usdt_amt = Decimal(state["usdt_amount_str"])
    rate = Decimal(state["rate"])
    egp_equiv = usdt_to_egp_cents(usdt_amt, rate)
    state["destination"] = addr
    state["step"] = "awaiting_v2_usdt_confirm"
    bot.send_message(
        message.chat.id,
        "<b>تأكيد طلب السحب</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "طريقة السحب:\nUSDT\n\n"
        "الشبكة:\nBNB Smart Chain (BEP-20)\n\n"
        f"المبلغ:\n{usdt_amt:.6f} USDT\n\n"
        f"القيمة التقريبية:\n{_egp_cents_to_egp(egp_equiv):.2f} EGP\n\n"
        "رسوم السحب:\n0\n\n"
        f"العنوان:\n{addr}\n\n"
        "⚠️ تأكد أن العنوان يدعم شبكة BNB Smart Chain (BEP-20). "
        "لا يمكن استرجاع المبلغ بعد التأكيد.\n\n"
        "هل تريد تأكيد الطلب؟",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد", callback_data="v2_confirm_withdraw")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="back_main")],
        ]),
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "v2_confirm_withdraw"
)
def callback_v2_confirm_withdraw(call):
    user_id = call.from_user.id
    state = user_state.get(user_id)
    if not state or state.get("step") not in (
        "awaiting_v2_vodafone_confirm",
        "awaiting_v2_usdt_confirm",
    ):
        bot.answer_callback_query(call.id, "⚠️ انتهت صلاحية الطلب.", show_alert=True)
        return
    method_code = state.get("method_code")
    destination = state.get("destination")
    if method_code == WITHDRAWAL_METHOD_VODAFONE:
        requested_egp_cents = state.get("requested_egp_cents")
        usdt_amount = None
        network_code = None
    else:
        requested_egp_cents = None
        usdt_amount = Decimal(state.get("usdt_amount_str"))
        network_code = WITHDRAWAL_NETWORK_BEP20

    user_state.pop(user_id, None)
    bot.answer_callback_query(call.id, "⏳ جاري إنشاء الطلب...")

    result = create_v2_withdrawal_request(
        user_id=user_id,
        method_code=method_code,
        destination=destination,
        requested_egp_cents=requested_egp_cents,
        usdt_amount=usdt_amount,
        network_code=network_code,
    )

    if isinstance(result, str):
        bot.send_message(
            call.message.chat.id,
            _v2_error_message(result, user_id),
        )
        return

    # Notify admin
    row = get_v2_withdrawal_request(result)
    try:
        admin_text = "💰 <b>طلب سحب V2 جديد</b>\n" + format_v2_withdrawal_admin_summary(row)
        admin_msg = bot.send_message(ADMIN_ID, admin_text)
        conn_ref = get_connection()
        conn_ref.execute(
            "UPDATE withdrawal_requests SET admin_message_id = ? WHERE id = ?",
            (admin_msg.message_id, result),
        )
        conn_ref.commit()
    except Exception:
        pass  # admin notif is best-effort

    bot.send_message(
        call.message.chat.id,
        "✨ تم استلام طلب السحب الخاص بك بنجاح. "
        "سيتم مراجعته وتحويل المبلغ خلال 24 ساعة كحد أقصى.",
    )


def _v2_error_message(code: str, user_id: int) -> str:
    if code == "cooldown":
        remaining = get_withdrawal_cooldown_remaining(user_id)
        return (
            "لقد استخدمت طلب السحب الخاص بك بالفعل.\n\n"
            f"يمكنك طلب سحب جديد بعد: {format_cooldown_remaining(remaining)}."
        )
    if code == "below_minimum":
        return "⚠️ المبلغ أقل من الحد الأدنى المسموح."
    if code == "insufficient_balance":
        return "❌ رصيدك غير كافٍ لإتمام هذا السحب."
    if code == "destination_invalid":
        return "⚠️ بيانات الوجهة غير صالحة."
    if code == "method_not_supported":
        return "⚠️ طريقة السحب غير مدعومة."
    if code == "rate_unavailable":
        return "⚠️ تعذر الحصول على سعر صرف محدّث. حاول لاحقاً."
    if code == "fraud":
        return "🚫 تم إيقاف طلب السحب مؤقتاً."
    return "⚠️ تعذر إنشاء طلب السحب. حاول لاحقاً."


@bot.callback_query_handler(
    func=lambda c: c.data == "admin_list_v2_withdrawals"
    and is_admin(c.from_user.id)
)
def callback_admin_list_v2_withdrawals(call):
    rows = list_pending_v2_withdrawals()
    if not rows:
        bot.answer_callback_query(call.id, "لا توجد طلبات V2 معلقة.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = "💰 <b>طلبات السحب V2 المعلقة</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    markup = InlineKeyboardMarkup()
    for row in rows[:10]:
        usdt = _micro_to_usdt(int(row["usdt_micro"] or 0))
        text += f"#{row['id']} — {row['method_code']} — {usdt:.4f} USDT\n"
        markup.add(InlineKeyboardButton(
            f"#{row['id']} — {row['method_code']}",
            callback_data=f"admin_view_v2_withdrawal_{row['id']}",
        ))
    markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    bot.edit_message_text(
        text, chat_id=call.message.chat.id,
        message_id=call.message.message_id, reply_markup=markup,
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("admin_view_v2_withdrawal_")
    and is_admin(c.from_user.id)
)
def callback_admin_view_v2_withdrawal(call):
    request_id = int(call.data[len("admin_view_v2_withdrawal_"):])
    row = get_v2_withdrawal_request(request_id)
    if row is None or row["method_code"] not in (
        WITHDRAWAL_METHOD_VODAFONE, WITHDRAWAL_METHOD_USDT
    ):
        bot.answer_callback_query(call.id, "الطلب غير موجود.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    text = format_v2_withdrawal_admin_summary(row)
    markup = InlineKeyboardMarkup()
    if row["status"] == "pending":
        markup.row(
            InlineKeyboardButton("✅ إكمال", callback_data=f"admin_complete_v2_{request_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"admin_reject_v2_{request_id}"),
        )
    markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="admin_list_v2_withdrawals"))
    bot.edit_message_text(
        text, chat_id=call.message.chat.id,
        message_id=call.message.message_id, reply_markup=markup,
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("admin_complete_v2_")
    and is_admin(c.from_user.id)
)
def callback_admin_complete_v2(call):
    request_id = int(call.data[len("admin_complete_v2_"):])
    user_state[call.from_user.id] = {
        "step": "awaiting_v2_tx_ref",
        "request_id": request_id,
    }
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"💰 إكمال طلب #{request_id}\n\n"
        "أرسل مرجع المعاملة (transaction reference) أو أرسل '-' إذا لا يوجد:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_v2_tx_ref"
)
def handle_admin_v2_tx_ref(message):
    admin_id = message.from_user.id
    state = user_state.get(admin_id, {})
    request_id = state.get("request_id")
    ref = (message.text or "").strip()
    if ref == "-":
        ref = None
    user_state.pop(admin_id, None)
    result = complete_v2_withdrawal(request_id, admin_id, ref)
    if result is None:
        bot.send_message(admin_id, "⚠️ تعذر إكمال الطلب.")
        return
    try:
        bot.send_message(
            result["user_id"],
            "🎉 <b>تم تحويل أرباحك بنجاح!</b>\n\n"
            f"تم اعتماد طلب السحب الخاص بك وإرسال المبلغ.",
        )
    except Exception:
        pass
    bot.send_message(
        admin_id,
        f"✅ تم إكمال طلب #{request_id} بنجاح.",
        reply_markup=admin_keyboard(),
    )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("admin_reject_v2_")
    and is_admin(c.from_user.id)
)
def callback_admin_reject_v2(call):
    request_id = int(call.data[len("admin_reject_v2_"):])
    result = reject_v2_withdrawal(request_id, call.from_user.id)
    if result is None:
        bot.answer_callback_query(call.id, "⚠️ تعذر رفض الطلب.", show_alert=True)
        return
    try:
        bot.send_message(
            result["user_id"],
            "ℹ️ <b>تم رفض طلب السحب</b>\n\n"
            "تمت إعادة المبلغ إلى رصيدك.",
        )
    except Exception:
        pass
    bot.answer_callback_query(call.id, f"تم رفض الطلب #{request_id} وإعادة المبلغ.")
    bot.edit_message_text(
        f"✅ تم رفض طلب #{request_id} وإعادة المبلغ للمستخدم.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )



def handle_payment_receipt(message):
    """يحفظ إيصال العميل ويرسله فوراً إلى المشرف."""
    user_id = message.from_user.id
    user = get_user(user_id)
    if user is None:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "يرجى إرسال /start أولاً ثم اختيار «💳 شراء نقاط».",
        )
        return

    file_id = message.photo[-1].file_id
    receipt_id = create_payment_receipt(user_id, file_id)
    username = (
        f"@{html.escape(user['username'])}"
        if user["username"]
        else "بدون معرف مستخدم"
    )
    display_name = html.escape(user["first_name"])
    admin_caption = (
        "📦 <b>طلب أوردر جديد لشراء النقاط</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>من المستخدم:</b> {username}\n"
        f"• <b>الاسم:</b> {display_name}\n"
        f"• <b>المعرف الرقمي:</b> <code>{user_id}</code>\n"
        f"• <b>رقم الإيصال:</b> <code>{receipt_id}</code>\n\n"
        "↩️ قم بالرد على هذه الرسالة لإرسال ردك للعميل."
    )

    try:
        admin_message = bot.send_photo(
            ADMIN_ID,
            file_id,
            caption=admin_caption,
        )
        set_receipt_admin_message(receipt_id, admin_message.message_id)
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "✨ <b>شكراً لك!</b>\n\n"
            "تم استلام إيصال التحويل الخاص بك بنجاح وجاري مراجعته من قبل الإدارة.\n"
            "إذا كنت غير متواجد حالياً، سأقوم بالرد عليك وتفعيل نقاطك قريباً جداً "
            "فور عودتي! 🙋‍♂️",
        )
    except Exception:
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر إرسال الإيصال إلى الإدارة حالياً. "
            "يرجى المحاولة مرة أخرى بعد قليل.",
        )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_topup_user_id"
)
def handle_topup_user_id(message):
    """يستقبل من المشرف المعرف الرقمي للعميل المراد شحنه."""
    admin_id = message.from_user.id
    raw_user_id = (message.text or "").strip()

    try:
        target_user_id = int(raw_user_id)
        if target_user_id <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(
            admin_id,
            "⚠️ المعرف غير صحيح. أرسل User ID رقمي موجب، أو أرسل /admin للإلغاء.",
        )
        return

    target_user = get_user(target_user_id)
    if target_user is None:
        bot.send_message(
            admin_id,
            "❌ هذا المستخدم غير مسجل في قاعدة البيانات.\n"
            "أرسل معرفاً صحيحاً لمستخدم بدأ البوت، أو أرسل /admin للإلغاء.",
        )
        return

    user_state[admin_id] = {
        "step": "awaiting_topup_amount",
        "target_user_id": target_user_id,
    }
    bot.send_message(
        admin_id,
        f"✅ تم العثور على المستخدم <code>{target_user_id}</code>.\n"
        "أرسل الآن مبلغ الشحن بالجنيه أو الدولار، مثل "
        "<code>12.35</code> أو <code>$0.50</code>، أو أرسل /admin للإلغاء.",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_topup_amount"
)
def handle_topup_amount(message):
    """يضيف مبلغاً بالقروش ويرسل إشعاراً للعميل بعد تحقق المدخلات."""
    admin_id = message.from_user.id
    state = user_state.get(admin_id, {})
    raw_amount = (message.text or "").strip()

    amount = parse_currency_input(raw_amount)
    if amount is None:
        bot.send_message(
            admin_id,
            "⚠️ المبلغ غير صحيح. أرسل قيمة موجبة بالجنيه أو الدولار، "
            "أو أرسل /admin للإلغاء.",
        )
        return

    target_user_id = state.get("target_user_id")
    target_user = get_user(target_user_id) if target_user_id else None
    if target_user is None:
        user_state.pop(admin_id, None)
        bot.send_message(admin_id, "❌ لم يعد المستخدم موجوداً. أعد العملية من لوحة الإدارة.")
        return

    add_points(target_user_id, amount)
    updated_user = get_user(target_user_id)
    user_state.pop(admin_id, None)

    notification = (
        "🎉 <b>بشرى سارة!</b>\n\n"
        f"تم شحن حسابك بنجاح بإضافة <b>{format_balance(amount)}</b> إلى رصيدك "
        "من قبل الإدارة!\n"
        "يمكنك الآن الشراء من متجر الخدمات فوراً."
    )
    try:
        bot.send_message(target_user_id, notification)
        client_status = "✅ وتم إرسال إشعار للعميل."
    except Exception:
        client_status = (
            "⚠️ تمت إضافة النقاط، لكن تعذر إرسال الإشعار "
            "لأن العميل ربما حظر البوت."
        )

    bot.send_message(
        admin_id,
        "✅ <b>تم شحن الرصيد بنجاح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>المستخدم:</b> <code>{target_user_id}</code>\n"
        f"➕ <b>المبلغ المضاف:</b> {format_balance(amount)}\n"
        f"🏆 <b>الرصيد الجديد:</b> {balance_text(updated_user)}\n\n"
        f"{client_status}",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_broadcast"
)
def handle_broadcast_input(message):
    """يستقبل نص الإذاعة من المشرف ويرسله لجميع المستخدمين."""
    admin_id = message.from_user.id
    user_state.pop(admin_id, None)

    broadcast_text = message.text or message.caption or ""
    if not broadcast_text.strip():
        bot.send_message(admin_id, "⚠️ الرسالة فارغة. تم الإلغاء.")
        return

    all_ids     = get_all_user_ids()
    total       = len(all_ids)
    success_cnt = 0
    fail_cnt    = 0

    # إشعار المشرف بالبدء
    progress_msg = bot.send_message(
        admin_id,
        f"📡 <b>جاري الإرسال...</b>\n"
        f"المستخدمون: <b>{total}</b>\n"
        f"⏳ الرجاء الانتظار.",
    )

    for uid in all_ids:
        try:
            bot.send_message(uid, broadcast_text)
            success_cnt += 1
        except Exception:
            fail_cnt += 1

    # تقرير نهائي
    report = (
        f"📊 <b>تقرير الإذاعة</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>نجح الإرسال:</b> {success_cnt} مستخدم\n"
        f"❌ <b>فشل الإرسال:</b> {fail_cnt} مستخدم\n"
        f"📨 <b>الإجمالي:</b> {total} مستخدم"
    )
    try:
        bot.edit_message_text(
            report,
            chat_id=admin_id,
            message_id=progress_msg.message_id,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 لوحة التحكم", callback_data="admin_panel"),
            ]]),
        )
    except Exception:
        bot.send_message(admin_id, report)


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_manual_task_title"
)
def handle_manual_task_title(message):
    admin_id = message.from_user.id
    title = (message.text or "").strip()
    if not title:
        bot.send_message(admin_id, "⚠️ العنوان فارغ. أرسل عنوان المهمة أو /admin للإلغاء.")
        return

    state = user_state[admin_id]
    state["step"] = "awaiting_manual_task_target"
    state["title"] = title[:200]
    bot.send_message(
        admin_id,
        "✅ تم حفظ عنوان المهمة.\n"
        "أرسل الآن هدف المهمة: رابطاً أو <code>@username</code> "
        "أو اسم المستخدم/الحساب، أو أرسل /admin للإلغاء.",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_manual_task_target"
)
def handle_manual_task_target(message):
    admin_id = message.from_user.id
    target = (message.text or "").strip()
    if not target or len(target) > 500:
        bot.send_message(
            admin_id,
            "⚠️ الهدف غير صحيح أو طويل جداً. أرسل رابطاً أو "
            "<code>@username</code> أو اسماً صحيحاً.",
        )
        return

    state = user_state[admin_id]
    if state.get("task_type") == "telegram_channel":
        channel = normalize_channel_input(target)
        if not channel:
            bot.send_message(
                admin_id,
                "⚠️ هدف قناة Telegram غير صالح. أرسل <code>@channel</code> "
                "أو رابط قناة عامة مثل <code>https://t.me/channel</code>.",
            )
            return
        target = channel
    state["step"] = "awaiting_manual_task_instructions"
    state["task_link"] = target
    state["target_reference"] = target
    bot.send_message(
        admin_id,
        "✅ تم حفظ الهدف.\n"
        "أرسل الآن شروط المعلن بالتفصيل، مثل: "
        "«تابع الحساب واضغط إعجاباً واترك تعليقاً»، "
        "أو أرسل /admin للإلغاء.",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step")
    == "awaiting_manual_task_instructions"
)
def handle_manual_task_instructions(message):
    admin_id = message.from_user.id
    instructions = (message.text or "").strip()
    if not instructions or len(instructions) > 1000:
        bot.send_message(
            admin_id,
            "⚠️ الشروط فارغة أو طويلة جداً. أرسل شروط المعلن بوضوح، "
            "أو أرسل /admin للإلغاء.",
        )
        return

    state = user_state[admin_id]
    state["step"] = "awaiting_manual_task_reward"
    state["task_instructions"] = instructions
    bot.send_message(
        admin_id,
        "✅ تم حفظ شروط المعلن.\n"
        "أرسل الآن مكافأة المهمة بالنقاط (رقم موجب)، أو /admin للإلغاء.",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_manual_task_reward"
)
def handle_manual_task_reward(message):
    admin_id = message.from_user.id
    reward = parse_currency_input(message.text or "")
    if reward is None:
        bot.send_message(
            admin_id,
            "⚠️ المكافأة غير صحيحة. أرسل مبلغاً موجباً بالجنيه أو الدولار، "
            "أو /admin للإلغاء.",
        )
        return

    state = user_state[admin_id]
    state["step"] = "awaiting_manual_task_quantity"
    state["reward_points"] = reward
    bot.send_message(
        admin_id,
        "✅ تم حفظ المكافأة.\n"
        "أرسل الآن عدد مرات تنفيذ المهمة (رقم موجب)، أو /admin للإلغاء.",
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_manual_task_quantity"
)
def handle_manual_task_quantity(message):
    admin_id = message.from_user.id
    try:
        quantity = int((message.text or "").strip())
        if quantity <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(
            admin_id,
            "⚠️ الكمية غير صحيحة. أرسل رقماً صحيحاً موجباً، أو /admin للإلغاء.",
        )
        return

    state = user_state.get(admin_id, {})
    task_id = create_manual_task(
        title=state["title"],
        task_link=state["task_link"],
        reward_points=state["reward_points"],
        quantity=quantity,
        task_type=state.get("task_type", "social_manual"),
        target_reference=state.get("target_reference", state["task_link"]),
        task_instructions=state.get("task_instructions", ""),
    )
    user_state.pop(admin_id, None)
    bot.send_message(
        admin_id,
        "✅ <b>تمت إضافة المهمة بنجاح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 رقم المهمة: <code>{task_id}</code>\n"
        f"📝 العنوان: <b>{html.escape(state['title'])}</b>\n"
        f"⚙️ النوع: <b>{'انضمام قناة Telegram — تحقق آلي' if state.get('task_type') == 'telegram_channel' else 'مهمة تواصل اجتماعي — مراجعة يدوية'}</b>\n"
        f"🎯 الهدف: <code>{html.escape(state.get('target_reference', state['task_link']))}</code>\n"
        f"📋 الشروط: {html.escape(state.get('task_instructions', ''))}\n"
        f"🎁 المكافأة: <b>{format_balance(state['reward_points'])}</b>\n"
        f"📊 الكمية: <b>{quantity}</b> تنفيذ\n\n"
        "ستظهر المهمة الآن في «المهام اليومية».",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_service_price"
)
def handle_service_price_input(message):
    """يحفظ سعر الخدمة بالقروش بعد قبول الجنيه أو الدولار."""
    admin_id = message.from_user.id
    state = user_state.get(admin_id, {})
    service_key = state.get("service_key")
    service = SERVICE_INDEX.get(service_key)
    price = parse_currency_input(message.text or "")

    if service is None:
        user_state.pop(admin_id, None)
        bot.send_message(
            admin_id,
            "⚠️ انتهت صلاحية إعداد الخدمة. افتح أسعار الخدمات من لوحة الأدمن مرة أخرى.",
            reply_markup=admin_keyboard(),
        )
        return
    if price is None or price < ABSOLUTE_ORDER_MIN_POINTS:
        bot.send_message(
            admin_id,
            "⚠️ أرسل سعراً صحيحاً أكبر من صفر.",
        )
        return

    set_service_price(service_key, price)
    user_state.pop(admin_id, None)
    selling = calculate_selling_price(price)
    bot.send_message(
        admin_id,
        "✅ <b>تم تحديث سعر الخدمة بنجاح</b>\n\n"
        f"الخدمة: <b>{html.escape(service_display_name(service_key, service))}</b>\n"
        f"📌 التكلفة الأساسية: <b>{format_balance(price)}</b>\n"
        f"💰 سعر البيع للعميل: <b>{format_balance(selling)}</b>\n"
        f"📊 هامش المنصة: <b>{int((MARGIN_MULTIPLIER - 1) * 100)}%</b>",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step")
    == "awaiting_promotion_package_details"
)
def handle_promotion_package_details(message):
    """يستقبل بيانات الباقة ويضيفها أو يحدّثها من لوحة الأدمن."""
    admin_id = message.from_user.id
    raw_parts = [part.strip() for part in (message.text or "").split("|")]
    if len(raw_parts) != 3:
        bot.send_message(
            admin_id,
            "⚠️ الصيغة غير صحيحة.\n"
            "استخدم: <code>اسم الباقة | عدد المشتركين | السعر</code>",
        )
        return

    label, raw_target, raw_price = raw_parts
    try:
        target_subscribers = int(raw_target.replace(",", ""))
    except (TypeError, ValueError):
        target_subscribers = 0
    points_cost = parse_currency_input(raw_price)
    if (
        not label
        or len(label) > 100
        or target_subscribers < 1
        or points_cost is None
        or points_cost < 1
    ):
        bot.send_message(
            admin_id,
            "⚠️ البيانات غير صحيحة.\n"
            "تأكد أن الاسم غير فارغ، وعدد المشتركين والسعر أكبر من صفر.",
        )
        return

    state = user_state.get(admin_id, {})
    package_key = state.get("package_key")
    if state.get("promotion_package_mode") == "add":
        package_key = next_promotion_package_key(
            label, target_subscribers, points_cost
        )
    if not package_key or not save_promotion_package(
        package_key, label, target_subscribers, points_cost
    ):
        bot.send_message(
            admin_id,
            "❌ تعذر حفظ الباقة. حاول مرة أخرى من لوحة الإدارة.",
            reply_markup=admin_keyboard(),
        )
        user_state.pop(admin_id, None)
        return

    user_state.pop(admin_id, None)
    bot.send_message(
        admin_id,
        "✅ <b>تم حفظ باقة الترويج بنجاح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📣 الباقة: <b>{html.escape(label)}</b>\n"
        f"🎯 الهدف: <b>{target_subscribers} مشترك جديد</b>\n"
        f"💰 السعر: <b>{format_balance(points_cost)}</b>\n\n"
        "ستظهر الباقة الآن للمستخدمين في قسم «تثبيت إعلان / روّج لقناتك».",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_ad_reward"
)
def handle_ad_reward_input(message):
    """يحفظ مكافأة مشاهدة الإعلان التي يحددها المشرف."""
    admin_id = message.from_user.id
    reward = parse_currency_input(message.text or "")
    if reward is None or reward < ABSOLUTE_ORDER_MIN_POINTS:
        bot.send_message(
            admin_id,
            "⚠️ أرسل مكافأة صحيحة أكبر من صفر، مثل 0.50 أو $0.01.",
        )
        return

    set_ad_reward(reward)
    user_state.pop(admin_id, None)
    bot.send_message(
        admin_id,
        "✅ <b>تم تحديث مكافأة مشاهدة الإعلان</b>\n\n"
        f"المكافأة الجديدة: <b>{format_balance(reward)}</b>\n"
        "سيتم تطبيقها على إثباتات الإعلانات الجديدة فقط.",
        reply_markup=admin_keyboard(),
    )




@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_min_withdrawal"
)
def handle_min_withdrawal_input(message):
    """يحفظ الحد الأدنى للسحب الجديد الذي يحدده المشرف."""
    admin_id = message.from_user.id
    amount = parse_currency_input(message.text or "")
    if amount is None or amount < 1:
        bot.send_message(
            admin_id,
            "⚠️ أرسل رقم صحيح أكبر من صفر، مثل 10 أو 25 أو 50.",
        )
        return

    set_min_withdrawal(amount)
    user_state.pop(admin_id, None)
    bot.send_message(
        admin_id,
        "✅ <b>تم تحديث الحد الأدنى للسحب</b>\n\n"
        f"الحد الأدنى الجديد: <b>{format_balance(amount)}</b>\n\n"
        "سيتم تطبيقه فوراً على جميع طلبات السحب الجديدة.",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step")
    == "awaiting_watch_ad_link"
)
def handle_watch_ad_link_input(message):
    """يحفظ رابط مشاهدة إعلاني جديداً من لوحة الأدمن."""
    admin_id = message.from_user.id
    parts = [part.strip() for part in (message.text or "").split("|", 1)]
    if len(parts) == 1:
        title = "إعلان مشاهدة"
        url = parts[0]
    else:
        title, url = parts
        title = title or "إعلان مشاهدة"

    ad = add_watch_ad_link(url, title)
    if ad is None:
        bot.send_message(
            admin_id,
            "⚠️ الرابط غير صالح أو مضاف من قبل.\n"
            "أرسل رابطاً يبدأ بـ <code>https://</code>، "
            "أو أرسل /admin للإلغاء.",
        )
        return

    user_state.pop(admin_id, None)
    bot.send_message(
        admin_id,
        "✅ <b>تمت إضافة إعلان المشاهدة بنجاح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 العنوان: <b>{html.escape(ad['title'])}</b>\n"
        f"🔗 الرابط: <code>{html.escape(ad['url'])}</code>\n"
        f"💰 المكافأة الحالية: <b>{format_balance(get_ad_reward())}</b>",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(
    func=lambda m: is_admin(m.from_user.id)
    and user_state.get(m.from_user.id, {}).get("step") == "awaiting_service_quantity"
)
def handle_service_quantity_input(message):
    """يحفظ كمية الخدمة التي اختارها المشرف."""
    admin_id = message.from_user.id
    state = user_state.get(admin_id, {})
    service_key = state.get("service_key")
    service = SERVICE_INDEX.get(service_key)
    try:
        quantity = int((message.text or "").strip())
    except (TypeError, ValueError):
        quantity = 0

    if service is None:
        user_state.pop(admin_id, None)
        bot.send_message(
            admin_id,
            "⚠️ انتهت صلاحية إعداد الخدمة. افتح أسعار وكميات الخدمات "
            "من لوحة الأدمن مرة أخرى.",
            reply_markup=admin_keyboard(),
        )
        return
    if quantity < 1:
        bot.send_message(
            admin_id,
            "⚠️ أرسل كمية صحيحة أكبر من صفر.",
        )
        return

    set_service_quantity(service_key, quantity)
    user_state.pop(admin_id, None)
    bot.send_message(
        admin_id,
        "✅ <b>تم تحديث كمية الخدمة بنجاح</b>\n\n"
        f"الخدمة: <b>{html.escape(service_display_name(service_key, service))}</b>\n"
        f"الكمية الجديدة: <b>{quantity} وحدة</b>",
        reply_markup=admin_keyboard(),
    )


@bot.message_handler(func=lambda m: m.from_user.id in user_state
                     and user_state[m.from_user.id].get("step") == "awaiting_link")
def handle_link_input(message):
    user_id     = message.from_user.id
    state       = user_state[user_id]
    service_key = state["service_key"]
    svc         = SERVICE_INDEX[service_key]
    link        = (message.text or "").strip()

    if not is_valid_service_link(link, svc):
        bot.send_message(
            message.chat.id,
            "⚠️ يبدو أن الرابط غير صحيح لهذه الخدمة.\n"
            "أرسل رابط الحساب أو الصفحة أو المنشور/الفيديو بالشكل:\n"
            "<code>https://example.com/your-profile-or-post</code>",
        )
        return

    row = get_user(user_id)
    price = get_service_price(service_key)
    if row is None or row_balance_cents(row) < price:
        bot.send_message(
            message.chat.id,
            service_price_message(
                service_key,
                row_balance_cents(row) if row else 0,
            ),
        )
        user_state.pop(user_id, None)
        return

    # حفظ الرابط وانتقل لمرحلة التأكيد
    user_state[user_id]["step"] = "awaiting_confirm"
    user_state[user_id]["link"] = link

    confirm_text = (
        f"🔍 <b>تأكيد الطلب</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{svc['emoji']} <b>الخدمة:</b> {service_display_name(service_key, svc)}\n"
        f"🔢 <b>الكمية:</b> {svc['quantity']}\n"
        f"🔗 <b>الرابط:</b> <code>{link}</code>\n"
        f"💰 <b>التكلفة:</b> {format_balance(get_service_price(service_key))}\n"
        f"💼 <b>رصيدك:</b> {balance_text(row)}\n\n"
        f"هل تريد تأكيد الطلب؟"
    )
    bot.send_message(message.chat.id, confirm_text,
                     reply_markup=confirm_keyboard(service_key))


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_referral_link"
)
def handle_referral_link_input(message):
    """يستقبل رابط بوت تيليجرام خارجي يحتوي على معامل start."""
    user_id = message.from_user.id
    link = (message.text or "").strip()

    if not parse_referral_link(link):
        bot.send_message(
            message.chat.id,
            "⚠️ رابط الإحالة غير صحيح.\n\n"
            "أرسل رابط بوت تيليجرام يحتوي على <code>?start=</code>، مثل:\n"
            "<code>https://t.me/ExampleBot?start=abc123</code>",
        )
        return

    user = get_user(user_id)
    price = get_service_price(REFERRAL_SERVICE_KEY)
    if user is None or row_balance_cents(user) < price:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            service_price_message(
                REFERRAL_SERVICE_KEY,
                row_balance_cents(user) if user else 0,
            ),
            reply_markup=shop_keyboard(),
        )
        return

    task_id = create_referral_task(user_id, link)
    if task_id is None:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            f"❌ رصيدك غير كافٍ لإنشاء الطلب.\n"
            f"تحتاج إلى <b>{format_balance(price)}</b> لشراء "
            f"{get_service_quantity(REFERRAL_SERVICE_KEY)} إحالة.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 شراء نقاط", callback_data="buy_points"),
                InlineKeyboardButton("🛒 المتجر", callback_data="shop"),
            ]]),
        )
        return

    user_state.pop(user_id, None)
    updated = get_user(user_id)
    bot.send_message(
        message.chat.id,
        "✅ <b>تم إنشاء طلب رشق الإحالات بنجاح!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>الخدمة:</b> رشق إحالات لبوت آخر\n"
        f"🔢 <b>الكمية:</b> {get_service_quantity(REFERRAL_SERVICE_KEY)} إحالة\n"
        f"🔗 <b>الرابط:</b> <code>{html.escape(link)}</code>\n"
        f"🆔 <b>رقم الطلب:</b> <code>{task_id}</code>\n"
        f"💰 <b>التكلفة:</b> {format_balance(price)}\n"
        f"💼 <b>رصيدك المتبقي:</b> {balance_text(updated)}\n\n"
        "سيظهر الرابط الآن للمستخدمين الآخرين ضمن «المهام اليومية».",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 المهام اليومية", callback_data="daily_tasks")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
        ]),
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_promoted_channel"
)
def handle_promoted_channel_input(message):
    """يستقبل معرف قناة المعلن ويفعّل الحملة بعد فحص القناة والرصيد."""
    user_id = message.from_user.id
    state = user_state.get(user_id, {})
    package_key = state.get("package_key")
    package = PROMOTION_PACKAGES.get(package_key)
    channel_username = normalize_channel_input(message.text or "")

    if package is None:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⚠️ انتهت صلاحية طلب الإعلان. افتح «تثبيت إعلان» من جديد.",
            reply_markup=main_keyboard(),
        )
        return

    if channel_username is None:
        bot.send_message(
            message.chat.id,
            "⚠️ معرف القناة غير صحيح.\n\n"
            "أرسل معرف قناة عامة مثل <code>@my_channel</code> "
            "أو رابطاً مثل <code>https://t.me/my_channel</code>.",
        )
        return

    validation = validate_promoted_channel(channel_username, user_id)
    if not validation["ok"]:
        bot.send_message(message.chat.id, f"⚠️ {validation['error']}")
        return

    user = get_user(user_id)
    if user is None:
        user_state.pop(user_id, None)
        bot.send_message(message.chat.id, "يرجى إرسال /start أولاً.")
        return
    if row_balance_cents(user) < PROMOTION_MIN_CENTS:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "❌ الحد الأدنى للسماح بإنشاء إعلان هو "
            f"<b>{format_balance(PROMOTION_MIN_CENTS)}</b>.\n\n"
            f"رصيدك الحالي: <b>{balance_text(user)}</b>.",
            reply_markup=main_keyboard(),
        )
        return
    if row_balance_cents(user) < package["points_cost"]:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "❌ رصيدك غير كافٍ لتفعيل الإعلان.\n\n"
            f"التكلفة: <b>{format_balance(package['points_cost'])}</b>\n"
            f"رصيدك الحالي: <b>{balance_text(user)}</b>.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 شراء نقاط", callback_data="buy_points")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
            ]),
        )
        return

    campaign = create_promoted_channel_campaign(
        user_id, validation, package_key
    )
    if campaign is None:
        user_state.pop(user_id, None)
        active_campaign = get_active_promoted_campaign(channel_username)
        if active_campaign is not None:
            error = "هذه القناة لديها إعلان نشط بالفعل."
        else:
            error = "تعذر خصم التكلفة. ربما تغيّر رصيدك قبل التأكيد."
        bot.send_message(
            message.chat.id,
            f"❌ {error}",
            reply_markup=main_keyboard(),
        )
        return

    user_state.pop(user_id, None)
    refresh_required_channels()
    updated = get_user(user_id)
    bot.send_message(
        message.chat.id,
        "🎉 <b>تم تفعيل إعلانك بنجاح!</b>\n\n"
        "قناتك الآن مضافة كقناة تفعيل إجبارية لجميع مستخدمي البوت.\n\n"
        f"📣 القناة: <b>{html.escape(campaign['channel_title'])}</b>\n"
        f"🎯 الباقة: <b>{package['label']}</b>\n"
        f"💰 المبلغ المخصوم: <b>{format_balance(campaign['amount_cents'] or campaign['points_cost'])}</b>\n"
        f"💼 رصيدك المتبقي: <b>{balance_text(updated)}</b>\n\n"
        "سيتوقف الإعلان وتُحذف القناة تلقائياً عند وصولها إلى العدد المدفوع "
        "من المشتركين الجدد.",
        reply_markup=main_keyboard(),
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_user_ad_title"
)
def handle_user_ad_title(message):
    user_id = message.from_user.id
    title = (message.text or "").strip()
    if not title or len(title) > 200:
        bot.send_message(
            message.chat.id,
            "⚠️ أرسل عنواناً واضحاً للإعلان (بحد أقصى 200 حرف).",
        )
        return

    state = user_state[user_id]
    state["title"] = title
    state["step"] = "awaiting_user_ad_description"
    bot.send_message(
        message.chat.id,
        "✅ تم حفظ عنوان الإعلان.\n\n"
        "أرسل الآن وصف الإعلان بالتفصيل (بحد أقصى 1500 حرف).",
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_user_ad_description"
)
def handle_user_ad_description(message):
    user_id = message.from_user.id
    description = (message.text or "").strip()
    if not description or len(description) > 1500:
        bot.send_message(
            message.chat.id,
            "⚠️ أرسل وصفاً واضحاً للإعلان (بحد أقصى 1500 حرف).",
        )
        return

    state = user_state[user_id]
    state["description"] = description
    state["step"] = "awaiting_user_ad_link"
    bot.send_message(
        message.chat.id,
        "✅ تم حفظ الوصف.\n\n"
        "أرسل الآن رابط الإعلان أو وسيلة التواصل، مثل:\n"
        "<code>https://t.me/your_channel</code>",
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_user_ad_link"
)
def handle_user_ad_link(message):
    user_id = message.from_user.id
    link = (message.text or "").strip()
    parsed = urlsplit(link)
    if (
        not link
        or len(link) > 1000
        or parsed.scheme not in ("http", "https")
        or not parsed.netloc
    ):
        bot.send_message(
            message.chat.id,
            "⚠️ الرابط غير صحيح. أرسل رابطاً يبدأ بـ "
            "<code>https://</code> أو <code>http://</code>.",
        )
        return

    state = user_state[user_id]
    state["link"] = link
    state["step"] = "awaiting_user_ad_price"
    bot.send_message(
        message.chat.id,
        "✅ تم حفظ الرابط.\n\n"
        "أرسل الآن سعر الإعلان بالجنيه أو الدولار، مثل:\n"
        "• <code>150</code>\n"
        "• <code>$3</code>\n"
        "ويمكنك إرسال <code>مجاني</code> إذا كان الإعلان بلا سعر.",
    )


@bot.message_handler(
    func=lambda m: m.from_user.id in user_state
    and user_state[m.from_user.id].get("step") == "awaiting_user_ad_price"
)
def handle_user_ad_price(message):
    user_id = message.from_user.id
    raw_price = (message.text or "").strip()
    price = (
        0
        if raw_price.lower() in {"مجاني", "free"}
        else parse_currency_input(raw_price)
    )
    if price is None or price < 0:
        bot.send_message(
            message.chat.id,
            "⚠️ السعر غير صحيح. أرسل مبلغاً موجباً أو كلمة "
            "<code>مجاني</code>.",
        )
        return

    state = user_state.get(user_id, {})
    title = state.get("title")
    description = state.get("description")
    link = state.get("link")
    if not title or not description or not link:
        user_state.pop(user_id, None)
        bot.send_message(
            message.chat.id,
            "⚠️ انتهت صلاحية نموذج الإعلان. ابدأ من القائمة الرئيسية مرة أخرى.",
            reply_markup=main_keyboard(),
        )
        return

    state["price_cents"] = price
    state["step"] = "awaiting_user_ad_publish"
    bot.send_message(
        message.chat.id,
        "🔎 <b>مراجعة الإعلان قبل النشر</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>العنوان:</b> {html.escape(title)}\n"
        f"📝 <b>الوصف:</b>\n{html.escape(description)}\n\n"
        f"🔗 <b>الرابط:</b> <code>{html.escape(link)}</code>\n"
        f"💰 <b>السعر:</b> "
        f"{'مجاني' if price == 0 else format_balance(price)}\n\n"
        "إذا كانت البيانات صحيحة اضغط «🚀 نشر الإعلان».",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 نشر الإعلان", callback_data="publish_user_ad")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="back_main")],
        ]),
    )


# ══════════════════════════════════════════════════════════════════════════════
# ─── Callbacks: لوحة الإدارة ──────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "publish_user_ad")
def callback_publish_user_ad(call):
    if not require_active_account(call):
        return
    user_id = call.from_user.id
    state = user_state.get(user_id, {})
    if state.get("step") != "awaiting_user_ad_publish":
        bot.answer_callback_query(
            call.id,
            "⚠️ انتهت صلاحية نموذج الإعلان. ابدأ من جديد.",
            show_alert=True,
        )
        user_state.pop(user_id, None)
        return

    title = state.get("title")
    description = state.get("description")
    link = state.get("link")
    price = state.get("price_cents")
    if (
        not title
        or not description
        or not link
        or not isinstance(price, int)
        or price < 0
    ):
        user_state.pop(user_id, None)
        bot.answer_callback_query(
            call.id,
            "⚠️ بيانات الإعلان غير مكتملة. ابدأ العملية من جديد.",
            show_alert=True,
        )
        return

    ad_id = create_user_ad(user_id, title, description, link, price)
    if ad_id is None:
        user_state.pop(user_id, None)
        bot.answer_callback_query(
            call.id,
            "⏳ لديك إعلان آخر قيد المراجعة بالفعل.",
            show_alert=True,
        )
        return

    user = get_user(user_id)
    username = (
        f"@{html.escape(user['username'])}"
        if user is not None and user["username"]
        else "بدون معرف مستخدم"
    )
    display_name = html.escape(
        " ".join(
            filter(
                None,
                [
                    user["first_name"] if user is not None else "",
                    user["last_name"] if user is not None else "",
                ],
            )
        )
    )
    admin_text = (
        "📢 <b>إعلان يدوي جديد بانتظار المراجعة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>رقم الإعلان:</b> <code>{ad_id}</code>\n"
        f"👤 <b>صاحب الإعلان:</b> {display_name}\n"
        f"🔗 <b>حساب Telegram:</b> {username}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n\n"
        f"📌 <b>العنوان:</b> {html.escape(title)}\n"
        f"📝 <b>الوصف:</b>\n{html.escape(description)}\n\n"
        f"🔗 <b>الرابط:</b> <code>{html.escape(link)}</code>\n"
        f"💰 <b>السعر:</b> "
        f"{'مجاني' if price == 0 else format_balance(price)}\n\n"
        "اختر قرار المراجعة من الأزرار أدناه."
    )

    try:
        admin_message = bot.send_message(
            ADMIN_ID,
            admin_text,
            reply_markup=user_ad_review_keyboard(ad_id),
        )
        set_user_ad_admin_message(ad_id, admin_message.message_id)
        user_state.pop(user_id, None)
        bot.answer_callback_query(call.id, "⏳ تم إرسال الإعلان للمراجعة.")
        bot.edit_message_text(
            "⏳ <b>تم إرسال إعلانك، وهو الآن قيد مراجعة الإدارة.</b>\n\n"
            "لن يظهر الإعلان للمستخدمين إلا بعد موافقة الأدمن.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_keyboard(),
        )
    except Exception:
        delete_pending_user_ad(ad_id)
        user_state.pop(user_id, None)
        bot.answer_callback_query(
            call.id,
            "⚠️ تعذر إرسال الإعلان للإدارة حالياً.",
            show_alert=True,
        )
        bot.edit_message_text(
            "⚠️ <b>تعذر إرسال الإعلان إلى الإدارة حالياً.</b>\n\n"
            "لم يتم نشره، حاول مرة أخرى لاحقاً.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_keyboard(),
        )


@bot.callback_query_handler(func=lambda call: call.data == "buy_points")
def callback_buy_points(call):
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(
            call.id,
            "يرجى إرسال /start أولاً.",
            show_alert=True,
        )
        return
    if not require_active_account(call):
        return

    user_state[user_id] = {"step": "awaiting_receipt"}
    payment_text = (
        "💳 <b>شراء نقاط</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 <b>طرق الدفع المتوفرة لشحن الرصيد:</b>\n"
        "• <b>فودافون كاش (داخل مصر):</b> <code>01062275398</code>\n"
        "• <b>العملات الرقمية (شبكة بايننس):</b>\n"
        "<code>0x295ecbbb578ab56a4b5b7328db9f8c1cd1cd2224</code>\n\n"
        "📝 بعد تحويل المبلغ، يرجى إرسال <b>صورة إيصال التحويل</b> "
        "هنا في البوت مباشرة لتفعيل نقاطك فوراً.\n\n"
        "أرسل لقطة الشاشة الآن، أو اضغط «رجوع» للإلغاء."
    )
    bot.edit_message_text(
        payment_text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=payment_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "withdraw_earnings")
def callback_withdraw_earnings(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    if user is None:
        bot.answer_callback_query(
            call.id,
            "يرجى إرسال /start أولاً.",
            show_alert=True,
        )
        return
    if not require_active_account(call):
        return

    user_state.pop(user_id, None)
    referral_check = run_referral_withdrawal_double_check(user_id)
    if referral_check["blocked"]:
        bot.answer_callback_query(
            call.id,
            "🚫 تم منع السحب بسبب إحالة غير صالحة.",
            show_alert=True,
        )
        return
    if referral_check["unknown"]:
        bot.answer_callback_query(
            call.id,
            "⚠️ تعذر التحقق من الإحالات حالياً. حاول لاحقاً.",
            show_alert=True,
        )
        return

    user = get_user(user_id)
    if has_recent_withdrawal(user_id):
        bot.answer_callback_query(
            call.id,
            WITHDRAWAL_COOLDOWN_MESSAGE,
            show_alert=True,
        )
        return

    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"💰 <b>سحب الأرباح</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"رصيدك الحالي: <b>{balance_text(user)}</b>\n"
        "اختر طريقة السحب:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=withdrawal_method_keyboard(),
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("withdraw_method_")
)
def callback_withdrawal_method(call):
    """Legacy entry point — refused. V2 is the only customer-facing flow."""
    bot.answer_callback_query(
        call.id,
        "⚠️ تم تحديث نظام السحب. استخدم القائمة الجديدة للسحب.",
        show_alert=True,
    )


@bot.callback_query_handler(func=lambda call: call.data == "admin_panel"
                             and is_admin(call.from_user.id))
def callback_admin_panel(call):
    user_state.pop(call.from_user.id, None)
    bot.edit_message_text(
        "🔐 <b>لوحة تحكم المشرف</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر أحد الخيارات:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=admin_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_stats"
                             and is_admin(call.from_user.id))
def callback_admin_stats(call):
    st = get_stats()
    text = (
        "📊 <b>إحصائيات البوت</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>إجمالي المستخدمين:</b> {st['users']}\n"
        f"🆕 <b>انضموا اليوم:</b> {st['today']}\n"
        f"👫 <b>إجمالي الإحالات:</b> {st['referrals']}\n"
        f"📦 <b>طلبات الرشق المنجزة:</b> {st['orders']}\n"
        f"💰 <b>إجمالي الأرصدة:</b> {format_balance(st['balance_cents'])}"
    )
    back_markup = InlineKeyboardMarkup()
    back_markup.add(InlineKeyboardButton("🔙 لوحة التحكم", callback_data="admin_panel"))
    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=back_markup,
    )
    bot.answer_callback_query(call.id)


def admin_promotion_packages_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    packages = get_all_promotion_packages()
    for package in packages:
        status = "✅" if package["active"] else "⏸️"
        markup.add(InlineKeyboardButton(
            f"{status} {package['label']} — {format_balance(package['points_cost'])}",
            callback_data=f"admin_promotion_edit_{package['package_key']}",
        ))
    markup.add(InlineKeyboardButton(
        "➕ إضافة باقة جديدة",
        callback_data="admin_promotion_add",
    ))
    markup.add(InlineKeyboardButton(
        "🔙 لوحة التحكم",
        callback_data="admin_panel",
    ))
    return markup


def admin_promotion_packages_text() -> str:
    packages = get_all_promotion_packages()
    if not packages:
        return (
            "📣 <b>إدارة باقات ترويج القنوات</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "لا توجد باقات حالياً. أضف باقة جديدة لتظهر للمستخدمين."
        )
    lines = [
        "📣 <b>إدارة باقات ترويج القنوات</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "اختر باقة لتعديل بياناتها أو إخفائها من قائمة المستخدمين:",
        "",
    ]
    for package in packages:
        status = "متاحة للمستخدمين ✅" if package["active"] else "مخفية ⏸️"
        lines.append(
            f"• <b>{html.escape(package['label'])}</b>\n"
            f"  🎯 {package['target_subscribers']} مشترك — "
            f"💰 {format_balance(package['points_cost'])} — {status}"
        )
    return "\n".join(lines)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_promotion_packages"
    and is_admin(call.from_user.id)
)
def callback_admin_promotion_packages(call):
    refresh_promotion_packages()
    bot.edit_message_text(
        admin_promotion_packages_text(),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=admin_promotion_packages_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_promotion_add"
    and is_admin(call.from_user.id)
)
def callback_admin_promotion_add(call):
    user_state[call.from_user.id] = {
        "step": "awaiting_promotion_package_details",
        "promotion_package_mode": "add",
    }
    bot.edit_message_text(
        "➕ <b>إضافة باقة ترويج جديدة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل بيانات الباقة في رسالة واحدة بهذا الشكل:\n"
        "<code>اسم الباقة | عدد المشتركين | السعر</code>\n\n"
        "مثال:\n"
        "<code>5000 مشترك جديد | 5000 | 1000 جنيه</code>\n\n"
        "يمكن كتابة السعر بالدولار أيضاً مثل <code>$20</code>.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="admin_promotion_packages",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_promotion_edit_")
    and is_admin(call.from_user.id)
)
def callback_admin_promotion_edit(call):
    package_key = call.data[len("admin_promotion_edit_"):]
    package = next(
        (
            item for item in get_all_promotion_packages()
            if item["package_key"] == package_key
        ),
        None,
    )
    if package is None:
        bot.answer_callback_query(call.id, "الباقة غير موجودة.", show_alert=True)
        return

    status = "متاحة للمستخدمين ✅" if package["active"] else "مخفية ⏸️"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        "✏️ تعديل الاسم والعدد والسعر",
        callback_data=f"admin_promotion_update_{package_key}",
    ))
    markup.add(InlineKeyboardButton(
        "⏸️ إخفاء الباقة" if package["active"] else "▶️ إظهار الباقة",
        callback_data=f"admin_promotion_toggle_{package_key}",
    ))
    markup.add(InlineKeyboardButton(
        "🔙 كل الباقات",
        callback_data="admin_promotion_packages",
    ))
    bot.edit_message_text(
        "⚙️ <b>إعدادات باقة الترويج</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"الاسم: <b>{html.escape(package['label'])}</b>\n"
        f"الهدف: <b>{package['target_subscribers']} مشترك جديد</b>\n"
        f"السعر: <b>{format_balance(package['points_cost'])}</b>\n"
        f"الحالة: <b>{status}</b>\n\n"
        "يمكنك تعديل الباقة أو إخفاؤها من قائمة المستخدمين.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_promotion_update_")
    and is_admin(call.from_user.id)
)
def callback_admin_promotion_update(call):
    package_key = call.data[len("admin_promotion_update_"):]
    package = next(
        (
            item for item in get_all_promotion_packages()
            if item["package_key"] == package_key
        ),
        None,
    )
    if package is None:
        bot.answer_callback_query(call.id, "الباقة غير موجودة.", show_alert=True)
        return
    user_state[call.from_user.id] = {
        "step": "awaiting_promotion_package_details",
        "promotion_package_mode": "edit",
        "package_key": package_key,
    }
    bot.edit_message_text(
        "✏️ <b>تعديل باقة الترويج</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل البيانات الجديدة في رسالة واحدة:\n"
        "<code>اسم الباقة | عدد المشتركين | السعر</code>\n\n"
        f"البيانات الحالية:\n"
        f"<b>{html.escape(package['label'])} | "
        f"{package['target_subscribers']} | "
        f"{format_balance(package['points_cost'])}</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data=f"admin_promotion_edit_{package_key}",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_promotion_toggle_")
    and is_admin(call.from_user.id)
)
def callback_admin_promotion_toggle(call):
    package_key = call.data[len("admin_promotion_toggle_"):]
    package = next(
        (
            item for item in get_all_promotion_packages()
            if item["package_key"] == package_key
        ),
        None,
    )
    if package is None:
        bot.answer_callback_query(call.id, "الباقة غير موجودة.", show_alert=True)
        return
    next_active = not bool(package["active"])
    set_promotion_package_active(package_key, next_active)
    bot.answer_callback_query(
        call.id,
        "✅ تم إظهار الباقة." if next_active else "⏸️ تم إخفاء الباقة.",
    )
    callback_admin_promotion_packages(call)


def admin_watch_ads_keyboard() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for ad in get_watch_ad_links(active_only=False):
        status = "✅" if ad["active"] else "⏸️"
        markup.add(InlineKeyboardButton(
            f"{status} {ad['title'][:45]}",
            callback_data=f"admin_watch_ad_toggle_{ad['id']}",
        ))
    markup.add(InlineKeyboardButton(
        "💰 تعديل مكافأة المشاهدة",
        callback_data="admin_watch_ad_reward",
    ))
    markup.add(InlineKeyboardButton(
        "➕ إضافة رابط إعلان",
        callback_data="admin_watch_ad_add",
    ))
    markup.add(InlineKeyboardButton(
        "🔙 لوحة التحكم",
        callback_data="admin_panel",
    ))
    return markup


def admin_watch_ads_text() -> str:
    ads = get_watch_ad_links(active_only=False)
    lines = [
        "📺 <b>إدارة إعلانات المشاهدة</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"💰 مكافأة المشاهدة الحالية: <b>{format_balance(get_ad_reward())}</b>",
        "",
        "اضغط على الإعلان لتغيير ظهوره للمستخدمين:",
        "",
    ]
    if not ads:
        lines.append("<i>لا توجد روابط إعلانية مضافة.</i>")
    else:
        for ad in ads:
            status = "ظاهر للمستخدمين ✅" if ad["active"] else "مخفي ⏸️"
            lines.append(
                f"• <b>{html.escape(ad['title'])}</b>\n"
                f"  <code>{html.escape(ad['url'])}</code>\n"
                f"  الحالة: {status}"
            )
    return "\n".join(lines)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_watch_ads"
    and is_admin(call.from_user.id)
)
def callback_admin_watch_ads(call):
    bot.edit_message_text(
        admin_watch_ads_text(),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=admin_watch_ads_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_watch_ad_reward"
    and is_admin(call.from_user.id)
)
def callback_admin_watch_ad_reward(call):
    admin_id = call.from_user.id
    user_state[admin_id] = {"step": "awaiting_ad_reward"}
    bot.edit_message_text(
        "💰 <b>تعديل مكافأة مشاهدة الإعلان</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"المكافأة الحالية: <b>{format_balance(get_ad_reward())}</b>\n\n"
        "أرسل المكافأة الجديدة بالجنيه أو الدولار، مثل:\n"
        "• <code>0.50</code>\n"
        "• <code>$0.01</code>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="admin_watch_ads",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_watch_ad_add"
    and is_admin(call.from_user.id)
)
def callback_admin_watch_ad_add(call):
    user_state[call.from_user.id] = {"step": "awaiting_watch_ad_link"}
    bot.edit_message_text(
        "➕ <b>إضافة إعلان مشاهدة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل رابط الإعلان فقط، أو أرسل العنوان والرابط بهذا الشكل:\n"
        "<code>عنوان الإعلان | https://example.com/video</code>\n\n"
        "يجب أن يبدأ الرابط بـ <code>http://</code> أو <code>https://</code>.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="admin_watch_ads",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_watch_ad_toggle_")
    and is_admin(call.from_user.id)
)
def callback_admin_watch_ad_toggle(call):
    try:
        link_id = int(call.data[len("admin_watch_ad_toggle_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "الرابط غير صالح.", show_alert=True)
        return
    ad = get_watch_ad_link(link_id)
    if ad is None:
        bot.answer_callback_query(call.id, "الرابط غير موجود.", show_alert=True)
        return
    set_watch_ad_link_active(link_id, not bool(ad["active"]))
    bot.answer_callback_query(
        call.id,
        "✅ تم إظهار الإعلان." if not ad["active"] else "⏸️ تم إخفاء الإعلان.",
    )
    callback_admin_watch_ads(call)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_service_prices"
    and is_admin(call.from_user.id)
)
def callback_admin_service_prices(call):
    text = (
        "⚙️ <b>أسعار وكميات الخدمات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "اختر الخدمة ثم عدّل السعر أو الكمية بشكل مستقل.\n"
        f"📺 مكافأة مشاهدة الإعلان الحالية: "
        f"<b>{format_balance(get_ad_reward())}</b>\n\n"
        "مثال: الكمية <b>1</b> متابع والسعر <b>0.50 جنيه</b>.\n"
        "السعر والكمية يجب أن يكونا أكبر من صفر."
    )
    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=service_prices_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_set_ad_reward"
    and is_admin(call.from_user.id)
)
def callback_admin_set_ad_reward(call):
    admin_id = call.from_user.id
    user_state[admin_id] = {"step": "awaiting_ad_reward"}
    bot.edit_message_text(
        "📺 <b>تعديل مكافأة مشاهدة الإعلان</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"المكافأة الحالية: <b>{format_balance(get_ad_reward())}</b>\n\n"
        "أرسل المكافأة الجديدة بالجنيه أو الدولار، مثل:\n"
        "• <code>0.50</code>\n"
        "• <code>$0.01</code>\n\n"
        "سيتم تطبيق القيمة الجديدة على إثباتات الإعلانات الجديدة فقط.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="admin_service_prices",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)




@bot.callback_query_handler(
    func=lambda call: call.data == "admin_set_min_withdrawal"
    and is_admin(call.from_user.id)
)
def callback_admin_set_min_withdrawal(call):
    admin_id = call.from_user.id
    current = get_min_withdrawal()
    user_state[admin_id] = {"step": "awaiting_min_withdrawal"}
    bot.edit_message_text(
        "💰 <b>تعديل الحد الأدنى للسحب</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"الحد الأدنى الحالي: <b>{format_balance(current)}</b>\n\n"
        "أرسل الحد الأدنى الجديد بالجنيه أو الدولار، مثل:\n"
        "• <code>10</code>\n"
        "• <code>25</code>\n"
        "• <code>50</code>\n"
        "• <code>100</code>\n"
        "• <code>250</code>\n"
        "• <code>1000</code>\n\n"
        "⚠️ القيمة يجب أن تكون أكبر من صفر.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="admin_panel",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_set_price_")
    and is_admin(call.from_user.id)
)
def callback_admin_set_price(call):
    admin_id = call.from_user.id
    service_key = call.data[len("admin_set_price_"):]
    service = SERVICE_INDEX.get(service_key)
    if service is None:
        bot.answer_callback_query(call.id, "الخدمة غير موجودة.", show_alert=True)
        return

    user_state[admin_id] = {
        "step": "awaiting_service_price",
        "service_key": service_key,
    }
    bot.edit_message_text(
        f"⚙️ <b>تعديل سعر الخدمة</b>\n\n"
        f"الخدمة: <b>{html.escape(service_display_name(service_key, service))}</b>\n"
        f"السعر الحالي: <b>{format_balance(get_service_price(service_key))}</b>\n\n"
        "أرسل السعر الجديد بالجنيه أو الدولار، مثل 12.35 أو $0.50.\n\n"
        "أرسل /admin للإلغاء والعودة للوحة التحكم.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data="admin_service_prices",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_service_settings_")
    and is_admin(call.from_user.id)
)
def callback_admin_service_settings(call):
    service_key = call.data[len("admin_service_settings_"):]
    service = SERVICE_INDEX.get(service_key)
    if service is None:
        bot.answer_callback_query(call.id, "الخدمة غير موجودة.", show_alert=True)
        return

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton(
            "💰 تعديل السعر",
            callback_data=f"admin_set_price_{service_key}",
        ),
        InlineKeyboardButton(
            "🔢 تعديل الكمية",
            callback_data=f"admin_set_quantity_{service_key}",
        ),
    )
    markup.add(InlineKeyboardButton(
        "🔙 أسعار وكميات الخدمات",
        callback_data="admin_service_prices",
    ))
    bot.edit_message_text(
        "⚙️ <b>إعدادات الخدمة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"الخدمة: <b>{html.escape(service_display_name(service_key, service))}</b>\n"
        f"📌 التكلفة الأساسية: <b>{format_balance(get_service_base_cost(service_key))}</b>\n"
        f"💰 سعر البيع (العميل): <b>{format_balance(get_service_price(service_key))}</b>\n"
        f"📊 هامش المنصة: <b>{int((MARGIN_MULTIPLIER - 1) * 100)}%</b>\n"
        f"🔢 الكمية الحالية: <b>{get_service_quantity(service_key)} وحدة</b>\n\n"
        "اختر الإعداد الذي تريد تغييره.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_set_quantity_")
    and is_admin(call.from_user.id)
)
def callback_admin_set_quantity(call):
    admin_id = call.from_user.id
    service_key = call.data[len("admin_set_quantity_"):]
    service = SERVICE_INDEX.get(service_key)
    if service is None:
        bot.answer_callback_query(call.id, "الخدمة غير موجودة.", show_alert=True)
        return

    user_state[admin_id] = {
        "step": "awaiting_service_quantity",
        "service_key": service_key,
    }
    bot.edit_message_text(
        "🔢 <b>تعديل كمية الخدمة</b>\n\n"
        f"الخدمة: <b>{html.escape(service_display_name(service_key, service))}</b>\n"
        f"الكمية الحالية: <b>{get_service_quantity(service_key)} وحدة</b>\n\n"
        "أرسل الكمية الجديدة كرقم صحيح موجب.\n"
        "مثال: أرسل <code>1</code> لجعل الطلب متابعاً واحداً.\n\n"
        "أرسل /admin للإلغاء والعودة للوحة التحكم.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ إلغاء",
                callback_data=f"admin_service_settings_{service_key}",
            ),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_topup"
                             and is_admin(call.from_user.id))
def callback_admin_topup(call):
    admin_id = call.from_user.id
    user_state[admin_id] = {"step": "awaiting_topup_user_id"}
    bot.edit_message_text(
        "➕ <b>شحن رصيد لمستخدم</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل الآن المعرف الرقمي للمستخدم (User ID):\n\n"
        "<i>أرسل /admin للإلغاء والعودة للوحة التحكم.</i>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel"),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_add_task"
                             and is_admin(call.from_user.id))
def callback_admin_add_task(call):
    admin_id = call.from_user.id
    bot.edit_message_text(
        "➕ <b>إضافة مهمة جديدة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "اختر نوع المهمة:\n"
        "• قناة Telegram: تحقق آلي من الاشتراك.\n"
        "• تواصل اجتماعي: لقطة شاشة ومراجعة يدوية قبل المكافأة.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📢 انضمام قناة Telegram — آلي",
                callback_data="admin_task_type_telegram",
            )],
            [InlineKeyboardButton(
                "📸 تواصل اجتماعي — مراجعة يدوية",
                callback_data="admin_task_type_social",
            )],
            [InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel")],
        ]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data in {
        "admin_task_type_telegram",
        "admin_task_type_social",
    } and is_admin(call.from_user.id)
)
def callback_admin_task_type(call):
    task_type = (
        "telegram_channel"
        if call.data == "admin_task_type_telegram"
        else "social_manual"
    )
    user_state[call.from_user.id] = {
        "step": "awaiting_manual_task_title",
        "task_type": task_type,
    }
    bot.edit_message_text(
        "📝 <b>إضافة المهمة</b>\n\n"
        f"النوع: <b>{'انضمام قناة Telegram — تحقق آلي' if task_type == 'telegram_channel' else 'تواصل اجتماعي — مراجعة يدوية'}</b>\n\n"
        "أرسل عنوان المهمة الذي سيظهر للمستخدمين، أو أرسل /admin للإلغاء.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel"),
        ]]),
    )
    bot.answer_callback_query(call.id)
@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast"
                             and is_admin(call.from_user.id))
def callback_admin_broadcast(call):
    admin_id = call.from_user.id
    user_state[admin_id] = {"step": "awaiting_broadcast"}
    total = len(get_all_user_ids())
    bot.edit_message_text(
        f"📢 <b>إذاعة رسالة</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"سيتم إرسال الرسالة إلى <b>{total}</b> مستخدم.\n\n"
        f"✏️ <b>أرسل الآن النص الذي تريد إذاعته:</b>\n\n"
        f"<i>أرسل /admin للإلغاء.</i>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel"),
        ]]),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_close"
                             and is_admin(call.from_user.id))
def callback_admin_close(call):
    user_state.pop(call.from_user.id, None)
    bot.edit_message_text(
        "🔐 <i>تم إغلاق لوحة التحكم.</i>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )
    bot.answer_callback_query(call.id, "تم إغلاق اللوحة.")


# ══════════════════════════════════════════════════════════════════════════════
# ─── Callback: الملف الشخصي ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "profile")
def callback_profile(call):
    user_id = call.from_user.id
    row     = get_user(user_id)
    if row is None:
        bot.answer_callback_query(call.id, "لم يتم العثور على حسابك.", show_alert=True)
        return
    if not require_active_account(call):
        return

    name_parts    = [row["first_name"]] + ([row["last_name"]] if row["last_name"] else [])
    full_name     = " ".join(name_parts)
    username_line = f"🔗 <b>المعرّف:</b> @{row['username']}\n" if row["username"] else ""
    ref_count     = get_referral_count(user_id)
    orders        = get_user_orders(user_id, limit=3)
    orders_count  = len(orders)

    profile_text = (
        "👤 <b>ملفك الشخصي</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📛 <b>الاسم:</b> {full_name}\n"
        f"🆔 <b>المعرّف الرقمي:</b> <code>{row['user_id']}</code>\n"
        f"{username_line}"
        f"💰 <b>رصيدك:</b> {balance_text(row)}\n"
        f"👥 <b>إحالاتك الناجحة:</b> {ref_count}\n"
        f"📦 <b>طلبات المتجر:</b> {orders_count}\n"
        f"📅 <b>تاريخ التسجيل:</b> {row['joined_at']}"
    )

    back_markup = InlineKeyboardMarkup()
    back_markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
    bot.edit_message_text(profile_text, chat_id=call.message.chat.id,
                          message_id=call.message.message_id, reply_markup=back_markup)
    bot.answer_callback_query(call.id)


# ══════════════════════════════════════════════════════════════════════════════
# ─── Callback: كسب النقاط / الإحالة ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "add_user_ad")
def callback_add_user_ad(call):
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(
            call.id, "يرجى إرسال /start أولاً.", show_alert=True
        )
        return
    if not require_active_account(call):
        return
    if get_pending_user_ad(user_id) is not None:
        bot.answer_callback_query(
            call.id,
            "⏳ لديك إعلان قيد المراجعة بالفعل.",
            show_alert=True,
        )
        return

    user_state[user_id] = {"step": "awaiting_user_ad_title"}
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "📢 <b>إضافة إعلان جديد</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل عنوان الإعلان، أو اضغط «إلغاء» للعودة للقائمة الرئيسية.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ إلغاء", callback_data="back_main"),
        ]]),
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("approve_user_ad_")
)
def callback_approve_user_ad(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(
            call.id, "⚠️ هذا القرار للإدارة فقط.", show_alert=True
        )
        return
    try:
        ad_id = int(call.data[len("approve_user_ad_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ الإعلان غير صالح.", show_alert=True)
        return

    ad = approve_user_ad(ad_id)
    if ad is None:
        bot.answer_callback_query(
            call.id,
            "ℹ️ تمت مراجعة الإعلان مسبقاً أو لم يعد متاحاً.",
            show_alert=True,
        )
        return

    ad_text = (
        "📢 <b>إعلان جديد</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>{html.escape(ad['title'])}</b>\n"
        f"📝 {html.escape(ad['description'])}\n\n"
        f"💰 <b>السعر:</b> "
        f"{'مجاني' if ad['price_cents'] == 0 else format_balance(ad['price_cents'])}\n"
        f"🔗 <b>الرابط:</b> {html.escape(ad['link'])}"
    )

    sent_count = 0
    failed_count = 0
    for user_id in get_all_user_ids():
        try:
            bot.send_message(user_id, ad_text)
            sent_count += 1
        except Exception:
            failed_count += 1

    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass

    try:
        bot.send_message(
            ad["user_id"],
            "✅ <b>تمت الموافقة على إعلانك ونشره للمستخدمين بنجاح.</b>\n\n"
            f"📌 <b>{html.escape(ad['title'])}</b>\n"
            f"📊 تم إرساله إلى <b>{sent_count}</b> مستخدم.",
        )
        owner_status = "وتم إبلاغ صاحب الإعلان."
    except Exception:
        owner_status = "لكن تعذر إرسال إشعار صاحب الإعلان."

    bot.answer_callback_query(call.id, "✅ تمت الموافقة ونشر الإعلان.", show_alert=True)
    bot.send_message(
        call.from_user.id,
        "✅ <b>تم نشر الإعلان</b>\n"
        f"📨 نجح الإرسال إلى: <b>{sent_count}</b>\n"
        f"⚠️ تعذر الإرسال إلى: <b>{failed_count}</b>\n"
        f"{owner_status}",
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("reject_user_ad_")
)
def callback_reject_user_ad(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(
            call.id, "⚠️ هذا القرار للإدارة فقط.", show_alert=True
        )
        return
    try:
        ad_id = int(call.data[len("reject_user_ad_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ الإعلان غير صالح.", show_alert=True)
        return

    ad = reject_user_ad(ad_id)
    if ad is None:
        bot.answer_callback_query(
            call.id,
            "ℹ️ تمت مراجعة الإعلان مسبقاً أو لم يعد متاحاً.",
            show_alert=True,
        )
        return

    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass

    try:
        bot.send_message(
            ad["user_id"],
            "❌ <b>تم رفض إعلانك من الإدارة.</b>\n\n"
            f"📌 الإعلان: <b>{html.escape(ad['title'])}</b>\n"
            "لم يتم نشر الإعلان للمستخدمين.",
        )
        owner_status = "وتم إبلاغ صاحب الإعلان."
    except Exception:
        owner_status = "لكن تعذر إرسال إشعار صاحب الإعلان."

    bot.answer_callback_query(call.id, "❌ تم رفض الإعلان.", show_alert=True)
    bot.send_message(
        call.from_user.id,
        f"❌ <b>تم رفض الإعلان رقم {ad_id}.</b>\n{owner_status}",
    )


@bot.callback_query_handler(func=lambda call: call.data == "promote_channel")
def callback_promote_channel(call):
    user_id = call.from_user.id
    user = get_user(user_id)
    if user is None:
        bot.answer_callback_query(
            call.id, "يرجى إرسال /start أولاً.", show_alert=True
        )
        return
    if not require_active_account(call):
        return
    if row_balance_cents(user) < PROMOTION_MIN_CENTS:
        bot.answer_callback_query(
            call.id,
            f"❌ تحتاج إلى {format_balance(PROMOTION_MIN_CENTS)} على الأقل لإنشاء إعلان.",
            show_alert=True,
        )
        return

    user_state.pop(user_id, None)
    package_lines = "\n".join(
        f"📣 <b>{package['label']}</b> — "
        f"<b>{promotion_package_price(package)}</b>"
        for package in PROMOTION_PACKAGES.values()
    )
    text = (
        "📣 <b>ثبّت إعلانك — روّج لقناتك</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "أضف قناتك إلى قائمة قنوات التفعيل الإجبارية أمام مستخدمي البوت "
        "لجذب مشتركين جدد.\n\n"
        f"{package_lines}\n\n"
        "✅ يتم الخصم من محفظتك بعد التحقق من القناة.\n"
        "✅ يجب أن تكون مالكاً أو مشرفاً في القناة.\n"
        "✅ يجب إضافة البوت مشرفاً حتى يتمكن من فحص الاشتراكات.\n"
        "✅ يتوقف الإعلان تلقائياً عند اكتمال العدد المدفوع.\n\n"
        f"💼 رصيدك الحالي: <b>{balance_text(get_user(user_id))}</b>\n\n"
        "اختر الباقة المناسبة للمتابعة:"
    )
    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=promotion_packages_keyboard(),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("promotion_package_")
)
def callback_promotion_package(call):
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(
            call.id, "يرجى إرسال /start أولاً.", show_alert=True
        )
        return
    if not require_active_account(call):
        return

    package_key = call.data[len("promotion_package_"):]
    package = PROMOTION_PACKAGES.get(package_key)
    if package is None:
        bot.answer_callback_query(call.id, "الباقة غير متاحة.", show_alert=True)
        return

    user_state[user_id] = {
        "step": "awaiting_promoted_channel",
        "package_key": package_key,
    }
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "📣 <b>إضافة قناة للإعلان</b>\n\n"
        f"الباقة المختارة: <b>{package['label']}</b>\n"
        f"التكلفة: <b>{format_balance(package['points_cost'])}</b> "
        f"({package['usd_price']}$)\n\n"
        "أرسل الآن معرف قناتك العامة مثل:\n"
        "<code>@my_channel</code>\n\n"
        "أو أرسل رابط القناة مثل:\n"
        "<code>https://t.me/my_channel</code>\n\n"
        "تأكد من إضافة البوت مشرفاً في القناة قبل الإرسال.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ إلغاء", callback_data="back_main"),
        ]]),
    )


@bot.callback_query_handler(func=lambda call: call.data == "earn_points")
def callback_earn_points(call):
    user_id = call.from_user.id
    row     = get_user(user_id)
    if row is None:
        bot.answer_callback_query(call.id, "يرجى بدء البوت أولاً.", show_alert=True)
        return
    if not require_active_account(call):
        return

    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    ref_count     = get_referral_count(user_id)

    earn_text = (
        "🎯 <b>كسب النقاط عبر الإحالة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📣 <b>كيف تعمل؟</b>\n"
        "• شارك رابطك الخاص مع أصدقائك.\n"
        f"• تربح <b>{format_balance(REFERRAL_REWARD)}</b> عن كل صديق "
        "ينضم عبر رابطك ويفتح حسابه بعد تحقق الشروط. 🎁\n"
        "• تُصرف المكافأة فور فتح الحساب، بدون انتظار أو احتجاز.\n\n"
        f"🔗 <b>رابطك الخاص:</b>\n"
        f"<code>{referral_link}</code>\n\n"
        f"👥 <b>إجمالي إحالاتك:</b> {ref_count}\n"
        f"🏆 <b>رصيدك الحالي:</b> {balance_text(row)}"
    )
    bot.edit_message_text(earn_text, chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          reply_markup=referral_keyboard(user_id))
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "watch_ads")
def callback_watch_ads(call):
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(
            call.id,
            "يرجى إرسال /start أولاً.",
            show_alert=True,
        )
        return
    if not require_active_account(call):
        return

    if get_pending_ad_review(user_id) is not None:
        bot.answer_callback_query(
            call.id,
            "⏳ أرسلت إثبات الإعلان بالفعل، وينتظر مراجعة الإدارة.",
            show_alert=True,
        )
        return

    active_ads = get_watch_ad_links()
    if not active_ads:
        bot.answer_callback_query(
            call.id,
            "لا توجد إعلانات متاحة حالياً. حاول لاحقاً.",
            show_alert=True,
        )
        return

    markup = InlineKeyboardMarkup()
    for ad in active_ads:
        markup.add(InlineKeyboardButton(
            f"▶️ {ad['title'][:45]}",
            callback_data=f"watch_ad_{ad['id']}",
        ))
    markup.add(InlineKeyboardButton(
        "🔙 رجوع للقائمة الرئيسية",
        callback_data="back_main",
    ))
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "📺 <b>إعلانات المشاهدة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎁 شاهد إعلاناً بالكامل لتكسب <b>{format_balance(get_ad_reward())}</b> "
        "بعد موافقة الإدارة.\n\n"
        "اختر إعلاناً للمتابعة:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("watch_ad_")
)
def callback_watch_ad(call):
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(
            call.id, "يرجى إرسال /start أولاً.", show_alert=True
        )
        return
    if not require_active_account(call):
        return
    try:
        ad_id = int(call.data[len("watch_ad_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "الإعلان غير صالح.", show_alert=True)
        return

    ad = get_watch_ad_link(ad_id)
    if ad is None or not ad["active"]:
        bot.answer_callback_query(
            call.id,
            "هذا الإعلان لم يعد متاحاً.",
            show_alert=True,
        )
        return

    user_state[user_id] = {
        "step": "awaiting_ad_proof",
        "ad_link_id": ad_id,
    }
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        "▶️ فتح الإعلان",
        url=ad["url"],
    ))
    markup.add(InlineKeyboardButton(
        "🔙 العودة للإعلانات",
        callback_data="watch_ads",
    ))
    markup.add(InlineKeyboardButton(
        "🏠 القائمة الرئيسية",
        callback_data="back_main",
    ))
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"🎁 شاهد <b>{html.escape(ad['title'])}</b> بالكامل لتكسب "
        f"<b>{format_balance(get_ad_reward())}</b> مجاناً!\n\n"
        "بعد الانتهاء، اضغط «فتح الإعلان» ثم أرسل هنا صورة لقطة شاشة "
        "تثبت المشاهدة. سيتم إرسالها إلى الإدارة للمراجعة قبل إضافة النقاط.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ─── Callback: المهام اليومية ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "daily_tasks")
def callback_daily_tasks(call):
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(call.id, "يرجى بدء البوت أولاً.", show_alert=True)
        return
    if not require_active_account(call):
        return
    text, markup = build_tasks_text(user_id)
    bot.edit_message_text(text, chat_id=call.message.chat.id,
                          message_id=call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("check_channel_"))
def callback_check_channel(call):
    """يتحقق من اشتراك المستخدم في قناة محددة ويمنحه مكافأتها فوراً."""
    if not require_active_account(call):
        return
    user_id  = call.from_user.id
    task_key = call.data[len("check_channel_"):]

    if get_user(user_id) is None:
        bot.answer_callback_query(call.id, "يرجى إرسال /start أولاً.", show_alert=True)
        return

    # تجديد القائمة حتى تظهر الحملات المدفوعة الجديدة فور تفعيلها.
    refresh_required_channels()
    # البحث عن القناة المطلوبة في القائمة
    channel = next((ch for ch in REQUIRED_CHANNELS if ch["task_key"] == task_key), None)
    if channel is None:
        bot.answer_callback_query(call.id, "⚠️ القناة غير موجودة.", show_alert=True)
        return

    if is_task_done(user_id, task_key):
        bot.answer_callback_query(
            call.id, "✅ لقد استلمتَ مكافأة هذه القناة مسبقاً!", show_alert=True
        )
        return

    if not is_subscribed(user_id, channel["username"]):
        bot.answer_callback_query(
            call.id,
            f"❌ لم تشترك في {channel['name']} بعد!\nاشترك أولاً ثم اضغط التحقق.",
            show_alert=True,
        )
        return

    # الاشتراك مؤكد — منح المكافأة فوراً، أو استرداد خصم سابق عند العودة.
    reward_result = grant_channel_reward(user_id, channel)
    if reward_result is None:
        bot.answer_callback_query(
            call.id, "✅ لقد استلمتَ مكافأة هذه القناة مسبقاً!", show_alert=True
        )
        return
    reward_kind = reward_result["kind"]
    reward_points = reward_result["points"]
    updated = get_user(user_id)
    bot.answer_callback_query(
        call.id,
        (
            f"🎉 تم التحقق! حصلتَ على {format_balance(reward_points)}."
            if reward_kind == "granted"
            else f"🔁 تم استرداد {format_balance(reward_points)} بعد عودتك للقناة."
        ),
        show_alert=True,
    )

    # تحديث الشاشة الحالية (مهام يومية أو بوابة التفعيل)
    try:
        if is_account_active(user_id):
            text, markup = build_tasks_text(user_id)
        else:
            text   = build_activation_gate_text(user_id)
            markup = activation_gate_keyboard(user_id)
        bot.edit_message_text(
            text, chat_id=call.message.chat.id,
            message_id=call.message.message_id, reply_markup=markup,
        )
    except Exception:
        pass

    bot.send_message(
        call.message.chat.id,
        f"✅ <b>{'مكافأة القناة' if reward_kind == 'granted' else 'استرداد مكافأة القناة'}!</b>\n\n"
        f"تم التحقق من اشتراكك في <b>{html.escape(channel['name'])}</b> بنجاح.\n"
        f"💰 تم إضافة <b>{format_balance(reward_points)}</b> إلى رصيدك.\n\n"
        f"🏆 <b>رصيدك الحالي:</b> {balance_text(updated)}",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("claim_referral_"))
def callback_claim_referral(call):
    """يسجل التنفيذ ويرسله لموافقة المعلن قبل صرف المكافأة."""
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(call.id, "يرجى إرسال /start أولاً.", show_alert=True)
        return
    if not require_active_account(call):
        return

    try:
        task_id = int(call.data[len("claim_referral_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ المهمة غير صالحة.", show_alert=True)
        return

    result = claim_referral_task(task_id, user_id)
    if result == "pending_client":
        claim = get_referral_task_claim_for_worker(task_id, user_id)
        bot.answer_callback_query(
            call.id,
            "✅ تم تسجيل التنفيذ وإرساله لموافقة صاحب الطلب.",
            show_alert=True,
        )
        text, markup = build_tasks_text(user_id)
        try:
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup,
            )
        except Exception:
            pass
        bot.send_message(
            call.message.chat.id,
            "✅ <b>تم تسجيل تنفيذ المهمة</b>\n\n"
            "⏳ ينتظر التنفيذ موافقة صاحب الطلب. "
            "لن تُضاف المكافأة قبل الموافقة.",
        )
        if claim is not None:
            try:
                bot.send_message(
                    claim["buyer_id"],
                    "📬 <b>تنفيذ جديد على طلب الإحالات</b>\n\n"
                    f"🔢 <b>رقم الطلب:</b> <code>{task_id}</code>\n"
                    f"👤 <b>المؤدي:</b> <code>{user_id}</code>\n"
                    f"💰 <b>المكافأة عند الموافقة:</b> "
                    f"<b>{format_balance(REFERRAL_REWARD)}</b>\n\n"
                    "راجع تنفيذ المهمة ثم اختر الموافقة أو الرفض:",
                    reply_markup=referral_claim_decision_keyboard(claim["id"]),
                )
            except Exception:
                pass
        return

    messages = {
        "already_done": "✅ لقد نفذت هذه المهمة واستلمت مكافأتها مسبقاً.",
        "unavailable": "⚠️ انتهت كمية هذه المهمة أو لم تعد متاحة.",
        "own_task": "⚠️ لا يمكنك تنفيذ مهمة الإحالة الخاصة بك.",
    }
    bot.answer_callback_query(
        call.id,
        messages.get(result, "⚠️ تعذر تنفيذ المهمة."),
        show_alert=True,
    )
    if result in ("unavailable", "already_done"):
        try:
            text, markup = build_tasks_text(user_id)
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup,
            )
        except Exception:
            pass


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("approve_referral_claim_")
)
def callback_approve_referral_claim(call):
    if not is_admin(call.from_user.id) and get_user(call.from_user.id) is None:
        bot.answer_callback_query(call.id, "⚠️ تعذر التحقق من الحساب.", show_alert=True)
        return
    try:
        claim_id = int(call.data[len("approve_referral_claim_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ الطلب غير صالح.", show_alert=True)
        return

    claim = get_referral_task_claim(claim_id)
    if claim is None or claim["buyer_id"] != call.from_user.id:
        bot.answer_callback_query(call.id, "⚠️ لا تملك صلاحية هذا القرار.", show_alert=True)
        return
    approved = approve_referral_task_claim(claim_id, call.from_user.id)
    if approved is None:
        bot.answer_callback_query(
            call.id,
            "ℹ️ تمت معالجة هذا التنفيذ مسبقاً أو لم يعد متاحاً.",
            show_alert=True,
        )
        return

    worker_id = approved["worker_id"]
    worker_balance = get_user(worker_id)
    bot.answer_callback_query(call.id, "✅ تمت الموافقة وصرف المكافأة.", show_alert=True)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass
    try:
        bot.send_message(
            worker_id,
            "🎉 <b>وافق صاحب الطلب على تنفيذك!</b>\n\n"
            f"✅ تمت إضافة <b>{format_balance(REFERRAL_REWARD)}</b> إلى رصيدك.\n"
            f"🏆 <b>رصيدك الحالي:</b> {balance_text(worker_balance)}",
        )
    except Exception:
        pass


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("reject_referral_claim_")
)
def callback_reject_referral_claim(call):
    if not require_active_account(call):
        return
    try:
        claim_id = int(call.data[len("reject_referral_claim_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ الطلب غير صالح.", show_alert=True)
        return

    claim = get_referral_task_claim(claim_id)
    if claim is None or claim["buyer_id"] != call.from_user.id:
        bot.answer_callback_query(call.id, "⚠️ لا تملك صلاحية هذا القرار.", show_alert=True)
        return
    rejected = reject_referral_task_claim(claim_id, call.from_user.id)
    if rejected is None:
        bot.answer_callback_query(
            call.id,
            "ℹ️ تمت معالجة هذا التنفيذ مسبقاً أو لم يعد متاحاً.",
            show_alert=True,
        )
        return

    bot.answer_callback_query(
        call.id,
        "تم رفض التنفيذ وإعادة الحصة للمهمة.",
        show_alert=True,
    )
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass
    try:
        bot.send_message(
            rejected["worker_id"],
            "⚠️ <b>رفض صاحب الطلب تنفيذ المهمة</b>\n\n"
            "تمت إعادة الحصة للمهمة، ولم تُخصم منك أو تُصرف مكافأة. "
            "إذا كان لديك إثبات على التنفيذ، يمكنك رفع شكوى بصورة:",
            reply_markup=referral_complaint_keyboard(claim_id),
        )
    except Exception:
        pass


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("complaint_referral_")
)
def callback_start_referral_complaint(call):
    if not require_active_account(call):
        return
    try:
        claim_id = int(call.data[len("complaint_referral_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ الطلب غير صالح.", show_alert=True)
        return
    claim = get_referral_task_claim(claim_id)
    if claim is None or claim["worker_id"] != call.from_user.id:
        bot.answer_callback_query(call.id, "⚠️ لا تملك صلاحية هذه الشكوى.", show_alert=True)
        return
    if claim["status"] != "client_rejected":
        bot.answer_callback_query(
            call.id,
            "⚠️ يمكن رفع الشكوى فقط بعد رفض صاحب الطلب.",
            show_alert=True,
        )
        return
    user_state[call.from_user.id] = {
        "step": "awaiting_referral_complaint_photo",
        "referral_claim_id": claim_id,
    }
    bot.answer_callback_query(call.id, "أرسل الآن صورة إثبات التنفيذ.", show_alert=True)
    bot.send_message(
        call.message.chat.id,
        "📷 أرسل صورة واضحة تثبت تنفيذ المهمة.\n"
        "سيتم إرسالها للإدارة للمراجعة، ولن تُصرف المكافأة إلا إذا ثبتت الشكوى.",
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("approve_referral_complaint_")
)
def callback_approve_referral_complaint(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⚠️ هذا القرار للإدارة فقط.", show_alert=True)
        return
    try:
        complaint_id = int(call.data[len("approve_referral_complaint_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ الشكوى غير صالحة.", show_alert=True)
        return
    approved = approve_referral_task_complaint(complaint_id)
    if approved is None:
        bot.answer_callback_query(
            call.id,
            "ℹ️ تمت معالجة الشكوى مسبقاً أو لم يعد التنفيذ متاحاً.",
            show_alert=True,
        )
        return
    if approved.get("error") == "insufficient_buyer_balance":
        bot.answer_callback_query(
            call.id,
            "⚠️ رصيد المعلن غير كافٍ؛ لم تُعتمد الشكوى.",
            show_alert=True,
        )
        return

    bot.answer_callback_query(call.id, "✅ تم اعتماد الشكوى وصرف التعويض.", show_alert=True)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass
    try:
        bot.send_message(
            approved["worker_id"],
            "🎉 <b>تم اعتماد شكواك</b>\n\n"
            f"تمت إضافة <b>{format_balance(REFERRAL_REWARD)}</b> إلى رصيدك "
            "بعد ثبوت تنفيذ المهمة.",
        )
    except Exception:
        pass
    try:
        bot.send_message(
            approved["buyer_id"],
            "⚠️ <b>إنذار بخصوص تنفيذ مهمة مدفوعة</b>\n\n"
            f"اعتمدت الإدارة شكوى المؤدي في الطلب <code>{approved['task_id']}</code>.\n"
            f"تم خصم <b>{format_balance(REFERRAL_REWARD)}</b> من رصيدك "
            "وإيداعها للمؤدي.\n"
            "يرجى مراجعة التنفيذات وعدم رفض التنفيذ الصحيح.",
        )
    except Exception:
        pass


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("reject_referral_complaint_")
)
def callback_reject_referral_complaint(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⚠️ هذا القرار للإدارة فقط.", show_alert=True)
        return
    try:
        complaint_id = int(call.data[len("reject_referral_complaint_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ الشكوى غير صالحة.", show_alert=True)
        return
    rejected = reject_referral_task_complaint(complaint_id)
    if rejected is None:
        bot.answer_callback_query(
            call.id,
            "ℹ️ تمت معالجة الشكوى مسبقاً أو لم تعد متاحة.",
            show_alert=True,
        )
        return
    bot.answer_callback_query(call.id, "تم رفض الشكوى دون صرف.", show_alert=True)
    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass
    try:
        bot.send_message(
            rejected["worker_id"],
            "❌ <b>تم رفض شكواك</b>\n\n"
            "لم تثبت الإدارة تنفيذ المهمة، لذلك لم تُصرف مكافأة. "
            "وقد أصبحت الحصة متاحة للمستخدمين الآخرين.",
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("open_manual_"))
def callback_open_manual(call):
    if not require_active_account(call):
        return
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(
            call.id, "يرجى إرسال /start أولاً.", show_alert=True
        )
        return

    try:
        task_id = int(call.data[len("open_manual_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ المهمة غير صالحة.", show_alert=True)
        return

    task = get_manual_task(task_id)
    if task is None:
        bot.answer_callback_query(call.id, "⚠️ المهمة غير متاحة.", show_alert=True)
        return

    if task["status"] != "active" or task["quantity_remaining"] <= 0:
        bot.answer_callback_query(
            call.id, "⚠️ انتهت كمية هذه المهمة أو لم تعد متاحة.", show_alert=True
        )
        return

    completed = is_task_done(user_id, f"manual_task:{task_id}")
    if completed:
        bot.answer_callback_query(
            call.id, "✅ لقد نفذت هذه المهمة واستلمت مكافأتها مسبقاً.",
            show_alert=True,
        )
        return

    if manual_task_requires_proof(task):
        if get_pending_manual_review(user_id, task_id) is not None:
            bot.answer_callback_query(
                call.id, "⏳ تم إرسال إثبات هذه المهمة وبانتظار مراجعة الإدارة.",
                show_alert=True,
            )
            return
        user_state[user_id] = {
            "step": "awaiting_manual_proof",
            "manual_task_id": task_id,
        }
        bot.answer_callback_query(call.id)
        markup = InlineKeyboardMarkup()
        task_url = manual_task_open_url(task)
        if task_url:
            markup.add(InlineKeyboardButton("🔗 فتح المهمة", url=task_url))
        markup.add(InlineKeyboardButton(
            "❌ إلغاء المهمة",
            callback_data=f"cancel_manual_{task_id}",
        ))
        bot.send_message(
            call.message.chat.id,
            "🛡️ <b>إثبات تنفيذ المهمة</b>\n\n"
            f"📝 <b>{html.escape(task['title'])}</b>\n"
            f"⚙️ <b>{manual_task_type_label(task)}</b>\n"
            f"🎯 <b>الهدف:</b> <code>{html.escape(manual_task_target(task))}</code>\n"
            f"📋 <b>الشروط:</b> {html.escape(manual_task_instructions(task))}\n\n"
            "بعد التنفيذ أرسل هنا <b>صورة لقطة شاشة</b> واضحة. "
            "لن تُضاف النقاط إلا بعد مراجعة الإدارة.",
            reply_markup=markup,
        )
        return

    bot.answer_callback_query(call.id)
    markup = InlineKeyboardMarkup()
    task_url = manual_task_open_url(task)
    if task_url:
        markup.add(InlineKeyboardButton("🔗 فتح القناة", url=task_url))
    markup.add(InlineKeyboardButton(
        "✅ تأكيد تنفيذ المهمة",
        callback_data=f"claim_manual_{task_id}",
    ))
    markup.add(InlineKeyboardButton(
        "❌ إلغاء المهمة",
        callback_data=f"cancel_manual_{task_id}",
    ))
    bot.send_message(
        call.message.chat.id,
        "📝 <b>تنفيذ مهمة Telegram</b>\n\n"
        f"📝 <b>{html.escape(task['title'])}</b>\n"
        f"🎯 القناة: <code>{html.escape(manual_task_target(task))}</code>\n"
        "اشترك في القناة ثم اضغط زر التأكيد. سيتم التحقق من اشتراكك آلياً.",
        reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_manual_"))
def callback_cancel_manual_task(call):
    """يلغي حالة تنفيذ المهمة الحالية دون حذف المهمة أو بياناتها."""
    if not require_active_account(call):
        return
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(call.id, "يرجى إرسال /start أولاً.", show_alert=True)
        return

    try:
        task_id = int(call.data[len("cancel_manual_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ المهمة غير صالحة.", show_alert=True)
        return

    state = user_state.get(user_id, {})
    if (
        state.get("step") == "awaiting_manual_proof"
        and state.get("manual_task_id") == task_id
    ):
        user_state.pop(user_id, None)

    if account_access_allowed(user_id):
        text, markup = build_tasks_text(user_id)
    else:
        text, markup = (
            build_activation_gate_text(user_id),
            activation_gate_keyboard(user_id),
        )
    bot.answer_callback_query(call.id, "✅ تم إلغاء تنفيذ المهمة.")
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=markup,
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("claim_manual_"))
def callback_claim_manual(call):
    if not require_active_account(call):
        return
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(call.id, "يرجى إرسال /start أولاً.", show_alert=True)
        return

    try:
        task_id = int(call.data[len("claim_manual_"):])
    except ValueError:
        bot.answer_callback_query(call.id, "⚠️ المهمة غير صالحة.", show_alert=True)
        return

    result = claim_manual_task(task_id, user_id)
    if result == "claimed":
        task = get_manual_task(task_id)
        reward = task["reward_points"] if task else 0
        was_active = is_account_active(user_id)
        activated = not was_active and activate_user(user_id)
        updated = get_user(user_id)
        bot.answer_callback_query(
            call.id,
            f"🎉 تم التحقق! حصلتَ على {format_balance(reward)}.",
            show_alert=True,
        )
        if activated or (was_active and account_access_allowed(user_id)):
            user = call.from_user
            text = (
                f"🌟 <b>مرحباً يا {html.escape(user.first_name or 'صديقي')}!</b>\n\n"
                "✅ تم قبول المهمة وتحديث حسابك بنجاح.\n"
                "اختر أحد الخيارات أدناه:"
            )
            markup = main_keyboard()
        else:
            text, markup = (
                build_activation_gate_text(user_id),
                activation_gate_keyboard(user_id),
            )
        try:
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup,
            )
        except Exception:
            pass
        bot.send_message(
            call.message.chat.id,
            "✅ <b>تم تنفيذ المهمة!</b>\n\n"
            f"تمت إضافة <b>{format_balance(reward)}</b> إلى رصيدك.\n"
            f"🏆 <b>رصيدك الحالي:</b> {balance_text(updated)}",
        )
        return

    messages = {
        "already_done": "✅ لقد نفذت هذه المهمة واستلمت مكافأتها مسبقاً.",
        "unavailable": "⚠️ انتهت كمية هذه المهمة أو لم تعد متاحة.",
        "proof_required": "📸 أرسل صورة لقطة الشاشة أولاً ليتم مراجعتها من الإدارة.",
        "invalid_target": "⚠️ هدف قناة Telegram غير صالح. راجع إعداد المهمة مع الإدارة.",
        "not_subscribed": "❌ لم يتم العثور على اشتراكك في القناة. اشترك أولاً ثم حاول مرة أخرى.",
    }
    bot.answer_callback_query(
        call.id,
        messages.get(result, "⚠️ تعذر تنفيذ المهمة."),
        show_alert=True,
    )
    if result in ("unavailable", "already_done"):
        try:
            if account_access_allowed(user_id):
                text, markup = build_tasks_text(user_id)
            else:
                text, markup = (
                    build_activation_gate_text(user_id),
                    activation_gate_keyboard(user_id),
                )
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup,
            )
        except Exception:
            pass


@bot.callback_query_handler(func=lambda call: call.data == "verify_activation")
def callback_verify_activation(call):
    if not require_active_account(call):
        return
    user_id = call.from_user.id
    if get_user(user_id) is None:
        bot.answer_callback_query(
            call.id, "يرجى إرسال /start أولاً.", show_alert=True
        )
        return

    channels     = get_channels_status(user_id)
    enforce_channel_subscriptions(user_id, channels)
    channels     = get_channels_status(user_id)
    not_subbed   = [ch for ch in channels if not ch["subscribed"]]
    _, pending_tasks = get_activation_requirements(user_id)

    # لا تزال هناك شروط ناقصة
    if not_subbed or pending_tasks:
        lines = [f"• {ch['name']}" for ch in not_subbed]
        lines += [f"• المهمة: {task['title']}" for task in pending_tasks[:3]]
        bot.answer_callback_query(
            call.id,
            "❌ لم تكتمل الشروط بعد:\n" + "\n".join(lines),
            show_alert=True,
        )
        show_activation_gate(call.message.chat.id, user_id, call.message.message_id)
        return

    was_frozen = not is_account_active(user_id)

    if was_frozen:
        # ─── فك تجميد حساب عوقب بسبب مغادرة قناة ───────────────────────────
        reactivated = reactivate_after_penalty(user_id)
        if not reactivated:
            bot.answer_callback_query(call.id, "⚠️ تعذر فك التجميد حالياً.", show_alert=True)
            show_activation_gate(call.message.chat.id, user_id, call.message.message_id)
            return
        # استرداد المبالغ التي خُصمت فعلياً بعد إعادة الاشتراك.
        channels_refreshed = get_channels_status(user_id)
        rewarded_back = restore_channel_rewards(user_id, channels_refreshed)
        updated = get_user(user_id)
        reward_lines = "".join(
            f"• {html.escape(ch['name'])}: +{format_balance(ch['deducted_points'])}\n"
            for ch in rewarded_back
        )
        bot.answer_callback_query(call.id, "🔓 تم فك تجميد حسابك!", show_alert=True)
        bot.edit_message_text(
            "🔓 <b>تم فك تجميد حسابك بنجاح!</b>\n\n"
            + (f"💰 <b>تم إرجاع المكافآت:</b>\n{reward_lines}\n" if reward_lines else "")
            + f"🏆 رصيدك الحالي: <b>{balance_text(updated)}</b>\n\n"
            + "أصبحت جميع ميزات البوت متاحة لك الآن.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=main_keyboard(),
        )
        return

    # ─── أول تفعيل للحساب ────────────────────────────────────────────────────
    already_active = is_account_active(user_id)
    activated      = activate_user(user_id) if not already_active else False
    updated        = get_user(user_id)

    if not activated and not already_active:
        bot.answer_callback_query(
            call.id, "⚠️ تعذر تفعيل الحساب حالياً.", show_alert=True
        )
        show_activation_gate(call.message.chat.id, user_id, call.message.message_id)
        return

    if already_active:
        bot.answer_callback_query(call.id, "✅ تم التحقق من شروط حسابك.")
    else:
        bot.answer_callback_query(call.id, "🎉 تم تفعيل حسابك بنجاح!", show_alert=True)

    activation_text = (
        "🎉 <b>تم تفعيل حسابك بنجاح!</b>\n\n"
        f"🎁 تمت إضافة <b>{format_balance(ACTIVATION_REWARD)}</b> كمكافأة تفعيل إضافية.\n"
        if activated else
        "✅ <b>تم التحقق من شروط حسابك بنجاح!</b>\n\n"
    )
    bot.edit_message_text(
        activation_text
        + f"🏆 رصيدك الحالي: <b>{balance_text(updated)}</b>\n\n"
        + "أصبحت جميع ميزات البوت متاحة لك الآن.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=main_keyboard(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# ─── Callback: متجر الخدمات ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "shop")
def callback_shop(call):
    user_id = call.from_user.id
    row     = get_user(user_id)
    if row is None:
        bot.answer_callback_query(call.id, "يرجى بدء البوت أولاً.", show_alert=True)
        return
    if not require_active_account(call):
        return

    # إلغاء أي حالة محادثة سابقة
    user_state.pop(user_id, None)

    bot.edit_message_text(
        build_shop_text(row_balance_cents(row)),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=shop_keyboard(),
    )
    bot.answer_callback_query(call.id)


# ─── اختيار منصة → عرض خدماتها ───────────────────────────────────────────────
@bot.callback_query_handler(
    func=lambda call: call.data.startswith("shop_category_")
)
def callback_shop_category(call):
    category_key = call.data[len("shop_category_"):]
    category = SERVICES.get(category_key)
    if category is None:
        bot.answer_callback_query(call.id, "⚠️ المنصة غير موجودة.", show_alert=True)
        return

    user = get_user(call.from_user.id)
    if user is None:
        bot.answer_callback_query(call.id, "يرجى بدء البوت أولاً.", show_alert=True)
        return
    if not require_active_account(call):
        return

    bot.edit_message_text(
        f"🛒 <b>{category['name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>رصيدك الحالي:</b> {balance_text(user)}\n\n"
        "اختر الخدمة التي تريدها:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=category_keyboard(category_key),
    )
    bot.answer_callback_query(call.id)


# ─── اختيار خدمة معينة → اطلب الرابط ────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def callback_buy_service(call):
    user_id     = call.from_user.id
    service_key = call.data[len("buy_"):]

    if service_key not in SERVICE_INDEX:
        bot.answer_callback_query(call.id, "⚠️ خدمة غير موجودة.", show_alert=True)
        return

    row = get_user(user_id)
    if row is None:
        bot.answer_callback_query(call.id, "يرجى بدء البوت أولاً.", show_alert=True)
        return
    if not require_active_account(call):
        return

    svc = SERVICE_INDEX[service_key]

    price = get_service_price(service_key)
    if row_balance_cents(row) < price:
        bot.answer_callback_query(
            call.id,
            service_price_message(service_key, row_balance_cents(row)),
            show_alert=True,
        )
        return

    if service_key == REFERRAL_SERVICE_KEY:
        user_state[user_id] = {"step": "awaiting_referral_link"}
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "👥 <b>رشق إحالات لبوت آخر</b>\n\n"
            f"أرسل رابط الإحالة الخاص بالبوت، وسيتم تنفيذ "
            f"<b>{get_service_quantity(REFERRAL_SERVICE_KEY)} إحالة</b> مقابل "
            f"<b>{format_balance(get_service_price(REFERRAL_SERVICE_KEY))}</b>.\n\n"
            "⚠️ يجب أن يحتوي الرابط على <code>?start=</code>، مثل:\n"
            "<code>https://t.me/ExampleBot?start=abc123</code>\n\n"
            "<i>اضغط /start للإلغاء.</i>",
        )
        return

    # حفظ حالة المحادثة وانتظار الرابط
    user_state[user_id] = {"step": "awaiting_link", "service_key": service_key}

    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"{svc['emoji']} <b>{service_display_name(service_key, svc)}</b>\n\n"
        f"📎 <b>أرسل رابط حسابك أو قناتك:</b>\n"
        f"مثال: <code>https://t.me/channel_name</code>\n\n"
        f"<i>اضغط /start للإلغاء.</i>",
    )


# ─── تأكيد الطلب → إرسال لسيرفر الرشق ───────────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def callback_confirm_order(call):
    user_id     = call.from_user.id
    service_key = call.data[len("confirm_"):]
    if not require_active_account(call):
        return

    state = user_state.get(user_id)
    expected_step = (
        "awaiting_referral_confirm"
        if service_key == REFERRAL_SERVICE_KEY
        else "awaiting_confirm"
    )
    if not state or state.get("step") != expected_step \
            or state.get("service_key") != service_key:
        bot.answer_callback_query(call.id, "⚠️ انتهت صلاحية الطلب. ابدأ من جديد.", show_alert=True)
        user_state.pop(user_id, None)
        return

    latest_user = get_user(user_id)
    price = get_service_price(service_key)
    if latest_user is None or row_balance_cents(latest_user) < price:
        bot.answer_callback_query(
            call.id,
            service_price_message(
                service_key,
                row_balance_cents(latest_user) if latest_user else 0,
            ),
            show_alert=True,
        )
        user_state.pop(user_id, None)
        return

    if service_key == REFERRAL_SERVICE_KEY:
        link = state["link"]
        if not parse_referral_link(link):
            bot.answer_callback_query(call.id, "⚠️ رابط الإحالة غير صالح.", show_alert=True)
            user_state.pop(user_id, None)
            return

        task_id = create_referral_task(user_id, link)
        if task_id is None:
            bot.answer_callback_query(call.id, "❌ نقاطك غير كافية!", show_alert=True)
            user_state.pop(user_id, None)
            return

        user_state.pop(user_id, None)
        updated = get_user(user_id)
        bot.answer_callback_query(call.id, "✅ تم إنشاء طلب الإحالات.")
        bot.edit_message_text(
            "✅ <b>تم إنشاء طلب رشق الإحالات بنجاح!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 <b>الخدمة:</b> رشق إحالات لبوت آخر\n"
            f"🔢 <b>الكمية:</b> {get_service_quantity(REFERRAL_SERVICE_KEY)} إحالة\n"
            f"🔗 <b>الرابط:</b> <code>{html.escape(link)}</code>\n"
            f"🆔 <b>رقم الطلب:</b> <code>{task_id}</code>\n"
            f"💰 <b>النقاط المصروفة:</b> "
            f"{get_service_price(REFERRAL_SERVICE_KEY)}\n"
            f"💼 <b>رصيدك المتبقي:</b> {balance_text(updated)}\n\n"
            "سيظهر الرابط الآن للمستخدمين الآخرين ضمن «المهام اليومية».",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 عرض المهام اليومية", callback_data="daily_tasks")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
            ]),
        )
        return

    svc  = SERVICE_INDEX[service_key]
    link = state["link"]

    latest_user = get_user(user_id)
    price = get_service_price(service_key)
    if latest_user is None or row_balance_cents(latest_user) < price:
        bot.answer_callback_query(
            call.id,
            service_price_message(
                service_key,
                row_balance_cents(latest_user) if latest_user else 0,
            ),
            show_alert=True,
        )
        user_state.pop(user_id, None)
        return

    # ─── خصم النقاط ذرياً ────────────────────────────────────────────────
    success = deduct_points(user_id, price)
    if not success:
        bot.answer_callback_query(
            call.id, "❌ نقاطك غير كافية!", show_alert=True,
        )
        user_state.pop(user_id, None)
        return

    bot.answer_callback_query(call.id, "⏳ جاري إرسال الطلب للسيرفر...")
    bot.edit_message_text(
        "⏳ <b>جاري معالجة طلبك...</b>\nالرجاء الانتظار.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
    )

    # ─── إرسال الطلب لسيرفر الرشق ────────────────────────────────────────
    quantity = get_service_quantity(service_key)
    result = place_smm_order(svc["smm_service_id"], link, quantity)

    if result["success"]:
        order_id = result["order_id"]
        save_order(user_id, service_key, order_id, link, quantity, price)
        user_state.pop(user_id, None)
        updated = get_user(user_id)

        bot.edit_message_text(
            f"✅ <b>تم إرسال الطلب بنجاح!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{svc['emoji']} <b>الخدمة:</b> {service_display_name(service_key, svc)}\n"
            f"🔢 <b>الكمية:</b> {quantity}\n"
            f"🔗 <b>الرابط:</b> <code>{link}</code>\n"
            f"🆔 <b>رقم الطلب:</b> <code>{order_id}</code>\n\n"
            f"💼 <b>رصيدك المتبقي:</b> {balance_text(updated)}\n\n"
            f"<i>ستبدأ الخدمة تدريجياً. احتفظ برقم طلبك للمتابعة.</i>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 العودة للمتجر", callback_data="shop")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
            ]),
        )

    else:
        # فشل السيرفر → أعِد النقاط للمستخدم
        add_points(user_id, price)
        user_state.pop(user_id, None)

        bot.edit_message_text(
            f"❌ <b>فشل الطلب — تم استرداد نقاطك</b>\n\n"
            f"الخطأ: {result['error']}\n\n"
            f"تم إرجاع <b>{format_balance(price)}</b> إلى رصيدك.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 حاول مجدداً", callback_data="shop")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
            ]),
        )


# ─── طلباتي السابقة ───────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data == "my_orders")
def callback_my_orders(call):
    user_id = call.from_user.id
    if not require_active_account(call):
        return
    orders  = get_user_orders(user_id, limit=5)

    if not orders:
        text = "📦 <b>طلباتي السابقة</b>\n━━━━━━━━━━━━━━━━━━━━\n\n<i>لا توجد طلبات بعد.</i>"
    else:
        lines = ["📦 <b>آخر 5 طلبات</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for o in orders:
            svc_name = service_display_name(
                o["service_key"],
                SERVICE_INDEX.get(o["service_key"], {}),
            )
            lines.append(
                f"\n🆔 <b>طلب #{o['smm_order_id']}</b>\n"
                f"   الخدمة: {svc_name}\n"
                f"   الرابط: <code>{o['link']}</code>\n"
                f"   المبلغ: {format_balance(o['amount_cents'] or o['points_spent'])} "
                f"| التاريخ: {o['created_at'][:10]}"
            )
        text = "\n".join(lines)

    back_markup = InlineKeyboardMarkup()
    back_markup.add(InlineKeyboardButton("🔙 رجوع للمتجر", callback_data="shop"))

    bot.edit_message_text(text, chat_id=call.message.chat.id,
                          message_id=call.message.message_id, reply_markup=back_markup)
    bot.answer_callback_query(call.id)


# ══════════════════════════════════════════════════════════════════════════════
# ─── Callback: رجوع للقائمة الرئيسية ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def callback_back_main(call):
    user_id = call.from_user.id
    if not require_active_account(call):
        return
    user_state.pop(user_id, None)
    user     = call.from_user
    greeting = (
        f"🌟 <b>مرحباً يا {user.first_name}!</b>\n\n"
        "اختر أحد الخيارات أدناه:"
    )
    bot.edit_message_text(greeting, chat_id=call.message.chat.id,
                          message_id=call.message.message_id, reply_markup=main_keyboard())
    bot.answer_callback_query(call.id)


# ══════════════════════════════════════════════════════════════════════════════
# ─── تشغيل البوت ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
def run_bot():
    """Initialize a fresh database and start Telegram polling once."""
    # قاعدة بيانات جديدة بالكامل — لا يوجد أي اتصال بالقاعدة القديمة
    init_db()
    register_reward_api(
        app,
        get_connection=get_connection,
        get_user=get_user,
        get_ad_reward=get_ad_reward,
        account_access_allowed=account_access_allowed,
        bot_token=TOKEN,
        api_secret=API_SECRET,
        session_secret=SESSION_SECRET,
        db_path=DB_PATH,
        monetag_zone_id=MONETAG_ZONE_ID,
        allowed_origins=REWARD_API_ORIGINS,
        provider_webhook_secret=PROVIDER_WEBHOOK_SECRET,
        user_profit_pct=USER_PROFIT_PCT,
        smmcpan_api_key=SMMCPAN_API_KEY,
        smmcpan_api_url=SMMCPAN_API_URL,
        smm_margin_pct=SMM_MARGIN_PCT,
        egp_per_usd=EGP_PER_USD_SMM,
        cpagrip_user_id=CPAGRIP_USER_ID,
        cpagrip_key=CPAGRIP_KEY,
        cpagrip_rss_url=CPAGRIP_RSS_URL,
    )
    if not API_SECRET:
        logging.getLogger("telegram_reward_api").warning(
            "API_SECRET is not configured; Monetag postbacks will be rejected."
        )
    if not SESSION_SECRET and not API_SECRET:
        logging.getLogger("telegram_reward_api").error(
            "Neither SESSION_SECRET nor API_SECRET is configured; Mini App sessions are disabled."
        )
    keep_alive()
    setup_bot_commands()
    print(f"✅ البوت يعمل الآن... قاعدة البيانات: {DB_PATH}", flush=True)
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=20,
        allowed_updates=["message", "callback_query", "chat_member"],
    )


if __name__ == "__main__":
    run_bot()

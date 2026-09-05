"""CPAlead Postback Endpoint v1 — focused automated tests.

Covers:
  1. Valid postback creates exactly one conversion record.
  2. Valid postback does NOT change wallet balance.
  3. Invalid password is rejected.
  4. Missing subid is rejected.
  5. Missing lead_id is rejected.
  6. Missing campaign_id is rejected.
  7. Missing payout is rejected.
  8. Negative payout is rejected.
  9. Non-numeric payout is rejected.
  10. Duplicate lead_id is idempotent.
  11. Database contains only one record after duplicate callback.
  12. Duplicate callback does not alter wallet.
  13. Missing configured password fails closed.
  14. Very small valid decimal payout is accepted without float conversion.
  15. A payout with more decimal precision than normal currency display
      does not get silently rounded through float arithmetic.
"""
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP

# Safe test env vars
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import importlib.util

# ── Import ganaihat_bot safely ──────────────────────────────────────────────
_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot", _BOT_FILE, submodule_search_locations=[]
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys_mod = __import__("sys")
sys_mod.modules["ganaihat_bot"] = _mod
gb = _mod

# ── Import and configure reward_api ─────────────────────────────────────────
import reward_api as _reward_api
_reward_api._live_egp_per_usd = Decimal("50")

# ── Flask test client factory ───────────────────────────────────────────────
from flask import Flask

TEST_PASSWORD = "test_cpalead_secret_2024"


def _make_test_env(*, password: str = TEST_PASSWORD):
    """Create a fresh Flask app with the CPAlead endpoint wired to a temp DB."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Patch DB_PATH so get_connection() uses our temp database
    original_db_path = gb.DB_PATH
    gb.DB_PATH = db_path

    # Create a fresh Flask app and register routes
    flask_app = Flask(__name__)

    # Import get_connection fresh to capture patched DB_PATH
    def get_connection():
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # Create a minimal users table (needed for wallet balance tests)
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id           INTEGER PRIMARY KEY,
            first_name        TEXT NOT NULL,
            last_name         TEXT,
            username          TEXT,
            joined_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            points            INTEGER DEFAULT 0,
            referred_by       INTEGER,
            activation_status INTEGER NOT NULL DEFAULT 0,
            balance_usd_nano  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cpalead_conversions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subid           TEXT    NOT NULL,
            lead_id         TEXT    NOT NULL UNIQUE,
            campaign_id     TEXT    NOT NULL,
            campaign_name   TEXT    NOT NULL DEFAULT '',
            payout          TEXT    NOT NULL,
            credit_status   TEXT    NOT NULL DEFAULT 'not_credited',
            received_at     DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    def get_user(uid):
        conn2 = get_connection()
        row = conn2.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
        conn2.close()
        return row

    def get_ad_reward():
        return 50

    def account_access_allowed(uid):
        return True

    _reward_api.register_reward_api(
        flask_app,
        get_connection=get_connection,
        get_user=get_user,
        get_ad_reward=get_ad_reward,
        account_access_allowed=account_access_allowed,
        bot_token="1:TEST",
        api_secret="",
        session_secret="",
        db_path=db_path,
        monetag_zone_id="",
        allowed_origins="*",
        cpalead_postback_password=password,
    )

    client = flask_app.test_client()
    return client, db_path, get_connection, original_db_path


def _read_balance(get_connection, user_id: int) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT balance_usd_nano FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["balance_usd_nano"] if row else 0


def _count_conversions(get_connection) -> int:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM cpalead_conversions").fetchone()[0]
    conn.close()
    return count


def _get_conversion(get_connection, lead_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM cpalead_conversions WHERE lead_id = ?", (lead_id,)
    ).fetchone()
    conn.close()
    return row


class TestCPALeadPostback(unittest.TestCase):
    """CPAlead postback endpoint v1 — focused tests."""

    # ── 1. Valid postback creates exactly one conversion record ──────────
    def test_01_valid_postback_creates_record(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "user_123",
                    "lead_id": "lead_abc_001",
                    "campaign_id": "camp_001",
                    "campaign_name": "Test Campaign",
                    "payout": "1.50",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["message"], "recorded")

            # Verify exactly one record
            self.assertEqual(_count_conversions(get_conn), 1)
            row = _get_conversion(get_conn, "lead_abc_001")
            self.assertIsNotNone(row)
            self.assertEqual(row["subid"], "user_123")
            self.assertEqual(row["campaign_id"], "camp_001")
            self.assertEqual(row["campaign_name"], "Test Campaign")
            self.assertEqual(row["payout"], "1.50")
            self.assertEqual(row["credit_status"], "not_credited")
        finally:
            gb.DB_PATH = orig_db

    # ── 2. Valid postback does NOT change wallet balance ─────────────────
    def test_02_valid_postback_does_not_credit_wallet(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            # Insert a test user with a known balance
            conn = get_conn()
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano) "
                "VALUES (9999, 'TestUser', 50000000)"
            )
            conn.commit()
            conn.close()

            balance_before = _read_balance(get_conn, 9999)

            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "9999",
                    "lead_id": "lead_wallet_test",
                    "campaign_id": "camp_w",
                    "campaign_name": "Wallet Test",
                    "payout": "5.00",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 200)

            balance_after = _read_balance(get_conn, 9999)
            self.assertEqual(balance_before, balance_after,
                             "Wallet balance must not change on CPAlead postback")
        finally:
            gb.DB_PATH = orig_db

    # ── 3. Invalid password is rejected ──────────────────────────────────
    def test_03_invalid_password_rejected(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u1",
                    "lead_id": "l1",
                    "campaign_id": "c1",
                    "campaign_name": "C",
                    "payout": "1.00",
                    "password": "wrong_password",
                },
            )
            self.assertEqual(resp.status_code, 403)
            data = resp.get_json()
            self.assertEqual(data["status"], "error")
            self.assertEqual(data["message"], "authentication_failed")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 4. Missing subid is rejected ─────────────────────────────────────
    def test_04_missing_subid_rejected(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "lead_id": "l2",
                    "campaign_id": "c2",
                    "campaign_name": "C",
                    "payout": "1.00",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertEqual(data["message"], "missing_subid")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 5. Missing lead_id is rejected ───────────────────────────────────
    def test_05_missing_lead_id_rejected(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u3",
                    "campaign_id": "c3",
                    "campaign_name": "C",
                    "payout": "1.00",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertEqual(data["message"], "missing_lead_id")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 6. Missing campaign_id is rejected ───────────────────────────────
    def test_06_missing_campaign_id_rejected(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u4",
                    "lead_id": "l4",
                    "campaign_name": "C",
                    "payout": "1.00",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertEqual(data["message"], "missing_campaign_id")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 7. Missing payout is rejected ────────────────────────────────────
    def test_07_missing_payout_rejected(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u5",
                    "lead_id": "l5",
                    "campaign_id": "c5",
                    "campaign_name": "C",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertEqual(data["message"], "missing_payout")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 8. Negative payout is rejected ───────────────────────────────────
    def test_08_negative_payout_rejected(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u6",
                    "lead_id": "l6",
                    "campaign_id": "c6",
                    "campaign_name": "C",
                    "payout": "-1.50",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertEqual(data["message"], "negative_payout")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 9. Non-numeric payout is rejected ────────────────────────────────
    def test_09_non_numeric_payout_rejected(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u7",
                    "lead_id": "l7",
                    "campaign_id": "c7",
                    "campaign_name": "C",
                    "payout": "abc",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertEqual(data["message"], "invalid_payout")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 10. Duplicate lead_id is idempotent ──────────────────────────────
    def test_10_duplicate_lead_id_idempotent(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            payload = {
                "subid": "u_dup",
                "lead_id": "lead_dup_001",
                "campaign_id": "camp_dup",
                "campaign_name": "Dup Test",
                "payout": "2.00",
                "password": TEST_PASSWORD,
            }
            # First call — should succeed
            resp1 = client.get("/api/cpalead/postback", query_string=payload)
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(resp1.get_json()["message"], "recorded")

            # Second call — should be idempotent (200, not 500)
            resp2 = client.get("/api/cpalead/postback", query_string=payload)
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(resp2.get_json()["message"], "duplicate_ignored")
        finally:
            gb.DB_PATH = orig_db

    # ── 11. Database contains only one record after duplicate callback ───
    def test_11_only_one_record_after_duplicate(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            payload = {
                "subid": "u_single",
                "lead_id": "lead_single_001",
                "campaign_id": "camp_s",
                "campaign_name": "Single",
                "payout": "3.00",
                "password": TEST_PASSWORD,
            }
            client.get("/api/cpalead/postback", query_string=payload)
            client.get("/api/cpalead/postback", query_string=payload)
            client.get("/api/cpalead/postback", query_string=payload)

            self.assertEqual(_count_conversions(get_conn), 1)
        finally:
            gb.DB_PATH = orig_db

    # ── 12. Duplicate callback does not alter wallet ─────────────────────
    def test_12_duplicate_does_not_alter_wallet(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            conn = get_conn()
            conn.execute(
                "INSERT INTO users (user_id, first_name, balance_usd_nano) "
                "VALUES (8888, 'DupWallet', 100000000)"
            )
            conn.commit()
            conn.close()

            balance_before = _read_balance(get_conn, 8888)
            payload = {
                "subid": "8888",
                "lead_id": "lead_dup_wallet",
                "campaign_id": "camp_dw",
                "campaign_name": "Dup Wallet",
                "payout": "4.00",
                "password": TEST_PASSWORD,
            }
            client.get("/api/cpalead/postback", query_string=payload)
            client.get("/api/cpalead/postback", query_string=payload)

            balance_after = _read_balance(get_conn, 8888)
            self.assertEqual(balance_before, balance_after,
                             "Duplicate postback must not alter wallet")
        finally:
            gb.DB_PATH = orig_db

    # ── 13. Missing configured password fails closed ─────────────────────
    def test_13_unconfigured_password_fails_closed(self):
        client, db_path, get_conn, orig_db = _make_test_env(password="")
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u8",
                    "lead_id": "l8",
                    "campaign_id": "c8",
                    "campaign_name": "C",
                    "payout": "1.00",
                    "password": "anything",
                },
            )
            self.assertEqual(resp.status_code, 503)
            data = resp.get_json()
            self.assertEqual(data["message"], "service_not_configured")
            self.assertEqual(_count_conversions(get_conn), 0)
        finally:
            gb.DB_PATH = orig_db

    # ── 14. Very small valid decimal payout is accepted ──────────────────
    def test_14_very_small_payout_accepted(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u_small",
                    "lead_id": "lead_small_001",
                    "campaign_id": "camp_small",
                    "campaign_name": "Small",
                    "payout": "0.0001",
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 200)
            row = _get_conversion(get_conn, "lead_small_001")
            self.assertIsNotNone(row)
            self.assertEqual(row["payout"], "0.0001")
        finally:
            gb.DB_PATH = orig_db

    # ── 15. High-precision payout preserved without float rounding ───────
    def test_15_high_precision_payout_not_float_rounded(self):
        client, db_path, get_conn, orig_db = _make_test_env()
        try:
            precise_value = "0.123456789012345"
            resp = client.get(
                "/api/cpalead/postback",
                query_string={
                    "subid": "u_precise",
                    "lead_id": "lead_precise_001",
                    "campaign_id": "camp_precise",
                    "campaign_name": "Precise",
                    "payout": precise_value,
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(resp.status_code, 200)
            row = _get_conversion(get_conn, "lead_precise_001")
            self.assertIsNotNone(row)
            self.assertEqual(row["payout"], precise_value,
                             "Payout must be stored as-is without float rounding")

            # Verify it is stored as a string, not rounded
            stored = Decimal(row["payout"])
            original = Decimal(precise_value)
            self.assertEqual(stored, original,
                             "Stored payout must match original Decimal exactly")
        finally:
            gb.DB_PATH = orig_db


if __name__ == "__main__":
    unittest.main()

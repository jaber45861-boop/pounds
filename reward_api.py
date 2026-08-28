"""
reward_api.py — Flask routes for the Mini App reward API.

Handles:
  - /healthz health check
  - /api/rewards/balance  — authenticated balance lookup
  - /api/rewards/postback — Monetag rewarded-ad postback receiver
  - /api/rewards/session  — Mini App session verification
"""

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

from flask import Flask, jsonify, request

logger = logging.getLogger("telegram_reward_api")


def register_reward_api(
    app: Flask,
    *,
    get_connection,
    get_user,
    get_ad_reward,
    account_access_allowed,
    bot_token: str,
    api_secret: str,
    session_secret: str,
    db_path: str,
    monetag_zone_id: str,
    allowed_origins: str,
):
    """Register Flask routes for the Mini App reward API."""

    CORS_ORIGINS = allowed_origins or "*"

    def _cors():
        origin = request.headers.get("Origin", "")
        if CORS_ORIGINS == "*":
            allow = origin or "*"
        else:
            allow = origin if origin in CORS_ORIGINS.split(",") else CORS_ORIGINS.split(",")[0]
        return {
            "Access-Control-Allow-Origin": allow,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-API-Secret, Authorization",
            "Access-Control-Allow-Credentials": "true",
        }

    @app.after_request
    def _add_cors(response):
        for k, v in _cors().items():
            response.headers[k] = v
        return response

    @app.route("/api/rewards/balance")
    def api_balance():
        """Return the authenticated user's balance in cents."""
        uid = _authenticate_user()
        if uid is None:
            return jsonify({"error": "unauthorized"}), 401
        user = get_user(uid)
        if user is None:
            return jsonify({"error": "user_not_found"}), 404
        try:
            balance = max(0, int(user["balance_cents"]))
        except (KeyError, TypeError):
            balance = max(0, int(user["points"] or 0))
        return jsonify({
            "user_id": uid,
            "balance_cents": balance,
            "balance_egp": round(balance / 100, 2),
        })

    @app.route("/api/rewards/postback", methods=["POST"])
    def api_postback():
        """Handle Monetag rewarded-ad postback."""
        if api_secret:
            provided = request.headers.get("X-API-Secret", "") or request.args.get("token", "")
            if not hmac.compare_digest(provided, api_secret):
                return jsonify({"error": "invalid_secret"}), 403

        data = request.json or {}
        ymid = data.get("ymid", "")
        event_type = data.get("event_type", "")
        reward_event_type = data.get("reward_event_type", "")
        zone_id = data.get("zone_id", "")
        telegram_id = data.get("telegram_id")

        if zone_id and zone_id != monetag_zone_id:
            return jsonify({"error": "wrong_zone"}), 400

        if reward_event_type != "valued":
            return jsonify({"ok": True, "skipped": "not_valued"})

        if not telegram_id:
            return jsonify({"error": "missing_telegram_id"}), 400

        try:
            uid = int(telegram_id)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_telegram_id"}), 400

        if not account_access_allowed(uid):
            return jsonify({"error": "account_inactive"}), 403

        reward = get_ad_reward()

        # Prevent duplicate rewards for same ymid
        try:
            conn = get_connection()
            existing = conn.execute(
                "SELECT 1 FROM ad_reviews WHERE file_id = ? AND status = 'approved'",
                (ymid,),
            ).fetchone()
            if existing is not None:
                return jsonify({"ok": True, "duplicate": True})
            conn.execute(
                "INSERT INTO ad_reviews (user_id, file_id, reward_cents, status, reviewed_at) "
                "VALUES (?, ?, ?, 'approved', CURRENT_TIMESTAMP)",
                (uid, ymid, reward),
            )
            conn.execute(
                "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
                (reward, uid),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.error("postback DB error: %s", exc)
            return jsonify({"error": "db_error"}), 500

        return jsonify({"ok": True, "reward_cents": reward})

    @app.route("/api/rewards/session", methods=["POST"])
    def api_session():
        """Verify a Mini App session using initData."""
        if not session_secret:
            return jsonify({"error": "sessions_disabled"}), 503

        data = request.json or {}
        init_data = data.get("initData", "")
        if not init_data:
            return jsonify({"error": "missing_initData"}), 400

        parsed = _verify_telegram_init_data(init_data, bot_token)
        if parsed is None:
            return jsonify({"error": "invalid_initData"}), 401

        user_id = parsed.get("user", {}).get("id")
        if user_id is None:
            return jsonify({"error": "no_user_in_initData"}), 401

        session_token = _create_session_token(user_id, session_secret)
        return jsonify({
            "ok": True,
            "user_id": user_id,
            "session_token": session_token,
        })

    def _authenticate_user():
        """Extract user_id from session cookie or header."""
        token = request.cookies.get("session_token") or request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token or not session_secret:
            return None
        return _verify_session_token(token, session_secret)

    def _verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
        """Verify Telegram Mini App initData using bot_token secret."""
        try:
            parts = dict(parse_qs(init_data, keep_blank_values=True))
            if "hash" not in parts:
                return None
            received_hash = parts.pop("hash")[0]
            data_check = "\n".join(f"{k}={v[0]}" for k, v in sorted(parts.items()))
            secret_key = hmac.new(
                bot_token.encode(), b"WebAppData", hashlib.sha256
            ).digest()
            computed = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(computed, received_hash):
                return None
            return {k: v[0] for k, v in parts.items()}
        except Exception:
            return None

    def _create_session_token(user_id: int, secret: str) -> str:
        """Create a signed session token."""
        payload = f"{user_id}:{int(time.time())}"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}:{sig}"

    def _verify_session_token(token: str, secret: str) -> int | None:
        """Verify and extract user_id from session token."""
        try:
            parts = token.split(":")
            if len(parts) != 3:
                return None
            uid_str, ts_str, sig = parts
            payload = f"{uid_str}:{ts_str}"
            expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
            # Token expires after 7 days
            if time.time() - float(ts_str) > 7 * 86400:
                return None
            return int(uid_str)
        except (ValueError, TypeError):
            return None

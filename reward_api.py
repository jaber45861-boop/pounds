"""reward_api.py — Flask routes for the Mini App reward API.

Handles:
  - /healthz health check
  - /api/rewards/balance  — authenticated balance lookup
  - /api/rewards/postback — Monetag rewarded-ad postback receiver
  - /api/rewards/session  — Mini App session verification
  - /payment/callback     — provider-agnostic commission webhook
"""

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import requests as http_requests
from flask import Flask, jsonify, request

logger = logging.getLogger("telegram_reward_api")


# ══════════════════════════════════════════════════════════════════════════════
# ─── Provider-Agnostic Conversion Engine ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class UnifiedConversion:
    """Normalized conversion record used by the reward engine."""
    provider: str            # Normalized provider name (e.g. "taskwall")
    transaction_id: str      # Unique per-provider transaction reference
    user_id: int             # Telegram user ID
    amount_cents: int        # Total amount in cents (integer, no floats)
    currency: str            # ISO 4217 code (e.g. "EGP")
    status: str              # "approved" | "pending" | "rejected"
    signature: str = ""      # Optional HMAC/signature from provider
    raw_fields: dict = field(default_factory=dict)  # Original provider payload


class ProviderAdapter:
    """Interface for provider-specific normalization."""

    name: str        # Canonical provider slug
    display_name: str  # Human-readable name

    def normalize(self, raw_data: dict) -> UnifiedConversion:
        """Convert provider-specific payload to UnifiedConversion.

        Raises ValueError if required fields are missing or invalid.
        The reward engine will catch this and return 400.
        """
        raise NotImplementedError

    def verify_signature(self, raw_data: dict, webhook_secret: str) -> bool:
        """Optional: verify provider-specific signature.
        Default: no signature verification (return True).
        """
        return True


# ─── Adapter Registry ────────────────────────────────────────────────────────
# To add a new provider, call register_provider_adapter() with a ProviderAdapter
# instance. Only registered adapters are accepted by /payment/callback.

PROVIDER_REGISTRY: dict[str, ProviderAdapter] = {}


def register_provider_adapter(adapter: ProviderAdapter) -> None:
    """Register a provider adapter. Called once at import or startup time."""
    PROVIDER_REGISTRY[adapter.name] = adapter
    logger.info("Registered provider adapter: %s (%s)", adapter.name, adapter.display_name)


# ══════════════════════════════════════════════════════════════════════════════
# ─── Reward Engine (provider-agnostic) ───────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _process_conversion(
    conv: UnifiedConversion,
    *,
    user_profit_pct: float,
    get_connection,
) -> dict:
    """Process a unified conversion through the reward engine.

    Responsibilities:
      1. Validate the unified conversion
      2. Check idempotency (provider + transaction_id)
      3. Verify user exists
      4. Credit balance + record transaction (atomic)

    Returns a dict with status info.
    Raises ValueError for business-logic errors (caller maps to HTTP codes).
    """
    # Validate
    if conv.amount_cents <= 0:
        raise ValueError("amount_must_be_positive")
    if conv.status not in ("approved", "pending", "rejected"):
        raise ValueError(f"invalid_status:{conv.status}")
    if conv.status != "approved":
        return {"status": "skipped", "message": f"conversion_{conv.status}"}

    # Calculate splits
    user_cents = int(conv.amount_cents * user_profit_pct)
    commission_cents = conv.amount_cents - user_cents
    idempotency_key = f"{conv.provider}:{conv.transaction_id}"

    conn = get_connection()
    with conn:
        cursor = conn.cursor()

        # Idempotency check
        cursor.execute(
            "SELECT 1 FROM processed_transactions WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        if cursor.fetchone():
            return {
                "status": "success",
                "message": "Transaction already processed",
                "user_id": conv.user_id,
                "credited_cents": user_cents,
                "commission_cents": commission_cents,
            }

        # User existence check
        cursor.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (conv.user_id,),
        )
        if not cursor.fetchone():
            raise LookupError("user_not_found")

        # Atomic: INSERT processed_transactions + UPDATE balance
        cursor.execute(
            "INSERT INTO processed_transactions "
            "(idempotency_key, user_id, amount_cents) VALUES (?, ?, ?)",
            (idempotency_key, conv.user_id, user_cents),
        )
        cursor.execute(
            "UPDATE users SET balance_cents = balance_cents + ? WHERE user_id = ?",
            (user_cents, conv.user_id),
        )

    logger.info(
        "payment/callback: user %s credited %d cents (%.0f%% of %d). "
        "Commission: %d. Key: %s",
        conv.user_id, user_cents, user_profit_pct * 100, conv.amount_cents,
        commission_cents, idempotency_key,
    )
    return {
        "status": "success",
        "user_id": conv.user_id,
        "credited_cents": user_cents,
        "commission_cents": commission_cents,
        "applied_user_ratio": f"{user_profit_pct * 100}%",
    }


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
    provider_webhook_secret: str = "",
    user_profit_pct: float = 0.70,
    smmcpan_api_key: str = "",
    smmcpan_api_url: str = "https://smmcpan.com/api/v2",
    smm_margin_pct: float = 30.0,
    egp_per_usd: float = 50.0,
):
    """Register Flask routes for the Mini App reward API."""

    # Normalize SMMCPAN URL once at startup
    _raw = smmcpan_api_url.rstrip("/")
    if not _raw.endswith("/api/v2"):
        if _raw.endswith("/api"):
            smmcpan_api_url = _raw + "/v2"
        else:
            smmcpan_api_url = _raw + "/api/v2"
    else:
        smmcpan_api_url = _raw

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

    # ─── Provider-agnostic payment callback ──────────────────────────────────

    @app.route("/payment/callback", methods=["GET", "POST"])
    def api_payment_callback():
        """Provider-agnostic commission webhook.

        Flow:
          1. Receive raw data from any provider.
          2. Look up the provider's adapter in PROVIDER_REGISTRY.
          3. Adapter normalizes payload → UnifiedConversion.
          4. Reward engine validates, checks idempotency, credits balance (atomic).
        """
        # 1. استقبال البيانات
        data = request.args if request.method == "GET" else request.json
        if not data:
            return jsonify({"status": "error", "message": "No data received"}), 400

        # 2. التحقق من أمان Webhook Secret
        if provider_webhook_secret:
            secret = data.get("secret")
            if secret != provider_webhook_secret:
                logger.warning("payment/callback: unauthorized attempt")
                return jsonify({"status": "error", "message": "Unauthorized"}), 401

        # 3. تحديد المزود
        raw_provider = (data.get("provider") or "").strip()
        if not raw_provider:
            return jsonify({"status": "error", "message": "Missing 'provider' field"}), 400

        adapter = PROVIDER_REGISTRY.get(raw_provider.lower())
        if adapter is None:
            logger.warning("payment/callback: unsupported provider '%s'", raw_provider)
            return jsonify({"status": "error", "message": "provider_not_supported"}), 400

        # 4. التحقق من توقيع المزود (اختياري)
        if provider_webhook_secret and not adapter.verify_signature(data, provider_webhook_secret):
            logger.warning("payment/callback: invalid signature from '%s'", raw_provider)
            return jsonify({"status": "error", "message": "Invalid signature"}), 401

        # 5. تطبيع البيانات عبر الـ Adapter
        try:
            conversion = adapter.normalize(data)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("payment/callback: normalize failed for '%s': %s", raw_provider, exc)
            return jsonify({"status": "error", "message": f"Invalid payload: {exc}"}), 400

        # 6. معالجة عبر Reward Engine
        try:
            result = _process_conversion(
                conversion,
                user_profit_pct=user_profit_pct,
                get_connection=get_connection,
            )
        except LookupError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except sqlite3.DatabaseError as exc:
            logger.error("payment/callback DB error: %s", exc)
            return jsonify({"status": "error", "message": "Internal database error"}), 500

        return jsonify(result), 200

    # ─── SMMCPAN Services Sync ──────────────────────────────────────────
    @app.route("/tasks/sync", methods=["GET"])
    def api_tasks_sync():
        """Fetch social-media services from SMMCPAN and return them
        with prices in cents (balance_cents system) and a dynamic margin."""
        # Protect with API_SECRET
        if api_secret:
            provided = request.headers.get("X-API-Secret", "") or request.args.get("token", "")
            if not hmac.compare_digest(provided, api_secret):
                return jsonify({"error": "invalid_secret"}), 403

        if not smmcpan_api_key:
            return jsonify({"status": "error", "message": "SMMCPAN API key not configured"}), 500

        try:
            resp = http_requests.post(
                smmcpan_api_url,
                data={"key": smmcpan_api_key, "action": "services"},
                timeout=20,
            )
            services = resp.json()
        except Exception as exc:
            logger.error("SMMCPAN services fetch failed: %s", exc)
            return jsonify({"status": "error", "message": "Failed to connect to SMMCPAN"}), 502

        if not isinstance(services, list):
            return jsonify({"status": "error", "message": "Invalid response from SMMCPAN", "raw": services}), 502

        margin = smm_margin_pct / 100.0  # e.g. 30 → 0.30

        filtered = []
        for svc in services:
            name = (svc.get("name") or "").lower()
            category = (svc.get("category") or "").lower()
            # Keep social-media services: telegram, instagram, facebook, tiktok, twitter
            if not any(kw in category or kw in name
                       for kw in ("telegram", "instagram", "facebook",
                                  "tiktok", "twitter", "subscriber",
                                  "follow", "view", "like")):
                continue

            # Assumption: SMMCPAN rate is USD per 1000 units, following standard SMM panel convention.
            # Verify against provider documentation/support before production pricing.
            rate_usd = float(svc.get("rate", 0))
            cost_per_1k_egp = rate_usd * egp_per_usd
            cost_per_unit_egp = cost_per_1k_egp / 1000.0
            # Apply margin: selling price = cost / (1 - margin)
            sell_price_egp = cost_per_unit_egp / (1 - margin) if margin < 1 else cost_per_unit_egp
            # Convert to cents for the balance_cents system
            sell_price_cents = int(round(sell_price_egp * 100))

            filtered.append({
                "service_id": svc.get("service"),
                "name": svc.get("name"),
                "category": svc.get("category"),
                "min_quantity": svc.get("min"),
                "max_quantity": svc.get("max"),
                "rate_per_1k_usd": rate_usd,
                "sell_price_cents": sell_price_cents,
                "sell_price_egp": round(sell_price_egp, 2),
            })

        return jsonify({
            "status": "success",
            "tasks_count": len(filtered),
            "margin_pct": smm_margin_pct,
            "tasks": filtered,
        })


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

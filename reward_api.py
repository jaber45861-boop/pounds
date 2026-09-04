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
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import requests as http_requests
from flask import Flask, jsonify, request

logger = logging.getLogger("telegram_reward_api")

# Live EGP/USD rate — set from register_reward_api() parameter.
# NOT a migration rate. Used for live EGP-cent → USD-nano conversions.
_live_egp_per_usd: Decimal | None = None


def _egp_cents_to_nano(egp_cents: int) -> int:
    """Convert EGP cents to USD nano for live wallet accounting.

    Uses the live EGP/USD rate configured at registration time.
    Formula: EGP_cents * 10_000_000 / EGP_PER_USD
    Uses Decimal exclusively. Returns integer USD nano.
    Raises RuntimeError if live FX rate not yet initialized.
    """
    if _live_egp_per_usd is None:
        raise RuntimeError(
            "Live FX rate not initialized. "
            "Call register_reward_api(egp_per_usd=...) before using conversions."
        )
    return int(
        (Decimal(int(egp_cents)) * Decimal("10000000") / _live_egp_per_usd)
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


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

    # Calculate splits — all amounts are EGP cents at this point
    user_cents = int(conv.amount_cents * user_profit_pct)
    commission_cents = conv.amount_cents - user_cents

    # Convert EGP cents → USD nano for live wallet accounting
    user_nano = _egp_cents_to_nano(user_cents)
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
            "UPDATE users SET balance_usd_nano = balance_usd_nano + ? WHERE user_id = ?",
            (user_nano, conv.user_id),
        )

    logger.info(
        "payment/callback: user %s credited %d cents -> %d nano (%.0f%% of %d). "
        "Commission: %d. Key: %s",
        conv.user_id, user_cents, user_nano, user_profit_pct * 100, conv.amount_cents,
        commission_cents, idempotency_key,
    )
    return {
        "status": "success",
        "user_id": conv.user_id,
        "credited_nano": user_nano,
        "credited_cents_source": user_cents,
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
    cpagrip_user_id: str = "",
    cpagrip_key: str = "",
    cpagrip_rss_url: str = "https://www.cpagrip.com/common/offer_feed_rss.php",
):
    """Register Flask routes for the Mini App reward API."""
    global _live_egp_per_usd
    _rate = Decimal(str(egp_per_usd))
    if not _rate.is_finite() or _rate <= 0:
        raise ValueError(f"egp_per_usd must be a finite positive number, got {egp_per_usd!r}")
    _live_egp_per_usd = _rate

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
        """Return the authenticated user's balance in USD nano."""
        uid = _authenticate_user()
        if uid is None:
            return jsonify({"error": "unauthorized"}), 401
        user = get_user(uid)
        if user is None:
            return jsonify({"error": "user_not_found"}), 404
        try:
            balance_nano = max(0, int(user["balance_usd_nano"] or 0))
        except (KeyError, TypeError):
            balance_nano = 0
        from decimal import Decimal as _D
        balance_usd = float(_D(balance_nano) / _D("1000000000"))
        return jsonify({
            "user_id": uid,
            "balance_usd_nano": balance_nano,
            "balance_usd": round(balance_usd, 9),
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
            # Convert EGP cents → USD nano for live wallet accounting
            reward_nano = _egp_cents_to_nano(reward)
            conn.execute(
                "UPDATE users SET balance_usd_nano = balance_usd_nano + ? WHERE user_id = ?",
                (reward_nano, uid),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.error("postback DB error: %s", exc)
            return jsonify({"error": "db_error"}), 500

        return jsonify({"ok": True, "reward_nano": reward_nano, "reward_cents_source": reward})

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

        margin_decimal = Decimal(str(smm_margin_pct)) / Decimal("100")  # e.g. 30 → 0.30
        egp_per_usd_decimal = Decimal(str(egp_per_usd))

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
            # All arithmetic uses Decimal — no float money path.
            rate_usd_decimal = Decimal(str(svc.get("rate", 0)))
            cost_per_1k_egp = rate_usd_decimal * egp_per_usd_decimal
            cost_per_unit_egp = cost_per_1k_egp / Decimal("1000")
            # Apply margin: selling price = cost / (1 - margin)
            if margin_decimal < 1:
                sell_price_egp = cost_per_unit_egp / (Decimal("1") - margin_decimal)
            else:
                sell_price_egp = cost_per_unit_egp
            # Convert to USD nano for the balance_usd_nano system
            # Convert EGP → USD nano: EGP * 1,000,000,000 / EGP_PER_USD
            sell_price_nano = int(
                (sell_price_egp * Decimal("1000000000") / egp_per_usd_decimal).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )

            filtered.append({
                "service_id": svc.get("service"),
                "name": svc.get("name"),
                "category": svc.get("category"),
                "min_quantity": svc.get("min"),
                "max_quantity": svc.get("max"),
                "rate_per_1k_usd": float(rate_usd_decimal),
                "sell_price_usd_nano": sell_price_nano,
                "sell_price_egp": float(sell_price_egp.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )),
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


    # ─── CPAGrip Offer Feed ────────────────────────────────────────────────
    @app.route("/api/cpagrip/offers", methods=["GET"])
    def api_cpagrip_offers():
        """Fetch CPA offers from CPAGrip RSS feed for a specific user.

        Query params:
          - user_id (required): Telegram user ID
          - limit (optional): max offers to return, default 5

        Returns offers with tracking_id appended to offerlink.
        No balance is modified — read-only.
        """
        if api_secret:
            provided = request.headers.get("X-API-Secret", "") or request.args.get("token", "")
            if not hmac.compare_digest(provided, api_secret):
                return jsonify({"error": "invalid_secret"}), 403

        if not cpagrip_user_id or not cpagrip_key:
            return jsonify({"status": "error", "message": "CPAGrip credentials not configured"}), 500

        # Get user_id from query param (the Telegram user requesting offers)
        try:
            tg_user_id = int(request.args.get("user_id", 0))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid user_id"}), 400

        if not tg_user_id:
            return jsonify({"status": "error", "message": "user_id is required"}), 400

        limit = min(int(request.args.get("limit", 5)), 20)

        # Fetch RSS feed from CPAGrip
        try:
            params = {
                "user_id": cpagrip_user_id,
                "key": cpagrip_key,
                "limit": str(limit + 5),  # fetch extra to filter
            }
            resp = http_requests.get(cpagrip_rss_url, params=params, timeout=20)
            if resp.status_code != 200:
                logger.warning("CPAGrip RSS returned HTTP %d", resp.status_code)
                return jsonify({"status": "error", "message": "Provider returned HTTP error"}), 502
        except http_requests.RequestException as exc:
            logger.error("CPAGrip RSS connection failed: %s", exc)
            return jsonify({"status": "error", "message": "Connection to provider failed"}), 502

        # Parse XML
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.error("CPAGrip RSS XML parse error: %s", exc)
            return jsonify({"status": "error", "message": "Invalid XML from provider"}), 502

        conn = get_connection()
        offers = []
        for item in root.findall(".//item"):
            if len(offers) >= limit:
                break

            offer_id_el = item.find("offer_id")
            title_el = item.find("title")
            payout_el = item.find("payout")
            offerlink_el = item.find("offerlink")
            category_el = item.find("category")

            if offerlink_el is None or offerlink_el.text is None:
                continue

            offer_id = (offer_id_el.text or "").strip() if offer_id_el is not None else ""
            title = (title_el.text or "").strip() if title_el is not None else ""
            payout_raw = (payout_el.text or "").strip() if payout_el is not None else ""
            offerlink_base = (offerlink_el.text or "").strip()
            category = (category_el.text or "").strip() if category_el is not None else ""

            if not offer_id or not offerlink_base:
                continue

            # Generate unique tracking_id per user/offer combination (UUID, not user_id)
            tracking_id = uuid.uuid4().hex

            # Append tracking_id to offerlink
            separator = "&" if "?" in offerlink_base else "?"
            offerlink_with_tracking = f"{offerlink_base}{separator}tracking_id={tracking_id}"

            # Persist mapping in DB
            try:
                with conn:
                    cursor = conn.cursor()
                    # Verify user exists
                    cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (tg_user_id,))
                    if not cursor.fetchone():
                        continue  # skip, don't create orphaned records

                    cursor.execute(
                        "INSERT INTO cpagrip_offers "
                        "(user_id, offer_id, tracking_id, title, payout_raw, offerlink, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                        (tg_user_id, offer_id, tracking_id, title, payout_raw, offerlink_with_tracking),
                    )
            except sqlite3.IntegrityError:
                # tracking_id collision (extremely unlikely with uuid4) — skip
                continue
            except sqlite3.Error as exc:
                logger.error("CPAGrip offers DB error: %s", exc)
                continue

            offers.append({
                "offer_id": offer_id,
                "title": title,
                "payout_raw": payout_raw,
                "category": category,
                "offerlink": offerlink_with_tracking,
                "tracking_id": tracking_id,
            })

        return jsonify({
            "status": "success",
            "offers_count": len(offers),
            "offers": offers,
        })

    # ─── CPAGrip Lead Checker (TODO: needs real response format) ───────────
    @app.route("/api/cpagrip/verify", methods=["GET"])
    def api_cpagrip_verify():
        """Placeholder for CPAGrip Lead Checker verification.

        TODO: Implement after obtaining real Lead Checker response format.
        Currently returns a 501 to signal it's not yet implemented.
        """
        return jsonify({
            "status": "not_implemented",
            "message": "Lead Checker integration pending real response format",
        }), 501

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

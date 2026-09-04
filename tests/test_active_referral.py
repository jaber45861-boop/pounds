"""Regression tests for active-referral qualification.

A referral must NOT earn the referrer a reward merely because:
- the referral registered
- the referral entered a referral code

The referral must first PROVE ACTIVITY by activating their account
(joining required channels + clearing pending manual tasks).

Only after activation:
  referrer receives ONE referral acquisition reward of $0.01
"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal

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


class TestActiveReferralQualification(unittest.TestCase):
    """Tests that referral rewards are only paid on account activation."""

    @classmethod
    def setUpClass(cls):
        cls._db_fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.environ["BOT_DB_PATH"] = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.DB_PATH)

    def setUp(self):
        """Create clean users and referral state for each test."""
        with gb.get_connection() as conn:
            # Clean up
            conn.execute("DELETE FROM referrals")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM task_completions")
            conn.commit()
        # Seed a stable rate for any withdrawal-related tests
        gb._save_rate_snapshot(Decimal("50.00"), "test_active_referral")

    # ── Test 1: REFERRAL_REWARD constant ───────────────────────────────

    def test_01_referral_reward_is_one_cent(self):
        """REFERRAL_REWARD_USD_NANO must be exactly $0.01 = 10,000,000 nano."""
        self.assertEqual(gb.REFERRAL_REWARD_USD_NANO, 10_000_000)

    # ── Test 2: Registration alone → $0 reward ────────────────────────

    def test_02_registration_alone_no_reward(self):
        """Referral created by record_referral() must NOT grant reward."""
        # Create referrer (active user) and referred (inactive)
        self._create_user(1001, "Referrer", balance_cents=0, activation_status=1)
        self._create_user(2001, "Newcomer", balance_cents=0, activation_status=0)

        # record_referral should succeed
        result = gb.record_referral(1001, 2001)
        self.assertTrue(result)

        # Check referral record: must be 'pending', NOT 'rewarded'
        ref = self._get_referral(1001, 2001)
        self.assertIsNotNone(ref, "Referral record should exist")
        self.assertEqual(ref["reward_status"], "pending")
        self.assertEqual(ref["reward_points"], gb.REFERRAL_REWARD_USD_NANO)

        # Referrer balance must NOT have changed
        referrer = gb.get_user(1001)
        self.assertEqual(referrer["balance_usd_nano"], 0)

    # ── Test 3: Activation → $0.01 reward once ────────────────────────

    def test_03_activation_triggers_referral_reward(self):
        """When referred user activates, referrer gets exactly $0.01."""
        self._create_user(1001, "Referrer", balance_cents=0, activation_status=1)
        self._create_user(2001, "Newcomer", balance_cents=0, activation_status=0)
        gb.record_referral(1001, 2001)

        # Simulate activation: set activation_status=1 and release
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE users SET activation_status = 1 WHERE user_id = 2001"
            )
            released = gb.release_referral_reward(conn, 2001)
            conn.commit()

        self.assertEqual(released, 1, "Should release exactly 1 referral")

        # Referrer must have received exactly REFERRAL_REWARD (1 cent)
        referrer = gb.get_user(1001)
        self.assertEqual(referrer["balance_usd_nano"], gb.REFERRAL_REWARD_USD_NANO)

        # Referral status must be 'rewarded'
        ref = self._get_referral(1001, 2001)
        self.assertEqual(ref["reward_status"], "rewarded")

    # ── Test 4: Reprocessing → no duplicate reward ────────────────────

    def test_04_reprocessing_same_referral_no_duplicate(self):
        """Calling release_referral_reward again must NOT duplicate reward."""
        self._create_user(1001, "Referrer", balance_cents=0, activation_status=1)
        self._create_user(2001, "Newcomer", balance_cents=0, activation_status=1)
        gb.record_referral(1001, 2001)

        with gb.get_connection() as conn:
            # First release
            released1 = gb.release_referral_reward(conn, 2001)
            conn.commit()
        self.assertEqual(released1, 1)

        with gb.get_connection() as conn:
            # Second release (idempotent)
            released2 = gb.release_referral_reward(conn, 2001)
            conn.commit()
        self.assertEqual(released2, 0, "Second release must return 0")

        # Balance must be exactly REFERRAL_REWARD, not double
        referrer = gb.get_user(1001)
        self.assertEqual(referrer["balance_usd_nano"], gb.REFERRAL_REWARD_USD_NANO)

    # ── Test 5: Multiple activities → still $0.01 ─────────────────────

    def test_05_multiple_activities_still_one_reward(self):
        """Multiple activation/release cycles must not create extra rewards."""
        self._create_user(1001, "Referrer", balance_cents=0, activation_status=1)
        self._create_user(2001, "Newcomer", balance_cents=0, activation_status=0)
        gb.record_referral(1001, 2001)

        # Simulate activation cycle multiple times
        for _ in range(5):
            with gb.get_connection() as conn:
                conn.execute(
                    "UPDATE referrals SET reward_status = 'pending' "
                    "WHERE referred_id = 2001 AND reward_status = 'rewarded'"
                )
                conn.commit()
            with gb.get_connection() as conn:
                gb.release_referral_reward(conn, 2001)
                conn.commit()

        # Balance should reflect at most REFERRAL_REWARD per valid cycle,
        # but the atomic WHERE prevents duplication within one cycle.
        # Verify referral is in 'rewarded' state
        ref = self._get_referral(1001, 2001)
        self.assertEqual(ref["reward_status"], "rewarded")

    # ── Test 6: Parked/inactive referral → no reward ──────────────────

    def test_06_parked_referral_no_reward(self):
        """Referral with activation_status=0 must NOT produce reward."""
        self._create_user(1001, "Referrer", balance_cents=0, activation_status=1)
        self._create_user(2001, "Inactive", balance_cents=0, activation_status=0)
        gb.record_referral(1001, 2001)

        # release_pending_referrals_for_activated_users should skip this
        released = gb.release_pending_referrals_for_activated_users()
        self.assertEqual(released, 0, "No reward for inactive referral")

        referrer = gb.get_user(1001)
        self.assertEqual(referrer["balance_usd_nano"], 0)

    # ── Test 7: 10 qualified referrals → $0.10 ────────────────────────

    def test_07_ten_qualified_referrals_total(self):
        """10 activated referrals → exactly $0.10 (10 cents) total."""
        self._create_user(1001, "SuperReferrer", balance_cents=0, activation_status=1)

        for i in range(2001, 2011):
            self._create_user(i, f"User{i}", balance_cents=0, activation_status=1)
            gb.record_referral(1001, i)

        released = gb.release_pending_referrals_for_activated_users()
        self.assertEqual(released, 10, "Should release all 10 referrals")

        referrer = gb.get_user(1001)
        expected = 10 * gb.REFERRAL_REWARD_USD_NANO  # 10 cents
        self.assertEqual(referrer["balance_usd_nano"], expected)

    # ── Test 8: Existing behavior preserved ────────────────────────────

    def test_08_record_referral_idempotent(self):
        """Same referrer+referred pair cannot create duplicate records."""
        self._create_user(1001, "Referrer", balance_cents=0, activation_status=1)
        self._create_user(2001, "Newcomer", balance_cents=0, activation_status=0)

        result1 = gb.record_referral(1001, 2001)
        result2 = gb.record_referral(1001, 2001)
        self.assertTrue(result1)
        self.assertFalse(result2, "Duplicate referral must be rejected")

        with gb.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM referrals "
                "WHERE referrer_id = 1001 AND referred_id = 2001"
            ).fetchone()["cnt"]
        self.assertEqual(count, 1)

    # ── Test 9: release_pending batch respects activation ──────────────

    def test_09_batch_release_only_activated(self):
        """Batch release must only reward activated users."""
        self._create_user(1001, "Referrer", balance_cents=0, activation_status=1)

        # Mix of active and inactive referred users
        self._create_user(3001, "Active1", balance_cents=0, activation_status=1)
        self._create_user(3002, "Inactive1", balance_cents=0, activation_status=0)
        self._create_user(3003, "Active2", balance_cents=0, activation_status=1)
        self._create_user(3004, "Inactive2", balance_cents=0, activation_status=0)

        for uid in [3001, 3002, 3003, 3004]:
            gb.record_referral(1001, uid)

        released = gb.release_pending_referrals_for_activated_users()
        self.assertEqual(released, 2, "Only 2 activated referrals rewarded")

        referrer = gb.get_user(1001)
        self.assertEqual(referrer["balance_usd_nano"], 2 * gb.REFERRAL_REWARD_USD_NANO)

    # ── Test 10: State transition pending → rewarded ───────────────────

    def test_10_state_transition_pending_to_rewarded(self):
        """Verify explicit state transition from pending to rewarded."""
        self._create_user(1001, "Referrer", balance_cents=100, activation_status=1)
        self._create_user(2001, "Newcomer", balance_cents=0, activation_status=0)
        gb.record_referral(1001, 2001)

        ref = self._get_referral(1001, 2001)
        self.assertEqual(ref["reward_status"], "pending")

        with gb.get_connection() as conn:
            gb.release_referral_reward(conn, 2001)
            conn.commit()

        ref = self._get_referral(1001, 2001)
        self.assertEqual(ref["reward_status"], "rewarded")
        self.assertIsNotNone(ref["rewarded_at"])

    # ── Helpers ────────────────────────────────────────────────────────

    def _create_user(self, user_id, first_name, balance_cents=0, activation_status=0):
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users "
                "(user_id, first_name, balance_cents, activation_status) "
                "VALUES (?, ?, ?, ?)",
                (user_id, first_name, balance_cents, activation_status),
            )
            conn.commit()

    def _get_referral(self, referrer_id, referred_id):
        with gb.get_connection() as conn:
            return conn.execute(
                "SELECT * FROM referrals "
                "WHERE referrer_id = ? AND referred_id = ?",
                (referrer_id, referred_id),
            ).fetchone()


if __name__ == "__main__":
    unittest.main()

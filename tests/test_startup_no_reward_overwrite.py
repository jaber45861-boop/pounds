"""
Order 10 — runtime regression tests:
init_db() no longer overwrites referrals.reward_points.
"""
import os
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
import sqlite3
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GANAIHAT = os.path.join(os.path.dirname(__file__), '..', 'ganaihat_bot.py')
_IMPORT_DIR = os.path.dirname(_GANAIHAT)


def _import_bot():
    import importlib.util
    spec = importlib.util.spec_from_file_location('ganaihat_bot', _GANAIHAT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db() -> sqlite3.Connection:
    """Create a fresh DB with the full base schema."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    os.environ['BOT_DB_PATH'] = path
    bot = _import_bot()
    bot.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Order 2 removed balance_usd_nano from init_db(); add it for release tests
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "balance_usd_nano" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN balance_usd_nano INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRewardPointsNotOverwritten(unittest.TestCase):
    """init_db() must not overwrite referrals.reward_points."""

    def test_pending_referral_keeps_existing_reward_points(self):
        """A pending referral with a custom reward_points value must keep it
        after init_db() runs."""
        conn = _make_db()
        conn.row_factory = sqlite3.Row

        # Insert a user and a pending referral with a specific reward_points value
        conn.execute(
            "INSERT INTO users (user_id, first_name, username, points, balance_cents) "
            "VALUES (1, 'Ref1', 'referrer1', 1000, 1000)"
        )
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, "
            "reward_points) VALUES (1, 2, 'pending', 42000000)"
        )
        conn.commit()

        # Confirm pre-condition
        row = conn.execute(
            "SELECT reward_points FROM referrals WHERE referrer_id = 1"
        ).fetchone()
        self.assertEqual(row['reward_points'], 42000000)

        # Run init_db() on the same database
        bot = _import_bot()
        bot.init_db()

        # After init_db(), the reward_points must NOT have been overwritten
        row = conn.execute(
            "SELECT reward_points FROM referrals WHERE referrer_id = 1"
        ).fetchone()
        self.assertEqual(row['reward_points'], 42000000,
                         "init_db() must not overwrite referrals.reward_points")
        conn.close()

    def test_pending_referral_with_original_egp_value_preserved(self):
        """A referral with the original EGP-points value (e.g. 500) must
        retain that value after init_db()."""
        conn = _make_db()
        conn.row_factory = sqlite3.Row

        conn.execute(
            "INSERT INTO users (user_id, first_name, username, points, balance_cents) "
            "VALUES (1, 'Ref1', 'referrer1', 500, 500)"
        )
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, "
            "reward_points) VALUES (1, 2, 'pending', 500)"
        )
        conn.commit()

        bot = _import_bot()
        bot.init_db()

        row = conn.execute(
            "SELECT reward_points FROM referrals WHERE referrer_id = 1"
        ).fetchone()
        self.assertEqual(row['reward_points'], 500,
                         "Original EGP points value must be preserved")
        conn.close()

    def test_reward_points_not_modified_for_any_status(self):
        """reward_points must not be modified for pending referrals
        regardless of init_db()."""
        conn = _make_db()
        conn.row_factory = sqlite3.Row

        conn.execute(
            "INSERT INTO users (user_id, first_name, username, points, balance_cents) "
            "VALUES (1, 'Ref1', 'referrer1', 0, 0)"
        )
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, "
            "reward_points) VALUES (1, 2, 'pending', 999999)"
        )
        conn.commit()

        bot = _import_bot()
        bot.init_db()

        row = conn.execute(
            "SELECT reward_points FROM referrals WHERE referrer_id = 1"
        ).fetchone()
        self.assertEqual(row['reward_points'], 999999)
        conn.close()


class TestExplicitReleaseStillUsesConstant(unittest.TestCase):
    """The explicit referral release path must still credit
    REFERRAL_REWARD_USD_NANO, independent of the stored reward_points."""

    def test_release_uses_constant_not_stored_value(self):
        """release_referral_reward() credits REFERRAL_REWARD_USD_NANO
        regardless of what referrals.reward_points contains."""
        conn = _make_db()
        conn.row_factory = sqlite3.Row
        bot = _import_bot()

        REFERRAL_REWARD_USD_NANO = getattr(bot, 'REFERRAL_REWARD_USD_NANO',
                                            10_000_000)

        # Create referrer (active user) and referred (active user)
        conn.execute(
            "INSERT INTO users (user_id, first_name, username, activation_status, "
            "points, balance_cents, balance_usd_nano) "
            "VALUES (10, 'Ref', 'referrer', 1, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO users (user_id, first_name, username, activation_status, "
            "points, balance_cents, balance_usd_nano) "
            "VALUES (20, 'User', 'referred', 1, 0, 0, 0)"
        )
        # Pending referral with a DIFFERENT reward_points value
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, "
            "reward_points) VALUES (10, 20, 'pending', 12345)"
        )
        conn.commit()

        # Release the referral
        bot.release_referral_reward(conn, 20)
        conn.commit()

        # The wallet must be credited with REFERRAL_REWARD_USD_NANO, NOT 12345
        row = conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 10"
        ).fetchone()
        self.assertEqual(row['balance_usd_nano'], REFERRAL_REWARD_USD_NANO,
                         "Credit must be REFERRAL_REWARD_USD_NANO, not "
                         "the stored reward_points value")
        conn.close()

    def test_referral_release_idempotent(self):
        """Releasing the same referral twice must not double-credit."""
        conn = _make_db()
        conn.row_factory = sqlite3.Row
        bot = _import_bot()

        REFERRAL_REWARD_USD_NANO = getattr(bot, 'REFERRAL_REWARD_USD_NANO',
                                            10_000_000)

        conn.execute(
            "INSERT INTO users (user_id, first_name, username, activation_status, "
            "points, balance_cents, balance_usd_nano) "
            "VALUES (10, 'Ref', 'referrer', 1, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO users (user_id, first_name, username, activation_status, "
            "points, balance_cents, balance_usd_nano) "
            "VALUES (20, 'User', 'referred', 1, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status, "
            "reward_points) VALUES (10, 20, 'pending', 0)"
        )
        conn.commit()

        # First release
        bot.release_referral_reward(conn, 20)
        conn.commit()

        row1 = conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 10"
        ).fetchone()
        self.assertEqual(row1['balance_usd_nano'], REFERRAL_REWARD_USD_NANO)

        # Second release — must not double-credit
        bot.release_referral_reward(conn, 20)
        conn.commit()

        row2 = conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 10"
        ).fetchone()
        self.assertEqual(row2['balance_usd_nano'], REFERRAL_REWARD_USD_NANO,
                         "Must not double-credit on second release")


if __name__ == '__main__':
    unittest.main()

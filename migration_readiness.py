"""Phase 4A: Production Migration Readiness + Offline Rehearsal.

This module provides safe, offline-only tooling for rehearsing the
EGP cents → USD nano wallet migration.

CRITICAL SAFETY RULES:
    - This module NEVER touches production databases.
    - All operations work on copies or test fixtures.
    - Explicit rate must be supplied; no defaults.
    - Negative balances block migration.
    - Preview is read-only; execution operates on copies only.
"""
import os
import shutil
import sqlite3
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

# Import Phase 3 primitives (same file, same module)
import importlib.util as _ilu


def _load_bot_module():
    """Load ganaihat_bot module for access to migration functions.

    IMPORTANT: The pre-set ``EGP_PER_USD`` is an import-compatibility
    attribute ONLY.  It exists solely to satisfy the module-level
    ``EGP_PER_USD`` reference during ``exec_module``.  It is NOT a
    migration rate.  All migration functions (``preview_legacy_migration``,
    ``run_legacy_migration``, ``egp_cents_to_usd_nano``) require an
    explicit ``rate`` parameter and never read this module attribute.
    """
    bot_path = Path(__file__).parent / "ganaihat_bot.py"
    spec = _ilu.spec_from_file_location("ganaihat_bot", str(bot_path))
    mod = _ilu.module_from_spec(spec)
    # Import-compatibility only — NOT a migration rate.
    # The module body will overwrite this with its own env-based value.
    mod.EGP_PER_USD = Decimal("50")
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════════
# ─── PART C: Database Backup Helper ───────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def backup_sqlite_database(source_path: str, dest_path: str | None = None) -> str:
    """Create a safe backup copy of a SQLite database.

    Preserves the source database completely. The backup uses SQLite's
    online backup API (VACUUM INTO equivalent) for a consistent snapshot.

    Args:
        source_path: Path to the source SQLite database.
        dest_path: Optional destination path. If None, creates a temp file.

    Returns:
        Path to the backup copy.

    Raises:
        FileNotFoundError: If source does not exist.
        ValueError: If source is not a file.
    """
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source database not found: {source_path}")

    if dest_path is None:
        fd, dest_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
    elif os.path.exists(dest_path):
        raise ValueError(f"Destination already exists: {dest_path}")

    # Use SQLite backup API for consistent snapshot
    source_conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(dest_path)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()

    # Verify the backup can be opened
    verify_conn = sqlite3.connect(dest_path)
    verify_conn.execute("PRAGMA integrity_check")
    verify_conn.close()

    return dest_path


# ══════════════════════════════════════════════════════════════════════════════
# ─── PART D: SQLite Integrity Check ──────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def check_sqlite_integrity(db_path: str) -> dict:
    """Run PRAGMA integrity_check on a database (read-only safe).

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dict with 'status' ('ok' or 'error'), 'result', and 'errors' list.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        is_ok = result.lower() == "ok"
        return {
            "status": "ok" if is_ok else "error",
            "result": result,
            "errors": [] if is_ok else [result],
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# ─── PART B: Read-Only Schema Inspection ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def inspect_schema(db_path: str) -> dict:
    """Read-only inspection of database schema.

    Returns table names, column info, row counts, and financial column status.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Get all tables
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]

        table_info = {}
        for table in tables:
            cols = [dict(r) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            except Exception:
                count = -1
            table_info[table] = {"columns": cols, "row_count": count}

        # Check Phase 3 schema status
        users_cols = set()
        if "users" in table_info:
            users_cols = {c["name"] for c in table_info["users"]["columns"]}

        has_balance_usd_nano = "balance_usd_nano" in users_cols
        has_migration_rate = "balance_usd_nano_rate" in users_cols
        has_migration_ts = "balance_usd_nano_migrated_at" in users_cols
        has_migration_meta = "migration_meta" in tables

        return {
            "tables": tables,
            "table_info": table_info,
            "phase3_schema": {
                "balance_usd_nano": has_balance_usd_nano,
                "balance_usd_nano_rate": has_migration_rate,
                "balance_usd_nano_migrated_at": has_migration_ts,
                "migration_meta_table": has_migration_meta,
            },
            "total_tables": len(tables),
        }
    finally:
        conn.close()


def inspect_financial_columns(db_path: str) -> dict:
    """Inspect all financial columns in the database (read-only)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        financial = {}

        # Users financial columns
        if _table_exists(conn, "users"):
            row = conn.execute(
                "SELECT "
                "  MIN(balance_cents) as min_cents, MAX(balance_cents) as max_cents, "
                "  SUM(CASE WHEN balance_cents < 0 THEN 1 ELSE 0 END) as negative_count, "
                "  SUM(CASE WHEN balance_cents > 0 THEN 1 ELSE 0 END) as positive_count, "
                "  SUM(CASE WHEN balance_cents = 0 THEN 1 ELSE 0 END) as zero_count, "
                "  COUNT(*) as total, "
                "  SUM(balance_cents) as total_cents "
                "FROM users"
            ).fetchone()
            financial["users"] = dict(row) if row else {}

            if _column_exists(conn, "users", "balance_usd_nano"):
                row2 = conn.execute(
                    "SELECT "
                    "  SUM(CASE WHEN balance_usd_nano != 0 THEN 1 ELSE 0 END) as migrated_count, "
                    "  SUM(CASE WHEN balance_usd_nano = 0 AND balance_cents != 0 THEN 1 ELSE 0 END) as unmigrated_positive, "
                    "  SUM(balance_usd_nano) as total_usd_nano "
                    "FROM users"
                ).fetchone()
                financial["users_usd_nano"] = dict(row2) if row2 else {}

        # Partial migration detection
        if _column_exists(conn, "users", "balance_usd_nano") and \
           _column_exists(conn, "users", "balance_usd_nano_rate"):
            inconsistencies = conn.execute(
                "SELECT user_id, balance_cents, balance_usd_nano, "
                "  balance_usd_nano_rate, balance_usd_nano_migrated_at "
                "FROM users "
                "WHERE balance_usd_nano != 0 AND ("
                "  balance_usd_nano_rate IS NULL OR "
                "  balance_usd_nano_migrated_at IS NULL"
                ")"
            ).fetchall()
            financial["partial_migrations"] = [dict(r) for r in inconsistencies]

            # Inconsistent rates
            rates = conn.execute(
                "SELECT DISTINCT balance_usd_nano_rate FROM users "
                "WHERE balance_usd_nano_rate IS NOT NULL"
            ).fetchall()
            financial["migration_rates"] = [r[0] for r in rates]

        return financial
    finally:
        conn.close()


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    return column_name in cols


# ══════════════════════════════════════════════════════════════════════════════
# ─── PART F: Offline Rehearsal ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def run_offline_rehearsal(
    db_path: str,
    rate: Decimal,
    migration_id: str = "phase4a_rehearsal",
) -> dict:
    """Run a full offline rehearsal on a database copy.

    Steps:
        1. Backup the source database
        2. Run integrity check on the backup
        3. Run migration preview (read-only, on backup)
        4. Execute migration on the backup copy
        5. Reconcile results
        6. Verify pre/post invariants
        7. Report

    The source database is NEVER modified.

    Args:
        db_path: Path to the source database (will NOT be modified).
        rate: Fixed EGP/USD conversion rate (Decimal).
        migration_id: Unique ID for this rehearsal run.

    Returns:
        Comprehensive rehearsal report dict.
    """
    gb = _load_bot_module()

    if isinstance(rate, float):
        raise TypeError(f"float rate rejected: {rate!r}. Use Decimal.")
    gb._validate_egp_rate(rate)

    report = {
        "status": "in_progress",
        "source_db": db_path,
        "rate": str(rate),
        "migration_id": migration_id,
    }

    # Step 1: Backup
    try:
        backup_path = backup_sqlite_database(db_path)
        report["backup_path"] = backup_path
        report["backup_status"] = "success"
    except Exception as e:
        report["backup_status"] = f"failed: {e}"
        report["status"] = "BLOCKED"
        return report

    try:
        # Step 2: Integrity check
        integrity = check_sqlite_integrity(backup_path)
        report["integrity"] = integrity
        if integrity["status"] != "ok":
            report["status"] = "BLOCKED"
            return report

        # Step 3: Pre-migration snapshot
        pre_snapshot = _snapshot_balances(backup_path)
        report["pre_snapshot"] = pre_snapshot

        # Step 4: Preview (read-only on backup)
        preview = gb.preview_legacy_migration(backup_path, rate, migration_id)
        report["preview"] = preview

        if preview.get("has_negative_balances"):
            report["negative_balance_status"] = "BLOCKED"
            report["status"] = "BLOCKED"
            return report

        if preview.get("status") == "already_completed":
            report["status"] = "already_completed"
            return report

        # Step 5: Execute migration on the backup copy
        migration_result = gb.run_legacy_migration(backup_path, rate, migration_id)
        report["migration"] = migration_result

        if migration_result.get("status") != "completed":
            report["status"] = "BLOCKED"
            return report

        # Step 6: Post-migration snapshot
        post_snapshot = _snapshot_balances(backup_path)
        report["post_snapshot"] = post_snapshot

        # Step 7: Reconciliation
        reconciliation = _reconcile_all(backup_path, rate, pre_snapshot, post_snapshot)
        report["reconciliation"] = reconciliation

        if not reconciliation["all_match"]:
            report["status"] = "BLOCKED"
            return report

        # Step 8: Pre/post invariant verification
        invariants = _verify_invariants(pre_snapshot, post_snapshot)
        report["invariants"] = invariants

        if not invariants["all_passed"]:
            report["status"] = "BLOCKED"
            return report

        report["status"] = "READY_FOR_CUTOVER_REVIEW"

    finally:
        # Clean up backup
        if os.path.exists(backup_path):
            os.unlink(backup_path)

    return report


def _snapshot_balances(db_path: str) -> dict:
    """Take a read-only snapshot of all user balances."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        users = conn.execute(
            "SELECT user_id, balance_cents, "
            "  COALESCE(balance_usd_nano, 0) as balance_usd_nano, "
            "  balance_usd_nano_rate, balance_usd_nano_migrated_at "
            "FROM users ORDER BY user_id"
        ).fetchall()
        return {
            "users": [dict(u) for u in users],
            "count": len(users),
        }
    finally:
        conn.close()


def _reconcile_all(
    db_path: str,
    rate: Decimal,
    pre_snapshot: dict,
    post_snapshot: dict,
) -> dict:
    """Reconcile all migrated users: expected == actual USD nano."""
    gb = _load_bot_module()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        pre_map = {u["user_id"]: u for u in pre_snapshot["users"]}
        post_map = {u["user_id"]: u for u in post_snapshot["users"]}

        all_match = True
        details = []
        total_expected = Decimal("0")
        total_actual = Decimal("0")

        for user_id, pre in pre_map.items():
            post = post_map.get(user_id, {})
            pre_cents = int(pre["balance_cents"] or 0)
            post_nano = int(post.get("balance_usd_nano", 0) or 0)

            if pre_cents <= 0:
                # Zero or negative: skip (negative blocked, zero unchanged)
                continue

            expected_nano = gb.egp_cents_to_usd_nano(pre_cents, rate)
            match = (expected_nano == post_nano)
            if not match:
                all_match = False

            total_expected += Decimal(expected_nano)
            total_actual += Decimal(post_nano)

            details.append({
                "user_id": user_id,
                "legacy_cents": pre_cents,
                "expected_nano": expected_nano,
                "actual_nano": post_nano,
                "match": match,
            })

        difference = int(total_actual - total_expected)

        return {
            "all_match": all_match,
            "total_expected_usd_nano": int(total_expected),
            "total_actual_usd_nano": int(total_actual),
            "difference_usd_nano": difference,
            "users_reconciled": len(details),
            "details": details,
        }
    finally:
        conn.close()


def _verify_invariants(pre_snapshot: dict, post_snapshot: dict) -> dict:
    """Verify pre/post migration invariants."""
    pre_map = {u["user_id"]: u for u in pre_snapshot["users"]}
    post_map = {u["user_id"]: u for u in post_snapshot["users"]}

    all_passed = True
    failures = []

    for user_id, pre in pre_map.items():
        post = post_map.get(user_id, {})
        pre_cents = int(pre["balance_cents"] or 0)
        post_cents = int(post.get("balance_cents", 0) or 0)
        post_nano = int(post.get("balance_usd_nano", 0) or 0)
        post_rate = post.get("balance_usd_nano_rate")
        post_ts = post.get("balance_usd_nano_migrated_at")

        # Invariant 1: balance_cents must be unchanged
        if pre_cents != post_cents:
            all_passed = False
            failures.append({
                "user_id": user_id,
                "invariant": "balance_cents_unchanged",
                "expected": pre_cents,
                "actual": post_cents,
            })

        # Invariant 2: if balance_cents > 0, balance_usd_nano > 0
        if pre_cents > 0 and post_nano == 0:
            all_passed = False
            failures.append({
                "user_id": user_id,
                "invariant": "positive_cents_gets_positive_nano",
                "expected_nano": "> 0",
                "actual_nano": 0,
            })

        # Invariant 3: if migrated, rate and timestamp must exist
        if post_nano > 0:
            if not post_rate:
                all_passed = False
                failures.append({
                    "user_id": user_id,
                    "invariant": "migrated_has_rate",
                    "rate": post_rate,
                })
            if not post_ts:
                all_passed = False
                failures.append({
                    "user_id": user_id,
                    "invariant": "migrated_has_timestamp",
                    "timestamp": post_ts,
                })

    return {
        "all_passed": all_passed,
        "failures": failures,
        "invariants_checked": [
            "balance_cents_unchanged",
            "positive_cents_gets_positive_nano",
            "migrated_has_rate",
            "migrated_has_timestamp",
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# ─── PART O: Readiness Report ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def generate_readiness_report(
    db_path: str,
    rate: Decimal | None = None,
) -> dict:
    """Generate a comprehensive readiness report for production migration.

    Args:
        db_path: Path to the database to inspect.
        rate: If provided, also run the offline rehearsal.

    Returns:
        Full readiness report dict.

    Raises:
        TypeError: If db_path is not a string.
    """
    if db_path is None or not isinstance(db_path, str):
        raise TypeError(f"db_path must be a string, got {type(db_path).__name__}")

    gb = _load_bot_module()

    report = {
        "status": "BLOCKED",
        "database_path": db_path,
    }

    # Schema inspection
    try:
        schema = inspect_schema(db_path)
        report["database_schema_status"] = "inspected"
        report["schema"] = schema
    except Exception as e:
        report["database_schema_status"] = f"failed: {e}"
        return report

    # Integrity check
    try:
        integrity = check_sqlite_integrity(db_path)
        report["integrity_status"] = integrity["status"]
        report["integrity"] = integrity
    except Exception as e:
        report["integrity_status"] = f"failed: {e}"
        return report

    if report["integrity_status"] != "ok":
        return report

    # Financial column inspection
    try:
        financial = inspect_financial_columns(db_path)
        report["financial"] = financial
    except Exception as e:
        report["financial_inspection"] = f"failed: {e}"

    # Negative balance check
    users_info = report.get("financial", {}).get("users", {})
    negative_count = users_info.get("negative_count", 0)
    report["negative_balance_status"] = "clear" if negative_count == 0 else "BLOCKED"
    if negative_count > 0:
        report["negative_balance_count"] = negative_count

    # Partial migration check
    partial = report.get("financial", {}).get("partial_migrations", [])
    report["partial_migration_status"] = "clear" if not partial else "BLOCKED"
    if partial:
        report["partial_migrations"] = partial

    # Migration schema check
    phase3 = report.get("schema", {}).get("phase3_schema", {})
    all_phase3 = all(phase3.get(k) for k in [
        "balance_usd_nano", "balance_usd_nano_rate",
        "balance_usd_nano_migrated_at", "migration_meta_table"
    ])
    report["migration_schema_status"] = "complete" if all_phase3 else "incomplete"

    # Explicit rate check
    if rate is None:
        report["explicit_rate_status"] = "not_supplied"
        report["migration_rate"] = None
    else:
        report["explicit_rate_status"] = "supplied"
        report["migration_rate"] = str(rate)

    # Backup status (we can always backup locally)
    report["backup_status"] = "available_locally"

    # Rate summary
    report["source_currency"] = "EGP"
    report["target_currency"] = "USD"
    report["source_unit"] = "EGP_cents"
    report["target_unit"] = "USD_nano"
    report["rounding_mode"] = "ROUND_HALF_UP"

    # User counts
    report["users_total"] = users_info.get("total", 0)
    report["users_positive"] = users_info.get("positive_count", 0)
    report["users_zero"] = users_info.get("zero_count", 0)
    report["users_negative"] = users_info.get("negative_count", 0)
    report["total_legacy_egp_cents"] = users_info.get("total_cents", 0)

    # Offline rehearsal if rate supplied
    if rate is not None:
        # Schema must be complete before rehearsal can run
        if report.get("migration_schema_status") != "complete":
            report["blockers"] = ["migration_schema"]
            report["status"] = "BLOCKED"
            report["reconciliation_status"] = "not_rehearsed"
            report["reconciliation"] = {}
            report["invariants"] = {}
            return report

        rehearsal = run_offline_rehearsal(db_path, rate)
        report["rehearsal"] = rehearsal
        report["rehearsal_status"] = rehearsal.get("status", "unknown")

        # Set overall status
        blockers = []
        if report["negative_balance_status"] == "BLOCKED":
            blockers.append("negative_balances")
        if report["partial_migration_status"] == "BLOCKED":
            blockers.append("partial_migrations")
        if report["integrity_status"] != "ok":
            blockers.append("integrity_failure")
        if report.get("migration_schema_status") != "complete":
            blockers.append("migration_schema")
        if rehearsal.get("status") != "READY_FOR_CUTOVER_REVIEW":
            blockers.append(f"rehearsal_{rehearsal.get('status', 'unknown')}")

        report["blockers"] = blockers
        report["status"] = "READY_FOR_CUTOVER_REVIEW" if not blockers else "BLOCKED"

        # Surface reconciliation and invariants at top level for API consumers
        report["reconciliation"] = rehearsal.get("reconciliation", {})
        report["invariants"] = rehearsal.get("invariants", {})
        report["reconciliation_status"] = (
            "ok" if report["reconciliation"].get("all_match") else "BLOCKED"
        )
    else:
        report["status"] = "BLOCKED" if negative_count > 0 else "NEEDS_REHEARSAL"
        report["reconciliation_status"] = "not_rehearsed"
        report["reconciliation"] = {}
        report["invariants"] = {}

    return report


# ══════════════════════════════════════════════════════════════════════════════
# ─── PART P: Rate Sensitivity ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def rate_sensitivity_report(db_path: str, rates: list[Decimal]) -> dict:
    """Show how different rates affect total USD nano conversion.

    Args:
        db_path: Path to database with user balances.
        rates: List of explicit EGP/USD rates to compare.

    Returns:
        Dict mapping each rate to its total converted USD nano.
    """
    gb = _load_bot_module()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        users = conn.execute(
            "SELECT balance_cents FROM users WHERE balance_cents > 0"
        ).fetchall()
        cents_list = [int(r[0]) for r in users]
    finally:
        conn.close()

    results = {}
    for rate in rates:
        total = sum(gb.egp_cents_to_usd_nano(c, rate) for c in cents_list)
        total_egp = sum(c for c in cents_list)
        results[str(rate)] = {
            "rate": str(rate),
            "total_legacy_egp_cents": total_egp,
            "total_legacy_egp": str(Decimal(total_egp) / Decimal("100")),
            "total_usd_nano": total,
            "total_usd": str(Decimal(total) / Decimal("1000000000")),
        }

    return {"sensitivity": results, "users_count": len(cents_list)}

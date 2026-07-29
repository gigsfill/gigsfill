"""System alerts — operational issues surfaced to the admin UI banner.

Self-clearing: a `record_alert` upserts the (alert_type) row, bumping
`count` + `last_seen_at` on each fire. `resolve_alert` flips
`resolved_at` so the banner clears automatically when the condition
is detected as fixed (e.g. a stripe webhook successfully verifies
again after a sig-failure run).

Designed to be cheap: each record_alert is one UPSERT, each
resolve_alert is one UPDATE on an indexed column. Safe to call from
the webhook hot path.

Connection-agnostic: accepts either a raw sqlite3 connection (what
the stripe webhook handler uses) or a SQLAlchemy session (what the
admin endpoints use). The two branches diverge on parameter style
('?' vs ':name'); since the stripe webhook is the primary writer
and uses raw sqlite, the helper takes a raw cursor by default. Admin
read-path uses SQLAlchemy via the admin endpoint.
"""
import logging
from typing import Optional

logger = logging.getLogger("gigsfill.system_alerts")


SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
_VALID_SEVERITIES = {SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_CRITICAL}


def record_alert(
    conn,
    *,
    alert_type: str,
    severity: str,
    message: str,
    details: Optional[str] = None,
):
    """Upsert: if an unresolved alert of this type exists, bump count +
    last_seen_at + refresh message. Otherwise insert a new row.

    `conn` must be a raw sqlite3.Connection (the webhook handler uses
    one of these directly via _webhook_sqlite_conn). For SQLAlchemy
    callers, use record_alert_sa() below.
    """
    if severity not in _VALID_SEVERITIES:
        severity = SEVERITY_WARNING
    try:
        c = conn.cursor()
        # Try update first — UPSERT pattern that works on SQLite + PG
        # since SQLite's INSERT...ON CONFLICT requires the column list
        # to match a unique index, and we're using a partial unique
        # index (WHERE resolved_at IS NULL) which not all engines
        # support uniformly. Update-then-insert is portable.
        # 2026-07-25 bug fix: clear acknowledged_at/by when a re-fire hits
        # an already-open alert. Prior version left the dismissal in place,
        # so a dismissed alert never re-surfaced even when the underlying
        # condition kept tripping — silently defeating the whole point of
        # dismissal being "hide until it happens again." Matches the intent
        # documented at admin.py:3176 (the acknowledge endpoint comment).
        c.execute(
            """UPDATE system_alerts
               SET count = count + 1,
                   last_seen_at = CURRENT_TIMESTAMP,
                   message = ?,
                   details = COALESCE(?, details),
                   severity = ?,
                   acknowledged_at = NULL,
                   acknowledged_by = NULL
               WHERE alert_type = ? AND resolved_at IS NULL""",
            (message, details, severity, alert_type),
        )
        if c.rowcount == 0:
            c.execute(
                """INSERT INTO system_alerts (alert_type, severity, message, details)
                   VALUES (?, ?, ?, ?)""",
                (alert_type, severity, message, details),
            )
        conn.commit()
    except Exception as e:
        # Never break the hot path because of an alerting failure.
        logger.warning(f"record_alert({alert_type}) failed: {e}")


def run_periodic_health_checks():
    """Runs once per scheduler loop iteration (every 10 minutes). Each
    check is self-contained, wrapped in try/except so one failing
    can't break the others, and uses the canonical record_alert /
    resolve_alert helpers so the banner self-clears once the
    underlying condition normalizes.

    Adding a new check: define a `_check_xxx` function that takes the
    sqlite3 connection and returns True (condition is BAD, alert
    active) or False (clean). Add it to the dispatch table below.
    Done.
    """
    import sqlite3
    from backend.db import DB_PATH
    from backend.db import get_db_connection

    conn = get_db_connection()
    try:
        # Heartbeat: tells future /healthz integrations that the
        # scheduler is still ticking. Written every loop iteration —
        # if more than ~15 minutes old, the scheduler is hung or dead.
        try:
            conn.cursor().execute(
                """INSERT INTO platform_settings (setting_key, setting_value)
                   VALUES ('scheduler_last_heartbeat', CURRENT_TIMESTAMP)
                   ON CONFLICT(setting_key) DO UPDATE
                   SET setting_value = CURRENT_TIMESTAMP"""
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"heartbeat write failed: {e}")

        # Run each check; one failing doesn't stop the others.
        for name, fn in (
            ("disk_usage", _check_disk_usage),
            ("stuck_transfers", _check_stuck_transfers),
            ("stale_digest_queue", _check_stale_digest_queue),
        ):
            try:
                fn(conn)
            except Exception as e:
                logger.warning(f"health check {name} failed: {e}")
    finally:
        conn.close()


# ──────────── Individual checks ────────────
# Each is responsible for recording AND resolving its own alert type.
# Convention: the check runs the underlying measurement, then either
# record_alert (condition bad) or resolve_alert (condition good).

def _check_disk_usage(conn):
    """Fire critical alert when root partition usage exceeds 85%.
    DB file + uploads dir + logs all live on the same disk, so a full
    disk takes down everything at once. 85% gives ~15 GB headroom on
    a 100 GB droplet — enough time to react before writes start
    failing."""
    import shutil
    usage = shutil.disk_usage("/")
    pct = (usage.used * 100) / usage.total
    THRESHOLD_BAD = 85
    THRESHOLD_RECOVER = 80  # hysteresis — avoid flapping at the edge
    free_gb = usage.free / 1_000_000_000

    # Read prior state: only resolve when we've dropped below the
    # recovery threshold (hysteresis), but record any time we're
    # over the bad threshold.
    if pct >= THRESHOLD_BAD:
        record_alert(
            conn,
            alert_type="disk_usage_high",
            severity=SEVERITY_CRITICAL,
            message=f"Disk usage at {pct:.1f}% — running out of space",
            details=(
                f"Root partition {pct:.1f}% full ({free_gb:.1f} GB free). "
                "At capacity, the app will start failing writes (DB commits, "
                "uploads, logs). Investigate: largest dirs via "
                "`du -sh /opt/gigsfill /var/log /var/lib`. Common culprits: "
                "uploads dir (move old flyers to S3), DB grew (run "
                "`VACUUM` or migrate to Postgres), journalctl logs "
                "(prune via `journalctl --vacuum-time=14d`)."
            ),
        )
    elif pct < THRESHOLD_RECOVER:
        resolve_alert(conn, alert_type="disk_usage_high", resolved_by="auto-recovered")


def _check_stuck_transfers(conn):
    """Fire critical when artist payouts are explicitly failed OR
    stuck in pending_transfer past their scheduled window.

    Two signals (the payout scheduler is the source of truth):
      a. status='transfer_failed' — explicit Stripe failure marker.
         Any row in this state has been seen by the retry sweep and
         left for manual intervention. Always actionable.
      b. status='pending_transfer' AND scheduled_process_at < now - 12h
         — legitimate retry state, but if it's been pending past the
         scheduled window for half a day, something's wrong (parent
         charge didn't clear? Connect account restricted?).
    """
    c = conn.cursor()
    failed_row = c.execute("""
        SELECT COUNT(*) FROM transactions
        WHERE transaction_type = 'artist_payout'
          AND status = 'transfer_failed'
    """).fetchone()
    stuck_row = c.execute("""
        SELECT COUNT(*) FROM transactions
        WHERE transaction_type = 'artist_payout'
          AND status = 'pending_transfer'
          AND scheduled_process_at IS NOT NULL
          AND scheduled_process_at < datetime('now', '-12 hours')
    """).fetchone()
    n_failed = int(failed_row[0] or 0) if failed_row else 0
    n_stuck = int(stuck_row[0] or 0) if stuck_row else 0
    total = n_failed + n_stuck
    if total > 0:
        ids = [r[0] for r in c.execute("""
            SELECT id FROM transactions
            WHERE transaction_type = 'artist_payout'
              AND (
                status = 'transfer_failed'
                OR (status = 'pending_transfer'
                    AND scheduled_process_at < datetime('now', '-12 hours'))
              )
            ORDER BY id DESC LIMIT 5
        """).fetchall()]
        record_alert(
            conn,
            alert_type="stuck_artist_payouts",
            severity=SEVERITY_CRITICAL,
            message=(
                f"{total} artist payout{'s' if total != 1 else ''} stuck "
                f"({n_failed} marked transfer_failed, "
                f"{n_stuck} pending > 12h)"
            ),
            details=(
                f"Transaction IDs (sample): {ids}. "
                "Most common causes: (1) artist's Stripe Connect account "
                "is restricted/deactivated → check Stripe Dashboard → "
                "Connect → Accounts; (2) artist's bank rejected the "
                "transfer → check Stripe transfer failure reason; (3) "
                "our Stripe balance is insufficient → check Stripe "
                "Dashboard → Balance. Admin tool: Payments tab → search "
                "the txn IDs → Reverse Transfer or Refire."
            ),
        )
    else:
        resolve_alert(conn, alert_type="stuck_artist_payouts", resolved_by="auto-recovered")


def _check_stale_digest_queue(conn):
    """Fire warning when digest queue rows sit unsent for > 36 hours.
    The send sweep fires hourly and drains anyone whose local hour
    is 9 AM. If a user has 36+ hour-old rows, either:
      - Their timezone never hits 9 in the schedule (state-resolution bug)
      - The send sweep is silently failing for them (SMTP-down for that
        specific user's mail server, etc.)
      - The master toggle off → rows pile up indefinitely
    Worth a look either way.
    """
    c = conn.cursor()
    row = c.execute("""
        SELECT COUNT(*) FROM artist_email_digest_queue
        WHERE sent_at IS NULL
          AND queued_at < datetime('now', '-36 hours')
    """).fetchone()
    n = int(row[0] or 0) if row else 0
    if n >= 10:
        # Only fire at 10+ — under that it's likely just one or two
        # users whose 9 AM tick hasn't fired yet.
        record_alert(
            conn,
            alert_type="stale_digest_queue",
            severity=SEVERITY_WARNING,
            message=f"{n} digest queue items unsent for > 36 hours",
            details=(
                "Possible causes: send sweep failing for specific users "
                "(check journalctl for SMTP FAILURE lines), state→timezone "
                "resolution broken, or master digest toggle disabled while "
                "the hourly detector keeps enqueuing. Check admin → Digest "
                "Queue tab for the affected users."
            ),
        )
    else:
        resolve_alert(conn, alert_type="stale_digest_queue", resolved_by="auto-recovered")


def resolve_alert(conn, *, alert_type: str, resolved_by: str = "auto"):
    """Mark all unresolved alerts of this type as resolved. Called when
    the underlying condition is detected as fixed (e.g. webhook
    signature verification succeeded after a run of failures)."""
    try:
        c = conn.cursor()
        c.execute(
            """UPDATE system_alerts
               SET resolved_at = CURRENT_TIMESTAMP,
                   resolved_by = ?
               WHERE alert_type = ? AND resolved_at IS NULL""",
            (resolved_by, alert_type),
        )
        n = c.rowcount
        conn.commit()
        if n > 0:
            logger.info(f"[ALERT_RESOLVED] type={alert_type} count={n} resolved_by={resolved_by}")
    except Exception as e:
        logger.warning(f"resolve_alert({alert_type}) failed: {e}")

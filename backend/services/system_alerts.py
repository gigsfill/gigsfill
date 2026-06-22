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
        c.execute(
            """UPDATE system_alerts
               SET count = count + 1,
                   last_seen_at = CURRENT_TIMESTAMP,
                   message = ?,
                   details = COALESCE(?, details),
                   severity = ?
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

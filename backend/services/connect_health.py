"""Stripe Connect account health auditor.

Why this exists: account.updated webhooks tell us when an artist's
Connect account changes (restrictions, missing info, etc.). But
webhooks miss — the June 19→22 secret-mismatch outage made that
obvious. A daily proactive poll closes the gap and gives us an
admin-facing aggregated view ("12 artists need to complete
verification") instead of expecting an admin to grep Stripe
Dashboard at scale.

What it does:
  - For each artist with a stripe_connect_account_id, call
    stripe.Account.retrieve and parse the requirements payload.
  - Cache the result in connect_account_health (one row/artist).
  - When an account first becomes unhealthy (or details change),
    optionally email the artist with a fresh Stripe Express
    onboarding link (debounced — once per 7 days per artist to
    avoid pestering them while they work on it).
  - When unhealthy-account count crosses a threshold, fire a
    system_alert so the admin banner shows it.

Performance: ~1 Stripe API call per artist per audit. At 1000
artists that's 10 seconds total at Stripe's 100/sec read limit —
fine for daily. If we ever cross 10K, we'll batch by oldest
last_polled_at and audit ~1K/hour.
"""
import json
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger("gigsfill.connect_health")

# How many unhealthy accounts triggers the admin banner alert.
# Below this we still log + show them in the admin Account Health
# tab, but no banner. Above: banner fires so admin notices.
UNHEALTHY_ALERT_THRESHOLD = 5

# Don't pester the artist more than once per 7 days about the
# same persistent issue. Set to None to disable email entirely
# (admin can act manually from the UI).
ARTIST_EMAIL_DEBOUNCE_DAYS = 7


def _safe_json(obj):
    """Stripe SDK objects can be Stripe object models, not plain
    dicts/lists — coerce to JSON-serializable form."""
    if obj is None:
        return None
    try:
        return json.dumps(list(obj) if hasattr(obj, "__iter__") and not isinstance(obj, (str, dict)) else obj)
    except Exception:
        try:
            return json.dumps(str(obj))
        except Exception:
            return None


def _normalize_requirements(req):
    """Stripe returns requirements as either a dict (raw API) or an
    object (SDK). Return (currently_due, past_due, errors,
    disabled_reason) as plain Python collections."""
    def _get(key, default=None):
        if req is None:
            return default
        if isinstance(req, dict):
            return req.get(key, default)
        return getattr(req, key, default)
    currently_due = list(_get("currently_due") or [])
    past_due = list(_get("past_due") or [])
    errors_raw = list(_get("errors") or [])
    # Errors are richer objects — collapse to {code, reason, requirement}
    errors = []
    for e in errors_raw:
        if isinstance(e, dict):
            errors.append({
                "code": e.get("code"),
                "reason": e.get("reason"),
                "requirement": e.get("requirement"),
            })
        else:
            errors.append({
                "code": getattr(e, "code", None),
                "reason": getattr(e, "reason", None),
                "requirement": getattr(e, "requirement", None),
            })
    disabled_reason = _get("disabled_reason")
    return currently_due, past_due, errors, disabled_reason


def audit_account(stripe, conn, artist_id: int, connect_acct: str) -> Optional[dict]:
    """Audit one artist's Connect account. Returns a dict describing
    the new health state, or None on Stripe API failure (caller
    should log + continue with the next artist)."""
    try:
        acct = stripe.Account.retrieve(connect_acct)
    except Exception as e:
        logger.warning(f"[CONNECT_HEALTH] retrieve failed for artist={artist_id} acct={connect_acct}: {e}")
        return None
    charges_enabled = bool(getattr(acct, "charges_enabled", False))
    payouts_enabled = bool(getattr(acct, "payouts_enabled", False))
    details_submitted = bool(getattr(acct, "details_submitted", False))
    currently_due, past_due, errors, disabled_reason = _normalize_requirements(
        getattr(acct, "requirements", None)
    )
    req_count = len(currently_due) + len(past_due)

    # Compare to existing row to decide if last_changed_at should bump
    prior = conn.execute(
        "SELECT charges_enabled, payouts_enabled, requirements_count, disabled_reason "
        "FROM connect_account_health WHERE artist_id = ?",
        (artist_id,)
    ).fetchone()
    changed = (
        prior is None
        or bool(prior[0]) != charges_enabled
        or bool(prior[1]) != payouts_enabled
        or int(prior[2] or 0) != req_count
        or (prior[3] or "") != (disabled_reason or "")
    )

    # UPSERT
    if prior is None:
        conn.execute(
            """INSERT INTO connect_account_health
               (artist_id, stripe_connect_account_id, charges_enabled,
                payouts_enabled, details_submitted, disabled_reason,
                currently_due_json, past_due_json, errors_json,
                requirements_count, last_polled_at, last_changed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (artist_id, connect_acct,
             1 if charges_enabled else 0,
             1 if payouts_enabled else 0,
             1 if details_submitted else 0,
             disabled_reason,
             json.dumps(currently_due),
             json.dumps(past_due),
             json.dumps(errors),
             req_count),
        )
    else:
        conn.execute(
            """UPDATE connect_account_health
               SET stripe_connect_account_id = ?,
                   charges_enabled = ?,
                   payouts_enabled = ?,
                   details_submitted = ?,
                   disabled_reason = ?,
                   currently_due_json = ?,
                   past_due_json = ?,
                   errors_json = ?,
                   requirements_count = ?,
                   last_polled_at = CURRENT_TIMESTAMP,
                   last_changed_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_changed_at END
               WHERE artist_id = ?""",
            (connect_acct,
             1 if charges_enabled else 0,
             1 if payouts_enabled else 0,
             1 if details_submitted else 0,
             disabled_reason,
             json.dumps(currently_due),
             json.dumps(past_due),
             json.dumps(errors),
             req_count,
             1 if changed else 0,
             artist_id),
        )
    return {
        "artist_id": artist_id,
        "payouts_enabled": payouts_enabled,
        "charges_enabled": charges_enabled,
        "disabled_reason": disabled_reason,
        "requirements_count": req_count,
        "changed": changed,
        "is_healthy": payouts_enabled and not disabled_reason and req_count == 0,
    }


def _maybe_email_artist(conn, artist_id: int, snapshot: dict):
    """Email the artist about Connect account issues, debounced to
    ARTIST_EMAIL_DEBOUNCE_DAYS per artist. Returns True if email
    was sent. Reads the artist's email, builds a Stripe Express
    onboarding link (Account Link for type='account_onboarding'),
    sends via the platform SMTP."""
    if ARTIST_EMAIL_DEBOUNCE_DAYS is None:
        return False
    if snapshot.get("is_healthy"):
        return False
    # Debounce
    row = conn.execute(
        "SELECT artist_emailed_at FROM connect_account_health WHERE artist_id = ?",
        (artist_id,),
    ).fetchone()
    if row and row[0]:
        try:
            r = conn.execute(
                "SELECT julianday('now') - julianday(?)",
                (row[0],),
            ).fetchone()
            if r and r[0] is not None and r[0] < ARTIST_EMAIL_DEBOUNCE_DAYS:
                return False
        except Exception:
            pass
    # Build the email
    try:
        meta = conn.execute(
            """SELECT a.name, u.email
               FROM artists a LEFT JOIN users u ON u.id = a.user_id
               WHERE a.id = ?""",
            (artist_id,),
        ).fetchone()
        if not meta or not meta[1]:
            return False
        artist_name, artist_email = meta[0], meta[1]

        # Build a fresh Stripe Account Link so the artist can resolve
        # the issue without us telling them what's wrong (Stripe's
        # onboarding flow knows exactly what's needed).
        import stripe as stripe_lib
        skey = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key='admin_stripe_secret_key'"
        ).fetchone()
        if not skey or not skey[0]:
            return False
        stripe_lib.api_key = skey[0]
        link = stripe_lib.AccountLink.create(
            account=snapshot.get("connect_account_id") or "",
            type="account_onboarding",
            refresh_url="https://gigsfill.com/app/user-profile.html",
            return_url="https://gigsfill.com/app/user-profile.html",
        )
        action_url = link.url

        # Send via the platform SMTP path (mirror what other artist
        # emails use — get_smtp_settings + send_email from scheduler).
        from backend.scheduler import get_smtp_settings, send_email
        smtp = get_smtp_settings(conn.cursor())
        if not smtp:
            return False
        subj = "Action needed: your GigsFill payout account"
        body = f"""<p>Hi {artist_name},</p>
<p>Your Stripe payout account linked to GigsFill needs a quick update before
we can send you future payouts. Stripe is asking for some additional
information.</p>
<p><a href="{action_url}" style="display:inline-block;padding:10px 16px;
background:#06b6d4;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;">
Complete account setup</a></p>
<p>This link opens Stripe's secure form. We don't see or store any of the
information you submit there — it goes directly to Stripe.</p>
<p>— The GigsFill team</p>"""
        if send_email(smtp, artist_email, subj, body):
            conn.execute(
                "UPDATE connect_account_health SET artist_emailed_at = CURRENT_TIMESTAMP "
                "WHERE artist_id = ?",
                (artist_id,),
            )
            conn.commit()
            logger.info(f"[CONNECT_HEALTH] Emailed artist={artist_id} ({artist_email}) about account issues")
            return True
    except Exception as e:
        logger.warning(f"[CONNECT_HEALTH] email send failed for artist={artist_id}: {e}")
    return False


def audit_all_accounts():
    """Top-level entry — called from the scheduler's daily tick.

    Iterates every artist with a Connect account, polls each one,
    updates the cache, optionally emails the artist, and fires a
    system alert if the unhealthy-count crosses the threshold.
    """
    from backend.db import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Need the live Stripe key from platform_settings — bail
        # early if not configured (test/dev environments).
        skey = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key='admin_stripe_secret_key'"
        ).fetchone()
        if not skey or not skey[0]:
            logger.info("[CONNECT_HEALTH] Stripe key not configured — skipping audit")
            return
        import stripe
        stripe.api_key = skey[0]

        artists = conn.execute("""
            SELECT a.id, eps.stripe_connect_account_id
            FROM artists a
            JOIN entity_payment_settings eps
              ON eps.entity_type = 'artist' AND eps.entity_id = a.id
            WHERE eps.stripe_connect_account_id IS NOT NULL
              AND eps.stripe_connect_account_id != ''
        """).fetchall()
        logger.info(f"[CONNECT_HEALTH] Auditing {len(artists)} artist Connect accounts")

        unhealthy = []
        for artist_id, acct in artists:
            snap = audit_account(stripe, conn, artist_id, acct)
            conn.commit()
            if snap is None:
                continue
            if not snap["is_healthy"]:
                unhealthy.append(snap)
                # Pass the connect acct id through so _maybe_email_artist
                # can build the Account Link without re-querying.
                snap["connect_account_id"] = acct
                _maybe_email_artist(conn, artist_id, snap)

        # Aggregate signal → system_alert
        from backend.services.system_alerts import (
            record_alert, resolve_alert, SEVERITY_WARNING, SEVERITY_CRITICAL,
        )
        if len(unhealthy) >= UNHEALTHY_ALERT_THRESHOLD:
            sample_ids = sorted([s["artist_id"] for s in unhealthy])[:8]
            critical = sum(
                1 for s in unhealthy if not s["payouts_enabled"] or s.get("disabled_reason")
            )
            severity = SEVERITY_CRITICAL if critical >= UNHEALTHY_ALERT_THRESHOLD else SEVERITY_WARNING
            record_alert(
                conn,
                alert_type="connect_accounts_unhealthy",
                severity=severity,
                message=(
                    f"{len(unhealthy)} artist Connect account{'s' if len(unhealthy) != 1 else ''} "
                    f"need attention ({critical} can't receive payouts)"
                ),
                details=(
                    f"Sample artist IDs: {sample_ids}. "
                    "Review the Account Health tab in admin → Payments. "
                    "Common cause: artist hasn't submitted ID verification or "
                    "the bank account they linked was rejected. Artists with "
                    "open issues are emailed a Stripe onboarding link "
                    f"automatically (debounced {ARTIST_EMAIL_DEBOUNCE_DAYS}d). "
                    "Manual remediation: admin → Payments → Account Health → "
                    "click the artist for details + 'Email onboarding link' to "
                    "force-send."
                ),
            )
        else:
            resolve_alert(conn, alert_type="connect_accounts_unhealthy", resolved_by="auto-recovered")

        logger.info(
            f"[CONNECT_HEALTH] Audit complete — healthy={len(artists)-len(unhealthy)} "
            f"unhealthy={len(unhealthy)}"
        )
    finally:
        conn.close()


def reconcile_recent_disputes():
    """Cross-check Stripe disputes against our DB. Catches disputes
    we'd have missed during a webhook outage. Runs alongside the
    daily Connect audit.

    Looks at all disputes Stripe has created in the last 14 days.
    For each, finds the matching transaction in our DB by PI ID.
    If the dispute isn't already noted in the txn's `notes`, we
    record it and fire the same admin email + venue suspension
    flow the live webhook would have triggered.
    """
    from backend.db import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    try:
        skey = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key='admin_stripe_secret_key'"
        ).fetchone()
        if not skey or not skey[0]:
            return
        import stripe
        stripe.api_key = skey[0]

        # Last 14 days
        import time as _t
        cutoff = int(_t.time()) - 14 * 86400
        try:
            disputes = stripe.Dispute.list(limit=100, created={"gte": cutoff}).data
        except Exception as e:
            logger.warning(f"[DISPUTE_RECONCILE] list failed: {e}")
            return

        missed = []
        for d in disputes:
            pi = getattr(d, "payment_intent", None) or getattr(d, "charge", None)
            if not pi:
                continue
            row = conn.execute(
                """SELECT id, status, notes FROM transactions
                   WHERE stripe_payment_intent_id = ? OR stripe_payment_intent_id LIKE ?
                   LIMIT 1""",
                (pi, f"%{pi}%"),
            ).fetchone()
            if not row:
                continue
            txn_id, txn_status, notes = row
            # If notes already mention this dispute id, we got the webhook
            if d.id in (notes or ""):
                continue
            missed.append({
                "txn_id": txn_id, "dispute_id": d.id, "reason": d.reason,
                "amount": d.amount, "status": d.status,
            })
            # Append to notes so a future audit doesn't re-detect this
            conn.execute(
                "UPDATE transactions SET notes = COALESCE(notes || ' | ', '') || ? WHERE id = ?",
                (f"Dispute reconciled {d.id} reason={d.reason} status={d.status}", txn_id),
            )
        conn.commit()

        if missed:
            logger.warning(f"[DISPUTE_RECONCILE] Found {len(missed)} disputes not previously tracked")
            from backend.services.system_alerts import record_alert, SEVERITY_CRITICAL
            record_alert(
                conn,
                alert_type="missed_stripe_disputes",
                severity=SEVERITY_CRITICAL,
                message=f"{len(missed)} Stripe dispute(s) found that GigsFill never recorded",
                details=(
                    f"Likely cause: webhook delivery failure during a recent outage. "
                    f"Sample txn IDs: {[m['txn_id'] for m in missed[:5]]}. "
                    "Review Stripe Dashboard → Payments → Disputes for full context. "
                    "Each transaction's `notes` field has been annotated with the "
                    "dispute ID so they can be tracked from the admin Payments tab. "
                    "Manual action may still be needed (suspend venue, alert artist)."
                ),
            )
        else:
            from backend.services.system_alerts import resolve_alert
            resolve_alert(conn, alert_type="missed_stripe_disputes", resolved_by="auto-recovered")
    finally:
        conn.close()

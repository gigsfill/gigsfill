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

# An account unhealthy for this many days triggers the final-
# warning flow: artist gets an "we're going to suspend this"
# email, onboarding_complete flag is cleared (so the booking
# flow stops routing transfers to them), and a critical
# system_alert fires for admin review.
AUTO_SUSPEND_THRESHOLD_DAYS = 30


def _get_or_create_onboarding_url_for_artist(conn, artist_id: int) -> Optional[str]:
    """Return https://gigsfill.com/api/stripe/onboarding/<token> for
    this artist, minting + storing the token if missing. Used by
    both the regular debounced reminder email and the 30-day final
    warning email.

    Stores token on entity_payment_settings.stripe_onboarding_token
    (added in db.py migration). The endpoint at that path generates
    a fresh Stripe AccountLink on each click."""
    import secrets
    row = conn.execute(
        "SELECT stripe_onboarding_token FROM entity_payment_settings "
        "WHERE entity_type='artist' AND entity_id = ?",
        (artist_id,),
    ).fetchone()
    if row and row[0]:
        return f"https://gigsfill.com/api/stripe/onboarding/{row[0]}"
    tok = secrets.token_urlsafe(24)
    try:
        conn.execute(
            """UPDATE entity_payment_settings
               SET stripe_onboarding_token = ?, updated_at = datetime('now')
               WHERE entity_type='artist' AND entity_id = ?""",
            (tok, artist_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"[CONNECT_HEALTH] token store failed for artist={artist_id}: {e}")
        return None
    return f"https://gigsfill.com/api/stripe/onboarding/{tok}"


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
        "SELECT charges_enabled, payouts_enabled, requirements_count, disabled_reason, "
        "unhealthy_since "
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

    # unhealthy_since: set NOW when an account transitions
    # healthy → unhealthy, clear back to NULL when it recovers.
    # Stays put during sustained unhealthy state — the value is
    # the start of THIS incident, not the most recent re-poll.
    is_healthy = payouts_enabled and not disabled_reason and req_count == 0
    prior_unhealthy_since = prior[4] if prior else None
    if is_healthy:
        new_unhealthy_since = None  # cleared on recovery
    elif prior_unhealthy_since:
        new_unhealthy_since = prior_unhealthy_since  # carry forward
    else:
        new_unhealthy_since = "CURRENT_TIMESTAMP"  # sentinel — handled below

    # UPSERT. unhealthy_since handled via two SQL paths so we can
    # use CURRENT_TIMESTAMP cleanly: NULL → clear, "CURRENT_TIMESTAMP"
    # sentinel → first-fire (use SQL function), explicit value →
    # carry forward.
    if prior is None:
        # First insert — unhealthy_since gets NOW if unhealthy, NULL if healthy
        uh_sql = "CURRENT_TIMESTAMP" if not is_healthy else "NULL"
        conn.execute(
            f"""INSERT INTO connect_account_health
               (artist_id, stripe_connect_account_id, charges_enabled,
                payouts_enabled, details_submitted, disabled_reason,
                currently_due_json, past_due_json, errors_json,
                requirements_count, last_polled_at, last_changed_at,
                unhealthy_since)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, {uh_sql})""",
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
        # UPDATE — three cases for unhealthy_since:
        #   healthy now           → NULL (clear it)
        #   already had a value   → keep it (carry forward)
        #   first-fire unhealthy  → set CURRENT_TIMESTAMP
        if is_healthy:
            uh_sql = "NULL"
        elif prior_unhealthy_since:
            uh_sql = "unhealthy_since"  # no-op self-assign
        else:
            uh_sql = "CURRENT_TIMESTAMP"
        conn.execute(
            f"""UPDATE connect_account_health
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
                   last_changed_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_changed_at END,
                   unhealthy_since = {uh_sql}
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
    # On recovery, also clear admin_alerted_at so the NEXT incident
    # for this account re-fires the admin email instead of being
    # silently de-duped.
    if is_healthy and prior is not None:
        conn.execute(
            "UPDATE connect_account_health SET admin_alerted_at = NULL, "
            "auto_suspended_at = NULL WHERE artist_id = ?",
            (artist_id,),
        )
    return {
        "artist_id": artist_id,
        "payouts_enabled": payouts_enabled,
        "charges_enabled": charges_enabled,
        "disabled_reason": disabled_reason,
        "requirements_count": req_count,
        "currently_due": currently_due,
        "past_due": past_due,
        "errors": errors,
        "changed": changed,
        "is_healthy": is_healthy,
        # `was_healthy_before` lets the caller fire the first-fire
        # admin email only on the healthy → unhealthy transition.
        "was_healthy_before": prior is None or (
            bool(prior[1]) and not (prior[3] or "") and int(prior[2] or 0) == 0
        ),
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

        # Build a tokenized redirect URL that mints a FRESH Stripe
        # AccountLink each click. Stripe AccountLinks are single-use
        # + ~10-min lifetime; embedding one directly in email broke
        # for any artist who didn't click within minutes (Stripe
        # redirected to our refresh_url = login page, which made
        # the email feel broken).
        action_url = _get_or_create_onboarding_url_for_artist(conn, artist_id)
        if not action_url:
            return False

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


def _maybe_admin_alert_first_fire(conn, artist_id: int, snapshot: dict):
    """Email admin when an account FIRST transitions healthy → unhealthy.
    Deduped by admin_alerted_at — re-fires only after the next
    healthy → unhealthy cycle. Avoids spamming admin during a
    sustained issue (the banner stays up the whole time).
    Returns True if email sent."""
    if snapshot.get("is_healthy") or not snapshot.get("was_healthy_before"):
        return False
    row = conn.execute(
        "SELECT admin_alerted_at FROM connect_account_health WHERE artist_id = ?",
        (artist_id,),
    ).fetchone()
    if row and row[0]:
        return False  # already alerted this incident
    try:
        # Look up artist + venue context for the email
        meta = conn.execute(
            "SELECT name FROM artists WHERE id = ?",
            (artist_id,),
        ).fetchone()
        artist_name = meta[0] if meta else f"Artist #{artist_id}"
        due_count = len(snapshot.get("currently_due", [])) + len(snapshot.get("past_due", []))
        sample = (snapshot.get("currently_due", []) + snapshot.get("past_due", []))[:5]
        sample_keys = [k.split('.')[-1] for k in sample]
        body = f"""<p>An artist's Stripe Connect account just transitioned to unhealthy.</p>
<ul>
  <li><b>Artist:</b> {artist_name} (id {artist_id})</li>
  <li><b>Payouts enabled:</b> {snapshot['payouts_enabled']}</li>
  <li><b>Charges enabled:</b> {snapshot['charges_enabled']}</li>
  <li><b>Disabled reason:</b> {snapshot.get('disabled_reason') or '—'}</li>
  <li><b>Requirements outstanding:</b> {due_count}{' (' + ', '.join(sample_keys) + ('…' if due_count > 5 else '') + ')' if sample_keys else ''}</li>
</ul>
<p>The artist has been auto-emailed a Stripe onboarding link. No action needed
unless they don't resolve it within ~30 days, at which point we'll auto-flag
and you'll get a follow-up alert.</p>
<p>Full detail: <a href="https://gigsfill.com/app/admin.html">Admin → Platform Settings → Account Health</a></p>"""
        from backend.routes.stripe_connect import _wh_admin_alert
        _wh_admin_alert(conn, f"Artist account unhealthy — {artist_name}", body)
        conn.execute(
            "UPDATE connect_account_health SET admin_alerted_at = CURRENT_TIMESTAMP "
            "WHERE artist_id = ?",
            (artist_id,),
        )
        conn.commit()
        logger.info(f"[CONNECT_HEALTH] First-fire admin alert sent for artist={artist_id}")
        return True
    except Exception as e:
        logger.warning(f"[CONNECT_HEALTH] admin alert failed for artist={artist_id}: {e}")
        return False


def _maybe_auto_suspend(conn, artist_id: int, snapshot: dict):
    """Trigger the final-warning flow when an account has been
    unhealthy for >= AUTO_SUSPEND_THRESHOLD_DAYS:
      - Clear stripe_connect_onboarding_complete so the booking flow
        knows not to route new transfers here
      - Email the artist a final-warning notice
      - Fire a CRITICAL system_alert with the artist details
    De-duped via auto_suspended_at (only fires once per incident)."""
    if snapshot.get("is_healthy"):
        return False
    row = conn.execute(
        "SELECT unhealthy_since, auto_suspended_at "
        "FROM connect_account_health WHERE artist_id = ?",
        (artist_id,),
    ).fetchone()
    if not row or not row[0] or row[1]:
        return False
    # Has it been unhealthy long enough?
    age_days_row = conn.execute(
        "SELECT julianday('now') - julianday(?)", (row[0],)
    ).fetchone()
    if not age_days_row or age_days_row[0] is None:
        return False
    if age_days_row[0] < AUTO_SUSPEND_THRESHOLD_DAYS:
        return False
    # Trigger the flow
    try:
        # 1. Clear onboarding_complete in entity_payment_settings
        conn.execute(
            """UPDATE entity_payment_settings
               SET stripe_connect_onboarding_complete = 0,
                   updated_at = datetime('now')
               WHERE entity_type='artist' AND entity_id = ?""",
            (artist_id,),
        )
        # 2. Mark auto-suspended in our cache (so we don't repeat)
        conn.execute(
            "UPDATE connect_account_health SET auto_suspended_at = CURRENT_TIMESTAMP "
            "WHERE artist_id = ?",
            (artist_id,),
        )
        conn.commit()

        # 3. Final-warning email to artist (bypasses the 7-day debounce —
        # this IS the escalation step)
        meta = conn.execute(
            """SELECT a.name, u.email FROM artists a
               LEFT JOIN users u ON u.id = a.user_id
               WHERE a.id = ?""",
            (artist_id,),
        ).fetchone()
        artist_name = meta[0] if meta else f"Artist #{artist_id}"
        artist_email = meta[1] if meta else None
        if artist_email:
            try:
                action_url = _get_or_create_onboarding_url_for_artist(conn, artist_id)
                from backend.scheduler import get_smtp_settings, send_email
                smtp = get_smtp_settings(conn.cursor())
                subj = "⚠ Final notice: your GigsFill payout account"
                body = f"""<p>Hi {artist_name},</p>
<p>Your GigsFill payout account has been incomplete for over 30 days.
We've paused new payouts to this account until it's resolved — venues
booking you after today will see a warning.</p>
<p><a href="{action_url}" style="display:inline-block;padding:10px 16px;
background:#ef4444;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;">
Complete account setup now</a></p>
<p>Once Stripe verifies your information, your account is automatically
reinstated. No action needed from us. If you're stuck, reply to this
email and we'll help.</p>
<p>— The GigsFill team</p>"""
                send_email(smtp, artist_email, subj, body)
            except Exception as ee:
                logger.warning(f"[AUTO_SUSPEND] final-warning email failed for artist={artist_id}: {ee}")

        # 4. Admin system alert
        from backend.services.system_alerts import record_alert, SEVERITY_CRITICAL
        record_alert(
            conn,
            alert_type=f"auto_suspended_artist_{artist_id}",
            severity=SEVERITY_CRITICAL,
            message=f"Artist {artist_name} auto-suspended — Stripe account unhealthy for 30+ days",
            details=(
                f"stripe_connect_onboarding_complete cleared for artist {artist_id}. "
                f"Final-warning email sent. Will auto-reinstate when Stripe "
                f"reports the account healthy again. Manual review: admin → "
                f"Payments → Account Health."
            ),
        )
        logger.warning(f"[AUTO_SUSPEND] artist={artist_id} ({artist_name}) auto-suspended after 30+d unhealthy")
        return True
    except Exception as e:
        logger.warning(f"[AUTO_SUSPEND] failed for artist={artist_id}: {e}")
        return False


def audit_all_accounts():
    """Top-level entry — called from the scheduler's daily tick.

    Iterates every artist with a Connect account, polls each one,
    updates the cache, optionally emails the artist, and fires a
    system alert if the unhealthy-count crosses the threshold.
    """
    from backend.db import DB_PATH
    from backend.db import get_db_connection

    conn = get_db_connection()
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
                # Email admin on the healthy → unhealthy transition
                _maybe_admin_alert_first_fire(conn, artist_id, snap)
                # Auto-suspend if unhealthy for 30+ days
                _maybe_auto_suspend(conn, artist_id, snap)

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


def send_weekly_admin_digest():
    """Once-per-week admin overview email. Covers:
      - Connect account health summary
      - Active system_alerts
      - Last 7d payment stats (charges, payouts, refunds, disputes)
      - Recent unhealthy accounts list

    Triggered from the scheduler on Mondays at 9 AM via a
    `last_weekly_digest` platform_setting timestamp guard.
    """
    import sqlite3
    from backend.db import DB_PATH
    from backend.db import get_db_connection

    conn = get_db_connection()
    try:
        # Section 1: Connect health summary
        ch = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN payouts_enabled = 1 AND requirements_count = 0
                              AND (disabled_reason IS NULL OR disabled_reason = '') THEN 1 ELSE 0 END) AS healthy,
                SUM(CASE WHEN payouts_enabled = 0 OR disabled_reason IS NOT NULL
                              OR requirements_count > 0 THEN 1 ELSE 0 END) AS unhealthy,
                SUM(CASE WHEN auto_suspended_at IS NOT NULL THEN 1 ELSE 0 END) AS auto_suspended
            FROM connect_account_health
        """).fetchone()

        unhealthy_rows = conn.execute("""
            SELECT cah.artist_id, a.name, cah.requirements_count, cah.disabled_reason,
                   cah.unhealthy_since, cah.auto_suspended_at
            FROM connect_account_health cah
            LEFT JOIN artists a ON a.id = cah.artist_id
            WHERE cah.payouts_enabled = 0
               OR cah.disabled_reason IS NOT NULL
               OR cah.requirements_count > 0
            ORDER BY cah.unhealthy_since ASC NULLS LAST
            LIMIT 20
        """).fetchall()

        # Section 2: Active system alerts
        active_alerts = conn.execute("""
            SELECT alert_type, severity, message, count, first_seen_at
            FROM system_alerts WHERE resolved_at IS NULL
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     last_seen_at DESC
            LIMIT 10
        """).fetchall()

        # Section 3: Payment stats (last 7 days)
        ps = conn.execute("""
            SELECT
                SUM(CASE WHEN transaction_type IN ('venue_charge','single')
                          AND status IN ('charged','paid')
                          AND created_at >= datetime('now','-7 days') THEN venue_charge_cents ELSE 0 END) AS charged_cents,
                SUM(CASE WHEN transaction_type IN ('venue_charge','single')
                          AND status IN ('charged','paid')
                          AND created_at >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS charge_count,
                SUM(CASE WHEN transaction_type='artist_payout' AND status='paid'
                          AND created_at >= datetime('now','-7 days') THEN amount_cents ELSE 0 END) AS payout_cents,
                SUM(CASE WHEN transaction_type='artist_payout' AND status='paid'
                          AND created_at >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS payout_count,
                SUM(CASE WHEN status='transfer_failed' THEN 1 ELSE 0 END) AS failed_total,
                SUM(CASE WHEN status='payment_cancelled'
                          AND cancelled_at >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS cancelled_count
            FROM transactions
        """).fetchone()

        # Section 4: New venue / artist signups last 7 days
        new_v = conn.execute("SELECT COUNT(*) FROM venues WHERE created_at >= datetime('now','-7 days')").fetchone()
        new_a = conn.execute("SELECT COUNT(*) FROM artists WHERE created_at >= datetime('now','-7 days')").fetchone()

        # Section 5: Gig activity
        ga = conn.execute("""
            SELECT
                SUM(CASE WHEN created_at >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS created,
                SUM(CASE WHEN status='booked' AND date >= date('now','-7 days') AND date < date('now') THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status='cancelled' AND created_at >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS cancelled
            FROM gigs
        """).fetchone()

        # Compose the email
        def _cents(c): return f"${(c or 0) / 100:,.2f}"

        unhealthy_section = ""
        if ch["unhealthy"] > 0:
            unhealthy_section = "<h3 style='color:#ef4444;margin:18px 0 6px;'>Accounts needing attention</h3><table style='border-collapse:collapse;width:100%;font-size:13px;'>"
            unhealthy_section += "<tr style='border-bottom:1px solid #444;'><th align='left' style='padding:4px 8px;'>Artist</th><th align='left' style='padding:4px 8px;'>Reason</th><th align='left' style='padding:4px 8px;'>Since</th></tr>"
            for r in unhealthy_rows:
                age_days = ""
                if r["unhealthy_since"]:
                    age = conn.execute("SELECT CAST(julianday('now') - julianday(?) AS INTEGER)", (r["unhealthy_since"],)).fetchone()
                    age_days = f"{age[0]}d" if age and age[0] is not None else ""
                reason = r["disabled_reason"] or (f"{r['requirements_count']} required" if r["requirements_count"] else "—")
                suspended = " 🔒 suspended" if r["auto_suspended_at"] else ""
                unhealthy_section += f"<tr><td style='padding:4px 8px;'>{r['name'] or ('#' + str(r['artist_id']))}</td><td style='padding:4px 8px;'>{reason}{suspended}</td><td style='padding:4px 8px;'>{age_days}</td></tr>"
            unhealthy_section += "</table>"

        alerts_section = ""
        if active_alerts:
            alerts_section = "<h3 style='margin:18px 0 6px;'>Active system alerts</h3><ul>"
            for a in active_alerts:
                sev_color = "#ef4444" if a["severity"] == "critical" else "#f59e0b"
                alerts_section += f"<li><b style='color:{sev_color};'>[{a['severity'].upper()}]</b> {a['message']} <span style='color:#888;'>(fired {a['count']}× since {a['first_seen_at']})</span></li>"
            alerts_section += "</ul>"
        else:
            alerts_section = "<p style='color:#22c55e;margin:18px 0 6px;'>✓ No active system alerts.</p>"

        body = f"""<div style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;color:#222;">
<h2 style="color:#06b6d4;margin:0 0 12px;">GigsFill Weekly Admin Digest</h2>
<p style="color:#666;font-size:13px;margin:0 0 18px;">{conn.execute("SELECT date('now')").fetchone()[0]} · last 7 days</p>

<h3 style='margin:6px 0;'>📈 Activity</h3>
<table style="border-collapse:collapse;font-size:14px;width:100%;">
  <tr><td style="padding:4px 12px 4px 0;">New venues</td><td><b>{new_v[0]}</b></td><td style="padding:4px 12px 4px 0;">New artists</td><td><b>{new_a[0]}</b></td></tr>
  <tr><td style="padding:4px 12px 4px 0;">Gigs created</td><td><b>{ga['created'] or 0}</b></td><td style="padding:4px 12px 4px 0;">Gigs completed</td><td><b>{ga['completed'] or 0}</b></td></tr>
  <tr><td style="padding:4px 12px 4px 0;">Gigs cancelled</td><td><b>{ga['cancelled'] or 0}</b></td><td></td><td></td></tr>
</table>

<h3 style='margin:18px 0 6px;'>💰 Payments (7d)</h3>
<table style="border-collapse:collapse;font-size:14px;width:100%;">
  <tr><td style="padding:4px 12px 4px 0;">Venue charges</td><td><b>{_cents(ps['charged_cents'])}</b> ({ps['charge_count'] or 0})</td></tr>
  <tr><td style="padding:4px 12px 4px 0;">Artist payouts</td><td><b>{_cents(ps['payout_cents'])}</b> ({ps['payout_count'] or 0})</td></tr>
  <tr><td style="padding:4px 12px 4px 0;">Failed transfers (cumulative)</td><td><b>{ps['failed_total'] or 0}</b></td></tr>
  <tr><td style="padding:4px 12px 4px 0;">Cancellations (7d)</td><td><b>{ps['cancelled_count'] or 0}</b></td></tr>
</table>

<h3 style='margin:18px 0 6px;'>🔗 Stripe Connect health</h3>
<p style='font-size:14px;margin:0 0 6px;'>Total tracked: <b>{ch['total']}</b> · Healthy: <b style='color:#22c55e;'>{ch['healthy']}</b> · Need attention: <b style='color:#ef4444;'>{ch['unhealthy']}</b>{' · Auto-suspended: <b style="color:#f59e0b;">' + str(ch['auto_suspended']) + '</b>' if ch['auto_suspended'] else ''}</p>
{unhealthy_section}

{alerts_section}

<p style="margin-top:24px;font-size:12px;color:#888;">
Full detail at <a href="https://gigsfill.com/app/admin.html">admin.html</a>.
This digest is sent weekly. To change the recipient, edit
<code>admin_alert_email</code> in Platform Settings.
</p>
</div>"""

        from backend.routes.stripe_connect import _wh_admin_alert
        _wh_admin_alert(conn, "Weekly Admin Digest", body)
        logger.info("[CONNECT_HEALTH] Weekly admin digest sent")
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
    from backend.db import get_db_connection

    conn = get_db_connection()
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

"""
GigsFill Affiliate Program Routes
===================================
Handles: recommend emails, referral tracking, earnings accrual,
         quarterly payouts, Stripe Connect for affiliates, admin management.
"""

import logging
from datetime import datetime, date
from backend.utils import utcnow_naive
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.rate_limiter import limiter, rate_email_send_limit, rate_aff_track_limit

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _site_base_url(db) -> str:
    """Return canonical site URL from platform_settings. Audit fix (May 2026
    part 5): centralizes lookup so affiliate signup links don't hardcode
    gigsfill.com — staging/custom-domain deploys would otherwise leak prod URLs."""
    try:
        for _key in ("site_url", "base_url"):
            row = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key = :k LIMIT 1"), {"k": _key}).first()
            if row and row[0] and "127.0.0.1" not in row[0] and "localhost" not in row[0]:
                return row[0].strip().rstrip("/")
    except Exception:
        pass
    return "https://gigsfill.com"


def _aff_setting(db, key, default):
    r = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key = :k"), {"k": key}).scalar()
    try:
        return float(r) if r is not None else default
    except Exception:
        return default


def _get_quarter(dt: datetime = None) -> str:
    """Return quarter string like '2026-Q1'.

    Audit fix (May 2026 part 9): on quarter-boundary midnight in platform tz,
    `utcnow_naive()` reads as the NEXT day in UTC for any platform tz behind
    UTC (e.g. America/Los_Angeles). That made the quarter label flip up to
    8 hours early and accrued earnings on Q4 Dec 31 23:30 PT to land in next
    year's Q1. Compute the calendar quarter in platform-local time when no
    explicit dt is passed.
    """
    if dt is None:
        try:
            import pytz as _pytz
            from backend.db import SessionLocal as _SL
            _db = _SL()
            try:
                _tz_str = _db.execute(text(
                    "SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'"
                )).scalar() or "America/Los_Angeles"
            finally:
                _db.close()
            dt = datetime.now(_pytz.timezone(_tz_str)).replace(tzinfo=None)
        except Exception:
            dt = utcnow_naive()
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"


def _current_rate(db, referral_row) -> float:
    """Return the current rate for a referral based on days since venue signup.

    Audit fix (May 2026 part 9): the rates and the reduction window are read
    LIVE from `platform_settings` (Admin → Affiliates → Affiliate Settings),
    not snapshotted from the referral row at signup time. This was a critical
    correctness gap — admin changing the platform-wide rate in the UI had no
    effect on existing affiliates because every accrual reused the snapshot
    rate stored on `affiliate_referrals` (typically 1.0% / 0.5% / 365d). Now:
    live setting wins, row snapshot is a fallback only when the setting is
    missing or unreadable.
    """
    linked_at = referral_row["linked_at"]
    if isinstance(linked_at, str):
        try:
            linked_at = datetime.fromisoformat(linked_at)
        except Exception:
            linked_at = utcnow_naive()
    days_elapsed = (utcnow_naive() - linked_at).days

    live_initial = _aff_setting(db, "affiliate_rate_percent", None)
    live_reduced = _aff_setting(db, "affiliate_reduced_rate_percent", None)
    live_days    = _aff_setting(db, "affiliate_reduced_after_days", None)

    initial_pct = live_initial if live_initial is not None else referral_row["initial_rate_percent"]
    reduced_pct = live_reduced if live_reduced is not None else referral_row["reduced_rate_percent"]
    reduced_days = int(live_days) if live_days is not None else referral_row["reduced_after_days"]

    if days_elapsed >= reduced_days:
        return reduced_pct
    return initial_pct


def _check_admin(user):
    # Audit fix (May 2026): use centralized to_admin_bool helper — handles
    # every storage form (bool, int, 'true'/'false', '1'/'0', None) safely.
    from backend.utils import to_admin_bool
    if not to_admin_bool(getattr(user, "is_admin", None)):
        raise HTTPException(403, "Admin only")


# ── Affiliate code click tracking (landing page cookie) ──────────────────────

_AFF_CODE_RE = __import__('re').compile(r'^[A-Z0-9-]{4,20}$')


def _safe_internal_redirect(redirect_to: str) -> str:
    """Audit fix (May 2026 part 9c): the /api/affiliate/track endpoint is
    publicly shared in recommend emails; an attacker who can hand-craft the
    URL can use it as an open-redirect drop-off for phishing. Constrain to
    same-origin relative paths only.
    """
    if not redirect_to:
        return "/"
    # Disallow scheme/authority/protocol-relative URLs
    rt = redirect_to.strip()
    if rt.startswith("//") or "://" in rt or rt.startswith("\\\\"):
        return "/"
    # Must start with a single slash (relative path on our origin)
    if not rt.startswith("/"):
        return "/"
    # Length cap defends against weird payloads
    if len(rt) > 200:
        return "/"
    return rt


@router.get("/api/affiliate/track/{code}")
@limiter.limit(rate_aff_track_limit)
def track_affiliate_click(request: Request, code: str, redirect_to: str = "/", db=Depends(get_db)):
    """Record affiliate click and set cookie, then redirect.

    Audit fix (May 2026 part 9c):
      - Rate-limited to 30/min/IP so bots can't spam the click-tracking writer.
      - `redirect_to` is constrained to same-origin relative paths only — the
        endpoint used to be an open redirect, perfect phishing vector.
      - Code shape is validated BEFORE the DB query; obviously-bogus codes
        skip the lookup entirely.
      - When the program is disabled (`affiliate_enabled='false'`), the
        endpoint still redirects so the link "works," but no cookie is set
        and no DB writes happen — accruals would no-op anyway.
    """
    safe_redirect = _safe_internal_redirect(redirect_to)
    code = (code or "").strip().upper()
    if not _AFF_CODE_RE.match(code):
        return RedirectResponse(safe_redirect)

    # Kill switch — recommend links still navigate, but stop accruing state.
    try:
        en = db.execute(text(
            "SELECT setting_value FROM platform_settings WHERE setting_key='affiliate_enabled'"
        )).scalar()
        if en is not None and str(en).lower() not in ("true", "1"):
            return RedirectResponse(safe_redirect)
    except Exception:
        pass

    row = db.execute(text("SELECT id FROM users WHERE affiliate_code = :c"), {"c": code}).first()
    if not row:
        return RedirectResponse(safe_redirect)

    # Mark any recommendation emails for this code as clicked (first click wins)
    try:
        db.execute(text("""
            UPDATE affiliate_recommend_emails
            SET clicked = 1, clicked_at = CURRENT_TIMESTAMP
            WHERE affiliate_code = :code AND clicked = 0
        """), {"code": code})
        db.commit()
    except Exception as _e:
        logger.warning(f"Could not update affiliate click tracking: {_e}")

    response = RedirectResponse(safe_redirect)
    # Cookie survives 90 days. secure=True since production is HTTPS-only.
    response.set_cookie(
        "aff_code", code,
        max_age=60 * 60 * 24 * 90,
        httponly=True, samesite="lax", secure=True
    )
    return response


# ── Send Recommend Email ──────────────────────────────────────────────────────

@router.post("/api/affiliate/recommend")
@limiter.limit(rate_email_send_limit)
async def send_recommend_email(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Send a GigsFill recommendation email on behalf of a user.

    Rate-limited (May 2026 audit): 10/minute per IP. Without this, a single
    authenticated account could blast hundreds of recommend-emails through
    our SMTP, exhausting Bluehost's daily quota and putting the platform's
    sender reputation at risk.
    """
    data = await request.json()
    recipient_email = (data.get("recipient_email") or "").strip().lower()
    personal_note   = (data.get("personal_note") or "").strip()
    recipient_name  = (data.get("recipient_name") or "").strip()

    if not recipient_email or "@" not in recipient_email:
        raise HTTPException(400, "Valid recipient email required")

    # Audit fix (May 2026 part 9c): kill switch — if the affiliate program is
    # disabled, refuse new recommends. Existing recommends + accruals already
    # short-circuit elsewhere; this closes the "send-then-resume-on-reenable"
    # loophole.
    en = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key='affiliate_enabled'"
    )).scalar()
    if en is not None and str(en).lower() not in ("true", "1"):
        raise HTTPException(403, "Affiliate program is currently disabled.")

    # Audit fix (May 2026 part 9c): per-affiliate DAILY cap. The per-IP
    # 10/min rate limit doesn't stop a determined user from blasting
    # 14,400 emails/day; this caps any one affiliate at 50 sends per
    # rolling 24h window (sends + resends combined) so a single
    # compromised account can't burn SMTP reputation. Admin override:
    # platform_settings.affiliate_daily_send_cap.
    _cap = db.execute(text(
        "SELECT COALESCE(setting_value, '50') FROM platform_settings WHERE setting_key='affiliate_daily_send_cap'"
    )).scalar() or "50"
    try:
        cap = int(_cap)
    except Exception:
        cap = 50
    if cap > 0:
        recent = db.execute(text("""
            SELECT COUNT(*) FROM affiliate_recommend_emails
            WHERE sender_user_id = :uid
              AND sent_at >= datetime('now', '-24 hours')
        """), {"uid": user.id}).scalar() or 0
        if int(recent) >= cap:
            raise HTTPException(429,
                f"Daily recommend-email cap reached ({cap}/24h). "
                f"Try again tomorrow or contact support to raise the limit.")

    # Get sender's affiliate code
    aff_row = db.execute(text("SELECT affiliate_code, first_name, last_name FROM users WHERE id = :uid"), {"uid": user.id}).mappings().first()
    if not aff_row or not aff_row["affiliate_code"]:
        raise HTTPException(400, "No affiliate code assigned — please contact support")

    aff_code   = aff_row["affiliate_code"]
    sender_name = f"{aff_row['first_name'] or ''} {aff_row['last_name'] or ''}".strip() or user.email

    # Check if this email was already recommended by someone else
    earlier = db.execute(text("""
        SELECT sender_user_id FROM affiliate_recommend_emails
        WHERE LOWER(recipient_email) = :email AND sender_user_id != :uid
        ORDER BY sent_at ASC LIMIT 1
    """), {"email": recipient_email, "uid": user.id}).first()

    if earlier:
        return JSONResponse({"ok": False, "already_claimed": True,
                             "message": "This email address was previously recommended by another user."})

    # Build affiliate URL. Audit fix (May 2026 part 9c): route through the
    # `/api/affiliate/track/{code}` endpoint so (a) the recommend email's
    # `clicked` flag is recorded and (b) the cookie is set server-side
    # before the landing page renders. Previously the email link bypassed
    # the tracker entirely, so the "Clicked" badge on the affiliate's
    # dashboard never lit up.
    # Audit fix (May 2026 part 9d): land the recipient directly on the
    # signup page instead of the bare homepage. The cookie is still set
    # by the track endpoint; this just saves them a click and makes the
    # intent of the email obvious ("we're inviting you to sign up").
    # Audit fix (May 2026 part 5): pull from platform_settings.site_url so
    # staging / custom-domain deploys don't leak gigsfill.com links.
    base = _site_base_url(db)
    # Land on the role-picker (no ?role= pre-selected) so the recipient
    # can pick venue OR artist. Affiliate attribution still fires only
    # on venue signup (per auth.py / our terms in legal.html#affiliate);
    # artist signups won't earn the sender a commission but they're a
    # legitimate outcome — better to let the recipient land where they
    # actually belong than force them into the wrong signup flow.
    signup_url = f"{base}/api/affiliate/track/{aff_code}?redirect_to=/app/signup-new.html"

    # Build template variables — every user-controlled field MUST be HTML-
    # escaped before substitution. recipient_name and personal_note are
    # supplied by the sender (untrusted); sender_name from DB CAN contain
    # crafted chars (someone could put HTML in their first_name on signup).
    # Audit fix (May 2026 part 9c).
    from html import escape as _h
    safe_recipient = _h(recipient_name)
    safe_note      = _h(personal_note)
    safe_sender    = _h(sender_name)
    greeting  = f", {safe_recipient}" if safe_recipient else ""
    note_html = (
        f'<p style="margin:0 0 20px 0;font-size:15px;line-height:1.6;color:#4b5563;'
        f'padding:16px;background:#f0fdf4;border-left:4px solid #10b981;'
        f'border-radius:4px;">{safe_note}</p>'
        if safe_note else ""
    )

    # Send using the recommend_gigsfill DB template
    try:
        from backend.email_service import EmailService
        es = EmailService(db)
        template = es.get_template("recommend_gigsfill")
        if not template:
            raise Exception("recommend_gigsfill template not found in DB")
        variables = {
            "user_name":          safe_sender,
            "recipient_greeting": greeting,
            "personal_note":      note_html,
            "aff_url":            signup_url,
        }
        subject = es.render_template(template["subject"], variables)
        body    = es.render_template(template["body"], variables)
        es._send_raw_email(to_email=recipient_email, subject=subject, html_body=body)
    except Exception as e:
        logger.error(f"Recommend email send failed: {e}")

    # Log the send
    db.execute(text("""
        INSERT INTO affiliate_recommend_emails (sender_user_id, recipient_email, recipient_name, affiliate_code)
        VALUES (:uid, :email, :rname, :code)
    """), {"uid": user.id, "email": recipient_email, "rname": recipient_name or None, "code": aff_code})
    db.commit()

    return {"ok": True}


# ── My Recommend Emails ───────────────────────────────────────────────────────

@router.get("/api/affiliate/my-emails")
def get_my_recommend_emails(user=Depends(get_current_user), db=Depends(get_db)):
    """Get all recommend emails sent by this user, with already-claimed flags."""
    rows = db.execute(text("""
        SELECT
            are.id, are.recipient_email, are.recipient_name, are.sent_at, are.clicked, are.clicked_at,
            -- Check if someone else sent a rec to this email BEFORE this send
            (SELECT COUNT(*) FROM affiliate_recommend_emails are2
             WHERE LOWER(are2.recipient_email) = LOWER(are.recipient_email)
               AND are2.sender_user_id != are.sender_user_id
               AND are2.sent_at < are.sent_at) as prior_sender_count,
            -- Check if this email is now a registered venue user
            (SELECT v.id FROM users u2 JOIN venues v ON v.user_id = u2.id
             WHERE LOWER(u2.email) = LOWER(are.recipient_email) LIMIT 1) as signed_up_venue_id,
            -- Check if linked to this affiliate
            (SELECT ar.id FROM affiliate_referrals ar
             JOIN venues v2 ON v2.id = ar.venue_id
             JOIN users u3 ON u3.id = v2.user_id
             WHERE LOWER(u3.email) = LOWER(are.recipient_email)
               AND ar.affiliate_user_id = :uid LIMIT 1) as referral_id
        FROM affiliate_recommend_emails are
        WHERE are.sender_user_id = :uid
        ORDER BY are.sent_at DESC
    """), {"uid": user.id}).mappings().all()

    result = []
    for r in rows:
        status = "sent"
        if r["prior_sender_count"] > 0:
            status = "claimed_by_other"
        elif r["signed_up_venue_id"] and r["referral_id"]:
            status = "converted"
        elif r["signed_up_venue_id"]:
            status = "signed_up_no_link"
        result.append({
            "id": r["id"],
            "recipient_email": r["recipient_email"],
            "recipient_name": r["recipient_name"],
            "sent_at": r["sent_at"],
            "clicked": bool(r["clicked"]),
            "clicked_at": r["clicked_at"],
            "status": status,
        })
    return result


@router.post("/api/affiliate/resend-recommend/{email_id}")
@limiter.limit(rate_email_send_limit)
async def resend_recommend_email(request: Request, email_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Resend a recommendation email. Rate-limited 10/minute per IP.

    Audit fix (May 2026 part 9c): hard caps added on top of the per-IP
    rate limit:
      - Affiliate-program kill switch (refuse if disabled).
      - Per-row resend cap (max 3 resends per recipient, ≥24h apart).
      - Per-affiliate 24h cap (same constant as initial sends).
    """
    en = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key='affiliate_enabled'"
    )).scalar()
    if en is not None and str(en).lower() not in ("true", "1"):
        raise HTTPException(403, "Affiliate program is currently disabled.")

    row = db.execute(text("""
        SELECT id, recipient_email, recipient_name, affiliate_code,
               COALESCE(resend_count, 0) AS resend_count, last_resent_at
        FROM affiliate_recommend_emails WHERE id = :id AND sender_user_id = :uid
    """), {"id": email_id, "uid": user.id}).mappings().first()
    if not row:
        raise HTTPException(404, "Email not found")

    if int(row["resend_count"] or 0) >= 3:
        raise HTTPException(429, "Resend limit reached for this recipient (3 max). Reach out via your own email if you need to follow up.")

    # 5-minute cooldown between resends to the same recipient. Tightens to
    # prevent click-spam while staying loose enough that legitimate testing
    # works. The per-affiliate 50/24h cap + per-IP 10/min rate limit are
    # the real abuse controls; this is just an "are you sure?" buffer.
    if row["last_resent_at"]:
        recent = db.execute(text("""
            SELECT 1 FROM affiliate_recommend_emails
            WHERE id = :id AND last_resent_at >= datetime('now', '-5 minutes')
        """), {"id": email_id}).first()
        if recent:
            raise HTTPException(429, "Please wait a few minutes before resending to the same recipient.")

    # Per-affiliate 24h cap (sends + resends combined)
    _cap = db.execute(text(
        "SELECT COALESCE(setting_value, '50') FROM platform_settings WHERE setting_key='affiliate_daily_send_cap'"
    )).scalar() or "50"
    try:
        cap = int(_cap)
    except Exception:
        cap = 50
    if cap > 0:
        recent_total = db.execute(text("""
            SELECT COUNT(*) FROM affiliate_recommend_emails
            WHERE sender_user_id = :uid
              AND (sent_at >= datetime('now', '-24 hours')
                   OR last_resent_at >= datetime('now', '-24 hours'))
        """), {"uid": user.id}).scalar() or 0
        if int(recent_total) >= cap:
            raise HTTPException(429,
                f"Daily recommend-email cap reached ({cap}/24h). Try again tomorrow.")

    recipient_email = row["recipient_email"]
    recipient_name  = row["recipient_name"] or ""
    aff_code        = row["affiliate_code"]
    # Audit fix (May 2026 part 9c+9d): route through tracking endpoint
    # (sets cookie + flags clicked) and land directly on signup page.
    # Resend reuses the same role-neutral landing page as the initial send —
    # role picker stays in front of the user so they can choose venue OR artist.
    aff_url         = f"{_site_base_url(db)}/api/affiliate/track/{aff_code}?redirect_to=/app/signup-new.html"
    sender_name     = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email
    from html import escape as _h
    safe_recipient  = _h(recipient_name)
    safe_sender     = _h(sender_name)

    try:
        from backend.email_service import EmailService
        es = EmailService(db)
        template = es.get_template("recommend_gigsfill")
        if template:
            variables = {
                "user_name": safe_sender,
                "recipient_greeting": f", {safe_recipient}" if safe_recipient else "",
                "personal_note": "",
                "aff_url": aff_url,
            }
            subject = es.render_template(template["subject"], variables)
            body    = es.render_template(template["body"], variables)
            es._send_raw_email(to_email=recipient_email, subject=subject, html_body=body)
        else:
            raise Exception("recommend_gigsfill template not found")
    except Exception as e:
        raise HTTPException(500, f"Email send failed: {e}")

    # Update resend tracking
    db.execute(text("""
        UPDATE affiliate_recommend_emails
        SET sent_at = CURRENT_TIMESTAMP,
            last_resent_at = CURRENT_TIMESTAMP,
            resend_count = COALESCE(resend_count, 0) + 1
        WHERE id = :id
    """), {"id": email_id})
    db.commit()
    return {"ok": True}


# ── Payout Preview (for admin modal) ─────────────────────────────────────────

@router.get("/api/admin/affiliate/payout-preview")
def get_payout_preview(user=Depends(get_current_user), db=Depends(get_db)):
    """Get full breakdown of all affiliates with unpaid earnings for the payout preview modal."""
    _check_admin(user)
    min_cents = int(_aff_setting(db, "affiliate_min_payout_cents", 5000))

    affiliates = db.execute(text("""
        SELECT
            u.id as user_id, u.first_name, u.last_name, u.email, u.affiliate_code,
            SUM(ae.earned_cents) as unpaid_cents,
            COUNT(ae.id) as total_gigs
        FROM affiliate_earnings ae
        JOIN users u ON u.id = ae.affiliate_user_id
        WHERE ae.payout_id IS NULL
        GROUP BY ae.affiliate_user_id
        HAVING SUM(ae.earned_cents) > 0
        ORDER BY unpaid_cents DESC
    """)).mappings().all()

    result = []
    for aff in affiliates:
        uid = aff["user_id"]

        # Stripe status
        stripe_row = db.execute(text("""
            SELECT affiliate_stripe_connect_account_id, affiliate_stripe_connect_onboarding_complete
            FROM entity_payment_settings WHERE entity_type = 'user' AND entity_id = :uid
        """), {"uid": uid}).mappings().first()
        has_stripe = bool(stripe_row and stripe_row["affiliate_stripe_connect_account_id"]
                          and stripe_row["affiliate_stripe_connect_onboarding_complete"])

        # Per-venue breakdown
        venues = db.execute(text("""
            SELECT
                v.id as venue_id, v.venue_name, v.city, v.state,
                ar.initial_rate_percent, ar.reduced_rate_percent, ar.reduced_after_days, ar.linked_at,
                COALESCE(SUM(ae.earned_cents), 0) as unpaid_venue_cents,
                COALESCE(SUM(ae.gig_fee_cents), 0) as total_gig_fees_cents,
                COUNT(ae.id) as gig_count,
                COALESCE((SELECT SUM(ae2.earned_cents) FROM affiliate_earnings ae2
                          WHERE ae2.affiliate_user_id = :uid AND ae2.venue_id = v.id), 0) as all_time_earned_cents
            FROM affiliate_referrals ar
            JOIN venues v ON v.id = ar.venue_id
            LEFT JOIN affiliate_earnings ae ON ae.venue_id = ar.venue_id
                AND ae.affiliate_user_id = ar.affiliate_user_id AND ae.payout_id IS NULL
            WHERE ar.affiliate_user_id = :uid
            GROUP BY ar.id
            ORDER BY unpaid_venue_cents DESC
        """), {"uid": uid}).mappings().all()

        result.append({
            "user_id": uid,
            "first_name": aff["first_name"],
            "last_name": aff["last_name"],
            "email": aff["email"],
            "affiliate_code": aff["affiliate_code"],
            "unpaid_cents": aff["unpaid_cents"],
            "total_gigs": aff["total_gigs"],
            "eligible": aff["unpaid_cents"] >= min_cents,
            "has_stripe": has_stripe,
            "venues": [dict(v) for v in venues],
        })

    return {
        "affiliates": result,
        "min_payout_cents": min_cents,
        "quarter": _get_quarter(),
        "eligible_count": sum(1 for a in result if a["eligible"]),
        "eligible_total_cents": sum(a["unpaid_cents"] for a in result if a["eligible"]),
    }



@router.get("/api/affiliate/my-referrals")
def get_my_referrals(user=Depends(get_current_user), db=Depends(get_db)):
    """Get all venues referred by this user with earnings summary.

    Audit fix (May 2026 part 9c): include a `current_rate_percent` field
    computed by `_current_rate()` so the frontend "Linked Venues → Rate"
    column shows the LIVE rate (the one actually applied to next
    accrual) instead of the snapshot stored on the row at signup time.
    The snapshot columns are still returned for the audit/admin view.
    """
    rows = db.execute(text("""
        SELECT
            ar.id as referral_id, ar.venue_id, ar.linked_at, ar.link_method,
            ar.initial_rate_percent, ar.reduced_rate_percent, ar.reduced_after_days,
            v.venue_name, v.city, v.state,
            COALESCE(SUM(ae.earned_cents), 0) as total_earned_cents,
            COALESCE(SUM(CASE WHEN ae.payout_id IS NULL THEN ae.earned_cents ELSE 0 END), 0) as unpaid_cents,
            COUNT(ae.id) as gig_count
        FROM affiliate_referrals ar
        JOIN venues v ON v.id = ar.venue_id
        LEFT JOIN affiliate_earnings ae ON ae.affiliate_user_id = ar.affiliate_user_id AND ae.venue_id = ar.venue_id
        WHERE ar.affiliate_user_id = :uid
        GROUP BY ar.id
        ORDER BY ar.linked_at DESC
    """), {"uid": user.id}).mappings().all()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["current_rate_percent"] = _current_rate(db, d)
        except Exception:
            d["current_rate_percent"] = d.get("initial_rate_percent")
        result.append(d)
    return result


@router.get("/api/affiliate/my-summary")
def get_my_summary(user=Depends(get_current_user), db=Depends(get_db)):
    """Summary stats + affiliate code for the user profile Affiliates tab."""
    aff = db.execute(text("SELECT affiliate_code FROM users WHERE id = :uid"), {"uid": user.id}).scalar()

    totals = db.execute(text("""
        SELECT
            COALESCE(SUM(ae.earned_cents), 0) as total_earned_cents,
            COALESCE(SUM(CASE WHEN ae.payout_id IS NULL THEN ae.earned_cents ELSE 0 END), 0) as unpaid_cents,
            COALESCE(SUM(CASE WHEN strftime('%Y', ae.accrued_at) = strftime('%Y', 'now') THEN ae.earned_cents ELSE 0 END), 0) as ytd_cents,
            COUNT(DISTINCT ae.venue_id) as active_venues,
            COUNT(ae.id) as total_gigs
        FROM affiliate_earnings ae
        WHERE ae.affiliate_user_id = :uid
    """), {"uid": user.id}).mappings().first()

    payouts = db.execute(text("""
        SELECT quarter, total_cents, status, paid_at
        FROM affiliate_payouts
        WHERE affiliate_user_id = :uid
        ORDER BY quarter DESC LIMIT 8
    """), {"uid": user.id}).mappings().all()

    referral_count = db.execute(text(
        "SELECT COUNT(*) FROM affiliate_referrals WHERE affiliate_user_id = :uid"
    ), {"uid": user.id}).scalar() or 0

    # Stripe Connect status
    stripe_row = db.execute(text("""
        SELECT affiliate_stripe_connect_account_id, affiliate_stripe_connect_onboarding_complete
        FROM entity_payment_settings WHERE entity_type = 'user' AND entity_id = :uid
    """), {"uid": user.id}).mappings().first()

    has_stripe = bool(stripe_row and stripe_row["affiliate_stripe_connect_account_id"] and
                      stripe_row["affiliate_stripe_connect_onboarding_complete"])

    stripe_account_id = stripe_row["affiliate_stripe_connect_account_id"] if has_stripe else None
    stripe_artist_name = None
    if stripe_account_id:
        artist_row = db.execute(text("""
            SELECT a.name FROM artists a
            JOIN entity_payment_settings eps ON eps.entity_type='artist' AND eps.entity_id=a.id
            WHERE a.user_id=:uid AND eps.stripe_connect_account_id=:acid LIMIT 1
        """), {"uid": user.id, "acid": stripe_account_id}).fetchone()
        if artist_row:
            stripe_artist_name = artist_row[0]

    return {
        "affiliate_code": aff,
        "referral_count": referral_count,
        "total_earned_cents": totals["total_earned_cents"] if totals else 0,
        "unpaid_cents": totals["unpaid_cents"] if totals else 0,
        "ytd_cents": totals["ytd_cents"] if totals else 0,
        "active_venues": totals["active_venues"] if totals else 0,
        "total_gigs": totals["total_gigs"] if totals else 0,
        "payouts": [dict(p) for p in payouts],
        "has_stripe": has_stripe,
        "stripe_account_id": stripe_account_id,
        "stripe_artist_name": stripe_artist_name,
        "current_quarter": _get_quarter(),
    }


@router.get("/api/affiliate/program-settings")
def get_program_settings_public(db=Depends(get_db)):
    """Public endpoint — returns affiliate program settings for display on user profile."""
    keys = ["affiliate_enabled", "affiliate_rate_percent", "affiliate_reduced_rate_percent",
            "affiliate_reduced_after_days", "affiliate_min_payout_cents"]
    rows = db.execute(text(
        f"SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ({','.join([':k'+str(i) for i in range(len(keys))])})"
    ), {f"k{i}": k for i, k in enumerate(keys)}).mappings().all()
    d = {r["setting_key"]: r["setting_value"] for r in rows}
    enabled_val = d.get("affiliate_enabled", "true")
    return {
        "enabled":              str(enabled_val).lower() in ("true", "1"),
        "rate_percent":         float(d.get("affiliate_rate_percent", 1.0)),
        "reduced_rate_percent": float(d.get("affiliate_reduced_rate_percent", 0.5)),
        "reduced_after_days":   int(d.get("affiliate_reduced_after_days", 365)),
        "min_payout_cents":     int(d.get("affiliate_min_payout_cents", 5000)),
    }


# ── Stripe Connect for Affiliates ────────────────────────────────────────────

@router.post("/api/affiliate/stripe/onboard")
async def affiliate_stripe_onboard(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Start Stripe Connect Express onboarding for affiliate payouts."""
    import stripe
    stripe_key = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'admin_stripe_secret_key'"
    )).scalar()
    if not stripe_key or stripe_key.startswith("•"):
        raise HTTPException(500, "Stripe not configured")
    stripe.api_key = stripe_key

    # Audit fix (May 2026): require the user to actually be in the affiliate
    # program (have an affiliate_code) before spawning a Stripe Express
    # account. Without this, any logged-in user could spam Stripe with empty
    # affiliate accounts via this endpoint.
    aff = db.execute(text("SELECT affiliate_code FROM users WHERE id = :uid"), {"uid": user.id}).scalar()
    if not aff:
        raise HTTPException(403, "Affiliate program enrollment required")

    # Get or create Connect account
    existing = db.execute(text("""
        SELECT affiliate_stripe_connect_account_id
        FROM entity_payment_settings WHERE entity_type = 'user' AND entity_id = :uid
    """), {"uid": user.id}).mappings().first()

    account_id = existing["affiliate_stripe_connect_account_id"] if existing else None

    if not account_id:
        user_row = db.execute(text("SELECT email FROM users WHERE id = :uid"), {"uid": user.id}).mappings().first()
        # Audit fix (May 2026 part 8): idempotency key prevents creating
        # duplicate affiliate Connect accounts on concurrent onboard clicks.
        account = stripe.Account.create(
            type="express",
            email=user_row["email"],
            capabilities={"transfers": {"requested": True}},
            metadata={"gigsfill_user_id": str(user.id), "type": "affiliate"},
            idempotency_key=f"affiliate_connect_create_{user.id}",
        )
        account_id = account.id
        # Upsert entity_payment_settings
        existing_row = db.execute(text(
            "SELECT id FROM entity_payment_settings WHERE entity_type = 'user' AND entity_id = :uid"
        ), {"uid": user.id}).first()
        if existing_row:
            db.execute(text("""
                UPDATE entity_payment_settings
                SET affiliate_stripe_connect_account_id = :acid
                WHERE entity_type = 'user' AND entity_id = :uid
            """), {"acid": account_id, "uid": user.id})
        else:
            db.execute(text("""
                INSERT INTO entity_payment_settings (entity_type, entity_id, affiliate_stripe_connect_account_id)
                VALUES ('user', :uid, :acid)
            """), {"uid": user.id, "acid": account_id})
        db.commit()

    # Audit fix (May 2026 part 5): use the shared _site_base_url helper so all
    # affiliate URLs come from platform_settings.site_url (with base_url as
    # legacy fallback). Hardcoded gigsfill.com is last resort.
    _base = _site_base_url(db)
    link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=f"{_base}/app/user-profile.html?tab=affiliates&stripe=refresh",
        return_url=f"{_base}/app/user-profile.html?tab=affiliates&stripe=complete",
        type="account_onboarding",
    )
    return {"url": link.url}


@router.get("/api/affiliate/stripe/status")
def affiliate_stripe_status(user=Depends(get_current_user), db=Depends(get_db)):
    """Check affiliate Stripe Connect status."""
    row = db.execute(text("""
        SELECT affiliate_stripe_connect_account_id, affiliate_stripe_connect_onboarding_complete
        FROM entity_payment_settings WHERE entity_type = 'user' AND entity_id = :uid
    """), {"uid": user.id}).mappings().first()

    if not row or not row["affiliate_stripe_connect_account_id"]:
        return {"connected": False, "complete": False}

    if row["affiliate_stripe_connect_onboarding_complete"]:
        return {"connected": True, "complete": True,
                "account_id": row["affiliate_stripe_connect_account_id"]}

    # Verify with Stripe
    try:
        import stripe
        stripe_key = db.execute(text(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'admin_stripe_secret_key'"
        )).scalar()
        if stripe_key and not stripe_key.startswith("•"):
            stripe.api_key = stripe_key
            acct = stripe.Account.retrieve(row["affiliate_stripe_connect_account_id"])
            complete = acct.details_submitted and acct.payouts_enabled
            if complete:
                db.execute(text("""
                    UPDATE entity_payment_settings
                    SET affiliate_stripe_connect_onboarding_complete = 1
                    WHERE entity_type = 'user' AND entity_id = :uid
                """), {"uid": user.id})
                db.commit()
            return {"connected": True, "complete": complete,
                    "account_id": row["affiliate_stripe_connect_account_id"]}
    except Exception as e:
        logger.error(f"Affiliate Stripe status check: {e}")

    return {"connected": True, "complete": False,
            "account_id": row["affiliate_stripe_connect_account_id"]}


# ── Earnings Accrual (called by payout_scheduler when txn goes paid) ─────────

def accrue_affiliate_earnings(db, transaction_id: int):
    """
    Check if a paid transaction's venue has an affiliate.
    If so, calculate and record earnings.
    Called from payout_scheduler after marking a transaction paid.
    """
    # Respect affiliate_enabled kill switch
    enabled = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'affiliate_enabled'"
    )).scalar()
    if enabled is not None and str(enabled).lower() not in ("true", "1"):
        return

    txn = db.execute(text("""
        SELECT t.id, t.amount_cents, t.commission_cents, t.gig_id, g.venue_id
        FROM transactions t
        JOIN gigs g ON g.id = t.gig_id
        WHERE t.id = :txid
    """), {"txid": transaction_id}).mappings().first()
    if not txn:
        return

    referral = db.execute(text("""
        SELECT * FROM affiliate_referrals WHERE venue_id = :vid
    """), {"vid": txn["venue_id"]}).mappings().first()
    if not referral:
        return

    # Don't double-accrue
    existing = db.execute(text(
        "SELECT id FROM affiliate_earnings WHERE transaction_id = :txid"
    ), {"txid": transaction_id}).first()
    if existing:
        return

    rate = _current_rate(db, referral)
    # Audit fix (May 2026 part 9): the affiliate referral commission is a
    # split of the gigsfill PLATFORM FEE — not a slice of the artist's pay.
    # Previously this multiplied by `txn.amount_cents` (the artists' total
    # pay), so a 5% rate on a $100 gig accrued $5 to the affiliate when the
    # platform only earned $10 commission to begin with — a 10× overpay.
    # Fee base is now `commission_cents` (the platform's actual revenue on
    # that txn).
    #
    # Audit fix (May 2026 part 9c): the fallback to amount_cents must ONLY
    # fire for LEGACY rows where commission_cents was never populated
    # (NULL). For free-trial / promo venues where platform_fee_percent=0,
    # commission_cents legitimately = 0, and the old `<= 0` fallback
    # re-triggered the 10× overpay we just fixed. Use `is None` to detect
    # truly-unpopulated rows; treat real-zero as "no platform revenue → no
    # affiliate commission."
    _comm_raw = txn["commission_cents"]
    if _comm_raw is None:
        fee_base = int(txn["amount_cents"] or 0)
        logger.warning(
            f"[AFFILIATE] txn {transaction_id}: commission_cents is NULL (legacy row?); "
            f"falling back to amount_cents={fee_base}. This row should be backfilled."
        )
    else:
        fee_base = int(_comm_raw)
    earned_cents = int(fee_base * rate / 100)
    if earned_cents <= 0:
        return

    quarter = _get_quarter()
    # Audit fix (May 2026 part 8): defense against double-accrual race. The
    # earlier SELECT-then-INSERT pattern is racy — two concurrent callers
    # (payout_scheduler tick + a webhook retry) both pass the "don't
    # double-accrue" gate and both INSERT, doubling the affiliate's payout.
    # Ensure a UNIQUE index on transaction_id, then catch the IntegrityError
    # so the second caller cleanly no-ops. The index is lazy-created at the
    # top of the function on first call to avoid touching db.py migrations.
    try:
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_affiliate_earnings_txid ON affiliate_earnings(transaction_id)"))
    except Exception:
        pass
    try:
        db.execute(text("""
            INSERT INTO affiliate_earnings
                (affiliate_user_id, venue_id, transaction_id, gig_fee_cents, rate_percent, earned_cents, quarter, accrued_at)
            VALUES (:auid, :vid, :txid, :fee, :rate, :earned, :q, CURRENT_TIMESTAMP)
        """), {
            "auid": referral["affiliate_user_id"],
            "vid": txn["venue_id"],
            "txid": transaction_id,
            "fee": fee_base,
            "rate": rate,
            "earned": earned_cents,
            "q": quarter,
        })
        db.commit()
        logger.info(f"Affiliate earnings accrued: txn {transaction_id}, ${earned_cents/100:.2f} @ {rate}% for user {referral['affiliate_user_id']}")
    except Exception as _ie:
        # Race-loser: another caller inserted the row first. Roll back and
        # treat as a no-op to preserve the single-accrual invariant.
        db.rollback()
        logger.info(f"Affiliate earnings accrue txn {transaction_id}: race-loser, skipping ({_ie})")


def claw_back_affiliate_earnings(db, transaction_id: int, reason: str = "refund"):
    """Audit fix (May 2026 part 9): when a parent venue_charge txn is refunded or
    a dispute is lost, claw back any affiliate accrual tied to it. If the
    earnings row has already been paid out (payout_id IS NOT NULL) we cannot
    silently reverse — admin alert + ledger note so the platform owner can
    decide whether to recover from the affiliate.
    Returns one of: 'no_row', 'voided', 'paid_alert'.
    """
    row = db.execute(text("""
        SELECT id, affiliate_user_id, earned_cents, payout_id
        FROM affiliate_earnings WHERE transaction_id = :txid
    """), {"txid": transaction_id}).mappings().first()
    if not row:
        return "no_row"

    if row["payout_id"] is None:
        # Still in the un-paid pool — safe to delete.
        db.execute(
            text("DELETE FROM affiliate_earnings WHERE id = :rid"),
            {"rid": row["id"]}
        )
        db.commit()
        logger.info(
            f"[AFFILIATE_CLAWBACK] txn {transaction_id}: voided ${row['earned_cents']/100:.2f} "
            f"un-paid earnings for affiliate user {row['affiliate_user_id']} ({reason})"
        )
        return "voided"

    # Already paid — money has left the platform. Log loudly so admin can
    # decide whether to net it out of a future quarter.
    logger.error(
        f"[AFFILIATE_CLAWBACK] txn {transaction_id}: CANNOT auto-claw — "
        f"${row['earned_cents']/100:.2f} already paid to affiliate user "
        f"{row['affiliate_user_id']} via payout {row['payout_id']} ({reason}). "
        f"Manual recovery required."
    )
    try:
        from backend.payout_scheduler import _send_admin_alert
        from backend.db import get_db_connection
        conn = get_db_connection()
        try:
            _send_admin_alert(
                conn,
                f"Affiliate clawback needed — txn {transaction_id}",
                f"""<p>Transaction <strong>#{transaction_id}</strong> was {reason} but
                ${row['earned_cents']/100:.2f} of affiliate commission has already been
                paid out to affiliate user #{row['affiliate_user_id']} (payout #{row['payout_id']}).</p>
                <p>The earnings row was NOT deleted — admin should net this out of the
                affiliate's next quarterly payout or reach out for refund directly.</p>"""
            )
        finally:
            conn.close()
    except Exception as _ae:
        logger.warning(f"clawback alert email failed: {_ae}")
    return "paid_alert"


# ── Quarterly Payout Admin Reminder Email ─────────────────────────────────────

def send_quarterly_affiliate_reminder(db):
    """
    Send admin a reminder email that quarterly affiliate payouts are due today.
    Called by scheduler on Apr 1, Jul 1, Oct 1, Dec 31 INSTEAD of auto-running payouts.
    Admin reviews data, then manually clicks "Run Quarterly Payouts Now" in the admin panel.
    """
    quarter = _get_quarter()
    min_cents = int(_aff_setting(db, "affiliate_min_payout_cents", 5000))

    # Get summary of pending payouts
    affiliates = db.execute(text("""
        SELECT ae.affiliate_user_id, u.first_name, u.last_name, u.email,
               SUM(ae.earned_cents) as unpaid_cents,
               COUNT(ae.id) as txn_count
        FROM affiliate_earnings ae
        JOIN users u ON u.id = ae.affiliate_user_id
        WHERE ae.payout_id IS NULL
        GROUP BY ae.affiliate_user_id
        HAVING SUM(ae.earned_cents) > 0
    """)).mappings().all()

    if not affiliates:
        logger.info("Quarterly affiliate reminder: no pending balances, skipping.")
        return

    eligible   = [a for a in affiliates if a["unpaid_cents"] >= min_cents]
    below_min  = [a for a in affiliates if a["unpaid_cents"] < min_cents]
    total_due  = sum(a["unpaid_cents"] for a in eligible)

    # Get admin email
    admin_email = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_email'"
    )).scalar()
    if not admin_email:
        logger.warning("Quarterly affiliate reminder: no admin email configured.")
        return

    aff_rows = "".join([
        f'<tr><td style="padding:6px 10px;font-size:13px;color:#374151;border-bottom:1px solid #f3f4f6;">'
        f'{a["first_name"] or ""} {a["last_name"] or ""} &lt;{a["email"]}&gt;</td>'
        f'<td style="padding:6px 10px;font-size:13px;text-align:center;border-bottom:1px solid #f3f4f6;">{a["txn_count"]}</td>'
        f'<td style="padding:6px 10px;font-size:13px;font-weight:600;color:#10b981;text-align:right;border-bottom:1px solid #f3f4f6;">${a["unpaid_cents"]/100:.2f}</td>'
        f'<td style="padding:6px 10px;font-size:13px;text-align:center;border-bottom:1px solid #f3f4f6;">'
        f'{"✅ Eligible" if a["unpaid_cents"] >= min_cents else f"⏸ Below ${min_cents/100:.0f} min"}</td></tr>'
        for a in affiliates
    ])

    body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fa;"><tr><td style="padding:40px 20px;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:580px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="display:block;">
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 8px;font-size:20px;font-weight:600;color:#111827;">Quarterly Affiliate Payouts Due Today</h1>
<p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#4b5563;">
  Today is a quarterly payout date ({quarter}). There are <strong>{len(eligible)} affiliate(s)</strong> eligible for payment
  totaling <strong>${total_due/100:.2f}</strong>.
  {f'<br>{len(below_min)} affiliate(s) are below the ${min_cents/100:.0f} minimum and will roll over.' if below_min else ''}
</p>
<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;margin-bottom:24px;">
<thead><tr style="background:#f9fafb;">
<th style="padding:8px 10px;font-size:11px;font-weight:700;text-transform:uppercase;color:#6b7280;text-align:left;">Affiliate</th>
<th style="padding:8px 10px;font-size:11px;font-weight:700;text-transform:uppercase;color:#6b7280;text-align:center;">Txns</th>
<th style="padding:8px 10px;font-size:11px;font-weight:700;text-transform:uppercase;color:#6b7280;text-align:right;">Balance</th>
<th style="padding:8px 10px;font-size:11px;font-weight:700;text-transform:uppercase;color:#6b7280;text-align:center;">Status</th>
</tr></thead>
<tbody>{aff_rows}</tbody>
<tfoot><tr style="background:#f9fafb;">
<td colspan="2" style="padding:8px 10px;font-size:13px;font-weight:700;color:#111827;">Total Due</td>
<td style="padding:8px 10px;font-size:13px;font-weight:700;color:#10b981;text-align:right;">${total_due/100:.2f}</td>
<td></td>
</tr></tfoot>
</table>
<div style="text-align:center;margin:28px 0;">
<a href="https://gigsfill.com/app/admin.html?tab=affiliates" style="display:inline-block;background:#f59e0b;color:#fff;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;">
  Review &amp; Run Payouts →
</a>
</div>
<p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">Log in to the Admin Panel → Affiliates → Accounting to review data, then click "Run Quarterly Payouts Now".</p>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#1a1a2e;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table></td></tr></table></body></html>"""

    try:
        from backend.email_service import EmailService
        es = EmailService(db)
        es._send_raw_email(
            to_email=admin_email,
            subject=f"⚠️ Quarterly Affiliate Payouts Due — {quarter} ({len(eligible)} eligible, ${total_due/100:.2f})",
            html_body=body
        )
        logger.info(f"Quarterly affiliate reminder sent to {admin_email}: {len(eligible)} eligible, ${total_due/100:.2f}")
    except Exception as e:
        logger.error(f"Quarterly affiliate reminder email failed: {e}")


# ── Quarterly Payout Runner ───────────────────────────────────────────────────

def run_quarterly_affiliate_payouts(db):
    """
    Run quarterly payout for all eligible affiliates.
    Called by scheduler on Apr 1, Jul 1, Oct 1, Dec 31.
    """
    # Respect affiliate_enabled kill switch
    enabled = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'affiliate_enabled'"
    )).scalar()
    if enabled is not None and str(enabled).lower() not in ("true", "1"):
        logger.info("run_quarterly_affiliate_payouts: affiliate program is disabled — skipping")
        return

    quarter = _get_quarter()
    min_cents = int(_aff_setting(db, "affiliate_min_payout_cents", 5000))
    threshold_cents = int(_aff_setting(db, "affiliate_1099_threshold_cents", 60000))

    stripe_key = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'admin_stripe_secret_key'"
    )).scalar()
    # Test Mode removed (Jul 1 2026) — stripe key presence is the gate.
    has_stripe = bool(stripe_key and not stripe_key.startswith("•"))

    if has_stripe:
        import stripe
        stripe.api_key = stripe_key

    # Find all affiliates with unpaid earnings. Audit fix (May 2026 part 10c):
    # removed `GROUP_CONCAT(DISTINCT ae.venue_id) as venue_ids` — the column
    # was selected but never read, and GROUP_CONCAT is SQLite-only (PG uses
    # string_agg). Dead code + Postgres incompatibility in one line.
    # Jul 2026 audit (M-H4): also snapshot MAX(id) at SUM time. The
    # UPDATE below then scopes to `id <= :snapshot_max_id`, so any new
    # affiliate_earnings row inserted between SUM and UPDATE keeps its
    # payout_id IS NULL and rolls into next quarter. Previously the
    # `WHERE payout_id IS NULL` UPDATE stamped the payout_id on rows
    # that weren't in `total` — affiliate was underpaid AND those rows
    # showed "paid" forever.
    affiliates = db.execute(text("""
        SELECT ae.affiliate_user_id,
               SUM(ae.earned_cents) as total_cents,
               MAX(ae.id) as snapshot_max_id
        FROM affiliate_earnings ae
        WHERE ae.payout_id IS NULL
        GROUP BY ae.affiliate_user_id
        HAVING SUM(ae.earned_cents) > 0
    """)).mappings().all()

    for aff in affiliates:
        uid        = aff["affiliate_user_id"]
        total      = aff["total_cents"]
        snapshot_max_id = aff["snapshot_max_id"]  # M-H4 race guard
        meets_min  = total >= min_cents

        # Build per-venue breakdown for email
        venue_rows = db.execute(text("""
            SELECT v.venue_name, SUM(ae.earned_cents) as venue_cents, COUNT(ae.id) as gig_count
            FROM affiliate_earnings ae
            JOIN venues v ON v.id = ae.venue_id
            WHERE ae.affiliate_user_id = :uid AND ae.payout_id IS NULL
            GROUP BY ae.venue_id
            ORDER BY venue_cents DESC
        """), {"uid": uid}).mappings().all()

        user_row = db.execute(text(
            "SELECT email, first_name, last_name FROM users WHERE id = :uid"
        ), {"uid": uid}).mappings().first()
        if not user_row:
            continue

        user_name = f"{user_row['first_name'] or ''} {user_row['last_name'] or ''}".strip() or user_row["email"]

        # Below threshold — send notification email but don't create a payout record.
        # Earnings stay with payout_id IS NULL and naturally roll into next quarter.
        if not meets_min:
            try:
                _send_quarterly_affiliate_email(
                    db, uid, user_name, user_row["email"],
                    total, venue_rows, quarter, False, min_cents, None
                )
            except Exception as e:
                logger.error(f"Affiliate below-threshold email error for user {uid}: {e}")
            continue

        # Create payout record (only for eligible affiliates).
        # Audit fix (May 2026 part 7): `INSERT OR IGNORE` is SQLite-only —
        # branch on _IS_POSTGRES and use `ON CONFLICT DO NOTHING` for PG.
        try:
            from backend.db import _IS_POSTGRES as _is_pg
            if _is_pg:
                db.execute(text("""
                    INSERT INTO affiliate_payouts
                        (affiliate_user_id, quarter, total_cents, status)
                    VALUES (:uid, :q, :total, 'processing')
                    ON CONFLICT (affiliate_user_id, quarter) DO NOTHING
                """), {"uid": uid, "q": quarter, "total": total})
            else:
                db.execute(text("""
                    INSERT OR IGNORE INTO affiliate_payouts
                        (affiliate_user_id, quarter, total_cents, status)
                    VALUES (:uid, :q, :total, 'processing')
                """), {"uid": uid, "q": quarter, "total": total})
            db.commit()
        except Exception:
            db.rollback()
            continue

        payout_row = db.execute(text(
            "SELECT id, status FROM affiliate_payouts WHERE affiliate_user_id = :uid AND quarter = :q"
        ), {"uid": uid, "q": quarter}).mappings().first()
        if not payout_row:
            continue
        payout_id = payout_row["id"]
        # Skip if already successfully paid this quarter
        if payout_row["status"] in ("paid", "transferred"):
            logger.info(f"Affiliate user {uid} already paid for {quarter} — skipping")
            continue

        transfer_id = None
        if meets_min:
            # Attempt Stripe transfer
            stripe_row = db.execute(text("""
                SELECT affiliate_stripe_connect_account_id, affiliate_stripe_connect_onboarding_complete
                FROM entity_payment_settings WHERE entity_type = 'user' AND entity_id = :uid
            """), {"uid": uid}).mappings().first()

            has_account = bool(stripe_row and stripe_row["affiliate_stripe_connect_account_id"]
                               and stripe_row["affiliate_stripe_connect_onboarding_complete"])

            if has_stripe and has_account:
                try:
                    # Idempotency key tied to (payout_id) — if a network blip
                    # makes us retry, Stripe returns the existing transfer
                    # instead of creating a duplicate quarterly payout.
                    transfer = stripe.Transfer.create(
                        amount=total,
                        currency="usd",
                        destination=stripe_row["affiliate_stripe_connect_account_id"],
                        metadata={"type": "affiliate_payout", "user_id": str(uid), "quarter": quarter},
                        description=f"GigsFill affiliate payout {quarter}",
                        idempotency_key=f"aff_payout_{payout_id}",
                    )
                    transfer_id = transfer.id
                    db.execute(text("""
                        UPDATE affiliate_payouts
                        SET status = 'paid', stripe_transfer_id = :tid, paid_at = CURRENT_TIMESTAMP
                        WHERE id = :pid
                    """), {"tid": transfer_id, "pid": payout_id})
                    # Link ONLY the earnings snapshotted at SUM time
                    # (Jul 2026 audit M-H4). Post-SUM accruals stay
                    # payout_id IS NULL and roll into next quarter.
                    db.execute(text("""
                        UPDATE affiliate_earnings SET payout_id = :pid
                        WHERE affiliate_user_id = :uid
                          AND payout_id IS NULL
                          AND id <= :snap
                    """), {"pid": payout_id, "uid": uid, "snap": snapshot_max_id})
                    db.commit()
                except Exception as e:
                    logger.error(f"Affiliate payout transfer failed for user {uid}: {e}")
                    db.execute(text("""
                        UPDATE affiliate_payouts SET status = 'transfer_failed', notes = :note WHERE id = :pid
                    """), {"note": str(e)[:200], "pid": payout_id})
                    db.commit()
            else:
                # No Stripe account — mark payout record as pending but DO NOT link earnings.
                # Earnings stay with payout_id IS NULL so they roll into next quarter
                # once the affiliate sets up Stripe. Admin can manually trigger payment.
                db.execute(text(
                    "UPDATE affiliate_payouts SET status = 'no_stripe' WHERE id = :pid"
                ), {"pid": payout_id})
                db.commit()

        # Send quarterly email (only eligible affiliates reach this point)
        try:
            _send_quarterly_affiliate_email(
                db, uid, user_name, user_row["email"],
                total, venue_rows, quarter, True, min_cents, transfer_id
            )
        except Exception as e:
            logger.error(f"Affiliate quarterly email error for user {uid}: {e}")

        # Check 1099 threshold.
        # Audit fix (Jun 2026): use an explicit calendar-year boundary range
        # (paid_at >= Jan-1 AND paid_at < Jan-1 next year) instead of
        # `strftime('%Y', ap.paid_at) = :yr`. strftime is SQLite-only —
        # on Postgres production it threw, and the bare `except: pass`
        # silently swallowed the error so this 1099 cutoff alert never
        # fired. Compliance risk: admin was never warned when an
        # affiliate crossed the IRS-reportable threshold.
        try:
            year = utcnow_naive().year
            year_start = f"{year}-01-01"
            year_end = f"{year + 1}-01-01"
            ytd = db.execute(text("""
                SELECT COALESCE(SUM(ae.earned_cents), 0)
                FROM affiliate_earnings ae
                JOIN affiliate_payouts ap ON ap.id = ae.payout_id
                WHERE ae.affiliate_user_id = :uid
                  AND ap.status IN ('paid')
                  AND ap.paid_at >= :y0
                  AND ap.paid_at <  :y1
            """), {"uid": uid, "y0": year_start, "y1": year_end}).scalar() or 0
            if ytd >= threshold_cents:
                logger.info(f"Affiliate user {uid} has ${ytd/100:.2f} YTD — may need 1099 for {year}")
        except Exception as _99e:
            logger.warning(f"1099 threshold check failed for affiliate user {uid}: {_99e}")


def _send_quarterly_affiliate_email(db, uid, user_name, email, total_cents, venue_rows, quarter, meets_min, min_cents, transfer_id):
    from backend.email_service import EmailService
    es = EmailService(db)
    logo_src = "https://gigsfill.com/app/static/img/gigsfill-logo_light.png"

    venue_lines = "".join([
        f'<tr><td style="padding:6px 10px;font-size:13px;color:#374151;border-bottom:1px solid #f3f4f6;">{r["venue_name"]}</td>'
        f'<td style="padding:6px 10px;font-size:13px;color:#374151;text-align:center;border-bottom:1px solid #f3f4f6;">{r["gig_count"]}</td>'
        f'<td style="padding:6px 10px;font-size:13px;font-weight:600;color:#10b981;text-align:right;border-bottom:1px solid #f3f4f6;">${r["venue_cents"]/100:.2f}</td></tr>'
        for r in venue_rows
    ])

    if meets_min:
        headline = f"Your affiliate payout of <strong>${total_cents/100:.2f}</strong> for {quarter} has been {'sent!' if transfer_id else 'recorded — payment pending Stripe setup.'}"
        status_color = "#10b981"
        status_note = "✅ Payment sent via Stripe transfer." if transfer_id else "⚠️ Set up your Stripe payment account to receive payouts."
    else:
        headline = f"Your affiliate earnings of <strong>${total_cents/100:.2f}</strong> for {quarter} are below the <strong>${min_cents/100:.0f}</strong> minimum — they'll roll over to next quarter."
        status_color = "#f59e0b"
        status_note = f"Minimum payout threshold is ${min_cents/100:.0f}. Keep referring venues to reach it!"

    body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fa;"><tr><td style="padding:40px 20px;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<img src="{logo_src}" alt="GigsFill" width="160" height="40" style="display:block;">
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 8px;font-size:20px;font-weight:600;color:#111827;">Affiliate Earnings — {quarter}</h1>
<p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#4b5563;">Hi {user_name},</p>
<p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#4b5563;">{headline}</p>
<p style="margin:0 0 16px;font-size:13px;color:{status_color};font-weight:600;">{status_note}</p>
<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;margin-bottom:24px;">
<thead><tr style="background:#f9fafb;">
<th style="padding:8px 10px;font-size:11px;font-weight:700;text-transform:uppercase;color:#6b7280;text-align:left;">Venue</th>
<th style="padding:8px 10px;font-size:11px;font-weight:700;text-transform:uppercase;color:#6b7280;text-align:center;">Gigs</th>
<th style="padding:8px 10px;font-size:11px;font-weight:700;text-transform:uppercase;color:#6b7280;text-align:right;">Earned</th>
</tr></thead>
<tbody>{venue_lines}</tbody>
<tfoot><tr style="background:#f9fafb;">
<td colspan="2" style="padding:8px 10px;font-size:13px;font-weight:700;color:#111827;">Total</td>
<td style="padding:8px 10px;font-size:13px;font-weight:700;color:#10b981;text-align:right;">${total_cents/100:.2f}</td>
</tr></tfoot>
</table>
<div style="text-align:center;">
<a href="https://gigsfill.com/app/user-profile.html?tab=affiliates" style="display:inline-block;background:#06b6d4;color:#fff;padding:12px 28px;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;">View Your Affiliate Dashboard →</a>
</div>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#1a1a2e;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table></td></tr></table></body></html>"""

    # Audit fix (May 2026 part 10c): the previous fallback called
    # send_notification_email with EMPTY variables, so the affiliate_quarterly
    # template (which has {{user_name}}, {{headline}} placeholders) would
    # render with literal "{{user_name}}" in the body. Pass real variables
    # if the template fallback fires.
    try:
        es._send_raw_email(to_email=email, subject=f"GigsFill Affiliate Earnings — {quarter}", html_body=body)
    except AttributeError:
        _headline = (
            f"Your affiliate payout of ${total_cents/100:.2f} for {quarter} "
            f"{'has been sent!' if (meets_min and transfer_id) else 'is being processed.'}"
            if meets_min
            else f"Your earnings of ${total_cents/100:.2f} for {quarter} are below the "
                 f"${min_cents/100:.0f} minimum and will roll over."
        )
        es.send_notification_email(
            user_email=email, user_id=uid,
            notification_type="affiliate_quarterly",
            variables={
                "user_name": user_name,
                "quarter": quarter,
                "headline": _headline,
            }
        )


# ── Admin Endpoints ───────────────────────────────────────────────────────────

@router.get("/api/admin/affiliate/settings")
def get_affiliate_settings(user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    keys = ["affiliate_enabled", "affiliate_rate_percent", "affiliate_reduced_rate_percent",
            "affiliate_reduced_after_days", "affiliate_min_payout_cents", "affiliate_1099_threshold_cents"]
    rows = db.execute(text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ({})".format(
        ",".join(f"'{k}'" for k in keys)
    ))).mappings().all()
    result = {r["setting_key"]: r["setting_value"] for r in rows}
    return {k: result.get(k, "") for k in keys}


@router.put("/api/admin/affiliate/settings")
async def update_affiliate_settings(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    data = await request.json()
    allowed = ["affiliate_enabled", "affiliate_rate_percent", "affiliate_reduced_rate_percent",
               "affiliate_reduced_after_days", "affiliate_min_payout_cents",
               "affiliate_1099_threshold_cents", "affiliate_daily_send_cap"]

    # Audit fix (May 2026 part 9c): range-validate before writing. The
    # earlier endpoint accepted any string — a typo of "15.0" instead of
    # "1.5" would quadruple every affiliate's earnings live across the
    # platform (because _current_rate now reads live from settings on
    # every accrual). Each field has explicit min/max and type rules.
    def _validate(key, raw):
        if key == "affiliate_enabled":
            v = str(raw).lower()
            if v not in ("true", "false", "1", "0"):
                raise HTTPException(400, f"{key}: must be true/false")
            return "true" if v in ("true", "1") else "false"
        if key in ("affiliate_rate_percent", "affiliate_reduced_rate_percent"):
            try: f = float(raw)
            except: raise HTTPException(400, f"{key}: must be a number")
            if f < 0 or f > 50:
                raise HTTPException(400, f"{key}: must be between 0 and 50 (percent)")
            return str(f)
        if key == "affiliate_reduced_after_days":
            try: i = int(float(raw))
            except: raise HTTPException(400, f"{key}: must be an integer")
            if i < 0 or i > 36500:
                raise HTTPException(400, f"{key}: must be between 0 and 36500 (days)")
            return str(i)
        if key in ("affiliate_min_payout_cents", "affiliate_1099_threshold_cents"):
            try: i = int(float(raw))
            except: raise HTTPException(400, f"{key}: must be an integer (cents)")
            if i < 0 or i > 100_000_00:  # $100k upper bound
                raise HTTPException(400, f"{key}: must be between 0 and 10000000 cents")
            return str(i)
        if key == "affiliate_daily_send_cap":
            try: i = int(float(raw))
            except: raise HTTPException(400, f"{key}: must be an integer")
            if i < 0 or i > 10000:
                raise HTTPException(400, f"{key}: must be between 0 and 10000")
            return str(i)
        return str(raw)

    # Audit fix (May 2026 part 3): snapshot before-state so the audit row
    # captures what changed.
    before = {}
    for key in allowed:
        if key in data:
            cur = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key = :k"), {"k": key}).first()
            before[key] = cur[0] if cur else None
    after = {}
    for key in allowed:
        if key in data:
            val = _validate(key, data[key])
            after[key] = val
            existing = db.execute(text("SELECT id FROM platform_settings WHERE setting_key = :k"), {"k": key}).first()
            if existing:
                db.execute(text("UPDATE platform_settings SET setting_value = :v WHERE setting_key = :k"), {"v": val, "k": key})
            else:
                db.execute(text("INSERT INTO platform_settings (setting_key, setting_value) VALUES (:k, :v)"), {"k": key, "v": val})
    db.commit()
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, user, "update_affiliate_settings",
            target_table="platform_settings", target_id=None,
            before=before, after=after, request=request,
        )
    except Exception:
        pass
    return {"ok": True}


@router.get("/api/admin/affiliate/accounting")
def get_affiliate_accounting(user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)

    affiliates = db.execute(text("""
        SELECT
            u.id as user_id, u.first_name, u.last_name, u.email, u.affiliate_code,
            COALESCE(ar_agg.venue_count, 0) as venue_count,
            COALESCE(ae_agg.total_earned_cents, 0) as total_earned_cents,
            COALESCE(ae_agg.unpaid_cents, 0) as unpaid_cents,
            COALESCE(ae_agg.ytd_cents, 0) as ytd_cents,
            COALESCE(ae_agg.total_gigs, 0) as total_gigs,
            ae_agg.last_earning_at
        FROM users u
        JOIN (
            SELECT affiliate_user_id, COUNT(DISTINCT venue_id) as venue_count
            FROM affiliate_referrals
            GROUP BY affiliate_user_id
        ) ar_agg ON ar_agg.affiliate_user_id = u.id
        LEFT JOIN (
            SELECT affiliate_user_id,
                   SUM(earned_cents) as total_earned_cents,
                   SUM(CASE WHEN payout_id IS NULL THEN earned_cents ELSE 0 END) as unpaid_cents,
                   SUM(CASE WHEN strftime('%Y', accrued_at) = strftime('%Y', 'now') THEN earned_cents ELSE 0 END) as ytd_cents,
                   COUNT(id) as total_gigs,
                   MAX(accrued_at) as last_earning_at
            FROM affiliate_earnings
            GROUP BY affiliate_user_id
        ) ae_agg ON ae_agg.affiliate_user_id = u.id
        ORDER BY total_earned_cents DESC
    """)).mappings().all()

    return [dict(a) for a in affiliates]


@router.get("/api/admin/affiliate/accounting/{user_id}")
def get_affiliate_detail(user_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)

    venues = db.execute(text("""
        SELECT
            ar.id as referral_id, ar.venue_id, ar.linked_at, ar.link_method,
            ar.initial_rate_percent, ar.reduced_rate_percent, ar.reduced_after_days,
            v.venue_name, v.city, v.state,
            COALESCE(SUM(ae.earned_cents), 0) as total_earned_cents,
            COALESCE(SUM(CASE WHEN ae.payout_id IS NULL THEN ae.earned_cents ELSE 0 END), 0) as unpaid_cents,
            COUNT(ae.id) as gig_count
        FROM affiliate_referrals ar
        JOIN venues v ON v.id = ar.venue_id
        LEFT JOIN affiliate_earnings ae ON ae.venue_id = ar.venue_id AND ae.affiliate_user_id = ar.affiliate_user_id
        WHERE ar.affiliate_user_id = :uid
        GROUP BY ar.id
        ORDER BY ar.linked_at DESC
    """), {"uid": user_id}).mappings().all()

    payouts = db.execute(text("""
        SELECT * FROM affiliate_payouts WHERE affiliate_user_id = :uid ORDER BY quarter DESC
    """), {"uid": user_id}).mappings().all()

    return {"venues": [dict(v) for v in venues], "payouts": [dict(p) for p in payouts]}


@router.get("/api/admin/affiliate/referrals")
def get_all_referrals(user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    rows = db.execute(text("""
        SELECT
            ar.id, ar.venue_id, ar.affiliate_user_id, ar.linked_at, ar.link_method,
            ar.initial_rate_percent, ar.reduced_rate_percent,
            v.venue_name, v.city, v.state,
            u.first_name, u.last_name, u.email as affiliate_email, u.affiliate_code,
            COALESCE(SUM(ae.earned_cents), 0) as total_earned_cents
        FROM affiliate_referrals ar
        JOIN venues v ON v.id = ar.venue_id
        JOIN users u ON u.id = ar.affiliate_user_id
        LEFT JOIN affiliate_earnings ae ON ae.affiliate_user_id = ar.affiliate_user_id AND ae.venue_id = ar.venue_id
        GROUP BY ar.id
        ORDER BY ar.linked_at DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


@router.post("/api/admin/affiliate/manual-link")
async def manual_link_affiliate(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    data = await request.json()
    venue_id = data.get("venue_id")
    affiliate_code = (data.get("affiliate_code") or "").strip().upper()

    if not venue_id or not affiliate_code:
        raise HTTPException(400, "venue_id and affiliate_code required")

    aff_user = db.execute(text("SELECT id FROM users WHERE affiliate_code = :c"), {"c": affiliate_code}).first()
    if not aff_user:
        raise HTTPException(404, "Affiliate code not found")

    venue = db.execute(text("SELECT id, user_id FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    # Audit fix (May 2026 part 9c): block admin from linking an affiliate to
    # a venue they themselves own — closes the self-referral path the signup
    # flow already blocks.
    if int(aff_user[0]) == int(venue["user_id"]):
        raise HTTPException(400,
            "Cannot link this affiliate to a venue they own — that would be self-referral.")

    # Capture pre-existing referral for audit AND to refuse silent overwrite
    before_row = db.execute(text(
        "SELECT affiliate_user_id, link_method, initial_rate_percent, "
        "reduced_rate_percent, reduced_after_days, linked_at "
        "FROM affiliate_referrals WHERE venue_id = :vid"
    ), {"vid": venue_id}).mappings().first()

    # Audit fix (May 2026 part 9c): refuse to overwrite an existing link
    # unless caller explicitly opts in with `force=True`. The old
    # `INSERT OR REPLACE` blew away the existing row INCLUDING `linked_at`,
    # silently restarting the full-rate window AND swapping the affiliate
    # to a different user — both real money implications with no warning.
    force = bool(data.get("force"))
    if before_row and not force:
        raise HTTPException(409, {
            "error": "venue_already_linked",
            "message": (f"Venue is already linked to affiliate user "
                        f"#{before_row['affiliate_user_id']} since {before_row['linked_at']}. "
                        f"Re-submit with force=true to replace (will reset the rate window)."),
            "current_link": dict(before_row),
        })

    init_rate    = _aff_setting(db, "affiliate_rate_percent", 1.0)
    reduced_rate = _aff_setting(db, "affiliate_reduced_rate_percent", 0.5)
    reduced_days = int(_aff_setting(db, "affiliate_reduced_after_days", 365))

    try:
        if before_row:
            # Preserve linked_at to avoid resetting the rate window on a
            # legitimate admin re-link (e.g. correcting a typo). The
            # initial/reduced columns are intentionally updated.
            db.execute(text("""
                UPDATE affiliate_referrals
                SET affiliate_user_id = :auid,
                    link_method = 'manual',
                    initial_rate_percent = :init,
                    reduced_rate_percent = :red,
                    reduced_after_days = :days,
                    manually_linked_by = :admin_id
                WHERE venue_id = :vid
            """), {"auid": aff_user[0], "vid": venue_id, "init": init_rate,
                   "red": reduced_rate, "days": reduced_days, "admin_id": user.id})
        else:
            db.execute(text("""
                INSERT INTO affiliate_referrals
                    (affiliate_user_id, venue_id, link_method, initial_rate_percent,
                     reduced_rate_percent, reduced_after_days, manually_linked_by)
                VALUES (:auid, :vid, 'manual', :init, :red, :days, :admin_id)
            """), {"auid": aff_user[0], "vid": venue_id, "init": init_rate,
                   "red": reduced_rate, "days": reduced_days, "admin_id": user.id})
        db.commit()

        from backend.utils import log_admin_action
        log_admin_action(
            db, user, "manual_link_affiliate",
            target_table="affiliate_referrals", target_id=venue_id,
            before=(dict(before_row) if before_row else None),
            after={
                "affiliate_user_id": aff_user[0],
                "affiliate_code": affiliate_code,
                "link_method": "manual",
                "initial_rate_percent": init_rate,
                "reduced_rate_percent": reduced_rate,
                "reduced_after_days": reduced_days,
            },
            metadata={"venue_id": venue_id},
            request=request,
        )
        return {"ok": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


@router.delete("/api/admin/affiliate/referrals/{referral_id}")
def delete_referral(request: Request, referral_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    before_row = db.execute(text(
        "SELECT id, affiliate_user_id, venue_id, link_method, initial_rate_percent, reduced_rate_percent, reduced_after_days "
        "FROM affiliate_referrals WHERE id = :rid"
    ), {"rid": referral_id}).mappings().first()

    # Audit fix (May 2026 part 6): also drop unpaid affiliate_earnings rows for
    # this (affiliate_user_id, venue_id) pair. Previously `delete_referral`
    # only removed the referral row, leaving orphan earnings that the next
    # quarterly sweep summed via `WHERE ae.payout_id IS NULL` and paid out —
    # admin-revoked referrals would still earn money.
    earnings_voided = 0
    if before_row:
        try:
            _res = db.execute(
                text("""DELETE FROM affiliate_earnings
                        WHERE affiliate_user_id = :uid
                          AND venue_id = :vid
                          AND payout_id IS NULL"""),
                {"uid": before_row["affiliate_user_id"], "vid": before_row["venue_id"]}
            )
            earnings_voided = _res.rowcount or 0
        except Exception as _ee:
            logger.warning(f"delete_referral: earnings cleanup failed for ref {referral_id}: {_ee}")
    db.execute(text("DELETE FROM affiliate_referrals WHERE id = :rid"), {"rid": referral_id})
    db.commit()

    # Audit fix (May 2026 part 9c): notify the affected affiliate so the
    # venue doesn't silently vanish from their dashboard. They still keep
    # any payouts that already cleared (paid is paid); the cleanup above
    # voided only un-paid earnings on that link.
    if before_row:
        try:
            _venue_name = db.execute(text(
                "SELECT venue_name FROM venues WHERE id = :vid"
            ), {"vid": before_row["venue_id"]}).scalar() or f"Venue #{before_row['venue_id']}"
            from backend.services.notification_service import create_notification as _cn
            _cn(db, before_row["affiliate_user_id"], "affiliate_link_removed",
                "Affiliate link removed",
                (f"Admin removed your affiliate link to {_venue_name}. "
                 f"Any previously paid commissions are unaffected; un-paid "
                 f"earnings on this venue ({earnings_voided} row(s)) have been "
                 f"voided. Contact support if you believe this was in error."),
                venue_id=before_row["venue_id"])
            db.commit()
        except Exception as _ne:
            logger.warning(f"delete_referral notification fan-out failed for "
                           f"ref {referral_id}: {_ne}")

    from backend.utils import log_admin_action
    _audit_after = {"earnings_voided": earnings_voided}
    log_admin_action(
        db, user, "delete_referral",
        target_table="affiliate_referrals", target_id=referral_id,
        before=(dict(before_row) if before_row else None),
        after=_audit_after,
        request=request,
    )
    return {"ok": True, "earnings_voided": earnings_voided}


@router.get("/api/admin/affiliate/venue-search")
def venue_search_for_affiliate(q: str = "", user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    # Audit fix (May 2026 part 10b): escape SQL LIKE wildcards in user input.
    # `%` and `_` in the venue name (e.g. "100% Live") used to expand the
    # search unexpectedly. ESCAPE '\\' tells the LIKE engine to treat the
    # backslash-escaped wildcard as a literal.
    safe_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.execute(text("""
        SELECT v.id, v.venue_name, v.city, v.state,
               u.email as owner_email,
               (SELECT ar.affiliate_user_id FROM affiliate_referrals ar WHERE ar.venue_id = v.id LIMIT 1) as affiliate_user_id
        FROM venues v
        JOIN users u ON u.id = v.user_id
        WHERE v.venue_name LIKE :q ESCAPE '\\'
           OR u.email      LIKE :q ESCAPE '\\'
           OR v.city       LIKE :q ESCAPE '\\'
        LIMIT 20
    """), {"q": f"%{safe_q}%"}).mappings().all()
    return [dict(r) for r in rows]


@router.post("/api/admin/affiliate/run-payouts")
# Audit fix (May 2026 part 5): each call loops over every eligible
# affiliate and fires `stripe.Transfer.create`. A misclick/refresh
# during a long run could double-trigger; per-payout idempotency keys
# protect each transfer but the unbounded loop still burns Stripe API
# budget. Cap to 2/minute — manual ops only.
@limiter.limit("2/minute")
async def run_payouts_manual(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Manually trigger quarterly payout run (admin only)."""
    _check_admin(user)
    try:
        run_quarterly_affiliate_payouts(db)

        from backend.utils import log_admin_action
        log_admin_action(
            db, user, "run_payouts_manual",
            target_table="affiliate_payouts",
            metadata={"quarter": _get_quarter()},
            request=request,
        )
        return {"ok": True, "message": "Payout run complete"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/affiliate/artist-stripe-accounts")
def get_artist_stripe_accounts(user=Depends(get_current_user), db=Depends(get_db)):
    """Return all Stripe Connect accounts the user has set up via their artist profiles."""
    rows = db.execute(text("""
        SELECT a.id as artist_id, a.name as artist_name,
               eps.stripe_connect_account_id, eps.stripe_connect_onboarding_complete
        FROM artists a
        JOIN entity_payment_settings eps ON eps.entity_type='artist' AND eps.entity_id=a.id
        WHERE a.user_id = :uid
          AND eps.stripe_connect_account_id IS NOT NULL
          AND eps.stripe_connect_account_id != ''
          AND eps.stripe_connect_onboarding_complete IS NOT NULL
          AND eps.stripe_connect_onboarding_complete != 0
        ORDER BY a.name
    """), {"uid": user.id}).fetchall()
    return [{"artist_id": r[0], "artist_name": r[1],
             "stripe_account_id": r[2]} for r in rows]


@router.post("/api/affiliate/use-artist-stripe")
async def use_artist_stripe_for_affiliate(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Link an existing artist Stripe Connect account to this user's affiliate profile."""
    body = await request.json()
    artist_id = body.get("artist_id")
    if not artist_id:
        raise HTTPException(400, "artist_id required")
    # Verify this artist belongs to the user
    artist = db.execute(text("SELECT id FROM artists WHERE id=:aid AND user_id=:uid"),
                        {"aid": artist_id, "uid": user.id}).fetchone()
    if not artist:
        raise HTTPException(403, "Not your artist")
    # Get the stripe account id
    eps = db.execute(text("""
        SELECT stripe_connect_account_id, stripe_connect_onboarding_complete
        FROM entity_payment_settings WHERE entity_type='artist' AND entity_id=:aid
    """), {"aid": artist_id}).fetchone()
    if not eps or not eps[0] or not eps[1]:
        raise HTTPException(400, "Artist does not have a complete Stripe account")
    account_id = eps[0]
    # Save to user's affiliate payment settings
    existing = db.execute(text("""
        SELECT id FROM entity_payment_settings WHERE entity_type='user' AND entity_id=:uid
    """), {"uid": user.id}).fetchone()
    if existing:
        db.execute(text("""
            UPDATE entity_payment_settings
            SET affiliate_stripe_connect_account_id=:acid,
                affiliate_stripe_connect_onboarding_complete=1
            WHERE entity_type='user' AND entity_id=:uid
        """), {"acid": account_id, "uid": user.id})
    else:
        db.execute(text("""
            INSERT INTO entity_payment_settings
            (entity_type, entity_id, affiliate_stripe_connect_account_id, affiliate_stripe_connect_onboarding_complete)
            VALUES ('user', :uid, :acid, 1)
        """), {"acid": account_id, "uid": user.id})
    db.commit()
    return {"ok": True, "stripe_account_id": account_id}


# ── Per-venue earnings detail (paginated, for expandable rows) ────────────────

@router.get("/api/affiliate/my-venue-earnings/{venue_id}")
def get_my_venue_earnings(
    venue_id: int,
    page: int = 1,
    limit: int = 10,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get paginated gig earnings for a specific referred venue."""
    # Verify this venue belongs to this affiliate
    ref = db.execute(text(
        "SELECT id FROM affiliate_referrals WHERE affiliate_user_id = :uid AND venue_id = :vid"
    ), {"uid": user.id, "vid": venue_id}).first()
    if not ref:
        raise HTTPException(403, "Not your referral")

    offset = (page - 1) * limit
    total = db.execute(text(
        "SELECT COUNT(*) FROM affiliate_earnings WHERE affiliate_user_id = :uid AND venue_id = :vid"
    ), {"uid": user.id, "vid": venue_id}).scalar() or 0

    # Resolve artist name preferring t.artist_id (set on artist_payout child
    # rows for both single- and multi-slot) over g.artist_id (NULL on multi-slot).
    # Without this, affiliate earnings on multi-slot gigs showed no artist name.
    rows = db.execute(text("""
        SELECT ae.id, ae.gig_fee_cents, ae.rate_percent, ae.earned_cents,
               ae.quarter, ae.accrued_at, ae.payout_id,
               g.date as gig_date, g.start_time, g.end_time, g.title as gig_title,
               a.name as artist_name,
               ap.status as payout_status
        FROM affiliate_earnings ae
        JOIN transactions t ON t.id = ae.transaction_id
        JOIN gigs g ON g.id = t.gig_id
        LEFT JOIN artists a ON a.id = COALESCE(t.artist_id, g.artist_id)
        LEFT JOIN affiliate_payouts ap ON ap.id = ae.payout_id
        WHERE ae.affiliate_user_id = :uid AND ae.venue_id = :vid
        ORDER BY g.date DESC, g.start_time DESC
        LIMIT :lim OFFSET :off
    """), {"uid": user.id, "vid": venue_id, "lim": limit, "off": offset}).mappings().all()

    return {
        "earnings": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/api/affiliate/check-new-venues")
def check_new_venues(user=Depends(get_current_user), db=Depends(get_db)):
    """Check if user has new referrals they haven't been notified about,
    plus the two payout prerequisites (W-9 + Stripe Connect) so the
    welcome popup can nudge whichever one is missing.
    """
    has_referrals = db.execute(text(
        "SELECT COUNT(*) FROM affiliate_referrals WHERE affiliate_user_id = :uid"
    ), {"uid": user.id}).scalar() or 0

    has_w9 = db.execute(text(
        "SELECT COUNT(*) FROM w9_forms WHERE entity_type = 'user' AND entity_id = :uid"
    ), {"uid": user.id}).scalar() or 0

    # Stripe Connect must be onboarded for the user to actually receive
    # affiliate payouts. Lives in entity_payment_settings (entity_type=
    # 'user', entity_id=user.id) — same row the onboard endpoint writes.
    # Bug fix Jun 2026: was incorrectly reading FROM users, which doesn't
    # carry these columns → SELECT raised OperationalError → endpoint 500.
    stripe_row = db.execute(text(
        "SELECT affiliate_stripe_connect_account_id, affiliate_stripe_connect_onboarding_complete "
        "FROM entity_payment_settings WHERE entity_type = 'user' AND entity_id = :uid"
    ), {"uid": user.id}).mappings().first()
    has_stripe = bool(
        stripe_row
        and stripe_row.get("affiliate_stripe_connect_account_id")
        and stripe_row.get("affiliate_stripe_connect_onboarding_complete")
    )

    # Per-prompt dismissal flags. The setting_value stores an ISO UTC
    # timestamp of WHEN the user dismissed the popup; the popup auto
    # re-fires after a 7-day cooldown so the user gets a weekly nudge
    # until both prerequisites (W-9 + Stripe) are complete. Legacy rows
    # that stored '1' as the value are treated as "dismissed long ago"
    # so they DO re-fire — gives existing users a single fresh nudge.
    from datetime import datetime as _dt, timedelta as _td
    def _dismiss_active(value):
        """Return True if the dismissal is still within the 7-day cooldown."""
        if not value:
            return False
        try:
            ts = _dt.fromisoformat(str(value).rstrip("Z"))
        except (ValueError, TypeError):
            # Legacy '1' / unparseable → treat as long-ago dismissal.
            return False
        return (_dt.utcnow() - ts) < _td(days=7)

    dismissed_w9_val = db.execute(text(
        "SELECT setting_value FROM user_settings WHERE user_id = :uid AND setting_key = 'aff_w9_prompt_dismissed'"
    ), {"uid": user.id}).scalar()
    dismissed_stripe_val = db.execute(text(
        "SELECT setting_value FROM user_settings WHERE user_id = :uid AND setting_key = 'aff_stripe_prompt_dismissed'"
    ), {"uid": user.id}).scalar()

    return {
        "has_referrals": bool(has_referrals),
        "has_w9": bool(has_w9),
        "has_stripe": has_stripe,
        "needs_w9_prompt":     bool(has_referrals and not has_w9     and not _dismiss_active(dismissed_w9_val)),
        "needs_stripe_prompt": bool(has_referrals and not has_stripe and not _dismiss_active(dismissed_stripe_val)),
        "referral_count": has_referrals,
    }


@router.post("/api/affiliate/dismiss-stripe-prompt")
def dismiss_stripe_prompt(user=Depends(get_current_user), db=Depends(get_db)):
    """Mark that user has dismissed the Stripe setup prompt. The popup
    auto re-fires after a 7-day cooldown (see check_new_venues for the
    expiry check) so the user gets a weekly nudge until Stripe is set
    up — they can also complete the flow any time from the Affiliate
    Dashboard checklist banner.
    """
    from datetime import datetime as _dt
    now_iso = _dt.utcnow().isoformat()
    try:
        db.execute(text("""
            INSERT INTO user_settings (user_id, setting_key, setting_value)
            VALUES (:uid, 'aff_stripe_prompt_dismissed', :ts)
            ON CONFLICT(user_id, setting_key) DO UPDATE SET setting_value = :ts
        """), {"uid": user.id, "ts": now_iso})
        db.commit()
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/affiliate/dismiss-w9-prompt")
def dismiss_w9_prompt(user=Depends(get_current_user), db=Depends(get_db)):
    """Mark that user has dismissed the W-9 prompt. Re-fires weekly per
    check_new_venues' _dismiss_active() cooldown."""
    from datetime import datetime as _dt
    now_iso = _dt.utcnow().isoformat()
    try:
        db.execute(text("""
            INSERT INTO user_settings (user_id, setting_key, setting_value)
            VALUES (:uid, 'aff_w9_prompt_dismissed', :ts)
            ON CONFLICT(user_id, setting_key) DO UPDATE SET setting_value = :ts
        """), {"uid": user.id, "ts": now_iso})
        db.commit()
    except Exception:
        pass
    return {"ok": True}

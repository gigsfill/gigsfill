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

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _aff_setting(db, key, default):
    r = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key = :k"), {"k": key}).scalar()
    try:
        return float(r) if r is not None else default
    except Exception:
        return default


def _get_quarter(dt: datetime = None) -> str:
    """Return quarter string like '2026-Q1'"""
    dt = dt or utcnow_naive()
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"


def _current_rate(db, referral_row) -> float:
    """Return the current rate for a referral based on days since venue signup."""
    linked_at = referral_row["linked_at"]
    if isinstance(linked_at, str):
        try:
            linked_at = datetime.fromisoformat(linked_at)
        except Exception:
            linked_at = utcnow_naive()
    days_elapsed = (utcnow_naive() - linked_at).days
    if days_elapsed >= referral_row["reduced_after_days"]:
        return referral_row["reduced_rate_percent"]
    return referral_row["initial_rate_percent"]


def _check_admin(user):
    # Audit fix (May 2026): use centralized to_admin_bool helper — handles
    # every storage form (bool, int, 'true'/'false', '1'/'0', None) safely.
    from backend.utils import to_admin_bool
    if not to_admin_bool(getattr(user, "is_admin", None)):
        raise HTTPException(403, "Admin only")


# ── Affiliate code click tracking (landing page cookie) ──────────────────────

@router.get("/api/affiliate/track/{code}")
def track_affiliate_click(code: str, redirect_to: str = "/", db=Depends(get_db)):
    """Record affiliate click and set cookie, then redirect."""
    code = code.strip().upper()
    row = db.execute(text("SELECT id FROM users WHERE affiliate_code = :c"), {"c": code}).first()
    if not row:
        return RedirectResponse(redirect_to)

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

    response = RedirectResponse(redirect_to)
    # Cookie survives 90 days
    response.set_cookie("aff_code", code, max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax")
    return response


# ── Send Recommend Email ──────────────────────────────────────────────────────

@router.post("/api/affiliate/recommend")
async def send_recommend_email(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Send a GigsFill recommendation email on behalf of a user."""
    data = await request.json()
    recipient_email = (data.get("recipient_email") or "").strip().lower()
    personal_note   = (data.get("personal_note") or "").strip()
    recipient_name  = (data.get("recipient_name") or "").strip()

    if not recipient_email or "@" not in recipient_email:
        raise HTTPException(400, "Valid recipient email required")

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

    # Build affiliate signup URL
    signup_url = f"https://gigsfill.com/?aff={aff_code}"

    # Build template variables
    greeting  = f", {recipient_name}" if recipient_name else ""
    note_html = f'<p style="margin:0 0 20px 0;font-size:15px;line-height:1.6;color:#4b5563;padding:16px;background:#f0fdf4;border-left:4px solid #10b981;border-radius:4px;">{personal_note}</p>' if personal_note else ""

    # Send using the recommend_gigsfill DB template
    try:
        from backend.email_service import EmailService
        es = EmailService(db)
        template = es.get_template("recommend_gigsfill")
        if not template:
            raise Exception("recommend_gigsfill template not found in DB")
        variables = {
            "user_name":          sender_name,
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
async def resend_recommend_email(email_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Resend a recommendation email."""
    row = db.execute(text("""
        SELECT id, recipient_email, recipient_name, affiliate_code
        FROM affiliate_recommend_emails WHERE id = :id AND sender_user_id = :uid
    """), {"id": email_id, "uid": user.id}).mappings().first()
    if not row:
        raise HTTPException(404, "Email not found")

    recipient_email = row["recipient_email"]
    recipient_name  = row["recipient_name"] or ""
    aff_code        = row["affiliate_code"]
    aff_url         = f"https://gigsfill.com/?aff={aff_code}"
    sender_name     = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email
    greeting        = f", {recipient_name}" if recipient_name else ""

    try:
        from backend.email_service import EmailService
        es = EmailService(db)
        template = es.get_template("recommend_gigsfill")
        if template:
            variables = {
                "user_name": sender_name,
                "recipient_greeting": f", {recipient_name}" if recipient_name else "",
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

    # Update sent_at timestamp
    db.execute(text(
        "UPDATE affiliate_recommend_emails SET sent_at = CURRENT_TIMESTAMP WHERE id = :id"
    ), {"id": email_id})
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
    """Get all venues referred by this user with earnings summary."""
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

    return [dict(r) for r in rows]


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
        account = stripe.Account.create(
            type="express",
            email=user_row["email"],
            capabilities={"transfers": {"requested": True}},
            metadata={"gigsfill_user_id": str(user.id), "type": "affiliate"}
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

    # Audit fix (May 2026): read base_url from platform_settings rather than
    # hardcoding gigsfill.com — staging or custom-domain deploys would have
    # routed users back to production after onboarding.
    _base = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key='base_url'")).scalar() or "https://gigsfill.com"
    _base = _base.rstrip("/")
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
        SELECT t.id, t.amount_cents, t.gig_id, g.venue_id
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
    earned_cents = int(txn["amount_cents"] * rate / 100)
    if earned_cents <= 0:
        return

    quarter = _get_quarter()
    db.execute(text("""
        INSERT INTO affiliate_earnings
            (affiliate_user_id, venue_id, transaction_id, gig_fee_cents, rate_percent, earned_cents, quarter, accrued_at)
        VALUES (:auid, :vid, :txid, :fee, :rate, :earned, :q, CURRENT_TIMESTAMP)
    """), {
        "auid": referral["affiliate_user_id"],
        "vid": txn["venue_id"],
        "txid": transaction_id,
        "fee": txn["amount_cents"],
        "rate": rate,
        "earned": earned_cents,
        "q": quarter,
    })
    db.commit()
    logger.info(f"Affiliate earnings accrued: txn {transaction_id}, ${earned_cents/100:.2f} @ {rate}% for user {referral['affiliate_user_id']}")


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
    payments_live = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'payments_enabled'"
    )).scalar()
    has_stripe = bool(stripe_key and not stripe_key.startswith("•") and
                      payments_live and str(payments_live).lower() in ("true", "1"))

    if has_stripe:
        import stripe
        stripe.api_key = stripe_key

    # Find all affiliates with unpaid earnings
    affiliates = db.execute(text("""
        SELECT ae.affiliate_user_id,
               SUM(ae.earned_cents) as total_cents,
               GROUP_CONCAT(DISTINCT ae.venue_id) as venue_ids
        FROM affiliate_earnings ae
        WHERE ae.payout_id IS NULL
        GROUP BY ae.affiliate_user_id
        HAVING SUM(ae.earned_cents) > 0
    """)).mappings().all()

    for aff in affiliates:
        uid        = aff["affiliate_user_id"]
        total      = aff["total_cents"]
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

        # Create payout record (only for eligible affiliates)
        try:
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
                    transfer = stripe.Transfer.create(
                        amount=total,
                        currency="usd",
                        destination=stripe_row["affiliate_stripe_connect_account_id"],
                        metadata={"type": "affiliate_payout", "user_id": str(uid), "quarter": quarter},
                        description=f"GigsFill affiliate payout {quarter}"
                    )
                    transfer_id = transfer.id
                    db.execute(text("""
                        UPDATE affiliate_payouts
                        SET status = 'paid', stripe_transfer_id = :tid, paid_at = CURRENT_TIMESTAMP
                        WHERE id = :pid
                    """), {"tid": transfer_id, "pid": payout_id})
                    # Link earnings to payout
                    db.execute(text("""
                        UPDATE affiliate_earnings SET payout_id = :pid
                        WHERE affiliate_user_id = :uid AND payout_id IS NULL
                    """), {"pid": payout_id, "uid": uid})
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

        # Check 1099 threshold
        try:
            year = utcnow_naive().year
            ytd = db.execute(text("""
                SELECT COALESCE(SUM(ae.earned_cents), 0)
                FROM affiliate_earnings ae
                JOIN affiliate_payouts ap ON ap.id = ae.payout_id
                WHERE ae.affiliate_user_id = :uid
                  AND ap.status IN ('paid') AND strftime('%Y', ap.paid_at) = :yr
            """), {"uid": uid, "yr": str(year)}).scalar() or 0
            if ytd >= threshold_cents:
                logger.info(f"Affiliate user {uid} has ${ytd/100:.2f} YTD — may need 1099 for {year}")
        except Exception:
            pass


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

    try:
        es._send_raw_email(to_email=email, subject=f"GigsFill Affiliate Earnings — {quarter}", html_body=body)
    except AttributeError:
        es.send_notification_email(user_email=email, user_id=uid,
            notification_type="affiliate_quarterly", variables={})


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
               "affiliate_reduced_after_days", "affiliate_min_payout_cents", "affiliate_1099_threshold_cents"]
    for key in allowed:
        if key in data:
            val = str(data[key])
            existing = db.execute(text("SELECT id FROM platform_settings WHERE setting_key = :k"), {"k": key}).first()
            if existing:
                db.execute(text("UPDATE platform_settings SET setting_value = :v WHERE setting_key = :k"), {"v": val, "k": key})
            else:
                db.execute(text("INSERT INTO platform_settings (setting_key, setting_value) VALUES (:k, :v)"), {"k": key, "v": val})
    db.commit()
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

    venue = db.execute(text("SELECT id FROM venues WHERE id = :vid"), {"vid": venue_id}).first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    init_rate    = _aff_setting(db, "affiliate_rate_percent", 1.0)
    reduced_rate = _aff_setting(db, "affiliate_reduced_rate_percent", 0.5)
    reduced_days = int(_aff_setting(db, "affiliate_reduced_after_days", 365))

    try:
        db.execute(text("""
            INSERT OR REPLACE INTO affiliate_referrals
                (affiliate_user_id, venue_id, link_method, initial_rate_percent,
                 reduced_rate_percent, reduced_after_days, manually_linked_by)
            VALUES (:auid, :vid, 'manual', :init, :red, :days, :admin_id)
        """), {"auid": aff_user[0], "vid": venue_id, "init": init_rate,
               "red": reduced_rate, "days": reduced_days, "admin_id": user.id})
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


@router.delete("/api/admin/affiliate/referrals/{referral_id}")
def delete_referral(referral_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    db.execute(text("DELETE FROM affiliate_referrals WHERE id = :rid"), {"rid": referral_id})
    db.commit()
    return {"ok": True}


@router.get("/api/admin/affiliate/venue-search")
def venue_search_for_affiliate(q: str = "", user=Depends(get_current_user), db=Depends(get_db)):
    _check_admin(user)
    rows = db.execute(text("""
        SELECT v.id, v.venue_name, v.city, v.state,
               u.email as owner_email,
               (SELECT ar.affiliate_user_id FROM affiliate_referrals ar WHERE ar.venue_id = v.id LIMIT 1) as affiliate_user_id
        FROM venues v
        JOIN users u ON u.id = v.user_id
        WHERE v.venue_name LIKE :q OR u.email LIKE :q OR v.city LIKE :q
        LIMIT 20
    """), {"q": f"%{q}%"}).mappings().all()
    return [dict(r) for r in rows]


@router.post("/api/admin/affiliate/run-payouts")
async def run_payouts_manual(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Manually trigger quarterly payout run (admin only)."""
    _check_admin(user)
    try:
        run_quarterly_affiliate_payouts(db)
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

    rows = db.execute(text("""
        SELECT ae.id, ae.gig_fee_cents, ae.rate_percent, ae.earned_cents,
               ae.quarter, ae.accrued_at, ae.payout_id,
               g.date as gig_date, g.start_time, g.end_time, g.title as gig_title,
               a.name as artist_name,
               ap.status as payout_status
        FROM affiliate_earnings ae
        JOIN transactions t ON t.id = ae.transaction_id
        JOIN gigs g ON g.id = t.gig_id
        LEFT JOIN artists a ON a.id = g.artist_id
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
    """Check if user has new referrals they haven't been notified about + W9 status."""
    has_referrals = db.execute(text(
        "SELECT COUNT(*) FROM affiliate_referrals WHERE affiliate_user_id = :uid"
    ), {"uid": user.id}).scalar() or 0

    has_w9 = db.execute(text(
        "SELECT COUNT(*) FROM w9_forms WHERE entity_type = 'user' AND entity_id = :uid"
    ), {"uid": user.id}).scalar() or 0

    # Check if user dismissed this prompt (stored in user preferences or a flag column)
    dismissed = db.execute(text(
        "SELECT setting_value FROM user_settings WHERE user_id = :uid AND setting_key = 'aff_w9_prompt_dismissed'"
    ), {"uid": user.id}).scalar()

    return {
        "has_referrals": bool(has_referrals),
        "has_w9": bool(has_w9),
        "needs_w9_prompt": bool(has_referrals and not has_w9 and not dismissed),
        "referral_count": has_referrals,
    }


@router.post("/api/affiliate/dismiss-w9-prompt")
def dismiss_w9_prompt(user=Depends(get_current_user), db=Depends(get_db)):
    """Mark that user has dismissed the W9 prompt (so we don't show it every login)."""
    try:
        db.execute(text("""
            INSERT INTO user_settings (user_id, setting_key, setting_value)
            VALUES (:uid, 'aff_w9_prompt_dismissed', '1')
            ON CONFLICT(user_id, setting_key) DO UPDATE SET setting_value = '1'
        """), {"uid": user.id})
        db.commit()
    except Exception:
        pass
    return {"ok": True}

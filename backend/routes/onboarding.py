# backend/routes/onboarding.py — Setup Checklist API

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
from datetime import datetime

router = APIRouter()


def _check_entity_access(db, user, entity_type, entity_id):
    """Verify user owns or has access to this entity.
    Jul 2026 audit: added deleted_at IS NULL — a tombstoned entity should
    never appear in a live onboarding checklist."""
    if entity_type == 'venue':
        row = db.execute(text("""
            SELECT v.id FROM venues v
            WHERE v.id = :eid AND v.deleted_at IS NULL AND (
                v.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'venue'
                           AND eu.entity_id = :eid AND eu.user_id = :uid)
            )
        """), {"eid": entity_id, "uid": user.id}).fetchone()
    elif entity_type == 'affiliate':
        # Affiliates are users themselves — entity_id is the user_id and only
        # the user can access their own affiliate checklist.
        if int(entity_id) != int(user.id):
            raise HTTPException(403, "Access denied")
        return
    else:
        row = db.execute(text("""
            SELECT a.id FROM artists a
            WHERE a.id = :eid AND a.deleted_at IS NULL AND (
                a.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'artist'
                           AND eu.entity_id = :eid AND eu.user_id = :uid)
            )
        """), {"eid": entity_id, "uid": user.id}).fetchone()
    if not row:
        raise HTTPException(403, "Access denied")


# ── Venue checklist items ─────────────────────────────────────────────

VENUE_TASKS = [
    {
        "key": "email_notifications",
        "title": "Email Notifications",
        "description": "Configure automated emails sent to your preferred artists.",
        "mandatory": False,
    },
    {
        "key": "payments",
        "title": "Payments",
        "description": "Enter your Venue's credit card with Stripe. Artists will be paid from this card.",
        "mandatory": True,
    },
    {
        "key": "contract_settings",
        "title": "Contract Settings",
        "description": "If Venue requires contracts, setup contract handling here.",
        "mandatory": False,
    },
    {
        "key": "tax_settings",
        "title": "Tax Settings",
        "description": "Does Venue require Artists to have an updated W-9 on file?",
        "mandatory": True,
    },
    {
        "key": "edit_profile",
        "title": "Edit Venue Profile",
        "description": "Finish your Venue's profile. A complete profile will limit Artist questions later.",
        "mandatory": False,
    },
]

# ── Artist checklist items ────────────────────────────────────────────

ARTIST_TASKS = [
    # Jul 2026: Tax Information moved AHEAD of Payments so filling out the W-9
    # first pre-fills the Stripe Connect onboarding (legal name / address / TIN),
    # saving the artist from re-typing the same fields. See
    # backend/routes/tax.py:build_stripe_individual_from_w9 for the pre-fill
    # helper wired into Account.create.
    {
        "key": "tax_info",
        "title": "Tax Information (do this first)",
        "description": "Complete your W-9. If you fill it out before setting up Payments below, we'll pre-fill your Stripe onboarding with your legal name, address, and TIN so you don't type them twice. Some venues also require an up-to-date W-9 to book you.",
        "mandatory": True,
    },
    {
        "key": "payments",
        "title": "Payments",
        "description": "Enter your Payout Account with Stripe. Your earnings will be paid to this account. If you completed the W-9 above first, Stripe onboarding starts already filled in.",
        "mandatory": True,
    },
    {
        "key": "edit_profile",
        "title": "Edit Artist Profile",
        "description": "Finish your Artist profile. A complete profile will help Venues approve your preferred artist request.",
        "mandatory": False,
    },
]

# ── Affiliate checklist items ─────────────────────────────────────────
# Triggered when a user has at least one affiliate_referrals row pointing at
# them (i.e. a venue signed up via their link or was manually linked). Both
# tasks are mandatory for IRS/Stripe compliance — affiliate cannot receive
# payouts without Stripe Connect, and we can't 1099 them without a W-9.

AFFILIATE_TASKS = [
    {
        "key": "stripe_setup",
        "title": "Set up Stripe",
        "description": "Connect Stripe so we can pay your quarterly affiliate commissions directly to your bank account.",
        "mandatory": True,
    },
    {
        "key": "w9_filed",
        "title": "File W-9",
        "description": "Affiliate commissions are reportable income. We need a W-9 on file to issue payouts and a 1099 at year-end.",
        "mandatory": True,
    },
]


def _check_venue_mandatory(db, entity_id, task_key):
    """Check if a mandatory venue task is actually complete"""
    if task_key == "payments":
        row = db.execute(text("""
            SELECT stripe_customer_id FROM entity_payment_settings
            WHERE entity_type = 'venue' AND entity_id = :eid
            AND stripe_customer_id IS NOT NULL AND stripe_customer_id != ''
        """), {"eid": entity_id}).fetchone()
        return row is not None

    if task_key == "tax_settings":
        row = db.execute(text("""
            SELECT id FROM venue_tax_settings WHERE venue_id = :vid
        """), {"vid": entity_id}).fetchone()
        return row is not None

    return False


def _check_artist_mandatory(db, entity_id, task_key):
    """Check if a mandatory artist task is actually complete"""
    if task_key == "payments":
        row = db.execute(text("""
            SELECT stripe_connect_account_id, stripe_connect_onboarding_complete
            FROM entity_payment_settings
            WHERE entity_type = 'artist' AND entity_id = :eid
            AND stripe_connect_account_id IS NOT NULL
            AND stripe_connect_onboarding_complete = 1
        """), {"eid": entity_id}).fetchone()
        return row is not None

    if task_key == "tax_info":
        # Jul 2026: use tax.py's TZ-aware helper so the "current tax year" flip
        # happens at midnight in the platform's timezone, not UTC. Otherwise a
        # west-coast venue whose New Year's Eve W-9 was saved just after 4 PM
        # PDT would show as "current" ~8 hours before the calendar year actually
        # changed in their local time.
        from backend.routes.tax import _current_tax_year
        current_year = _current_tax_year(db)
        row = db.execute(text("""
            SELECT id FROM w9_forms
            WHERE entity_type = 'artist' AND entity_id = :eid
            AND tax_year >= :yr
        """), {"eid": entity_id, "yr": current_year}).fetchone()
        return row is not None

    return False


def _check_affiliate_mandatory(db, entity_id, task_key):
    """Check if a mandatory affiliate task is complete.
    entity_id is the user's id (affiliates are users, not separate entities).
    Stripe Connect account for affiliate payouts lives on entity_payment_settings
    with entity_type='user' (see affiliate.py:341 etc). W-9 lives in w9_forms with
    entity_type='user' (the per-user W-9 used for affiliate 1099s)."""
    if task_key == "stripe_setup":
        row = db.execute(text("""
            SELECT 1 FROM entity_payment_settings
            WHERE entity_type = 'user' AND entity_id = :uid
              AND affiliate_stripe_connect_account_id IS NOT NULL
              AND affiliate_stripe_connect_account_id != ''
              AND COALESCE(affiliate_stripe_connect_onboarding_complete, 0) = 1
        """), {"uid": entity_id}).fetchone()
        return row is not None

    if task_key == "w9_filed":
        # Jul 2026: use tax.py's TZ-aware helper so the "current tax year" flip
        # happens at midnight in the platform's timezone, not UTC. Otherwise a
        # west-coast venue whose New Year's Eve W-9 was saved just after 4 PM
        # PDT would show as "current" ~8 hours before the calendar year actually
        # changed in their local time.
        from backend.routes.tax import _current_tax_year
        current_year = _current_tax_year(db)
        # User-level W-9 (entity_type='user') is the canonical record for
        # affiliate payouts — see tax.py:save_user_w9.
        row = db.execute(text("""
            SELECT id FROM w9_forms
            WHERE entity_type = 'user'
              AND entity_id = :uid
              AND tax_year >= :yr
        """), {"uid": entity_id, "yr": current_year}).fetchone()
        return row is not None

    return False


def _user_is_affiliate(db, user_id):
    """A user becomes an affiliate the moment at least one venue is linked
    to them via affiliate_referrals. We use this gate to suppress the
    checklist for users who haven't yet had a referral land."""
    row = db.execute(text(
        "SELECT 1 FROM affiliate_referrals WHERE affiliate_user_id = :uid LIMIT 1"
    ), {"uid": user_id}).fetchone()
    return row is not None


_VALID_ENTITY_TYPES = ("venue", "artist", "affiliate")

_TASK_BY_TYPE = {
    "venue":     (VENUE_TASKS,     _check_venue_mandatory),
    "artist":    (ARTIST_TASKS,    _check_artist_mandatory),
    "affiliate": (AFFILIATE_TASKS, _check_affiliate_mandatory),
}


@router.get("/api/onboarding/{entity_type}/{entity_id}")
def get_onboarding_checklist(entity_type: str, entity_id: int,
                              user=Depends(get_current_user), db=Depends(get_db)):
    """Get onboarding checklist with completion statuses"""
    if entity_type not in _VALID_ENTITY_TYPES:
        raise HTTPException(400, "Invalid entity type")

    _check_entity_access(db, user, entity_type, entity_id)

    # Affiliate checklist is only relevant once at least one referral exists —
    # for users with no linked venues yet, return an empty list so the
    # frontend doesn't pop a useless modal.
    if entity_type == "affiliate" and not _user_is_affiliate(db, entity_id):
        return {"tasks": [], "all_complete": True, "is_affiliate": False}

    # Get visit-based completions
    visits = db.execute(text("""
        SELECT task_key FROM onboarding_visits
        WHERE entity_type = :etype AND entity_id = :eid
    """), {"etype": entity_type, "eid": entity_id}).fetchall()
    visited_keys = {r[0] for r in visits}

    tasks, check_fn = _TASK_BY_TYPE[entity_type]

    result = []
    all_complete = True
    for task in tasks:
        if task["mandatory"]:
            completed = check_fn(db, entity_id, task["key"])
        else:
            completed = task["key"] in visited_keys

        if not completed:
            all_complete = False

        result.append({
            "key": task["key"],
            "title": task["title"],
            "description": task["description"],
            "mandatory": task["mandatory"],
            "completed": completed,
        })

    return {"tasks": result, "all_complete": all_complete, "is_affiliate": True if entity_type == "affiliate" else None}


@router.post("/api/onboarding/{entity_type}/{entity_id}/{task_key}/visit")
def mark_task_visited(entity_type: str, entity_id: int, task_key: str,
                      user=Depends(get_current_user), db=Depends(get_db)):
    """Mark a visit-based task as completed"""
    if entity_type not in _VALID_ENTITY_TYPES:
        raise HTTPException(400, "Invalid entity type")

    _check_entity_access(db, user, entity_type, entity_id)

    db.execute(text("""
        INSERT OR IGNORE INTO onboarding_visits (entity_type, entity_id, task_key, visited_at)
        VALUES (:etype, :eid, :tkey, CURRENT_TIMESTAMP)
    """), {"etype": entity_type, "eid": entity_id, "tkey": task_key})
    db.commit()

    return {"ok": True}

# backend/routes/onboarding.py — Setup Checklist API

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
from datetime import datetime

router = APIRouter()


def _check_entity_access(db, user, entity_type, entity_id):
    """Verify user owns or has access to this entity"""
    if entity_type == 'venue':
        row = db.execute(text("""
            SELECT v.id FROM venues v
            WHERE v.id = :eid AND (
                v.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'venue'
                           AND eu.entity_id = :eid AND eu.user_id = :uid)
            )
        """), {"eid": entity_id, "uid": user.id}).fetchone()
    else:
        row = db.execute(text("""
            SELECT a.id FROM artists a
            WHERE a.id = :eid AND (
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
    {
        "key": "payments",
        "title": "Payments",
        "description": "Enter your Payout Account with Stripe. Your earnings will be paid to this account.",
        "mandatory": True,
    },
    {
        "key": "tax_info",
        "title": "Tax Information",
        "description": "Complete a W-9 form (some Venues will require this).",
        "mandatory": True,
    },
    {
        "key": "edit_profile",
        "title": "Edit Artist Profile",
        "description": "Finish your Artist profile. A complete profile will help Venues approve your preferred artist request.",
        "mandatory": False,
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
        current_year = datetime.now().year
        row = db.execute(text("""
            SELECT id FROM w9_forms
            WHERE entity_type = 'artist' AND entity_id = :eid
            AND tax_year >= :yr
        """), {"eid": entity_id, "yr": current_year}).fetchone()
        return row is not None

    return False


@router.get("/api/onboarding/{entity_type}/{entity_id}")
def get_onboarding_checklist(entity_type: str, entity_id: int,
                              user=Depends(get_current_user), db=Depends(get_db)):
    """Get onboarding checklist with completion statuses"""
    if entity_type not in ("venue", "artist"):
        raise HTTPException(400, "Invalid entity type")

    _check_entity_access(db, user, entity_type, entity_id)

    # Get visit-based completions
    visits = db.execute(text("""
        SELECT task_key FROM onboarding_visits
        WHERE entity_type = :etype AND entity_id = :eid
    """), {"etype": entity_type, "eid": entity_id}).fetchall()
    visited_keys = {r[0] for r in visits}

    tasks = VENUE_TASKS if entity_type == "venue" else ARTIST_TASKS
    check_fn = _check_venue_mandatory if entity_type == "venue" else _check_artist_mandatory

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

    return {"tasks": result, "all_complete": all_complete}


@router.post("/api/onboarding/{entity_type}/{entity_id}/{task_key}/visit")
def mark_task_visited(entity_type: str, entity_id: int, task_key: str,
                      user=Depends(get_current_user), db=Depends(get_db)):
    """Mark a visit-based task as completed"""
    if entity_type not in ("venue", "artist"):
        raise HTTPException(400, "Invalid entity type")

    _check_entity_access(db, user, entity_type, entity_id)

    db.execute(text("""
        INSERT OR IGNORE INTO onboarding_visits (entity_type, entity_id, task_key, visited_at)
        VALUES (:etype, :eid, :tkey, CURRENT_TIMESTAMP)
    """), {"etype": entity_type, "eid": entity_id, "tkey": task_key})
    db.commit()

    return {"ok": True}

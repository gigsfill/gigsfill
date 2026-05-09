"""
Email management routes
"""
from fastapi import APIRouter, Depends, HTTPException
import logging
from sqlalchemy import text
from .auth import get_current_user
from backend.db import get_db
logger = logging.getLogger("gigsfill.emails")

router = APIRouter()

# NOTE: GET /api/email-templates is in routes_admin.py (requires admin)

@router.put("/api/email-templates/{notification_type}")
def update_email_template(
    notification_type: str,
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Update email template (admin only).

    NOTE: this endpoint is redundant with PUT /api/email-templates in
    routes/admin.py (which is what the admin UI actually calls). It's kept
    here for any external callers, but the auth check has been hardened
    (May 2026) — previously `if not admin_row["is_admin"]` was buggy because
    `is_admin` is stored as the literal TEXT 'false', and `not 'false'` is
    False in Python (any non-empty string is truthy). So a non-admin user
    with `is_admin = 'false'` would PASS the gate. Now we use the same
    string-aware check the rest of the codebase uses.
    """
    # Enforce admin-only access — centralized via to_admin_bool (May 2026 audit).
    from backend.utils import to_admin_bool
    admin_row = db.execute(
        text("SELECT is_admin FROM users WHERE id = :uid"),
        {"uid": user.id}
    ).mappings().first()
    if not admin_row or not to_admin_bool(admin_row.get("is_admin")):
        raise HTTPException(status_code=403, detail="Admin access required")
    from datetime import datetime
    
    db.execute(
        text("""
            UPDATE email_templates
            SET subject = :subject,
                body = :body,
                updated_at = :updated_at
            WHERE template_key = :type
        """),
        {
            "type": notification_type,
            "subject": data.get("subject"),
            "body": data.get("body"),
            "updated_at": utcnow_naive()
        }
    )
    db.commit()
    
    return {"ok": True}

@router.get("/api/user-email-preferences")
def get_user_email_preferences(user=Depends(get_current_user), db=Depends(get_db)):
    """Get email preferences for current user"""
    try:
        # Get all template keys from email_templates (NOT notification_type)
        all_types = db.execute(
            text("SELECT DISTINCT template_key FROM email_templates")
        ).fetchall()
        
        # Filter to artist_, venue_ templates plus special keys
        _EXTRA_KEYS = {'waitlist_offer', 'transfer_failed_venue'}
        types_list = [row[0] for row in all_types if row[0].startswith('artist_') or row[0].startswith('venue_') or row[0] in _EXTRA_KEYS]
        
        # Get user's existing preferences
        user_prefs_result = db.execute(
            text("SELECT notification_type, enabled FROM email_preferences WHERE user_id = :uid"),
            {"uid": user.id}
        ).fetchall()
        
        user_prefs = {row[0]: row[1] for row in user_prefs_result}
        
        # Blast types default to OFF for 4w/2w, ON for 1w/36h
        BLAST_DEFAULTS = {
            'venue_open_gig_4w':              False,
            'venue_open_gig_2w':              False,
            'venue_open_gig_1w':              True,
            'venue_open_gig_36h':             True,
            'cancelled_gig_preferred_blast':  True,
            'cancelled_gig_radius_blast':     True,
        }
        # Add blast types even if not in email_templates (scheduler uses them)
        all_blast_keys = list(BLAST_DEFAULTS.keys())
        for k in all_blast_keys:
            if k not in types_list:
                types_list.append(k)

        return [
            {
                'notification_type': t,
                'enabled': user_prefs[t] if t in user_prefs else BLAST_DEFAULTS.get(t, True)
            }
            for t in types_list
        ]
    except Exception as e:
        return []

@router.put("/api/user-email-preferences")
def update_user_email_preferences(
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Update user's email preferences"""
    notification_type = data.get("notification_type")
    enabled = 1 if data.get("enabled", True) else 0
    
    
    try:
        # v97: Check if row exists first
        existing = db.execute(
            text("SELECT id FROM email_preferences WHERE user_id = :uid AND notification_type = :type"),
            {"uid": user.id, "type": notification_type}
        ).fetchone()
        
        if existing:
            # Update existing
            db.execute(
                text("UPDATE email_preferences SET enabled = :enabled WHERE user_id = :uid AND notification_type = :type"),
                {"uid": user.id, "type": notification_type, "enabled": enabled}
            )
        else:
            # Insert new
            db.execute(
                text("INSERT INTO email_preferences (user_id, notification_type, enabled) VALUES (:uid, :type, :enabled)"),
                {"uid": user.id, "type": notification_type, "enabled": enabled}
            )
        
        db.commit()
        return {"ok": True}
        
    except Exception as e:
        raise HTTPException(500, "Failed to update preference. Please try again.")


# ==========================================
# SMS PREFERENCES
# ==========================================

@router.get("/api/sms-carriers")
def get_sms_carriers():
    """Return list of supported SMS carriers"""
    from backend.sms_service import CARRIER_NAMES
    return [{"id": k, "name": v} for k, v in CARRIER_NAMES.items()]


@router.get("/api/user-sms-preferences")
def get_user_sms_preferences(user=Depends(get_current_user), db=Depends(get_db)):
    """Get SMS preferences for current user"""
    try:
        # Use same notification types as email
        all_types = db.execute(
            text("SELECT DISTINCT template_key FROM email_templates")
        ).fetchall()
        types_list = [row[0] for row in all_types if row[0].startswith('artist_') or row[0].startswith('venue_')]

        try:
            user_prefs_result = db.execute(
                text("SELECT notification_type, enabled FROM sms_preferences WHERE user_id = :uid"),
                {"uid": user.id}
            ).fetchall()
        except Exception:
            # Table doesn't exist yet - create it
            db.rollback()
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS sms_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    notification_type TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, notification_type)
                )
            """))
            db.commit()
            user_prefs_result = []

        user_prefs = {row[0]: row[1] for row in user_prefs_result}

        return [
            {
                'notification_type': t,
                'enabled': user_prefs.get(t, False)  # Default OFF for SMS (opt-in)
            }
            for t in types_list
        ]
    except Exception as e:
        logger.error(f"Error: {e}")
        return []


@router.put("/api/user-sms-preferences")
def update_user_sms_preferences(
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Update user's SMS preference for a notification type"""
    notification_type = data.get("notification_type")
    enabled = 1 if data.get("enabled", False) else 0

    def _ensure_table():
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS sms_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                notification_type TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, notification_type)
            )
        """))
        db.commit()

    try:
        existing = db.execute(
            text("SELECT id FROM sms_preferences WHERE user_id = :uid AND notification_type = :type"),
            {"uid": user.id, "type": notification_type}
        ).fetchone()

        if existing:
            db.execute(
                text("UPDATE sms_preferences SET enabled = :enabled WHERE user_id = :uid AND notification_type = :type"),
                {"uid": user.id, "type": notification_type, "enabled": enabled}
            )
        else:
            db.execute(
                text("INSERT INTO sms_preferences (user_id, notification_type, enabled) VALUES (:uid, :type, :enabled)"),
                {"uid": user.id, "type": notification_type, "enabled": enabled}
            )

        db.commit()
        return {"ok": True}

    except Exception as e:
        # Table might not exist
        logger.error(f"First attempt failed: {e}, creating table...")
        try:
            db.rollback()
            _ensure_table()
            db.execute(
                text("INSERT OR REPLACE INTO sms_preferences (user_id, notification_type, enabled) VALUES (:uid, :type, :enabled)"),
                {"uid": user.id, "type": notification_type, "enabled": enabled}
            )
            db.commit()
            return {"ok": True}
        except Exception as e2:
            logger.error(f"Failed after table create: {e2}")
            raise HTTPException(500, "Failed to update SMS preference. Please try again.")


@router.put("/api/user-sms-carrier")
def update_user_sms_carrier(
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Update user's SMS carrier"""
    carrier = data.get("carrier") or None

    try:
        db.execute(
            text("UPDATE users SET sms_carrier = :carrier WHERE id = :uid"),
            {"uid": user.id, "carrier": carrier}
        )
        db.commit()
        logger.info(f"Saved carrier={carrier} for user={user.id}")
        return {"ok": True}
    except Exception as e:
        # Column might not exist yet - try to add it
        logger.error(f"First attempt failed: {e}, trying to add column...")
        try:
            db.rollback()
            db.execute(text("ALTER TABLE users ADD COLUMN sms_carrier VARCHAR"))
            db.commit()
            db.execute(
                text("UPDATE users SET sms_carrier = :carrier WHERE id = :uid"),
                {"uid": user.id, "carrier": carrier}
            )
            db.commit()
            logger.info(f"Column created and carrier={carrier} saved for user={user.id}")
            return {"ok": True}
        except Exception as e2:
            logger.error(f"Failed even after column add: {e2}")
            raise HTTPException(500, "Failed to update carrier. Please try again.")

"""
Venue Email Routes
Handles venue-to-artist email communications
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from datetime import datetime
from backend.utils import utcnow_naive
from typing import Optional
from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.email_service import EmailService

router = APIRouter()

def check_venue_access(venue_id: int, user_id: int, db) -> bool:
    """Check if user has access to venue (owner OR via entity_users)"""
    access = db.execute(
        text("""
            SELECT 1 FROM venues v
            LEFT JOIN entity_users eu ON eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid
            WHERE v.id = :vid AND (v.user_id = :uid OR eu.user_id = :uid)
        """),
        {"vid": venue_id, "uid": user_id}
    ).scalar()
    return access is not None

@router.post("/api/venues/send-email")
async def send_venue_email(
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Send email from venue to multiple artists"""
    
    venue_id = data.get('venue_id')
    venue_name = data.get('venue_name')
    artist_ids = data.get('artist_ids', [])
    subject = data.get('subject', '').strip()
    body = data.get('body', '').strip()
    
    # Validation
    if not venue_id or not artist_ids or not subject or not body:
        raise HTTPException(400, "Missing required fields")
    
    if len(artist_ids) == 0:
        raise HTTPException(400, "No recipients selected")
    
    if len(subject) > 200:
        raise HTTPException(400, "Subject too long (max 200 characters)")
    
    if len(body) > 5000:
        raise HTTPException(400, "Message too long (max 5000 characters)")
    
    # Verify user has access to this venue (owner OR entity_users)
    if not check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "You don't have permission to send emails from this venue")
    
    # Get artist emails
    placeholders = ','.join(f':id{i}' for i in range(len(artist_ids)))
    query = f"""
        SELECT u.id, u.email, a.name
        FROM artists a
        JOIN users u ON a.user_id = u.id
        WHERE a.id IN ({placeholders})
    """
    params = {f'id{i}': aid for i, aid in enumerate(artist_ids)}
    artist_emails = db.execute(text(query), params).mappings().all()
    
    if not artist_emails:
        raise HTTPException(404, "No valid artist emails found")
    
    # Initialize email service
    email_service = EmailService(db)
    
    if not email_service.enabled:
        raise HTTPException(503, "Email service is not configured. Please contact administrator.")
    
    # Send using venue_message_to_artists template
    sent_count = 0
    failed_count = 0
    sent_recipients = []  # FIX (May 2026): collect successful recipients for history

    # FIX (May 2026): build per-recipient template vars so the email's To: line
    # shows that artist's name. venue_name/subject/body are constant across
    # recipients, but artist_name is per-recipient.
    base_vars = {
        "venue_name": venue_name or "",
        "subject": subject,
        "body": body.replace("\n", "<br>"),
    }

    for artist in artist_emails:
        per_recipient_vars = dict(base_vars)
        per_recipient_vars["artist_name"] = artist.get("name") or "Artist"
        try:
            result = email_service.send_notification_email(
                artist['email'], artist['id'], "venue_message_to_artists", per_recipient_vars
            )
            if result:
                sent_count += 1
                sent_recipients.append({
                    "name":  artist.get("name") or "",
                    "email": artist.get("email") or "",
                })
            else:
                failed_count += 1
        except Exception as e:
            failed_count += 1
    
    # Save to email history
    # FIX (May 2026): include recipients_json so the modal can show
    # clickable name+email list instead of just "N artist(s)".
    import json as _json
    _recipients_json = _json.dumps(sent_recipients) if sent_recipients else None
    try:
        # Best-effort: ensure recipients_json column exists. Idempotent — ALTER fails
        # silently on second run if column is already there.
        try:
            db.execute(text("ALTER TABLE venue_email_history ADD COLUMN recipients_json TEXT"))
            db.commit()
        except Exception:
            pass

        db.execute(
            text("""
                INSERT INTO venue_email_history 
                (venue_id, venue_name, user_id, subject, body, recipient_count, sent_at, recipients_json)
                VALUES (:venue_id, :venue_name, :user_id, :subject, :body, :count, :sent_at, :recipients_json)
            """),
            {
                "venue_id": venue_id,
                "venue_name": venue_name,
                "user_id": user.id,
                "subject": subject,
                "body": body,
                "count": sent_count,
                "sent_at": utcnow_naive(),
                "recipients_json": _recipients_json,
            }
        )
        db.commit()
    except Exception as e:
        pass  # Don't fail the request if history save fails
    
    return {
        "ok": True,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "message": f"Email sent to {sent_count} artist(s)"
    }

@router.get("/api/venues/email-history")
def get_venue_email_history(
    venue_id: Optional[int] = Query(None),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get email history for user's venues. Optionally filter by venue_id."""
    
    # Build query based on whether venue_id is provided
    if venue_id:
        # Verify user has access to this venue (owner OR entity_users)
        if not check_venue_access(venue_id, user.id, db):
            raise HTTPException(403, "You don't have permission to view this venue's email history")
        
        history = db.execute(
            text("""
                SELECT 
                    h.id,
                    h.venue_id,
                    h.venue_name,
                    h.subject,
                    h.body,
                    h.recipient_count,
                    h.sent_at,
                    -- FIX (May 2026): include recipients_json so modal can show name+email list
                    COALESCE(h.recipients_json, NULL) as recipients_json
                FROM venue_email_history h
                WHERE h.venue_id = :venue_id
                ORDER BY h.sent_at DESC
                LIMIT 50
            """),
            {"venue_id": venue_id}
        ).mappings().all()
    else:
        # Return all email history for user's venues
        history = db.execute(
            text("""
                SELECT 
                    h.id,
                    h.venue_id,
                    h.venue_name,
                    h.subject,
                    h.body,
                    h.recipient_count,
                    h.sent_at,
                    COALESCE(h.recipients_json, NULL) as recipients_json
                FROM venue_email_history h
                WHERE h.user_id = :user_id
                ORDER BY h.sent_at DESC
                LIMIT 50
            """),
            {"user_id": user.id}
        ).mappings().all()
    
    return [dict(h) for h in history]

# Alias route for frontend compatibility
@router.get("/api/venue-emails/history")
def get_venue_email_history_alias(
    venue_id: Optional[int] = Query(None),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Alias for /api/venues/email-history for frontend compatibility"""
    return get_venue_email_history(venue_id, user, db)


@router.delete("/api/venue-emails/history/{email_id}")
def delete_venue_email_history_item(
    email_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Delete a single venue email history row.

    FIX (May 2026): allow venues to clean up their email history list. Auth
    requirement: requester must have access to the venue this email was sent
    from (owner OR entity_users). Verified via check_venue_access() against
    the row's venue_id, not just user_id, so a venue's secondary users can
    also clean up shared history.
    """
    row = db.execute(
        text("SELECT id, venue_id FROM venue_email_history WHERE id = :eid"),
        {"eid": email_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Email history row not found")

    if not check_venue_access(row["venue_id"], user.id, db):
        raise HTTPException(403, "You don't have permission to delete this email history")

    db.execute(text("DELETE FROM venue_email_history WHERE id = :eid"), {"eid": email_id})
    db.commit()
    return {"ok": True, "deleted_id": email_id}


def create_venue_email_html(venue_name: str, subject: str, body: str) -> str:
    """Create professional HTML email from venue to artists"""
    
    # Convert plain text body to HTML with line breaks
    body_html = body.replace('\n', '<br>')
    
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background-color: #0a0a0a;">
    <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #0a0a0a;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.3);">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #7B2CBF 0%, #9D4EDD 100%); padding: 40px 30px; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 32px; font-weight: bold;">🎸 GigsFill</h1>
                            <p style="margin: 10px 0 0 0; color: #E0E0E0; font-size: 14px;">Message from {venue_name}</p>
                        </td>
                    </tr>
                    
                    <!-- Main content -->
                    <tr>
                        <td style="padding: 40px 30px;">
                            <h2 style="margin: 0 0 20px 0; color: #10F7CF; font-size: 24px; font-weight: bold;">{subject}</h2>
                            
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="background: rgba(123, 44, 191, 0.1); border-left: 4px solid #7B2CBF; border-radius: 8px; margin: 30px 0;">
                                <tr>
                                    <td style="padding: 25px;">
                                        <p style="margin: 0; color: #E0E0E0; font-size: 16px; line-height: 1.6;">
                                            {body_html}
                                        </p>
                                    </td>
                                </tr>
                            </table>
                            
                            <p style="margin: 20px 0; color: #B0B0B0; font-size: 14px; line-height: 1.6;">
                                This message was sent to you as a preferred artist at <strong style="color: #9D4EDD;">{venue_name}</strong>.
                            </p>
                            
                            <!-- CTA Button -->
                            <table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 30px 0;">
                                <tr>
                                    <td align="center">
                                        <a href="https://gigsfill.com" style="display: inline-block; padding: 16px 40px; background: linear-gradient(135deg, #7B2CBF 0%, #9D4EDD 100%); color: #ffffff; text-decoration: none; border-radius: 8px; font-size: 16px; font-weight: bold; box-shadow: 0 4px 15px rgba(123, 44, 191, 0.4);">View on GigsFill</a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background: rgba(10, 10, 10, 0.6); padding: 30px; text-align: center; border-top: 1px solid rgba(255,255,255,0.1);">
                            <p style="margin: 0 0 10px 0; color: #B0B0B0; font-size: 14px;">
                                Stay connected! 🎸
                            </p>
                            <p style="margin: 0 0 20px 0; color: #808080; font-size: 12px;">
                                © 2026 GigsFill. Connecting artists with venues.
                            </p>
                            <p style="margin: 0; color: #606060; font-size: 11px;">
                                You received this because you are a preferred artist at {venue_name}.<br>
                                <a href="https://gigsfill.com/user-profile" style="color: #7B2CBF; text-decoration: none;">Manage Email Preferences</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>'''

# =====================================================
# VENUE EMAIL NOTIFICATION SETTINGS (automated gig emails)
# =====================================================

NOTIFICATION_DEFAULTS = {
    'gig_confirmation': {'time_value': 1,  'time_unit': 'weeks', 'template_key': 'venue_gig_confirmation_reminder', 'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_4w':      {'time_value': 4,  'time_unit': 'weeks', 'template_key': 'venue_open_gig_4w',              'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_2w':      {'time_value': 2,  'time_unit': 'weeks', 'template_key': 'venue_open_gig_2w',              'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_1w':      {'time_value': 1,  'time_unit': 'weeks', 'template_key': 'venue_open_gig_1w',              'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_36h':     {'time_value': 36, 'time_unit': 'hours', 'template_key': 'venue_open_gig_36h', 'blast_all_enabled': True,  'blast_all_radius': 20, 'blink_enabled': False, 'blink_color': '#f59e0b'},
    'cancelled_blast':  {'time_value': 1,  'time_unit': 'weeks', 'template_key': 'cancelled_gig_preferred_blast', 'radius_miles': None, 'blast_all_enabled': True,  'blast_all_radius': 20, 'blink_enabled': True,  'blink_color': '#f59e0b'},
    'radius_blast':     {'time_value': 36, 'time_unit': 'hours', 'template_key': 'cancelled_gig_radius_blast', 'radius_miles': 20, 'blast_all_enabled': True,  'blast_all_radius': 20, 'blink_enabled': True,  'blink_color': '#f59e0b'},
}

@router.get("/api/venues/{venue_id}/email-notifications")
def get_venue_email_notifications(
    venue_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get automated email notification settings for a venue"""
    if not check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    rows = db.execute(
        text("""
            SELECT notification_key, enabled, time_value, time_unit, radius_miles,
                   COALESCE(blast_all_enabled, 0) as blast_all_enabled,
                   blast_all_radius,
                   COALESCE(blink_enabled, 0) as blink_enabled,
                   blink_color
            FROM venue_email_notifications
            WHERE venue_id = :vid
        """),
        {"vid": venue_id}
    ).mappings().all()
    
    # Build response with defaults for missing keys (default ON)
    settings = {}
    for key, defaults in NOTIFICATION_DEFAULTS.items():
        settings[key] = {
            'enabled': True,
            'time_value': defaults['time_value'],
            'time_unit': defaults['time_unit'],
            'radius_miles': defaults.get('radius_miles'),
            'blast_all_enabled': defaults.get('blast_all_enabled', False),
            'blast_all_radius': defaults.get('blast_all_radius', 20),
            'blink_enabled': defaults.get('blink_enabled', False),
            'blink_color': defaults.get('blink_color', '#f59e0b'),
        }
    
    _default_blink_colors = {
        'gig_confirmation': '#10b981',
        'open_gig_4w':      '#10b981',
        'open_gig_2w':      '#10b981',
        'open_gig_1w':      '#f59e0b',
        'open_gig_36h':     '#f59e0b',
        'cancelled_blast':  '#f59e0b',
        'radius_blast':     '#f59e0b',
    }

    for row in rows:
        key = row['notification_key']
        if key in settings:
            settings[key] = {
                'enabled': bool(row['enabled']),
                'time_value': row['time_value'],
                'time_unit': row['time_unit'],
                'radius_miles': row['radius_miles'],
                'blast_all_enabled': bool(row.get('blast_all_enabled', 0)),
                'blast_all_radius': row.get('blast_all_radius') or 20,
                'blink_enabled': bool(row.get('blink_enabled', 0)),
                'blink_color': row.get('blink_color') or _default_blink_colors.get(key, '#f59e0b'),
            }
    
    return settings

@router.post("/api/venues/{venue_id}/email-notifications")
def save_venue_email_notifications(
    venue_id: int,
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Save automated email notification settings for a venue"""
    if not check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "Access denied")
    
    for key, val in data.items():
        if key not in NOTIFICATION_DEFAULTS:
            continue
        
        enabled = 1 if val.get('enabled') else 0
        time_value = int(val.get('time_value', NOTIFICATION_DEFAULTS[key]['time_value']))
        time_unit = val.get('time_unit', NOTIFICATION_DEFAULTS[key]['time_unit'])
        radius_miles_raw = val.get('radius_miles')
        radius_miles = int(radius_miles_raw) if radius_miles_raw is not None else None
        
        if time_unit not in ('weeks', 'days', 'hours'):
            time_unit = 'weeks'
        if time_value < 1:
            time_value = 1
        if time_value > 52:
            time_value = 52
        if radius_miles is not None:
            radius_miles = max(1, min(500, radius_miles))
        
        blast_all_enabled = 1 if val.get('blast_all_enabled') else 0
        blast_all_radius_raw = val.get('blast_all_radius')
        blast_all_radius = int(blast_all_radius_raw) if blast_all_radius_raw else 20
        blast_all_radius = max(1, min(500, blast_all_radius))

        blink_enabled = 1 if val.get('blink_enabled') else 0
        blink_color = (val.get('blink_color') or '').strip() or None
        # Validate hex color
        if blink_color and not (blink_color.startswith('#') and len(blink_color) in (4, 7)):
            blink_color = None

        try:
            db.execute(
                text("""
                    INSERT INTO venue_email_notifications
                        (venue_id, notification_key, enabled, time_value, time_unit, radius_miles,
                         blast_all_enabled, blast_all_radius, blink_enabled, blink_color, updated_at)
                    VALUES (:vid, :key, :enabled, :tv, :tu, :rm, :bae, :bar, :be, :bc, CURRENT_TIMESTAMP)
                    ON CONFLICT(venue_id, notification_key)
                    DO UPDATE SET enabled = :enabled, time_value = :tv, time_unit = :tu, radius_miles = :rm,
                        blast_all_enabled = :bae, blast_all_radius = :bar,
                        blink_enabled = :be, blink_color = :bc, updated_at = CURRENT_TIMESTAMP
                """),
                {"vid": venue_id, "key": key, "enabled": enabled, "tv": time_value, "tu": time_unit,
                 "rm": radius_miles, "bae": blast_all_enabled, "bar": blast_all_radius,
                 "be": blink_enabled, "bc": blink_color}
            )
        except Exception as _col_err:
            # Fallback: columns may not exist yet — save core fields only
            db.execute(
                text("""
                    INSERT INTO venue_email_notifications
                        (venue_id, notification_key, enabled, time_value, time_unit, radius_miles, updated_at)
                    VALUES (:vid, :key, :enabled, :tv, :tu, :rm, CURRENT_TIMESTAMP)
                    ON CONFLICT(venue_id, notification_key)
                    DO UPDATE SET enabled = :enabled, time_value = :tv, time_unit = :tu,
                        radius_miles = :rm, updated_at = CURRENT_TIMESTAMP
                """),
                {"vid": venue_id, "key": key, "enabled": enabled, "tv": time_value,
                 "tu": time_unit, "rm": radius_miles}
            )
    
    db.commit()
    return {"ok": True}


# =====================================================
# PUBLIC blast settings — no auth, used by artist calendar
# Returns only the fields artists need to decide whether to blink gig bubbles
# =====================================================

@router.get("/api/venues/{venue_id}/blast-settings/public")
def get_venue_blast_settings_public(
    venue_id: int,
    db=Depends(get_db)
):
    """
    Public endpoint — no auth required.
    Returns blink/blast settings for open_gig_1w, open_gig_36h, cancelled_blast, radius_blast
    so the artist calendar can decide which gig bubbles to blink and for whom.
    """
    keys_needed = ['open_gig_1w', 'open_gig_36h', 'cancelled_blast', 'radius_blast']

    defaults = {
        'open_gig_1w':     {'enabled': True,  'time_value': 1,  'time_unit': 'weeks', 'blast_all_enabled': False, 'blast_all_radius': 20, 'blink_enabled': True,  'blink_color': '#f59e0b'},
        'open_gig_36h':    {'enabled': True,  'time_value': 36, 'time_unit': 'hours', 'blast_all_enabled': False, 'blast_all_radius': 20, 'blink_enabled': True,  'blink_color': '#f59e0b'},
        'cancelled_blast': {'enabled': True,  'time_value': 7,  'time_unit': 'days',  'blast_all_enabled': False, 'blast_all_radius': 20, 'blink_enabled': True,  'blink_color': '#f59e0b'},
        'radius_blast':    {'enabled': True,  'time_value': 36, 'time_unit': 'hours', 'blast_all_enabled': False, 'blast_all_radius': 20, 'blink_enabled': True,  'blink_color': '#f59e0b'},
    }

    rows = db.execute(
        text("""
            SELECT notification_key, enabled, time_value, time_unit,
                   COALESCE(blast_all_enabled, 0) as blast_all_enabled,
                   COALESCE(blast_all_radius, 20) as blast_all_radius,
                   COALESCE(blink_enabled, 0) as blink_enabled,
                   blink_color
            FROM venue_email_notifications
            WHERE venue_id = :vid AND notification_key IN ('open_gig_1w','open_gig_36h','cancelled_blast','radius_blast')
        """),
        {"vid": venue_id}
    ).mappings().all()

    result = dict(defaults)
    for row in rows:
        key = row['notification_key']
        if key in result:
            result[key] = {
                'enabled':          bool(row['enabled']),
                'time_value':       row['time_value'],
                'time_unit':        row['time_unit'],
                'blast_all_enabled': bool(row['blast_all_enabled']),
                'blast_all_radius': row['blast_all_radius'] or 20,
                'blink_enabled':    bool(row['blink_enabled']),
                'blink_color':      row['blink_color'] or '#f59e0b',
            }

    return result

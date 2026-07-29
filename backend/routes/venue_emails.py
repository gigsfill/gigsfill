"""
Venue Email Routes
Handles venue-to-artist email communications
"""
import html as _html
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from datetime import datetime
from backend.utils import utcnow_naive
from typing import Optional
from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.email_service import EmailService
from backend.rate_limiter import limiter

router = APIRouter()

# Audit fix (May 2026 part 2): hard cap on recipients per broadcast and a
# per-venue rate limit. Earlier this endpoint had neither, so a venue user
# (or a compromised one) could send to every approved preferred artist
# with arbitrary HTML in `body` as a phishing vector.
MAX_BROADCAST_RECIPIENTS = 100
BROADCAST_RATE = "5/minute"

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
@limiter.limit(BROADCAST_RATE)
async def send_venue_email(
    data: dict,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Send email from venue to multiple artists.

    Audit fix (May 2026 part 2):
      - Rate limited (BROADCAST_RATE) so a compromised account can't flood.
      - Recipients capped at MAX_BROADCAST_RECIPIENTS per call.
      - Subject + venue_name + body are HTML-escaped before being injected
        into the template, then only newline → <br> is re-introduced.
        Previously a venue user could embed arbitrary HTML (script-style
        phishing payloads) in the body via the {{body}} placeholder.
    """
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

    if len(artist_ids) > MAX_BROADCAST_RECIPIENTS:
        raise HTTPException(
            400,
            f"Too many recipients — broadcasts are limited to {MAX_BROADCAST_RECIPIENTS} artists per send."
        )

    if len(subject) > 200:
        raise HTTPException(400, "Subject too long (max 200 characters)")

    if len(body) > 5000:
        raise HTTPException(400, "Message too long (max 5000 characters)")

    # Verify user has access to this venue (owner OR entity_users)
    if not check_venue_access(venue_id, user.id, db):
        raise HTTPException(403, "You don't have permission to send emails from this venue")

    # BUG FIX (Jul 2026 audit): scope the recipient list. Previously a venue
    # could POST any artist_ids and the send would proceed — effectively
    # anonymous mass-email to any artist on the platform. Restrict to
    # artists the venue has an established relationship with:
    #   (a) preferred artists (status='approved'), OR
    #   (b) any artist who has ever been booked (past or in-flight) on
    #       one of this venue's gigs.
    # Anything outside that set is dropped from artist_ids. If the entire
    # list is filtered out, return 400 so the venue sees why.
    _in_ph = ','.join(f':aid{i}' for i in range(len(artist_ids)))
    _in_params = {f'aid{i}': aid for i, aid in enumerate(artist_ids)}
    _allowed_rows = db.execute(text(f"""
        SELECT DISTINCT pa.artist_id AS aid FROM preferred_artists pa
        WHERE pa.venue_id = :vid AND pa.status = 'approved'
          AND pa.artist_id IN ({_in_ph})
        UNION
        SELECT DISTINCT gs.artist_id AS aid
        FROM gigs g
        JOIN gig_slots gs ON gs.gig_id = g.id
        WHERE g.venue_id = :vid
          AND gs.artist_id IS NOT NULL
          AND gs.artist_id IN ({_in_ph})
    """), {**_in_params, "vid": venue_id}).fetchall()
    _allowed = {row[0] for row in _allowed_rows}
    _dropped = [aid for aid in artist_ids if aid not in _allowed]
    if _dropped:
        import logging as _drop_log
        _drop_log.getLogger("gigsfill.venue_emails").warning(
            f"venue {venue_id} tried to email non-preferred/non-booked artist_ids={_dropped}"
        )
    artist_ids = [aid for aid in artist_ids if aid in _allowed]
    if not artist_ids:
        raise HTTPException(400,
            "No eligible recipients. You can only email preferred artists or "
            "artists who have been booked at your venue.")

    # Sanitize text that will be substituted into the HTML email template.
    # Strip every tag, then re-introduce newline → <br>. The previous code
    # piped the raw body straight into {{body}}, which let venues author
    # arbitrary HTML payloads.
    subject = _html.escape(subject, quote=True)
    venue_name = _html.escape(venue_name or "", quote=True)
    body = _html.escape(body, quote=False)
    
    # Get artist emails — include every entity_user for each selected artist
    # (owner + any added bandmates/agents/sound techs), de-duplicated by
    # email. Audit fix (May 2026 part 4): previously the JOIN only used
    # `a.user_id`, so multi-user artist accounts only received the bulk
    # email at the primary user's address — the artist's actual booking
    # contact (a different entity_user) was missed.
    placeholders = ','.join(f':id{i}' for i in range(len(artist_ids)))
    params = {f'id{i}': aid for i, aid in enumerate(artist_ids)}
    artist_emails_rows = db.execute(text(f"""
        SELECT DISTINCT u.id, u.email, a.name
        FROM artists a
        JOIN users u ON u.id = a.user_id
        WHERE a.id IN ({placeholders})
        UNION
        SELECT DISTINCT u.id, u.email, a.name
        FROM artists a
        JOIN entity_users eu
          ON eu.entity_type = 'artist' AND eu.entity_id = a.id
        JOIN users u ON u.id = eu.user_id
        WHERE a.id IN ({placeholders})
    """), params).mappings().all()
    # Dedupe by email
    _seen = set()
    artist_emails = []
    for r in artist_emails_rows:
        if r["email"] and r["email"] not in _seen:
            _seen.add(r["email"])
            artist_emails.append(r)

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

    # Audit fix (Jul 2026): args were in the wrong order (real signature:
    # `check_venue_access(db, venue_id, user_id)`) AND the return value
    # was treated as a bool but the helper raises on failure / returns
    # None on success. Endpoint was previously broken — either 500 on
    # arg mis-type or 403 for everyone.
    check_venue_access(db, row["venue_id"], user.id)

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
    # Jul 2026: per-key waive_frequency + cancel_* defaults per venue-user spec.
    # Early reminders (4w/2w) don't waive frequency limits and don't fire
    # cancellation blasts; late reminders (1w/36h) waive frequency + fire
    # cancellation blasts (venue is trying harder to fill the gig as it
    # approaches). cancelled_blast + radius_blast are legacy rows kept for
    # backwards compat; fire_cancelled_gig_blast no longer reads them.
    'gig_confirmation': {'time_value': 1,  'time_unit': 'weeks', 'template_key': 'venue_gig_confirmation_reminder', 'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_4w':      {'time_value': 4,  'time_unit': 'weeks', 'template_key': 'venue_open_gig_4w',              'blast_all_enabled': False, 'blast_all_radius': 20, 'waive_frequency': False, 'cancel_notify_preferred': False, 'cancel_notify_all_enabled': False, 'cancel_notify_all_radius': 20, 'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_2w':      {'time_value': 2,  'time_unit': 'weeks', 'template_key': 'venue_open_gig_2w',              'blast_all_enabled': False, 'blast_all_radius': 20, 'waive_frequency': False, 'cancel_notify_preferred': False, 'cancel_notify_all_enabled': False, 'cancel_notify_all_radius': 20, 'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_1w':      {'time_value': 1,  'time_unit': 'weeks', 'template_key': 'venue_open_gig_1w',              'blast_all_enabled': False, 'blast_all_radius': 20, 'waive_frequency': True,  'cancel_notify_preferred': True,  'cancel_notify_all_enabled': False, 'cancel_notify_all_radius': 20, 'blink_enabled': False, 'blink_color': '#10b981'},
    'open_gig_36h':     {'time_value': 36, 'time_unit': 'hours', 'template_key': 'venue_open_gig_36h',             'blast_all_enabled': False, 'blast_all_radius': 20, 'waive_frequency': True,  'cancel_notify_preferred': True,  'cancel_notify_all_enabled': True,  'cancel_notify_all_radius': 20, 'blink_enabled': False, 'blink_color': '#f59e0b'},
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
                   blink_color,
                   COALESCE(waive_frequency, 1) as waive_frequency,
                   COALESCE(cancel_notify_preferred, 0)   as cancel_notify_preferred,
                   COALESCE(cancel_notify_all_enabled, 0) as cancel_notify_all_enabled,
                   COALESCE(cancel_notify_all_radius, 20) as cancel_notify_all_radius
            FROM venue_email_notifications
            WHERE venue_id = :vid
        """),
        {"vid": venue_id}
    ).mappings().all()

    # Keys that consume the Jul 2026 fields — audit fix so the API response
    # doesn't hydrate irrelevant defaults into keys that don't read them
    # (was returning waive_frequency=True on gig_confirmation etc., which the
    # frontend echoed back and POST'd into DB rows, creating meaningless state).
    _OPEN_GIG_KEYS = {'open_gig_4w', 'open_gig_2w', 'open_gig_1w', 'open_gig_36h'}

    def _base_settings(key, defaults, *, enabled, time_value, time_unit, radius_miles,
                      blast_all_enabled, blast_all_radius, blink_enabled, blink_color,
                      waive_frequency, cancel_notify_preferred,
                      cancel_notify_all_enabled, cancel_notify_all_radius):
        """Assemble the response dict for one notification key, including only
        the fields that key actually consumes. Falls back to NOTIFICATION_DEFAULTS
        for blink_color so both the pristine-venue path and the null-color path
        use the same source of truth (was previously two divergent maps)."""
        out = {
            'enabled': enabled,
            'time_value': time_value,
            'time_unit': time_unit,
            'radius_miles': radius_miles,
            'blast_all_enabled': blast_all_enabled,
            'blast_all_radius': blast_all_radius,
            'blink_enabled': blink_enabled,
            'blink_color': blink_color or defaults.get('blink_color', '#f59e0b'),
        }
        # Only the 4 open-gig rows consume these — omit for others so the
        # frontend doesn't round-trip meaningless values into DB.
        if key in _OPEN_GIG_KEYS:
            out['waive_frequency'] = waive_frequency
            out['cancel_notify_preferred'] = cancel_notify_preferred
            out['cancel_notify_all_enabled'] = cancel_notify_all_enabled
            out['cancel_notify_all_radius'] = cancel_notify_all_radius
        return out

    # Build response with defaults for missing keys (default ON)
    settings = {}
    for key, defaults in NOTIFICATION_DEFAULTS.items():
        settings[key] = _base_settings(
            key, defaults,
            enabled=True,
            time_value=defaults['time_value'],
            time_unit=defaults['time_unit'],
            radius_miles=defaults.get('radius_miles'),
            blast_all_enabled=defaults.get('blast_all_enabled', False),
            blast_all_radius=defaults.get('blast_all_radius', 20),
            blink_enabled=defaults.get('blink_enabled', False),
            blink_color=defaults.get('blink_color'),
            waive_frequency=defaults.get('waive_frequency', True),
            cancel_notify_preferred=defaults.get('cancel_notify_preferred', False),
            cancel_notify_all_enabled=defaults.get('cancel_notify_all_enabled', False),
            cancel_notify_all_radius=defaults.get('cancel_notify_all_radius', 20),
        )

    for row in rows:
        key = row['notification_key']
        if key in settings:
            defaults = NOTIFICATION_DEFAULTS[key]
            settings[key] = _base_settings(
                key, defaults,
                enabled=bool(row['enabled']),
                time_value=row['time_value'],
                time_unit=row['time_unit'],
                radius_miles=row['radius_miles'],
                blast_all_enabled=bool(row.get('blast_all_enabled', 0)),
                blast_all_radius=row.get('blast_all_radius') or 20,
                blink_enabled=bool(row.get('blink_enabled', 0)),
                blink_color=row.get('blink_color'),  # None → falls back to defaults inside _base_settings
                waive_frequency=bool(row.get('waive_frequency', 1)),
                cancel_notify_preferred=bool(row.get('cancel_notify_preferred', 0)),
                cancel_notify_all_enabled=bool(row.get('cancel_notify_all_enabled', 0)),
                cancel_notify_all_radius=row.get('cancel_notify_all_radius') or 20,
            )
    
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

        # Jul 2026: waive_frequency toggle (default 1 = current behavior)
        waive_frequency = 0 if val.get('waive_frequency') is False else 1

        # Jul 2026: per-window cancellation blast toggles.
        cancel_notify_preferred = 1 if val.get('cancel_notify_preferred') else 0
        cancel_notify_all_enabled = 1 if val.get('cancel_notify_all_enabled') else 0
        cancel_notify_all_radius_raw = val.get('cancel_notify_all_radius')
        cancel_notify_all_radius = int(cancel_notify_all_radius_raw) if cancel_notify_all_radius_raw else 20
        cancel_notify_all_radius = max(1, min(500, cancel_notify_all_radius))

        # Jul 2026: enforce per-key semantics. Only open_gig_* rows consume the
        # waive_frequency + cancel_notify_* fields. For any other key, force DB
        # defaults so a stale payload can't accumulate meaningless state (was
        # writing waive_frequency=1 into gig_confirmation/cancelled_blast/
        # radius_blast rows on every save).
        _OPEN_GIG_KEYS = ('open_gig_4w', 'open_gig_2w', 'open_gig_1w', 'open_gig_36h')
        if key not in _OPEN_GIG_KEYS:
            waive_frequency = 1
            cancel_notify_preferred = 0
            cancel_notify_all_enabled = 0
            cancel_notify_all_radius = 20

        _params = {"vid": venue_id, "key": key, "enabled": enabled, "tv": time_value, "tu": time_unit,
                   "rm": radius_miles, "bae": blast_all_enabled, "bar": blast_all_radius,
                   "be": blink_enabled, "bc": blink_color, "wf": waive_frequency,
                   "cnp": cancel_notify_preferred, "cnae": cancel_notify_all_enabled,
                   "cnar": cancel_notify_all_radius}
        try:
            db.execute(
                text("""
                    INSERT INTO venue_email_notifications
                        (venue_id, notification_key, enabled, time_value, time_unit, radius_miles,
                         blast_all_enabled, blast_all_radius, blink_enabled, blink_color,
                         waive_frequency, cancel_notify_preferred, cancel_notify_all_enabled,
                         cancel_notify_all_radius, updated_at)
                    VALUES (:vid, :key, :enabled, :tv, :tu, :rm, :bae, :bar, :be, :bc, :wf,
                            :cnp, :cnae, :cnar, CURRENT_TIMESTAMP)
                    ON CONFLICT(venue_id, notification_key)
                    DO UPDATE SET enabled = :enabled, time_value = :tv, time_unit = :tu, radius_miles = :rm,
                        blast_all_enabled = :bae, blast_all_radius = :bar,
                        blink_enabled = :be, blink_color = :bc,
                        waive_frequency = :wf,
                        cancel_notify_preferred = :cnp,
                        cancel_notify_all_enabled = :cnae,
                        cancel_notify_all_radius = :cnar,
                        updated_at = CURRENT_TIMESTAMP
                """),
                _params
            )
        except Exception as _col_err:
            # Tiered fallback for DBs missing one of the additive column sets.
            # Try trimming Jul 2026 fields first, then May 2026 blast_*/blink_*,
            # so we keep as much of the payload as the schema supports.
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
                    {k: _params[k] for k in ("vid", "key", "enabled", "tv", "tu", "rm", "bae", "bar", "be", "bc")}
                )
            except Exception:
                db.execute(
                    text("""
                        INSERT INTO venue_email_notifications
                            (venue_id, notification_key, enabled, time_value, time_unit, radius_miles, updated_at)
                        VALUES (:vid, :key, :enabled, :tv, :tu, :rm, CURRENT_TIMESTAMP)
                        ON CONFLICT(venue_id, notification_key)
                        DO UPDATE SET enabled = :enabled, time_value = :tv, time_unit = :tu,
                            radius_miles = :rm, updated_at = CURRENT_TIMESTAMP
                    """),
                    {k: _params[k] for k in ("vid", "key", "enabled", "tv", "tu", "rm")}
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

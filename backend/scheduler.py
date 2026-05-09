"""
GigsFill Background Scheduler
==============================
Processes automated gig email notifications.
Runs in a background thread, checks every hour for emails that need to be sent.
"""

import threading
import logging
import time
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
from backend.services.notification_service import format_time_12hr
from backend.services.email_dispatch import format_email_date
from backend.email_service import BLAST_OFF_DEFAULTS
logger = logging.getLogger("gigsfill.scheduler")

DB_PATH = Path(__file__).parent.parent / "backend.db"


def _raw_db_conn():
    """Return a raw sqlite3 connection with row_factory set."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _build_venue_detail_vars(cursor, venue_id, gig_notes=None):
    """Fetch venue details from SQLite cursor and return human-readable template variables."""
    try:
        cursor.execute("""
            SELECT venue_size,
                   address_line_1, address_line_2, city, state, postal_code,
                   has_stage, stage_width_ft, stage_depth_ft, setup_location_description,
                   has_sound_equipment, sound_equipment_description,
                   has_sound_engineer, sound_engineer_details,
                   has_lighting, lighting_description,
                   arrival_time_type, arrival_no_earlier_than_hour, arrival_no_earlier_than_period,
                   bar_tab_details, food_tab_details
            FROM venues WHERE id = ?
        """, (venue_id,))
        row = cursor.fetchone()
        if not row:
            return {}
        cols = ['venue_size','address_line_1','address_line_2','city','state','postal_code',
                'has_stage','stage_width_ft','stage_depth_ft','setup_location_description',
                'has_sound_equipment','sound_equipment_description','has_sound_engineer','sound_engineer_details',
                'has_lighting','lighting_description','arrival_time_type','arrival_no_earlier_than_hour',
                'arrival_no_earlier_than_period','bar_tab_details','food_tab_details']
        v = dict(zip(cols, row))

        # Address
        parts = []
        if v.get('address_line_1'): parts.append(v['address_line_1'])
        if v.get('address_line_2'): parts.append(v['address_line_2'])
        city_state_zip = ' '.join(filter(None, [v.get('city'), v.get('state'), v.get('postal_code')]))
        if city_state_zip: parts.append(city_state_zip)
        venue_address = ', '.join(parts) if parts else 'Not provided'

        # Capacity
        venue_capacity = v.get('venue_size') or 'Not specified'

        # Arrival
        atype = (v.get('arrival_time_type') or '').lower().strip()
        if atype == 'flexible':
            arrival_info = 'Flexible'
        elif atype == 'no_earlier_than' and v.get('arrival_no_earlier_than_hour'):
            h = int(v['arrival_no_earlier_than_hour'])
            period = (v.get('arrival_no_earlier_than_period') or 'PM').upper()
            arrival_info = f'No earlier than {h}:00 {period}'
        elif atype == 'no_earlier_than':
            # Type set but hour not filled in
            arrival_info = 'No earlier than — time not specified'
        else:
            # Not set at all — treat as flexible
            arrival_info = 'Flexible'

        # Stage
        if v.get('has_stage'):
            w, d = v.get('stage_width_ft'), v.get('stage_depth_ft')
            stage_info = f'Yes — {w}ft x {d}ft' if w and d else ('Yes — ' + v['setup_location_description']) if v.get('setup_location_description') else 'Yes'
        else:
            desc = v.get('setup_location_description') or ''
            stage_info = f'No — {desc}' if desc else 'No'

        # Sound
        if v.get('has_sound_equipment'):
            desc = v.get('sound_equipment_description') or ''
            sound_info = f'Provided — {desc}' if desc else 'Provided'
        else:
            sound_info = 'No — bring your own'

        # Engineer
        if v.get('has_sound_engineer'):
            details = v.get('sound_engineer_details') or ''
            engineer_info = f'Provided — {details}' if details else 'Provided'
        else:
            engineer_info = 'No'

        # Lighting
        if v.get('has_lighting'):
            desc = v.get('lighting_description') or ''
            lighting_info = f'Provided — {desc}' if desc else 'Provided'
        else:
            lighting_info = 'No'

        from urllib.parse import quote
        maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(venue_address)}" if venue_address and venue_address != 'Not provided' else ''
        venue_address_link = f'<a href="{maps_url}" target="_blank" style="color: #8b5cf6; text-decoration: none;">{venue_address}</a>' if maps_url else venue_address

        return {
            'venue_address':      venue_address,
            'venue_address_link': venue_address_link,
            'venue_capacity':  venue_capacity,
            'arrival_info':    arrival_info,
            'stage_info':      stage_info,
            'sound_info':      sound_info,
            'engineer_info':   engineer_info,
            'lighting_info':   lighting_info,
            'bar_tab':         v.get('bar_tab_details') or 'None',
            'food_tab':        v.get('food_tab_details') or 'None',
            'notes_to_artist': gig_notes or '',
        }
    except Exception as e:
        logger.warning(f"Could not fetch venue details for {venue_id}: {e}")
        return {}

# Map notification_key -> email template_key
TEMPLATE_MAP = {
    'gig_confirmation': 'venue_gig_confirmation_reminder',
    'open_gig_4w':      'venue_open_gig_4w',
    'open_gig_2w':      'venue_open_gig_2w',
    'open_gig_1w':      'venue_open_gig_1w',
    'open_gig_36h':     'venue_open_gig_36h',
    'cancelled_blast':  'cancelled_gig_preferred_blast',
    'radius_blast':     'cancelled_gig_radius_blast',
}


def get_smtp_settings(cursor):
    """Load SMTP settings from platform_settings"""
    cursor.execute(
        "SELECT setting_key, setting_value FROM platform_settings "
        "WHERE setting_key LIKE '%smtp%' OR setting_key LIKE '%email%'"
    )
    settings = {row[0]: row[1] for row in cursor.fetchall()}

    smtp_config = {
        'server': 'smtp.gmail.com',
        'port': 587,
        'username': '',
        'password': '',
        'from_email': 'noreply@gigsfill.com',
    }

    smtp_config['username'] = (
        settings.get('platform_email') or settings.get('smtp_email') or ''
    )
    smtp_config['from_email'] = smtp_config['username'] or 'noreply@gigsfill.com'
    smtp_config['from_name'] = settings.get('platform_email_from_name') or ''
    smtp_config['password'] = (
        settings.get('platform_email_password') or settings.get('smtp_password') or ''
    )
    if settings.get('platform_smtp_server') or settings.get('smtp_server'):
        smtp_config['server'] = settings.get('platform_smtp_server') or settings.get('smtp_server')
    if settings.get('platform_smtp_port') or settings.get('smtp_port'):
        smtp_config['port'] = int(settings.get('platform_smtp_port') or settings.get('smtp_port'))

    return smtp_config


def get_template(cursor, template_key):
    """Get email template by key"""
    # Try template_key column first, then notification_type
    cursor.execute(
        "SELECT subject, body FROM email_templates WHERE template_key = ? LIMIT 1",
        (template_key,)
    )
    row = cursor.fetchone()
    if row:
        return {'subject': row[0], 'body': row[1]}

    cursor.execute(
        "SELECT subject, body FROM email_templates WHERE notification_type = ? LIMIT 1",
        (template_key,)
    )
    row = cursor.fetchone()
    if row:
        return {'subject': row[0], 'body': row[1]}

    return None


def render_template(template_str, variables):
    """Replace {{variable}} placeholders. Supports {{#var}}...{{/var}} conditional blocks."""
    import re
    result = template_str
    def _replace_block(m):
        key = m.group(1)
        inner = m.group(2)
        return inner if variables.get(key) else ''
    result = re.sub(r'\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}', _replace_block, result, flags=re.DOTALL)
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", str(value or ''))
    return result


def send_email(smtp_config, to_email, subject, body_html):
    """Send a single email"""
    if not smtp_config['username'] or not smtp_config['password']:
        return False

    try:
        msg = MIMEMultipart('alternative')
        _fn = smtp_config.get('from_name', '')
        if _fn:
            from email.utils import formataddr as _fa
            msg['From'] = _fa((_fn, smtp_config['from_email']))
        else:
            msg['From'] = smtp_config['from_email']
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body_html, 'html'))

        port = smtp_config['port']
        if port == 465:
            # SSL
            with smtplib.SMTP_SSL(smtp_config['server'], port, timeout=15) as server:
                server.login(smtp_config['username'], smtp_config['password'])
                server.send_message(msg)
        elif port in (587, 2587):
            # STARTTLS
            with smtplib.SMTP(smtp_config['server'], port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_config['username'], smtp_config['password'])
                server.send_message(msg)
        else:
            # Plain SMTP (port 25, 26, etc.) — try starttls first, fall back to plain
            with smtplib.SMTP(smtp_config['server'], port, timeout=15) as server:
                server.ehlo()
                try:
                    server.starttls()
                    server.ehlo()
                except Exception:
                    pass  # Server doesn't support STARTTLS — proceed plain
                server.login(smtp_config['username'], smtp_config['password'])
                server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Email send failed to {to_email}: {e}")
        return False


def get_platform_timezone():
    """Read platform timezone from DB; fall back to America/Los_Angeles."""
    from zoneinfo import ZoneInfo
    try:
        conn = _raw_db_conn()
        row = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return ZoneInfo(row[0])
    except Exception as e:
        logger.warning(f"Could not read platform_timezone: {e}")
    return ZoneInfo("America/Los_Angeles")


def compute_target_date(time_value, time_unit):
    """Compute the gig date we should be targeting today based on the lead time.
    Uses platform local time so a gig on Friday reads as Friday even after 4 PM UTC.
    For 'hours', returns the date of the datetime that is time_value hours from now."""
    tz = get_platform_timezone()
    now_local = datetime.now(tz)
    if time_unit == 'weeks':
        return now_local.date() + timedelta(weeks=time_value)
    elif time_unit == 'hours':
        return (now_local + timedelta(hours=time_value)).date()
    else:
        return now_local.date() + timedelta(days=time_value)


def compute_target_datetime(time_value, time_unit):
    """For hour-based blasts: return the exact window (start, end) to match gig start times."""
    tz = get_platform_timezone()
    now_local = datetime.now(tz)
    if time_unit == 'hours':
        target = now_local + timedelta(hours=time_value)
        return target, target + timedelta(hours=1)
    elif time_unit == 'weeks':
        target_date = now_local.date() + timedelta(weeks=time_value)
        return None, None  # date-based, not window-based
    else:
        target_date = now_local.date() + timedelta(days=time_value)
        return None, None


def process_gig_confirmation(cursor, smtp_config):
    """Process gig confirmation reminders for booked gigs"""
    # Get all active venues — use saved settings if present, otherwise use defaults (enabled=True, 1 week)
    cursor.execute("""
        SELECT v.id as venue_id,
               COALESCE(ven.time_value, 1) as time_value,
               COALESCE(ven.time_unit, 'weeks') as time_unit
        FROM venues v
        LEFT JOIN venue_email_notifications ven
            ON ven.venue_id = v.id AND ven.notification_key = 'gig_confirmation'
        WHERE COALESCE(ven.enabled, 1) = 1
          AND COALESCE(v.payment_status, 'active') != 'suspended'
    """)
    venue_settings = cursor.fetchall()

    template = get_template(cursor, 'venue_gig_confirmation_reminder')
    if not template:
        return

    for venue_id, time_value, time_unit in venue_settings:
        target_date = compute_target_date(time_value, time_unit)
        target_date_str = target_date.strftime('%Y-%m-%d')

        # Find booked gigs on the target date that haven't had this email sent.
        # FIX (May 2026): Dedup is now strictly by (gig_id, notification_key).
        # Previously the SELECT also keyed on sent_for_date, intending to allow
        # re-send if a venue changed their timing setting — but the
        # gig_email_log UNIQUE constraint is (gig_id, notification_key) only,
        # so the INSERT would silently no-op while the email kept sending,
        # causing hourly spam. Once a confirmation has been sent for a gig,
        # it is never re-sent — even if the venue changes their lead time.
        # The sent_for_date column is kept in the schema for informational
        # purposes but is no longer part of dedup.
        # Query covers both single-artist gigs (artist_id on gigs) and
        # multi-slot gigs (artist_id on gig_slots). Uses UNION to avoid duplicates.
        cursor.execute("""
            SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.notes, g.artist_id,
                   v.venue_name as venue_name
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            WHERE g.venue_id = ?
              AND g.date = ?
              AND g.status = 'booked'
              AND g.artist_id IS NOT NULL
              AND g.id NOT IN (
                  SELECT gig_id FROM gig_email_log
                  WHERE notification_key = 'gig_confirmation'
              )
            UNION
            SELECT g.id, g.date, g.start_time, g.end_time,
                   COALESCE(gs.pay, g.pay) as pay, g.notes, gs.artist_id,
                   v.venue_name as venue_name
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
            WHERE g.venue_id = ?
              AND g.date = ?
              AND g.status = 'booked'
              AND gs.artist_id IS NOT NULL
              AND g.id NOT IN (
                  SELECT gig_id FROM gig_email_log
                  WHERE notification_key = 'gig_confirmation'
              )
        """, (venue_id, target_date_str,
               venue_id, target_date_str))

        gigs = cursor.fetchall()

        for gig in gigs:
            gig_id, date, start_time, end_time, pay, gig_notes, artist_id, venue_name = gig

            # Get artist info
            cursor.execute("""
                SELECT a.name, u.email, u.id as user_id
                FROM artists a
                JOIN users u ON u.id = a.user_id
                WHERE a.id = ?
            """, (artist_id,))
            artist = cursor.fetchone()
            if not artist:
                continue

            artist_name, artist_email, user_id = artist

            # Check if artist has email notifications enabled
            cursor.execute("""
                SELECT enabled FROM email_preferences
                WHERE user_id = ? AND notification_type = 'venue_gig_confirmation_reminder'
            """, (user_id,))
            pref = cursor.fetchone()
            if pref and pref[0] == 0:
                continue  # Artist disabled this notification

            venue_vars = _build_venue_detail_vars(cursor, venue_id, gig_notes=gig_notes)
            variables = {
                'artist_name': artist_name,
                'venue_name': venue_name,
                'artist_id': str(artist_id),
                'venue_id': str(venue_id),
                'date': format_email_date(date),
                'start_time': format_time_12hr(start_time),
                'end_time': format_time_12hr(end_time),
                'pay': pay or '0',
                **venue_vars,
            }

            subject = render_template(template['subject'], variables)
            body = render_template(template['body'], variables)

            if send_email(smtp_config, artist_email, subject, body):
                # FIX (May 2026): use UPSERT so multi-slot gigs (multiple artists
                # per gig) increment recipient_count correctly. Previously this
                # was INSERT OR IGNORE which left the count at 1 regardless of
                # how many artists got the email.
                cursor.execute("""
                    INSERT INTO gig_email_log (gig_id, venue_id, notification_key, sent_for_date, recipient_count)
                    VALUES (?, ?, 'gig_confirmation', ?, 1)
                    ON CONFLICT(gig_id, notification_key) DO UPDATE SET
                        recipient_count = recipient_count + 1
                """, (gig_id, venue_id, target_date_str))


def _build_slots_html_for_scheduler(cursor, gig_id, gig_pay, gig_artist_type, gig_band_formats, gig_styles):
    """Build slots_html table rows for scheduler emails."""
    from backend.services.notification_service import format_time_12hr
    ROW = '<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">{label}</td><td style="padding:6px 0;font-size:14px;color:{color};font-weight:{weight};">{value}</td></tr>'
    SEP = '<tr><td colspan="2" style="padding:4px 0;border-top:1px solid #e5e7eb;"></td></tr>'
    try:
        slots = cursor.execute(
            "SELECT start_time, end_time, pay, artist_type, band_formats, styles FROM gig_slots WHERE gig_id=? AND status='open' ORDER BY start_time",
            (gig_id,)
        ).fetchall()
    except Exception:
        slots = []
    if not slots:
        # Single-gig fallback
        t_start = format_time_12hr(gig_pay) if False else ''  # unused
        time_str = ''
        pay_val = str(gig_pay or '0')
        atype = gig_artist_type or ''
        lineup = ', '.join(x.strip() for x in (gig_band_formats or '').split(',') if x.strip())
        styles = ', '.join(x.strip() for x in (gig_styles or '').split(',') if x.strip())
        return ''  # no slots, template will use start_time/pay/etc directly
    html = ''
    for i, s in enumerate(slots):
        if i > 0:
            html += SEP
        t_s = format_time_12hr(s[0] or '')
        t_e = format_time_12hr(s[1] or '')
        time_str = f"{t_s} – {t_e}" if t_e else t_s
        pay_val = str(s[2] or gig_pay or '0')
        try:
            pf = float(pay_val); pay_val = f"{pf:.2f}" if pf != int(pf) else str(int(pf))
        except Exception:
            pass
        atype  = s[3] or gig_artist_type or ''
        lineup = ', '.join(x.strip() for x in (s[4] or gig_band_formats or '').split(',') if x.strip())
        st     = ', '.join(x.strip() for x in (s[5] or gig_styles or '').split(',') if x.strip())
        html += ROW.format(label="Time",  color="#111827", weight="500", value=time_str)
        html += ROW.format(label="Pay",   color="#059669", weight="600", value=f"${pay_val}")
        if atype:
            html += ROW.format(label="Type",  color="#111827", weight="500", value=atype)
        if lineup:
            html += ROW.format(label="Lineup", color="#111827", weight="500", value=lineup)
        if st:
            html += ROW.format(label="Styles", color="#111827", weight="500", value=st)
    return html


def process_open_gig_notifications(cursor, smtp_config, notification_key):
    """Process open gig notifications — email all preferred artists"""
    logger.info(f"[SCHED] process_open_gig_notifications: {notification_key}")
    template_key = TEMPLATE_MAP.get(notification_key)
    if not template_key:
        logger.warning(f"[SCHED] No template_key for {notification_key}")
        return

    # Default time values per notification key
    default_time = {'open_gig_4w': (4, 'weeks'), 'open_gig_2w': (2, 'weeks'), 'open_gig_1w': (1, 'weeks'), 'open_gig_36h': (36, 'hours')}
    def_val, def_unit = default_time.get(notification_key, (1, 'weeks'))

    # Get all active venues — use saved settings if present, otherwise use defaults (enabled=True)
    cursor.execute("""
        SELECT v.id as venue_id,
               COALESCE(ven.time_value, ?) as time_value,
               COALESCE(ven.time_unit, ?) as time_unit,
               COALESCE(ven.blast_all_enabled, 0) as blast_all_enabled,
               COALESCE(ven.blast_all_radius, 20) as blast_all_radius
        FROM venues v
        LEFT JOIN venue_email_notifications ven
            ON ven.venue_id = v.id AND ven.notification_key = ?
        WHERE COALESCE(ven.enabled, 1) = 1
          AND COALESCE(v.payment_status, 'active') != 'suspended'
    """, (def_val, def_unit, notification_key))
    venue_settings = cursor.fetchall()

    template = get_template(cursor, template_key)
    if not template:
        return

    import math as _math
    tz = get_platform_timezone()
    now_tz = datetime.now(tz)

    for venue_row in venue_settings:
        venue_id       = venue_row[0]
        time_value     = venue_row[1]
        time_unit      = venue_row[2]
        blast_all_en   = bool(venue_row[3])
        blast_all_mi   = int(venue_row[4] or 20)

        # Compute target date; for hours compute a time window
        target_date = compute_target_date(time_value, time_unit)
        target_date_str = target_date.strftime('%Y-%m-%d')

        # Find open gigs on target date not yet notified.
        # FIX (May 2026): dedup by (gig_id, notification_key) only — see comment in
        # process_gig_confirmation. Once an email has been sent for this gig+key,
        # it is never re-sent, even if the venue changes their lead time.
        cursor.execute("""
            SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.notes, g.artist_type,
                   g.title, g.band_formats, g.styles,
                   v.venue_name, v.city, v.state, v.latitude, v.longitude,
                   g.last_cancelled_artist_id
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            WHERE g.venue_id = ?
              AND g.date = ?
              AND g.status = 'open'
              AND (g.artist_id IS NULL OR g.artist_id = '')
              AND g.id NOT IN (
                  SELECT gig_id FROM gig_email_log
                  WHERE notification_key = ?
              )
        """, (venue_id, target_date_str, notification_key))

        gigs = cursor.fetchall()

        for gig in gigs:
            gig_id, date, start_time, end_time, pay, gig_notes, artist_type, gig_title, band_formats, styles, venue_name, city, state, vlat, vlon, _last_cancelled = gig

            # For hours-based blasts, fire if gig is within the target window.
            # Window: from now+(hours-2) to now+(hours+2) — 4h total to handle
            # scheduler timing variance and missed runs.
            # Also fires as a catch-up if gig hasn't been notified yet and is still future.
            if time_unit == 'hours':
                try:
                    gig_dt_naive = datetime.strptime(f"{date} {(start_time or '00:00')[:5]}", "%Y-%m-%d %H:%M")
                    gig_dt = gig_dt_naive.replace(tzinfo=tz)
                    # Primary window: ±2h around target
                    window_start = now_tz + timedelta(hours=time_value - 2.0)
                    window_end   = now_tz + timedelta(hours=time_value + 2.0)
                    # Catch-up: also fire if gig is still in the future but missed the window
                    # (gig hasn't been notified, still open, gig start is at least 1h away)
                    in_catchup = (gig_dt > now_tz + timedelta(hours=1)) and (gig_dt <= now_tz + timedelta(hours=time_value + 2.0))
                    if not (window_start <= gig_dt <= window_end) and not in_catchup:
                        continue
                except Exception as _we:
                    continue

            # Set frequency_exempt so any preferred artist can book regardless of frequency limits.
            # Do NOT set radius_blast_token here — that field sets is_blast_open on the calendar
            # and must only be set by the actual radius_blast (cancelled gig blast) process.
            cursor.execute(
                "UPDATE gigs SET frequency_exempt = 1 WHERE id = ?",
                (gig_id,)
            )
            blast_token = ''  # not a radius blast — no token needed
            venue_vars = _build_venue_detail_vars(cursor, venue_id, gig_notes=gig_notes)
            sent_count = 0

            # ── Always notify preferred artists ──
            # FIX (May 2026): exclude artists with a blackout date covering the gig date.
            # An artist who blocked May 6 should not get a blast for a May 6 gig.
            # The same filter is applied in the blast-all branch below and in radius_blast.
            cursor.execute("""
                SELECT a.id as artist_id, a.name, u.email, u.id as user_id
                FROM preferred_artists pa
                JOIN artists a ON a.id = pa.artist_id
                JOIN users u ON u.id = a.user_id
                WHERE pa.venue_id = ? AND pa.status = 'approved'
                  AND a.id NOT IN (
                      SELECT artist_id FROM venue_artist_bans WHERE venue_id = ?
                  )
                  AND (? IS NULL OR a.id != ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM artist_availability aa
                      WHERE aa.artist_id = a.id
                        AND date(?) BETWEEN date(aa.blackout_start) AND date(aa.blackout_end)
                  )
            """, (venue_id, venue_id, _last_cancelled, _last_cancelled, str(date)[:10]))
            preferred = cursor.fetchall()
            # Add ALL preferred artists to notified_user_ids immediately so radius blast
            # never sends them a duplicate email, even if their email pref is disabled
            notified_user_ids = {p[3] for p in preferred}

            for a_id, artist_name, artist_email, user_id in preferred:
                cursor.execute("""
                    SELECT enabled FROM email_preferences
                    WHERE user_id = ? AND notification_type = ?
                """, (user_id, template_key))
                pref = cursor.fetchone()
                if pref is not None:
                    if pref[0] == 0:
                        continue
                elif template_key in BLAST_OFF_DEFAULTS:
                    # Default OFF — artist must explicitly opt in. Currently only
                    # _4w and _2w (long-lead-time blasts) are in this set; _1w,
                    # _36h, and cancellation blasts default ON. See
                    # backend/email_service.py for the canonical list.
                    continue
                # Look up pay override for this preferred artist
                ov_row = cursor.execute(
                    "SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id=? AND artist_id=? AND status='approved'",
                    (venue_id, a_id)
                ).fetchone()
                effective_pay = pay  # default to published gig pay
                if ov_row and ov_row[0] is not None:
                    override_amt = float(ov_row[0]) + float(ov_row[1] or 0) / 100
                    pub_amt = float(pay or 0)
                    effective_pay = max(override_amt, pub_amt)
                pay_display = f"{float(effective_pay):.2f}" if effective_pay else "0"
                variables = {
                    'artist_name': artist_name, 'venue_name': venue_name,
                    'artist_id': str(a_id), 'venue_id': str(venue_id),
                    'gig_id': str(gig_id), 'blast_token': blast_token,
                    'date': format_email_date(date),
                    'start_time': format_time_12hr(start_time),
                    'end_time': format_time_12hr(end_time),
                    'pay': pay_display, 'artist_type': artist_type or '',
                    'title': gig_title or '', 'city': city or '', 'state': state or '',
                    'band_formats': ', '.join(x.strip() for x in (band_formats or '').split(',') if x.strip()),
                    'styles': ', '.join(x.strip() for x in (styles or '').split(',') if x.strip()),
                    'slots_html': _build_slots_html_for_scheduler(cursor, gig_id, effective_pay, artist_type, band_formats, styles),
                    **venue_vars,
                }
                subject = render_template(template['subject'], variables)
                body    = render_template(template['body'], variables)
                if send_email(smtp_config, artist_email, subject, body):
                    sent_count += 1
                    notified_user_ids.add(user_id)

            # ── blast_all: also notify all matching artists within radius ──
            if blast_all_en and vlat and vlon:
                # Bounding-box pre-filter — avoids full-table Python scan
                _lat_d = blast_all_mi / 69.0
                _lon_d = blast_all_mi / (69.0 * _math.cos(_math.radians(vlat)))
                cursor.execute("""
                    SELECT a.id, a.name, a.artist_type, a.latitude, a.longitude, u.email, u.id as user_id
                    FROM artists a JOIN users u ON u.id = a.user_id
                    WHERE a.latitude  BETWEEN ? AND ?
                      AND a.longitude BETWEEN ? AND ?
                      AND a.latitude  IS NOT NULL
                      AND a.longitude IS NOT NULL
                      AND u.id NOT IN (
                          SELECT u2.id FROM preferred_artists pa2
                          JOIN artists a2 ON a2.id = pa2.artist_id
                          JOIN users u2 ON u2.id = a2.user_id
                          WHERE pa2.venue_id = ? AND pa2.status = 'approved'
                      )
                      AND a.id NOT IN (
                          SELECT artist_id FROM venue_artist_bans WHERE venue_id = ?
                      )
                      AND (? IS NULL OR a.id != ?)
                      AND NOT EXISTS (
                          SELECT 1 FROM artist_availability aa
                          WHERE aa.artist_id = a.id
                            AND date(?) BETWEEN date(aa.blackout_start) AND date(aa.blackout_end)
                      )
                """, (vlat - _lat_d, vlat + _lat_d,
                      vlon - _lon_d, vlon + _lon_d,
                      venue_id, venue_id,
                      _last_cancelled, _last_cancelled,
                      str(date)[:10]))
                all_artists = cursor.fetchall()
                for a_id, a_name, a_type, alat, alon, a_email, user_id in all_artists:
                    if user_id in notified_user_ids:
                        continue
                    if artist_type and a_type and artist_type.lower() != a_type.lower():
                        continue
                    # Precise haversine on small candidate set
                    R = 3958.8
                    lat1,lon1 = _math.radians(vlat), _math.radians(vlon)
                    lat2,lon2 = _math.radians(alat), _math.radians(alon)
                    dlat,dlon = lat2-lat1, lon2-lon1
                    a_ = _math.sin(dlat/2)**2 + _math.cos(lat1)*_math.cos(lat2)*_math.sin(dlon/2)**2
                    dist = R * 2 * _math.asin(_math.sqrt(a_))
                    if dist > blast_all_mi:
                        continue
                    cursor.execute("""
                        SELECT enabled FROM email_preferences
                        WHERE user_id = ? AND notification_type = ?
                    """, (user_id, template_key))
                    pref = cursor.fetchone()
                    if pref is not None:
                        if pref[0] == 0:
                            continue
                    elif template_key in BLAST_OFF_DEFAULTS:
                        # Default OFF (see preferred-artist branch above for
                        # rationale + canonical constant in email_service.py).
                        continue
                    variables = {
                        'artist_name': a_name, 'venue_name': venue_name,
                        'artist_id': str(a_id), 'venue_id': str(venue_id),
                        'gig_id': str(gig_id), 'blast_token': blast_token,
                        'date': format_email_date(date),
                        'start_time': format_time_12hr(start_time),
                        'end_time': format_time_12hr(end_time),
                        'pay': pay or '0', 'artist_type': artist_type or '',
                        'title': gig_title or '', 'city': city or '', 'state': state or '',
                        'band_formats': ', '.join(x.strip() for x in (band_formats or '').split(',') if x.strip()),
                        'styles': ', '.join(x.strip() for x in (styles or '').split(',') if x.strip()),
                        'radius_miles': str(blast_all_mi),
                        'slots_html': _build_slots_html_for_scheduler(cursor, gig_id, pay, artist_type, band_formats, styles),
                        **venue_vars,
                    }
                    subject = render_template(template['subject'], variables)
                    body    = render_template(template['body'], variables)
                    if send_email(smtp_config, a_email, subject, body):
                        sent_count += 1

            cursor.execute("""
                INSERT OR IGNORE INTO gig_email_log (gig_id, venue_id, notification_key, sent_for_date, recipient_count)
                VALUES (?, ?, ?, ?, ?)
            """, (gig_id, venue_id, notification_key, target_date_str, sent_count))
            cursor.connection.commit()


def process_radius_blast(cursor, smtp_config):
    """36hr before gig start: email all matching artists within radius_miles of venue"""
    logger.info("[SCHED] process_radius_blast running")
    import math

    # Get all active venues — read time_value/time_unit/radius from saved settings
    cursor.execute("""
        SELECT v.id as venue_id,
               COALESCE(ven.radius_miles, 20) as radius_miles,
               COALESCE(ven.time_value, 36) as time_value,
               COALESCE(ven.time_unit, 'hours') as time_unit
        FROM venues v
        LEFT JOIN venue_email_notifications ven
            ON ven.venue_id = v.id AND ven.notification_key = 'radius_blast'
        WHERE COALESCE(ven.enabled, 1) = 1
          AND COALESCE(v.payment_status, 'active') != 'suspended'
    """)
    venue_settings = cursor.fetchall()

    # Use the open-gig 36h template — this is a live open gig blast, NOT a cancellation.
    # 'cancelled_gig_radius_blast' is the wrong template; it contains cancellation language.
    template = get_template(cursor, 'venue_open_gig_36h')
    if not template:
        # Fall back to radius blast template if 36h template not configured
        template = get_template(cursor, 'cancelled_gig_radius_blast')
    if not template:
        logger.warning("[SCHED] process_radius_blast: no template found (tried venue_open_gig_36h, cancelled_gig_radius_blast)")
        return

    tz = get_platform_timezone()
    now = datetime.now(tz)

    for venue_row in venue_settings:
        venue_id     = venue_row[0]
        radius_miles = venue_row[1] or 20
        time_value   = int(venue_row[2] or 36)
        time_unit    = str(venue_row[3] or 'hours')

        # Convert to hours for window calculation
        if time_unit == 'weeks':
            hours_before = time_value * 168
        elif time_unit == 'days':
            hours_before = time_value * 24
        else:
            hours_before = time_value

        window_start = now + timedelta(hours=hours_before - 2.0)
        window_end   = now + timedelta(hours=hours_before + 2.0)

        cursor.execute("""
            SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.notes, g.artist_type,
                   v.venue_name, v.city, v.state, v.latitude, v.longitude,
                   g.last_cancelled_artist_id
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            WHERE g.venue_id = ?
              AND g.status = 'open'
              AND (g.artist_id IS NULL OR g.artist_id = '')
              AND g.id NOT IN (
                  SELECT gig_id FROM gig_email_log WHERE notification_key = 'radius_blast'
              )
        """, (venue_id,))

        gigs = cursor.fetchall()
        for gig in gigs:
            gig_id, date, start_time, end_time, pay, gig_notes, artist_type, venue_name, city, state, vlat, vlon, _last_cancelled = gig

            # Parse gig start as platform-local naive datetime, then make it timezone-aware
            # so it can be compared directly with window_start/window_end
            try:
                gig_dt_str = f"{date} {(start_time or '00:00')[:5]}"
                gig_dt_naive = datetime.strptime(gig_dt_str, "%Y-%m-%d %H:%M")
                gig_dt = gig_dt_naive.replace(tzinfo=tz)
            except Exception:
                continue
            in_catchup_r = (gig_dt > now + timedelta(hours=1)) and (gig_dt <= now + timedelta(hours=hours_before + 2.0))
            if not (window_start <= gig_dt <= window_end) and not in_catchup_r:
                continue

            # Mark frequency exempt and set blast token so calendars show yellow bubble
            import secrets as _secrets
            blast_token = _secrets.token_urlsafe(32)
            cursor.execute(
                "UPDATE gigs SET frequency_exempt = 1, radius_blast_token = ? WHERE id = ?",
                (blast_token, gig_id)
            )

            # Bounding-box pre-filter — avoids full-table Python scan
            _lat_d2 = radius_miles / 69.0
            _lon_d2 = radius_miles / (69.0 * math.cos(math.radians(vlat))) if vlat else _lat_d2
            cursor.execute("""
                SELECT a.id, a.name, a.artist_type, a.latitude, a.longitude, u.email, u.id as user_id
                FROM artists a
                JOIN users u ON u.id = a.user_id
                WHERE a.latitude  BETWEEN ? AND ?
                  AND a.longitude BETWEEN ? AND ?
                  AND a.latitude  IS NOT NULL
                  AND a.longitude IS NOT NULL
                  AND a.id NOT IN (
                      SELECT artist_id FROM venue_artist_bans WHERE venue_id = ?
                  )
                  AND (? IS NULL OR a.id != ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM artist_availability aa
                      WHERE aa.artist_id = a.id
                        AND date(?) BETWEEN date(aa.blackout_start) AND date(aa.blackout_end)
                  )
            """, (vlat - _lat_d2, vlat + _lat_d2,
                  vlon - _lon_d2, vlon + _lon_d2, venue_id,
                  _last_cancelled, _last_cancelled,
                  str(date)[:10]))
            all_artists = cursor.fetchall()

            sent_count = 0
            for a_id, a_name, a_type, alat, alon, a_email, user_id in all_artists:
                # Match artist type
                if artist_type and a_type and artist_type.lower() != a_type.lower():
                    continue

                # Precise haversine on small candidate set
                if vlat and vlon:
                    R = 3958.8
                    lat1, lon1 = math.radians(vlat), math.radians(vlon)
                    lat2, lon2 = math.radians(alat), math.radians(alon)
                    dlat = lat2 - lat1
                    dlon = lon2 - lon1
                    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
                    dist = R * 2 * math.asin(math.sqrt(a))
                    if dist > radius_miles:
                        continue

                # Check email preference
                cursor.execute("""
                    SELECT enabled FROM email_preferences
                    WHERE user_id = ? AND notification_type = 'cancelled_gig_radius_blast'
                """, (user_id,))
                pref = cursor.fetchone()
                if pref and pref[0] == 0:
                    continue

                venue_vars = _build_venue_detail_vars(cursor, venue_id, gig_notes=gig_notes)
                variables = {
                    'artist_name': a_name,
                    'artist_id': str(a_id),
                    'venue_name': venue_name,
                    'date': format_email_date(date),
                    'start_time': format_time_12hr(start_time),
                    'end_time': format_time_12hr(end_time),
                    'pay': pay or '0',
                    'artist_type': artist_type or '',
                    'city': city or '',
                    'state': state or '',
                    'radius_miles': str(radius_miles),
                    **venue_vars,
                }
                subject = render_template(template['subject'], variables)
                body = render_template(template['body'], variables)
                if send_email(smtp_config, a_email, subject, body):
                    sent_count += 1

            cursor.execute("""
                INSERT OR IGNORE INTO gig_email_log (gig_id, venue_id, notification_key, sent_for_date, recipient_count)
                VALUES (?, ?, 'radius_blast', ?, ?)
            """, (gig_id, venue_id, date, sent_count))
            # Commit immediately so radius_blast_token is visible to calendars right away
            cursor.connection.commit()


def process_review_requests(cursor, smtp_config):
    """
    12 hours after a gig's end_time: email BOTH the venue (rate the artist)
    AND the artist (rate the venue). One email per direction per gig.
    Tracked in gig_email_log with keys 'venue_review_request' and 'artist_review_request'.
    Uses the venue_review_request / artist_review_request email templates.
    """
    try:
        from backend.db import get_db_connection as _raw_db_conn, _IS_POSTGRES, SessionLocal as _SL
        _db = _SL()
        try:
            from backend.email_service import EmailService
            from backend.services.email_dispatch import format_email_date
            es = EmailService(_db)
            base_url = "https://gigsfill.com"

            # Find booked gigs whose end_time was >=12h ago, <=7 days ago
            # Use end_time if set, else start_time + 2h as fallback
            # Use platform-local time for date comparisons (gig dates stored in platform tz)
            _rv_tz = get_platform_timezone()
            _rv_now = datetime.now(_rv_tz)
            _rv_today = _rv_now.strftime("%Y-%m-%d")
            _rv_7days_ago = (_rv_now - timedelta(days=7)).strftime("%Y-%m-%d")
            _rv_12h_ago = (_rv_now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M")
            rows = cursor.execute("""
                SELECT DISTINCT
                    g.id as gig_id, g.title, g.date, g.end_time, g.start_time,
                    v.id as venue_id, v.venue_name,
                    vu.id as venue_user_id, vu.email as venue_email,
                    a.id as artist_id, a.name as artist_name,
                    au.id as artist_user_id, au.email as artist_email
                FROM gigs g
                JOIN venues v ON v.id = g.venue_id
                JOIN users vu ON vu.id = v.user_id
                JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
                JOIN artists a ON a.id = gs.artist_id
                JOIN users au ON au.id = a.user_id
                WHERE g.status IN ('booked', 'completed', 'closed')
                  AND date(g.date) BETWEEN ? AND ?
                  AND (g.date || ' ' || COALESCE(g.end_time, time(g.start_time, '+2 hours')))
                      <= ?
                  AND vu.email IS NOT NULL AND vu.email != ''
                  AND au.email IS NOT NULL AND au.email != ''
            LIMIT 100
            """, (_rv_7days_ago, _rv_today, _rv_12h_ago)).fetchall()

            if not rows:
                return

            # Deduplicate: for multi-slot gigs the query returns one row per artist.
            # We only want ONE venue review email per gig (first artist listed).
            # Artist review emails: one per artist per gig.
            _venue_email_sent = set()   # gig_ids already emailed to venue this run
            _artist_email_sent = set()  # (gig_id, artist_id) pairs emailed this run

            for row in rows:
                gig_id     = row[0]
                gig_title  = row[1] or 'Gig'
                gig_date   = format_email_date(str(row[2] or '')[:10])
                venue_id   = row[5]
                venue_name = row[6] or 'The venue'
                venue_user_id  = row[7]
                venue_email    = row[8]
                artist_id  = row[9]
                artist_name    = row[10] or 'the artist'
                artist_user_id = row[11]
                artist_email   = row[12]

                # ── 1. Venue reviews artist ──────────────────────────────────
                # Skip if already sent to this venue for this gig in this run
                if gig_id in _venue_email_sent:
                    pass  # already handled below via already_sent_v check
                already_sent_v = cursor.execute(
                    "SELECT 1 FROM gig_email_log WHERE gig_id=? AND notification_key='venue_review_request'",
                    (gig_id,)
                ).fetchone()
                if gig_id in _venue_email_sent:
                    already_sent_v = True  # prevent second send within same run
                already_reviewed_v = cursor.execute(
                    "SELECT 1 FROM artist_reviews WHERE venue_id=? AND artist_id=?",
                    (venue_id, artist_id)
                ).fetchone()

                if not already_sent_v and not already_reviewed_v:
                    try:
                        from backend.routes.review_links import generate_review_token
                        rv_token = generate_review_token(
                            direction="venue_rates_artist",
                            gig_id=gig_id, venue_id=venue_id,
                            artist_id=artist_id, user_id=venue_user_id
                        )
                        review_url_v = f"{base_url}/app/review.html?token={rv_token}"
                    except Exception as _te:
                        review_url_v = f"{base_url}/app/venue-create-gigs.html?venue_id={venue_id}&tab=reviews&review_gig={gig_id}"
                        logger.warning(f"Failed to generate review token for gig {gig_id}: {_te}")
                    try:
                        es.send_notification_email(
                            user_email=venue_email,
                            user_id=venue_user_id,
                            notification_type="venue_review_request",
                            variables={
                                "venue_name":   venue_name,
                                "artist_name":  artist_name,
                                "gig_title":    gig_title,
                                "gig_date":     gig_date,
                                "review_url":   review_url_v,
                            }
                        )
                        cursor.execute(
                            "INSERT OR IGNORE INTO gig_email_log (gig_id, venue_id, notification_key, sent_at) VALUES (?, ?, 'venue_review_request', datetime('now'))",
                            (gig_id, venue_id)
                        )
                        cursor.connection.commit()
                        _venue_email_sent.add(gig_id)
                        logger.info(f"Venue review request sent to {venue_email} for gig {gig_id}")
                    except Exception as e:
                        logger.warning(f"Venue review request failed gig {gig_id}: {e}")

                # ── 2. Artist reviews venue ──────────────────────────────────
                # FIX (May 2026): For multi-slot gigs, dedup must be per-artist.
                # The gig_email_log UNIQUE constraint is (gig_id, notification_key),
                # so we encode the artist_id into the notification_key to give each
                # artist their own dedup row. Legacy rows with notification_key=
                # 'artist_review_request' (no suffix) remain in the DB; the per-
                # artist key naturally ignores them — those past artists won't
                # be re-emailed because the legacy row covered the first artist
                # of each gig and we don't want to spam old gigs with re-sends.
                _ar_key = f"artist_review_request:{artist_id}"
                already_sent_a = cursor.execute(
                    "SELECT 1 FROM gig_email_log WHERE gig_id=? AND notification_key=?",
                    (gig_id, _ar_key)
                ).fetchone()
                if (gig_id, artist_id) in _artist_email_sent:
                    already_sent_a = True
                # Check venue_reviews table if it exists
                already_reviewed_a = False
                try:
                    already_reviewed_a = cursor.execute(
                        "SELECT 1 FROM venue_reviews WHERE venue_id=? AND artist_id=?",
                        (venue_id, artist_id)
                    ).fetchone()
                except Exception:
                    pass  # table may not exist yet

                if not already_sent_a and not already_reviewed_a:
                    try:
                        from backend.routes.review_links import generate_review_token
                        ra_token = generate_review_token(
                            direction="artist_rates_venue",
                            gig_id=gig_id, venue_id=venue_id,
                            artist_id=artist_id, user_id=artist_user_id
                        )
                        review_url_a = f"{base_url}/app/review.html?token={ra_token}"
                    except Exception as _te:
                        review_url_a = f"{base_url}/app/artist-book-gigs.html?artist_id={artist_id}&tab=reviews&review_gig={gig_id}"
                        logger.warning(f"Failed to generate artist review token for gig {gig_id}: {_te}")
                    try:
                        es.send_notification_email(
                            user_email=artist_email,
                            user_id=artist_user_id,
                            notification_type="artist_review_request",
                            variables={
                                "artist_name":  artist_name,
                                "venue_name":   venue_name,
                                "gig_title":    gig_title,
                                "gig_date":     gig_date,
                                "review_url":   review_url_a,
                            }
                        )
                        cursor.execute(
                            "INSERT OR IGNORE INTO gig_email_log (gig_id, venue_id, notification_key, sent_at) VALUES (?, ?, ?, datetime('now'))",
                            (gig_id, venue_id, _ar_key)
                        )
                        cursor.connection.commit()
                        _artist_email_sent.add((gig_id, artist_id))
                        logger.info(f"Artist review request sent to {artist_email} for gig {gig_id} (artist {artist_id})")
                    except Exception as e:
                        logger.warning(f"Artist review request failed gig {gig_id} artist {artist_id}: {e}")

            cursor.connection.commit()
        finally:
            _db.close()

    except Exception as e:
        logger.error(f"process_review_requests error: {e}")



def process_waitlist_expirations():
    """
    Runs every hour.
    1. Clears waitlist entries for past or fully-gone gigs.
    2. Advances expired offers to the next artist.
    """
    try:
        from backend.db import SessionLocal
        from sqlalchemy import text as sa_text
        db = SessionLocal()
        try:
            # Clean up waitlist for past gigs (gig end time passed) or gigs that no longer exist
            # Use platform-local date so gigs tonight aren't pruned early due to UTC offset
            _wl_tz = get_platform_timezone()
            _wl_now = datetime.now(_wl_tz)
            _wl_today = _wl_now.strftime("%Y-%m-%d")
            _wl_time  = _wl_now.strftime("%H:%M")
            db.execute(sa_text("""
                DELETE FROM gig_waitlist
                WHERE gig_id NOT IN (SELECT id FROM gigs)
                   OR gig_id IN (
                       SELECT id FROM gigs WHERE
                           date < :wl_today
                           OR (date = :wl_today AND (
                               CASE
                                 WHEN end_time IS NOT NULL THEN end_time <= :wl_time
                                 WHEN start_time IS NOT NULL THEN time(start_time, '+4 hours') <= :wl_time
                                 ELSE 0
                               END
                           ))
                   )
            """), {"wl_today": _wl_today, "wl_time": _wl_time})
            db.commit()

            expired = db.execute(sa_text("""
                SELECT w.id, w.gig_id, w.artist_id
                FROM gig_waitlist w
                JOIN gigs g ON g.id = w.gig_id
                WHERE w.offer_sent = 1
                  AND w.offer_token IS NOT NULL
                  AND (w.offer_declined = 0 OR w.offer_declined IS NULL)
                  AND datetime(w.offer_expires_at) < datetime('now')
                  AND g.status = 'open'
            """)).mappings().all()

            for row in expired:
                try:
                    logger.info(f"[WAITLIST] Offer expired for artist {row['artist_id']} on gig {row['gig_id']} — advancing (timeout, NOT marked declined)")
                    # Delete the row — timed-out artists didn't actively decline,
                    # so they should still receive the open-gig blast if waitlist exhausts.
                    db.execute(sa_text(
                        "DELETE FROM gig_waitlist WHERE id = :wid"
                    ), {"wid": row["id"]})
                    db.commit()
                    from backend.routes.waitlist import advance_waitlist_offer
                    advance_waitlist_offer(db, row["gig_id"])
                except Exception as e:
                    logger.error(f"[WAITLIST] Error advancing expired offer for gig {row['gig_id']}: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"process_waitlist_expirations error: {e}")

def run_scheduled_emails():
    """Main scheduler function — processes all automated email types.
    Each function runs in its own try/except so one failure cannot block others."""
    print("[SCHED] run_scheduled_emails fired", flush=True)
    conn = None
    try:
        conn = _raw_db_conn()
        cursor = conn.cursor()

        smtp_config = get_smtp_settings(cursor)
        if not smtp_config['username'] or not smtp_config['password']:
            logger.warning("[SCHED] SMTP not configured — skipping all email blasts")
            return

        def _run(fn, label):
            try:
                fn()
                conn.commit()
            except Exception as _e:
                logger.error(f"[SCHED] {label} failed: {_e}", exc_info=True)

        _run(lambda: process_gig_confirmation(cursor, smtp_config),         "process_gig_confirmation")
        _run(lambda: process_open_gig_notifications(cursor, smtp_config, 'open_gig_4w'),  "open_gig_4w")
        _run(lambda: process_open_gig_notifications(cursor, smtp_config, 'open_gig_2w'),  "open_gig_2w")
        _run(lambda: process_open_gig_notifications(cursor, smtp_config, 'open_gig_1w'),  "open_gig_1w")
        _run(lambda: process_open_gig_notifications(cursor, smtp_config, 'open_gig_36h'), "open_gig_36h")
        # NOTE (May 2026): process_radius_blast was removed from the hourly loop because
        # it overlaps with process_open_gig_notifications('open_gig_36h') — both fire at
        # 36h before gig start with the same `venue_open_gig_36h` template, producing
        # duplicate emails for any preferred artist who's also in the venue's radius.
        # The cancellation case (artist cancels last-minute → blast nearby artists) is
        # handled synchronously by `fire_cancelled_gig_blast` in routes/gigs.py, which
        # has its own dedup via gig_email_log notification_keys 'cancelled_blast' and
        # 'radius_blast'. The function `process_radius_blast` is kept in this file so
        # past references (e.g. log scrapers) don't break, but it's no longer scheduled.
        _run(lambda: process_review_requests(cursor, smtp_config),          "process_review_requests")

    except Exception as e:
        logger.error(f"[SCHED] Fatal error in run_scheduled_emails: {e}", exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # Waitlist expiry is now handled directly in _scheduler_loop every 10 minutes


# WAL size threshold: checkpoint when file exceeds this (bytes)
_WAL_CHECKPOINT_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB

def _run_started_gig_waitlist_cleanup():
    """Delete waitlist + waitlist_offered entries for gigs that have started.
    Keeps the DB clean — no point holding waitlist data once a gig is underway."""
    try:
        conn = _raw_db_conn()
        deleted = conn.execute("""
            DELETE FROM gig_waitlist
            WHERE gig_id IN (
                SELECT id FROM gigs
                WHERE date < date('now', 'localtime')
                   OR (date = date('now', 'localtime')
                       AND start_time IS NOT NULL
                       AND start_time <= time('now', 'localtime'))
            )
        """).rowcount
        deleted2 = conn.execute("""
            DELETE FROM waitlist_offered
            WHERE gig_id IN (
                SELECT id FROM gigs
                WHERE date < date('now', 'localtime')
                   OR (date = date('now', 'localtime')
                       AND start_time IS NOT NULL
                       AND start_time <= time('now', 'localtime'))
            )
        """).rowcount
        conn.commit()
        conn.close()
        if deleted or deleted2:
            logger.info(f"[WAITLIST-CLEANUP] Removed {deleted} waitlist + {deleted2} offered rows for started gigs")
    except Exception as e:
        logger.warning(f"_run_started_gig_waitlist_cleanup error: {e}")


def _run_wal_checkpoint(force: bool = False):
    """Truncate the WAL file when it exceeds threshold (or when force=True).
    TRUNCATE mode: resets WAL to zero bytes after all readers finish — keeps file small.
    Runs after every scheduler pass so the WAL never grows unchecked under load.
    """
    try:
        wal_path = str(DB_PATH) + "-wal"
        import os as _os
        wal_size = _os.path.getsize(wal_path) if _os.path.exists(wal_path) else 0
        if not force and wal_size < _WAL_CHECKPOINT_THRESHOLD_BYTES:
            logger.debug(f"WAL checkpoint skipped — {wal_size/1024:.0f} KB (below {_WAL_CHECKPOINT_THRESHOLD_BYTES//1024//1024} MB threshold)")
            return
        conn = _raw_db_conn()
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.close()
        # result = (busy, log, checkpointed)
        logger.info(f"WAL checkpoint: {wal_size/1024:.0f} KB → truncated (log={result[1]}, checkpointed={result[2]})")
    except Exception as e:
        logger.warning(f"WAL checkpoint failed: {e}")


def _run_contract_hold_cleanup():
    """Release gigs stuck in pending_contract/awaiting_venue_contract past their 48h hold expiry."""
    try:
        from backend.db import SessionLocal as _SL
        _db = _SL()
        try:
            from backend.routes.contracts import cleanup_expired_holds
            from fastapi import Request as _Req
            result = cleanup_expired_holds(_db)
            # FIX (May 2026): cleanup_expired_holds() returns {"released_count": N, "released_gig_ids": [...]}
            # Previously this read result.get("released", 0) which always returned 0,
            # so the log said "released 0" even when N > 0.
            released = result.get("released_count", 0) if isinstance(result, dict) else 0
            if released:
                logger.info(f"Contract hold cleanup: released {released} expired hold(s)")
        finally:
            _db.close()
    except Exception as e:
        logger.warning(f"Contract hold cleanup error: {e}")


def _scheduler_loop():
    """Background loop — email blasts every hour, waitlist expiry every 10 minutes"""
    last_email_run = 0
    while True:
        now = time.time()

        # Waitlist expiry: check every 10 minutes so 2-hour windows advance promptly
        try:
            process_waitlist_expirations()
        except Exception as e:
            logger.error(f"Waitlist expiry check error: {e}")

        # Full email blast run: once per hour
        if now - last_email_run >= 3600:
            try:
                run_scheduled_emails()
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            _run_contract_hold_cleanup()
            _run_started_gig_waitlist_cleanup()
            _run_wal_checkpoint()
            last_email_run = time.time()

        time.sleep(600)  # check every 10 minutes


_scheduler_started = False

def start_scheduler():
    """Start the background scheduler thread.

    Should be called exactly once per process. Uses a process-level guard
    (_scheduler_started) to prevent accidental double-starts within the same
    Python process.

    Architectural note: this used to use a fcntl file lock at
    /tmp/gigsfill_scheduler.lock to coordinate across multiple uvicorn workers.
    That lock had a race condition (file truncation on `open(path, 'w')` could
    confuse the stale-lock detection, letting two workers both think they
    owned the lock — which produced duplicate emails). The fix was structural:
    the scheduler now runs only inside the dedicated `gigsfill-scheduler`
    systemd service, which is a single process, so no cross-process lock is
    needed. The API service (uvicorn workers) does NOT start the scheduler —
    that path is gated by GIGSFILL_RUN_SCHEDULERS in backend/main.py.
    """
    global _scheduler_started
    if _scheduler_started:
        logger.warning("start_scheduler called twice in the same process — ignoring")
        return

    _scheduler_started = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="EmailScheduler")
    thread.start()
    print("✅ Email notification scheduler started (runs every hour)", flush=True)
    logger.info("✅ Email notification scheduler started (runs every hour)")

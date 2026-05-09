"""
Email Dispatch Service
=======================
Centralized email sending for gig events (booking, cancellation).
Replaces the copy-pasted try/except blocks scattered across gigs.py.
"""

import logging
from sqlalchemy import text
from backend.services.notification_service import format_time_12hr

logger = logging.getLogger("gigsfill.services.email_dispatch")


def compute_slot_times(db, gig_id: int, artist_id=None) -> str:
    """Return a human-readable time string for a gig's slot(s).

    - artist_id given: returns THAT artist's slot's "start - end" if booked on
      a multi-slot gig; falls back to the gig's overall start-end otherwise.
    - artist_id None: for multi-slot, returns all booked slot times joined
      by " | " (e.g., "7:00 PM - 9:00 PM | 9:00 PM - 11:00 PM").
      For single-slot or no booked slots, returns the gig's start-end.

    Used by all dispatch paths that include the {{slot_times}} placeholder.
    """
    try:
        rows = db.execute(
            text("""SELECT gs.start_time, gs.end_time, gs.artist_id
                    FROM gig_slots gs
                    WHERE gs.gig_id = :gid AND gs.status = 'booked'
                    ORDER BY gs.slot_number ASC"""),
            {"gid": gig_id}
        ).mappings().all()
        if rows:
            if artist_id is not None:
                for r in rows:
                    if r["artist_id"] == artist_id:
                        return f"{format_time_12hr(r['start_time'])} - {format_time_12hr(r['end_time'])}"
            return " | ".join(
                f"{format_time_12hr(r['start_time'])} - {format_time_12hr(r['end_time'])}"
                for r in rows
            )
        g = db.execute(
            text("SELECT start_time, end_time FROM gigs WHERE id = :gid"),
            {"gid": gig_id}
        ).mappings().first()
        if g and g["start_time"]:
            if g["end_time"]:
                return f"{format_time_12hr(g['start_time'])} - {format_time_12hr(g['end_time'])}"
            return format_time_12hr(g["start_time"])
    except Exception as _e:
        logger.warning(f"compute_slot_times failed for gig {gig_id}: {_e}")
    return ""


def _get_effective_pay_for_slot(db, venue_id: int, artist_id: int, base_pay: float) -> float:
    """Return max(base_pay, artist pay override) for email display."""
    try:
        row = db.execute(
            text("""SELECT COALESCE(pay_dollars_override,0) + COALESCE(pay_cents_override,0)/100.0 as op
                    FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid"""),
            {"vid": venue_id, "aid": artist_id}
        ).mappings().first()
        if row and row["op"] and float(row["op"]) > base_pay:
            return float(row["op"])
    except Exception:
        pass
    return base_pay


def format_email_date(date_val) -> str:
    """Convert YYYY-MM-DD or date object to 'Friday, March 6, 2026' format."""
    try:
        from datetime import datetime as _dt
        if isinstance(date_val, str):
            d = _dt.strptime(str(date_val)[:10], "%Y-%m-%d")
        else:
            d = _dt.combine(date_val, _dt.min.time())
        return d.strftime("%A, %B %-d, %Y")
    except Exception:
        return str(date_val)


def _maps_url(address: str) -> str:
    """Return a Google Maps URL for the given address string."""
    from urllib.parse import quote
    return f"https://www.google.com/maps/search/?api=1&query={quote(address)}"


def _fetch_venue_detail_vars(db, venue_id, gig_notes=None):
    """Fetch venue details and return human-readable template variables."""
    try:
        v = db.execute(text("""
            SELECT venue_size,
                   address_line_1, address_line_2, city, state, postal_code,
                   has_stage, stage_width_ft, stage_depth_ft, setup_location_description,
                   has_sound_equipment, sound_equipment_description,
                   has_sound_engineer, sound_engineer_details,
                   has_lighting, lighting_description,
                   arrival_time_type, arrival_no_earlier_than_hour, arrival_no_earlier_than_period,
                   bar_tab_details, food_tab_details
            FROM venues WHERE id = :vid
        """), {"vid": venue_id}).mappings().first()
        if not v:
            return {}

        # Address — build multi-line string
        parts = []
        if v.get('address_line_1'): parts.append(v['address_line_1'])
        if v.get('address_line_2'): parts.append(v['address_line_2'])
        city_state_zip = ' '.join(filter(None, [v.get('city'), v.get('state'), v.get('postal_code')]))
        if city_state_zip: parts.append(city_state_zip)
        venue_address = ', '.join(parts) if parts else 'Not provided'

        # Capacity
        cap = v.get('venue_size') or ''
        venue_capacity = cap if cap else 'Not specified'

        # Arrival
        atype = (v.get('arrival_time_type') or '').lower().strip()
        if atype == 'flexible':
            arrival_info = 'Flexible'
        elif atype == 'no_earlier_than' and v.get('arrival_no_earlier_than_hour'):
            h = int(v['arrival_no_earlier_than_hour'])
            period = (v.get('arrival_no_earlier_than_period') or 'PM').upper()
            arrival_info = f'No earlier than {h}:00 {period}'
        elif atype == 'no_earlier_than':
            arrival_info = 'No earlier than — time not specified'
        else:
            arrival_info = 'Flexible'

        # Stage
        if v.get('has_stage'):
            w, d = v.get('stage_width_ft'), v.get('stage_depth_ft')
            stage_info = f'Yes — {w}ft x {d}ft' if w and d else ('Yes — ' + v.get('setup_location_description', '')) if v.get('setup_location_description') else 'Yes'
        else:
            desc = v.get('setup_location_description') or ''
            stage_info = f'No — {desc}' if desc else 'No'

        # Sound equipment
        if v.get('has_sound_equipment'):
            desc = v.get('sound_equipment_description') or ''
            sound_info = f'Provided — {desc}' if desc else 'Provided'
        else:
            sound_info = 'No — bring your own'

        # Sound engineer
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

        return {
            'venue_address':      venue_address,
            'venue_address_link': f'<a href="{_maps_url(venue_address)}" target="_blank" style="color: #8b5cf6; text-decoration: none;">{venue_address}</a>' if venue_address and venue_address != 'Not provided' else venue_address,
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


def send_booking_emails(db, gig_id_or_details, slot_id: int = None):
    """
    Send booking confirmation emails for a specific slot booking.
    If slot_id is provided, only emails for that slot (not all booked slots).
    Accepts either a gig_id (int) or a dict with 'id'. Always queries DB fresh.
    """
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users

        gig_id = int(gig_id_or_details) if not isinstance(gig_id_or_details, dict) else (gig_id_or_details.get('id') or gig_id_or_details.get('gig_id'))
        if not gig_id:
            logger.error("[BOOKING EMAIL] No gig_id")
            return

        # Query gig base info
        gig = db.execute(text("""
            SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.title, g.notes,
                   g.venue_id, g.artist_id, g.artist_type, g.band_formats, g.styles,
                   v.venue_name
            FROM gigs g
            JOIN venues v ON g.venue_id = v.id
            WHERE g.id = :gid
        """), {"gid": gig_id}).mappings().first()

        if not gig:
            logger.error(f"[BOOKING EMAIL] Gig {gig_id} not found")
            return

        # Find all booked slots to determine who to email
        _slot_filter = "AND gs.id = :sid" if slot_id else ""
        _slot_params = {"gid": gig_id, "sid": slot_id} if slot_id else {"gid": gig_id}
        booked_slots = db.execute(text(f"""
            SELECT gs.id, gs.artist_id, gs.start_time, gs.end_time, gs.pay,
                   gs.artist_type, gs.band_formats, gs.styles,
                   a.name as artist_name
            FROM gig_slots gs
            JOIN artists a ON a.id = gs.artist_id
            WHERE gs.gig_id = :gid AND gs.status IN ('booked', 'pending_contract')
            {_slot_filter}
        """), _slot_params).mappings().all()

        if not booked_slots:
            # Fallback: try gig.artist_id
            if gig["artist_id"]:
                fallback_artist = db.execute(text("SELECT id, name FROM artists WHERE id = :aid"), {"aid": gig["artist_id"]}).mappings().first()
                if fallback_artist:
                    booked_slots = [{"artist_id": gig["artist_id"], "artist_name": fallback_artist["name"],
                                     "start_time": gig["start_time"], "end_time": gig["end_time"],
                                     "pay": gig["pay"], "artist_type": gig["artist_type"],
                                     "band_formats": gig["band_formats"], "styles": gig["styles"]}]
            if not booked_slots:
                logger.error(f"[BOOKING EMAIL] No booked slots for gig {gig_id}")
                return

        email_service = EmailService(db)
        venue_vars = _fetch_venue_detail_vars(db, gig["venue_id"], gig_notes=gig.get("notes", ""))

        # Send one email per booked artist using THEIR specific slot's details.
        # The venue also gets one email per booking event — using that artist's slot data
        # so the venue sees exactly which slot was just filled.
        venue_users = get_all_entity_users(db, 'venue', gig["venue_id"])

        for slot in booked_slots:
            aid = slot["artist_id"]
            email_vars = {
                'artist_name':  slot.get("artist_name") or "",
                'venue_name':   gig["venue_name"],
                'artist_id':    str(aid),
                'venue_id':     str(gig["venue_id"]),
                'gig_id':       str(gig_id),
                'date':         format_email_date(gig["date"]),
                'start_time':   format_time_12hr(slot["start_time"]),
                'end_time':     format_time_12hr(slot["end_time"]),
                'pay':          f"{_get_effective_pay_for_slot(db, gig['venue_id'], aid, float(slot['pay'] or gig.get('pay') or 0)):,.2f}",
                'title':        gig.get("title") or "",
                'artist_type':  slot.get("artist_type") or gig.get("artist_type") or "",
                'band_formats': ", ".join(x.strip() for x in (slot.get("band_formats") or gig.get("band_formats") or "").split(",") if x.strip()),
                'styles':       ", ".join(x.strip() for x in (slot.get("styles") or gig.get("styles") or "").split(",") if x.strip()),
                **venue_vars,
            }

            # Artist email — each booked artist gets their own slot-specific confirmation
            artist_users = get_all_entity_users(db, 'artist', aid)
            for au in artist_users:
                result = email_service.send_notification_email(
                    user_email=au["email"], user_id=au["user_id"],
                    notification_type='artist_gig_booked', variables=email_vars
                )
                logger.info(f"[BOOKING EMAIL] artist result={result} to={au['email']}")

            # Venue email — bypass preferences, venue must always know about bookings
            _booked_sent_venues = set()
            for vu in venue_users:
                if vu["email"] in _booked_sent_venues:
                    continue
                _booked_sent_venues.add(vu["email"])
                try:
                    from backend.email_service import _smtp_send as _bk_smtp
                    from email.mime.multipart import MIMEMultipart as _BM
                    from email.mime.text import MIMEText as _BT
                    from email.utils import formataddr as _bkfa
                    tpl = email_service.get_template('venue_gig_booked')
                    if tpl and email_service.enabled:
                        subj = email_service.render_template(tpl['subject'], email_vars)
                        body = email_service.render_template(tpl['body'], email_vars)
                        msg = _BM("alternative")
                        msg['Subject'] = subj
                        msg['From'] = _bkfa((email_service.from_name, email_service.from_email)) if email_service.from_name else email_service.from_email
                        msg['To'] = vu["email"]
                        msg['X-Mailer'] = 'GigsFill'
                        msg.attach(_BT(body, 'html'))
                        _bk_smtp(email_service.smtp_server, email_service.smtp_port,
                                 email_service.smtp_username, email_service.smtp_password, msg)
                        logger.info(f"[BOOKING EMAIL] venue booked sent to {vu['email']}")
                    else:
                        result = email_service.send_notification_email(
                            user_email=vu["email"], user_id=vu["user_id"],
                            notification_type='venue_gig_booked', variables=email_vars
                        )
                        logger.info(f"[BOOKING EMAIL] venue result={result} to={vu['email']}")
                except Exception as _bve:
                    logger.error(f"[BOOKING EMAIL] venue send FAILED to {vu['email']}: {_bve}", exc_info=True)

    except Exception as e:
        import traceback
        logger.error(f"[BOOKING EMAIL] ERROR: {e}\n{traceback.format_exc()}")


def send_cancellation_emails(db, gig_details: dict, cancellation_reason: str = "",
                             slot_info: str = "", skip_venue_email: bool = False,
                             cancelled_by: str = "venue"):
    """
    Send cancellation emails to ALL entity users for both artist and venue.
    Venue email includes a waitlist status message if artists are waiting.
    skip_venue_email: set True when within blast window — blast summary covers venue notification.
    cancelled_by: "venue" or "artist" — drives the subject line on the venue email
                  so the venue can immediately see who actually cancelled.
    """
    logger.info(f"[CANCEL EMAIL] send_cancellation_emails: gig={gig_details.get('id') or gig_details.get('gig_id')}, artist_id={gig_details.get('artist_id')}, venue_id={gig_details.get('venue_id')}, cancelled_by={cancelled_by}")
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users
        email_service = EmailService(db)

        reason = cancellation_reason or "No reason provided"

        # ── Build waitlist message for venue email ────────────────────────
        gig_id = gig_details.get('id') or gig_details.get('gig_id')
        waitlist_message = ""
        if gig_id:
            try:
                from sqlalchemy import text as _text
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                wl_rows = db.execute(
                    _text("""
                        SELECT a.name as artist_name, w.id, w.offer_sent, w.offer_declined,
                               w.offer_expires_at
                        FROM gig_waitlist w
                        JOIN artists a ON a.id = w.artist_id
                        WHERE w.gig_id = :gid
                          AND (w.offer_declined = 0 OR w.offer_declined IS NULL)
                        ORDER BY w.id ASC
                    """),
                    {"gid": gig_id}
                ).mappings().all()

                # Compute the offer deadline we WILL send (same logic as waitlist.py)
                # since venue email fires before notify_waitlist sets offer_expires_at
                try:
                    _gig_date = gig_details.get("date", "")
                    _gig_start = gig_details.get("start_time", "00:00")
                    _gig_dt = _dt.fromisoformat(f"{_gig_date}T{str(_gig_start)[:5]}")
                    # Use platform timezone for consistent comparison with naive gig datetimes
                    try:
                        import pytz as _ed_pytz
                        _ed_tz_str = db.execute(_text("SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'")).scalar() or "America/Los_Angeles"
                        _now_platform = _dt.now(_ed_pytz.timezone(_ed_tz_str)).replace(tzinfo=None)
                    except Exception:
                        _now_platform = _dt.utcnow()
                    _hours_until = (_gig_dt - _now_platform).total_seconds() / 3600
                    # Tiered: >1wk=24h, 36h-1wk=2h, <36h=30min
                    if _hours_until < 36:
                        _offer_hours = 0.5
                    elif _hours_until <= 168:
                        _offer_hours = 2
                    else:
                        _offer_hours = 24
                except Exception:
                    _offer_hours = 24
                _computed_expires = _dt.now(_tz.utc) + _td(hours=_offer_hours)

                if wl_rows:
                    names = [r["artist_name"] for r in wl_rows]
                    first = names[0]
                    # Get radius for blast fallback message
                    blast_settings = db.execute(
                        _text("""SELECT COALESCE(ven_r.radius_miles, 20) as radius,
                                        COALESCE(ven_c.blast_all_enabled, 0) as cancelled_blast_all,
                                        COALESCE(ven_c.blast_all_radius, 20) as cancelled_blast_radius
                                 FROM gigs g
                                 LEFT JOIN venue_email_notifications ven_r
                                       ON ven_r.venue_id = g.venue_id AND ven_r.notification_key = 'radius_blast'
                                 LEFT JOIN venue_email_notifications ven_c
                                       ON ven_c.venue_id = g.venue_id AND ven_c.notification_key = 'cancelled_blast'
                                 WHERE g.id = :gid"""),
                        {"gid": gig_id}
                    ).mappings().first()
                    blast_all = blast_settings and bool(blast_settings["cancelled_blast_all"])
                    blast_radius = int((blast_settings["cancelled_blast_radius"] if blast_settings else None) or 20)
                    blast_suffix = (
                        (f" and all artists within {blast_radius} miles" if blast_all else "")
                        + " depending on your Email Center settings."
                    )

                    if len(names) == 1:
                        # Get actual deadline time for first waitlisted artist
                        _deadline_str = ""
                        try:
                            from zoneinfo import ZoneInfo as _ZI
                            from sqlalchemy import text as _tz_tx
                            _tz_name = db.execute(_tz_tx(
                                "SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'"
                            )).scalar() or "America/Los_Angeles"
                            _local_exp = _computed_expires.astimezone(_ZI(_tz_name))
                            _deadline_str = _local_exp.strftime("%-I:%M %p")
                        except Exception:
                            pass
                        _deadline_phrase = (
                            f"<strong>{first}</strong> has until <strong>{_deadline_str}</strong> to book it!"
                            if _deadline_str else
                            f"<strong>{first}</strong> has 24 hours to respond"
                        )
                        waitlist_message = (
                            f"The gig is now open again. There is 1 waitlisted artist "
                            f"(<strong>{first}</strong>), so we will email them first. "
                            f"{_deadline_phrase}. "
                            f"If they cannot perform, an email blast will be sent to your Preferred Artists"
                            + blast_suffix
                        )
                    else:
                        rest = names[1:]
                        rest_str = ", ".join(f"<strong>{n}</strong>" for n in rest)
                        # Get deadline for first artist
                        _deadline_str2 = _deadline_str  # reuse same computed deadline
                        _deadline_phrase2 = (
                            f"they have until <strong>{_deadline_str2}</strong> to book it!"
                            if _deadline_str2 else "they have 24 hours to respond"
                        )
                        waitlist_message = (
                            f"The gig is now open again. There are {len(names)} waitlisted artists, "
                            f"so we will email them in order to try and fill this gig. "
                            f"An email was sent to <strong>{first}</strong> (#1 on the waitlist) — "
                            f"{_deadline_phrase2}. If they cannot perform, we will automatically "
                            f"contact {rest_str}. "
                            f"If nobody on the waitlist can fill this gig, an email blast will be sent to your Preferred Artists"
                            + blast_suffix
                        )
            except Exception as _wl_err:
                logger.warning(f"Could not build waitlist message: {_wl_err}")

        if not waitlist_message:
            # No waitlist — generic message
            waitlist_message = (
                "The gig is now open again. An email blast will be sent to your Preferred Artists "
                "and/or artists within your configured radius depending on your Email Center settings."
            )

        cancel_vars = {
            'user_name': gig_details.get('artist_name', 'Artist'),
            'venue_name': gig_details.get('venue_name', ''),
            'artist_name': gig_details.get('artist_name', ''),
            'artist_id': str(gig_details.get('artist_id', '')),
            'venue_id': str(gig_details.get('venue_id', '')),
            'gig_id': str(gig_id or ''),
            'date': format_email_date(gig_details.get('date', '')),
            # FIX (May 2026): include time fields so cancellation emails can show
            # the slot/gig time. format_time_12hr returns '' for empty input.
            'start_time': format_time_12hr(gig_details.get('start_time', '')),
            'end_time':   format_time_12hr(gig_details.get('end_time', '')),
            # {{slot_times}} placeholder: prefer the supplied start/end (e.g. for
            # a single-slot cancel, dispatch passes that slot's times). Otherwise
            # fall back to compute_slot_times() which inspects gig_slots.
            'slot_times': (
                f"{format_time_12hr(gig_details.get('start_time'))} - {format_time_12hr(gig_details.get('end_time'))}"
                if gig_details.get('start_time') and gig_details.get('end_time')
                else compute_slot_times(db, gig_id)
            ),
            'cancellation_reason': reason,
            'waitlist_message': waitlist_message,
        }

        # Send to ALL artist + venue users in ONE SMTP session
        import smtplib as _smtplib
        _smtp = None
        try:
            if email_service.enabled:
                if email_service.smtp_port == 465:
                    _smtp = _smtplib.SMTP_SSL(email_service.smtp_server, email_service.smtp_port, timeout=15)
                else:
                    _smtp = _smtplib.SMTP(email_service.smtp_server, email_service.smtp_port, timeout=15)
                    _smtp.starttls()
                _smtp.login(email_service.smtp_username, email_service.smtp_password)
        except Exception as _e:
            logger.warning(f"SMTP open failed for cancellation emails: {_e}")
            _smtp = None

        # Artist email — bypass preferences, cancellations are always critical
        _cancel_artist_id = gig_details.get('artist_id')
        def _cancel_send(to_email, notification_type, subject_override=None):
            """Send cancellation email bypassing preference check.

            subject_override: if set, use this string instead of the template subject.
            (Used by venue path when cancelled_by='venue' so the subject reflects who
            actually cancelled — see Issue 1 fix May 2026.)
            """
            from backend.email_service import _smtp_send as _do_send
            from email.mime.multipart import MIMEMultipart as _MM
            from email.mime.text import MIMEText as _MT
            from email.utils import formataddr
            logger.info(f"[CANCEL EMAIL] _cancel_send: to={to_email} type={notification_type} smtp_enabled={email_service.enabled} smtp_server={email_service.smtp_server} smtp_user={email_service.smtp_username!r}")
            if not email_service.enabled:
                logger.error(f"[CANCEL EMAIL] EmailService not enabled — smtp_username={email_service.smtp_username!r} smtp_password_set={bool(email_service.smtp_password)}")
                return False
            tpl = email_service.get_template(notification_type)
            if not tpl:
                logger.error(f"[CANCEL EMAIL] No template '{notification_type}' — cannot send to {to_email}")
                return False
            if subject_override is not None:
                subj = email_service.render_template(subject_override, cancel_vars)
            else:
                subj = email_service.render_template(tpl['subject'], cancel_vars)
            body = email_service.render_template(tpl['body'], cancel_vars)
            msg = _MM("alternative")  # "alternative" prevents Outlook paperclip
            msg['Subject'] = subj
            msg['From'] = formataddr((email_service.from_name, email_service.from_email)) if email_service.from_name else email_service.from_email
            msg['To'] = to_email
            msg['X-Mailer'] = 'GigsFill'
            msg.attach(_MT(body, 'html'))
            _do_send(email_service.smtp_server, email_service.smtp_port,
                     email_service.smtp_username, email_service.smtp_password, msg)
            logger.info(f"[CANCEL EMAIL] _cancel_send SUCCESS to {to_email}")
            return True

        if _cancel_artist_id:
            try:
                artist_users = get_all_entity_users(db, 'artist', _cancel_artist_id)
                for au in artist_users:
                    try:
                        _cancel_send(au["email"], 'artist_gig_cancelled')
                        logger.info(f"[CANCEL EMAIL] artist cancel sent to {au['email']}")
                    except Exception as _ae:
                        logger.error(f"[CANCEL EMAIL] artist cancel FAILED to {au['email']}: {_ae}")
            except Exception as _ae2:
                logger.error(f"[CANCEL EMAIL] artist email error: {_ae2}")
        else:
            logger.warning(f"[CANCEL EMAIL] No artist_id — skipping artist cancel email for gig {gig_id}")

        # Venue email — bypass preferences, always fires independently
        _cancel_venue_id = gig_details.get('venue_id')
        if not _cancel_venue_id:
            logger.error(f"[CANCEL EMAIL] No venue_id — cannot send venue cancel email for gig {gig_id}")
        elif not skip_venue_email:
            try:
                venue_users = get_all_entity_users(db, 'venue', _cancel_venue_id)
                logger.info(f"[CANCEL EMAIL] sending to {len(venue_users)} venue user(s) for gig {gig_id}")
                _sent_venue_emails = set()
                # Build venue email subject based on who cancelled (May 2026 fix).
                # Default template subject hardcodes "{{artist_name}} cancelled their gig"
                # which is wrong when the venue is the canceller.
                if cancelled_by == "venue":
                    _venue_subject = "You cancelled your gig on {{date}}"
                else:
                    _venue_subject = None  # use template default ("{{artist_name}} cancelled their gig on {{date}}")
                for vu in venue_users:
                    if vu["email"] in _sent_venue_emails:
                        continue
                    _sent_venue_emails.add(vu["email"])
                    try:
                        _cancel_send(vu["email"], 'venue_gig_cancelled', subject_override=_venue_subject)
                        logger.info(f"[CANCEL EMAIL] venue cancel sent to {vu['email']} (subject_override={_venue_subject!r})")
                    except Exception as _ve:
                        logger.error(f"[CANCEL EMAIL] venue cancel FAILED to {vu['email']}: {_ve}", exc_info=True)
            except Exception as _ve2:
                logger.error(f"[CANCEL EMAIL] venue email error: {_ve2}", exc_info=True)
        else:
            logger.info(f"[CANCEL EMAIL] skipping venue generic cancel email — blast summary will cover it")

    except Exception as e:
        logger.error(f"[CANCEL EMAIL] send_cancellation_emails outer error: {e}", exc_info=True)


def send_contract_sign_email(db, venue_id: int, artist_id: int, gig_id: int, gig_date: str):
    """
    Send email to ALL venue users when an artist signs a contract,
    prompting them to countersign.
    Idempotent: only sends once per gig — duplicate calls are silently ignored.
    """
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users
        from sqlalchemy import text as _cse_text

        # Idempotency guard: once per gig+artist combination
        # This prevents double-send if button double-clicked, but allows re-send after cancel+rebook
        try:
            _ig_key = f"contract_sign_needed_{artist_id}"
            already_sent = db.execute(
                _cse_text("SELECT 1 FROM gig_email_log WHERE gig_id = :gid AND notification_key = :key LIMIT 1"),
                {"gid": gig_id, "key": _ig_key}
            ).first()
            if already_sent:
                logger.info(f"Contract sign email already sent for gig {gig_id} artist {artist_id} — skipping duplicate")
                return
            db.execute(
                _cse_text("INSERT OR IGNORE INTO gig_email_log (gig_id, venue_id, notification_key, recipient_count) VALUES (:gid, :vid, :key, 1)"),
                {"gid": gig_id, "vid": venue_id, "key": _ig_key}
            )
            db.commit()
        except Exception as _ig_err:
            logger.warning(f"Contract sign idempotency check failed: {_ig_err}")
        from sqlalchemy import text

        email_service = EmailService(db)
        if not email_service.enabled:
            logger.warning(f"Contract sign email skipped — SMTP not configured (gig {gig_id})")
            return

        venue = db.execute(
            text("SELECT venue_name FROM venues WHERE id = :vid"),
            {"vid": venue_id}
        ).mappings().first()
        artist = db.execute(
            text("SELECT name FROM artists WHERE id = :aid"),
            {"aid": artist_id}
        ).mappings().first()

        if not venue or not artist:
            logger.warning(f"Contract sign email: venue or artist not found (venue={venue_id}, artist={artist_id})")
            return

        date_display = format_email_date(gig_date)
        venue_name = venue['venue_name']
        artist_name = artist['name']

        email_vars = {
            'artist_name': artist_name,
            'venue_name': venue_name,
            'venue_id': str(venue_id),
            'date': date_display,
            # The artist's specific slot's time on multi-slot gigs;
            # falls back to gig overall start-end on single-slot.
            'slot_times': compute_slot_times(db, gig_id, artist_id=artist_id),
        }

        venue_users = get_all_entity_users(db, 'venue', venue_id)
        logger.info(f"Contract sign email: {len(venue_users)} venue user(s) for gig {gig_id}")

        _sent_emails = set()
        for vu in venue_users:
            if vu["email"] in _sent_emails:
                continue
            _sent_emails.add(vu["email"])
            try:
                from backend.email_service import _smtp_send as _cs_smtp2
                from email.mime.multipart import MIMEMultipart as _CSM
                from email.mime.text import MIMEText as _CST
                from email.utils import formataddr as _csfa
                tpl = email_service.get_template('venue_contract_sign_needed')
                if tpl and email_service.enabled:
                    subj = email_service.render_template(tpl['subject'], email_vars)
                    body = email_service.render_template(tpl['body'], email_vars)
                    msg = _CSM("alternative")
                    msg['Subject'] = subj
                    msg['From'] = _csfa((email_service.from_name, email_service.from_email)) if email_service.from_name else email_service.from_email
                    msg['To'] = vu["email"]
                    msg['X-Mailer'] = 'GigsFill'
                    msg.attach(_CST(body, 'html'))
                    _cs_smtp2(email_service.smtp_server, email_service.smtp_port,
                              email_service.smtp_username, email_service.smtp_password, msg)
                    logger.info(f"Contract sign email sent to {vu['email']} for gig {gig_id}")
                else:
                    result = email_service.send_notification_email(
                        user_email=vu["email"], user_id=vu["user_id"],
                        notification_type='venue_contract_sign_needed', variables=email_vars)
                    logger.info(f"Contract sign email to {vu['email']}: result={result}")
            except Exception as _cse:
                logger.error(f"Contract sign email FAILED to {vu['email']}: {_cse}", exc_info=True)

    except Exception as e:
        logger.error(f"Contract sign email error: {e}")


def send_gig_edited_emails(db, gig_id: int):
    """Send gig-edited notification to all booked artists (single-slot and multi-slot)."""
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users
        from backend.services.email_dispatch import _fetch_venue_detail_vars

        email_service = EmailService(db)

        gig = db.execute(text("""
            SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.title, g.notes,
                   g.venue_id, g.artist_id,
                   g.artist_type, g.band_formats, g.styles,
                   v.venue_name
            FROM gigs g
            JOIN venues v ON g.venue_id = v.id
            WHERE g.id = :gid
        """), {"gid": gig_id}).mappings().first()
        if not gig:
            return

        date_display = format_email_date(gig["date"])
        venue_vars = _fetch_venue_detail_vars(db, gig["venue_id"], gig_notes=gig.get("notes", ""))

        base_vars = {
            "venue_name":   gig["venue_name"],
            "date":         date_display,
            "start_time":   format_time_12hr(gig["start_time"]),
            "end_time":     format_time_12hr(gig["end_time"]),
            "pay":          f"{float(gig['pay'] or 0):,.2f}",  # overridden per-artist below
            "title":        gig.get("title") or "",
            "notes":        gig.get("notes") or "",
            "artist_type":  gig.get("artist_type") or "",
            "band_formats": ", ".join(x.strip() for x in (gig.get("band_formats") or "").split(",") if x.strip()),
            "styles":       ", ".join(x.strip() for x in (gig.get("styles") or "").split(",") if x.strip()),
            "gig_id":       str(gig_id),
            **venue_vars,
        }

        # Collect all booked artists from slots
        artist_ids = set()
        slots_all = db.execute(text("""
            SELECT DISTINCT artist_id FROM gig_slots
            WHERE gig_id = :gid AND status = 'booked' AND artist_id IS NOT NULL
        """), {"gid": gig_id}).fetchall()
        for s in slots_all:
            artist_ids.add(s[0])
        # Also include gig.artist_id as fallback
        if gig["artist_id"] and not artist_ids:
            artist_ids.add(gig["artist_id"])

        for aid in artist_ids:
            artist = db.execute(text(
                "SELECT id, name, user_id FROM artists WHERE id = :aid"
            ), {"aid": aid}).mappings().first()
            if not artist:
                continue

            # Override base_vars with this artist's slot data
            slot_vars = {}
            slot = db.execute(text("""
                SELECT start_time, end_time, pay, artist_type, band_formats, styles
                FROM gig_slots
                WHERE gig_id = :gid AND artist_id = :aid AND status = 'booked'
                LIMIT 1
            """), {"gid": gig_id, "aid": aid}).mappings().first()
            if slot:
                _slot_base_pay = float(slot['pay'] or gig.get('pay') or 0)
                _slot_eff_pay  = _get_effective_pay_for_slot(db, gig["venue_id"], aid, _slot_base_pay)
                slot_vars = {
                    "start_time":   format_time_12hr(slot["start_time"]),
                    "end_time":     format_time_12hr(slot["end_time"]),
                    "pay":          f"{_slot_eff_pay:,.2f}",
                    "artist_type":  slot.get("artist_type") or base_vars["artist_type"],
                    "band_formats": ", ".join(x.strip() for x in (slot.get("band_formats") or base_vars["band_formats"]).split(",") if x.strip()),
                    "styles":       ", ".join(x.strip() for x in (slot.get("styles") or base_vars["styles"]).split(",") if x.strip()),
                }

            email_vars = {
                **base_vars,
                **slot_vars,
                "artist_name": artist["name"],
                "artist_id":   str(aid),
            }

            # Apply per-artist pay override if no slot override already set it
            if not slot_vars:
                _base_pay = float(gig.get('pay') or 0)
                _eff_pay  = _get_effective_pay_for_slot(db, gig["venue_id"], aid, _base_pay)
                email_vars["pay"] = f"{_eff_pay:,.2f}"

            # FIX (May 2026): build slots_html so the {{slots_html}} placeholder
            # in the artist_gig_edited template gets substituted. Without this,
            # the artist's update email displayed literal "{{slots_html}}" text
            # where Time/Pay rows should appear.
            _row = '<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">{label}</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{value}</td></tr>'
            email_vars["slots_html"] = (
                _row.format(label="Time",
                            value=f"{email_vars.get('start_time','')} – {email_vars.get('end_time','')}")
                + _row.format(label="Pay", value=f"${email_vars.get('pay','0.00')}")
            )

            users = get_all_entity_users(db, "artist", aid)
            for u in users:
                email_service.send_notification_email(
                    user_email=u["email"],
                    user_id=u["user_id"],
                    notification_type="artist_gig_edited",
                    variables=email_vars,
                )

        logger.info(f"[GIG_EDITED] Emails sent for gig {gig_id} to {len(artist_ids)} artist(s)")

    except Exception as e:
        logger.error(f"[GIG_EDITED] Email send error: {e}", exc_info=True)


def send_approval_request_emails(db, gig_details: dict, artist_id: int, slot_info: str = ""):
    """
    Send same-day booking approval request to ALL venue users,
    and a 'pending' notification to ALL artist users.
    gig_details must include: id, venue_id, artist_id, artist_name, venue_name,
                               date, start_time, end_time, pay, title,
                               venue_user_id (for token lookup)
    """
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users
        import secrets

        email_service = EmailService(db)
        gig_id = gig_details.get('id') or gig_details.get('gig_id')
        venue_id = gig_details.get('venue_id')
        # Use effective pay — respects artist pay override (take max of listed vs override)
        base_pay = float(gig_details.get('pay') or 0)
        effective_pay = _get_effective_pay_for_slot(db, venue_id, artist_id, base_pay)
        pay_display = f"{effective_pay:,.2f}"

        # Generate a one-time approval token stored on the gig
        from sqlalchemy import text as _text
        approval_token = secrets.token_urlsafe(32)
        db.execute(_text("UPDATE gigs SET approval_token = :tok WHERE id = :gid"),
                   {"tok": approval_token, "gid": gig_id})
        db.flush()

        base_url = "https://gigsfill.com"
        approve_url = f"{base_url}/api/gigs/{gig_id}/approve-booking?token={approval_token}&artist_id={artist_id}"
        deny_url    = f"{base_url}/api/gigs/{gig_id}/deny-booking?token={approval_token}&artist_id={artist_id}"

        slot_vars = {"slot_info": slot_info} if slot_info else {}

        email_vars = {
            'artist_name': gig_details.get('artist_name', ''),
            'venue_name':  gig_details.get('venue_name', ''),
            'artist_id':   str(artist_id),
            'venue_id':    str(venue_id),
            'gig_id':      str(gig_id),
            'date':        format_email_date(gig_details.get('date', '')),
            'start_time':  format_time_12hr(gig_details.get('start_time')),
            'end_time':    format_time_12hr(gig_details.get('end_time')),
            'pay':         pay_display,
            'approve_url': approve_url,
            'deny_url':    deny_url,
            **slot_vars,
        }

        # Venue users — approval request
        venue_users = get_all_entity_users(db, 'venue', venue_id)
        logger.info(f"[APPROVAL_EMAIL] venue_id={venue_id} artist_id={artist_id} gig_id={gig_id} venue_users={[u['email'] for u in venue_users]} smtp_enabled={email_service.enabled}")
        for vu in venue_users:
            result = email_service.send_notification_email(
                user_email=vu["email"],
                user_id=vu["user_id"],
                notification_type='venue_booking_approval_request',
                variables=email_vars,
            )
            logger.info(f"[APPROVAL_EMAIL] venue email to {vu['email']}: sent={result}")

        # Artist users — pending notification
        artist_email_vars = {k: v for k, v in email_vars.items() if k not in ('approve_url', 'deny_url')}
        artist_users = get_all_entity_users(db, 'artist', artist_id)
        logger.info(f"[APPROVAL_EMAIL] artist_users={[u['email'] for u in artist_users]}")
        for au in artist_users:
            result = email_service.send_notification_email(
                user_email=au["email"],
                user_id=au["user_id"],
                notification_type='artist_booking_pending_approval',
                variables=artist_email_vars,
            )
            logger.info(f"[APPROVAL_EMAIL] artist email to {au['email']}: sent={result}")

    except Exception as e:
        import traceback
        logger.error(f"[APPROVAL_REQUEST_EMAIL] ERROR: {e}\n{traceback.format_exc()}")


def send_approval_decision_emails(db, gig_details: dict, artist_id: int,
                                  approved: bool, slot_info: str = ""):
    """Send approved/denied email to ALL artist users."""
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users

        email_service = EmailService(db)
        # Use effective pay — respects artist pay override
        base_pay = float(gig_details.get('pay') or 0)
        venue_id = gig_details.get('venue_id')
        effective_pay = _get_effective_pay_for_slot(db, venue_id, artist_id, base_pay)
        pay_display = f"{effective_pay:,.2f}"
        notification_type = 'artist_booking_approved' if approved else 'artist_booking_denied'

        slot_vars = {"slot_info": slot_info} if slot_info else {}

        email_vars = {
            'artist_name': gig_details.get('artist_name', ''),
            'venue_name':  gig_details.get('venue_name', ''),
            'date':        format_email_date(gig_details.get('date', '')),
            'start_time':  format_time_12hr(gig_details.get('start_time')),
            'end_time':    format_time_12hr(gig_details.get('end_time')),
            'pay':         pay_display,
            **slot_vars,
        }

        artist_users = get_all_entity_users(db, 'artist', artist_id)
        for au in artist_users:
            email_service.send_notification_email(
                user_email=au["email"],
                user_id=au["user_id"],
                notification_type=notification_type,
                variables=email_vars,
            )

    except Exception as e:
        import traceback
        logger.error(f"[APPROVAL_DECISION_EMAIL] ERROR: {e}\n{traceback.format_exc()}")

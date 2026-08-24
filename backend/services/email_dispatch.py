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


def _venue_on_free_trial(db, venue_id) -> bool:
    """Check if a venue is currently on Free Trial (payments suspended).
    Used to inject `is_free_trial` into email template variables so the
    booking / cancellation / edit / approval templates can render a
    "This is a Free Trial venue — no card charge, arrange payment
    directly" note. Silent-false on any error (defensive: emails should
    never fail because the trial-check couldn't run).
    """
    if not venue_id:
        return False
    try:
        row = db.execute(
            text("SELECT payments_suspended FROM venue_payment_overrides WHERE venue_id = :vid"),
            {"vid": venue_id}
        ).mappings().first()
        return bool(row and row.get("payments_suspended"))
    except Exception:
        return False


def format_slot_pay_summary(slot_or_gig, fallback_pay=None):
    """Render a pay description for use INSIDE an email template that
    already prepends "$" before the {pay} placeholder (the convention
    in backend/email_templates.py — "${{pay}}" → "${pay}").

    For flat-pay slots: "60.00".
    For door-split slots (deal_type='door'): "50.00 guarantee + 20% of door".

    For surfaces OUTSIDE of email-template substitution (JSON API responses,
    contracts, direct frontend display) use `format_pay_summary_with_sign`
    instead — it adds the leading "$".

    `slot_or_gig` is a dict-like (sqlalchemy mappings row or plain dict).
    `fallback_pay` is used when the slot has no pay column. All values
    are coerced safely so a missing column doesn't blow up the email send.
    """
    def _get(k, default=None):
        if slot_or_gig is None:
            return default
        try:
            v = slot_or_gig.get(k) if hasattr(slot_or_gig, 'get') else slot_or_gig[k]
        except (KeyError, IndexError, TypeError):
            return default
        return v if v is not None else default

    deal_type = (_get('deal_type', 'flat') or 'flat').lower()
    pay_raw = _get('pay')
    if pay_raw is None:
        pay_raw = fallback_pay
    try:
        pay_f = float(pay_raw or 0)
    except (TypeError, ValueError):
        pay_f = 0.0

    # IMPORTANT: do NOT include the leading "$" — every gig-related email
    # template already prepends a literal "$" before the {pay} placeholder
    # (see email_templates.py — "${{pay}}" is the convention). Adding "$"
    # here would produce "$$60.00" in the rendered email.
    if deal_type == 'door':
        gua_cents = int(_get('guarantee_cents', 0) or 0)
        pct = int(_get('door_pct', 0) or 0)
        gua_dollars = gua_cents / 100.0
        # If neither guarantee nor pct is set, fall back to flat formatting
        # so emails don't say "$0 guarantee + 0% of door" — that's worse
        # than just hiding the deal terms.
        if gua_cents > 0 or pct > 0:
            # Returns e.g. "50.00 guarantee + 20% of door" → template adds
            # the "$": "$50.00 guarantee + 20% of door"
            return f"{gua_dollars:,.2f} guarantee + {pct}% of door"
    return f"{pay_f:,.2f}"


def format_pay_summary_with_sign(slot_or_gig, fallback_pay=None) -> str:
    """Same as `format_slot_pay_summary` but INCLUDES the leading "$".
    Use for JSON API responses (e.g. pay_summary field), contract bodies,
    SMS strings, anywhere outside of "${{pay}}" template substitution.

    Returns "$60.00" for flat or "$50.00 guarantee + 20% of door" for door deals.
    """
    return "$" + format_slot_pay_summary(slot_or_gig, fallback_pay)


def slot_has_door_terms(slot_or_gig) -> bool:
    """True if the slot/gig dict carries usable door-deal terms.
    Mirrors the logic in `format_slot_pay_summary` so the two stay in sync.
    """
    if slot_or_gig is None:
        return False
    try:
        deal_type = (slot_or_gig.get('deal_type') if hasattr(slot_or_gig, 'get') else slot_or_gig['deal_type']) or 'flat'
    except (KeyError, IndexError, TypeError):
        return False
    if str(deal_type).lower() != 'door':
        return False
    try:
        gua = int(slot_or_gig.get('guarantee_cents') or 0)
        pct = int(slot_or_gig.get('door_pct') or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return gua > 0 or pct > 0


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
    """Return max(base_pay, artist pay override) for email display.

    Override only applies when status='approved' — a pending/denied/revoked row still
    carries the override columns from before, but should not affect emails.
    """
    try:
        row = db.execute(
            text("""SELECT COALESCE(pay_dollars_override,0) + COALESCE(pay_cents_override,0)/100.0 as op
                    FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid AND status='approved'"""),
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

        # Stage — 2026-08-06 rewrite: show dimensions AND description
        # together when both are set (previously dropped the description
        # when dimensions were present). Also stopped surfacing a stale
        # setup description on the No branch — if there's no stage, the
        # setup notes are irrelevant.
        if v.get('has_stage'):
            _parts = ['Yes']
            _w, _d = v.get('stage_width_ft'), v.get('stage_depth_ft')
            if _w and _d:
                _parts.append(f'{_w}ft x {_d}ft')
            _desc = (v.get('setup_location_description') or '').strip()
            if _desc:
                _parts.append(_desc)
            stage_info = ' — '.join(_parts) if len(_parts) > 1 else 'Yes'
        else:
            stage_info = 'No'

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

        # Jul 2026 full-site audit (E-H1): venue_address_link went into
        # emails as raw HTML, so a `"` in the address (or malicious
        # markup embedded via venue edit) escaped the href and injected
        # scripts. HTML-escape the visible text AND the href attr.
        import html as _html
        _addr_visible = _html.escape(venue_address) if venue_address else ''
        _addr_href = _html.escape(_maps_url(venue_address), quote=True) if venue_address else ''
        _addr_link = (
            f'<a href="{_addr_href}" target="_blank" style="color: #8b5cf6; text-decoration: none;">{_addr_visible}</a>'
            if venue_address and venue_address != 'Not provided'
            else _addr_visible
        )
        return {
            'venue_address':      venue_address,
            'venue_address_link': _addr_link,
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


def send_booking_emails(db, gig_id_or_details, slot_id: int = None, skip_artist: bool = False):
    """
    Send booking confirmation emails for a specific slot booking.
    If slot_id is provided, only emails for that slot (not all booked slots).
    Accepts either a gig_id (int) or a dict with 'id'. Always queries DB fresh.

    skip_artist=True (2026-08-24): only send to venue-side, skip artist.
    Used by approve_booking so the artist doesn't get two emails (the
    enriched artist_booking_approved template already carries all the
    details the standard booked email would provide).
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
                   gs.deal_type, gs.door_pct, gs.guarantee_cents,
                   a.name as artist_name
            FROM gig_slots gs
            JOIN artists a ON a.id = gs.artist_id
            -- Audit fix (May 2026 part 6): include awaiting_venue_contract + pending_venue_approval
            WHERE gs.gig_id = :gid AND gs.status IN ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval')
            {_slot_filter}
        """), _slot_params).mappings().all()

        if not booked_slots:
            # Jul 2026: legacy single-slot gig.artist_id fallback removed.
            # Backfill in db.setup_database ensures every gig has ≥1
            # gig_slots row, so if no booked_slots came back from the
            # slot query, there really is no booking to email about.
            # (The old fallback synthesized a slot from gig-level fields
            # for pre-multi-slot data; that shape no longer exists.)
            logger.error(f"[BOOKING EMAIL] No booked slots for gig {gig_id}")
            return

        email_service = EmailService(db)
        venue_vars = _fetch_venue_detail_vars(db, gig["venue_id"], gig_notes=gig.get("notes", ""))
        # 2026-08-21: pass Free Trial status through so booking-confirmation
        # templates can render a "no card charge — arrange payment directly"
        # note. Empty string when NOT on trial so {{#is_free_trial}} blocks
        # evaluate to falsy per the Mustache render at email_service.py:229.
        _is_free_trial = '1' if _venue_on_free_trial(db, gig["venue_id"]) else ''

        # Far-away-booking detection (May 2026 part 10h). The blast email
        # radius (20mi default) only limits who gets NOTIFIED — any artist on
        # the platform can book during an open-blast window. So a touring band
        # from out of state can legitimately grab an opening. We don't block
        # that, but we (a) flag it to the venue so they're not surprised, and
        # (b) drop a soft notice on the artist's confirmation so they're sure
        # the venue is far. Threshold is admin-configurable.
        try:
            _far_miles = float(db.execute(text(
                "SELECT COALESCE(setting_value, '50') FROM platform_settings WHERE setting_key='far_booking_alert_miles'"
            )).scalar() or 50)
        except Exception:
            _far_miles = 50.0
        _venue_geo = db.execute(text(
            "SELECT latitude, longitude, city, state FROM venues WHERE id = :vid"
        ), {"vid": gig["venue_id"]}).mappings().first()

        def _miles_between(lat1, lon1, lat2, lon2):
            import math
            if None in (lat1, lon1, lat2, lon2):
                return None
            R = 3959.0  # Earth radius miles
            p1, p2 = math.radians(lat1), math.radians(lat2)
            dphi = math.radians(lat2 - lat1)
            dlmb = math.radians(lon2 - lon1)
            a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        # Send one email per booked artist using THEIR specific slot's details.
        # The venue also gets one email per booking event — using that artist's slot data
        # so the venue sees exactly which slot was just filled.
        venue_users = get_all_entity_users(db, 'venue', gig["venue_id"])

        for slot in booked_slots:
            aid = slot["artist_id"]
            email_vars = {
                # Empty-name fallback: artists.name is nullable; without a
                # default the email body renders "Hi ," (broken). Use a
                # neutral placeholder so emails always read cleanly.
                'artist_name':  slot.get("artist_name") or "there",
                'venue_name':   gig["venue_name"] or "the venue",
                'artist_id':    str(aid),
                'venue_id':     str(gig["venue_id"]),
                'gig_id':       str(gig_id),
                'date':         format_email_date(gig["date"]),
                'start_time':   format_time_12hr(slot["start_time"]),
                'end_time':     format_time_12hr(slot["end_time"]),
                # Door-deal aware. format_slot_pay_summary returns
                # "60.00" for flat or "50.00 guarantee + 20% of door"
                # when this slot is a door split (no leading "$" — the
                # template prepends one). For flat we keep the existing
                # preferred-artist override via _get_effective_pay_for_slot.
                'pay':          format_slot_pay_summary(slot) if (slot.get('deal_type') == 'door')
                                else f"{_get_effective_pay_for_slot(db, gig['venue_id'], aid, float(slot['pay'] or gig.get('pay') or 0)):,.2f}",
                'title':        gig.get("title") or "",
                'artist_type':  slot.get("artist_type") or gig.get("artist_type") or "",
                'band_formats': ", ".join(x.strip() for x in (slot.get("band_formats") or gig.get("band_formats") or "").split(",") if x.strip()),
                'styles':       ", ".join(x.strip() for x in (slot.get("styles") or gig.get("styles") or "").split(",") if x.strip()),
                'far_notice_artist': '',
                'far_notice_venue': '',
                'is_free_trial': _is_free_trial,
                **venue_vars,
            }

            # Compute artist↔venue distance and, if beyond the threshold, build
            # the two notice blocks (HTML — added to _HTML_SAFE_KEYS so they
            # render as markup, not escaped text).
            try:
                _art_geo = db.execute(text(
                    "SELECT latitude, longitude, city, state FROM artists WHERE id = :aid"
                ), {"aid": aid}).mappings().first()
                if _venue_geo and _art_geo:
                    _dist = _miles_between(
                        _art_geo["latitude"], _art_geo["longitude"],
                        _venue_geo["latitude"], _venue_geo["longitude"]
                    )
                    if _dist is not None and _dist > _far_miles:
                        # Jul 2026 full-site audit (E-H1): escape the
                        # user-controlled fragments before interpolating
                        # into the "safe HTML" block. Whitelist covers the
                        # wrapper only, not `artist_name`/city/state.
                        import html as _html
                        _mi = int(round(_dist))
                        _v_loc = _html.escape(", ".join([p for p in [_venue_geo.get("city"), _venue_geo.get("state")] if p]) or "the venue's area")
                        _a_loc = _html.escape(", ".join([p for p in [_art_geo.get("city"), _art_geo.get("state")] if p]) or "out of the area")
                        _art_name_esc = _html.escape(slot.get("artist_name") or "this artist")
                        email_vars['far_notice_artist'] = (
                            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                            f'style="margin:16px 0;"><tr><td style="background:#fffbeb;border:1px solid #fcd34d;'
                            f'border-radius:6px;padding:14px 16px;font-size:13px;line-height:1.5;color:#92400e;">'
                            f'📍 Heads up: this venue is in <strong>{_v_loc}</strong>, about '
                            f'<strong>{_mi} miles</strong> from your listed location. Just making sure '
                            f'you can perform in person — if you booked this by mistake, please cancel so '
                            f'the venue can re-open the slot.</td></tr></table>'
                        )
                        email_vars['far_notice_venue'] = (
                            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                            f'style="margin:16px 0;"><tr><td style="background:#eff6ff;border:1px solid #bfdbfe;'
                            f'border-radius:6px;padding:14px 16px;font-size:13px;line-height:1.5;color:#1e40af;">'
                            f'📍 Heads up: <strong>{_art_name_esc}</strong> is based in '
                            f'<strong>{_a_loc}</strong>, about <strong>{_mi} miles</strong> away. They booked through '
                            f'your open-gig window. If this looks like a mistake, you can cancel the booking from the '
                            f'gig details.</td></tr></table>'
                        )
            except Exception as _de:
                logger.warning(f"[BOOKING EMAIL] far-distance notice failed for gig {gig_id} artist {aid}: {_de}")

            # Artist email — each booked artist gets their own slot-specific confirmation.
            # Audit fix (May 2026 part 4): track which emails we sent on
            # the artist side so the venue loop below can skip the same
            # email if the same user owns both sides. Previously a user
            # who owned both the artist and the venue got two emails for
            # the same booking.
            # 2026-08-24: skip_artist=True caller (approve_booking) wants
            # only venue-side to fire because the artist already got the
            # enriched artist_booking_approved email with all the same
            # detail.
            artist_users = get_all_entity_users(db, 'artist', aid)
            _booked_sent_artists = set()
            if skip_artist:
                # Still track artist_users' emails so the venue-side loop
                # below can skip a shared owner (same-user-owns-both), which
                # is why we populate _booked_sent_artists rather than
                # returning early.
                for au in artist_users:
                    _booked_sent_artists.add(au["email"])
                logger.info(f"[BOOKING EMAIL] skip_artist=True — skipping {len(artist_users)} artist recipient(s) for gig {gig_id}")
            else:
                for au in artist_users:
                    if au["email"] in _booked_sent_artists:
                        continue
                    _booked_sent_artists.add(au["email"])
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
                # Skip if the same user already got the artist-side email above.
                if vu["email"] in _booked_sent_artists:
                    logger.info(f"[BOOKING EMAIL] skipping venue email to {vu['email']} — already got artist-side email (shared user)")
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

        # 2026-08-21: propagate Free Trial status into cancellation copy so
        # both parties see "cancelled — nothing to refund (Free Trial)"
        # instead of the standard cancellation language that implies money
        # was in flight.
        _cx_is_free_trial = '1' if _venue_on_free_trial(db, gig_details.get('venue_id')) else ''
        cancel_vars = {
            'user_name': gig_details.get('artist_name', 'Artist'),
            'venue_name': gig_details.get('venue_name', ''),
            'artist_name': gig_details.get('artist_name', ''),
            'artist_id': str(gig_details.get('artist_id', '')),
            'venue_id': str(gig_details.get('venue_id', '')),
            'gig_id': str(gig_id or ''),
            'date': format_email_date(gig_details.get('date', '')),
            'is_free_trial': _cx_is_free_trial,
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

            Audit fix (May 2026 part 3): use the pre-opened pooled ``_smtp``
            connection from the enclosing scope so the entire batch goes
            through one SMTP session. Previously this called ``_smtp_send``
            which opens a fresh connection per recipient — the optimization
            comment above the pool setup ("Send to ALL artist + venue users
            in ONE SMTP session") was a lie. Falls back to per-call open
            when the pool isn't available (SMTP login earlier failed).

            subject_override: if set, use this string instead of the template subject.
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
            # Use pooled connection when available, fall back to per-call open.
            if _smtp is not None:
                try:
                    _smtp.send_message(msg)
                    logger.info(f"[CANCEL EMAIL] _cancel_send SUCCESS (pooled) to {to_email}")
                    return True
                except Exception as _pe:
                    logger.warning(f"[CANCEL EMAIL] pooled send failed, falling back to per-call: {_pe}")
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
    finally:
        # Close the pooled SMTP connection if we opened one. Use
        # locals().get to handle the case where execution bailed before
        # _smtp was assigned.
        _smtp_local = locals().get('_smtp')
        if _smtp_local is not None:
            try:
                _smtp_local.quit()
            except Exception:
                pass


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

        # Build a rich slot-details block (Time / Pay / Type / Lineup / Styles)
        # for the artist's specific slot(s) so the venue email matches the
        # detail level of the other gig emails. May 15 2026 — was just a
        # bare "{{slot_times}}" inline string before.
        from sqlalchemy import text as _cse_text2
        gig_row = db.execute(_cse_text2("""
            SELECT g.title, g.notes, g.start_time, g.end_time, g.pay,
                   g.artist_type, g.band_formats, g.styles
            FROM gigs g WHERE g.id = :gid
        """), {"gid": gig_id}).mappings().first()
        slot_rows = db.execute(_cse_text2("""
            SELECT gs.slot_number, gs.start_time, gs.end_time, gs.pay,
                   gs.artist_type, gs.band_formats, gs.styles,
                   gs.deal_type, gs.door_pct, gs.guarantee_cents
            FROM gig_slots gs
            WHERE gs.gig_id = :gid AND gs.artist_id = :aid
              AND gs.status IN ('booked', 'pending_contract', 'awaiting_venue_contract')
            ORDER BY gs.slot_number ASC
        """), {"gid": gig_id, "aid": artist_id}).mappings().all()

        def _fmt_pay(v):
            try:
                pf = float(v); return f"{pf:.2f}" if pf != int(pf) else str(int(pf))
            except (ValueError, TypeError):
                return str(v or '')

        def _commas(s):
            return ', '.join(x.strip() for x in (s or '').split(',') if x.strip())

        ROW = ('<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">{label}</td>'
               '<td style="padding:6px 0;font-size:14px;color:{color};font-weight:{weight};">{value}</td></tr>')
        SEP = '<tr><td colspan="2" style="padding:4px 0;border-top:1px solid #e5e7eb;"></td></tr>'

        slot_rows_html = []
        if slot_rows:
            for i, sl in enumerate(slot_rows):
                if i > 0:
                    slot_rows_html.append(SEP)
                t_s = format_time_12hr(sl["start_time"] or '')
                t_e = format_time_12hr(sl["end_time"] or '')
                time_str = f"{t_s} – {t_e}" if t_e else t_s
                # Door-deal aware pay rendering. format_pay_summary_with_sign
                # returns "$60.00" for flat or "$50.00 guarantee + 20% of door".
                pay_display = format_pay_summary_with_sign(
                    sl, fallback_pay=(gig_row.get("pay") if gig_row else None)
                )
                atype  = sl.get("artist_type")  or (gig_row.get("artist_type")  if gig_row else '')
                lineup = _commas(sl.get("band_formats") or (gig_row.get("band_formats") if gig_row else ''))
                styles = _commas(sl.get("styles")       or (gig_row.get("styles")       if gig_row else ''))
                slot_rows_html.append(ROW.format(label="Time",   color="#111827", weight="500", value=time_str))
                slot_rows_html.append(ROW.format(label="Pay",    color="#059669", weight="600", value=pay_display))
                if atype:  slot_rows_html.append(ROW.format(label="Type",   color="#111827", weight="500", value=atype))
                if lineup: slot_rows_html.append(ROW.format(label="Lineup", color="#111827", weight="500", value=lineup))
                if styles: slot_rows_html.append(ROW.format(label="Styles", color="#111827", weight="500", value=styles))
        # Jul 2026: legacy single-slot fallback removed — every gig has a
        # gig_slots row (see db.setup_database backfill). If we hit this
        # branch it means the caller's slot query returned nothing when it
        # should have, so we log and produce no rows rather than silently
        # swapping in gig-level umbrella data.
        elif gig_row:
            logger.warning(
                f"[CONTRACT SIGN EMAIL] gig {gig_row.get('id','?')} has no matching slots — "
                f"slot rows may have been deleted out of band. Email will have empty terms."
            )

        slots_html = ''.join(slot_rows_html)
        gig_title  = (gig_row.get("title") if gig_row else '') or ''

        email_vars = {
            'artist_name': artist_name,
            'venue_name': venue_name,
            'venue_id': str(venue_id),
            'date': date_display,
            # Keep slot_times for backward-compat with anything still using it
            'slot_times': compute_slot_times(db, gig_id, artist_id=artist_id),
            'gig_title': gig_title,
            'title': gig_title,
            'slots_html': slots_html,
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
        # BUG FIX (Jul 2026 audit): match the status set notify_gig_edited uses
        # so artists mid-contract-flow (pending_contract / awaiting_venue_contract
        # / pending_venue_approval) get the edit email in addition to the in-app
        # notification. Previously they got the notification but no email — a
        # silent divergence per the audit report.
        _IN_FLIGHT = ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
        artist_ids = set()
        slots_all = db.execute(text("""
            SELECT DISTINCT artist_id FROM gig_slots
            WHERE gig_id = :gid AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
              AND artist_id IS NOT NULL
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
                SELECT start_time, end_time, pay, artist_type, band_formats, styles,
                       deal_type, door_pct, guarantee_cents
                FROM gig_slots
                WHERE gig_id = :gid AND artist_id = :aid
                  AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                LIMIT 1
            """), {"gid": gig_id, "aid": aid}).mappings().first()
            if slot:
                _slot_base_pay = float(slot['pay'] or gig.get('pay') or 0)
                _slot_eff_pay  = _get_effective_pay_for_slot(db, gig["venue_id"], aid, _slot_base_pay)
                # Door-deal slots: render "50.00 guarantee + 20% of door" instead
                # of the flat amount. The {pay} placeholder in templates already
                # prepends "$" so we use the no-sign variant. For flat we keep
                # the venue-override-aware effective pay.
                if (slot.get('deal_type') or '').lower() == 'door':
                    _pay_str = format_slot_pay_summary(slot, fallback_pay=gig.get('pay'))
                else:
                    _pay_str = f"{_slot_eff_pay:,.2f}"
                slot_vars = {
                    "start_time":   format_time_12hr(slot["start_time"]),
                    "end_time":     format_time_12hr(slot["end_time"]),
                    "pay":          _pay_str,
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
        # Door-deal aware pay rendering. Look up the artist's slot (any
        # in-flight state) so the email pay line reflects the actual deal
        # terms — flat OR "$X guarantee + Y% of door". For flat deals we
        # keep the venue-override-aware effective pay.
        _slot_row = db.execute(text("""
            SELECT pay, deal_type, door_pct, guarantee_cents, start_time, end_time
            FROM gig_slots
            WHERE gig_id = :gid AND artist_id = :aid
              AND status IN ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval')
            ORDER BY slot_number ASC LIMIT 1
        """), {"gid": gig_id, "aid": artist_id}).mappings().first()
        base_pay = float(gig_details.get('pay') or 0)
        if _slot_row and (_slot_row.get('deal_type') or '').lower() == 'door':
            pay_display = format_slot_pay_summary(_slot_row, fallback_pay=base_pay)
        else:
            effective_pay = _get_effective_pay_for_slot(db, venue_id, artist_id, base_pay)
            pay_display = f"{effective_pay:,.2f}"

        # Jul 2026 bug fix: use the artist's slot times, not the gig umbrella,
        # so approval-request emails show the actual slot the artist requested
        # (8-10pm) instead of the whole gig window (7-11pm) on multi-slot gigs.
        _slot_start = (_slot_row.get('start_time') if _slot_row else None) or gig_details.get('start_time')
        _slot_end   = (_slot_row.get('end_time')   if _slot_row else None) or gig_details.get('end_time')

        # Generate a one-time approval token. Audit fix (May 2026 part 5):
        # previously this overwrote a single `gigs.approval_token` column —
        # two artists requesting same-day on the same multi-slot gig would
        # invalidate each other's email links. Now we store per-(gig, artist)
        # tokens in pending_approval_tokens. The legacy gigs.approval_token
        # is still updated as a fallback for handlers that haven't migrated
        # to the new lookup yet (compat during rollout).
        from sqlalchemy import text as _text
        from backend.utils import utcnow_naive as _utcnow_naive
        approval_token = secrets.token_urlsafe(32)
        try:
            # Replace any prior pending row for this (gig, artist) — a fresh
            # request supersedes its predecessor.
            db.execute(_text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid AND artist_id = :aid"),
                       {"gid": gig_id, "aid": artist_id})
            # Jul 2026 audit (B-C2): populate `expires_at` so a venue
            # that never acts can't leave a valid replayable token forever.
            # 72h window matches the artist's typical wait tolerance for
            # a same-day booking response.
            _now_utc = _utcnow_naive()
            from datetime import timedelta as _td
            _expires_at = _now_utc + _td(hours=72)
            db.execute(_text("INSERT INTO pending_approval_tokens (token, gig_id, artist_id, created_at, expires_at) VALUES (:tok, :gid, :aid, :now, :exp)"),
                       {"tok": approval_token, "gid": gig_id, "aid": artist_id,
                        "now": _now_utc, "exp": _expires_at})
        except Exception as _pe:
            logger.warning(f"[APPROVAL_EMAIL] pending_approval_tokens write failed: {_pe}")
        db.execute(_text("UPDATE gigs SET approval_token = :tok WHERE id = :gid"),
                   {"tok": approval_token, "gid": gig_id})
        db.flush()

        # Audit fix (May 2026 part 4): read site_url from platform_settings
        # so staging / test envs aren't hardcoded to production. Falls back
        # to gigsfill.com if the setting is missing.
        base_url = "https://gigsfill.com"
        try:
            _row = db.execute(_text(
                "SELECT setting_value FROM platform_settings WHERE setting_key = 'site_url'"
            )).scalar()
            if _row:
                base_url = _row.rstrip("/")
        except Exception:
            pass
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
            'start_time':  format_time_12hr(_slot_start),
            'end_time':    format_time_12hr(_slot_end),
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
        # Door-deal aware pay rendering (mirror of send_approval_request_emails).
        base_pay = float(gig_details.get('pay') or 0)
        venue_id = gig_details.get('venue_id')
        gig_id = gig_details.get('id') or gig_details.get('gig_id')
        _slot_row = db.execute(text("""
            SELECT pay, deal_type, door_pct, guarantee_cents, start_time, end_time
            FROM gig_slots
            WHERE gig_id = :gid AND artist_id = :aid
              AND status IN ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval')
            ORDER BY slot_number ASC LIMIT 1
        """), {"gid": gig_id, "aid": artist_id}).mappings().first() if gig_id else None
        if _slot_row and (_slot_row.get('deal_type') or '').lower() == 'door':
            pay_display = format_slot_pay_summary(_slot_row, fallback_pay=base_pay)
        else:
            effective_pay = _get_effective_pay_for_slot(db, venue_id, artist_id, base_pay)
            pay_display = f"{effective_pay:,.2f}"
        notification_type = 'artist_booking_approved' if approved else 'artist_booking_denied'

        # Jul 2026 bug fix: render artist's slot time, not gig umbrella.
        _slot_start = (_slot_row.get('start_time') if _slot_row else None) or gig_details.get('start_time')
        _slot_end   = (_slot_row.get('end_time')   if _slot_row else None) or gig_details.get('end_time')

        slot_vars = {"slot_info": slot_info} if slot_info else {}

        # 2026-08-24: enrich the approval email with the same venue/gig
        # detail bag the standard artist_gig_booked template uses, so
        # the artist gets ONE email (approval) with everything instead
        # of two (approval + standard booked). approve_booking now skips
        # the artist side of send_booking_emails.
        _gig_extras = {}
        _venue_extras = {}
        if approved and gig_id:
            try:
                _g = db.execute(text("""
                    SELECT g.title, g.notes as gig_notes, g.artist_type, g.band_formats, g.styles
                    FROM gigs g WHERE g.id = :gid
                """), {"gid": gig_id}).mappings().first()
                if _g:
                    _gig_extras = {
                        'title':        _g.get('title') or '',
                        'artist_type':  _g.get('artist_type') or '',
                        'band_formats': ", ".join(x.strip() for x in (_g.get('band_formats') or '').split(',') if x.strip()),
                        'styles':       ", ".join(x.strip() for x in (_g.get('styles') or '').split(',') if x.strip()),
                    }
                    if venue_id:
                        _venue_extras = _fetch_venue_detail_vars(db, venue_id, gig_notes=_g.get('gig_notes') or '')
            except Exception as _xe:
                logger.warning(f"[APPROVAL_DECISION_EMAIL] enrichment fetch failed: {_xe}")

        email_vars = {
            'artist_name': gig_details.get('artist_name', ''),
            'venue_name':  gig_details.get('venue_name', ''),
            'artist_id':   str(artist_id),
            'venue_id':    str(venue_id) if venue_id else '',
            'gig_id':      str(gig_id) if gig_id else '',
            'date':        format_email_date(gig_details.get('date', '')),
            'start_time':  format_time_12hr(_slot_start),
            'end_time':    format_time_12hr(_slot_end),
            'pay':         pay_display,
            'far_notice_artist': '',
            **_gig_extras,
            **_venue_extras,
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

"""
Open-gig daily digest pipeline.

OWNERSHIP:
  Detection runs hourly in scheduler.py:process_open_gig_notifications.
  When platform_setting `open_gig_daily_digest_enabled` is true (the
  default), that detector now ENQUEUES into artist_email_digest_queue
  instead of sending immediately. This module provides:

      enqueue_open_gig_for_artist(cursor, user_id, artist_id, gig_id,
                                  venue_id, notification_key, via_radius)
          Idempotent INSERT (the unique constraint on (user_id, gig_id,
          notification_key) handles re-detection across hours/days).

      send_daily_artist_digest(cursor, smtp_config)
          Runs once per hour (from the scheduler loop). Selects users
          whose local hour equals `open_gig_daily_digest_hour` AND
          who have unsent queued entries. Renders the daily digest
          (grouped by venue), sends one consolidated email per user,
          marks the queue rows sent_at=now.

      prune_old_queue_rows(cursor, days=30)
          Cleanup. Drops sent rows older than N days so the queue
          doesn't accrete forever.

DESIGN NOTES:
  - Per-artist per-window email_preferences continue to filter at
    enqueue time, so an artist who disabled venue_open_gig_4w never
    gets 4w gigs in their digest. The detector applies that filter
    before calling enqueue_open_gig_for_artist.
  - The digest also picks up the `notification_key` 'open_gig_36h' as
    the "urgent" badge — gigs with that key render with a 🚨 prefix
    in the email body. No separate 36h send path.
  - Radius-blast (blast_all_enabled) gigs are enqueued with
    via_radius=1; the email adds an "in your area" badge so the
    artist understands why they got it even without a preferred
    relationship.
  - Cancellation blasts (fire_cancelled_gig_blast in routes/gigs.py)
    DO NOT go through this digest — they're synchronous, urgent, and
    keep their own send path. They're a separate notification class.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("gigsfill.open_gig_digest")


def enqueue_open_gig_for_artist(cursor, *, user_id: int, artist_id: int,
                                gig_id: int, venue_id: int,
                                notification_key: str, via_radius: bool = False) -> bool:
    """Idempotent enqueue. Returns True on insert, False on dup."""
    try:
        cursor.execute(
            """INSERT OR IGNORE INTO artist_email_digest_queue
                (user_id, artist_id, gig_id, venue_id, notification_key, via_radius)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (int(user_id), int(artist_id), int(gig_id), int(venue_id),
             notification_key, 1 if via_radius else 0)
        )
        return (cursor.rowcount or 0) > 0
    except Exception as e:
        logger.warning(f"enqueue_open_gig_for_artist failed: {e}")
        return False


def _due_users(cursor, digest_hour: int) -> list[dict]:
    """Return users whose local-time hour == digest_hour AND who have
    pending queue rows. Resolves each user's timezone via their artists
    record (state → US_STATE_TIMEZONES → platform fallback).
    """
    # Pull every distinct user with unsent rows + their primary artist's
    # state. One user can own multiple artists; we use the first one's
    # state to pick a timezone — close enough for "is it 9am for them".
    rows = cursor.execute("""
        SELECT DISTINCT q.user_id, q.artist_id, a.state
        FROM artist_email_digest_queue q
        JOIN artists a ON a.id = q.artist_id
        WHERE q.sent_at IS NULL
    """).fetchall()
    if not rows:
        return []

    # Lazy imports — these utilities don't ship with the raw scheduler
    # process_open_gig path, so we only pay the cost when there's
    # actually work to do.
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return []
    from backend.utils import US_STATE_TIMEZONES

    # Read platform tz fallback once.
    platform_tz = "America/Los_Angeles"
    try:
        row = cursor.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'"
        ).fetchone()
        if row and row[0]:
            platform_tz = row[0]
    except Exception:
        pass

    due = []
    seen_users = set()
    for r in rows:
        user_id = r[0]
        if user_id in seen_users:
            continue
        seen_users.add(user_id)
        state = (r[2] or "").strip().upper()
        tz_str = US_STATE_TIMEZONES.get(state) or platform_tz
        try:
            tz = ZoneInfo(tz_str)
            local_hour = datetime.now(tz).hour
        except Exception:
            continue
        if local_hour == digest_hour:
            due.append({"user_id": user_id, "tz_str": tz_str})
    return due


def _fetch_user_queue(cursor, user_id: int) -> list[dict]:
    """Pull unsent queue rows for a user with all the gig+venue context
    the digest needs. Filters out gigs that are no longer 'open' (got
    booked between enqueue and send) — those rows are still marked
    sent_at so the queue stays clean.

    Each row also carries the ARTIST's identity (id + name) so the
    digest renderer can group by artist when a user owns multiple.
    """
    rows = cursor.execute("""
        SELECT q.id, q.gig_id, q.venue_id, q.notification_key, q.via_radius,
               q.artist_id, a.name as artist_name,
               g.date, g.start_time, g.end_time, g.pay, g.title, g.status,
               g.artist_type, g.band_formats, g.styles,
               COALESCE(g.is_multi_slot, 0) as is_multi_slot,
               (SELECT COUNT(*) FROM gig_slots gs
                  WHERE gs.gig_id = g.id AND gs.status = 'open') as open_slot_count,
               v.venue_name, v.city, v.state, v.latitude as v_lat, v.longitude as v_lon
        FROM artist_email_digest_queue q
        JOIN artists a ON a.id = q.artist_id
        JOIN gigs g ON g.id = q.gig_id
        JOIN venues v ON v.id = q.venue_id
        WHERE q.user_id = ? AND q.sent_at IS NULL
        ORDER BY a.id ASC, g.date ASC, q.venue_id ASC
    """, (user_id,)).fetchall()
    return [
        {
            "queue_id": r[0], "gig_id": r[1], "venue_id": r[2],
            "notification_key": r[3], "via_radius": bool(r[4]),
            "artist_id": r[5], "artist_name": r[6] or "",
            "date": r[7], "start_time": r[8], "end_time": r[9],
            "pay": r[10], "title": r[11], "status": r[12],
            "artist_type": r[13], "band_formats": r[14], "styles": r[15],
            "is_multi_slot": bool(r[16]),
            "open_slot_count": int(r[17] or 0),
            "venue_name": r[18], "city": r[19], "state": r[20],
            "venue_lat": r[21], "venue_lon": r[22],
        }
        for r in rows
    ]


def _fetch_artist_booked_gigs_live(cursor, artist_id: int, window_days: int = 28) -> list[dict]:
    """Return every CONFIRMED/in-flight booking for this artist within
    `window_days` of today — booked, pending_contract,
    awaiting_venue_contract, or pending_venue_approval. Per user
    request (Jun 2026): include these in the digest so the artist
    sees their next 4 weeks at a glance — what they've committed to
    AND what's still bookable.

    Returns rows shaped the same as _fetch_artist_open_gigs_live so the
    renderer can share format helpers. `slots` only contains the slots
    THIS artist holds (not all slots on the gig). Each slot carries an
    explicit `status` field so the renderer can show "Awaiting Venue
    Countersign" etc.
    """
    art = cursor.execute(
        "SELECT artist_type, latitude, longitude FROM artists WHERE id = ?",
        (artist_id,)
    ).fetchone()
    if not art:
        return []
    artist_lat, artist_lon = art[1], art[2]

    # Apply this artist's standing override per venue so the displayed
    # pay matches what they'll actually be paid.
    override_by_venue: dict[int, float] = {}
    for orow in cursor.execute(
        """SELECT venue_id,
                  COALESCE(pay_dollars_override, 0) + COALESCE(pay_cents_override, 0) / 100.0 as override_pay
           FROM preferred_artists
           WHERE artist_id = ? AND status = 'approved'
             AND pay_dollars_override IS NOT NULL""",
        (artist_id,)
    ).fetchall():
        try:
            override_by_venue[int(orow[0])] = float(orow[1] or 0)
        except Exception:
            pass

    statuses = ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval')
    rows = cursor.execute(f"""
        SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.title,
               g.artist_type,
               COALESCE(g.is_multi_slot, 0) as is_multi_slot,
               g.venue_id, v.venue_name, v.city, v.state,
               v.latitude as v_lat, v.longitude as v_lon
        FROM gigs g
        JOIN venues v ON v.id = g.venue_id
        WHERE g.date >= date('now')
          AND g.date <= date('now', '+{int(window_days)} days')
          AND (
              (g.artist_id = ? AND g.status IN ({",".join("?" * len(statuses))}))
              OR EXISTS (
                  SELECT 1 FROM gig_slots gs
                  WHERE gs.gig_id = g.id AND gs.artist_id = ?
                    AND gs.status IN ({",".join("?" * len(statuses))})
              )
          )
        ORDER BY g.date ASC, g.start_time ASC
    """, (artist_id, *statuses, artist_id, *statuses)).fetchall()

    out = []
    for r in rows:
        (gid, gdate, gstart, gend, gpay, gtitle, g_type, is_multi,
         v_id, v_name, v_city, v_state, v_lat, v_lon) = r
        v_id = int(v_id)
        # Only this artist's slots on the gig
        slot_rows = cursor.execute("""
            SELECT slot_number, start_time, end_time, pay, status,
                   artist_type, deal_type, door_pct, guarantee_cents,
                   COALESCE(apply_override, 0) as apply_override
            FROM gig_slots
            WHERE gig_id = ? AND artist_id = ?
              AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
            ORDER BY slot_number
        """, (int(gid), artist_id)).fetchall()
        override_pay = override_by_venue.get(v_id)
        out_slots = []
        for s in slot_rows:
            is_door = (s[6] or "").lower() == "door"
            if is_door:
                eff = float((s[8] or 0)) / 100.0
                if override_pay is not None and int(s[9] or 0) == 1 and override_pay > eff:
                    eff = override_pay
            else:
                eff = float(s[3] or 0)
                if override_pay is not None and override_pay > eff:
                    eff = override_pay
            out_slots.append({
                "slot_number": s[0], "start_time": s[1], "end_time": s[2],
                "status": s[4], "artist_type": s[5],
                "deal_type": s[6] or "flat", "door_pct": int(s[7] or 0),
                "effective_pay": eff,
            })
        # Single-slot fallback: gig itself carries the artist's booking
        if not out_slots:
            out_slots.append({
                "slot_number": 1, "start_time": gstart, "end_time": gend,
                "status": "booked", "artist_type": g_type,
                "deal_type": "flat", "door_pct": 0,
                "effective_pay": float(gpay or 0),
            })
        dist_mi = _haversine_miles(artist_lat, artist_lon, v_lat, v_lon)
        out.append({
            "gig_id": int(gid), "venue_id": v_id,
            "date": gdate, "start_time": gstart, "end_time": gend,
            "pay": gpay, "title": gtitle, "artist_type": g_type,
            "is_multi_slot": bool(is_multi),
            "open_slot_count": len(out_slots),
            "venue_name": v_name, "city": v_city, "state": v_state,
            "venue_lat": v_lat, "venue_lon": v_lon,
            "distance_mi": dist_mi,
            "urgent_36h": False,  # booked gigs don't get the urgency badge
            "artist_id": artist_id,
            "slots": out_slots,
        })
    return out


def _fetch_artist_external_gigs_for_digest(cursor, artist_id: int,
                                             window_days: int = 28) -> list[dict]:
    """Return external (non-GigsFill) gigs the artist logged themselves,
    shaped for the "Your upcoming gigs" digest section. Marked
    `_is_external: True` so the renderer skips the venue link + pay
    column (external venues aren't on GigsFill and external gigs never
    carry pay in our DB).
    2026-08-04: added so the artist's digest reflects their complete
    schedule, not just GigsFill bookings.
    """
    rows = cursor.execute(f"""
        SELECT id, venue_name, venue_city, venue_state,
               date, start_time, end_time, artist_type
        FROM artist_external_gigs
        WHERE artist_id = ?
          AND date >= date('now')
          AND date <= date('now', '+{int(window_days)} days')
        ORDER BY date ASC, start_time ASC
    """, (artist_id,)).fetchall()
    out = []
    for r in rows:
        (eid, v_name, v_city, v_state, gdate, gstart, gend, a_type) = r
        out.append({
            # gig_id None → renderer switches date to plain text (no link
            # into the artist calendar since there's no real gig row).
            "gig_id": None,
            "venue_id": None,
            "date": gdate, "start_time": gstart, "end_time": gend,
            "pay": 0, "title": "", "artist_type": a_type,
            "is_multi_slot": False,
            "open_slot_count": 1,
            "venue_name": v_name or "",
            "city": v_city or "", "state": v_state or "",
            "venue_lat": None, "venue_lon": None,
            "distance_mi": None,
            "urgent_36h": False,
            "artist_id": artist_id,
            "slots": [{
                "slot_number": 1,
                "start_time": gstart, "end_time": gend,
                "status": "booked",
                "artist_type": a_type,
                "deal_type": "flat", "door_pct": 0,
                "effective_pay": 0,
            }],
            "_is_external": True,
        })
    return out


def _fetch_artist_open_gigs_live(cursor, artist_id: int, window_days: int = 28,
                                  mode: str = "eligible",
                                  nearby_radius_miles: int = 20) -> list[dict]:
    """Return every OPEN gig within `window_days` of today that THIS artist
    could book. Two modes:

    mode='eligible' (default) — the "you can book" list. Requires preferred
    at the venue OR in-radius of a venue with blast_all_enabled. Same as
    the historical behavior.

    mode='nearby_non_preferred' (Jul 2026, user request) — the "not
    preferred yet" list. Requires the venue to be within
    `nearby_radius_miles` of the artist AND the artist NOT to be preferred
    at the venue. Feeds the "Nearby gigs — request Preferred Status" section
    of the digest so artists see venues they could reach out to. Same
    type / lineup / style / equipment / blackout / frequency filters as
    the eligible mode so we never surface a gig the artist actually can't
    book — the only gate that changes is the preferred-vs-radius gate.

    Rules (both modes):
      - Open: g.status='open' AND at least one slot open
      - Window: today <= g.date <= today + window_days
      - Hidden: held gigs (hold_status active/exhausted) excluded
      - Type + lineup + style + equipment match (from _artist_matches_slot)
      - Artist not on venue ban list
      - Artist has no blackout covering the gig date
      - Artist not already booked on this gig
    """
    art = cursor.execute(
        "SELECT artist_type, latitude, longitude, band_formats, styles, "
        "       COALESCE(has_own_equipment, 0) as has_own_equipment "
        "FROM artists WHERE id = ?",
        (artist_id,)
    ).fetchone()
    if not art:
        return []
    artist_type = (art[0] or "").strip()
    artist_lat, artist_lon = art[1], art[2]
    # Jul 1 2026 fix: also fetch band_formats + styles + has_own_equipment
    # so we can filter slots by the same rules as `_artist_matches_slot`
    # in gig_hold.py. Solo/Duo LB artists don't see Trio/Full-Band-only
    # slots; MC-type slots requiring equipment don't reach artists
    # without has_own_equipment.
    def _csv_set(s):
        if not s:
            return set()
        return {t.strip().lower() for t in str(s).replace(";", ",").split(",") if t.strip()}
    artist_formats = _csv_set(art[3])
    artist_styles = _csv_set(art[4])
    artist_has_equipment = bool(art[5])
    _MC_TYPES_LC = {"open mic mc", "karaoke mc"}

    # Preferred-approved venues for this artist
    preferred_venue_ids = {
        r[0] for r in cursor.execute(
            "SELECT venue_id FROM preferred_artists WHERE artist_id = ? AND status = 'approved'",
            (artist_id,)
        ).fetchall()
    }

    # Venues with blast_all_enabled for at least one window (used to
    # gate the in-radius branch — if a venue never blasts to all, an
    # in-radius non-preferred artist shouldn't see their gigs here).
    blast_capable_venues = {}  # venue_id → radius_miles
    for r in cursor.execute("""
        SELECT venue_id, MAX(COALESCE(blast_all_radius, 20)) as radius
        FROM venue_email_notifications
        WHERE COALESCE(enabled, 1) = 1 AND COALESCE(blast_all_enabled, 0) = 1
        GROUP BY venue_id
    """).fetchall():
        blast_capable_venues[int(r[0])] = int(r[1] or 20)

    # Candidate gig pool. Outer filters keep the result set tight; we
    # apply per-row type / eligibility / blackout filters in Python so
    # the SQL stays portable across SQLite + Postgres.
    # Jul 2026 audit (P-C3): include COALESCE(g.frequency_exempt, 0) in
    # the outer SELECT so the per-gig SELECT at line 597 (which was
    # inside the row loop → N+1 for N candidate gigs × 2 modes × M
    # artists × K users) becomes a column read.
    rows = cursor.execute(f"""
        SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.title,
               g.artist_type, g.band_formats, g.styles,
               COALESCE(g.is_multi_slot, 0) as is_multi_slot,
               COALESCE(g.frequency_exempt, 0) as frequency_exempt,
               g.venue_id, v.venue_name, v.city, v.state,
               v.latitude as v_lat, v.longitude as v_lon,
               (SELECT COUNT(*) FROM gig_slots gs
                  WHERE gs.gig_id = g.id AND gs.status = 'open') as open_slot_count
        FROM gigs g
        JOIN venues v ON v.id = g.venue_id
        WHERE g.status = 'open'
          AND g.date >= date('now')
          AND g.date <= date('now', '+{int(window_days)} days')
          AND (g.hold_status IS NULL OR g.hold_status NOT IN ('active','exhausted'))
          AND COALESCE(v.payment_status, 'active') != 'suspended'
          AND EXISTS (
              SELECT 1 FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status = 'open'
          )
        ORDER BY g.date ASC, g.start_time ASC
    """).fetchall()
    if not rows:
        return []

    # Pull full per-slot data once (artist_type for type match, plus
    # deal terms + apply_override so the digest renderer can show
    # the correct effective pay per slot — including door deals like
    # "$15 + 50% door" — and so we can apply this artist's standing
    # per-venue pay override to each slot's displayed pay.
    gig_ids = [r[0] for r in rows]
    slot_types_by_gig: dict[int, list[str]] = {}
    slots_by_gig: dict[int, list[dict]] = {}
    if gig_ids:
        placeholders = ",".join("?" * len(gig_ids))
        slot_rows = cursor.execute(f"""
            SELECT gig_id, slot_number, start_time, end_time, pay, status,
                   artist_type, deal_type, door_pct, guarantee_cents,
                   COALESCE(apply_override, 0) as apply_override,
                   band_formats, styles, requires_equipment
            FROM gig_slots
            WHERE gig_id IN ({placeholders})
            ORDER BY gig_id, slot_number
        """, gig_ids).fetchall()
        for sr in slot_rows:
            gid = int(sr[0])
            if sr[5] == 'open':
                slot_types_by_gig.setdefault(gid, []).append((sr[6] or "").strip())
            slots_by_gig.setdefault(gid, []).append({
                "slot_number": sr[1], "start_time": sr[2], "end_time": sr[3],
                "pay": sr[4], "status": sr[5], "artist_type": sr[6],
                "deal_type": sr[7], "door_pct": sr[8],
                "guarantee_cents": sr[9], "apply_override": int(sr[10] or 0),
                # Jul 1 2026 band-format/style filter fields:
                "band_formats": sr[11], "styles": sr[12],
                # Jul 1 2026 MC-equipment gate:
                "requires_equipment": sr[13],
            })

    # Pull this artist's pay override at every venue in one shot so we
    # can apply it per-slot without N+1 queries. Map venue_id → dollars.
    override_by_venue: dict[int, float] = {}
    for orow in cursor.execute(
        """SELECT venue_id,
                  COALESCE(pay_dollars_override, 0) + COALESCE(pay_cents_override, 0) / 100.0 as override_pay
           FROM preferred_artists
           WHERE artist_id = ? AND status = 'approved'
             AND pay_dollars_override IS NOT NULL""",
        (artist_id,)
    ).fetchall():
        try:
            override_by_venue[int(orow[0])] = float(orow[1] or 0)
        except Exception:
            pass

    # Frequency policy lookup: venue's default + this artist's per-venue
    # override. Used to filter gigs the artist couldn't legitimately
    # book because they have another booking within the policy window.
    # Mirrors the booking-time check at routes/gigs.py:2369 / 4577.
    freq_by_venue: dict[int, int] = {}
    for fr in cursor.execute("""
        SELECT v.id,
               COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
        FROM venues v
        LEFT JOIN preferred_artists pa
            ON pa.venue_id = v.id AND pa.artist_id = ?
    """, (artist_id,)).fetchall():
        try:
            freq_by_venue[int(fr[0])] = int(fr[1] or 0)
        except Exception:
            pass

    # All this artist's existing bookings at any venue (with dates) —
    # used to detect frequency conflicts.
    existing_bookings = cursor.execute("""
        SELECT g.id, g.venue_id, g.date
        FROM gigs g
        WHERE g.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
          AND (g.artist_id = ?
               OR EXISTS (
                  SELECT 1 FROM gig_slots gs
                  WHERE gs.gig_id = g.id AND gs.artist_id = ?
                    AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
               ))
    """, (artist_id, artist_id)).fetchall()
    bookings_by_venue: dict[int, list[str]] = {}
    for b in existing_bookings:
        bookings_by_venue.setdefault(int(b[1]), []).append(str(b[2])[:10])

    # Bans + blackouts in one shot each
    banned_venue_ids = {
        int(r[0]) for r in cursor.execute(
            "SELECT venue_id FROM venue_artist_bans WHERE artist_id = ?",
            (artist_id,)
        ).fetchall()
    }
    blackout_dates: set[str] = set()
    for br in cursor.execute(
        "SELECT blackout_start, blackout_end FROM artist_availability WHERE artist_id = ?",
        (artist_id,)
    ).fetchall():
        try:
            from datetime import date as _d, timedelta as _td
            s = _d.fromisoformat(str(br[0])[:10])
            e = _d.fromisoformat(str(br[1])[:10])
            d = s
            while d <= e:
                blackout_dates.add(d.isoformat())
                d += _td(days=1)
        except Exception:
            pass

    # Already-booked gigs (artist has any slot on this gig in a non-open state with their id)
    already_booked_gig_ids = {
        int(r[0]) for r in cursor.execute(
            """SELECT DISTINCT gig_id FROM gig_slots
               WHERE artist_id = ? AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')""",
            (artist_id,)
        ).fetchall()
    }

    out = []
    for r in rows:
        (gid, gdate, gstart, gend, gpay, gtitle,
         g_type, g_formats, g_styles, is_multi,
         gig_freq_exempt_col,
         v_id, v_name, v_city, v_state, v_lat, v_lon,
         open_count) = r
        v_id = int(v_id)
        if v_id in banned_venue_ids:
            continue
        if str(gdate)[:10] in blackout_dates:
            continue
        if int(gid) in already_booked_gig_ids:
            continue

        # Type + format + style filter (Jul 1 2026 fix). The artist must
        # be able to actually BOOK at least one open slot, mirroring the
        # `_artist_matches_slot` rule in services/gig_hold.py:1007:
        #   - slot's effective artist_type == artist's type
        #   - if slot specifies band_formats: at least one overlap with
        #     the artist's band_formats
        #   - if slot specifies styles: at least one overlap with the
        #     artist's styles
        # Effective slot fields use the gig-level value when the slot's
        # own value is NULL/empty (venue create-gigs form's inherit rule).
        _gig_type_lc = (g_type or "").strip().lower()
        _gig_formats_set = _csv_set(g_formats)
        _gig_styles_set = _csv_set(g_styles)
        _artist_type_lc = (artist_type or "").strip().lower()

        def _slot_matches_artist(_slot):
            _st = (_slot.get("artist_type") or "").strip().lower() or _gig_type_lc
            if _artist_type_lc and _st and _st != _artist_type_lc:
                return False
            # MC types (Open Mic MC, Karaoke MC) skip band_formats + styles
            # entirely (per Jul 1 2026 product decision — they have no
            # such axis). All other types honor the overlap check.
            _is_mc = _st in _MC_TYPES_LC
            if not _is_mc:
                _sf = _csv_set(_slot.get("band_formats")) or _gig_formats_set
                if _sf and not (_sf & artist_formats):
                    return False
                _ss = _csv_set(_slot.get("styles")) or _gig_styles_set
                if _ss and not (_ss & artist_styles):
                    return False
            # Equipment gate — venue set requires_equipment=1 → artist
            # must have opted in. NULL / 0 = no filter.
            _req = _slot.get("requires_equipment")
            if _req in (1, True, "1", "true", "True"):
                if not artist_has_equipment:
                    return False
            return True

        _all_slots = slots_by_gig.get(int(gid), [])
        _open_slots = [s for s in _all_slots if (s.get("status") or "").lower() == "open"]

        # Jul 2026 refactor: every gig has ≥1 gig_slots row (backfill in
        # db.setup_database). The prior 'else' branch that matched against
        # gig-level fields for legacy slot-less gigs is now unreachable —
        # the outer SQL also requires `EXISTS (SELECT 1 FROM gig_slots
        # WHERE gig_id = g.id AND status = 'open')`. Slot-only match now.
        if not any(_slot_matches_artist(s) for s in _open_slots):
            continue

        # Eligibility gate — differs per mode. Jul 2026: added
        # nearby_non_preferred mode so digest can surface "here are gigs
        # you're not preferred at yet but could reach out about" section.
        dist_mi = None
        if mode == "nearby_non_preferred":
            # Skip venues where the artist is already preferred — those are
            # in the main "you can book" section, don't double-list them.
            if v_id in preferred_venue_ids:
                continue
            dist_mi = _haversine_miles(artist_lat, artist_lon, v_lat, v_lon)
            if dist_mi is None or dist_mi > nearby_radius_miles:
                continue
        else:
            # Default 'eligible' mode: preferred OR (in-radius with a
            # blast-capable venue). Original behavior.
            eligible = False
            if v_id in preferred_venue_ids:
                eligible = True
            elif v_id in blast_capable_venues:
                dist_mi = _haversine_miles(artist_lat, artist_lon, v_lat, v_lon)
                radius = blast_capable_venues[v_id]
                if dist_mi is not None and dist_mi <= radius:
                    eligible = True
            if not eligible:
                continue
            if dist_mi is None:
                dist_mi = _haversine_miles(artist_lat, artist_lon, v_lat, v_lon)

        # Frequency check — mirror routes/gigs.py:2369 / 4577. If the
        # gig has been flagged frequency_exempt (set by the open-gig
        # blast detector at 36h/1w/2w/4w), the booking flow waives the
        # policy; reflect that here so we don't drop gigs the artist
        # CAN actually book. Otherwise: scan this artist's existing
        # bookings at this venue and drop the candidate if any falls
        # within `freq_days` of the candidate's date.
        # Jul 2026 audit (P-C3): use the frequency_exempt column already
        # in the outer SELECT (was a per-gig SELECT here — N+1).
        gig_freq_exempt = bool(gig_freq_exempt_col)
        if not gig_freq_exempt:
            freq_days = freq_by_venue.get(v_id, 0)
            if freq_days and freq_days > 0:
                from datetime import datetime as _dtf
                try:
                    d1 = _dtf.strptime(str(gdate)[:10], "%Y-%m-%d")
                    has_conflict = False
                    for other_date_str in bookings_by_venue.get(v_id, []):
                        try:
                            d2 = _dtf.strptime(other_date_str, "%Y-%m-%d")
                            if abs((d1 - d2).days) <= freq_days:
                                has_conflict = True
                                break
                        except Exception:
                            continue
                    if has_conflict:
                        continue  # filter out — artist can't book this one
                except Exception:
                    pass

        # Urgency badge: gig date within 36h
        urgent = False
        try:
            from datetime import datetime as _dt
            gig_dt = _dt.strptime(f"{str(gdate)[:10]} {(gstart or '00:00')[:5]}", "%Y-%m-%d %H:%M")
            hours_until = (gig_dt - _dt.utcnow()).total_seconds() / 3600.0
            urgent = hours_until <= 36
        except Exception:
            pass

        # Per-slot effective-pay computation (Jun 2026): apply this
        # artist's standing override per the unified rule —
        #   - Flat slot: max(slot.pay, override)
        #   - Door slot + apply_override=1: max(guarantee_cents/100, override)
        #   - Door slot + apply_override=0: guarantee stays as-is
        # So the digest renders each slot at the SAME amount the
        # booking flow would actually charge/pay.
        override_pay = override_by_venue.get(v_id)
        out_slots = []
        for s in slots_by_gig.get(int(gid), []):
            # Jul 1 2026 fix: for OPEN slots, hide any the artist can't
            # actually book — mismatched artist_type OR non-overlapping
            # band_formats OR non-overlapping styles. Uses the same
            # effective-inherits-from-gig rule as _slot_matches_artist
            # above. Booked slots always render (they're informational).
            _s_status = (s.get("status") or "").lower()
            if _s_status == "open" and not _slot_matches_artist(s):
                continue
            is_door = (s.get("deal_type") or "").lower() == "door"
            if is_door:
                gua_dollars = float(s.get("guarantee_cents") or 0) / 100.0
                if override_pay is not None and int(s.get("apply_override") or 0) == 1:
                    if override_pay > gua_dollars:
                        gua_dollars = override_pay
                eff_pay = gua_dollars
            else:
                slot_pay = float(s.get("pay") or 0)
                if override_pay is not None and override_pay > slot_pay:
                    slot_pay = override_pay
                eff_pay = slot_pay
            out_slots.append({
                "slot_number": s.get("slot_number"),
                "start_time": s.get("start_time"),
                "end_time": s.get("end_time"),
                "status": s.get("status"),
                "artist_type": s.get("artist_type"),
                "deal_type": s.get("deal_type") or "flat",
                "door_pct": int(s.get("door_pct") or 0),
                "effective_pay": eff_pay,
            })

        # Defense in depth: if after the per-slot type filter above, this
        # gig has zero open slots the artist can book AND has any slot
        # data at all (i.e. it's a slot-based gig), drop the gig entirely.
        # The gate-level filter should have already caught this, but the
        # legacy path around slot_types_by_gig's gig-level fallback might
        # let a gig through — belt-and-suspenders.
        _has_slot_data = bool(slots_by_gig.get(int(gid)))
        _has_bookable_open = any(
            (s.get("status") or "").lower() == "open" for s in out_slots
        )
        if _has_slot_data and not _has_bookable_open:
            continue

        out.append({
            "gig_id": int(gid), "venue_id": v_id,
            "date": gdate, "start_time": gstart, "end_time": gend,
            "pay": gpay, "title": gtitle, "artist_type": g_type,
            "is_multi_slot": bool(is_multi),
            "open_slot_count": int(open_count or 0),
            "venue_name": v_name, "city": v_city, "state": v_state,
            "venue_lat": v_lat, "venue_lon": v_lon,
            "distance_mi": dist_mi,
            "urgent_36h": urgent,
            "artist_id": artist_id,
            # Per-slot terms — used by the renderer to draw multi-slot
            # gigs with each slot's time + pay on its own line.
            "slots": out_slots,
            # Jul 2026: flag whether this row surfaced via the nearby-only
            # mode (i.e. artist isn't preferred at the venue). Renderer
            # uses it to append a "Request Preferred Status" CTA per row.
            "not_preferred_at_venue": mode == "nearby_non_preferred",
        })

    return out


def _haversine_miles(lat1, lon1, lat2, lon2) -> float | None:
    """Great-circle distance in statute miles. None when any input is
    None or non-numeric. Inlined here (rather than importing from
    scheduler.py) so the digest module stays independent of the
    legacy scheduler-side helpers."""
    try:
        import math
        lat1, lon1 = float(lat1), float(lon1)
        lat2, lon2 = float(lat2), float(lon2)
    except (TypeError, ValueError):
        return None
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _mark_queue_rows_sent(cursor, queue_ids: list[int]) -> None:
    if not queue_ids:
        return
    placeholders = ",".join(["?"] * len(queue_ids))
    cursor.execute(
        f"UPDATE artist_email_digest_queue SET sent_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
        queue_ids
    )


def _render_digest_email_live(*, salutation: str, rows: list[dict],
                               booked_rows: list[dict] = None,
                               nearby_rows: list[dict] = None,
                               nearby_radius_miles: int = 20,
                               multi_artist_user: bool = False) -> tuple[str, str]:
    """Single-line-per-gig digest (Jun 2026 redesign). Each row:

        Fri, Jun 26, 2026 · 14 Cannons - Thousand Oaks, CA (0.0 mi)
            · 7:00 PM – 12:00 AM · $10.00 · 2 open slots · Less Than 36 Hours!

    Rows are pre-sorted nearest-date-first by the caller. No grouping.

    When `booked_rows` is provided (Jun 2026 follow-up), a separate
    "Your upcoming gigs" section is rendered above the open-gigs
    section so the artist sees commitments + opportunities side by
    side. Each booked-slot line carries a status tag
    (Booked / Awaiting Venue Countersign / etc.) so the artist knows
    what's locked in vs in flight.
    """
    booked_rows = booked_rows or []
    def _esc(s):
        s = str(s if s is not None else "")
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def _fmt_date(d):
        try:
            dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
            return dt.strftime("%a, %b %d, %Y")
        except Exception:
            return str(d or "")

    def _fmt_time(t):
        try:
            h, m = (str(t)[:5]).split(":")
            h = int(h)
            ampm = "PM" if h >= 12 else "AM"
            h12 = h % 12 or 12
            return f"{h12}:{m} {ampm}"
        except Exception:
            return str(t or "")

    def _fmt_pay(p):
        try:
            return f"${float(p):.2f}"
        except Exception:
            return ""

    def _slot_pay_html(s):
        """Per-slot pay string honoring door deals + override-bumped
        amounts (`effective_pay` already carries the override-aware
        dollars). Returns "$15.00" for flat or "$15.00 + 50% door"
        for door deals."""
        eff = float(s.get("effective_pay") or 0)
        amt = _fmt_pay(eff)
        if (s.get("deal_type") or "").lower() == "door":
            pct = int(s.get("door_pct") or 0)
            return f"{amt} + {pct}% door"
        return amt

    def _status_badge_html(status: str) -> str:
        """Render a small colored chip describing a booked-slot's
        current state. Empty string for 'booked' (the default state —
        no need to badge the obvious)."""
        if not status:
            return ""
        s = status.lower()
        if s == "booked":
            label, bg, fg = "Booked", "#dcfce7", "#15803d"
        elif s == "pending_contract":
            label, bg, fg = "Sign Contract", "#fef3c7", "#b45309"
        elif s == "awaiting_venue_contract":
            label, bg, fg = "Awaiting Venue Countersign", "#dbeafe", "#1d4ed8"
        elif s == "pending_venue_approval":
            label, bg, fg = "Awaiting Venue Approval", "#fef3c7", "#b45309"
        else:
            return ""
        # Prefix the separator dot so the badge sits on the same line
        # as the pay column with consistent " · " spacing — matches the
        # rest of the segments in the line (Jul 2026 user request).
        return (
            " <span style='color:#9ca3af;'>·</span> "
            f"<span style='display:inline-block;padding:1px 8px;border-radius:999px;"
            f"background:{bg};color:{fg};font-size:11px;font-weight:700;"
            f"text-transform:uppercase;letter-spacing:0.04em;'>{label}</span>"
        )

    def _fmt_line(g):
        # External gigs (artist-logged, not on GigsFill) have no gig_id
        # + no venue_id — render as plain text so we don't produce
        # broken calendar/venue links. Pay column is also skipped in the
        # slot lines below since external gigs don't carry pay.
        is_external = bool(g.get("_is_external"))
        if is_external or not g.get("gig_id"):
            date_html = (
                f"<span style='color:#111827;font-weight:600;'>"
                f"{_esc(_fmt_date(g['date']))}</span>"
            )
        else:
            date_html = (
                f"<a href='https://gigsfill.com/app/artist-book-gigs.html?gig={int(g['gig_id'])}' "
                f"style='color:#111827;text-decoration:none;font-weight:600;'>"
                f"{_esc(_fmt_date(g['date']))}</a>"
            )
        if is_external or not g.get("venue_id"):
            venue_html = (
                f"<span style='color:#111827;'>{_esc(g['venue_name'])}</span>"
            )
        else:
            venue_html = (
                f"<a href='https://gigsfill.com/app/venue-profile.html?venue_id={int(g['venue_id'])}' "
                f"style='color:#111827;text-decoration:none;border-bottom:1px solid #d1d5db;'>"
                f"{_esc(g['venue_name'])}</a>"
            )
        loc_parts = [g.get("city"), g.get("state")]
        loc = ", ".join(filter(None, [_esc(x) for x in loc_parts if x]))
        dist = ""
        if g.get("distance_mi") is not None:
            try:
                dist = f" <em style='color:#6b7280;font-size:12px;'>({float(g['distance_mi']):.1f} mi)</em>"
            except Exception:
                pass
        venue_segment = f"{venue_html}"
        if loc:
            venue_segment += f" <span style='color:#6b7280;'>- {loc}{dist}</span>"
        elif dist:
            venue_segment += dist

        urgency_segment = ""
        if g.get("urgent_36h"):
            urgency_segment = "<span style='color:#dc2626;font-weight:600;'>Less Than 36 Hours!</span>"

        title_segment = ""
        if g.get("title"):
            title_segment = (
                f"<span style='color:#374151;font-style:italic;'>"
                f"\"{_esc(g['title'])}\"</span>"
            )

        # In the open-gigs section, only OPEN slots are listed (those
        # are bookable). In the booked-gigs section, list every slot
        # the artist holds — already-committed slots with their
        # contract-state badge.
        is_booked_section = bool(g.get("_is_booked_section"))
        if is_booked_section:
            slots = list(g.get("slots") or [])
        else:
            slots = [s for s in (g.get("slots") or []) if (s or {}).get("status") == "open"]

        # Jul 2026: always render header + per-slot sub-lines, even for
        # single-slot gigs. User request — a solo "Slot 1" line keeps
        # every gig visually consistent whether it has 1 slot or 4.
        # Legacy gigs missing a gig_slots row synthesize a Slot 1 from
        # the gig-level time/pay so the format still applies.
        if not slots and not is_booked_section:
            slots = [{
                "slot_number": 1,
                "start_time": g.get("start_time"),
                "end_time": g.get("end_time"),
                "effective_pay": float(g.get("pay") or 0),
                "deal_type": "flat",
                "door_pct": 0,
                "status": "open",
            }]

        # Header line with date + venue + title (and the urgency badge
        # if any slot is within 36h), then one indented sub-line per
        # slot with its time + per-slot pay (door-deal terms rendered
        # inline). Each slot is the unit an artist would actually book,
        # so each carries its own line.
        header_segments = [date_html, venue_segment]
        if title_segment:
            header_segments.append(title_segment)
        if urgency_segment:
            header_segments.append(urgency_segment)
        header_joined = " <span style='color:#9ca3af;'>·</span> ".join(header_segments)

        # Build slot lines. "Slot N · 7:00 PM – 9:00 PM · $15.00 + 50% door [BADGE]"
        # Multi-artist user (Jul 2026): render each slot's pay per
        # qualifying artist — "Slot 1 · time · Fridays Past $10.00 ·
        # Fifty Proof $15.00". Single-artist users see the unchanged
        # single-pay format.
        slot_lines = []
        for s in slots:
            time_str = _fmt_time(s.get("start_time"))
            if s.get("end_time"):
                time_str += f" – {_fmt_time(s.get('end_time'))}"
            sn = s.get("slot_number")
            slot_label = f"Slot {sn}" if sn is not None else "Slot"
            status_badge = _status_badge_html(s.get("status") or "") if is_booked_section else ""

            variants = s.get("variants") or []
            if multi_artist_user and variants:
                # In the booked section a slot has exactly one variant
                # (one artist per booked slot), so this renders a single
                # tagged pill — telling the user which of their artists
                # holds that slot. In the open section a slot can have
                # 1+ variants and we list each with its pay.
                pay_parts = []
                for v in variants:
                    variant_slot = {**s, "effective_pay": v.get("effective_pay") or 0}
                    pay_parts.append(
                        f"<b style='color:#0f172a;'>{_esc(v.get('artist_name') or '')}</b> "
                        f"<span style='color:#111827;font-weight:600;'>{_esc(_slot_pay_html(variant_slot))}</span>"
                    )
                pay_html = " <span style='color:#9ca3af;'>·</span> ".join(pay_parts)
            else:
                pay_html = (
                    f"<span style='color:#111827;font-weight:600;'>{_esc(_slot_pay_html(s))}</span>"
                )

            # External gigs skip the pay segment — the artist didn't
            # book them through GigsFill, we have no pay data to show.
            pay_segment = ("" if is_external else
                f" <span style='color:#9ca3af;'>·</span> {pay_html}")
            slot_lines.append(
                "<div style='padding:3px 0 3px 18px;font-size:13.5px;color:#374151;'>"
                f"<span style='color:#6b7280;font-weight:600;'>{_esc(slot_label)}</span>"
                f" <span style='color:#9ca3af;'>·</span> "
                f"<span>{_esc(time_str)}</span>"
                f"{pay_segment}"
                f"{status_badge}"
                "</div>"
            )

        return (
            "<div style='padding:10px 0;border-bottom:1px solid #f3f4f6;font-size:14px;color:#111827;line-height:1.5;'>"
            f"<div>{header_joined}</div>"
            f"{''.join(slot_lines)}"
            "</div>"
        )

    # Mark booked rows so _fmt_line can render a status tag per slot.
    # Done here (not at fetch time) so the renderer stays the single
    # source of truth for "is this a commitment vs an opportunity"
    # presentation.
    for br in booked_rows:
        br["_is_booked_section"] = True
    for orow in rows:
        orow["_is_booked_section"] = False

    # Subject — combine open + booked counts when both exist.
    open_count = len(rows)
    booked_count = len(booked_rows)
    open_venues = {int(r["venue_id"]) for r in rows} if rows else set()
    if booked_count and open_count:
        subj = (
            f"{open_count} Open Gig{'s' if open_count != 1 else ''} + "
            f"{booked_count} Booking{'s' if booked_count != 1 else ''} Next 4 Weeks"
        )
    elif booked_count:
        subj = (
            f"{booked_count} Booked Gig{'s' if booked_count != 1 else ''} Next 4 Weeks"
        )
    else:
        subj = (
            f"{open_count} Open Gig{'s' if open_count != 1 else ''} "
            f"at {len(open_venues)} Venue{'s' if len(open_venues) != 1 else ''}"
        )

    nearby_rows = nearby_rows or []
    open_lines_html = "".join(_fmt_line(g) for g in rows)
    booked_lines_html = "".join(_fmt_line(g) for g in booked_rows)
    nearby_lines_html = "".join(_fmt_line(g) for g in nearby_rows)

    section_heading = (
        "font-size:13px;font-weight:700;color:#6b7280;"
        "text-transform:uppercase;letter-spacing:0.06em;"
        "margin:6px 0 8px 0;"
    )

    booked_section_html = ""
    if booked_lines_html:
        booked_section_html = (
            f"<div style='{section_heading}'>Your upcoming gigs ({booked_count})</div>"
            f"{booked_lines_html}"
            "<div style='height:14px;'></div>"
        )

    open_section_html = ""
    if open_lines_html:
        open_section_html = (
            f"<div style='{section_heading}'>Open gigs you can book ({open_count})</div>"
            f"{open_lines_html}"
        )

    # Jul 2026: new "Nearby — Preferred Status not yet granted" section.
    # These are gigs within nearby_radius_miles of the artist at venues
    # where the artist ISN'T on the preferred list. Shown after the main
    # "you can book" list so it doesn't crowd out the primary CTA. Each
    # row already carries `not_preferred_at_venue=True` from the fetcher.
    nearby_count = len(nearby_rows)
    nearby_section_html = ""
    if nearby_lines_html:
        nearby_section_html = (
            "<div style='height:14px;'></div>"
            f"<div style='{section_heading}'>"
            f"Nearby open gigs — you're not a Preferred Artist yet ({nearby_count})"
            "</div>"
            "<p style='margin:0 0 10px 0;font-size:13px;line-height:1.55;color:#6b7280;'>"
            f"These gigs are within {int(nearby_radius_miles)} miles of you and match "
            "your artist type, but you haven't been approved as a Preferred Artist at "
            "these venues yet. Open a venue's profile to <b>Request Preferred Status</b> "
            "— once the venue approves, you'll be able to book their gigs directly from "
            "your calendar."
            "</p>"
            f"{nearby_lines_html}"
        )

    intro_text = (
        f"{_esc(salutation)}, here's your next 4 weeks at a glance — "
        "what you've already got booked, and what's still open."
        if booked_lines_html
        else f"{_esc(salutation)}, here's a list of open gigs within the next 4 weeks:"
    )

    body = f"""\
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 28px 40px;">
<p style="margin: 0 0 18px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">{intro_text}</p>
{booked_section_html}
{open_section_html}
{nearby_section_html}
<p style="margin: 20px 0 0 0; font-size: 13px; color: #6b7280; line-height: 1.5;">To change which notifications you receive — including turning this digest off — go to <a href="https://gigsfill.com/app/user-profile.html?tab=email" style="color: #3b82f6;">your notification preferences</a>.</p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
"""
    return subj, body


def _render_digest_email(*, artist_name: str, rows: list[dict],
                         artist_lat=None, artist_lon=None) -> tuple[str, str]:
    """Render subject + HTML body. Two grouping modes:

      - Single-artist users (the common case): one section per venue.
      - Multi-artist users (a user owns multiple artist profiles, e.g.
        a band alias + a DJ alias): top-level groups by ARTIST so the
        user sees "for Fridays Past: …", "for DJ Fridays: …". Within
        each artist, gigs are still grouped by venue.

    When artist coords are provided, the venue line shows "(X.X mi)"
    after the city — computed against each venue's geo.
    """
    # First pass: partition rows by artist_id. If there's only one,
    # we render single-section style (matches the original behavior).
    # Sort by artist_id so the multi-artist sections come out in a
    # stable order, and so the salutation below mirrors the in-body
    # ordering ("For Fridays Past" then "For Fifty Proof"
    # ↔ "Fridays Past + Fifty Proof, here's a list…").
    by_artist: dict[int, dict] = {}
    for r in sorted(rows, key=lambda r: int(r.get("artist_id") or 0)):
        aid = int(r.get("artist_id") or 0)
        a = by_artist.setdefault(aid, {
            "artist_name": r.get("artist_name") or artist_name,
            "by_venue": {},
        })
        v = a["by_venue"].setdefault(r["venue_id"], {
            "venue_name": r["venue_name"],
            "city": r["city"], "state": r["state"],
            "venue_lat": r.get("venue_lat"), "venue_lon": r.get("venue_lon"),
            "gigs": []
        })
        v["gigs"].append(r)

    multi_artist = len(by_artist) > 1

    # Backwards-compat alias used by the existing rendering loop below.
    # Single-artist path collapses to one venue map.
    by_venue: dict[int, dict] = (
        next(iter(by_artist.values()))["by_venue"] if not multi_artist else {}
    )

    # Sort gigs within each venue
    for v in by_venue.values():
        v["gigs"].sort(key=lambda g: (str(g["date"] or ""), str(g["start_time"] or "")))

    # Subject — title-cased per user preference, no trailing CTA.
    gig_count = len(rows)
    venue_count = len(by_venue)
    subj = (
        f"{gig_count} Open Gig{'s' if gig_count != 1 else ''} "
        f"at {venue_count} Venue{'s' if venue_count != 1 else ''}"
    )

    # Body
    def _esc(s):
        s = str(s if s is not None else "")
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def _fmt_date(d):
        try:
            dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
            return dt.strftime("%a, %b %d, %Y")
        except Exception:
            return str(d or "")

    def _fmt_time(t):
        try:
            h, m = (str(t)[:5]).split(":")
            h = int(h)
            ampm = "PM" if h >= 12 else "AM"
            h12 = h % 12 or 12
            return f"{h12}:{m} {ampm}"
        except Exception:
            return str(t or "")

    def _fmt_pay(p):
        try:
            return f"${float(p):.2f}"
        except Exception:
            return ""

    # Render each venue as a section. Inside the section, gigs sit in a
    # 4-column table so date / time / pay / urgency-tag line up
    # vertically across rows. Column widths are explicit so longer pay
    # strings ($1,250.00) don't push the urgency tag around.
    def _render_venue_section(v):
        loc = ", ".join(filter(None, [_esc(v.get("city")), _esc(v.get("state"))]))
        dist_html = ""
        dist_mi = _haversine_miles(artist_lat, artist_lon, v.get("venue_lat"), v.get("venue_lon"))
        if dist_mi is not None and dist_mi >= 0:
            dist_html = (
                f" <em style='color:#6b7280;font-size:12px;'>"
                f"({dist_mi:.1f} mi)</em>"
            )
        venue_id = v["gigs"][0]["venue_id"] if v["gigs"] else None
        venue_link_open = (
            f"<a href='https://gigsfill.com/app/venue-profile.html?venue_id={int(venue_id)}' "
            f"style='color:#111827;text-decoration:none;border-bottom:1px solid #d1d5db;'>"
            if venue_id else "<span>"
        )
        venue_link_close = "</a>" if venue_id else "</span>"
        venue_header = (
            f"<strong>{venue_link_open}{_esc(v['venue_name'])}{venue_link_close}</strong>"
            + (f" <span style='color:#6b7280;'>· {loc}{dist_html}</span>" if loc else dist_html)
        )
        gig_rows = []
        for g in v["gigs"]:
            right_parts = []
            if g.get("title"):
                right_parts.append(
                    f"<span style='color:#374151;font-style:italic;'>"
                    f"\"{_esc(g['title'])}\"</span>"
                )
            if g.get("is_multi_slot") and g.get("open_slot_count"):
                _n = int(g["open_slot_count"])
                right_parts.append(
                    f"<span style='color:#6b7280;'>"
                    f"{_n} open slot{'s' if _n != 1 else ''}</span>"
                )
            if g["notification_key"] == "open_gig_36h":
                right_parts.append(
                    "<span style='color:#dc2626;font-weight:600;'>"
                    "Less Than 36 Hours!</span>"
                )
            right_html = (
                "<span style='font-size:13px;'>"
                + " <span style='color:#9ca3af;'>·</span> ".join(right_parts)
                + "</span>"
            ) if right_parts else ""
            link_open = (
                f"<a href='https://gigsfill.com/app/artist-book-gigs.html?gig={g['gig_id']}' "
                f"style='color:#111827;text-decoration:none;'>"
            )
            time_str = _fmt_time(g["start_time"])
            if g.get("end_time"):
                time_str += f" – {_fmt_time(g['end_time'])}"
            gig_rows.append(
                "<tr>"
                f"<td style='padding:5px 14px 5px 0;font-size:14px;color:#111827;white-space:nowrap;'>{link_open}{_esc(_fmt_date(g['date']))}</a></td>"
                f"<td style='padding:5px 14px 5px 0;font-size:14px;color:#374151;white-space:nowrap;'>{link_open}{_esc(time_str)}</a></td>"
                f"<td style='padding:5px 14px 5px 0;font-size:14px;color:#374151;white-space:nowrap;'>{link_open}{_esc(_fmt_pay(g['pay']))}</a></td>"
                f"<td style='padding:5px 0;white-space:nowrap;'>{right_html}</td>"
                "</tr>"
            )
        return (
            "<div style='margin:18px 0;padding-bottom:14px;border-bottom:1px solid #e5e7eb;'>"
            f"<div style='font-size:15px;margin-bottom:8px;'>{venue_header}</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0' border='0' style='border-collapse:collapse;'>"
            f"{''.join(gig_rows)}"
            "</table>"
            "</div>"
        )

    # Salutation lists every artist that actually appears in this
    # digest — was previously just the user's first artist name
    # (from the meta query upstream), which surfaced a confusing
    # "Fridays Past, here's a list of open gigs…" greeting on a
    # digest that only contained Fifty Proof's row. (Jun 2026 user
    # report — multi-artist user where the digest's rows belong to
    # the not-first artist.) For single-artist digests the result is
    # unchanged; for multi-artist it joins names with " + ".
    _artist_names_in_digest = [
        a.get("artist_name") or "there" for a in by_artist.values()
    ]
    if not _artist_names_in_digest:
        _salutation_names = artist_name
    elif len(_artist_names_in_digest) == 1:
        _salutation_names = _artist_names_in_digest[0]
    else:
        _salutation_names = " + ".join(_artist_names_in_digest)

    sections = []
    if multi_artist:
        # Multi-artist user: outer group per artist with a header line
        # ("for Fridays Past"), then the venue sections nested under.
        # Same gig may appear under two artists if it triggered both —
        # we don't dedup (per user note: they may be different types
        # and the artist column makes the relevance explicit).
        for a in by_artist.values():
            sub_sections = "".join(_render_venue_section(v) for v in a["by_venue"].values())
            sections.append(
                "<div style='margin:24px 0 8px 0;font-size:14px;color:#6b7280;"
                "text-transform:uppercase;letter-spacing:0.05em;font-weight:700;'>"
                f"For {_esc(a['artist_name'])}"
                "</div>"
                f"{sub_sections}"
            )
    else:
        for v in by_venue.values():
            sections.append(_render_venue_section(v))

    body = f"""\
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 28px 40px;">
<p style="margin: 0 0 12px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">{_esc(_salutation_names)}, here's a list of open gigs within the next month:</p>
{''.join(sections)}
<p style="margin: 20px 0 0 0; font-size: 13px; color: #6b7280; line-height: 1.5;">To change which notifications you receive — including turning this digest off — go to <a href="https://gigsfill.com/app/user-profile.html?tab=email" style="color: #3b82f6;">your notification preferences</a>.</p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
"""
    return subj, body


def _due_users_live(cursor, digest_hour: int) -> list[int]:
    """Return user_ids whose local-time hour == digest_hour. Live-snapshot
    digest (Jun 2026) doesn't need a queue — we scan every user with at
    least one artist (the digest is artist-bound) and pick the ones whose
    artist's state-derived timezone says the hour matches.
    """
    rows = cursor.execute("""
        SELECT DISTINCT u.id, a.state
        FROM users u
        JOIN artists a ON a.user_id = u.id AND a.deleted_at IS NULL
    """).fetchall()
    if not rows:
        return []
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return []
    from backend.utils import US_STATE_TIMEZONES

    platform_tz = "America/Los_Angeles"
    try:
        row = cursor.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'"
        ).fetchone()
        if row and row[0]:
            platform_tz = row[0]
    except Exception:
        pass

    out: list[int] = []
    seen = set()
    for r in rows:
        uid = r[0]
        if uid in seen:
            continue
        seen.add(uid)
        state = (r[1] or "").strip().upper()
        tz_str = US_STATE_TIMEZONES.get(state) or platform_tz
        try:
            tz = ZoneInfo(tz_str)
            local_hour = datetime.now(tz).hour
        except Exception:
            continue
        if local_hour == digest_hour:
            out.append(uid)
    return out


def _merge_gig_variants(bucket: dict, artist_name: str,
                         gigs: list[dict], skip_ids: set[int] | None = None) -> None:
    """Multi-artist digest support (Jul 2026): a user with multiple
    artists can have both qualify for the same gig — often at different
    per-artist pay via preferred_artists.pay_dollars_override. Older
    dedup-by-gig-id logic kept whichever artist ran first (lowest
    artist_id) and silently dropped the other's pay. That masked real
    override money the user was leaving on the table.

    Now: instead of dedup, keep every artist's variant. For each gig,
    upsert a row keyed by gig_id and slots keyed by slot_number; append
    this artist's (name, effective_pay) to the slot's `variants` list.
    Gig-level fields come from the first artist to contribute — they're
    invariant across artists for the same gig.
    """
    skip_ids = skip_ids or set()
    for g in gigs:
        gid = g["gig_id"]
        if gid in skip_ids:
            continue
        entry = bucket.get(gid)
        if entry is None:
            entry = {k: v for k, v in g.items() if k != "slots"}
            entry["_slots_by_num"] = {}
            bucket[gid] = entry
        for s in g.get("slots") or []:
            sn = s.get("slot_number")
            key = sn if sn is not None else -1
            slot = entry["_slots_by_num"].get(key)
            if slot is None:
                # slot-level fields (deal_type, door_pct, times, status)
                # are identical across artists for the same slot; only
                # effective_pay varies. Copy once, then attach variants.
                slot = {k: v for k, v in s.items() if k != "effective_pay"}
                slot["variants"] = []
                entry["_slots_by_num"][key] = slot
            slot["variants"].append({
                "artist_name": artist_name,
                "effective_pay": float(s.get("effective_pay") or 0),
            })


def _finalize_variant_gigs(bucket: dict) -> list[dict]:
    """Flatten the per-gig slot map, sort slots by slot_number, and seed
    each slot's `effective_pay` to the first variant's value (renderer
    uses that field when the user has only one artist, keeping the
    single-artist path unchanged)."""
    out = []
    for _gid, entry in bucket.items():
        slots = list(entry.pop("_slots_by_num").values())
        slots.sort(key=lambda x: (x.get("slot_number") or 0))
        for s in slots:
            variants = s.get("variants") or []
            # Sort variants by effective_pay DESC so the highest-paying
            # opportunity for this user shows first — right where the eye
            # lands. Stable sort preserves artist-id order on ties.
            variants.sort(key=lambda v: -float(v.get("effective_pay") or 0))
            if variants:
                s["effective_pay"] = variants[0]["effective_pay"]
        entry["slots"] = slots
        out.append(entry)
    out.sort(key=lambda g: (str(g.get("date") or ""), str(g.get("start_time") or "")))
    return out


def build_digest_for_user(cursor, user_id: int) -> dict:
    """Build (but don't send) the live digest for one user. Returns
    {"ok": bool, "reason": str|None, "email": str|None, "subject": str|None,
     "body": str|None, "counts": {"open","booked","nearby"}}.

    Extracted (Jul 2026) so `send_daily_artist_digest` (scheduler tick)
    and `admin_force_send_digest` (admin trigger) both render through
    the same live pathway — previously the admin trigger used the
    legacy queue-based renderer and its output diverged from what real
    users received. This is now the single source of truth for
    live-digest content.
    """
    # Pull this user's artists — exclude tombstones so we don't iterate
    # a dead artist. Include artists this user is a delegated team
    # member of via entity_users (not just direct owner) — a band's
    # secondary booker should also get the digest for their team's row.
    artists = cursor.execute(
        """SELECT DISTINCT a.id, a.name
             FROM artists a
             LEFT JOIN entity_users eu ON eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id = ?
             WHERE a.deleted_at IS NULL AND (a.user_id = ? OR eu.user_id IS NOT NULL)
             ORDER BY a.id ASC""",
        (user_id, user_id,)
    ).fetchall()
    if not artists:
        return {"ok": False, "reason": "no artists", "email": None,
                "subject": None, "body": None,
                "counts": {"open": 0, "booked": 0, "nearby": 0}}
    email_row = cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not email_row or not email_row[0]:
        return {"ok": False, "reason": "no email", "email": None,
                "subject": None, "body": None,
                "counts": {"open": 0, "booked": 0, "nearby": 0}}
    artist_email = email_row[0]

    # Live-query open + booked + nearby gigs for each artist. Multi-artist
    # users (Jul 2026): merge every artist's slot-level variants under
    # one gig row so each slot lists which of the user's artists
    # qualifies + that artist's effective pay.
    opens_by_gig: dict[int, dict] = {}
    nearby_by_gig: dict[int, dict] = {}
    booked_by_gig: dict[int, dict] = {}
    external_rows: list[dict] = []  # external gigs bypass variant-merge
    contributing_artist_names: list[str] = []
    _NEARBY_RADIUS_MI = 20
    for art_id, art_name in artists:
        opens = _fetch_artist_open_gigs_live(cursor, int(art_id), window_days=28)
        books = _fetch_artist_booked_gigs_live(cursor, int(art_id), window_days=28)
        nearby = _fetch_artist_open_gigs_live(
            cursor, int(art_id), window_days=28,
            mode="nearby_non_preferred",
            nearby_radius_miles=_NEARBY_RADIUS_MI,
        )
        exts = _fetch_artist_external_gigs_for_digest(cursor, int(art_id), window_days=28)
        if opens or books or nearby or exts:
            contributing_artist_names.append(art_name)
        _merge_gig_variants(opens_by_gig, art_name or "", opens)
        _merge_gig_variants(nearby_by_gig, art_name or "", nearby,
                            skip_ids=set(opens_by_gig.keys()))
        _merge_gig_variants(booked_by_gig, art_name or "", books)
        # External gigs don't have real gig_ids so they'd all collide
        # on None if we merged them. They're already artist-scoped and
        # never have per-artist variants, so append verbatim.
        for e in exts:
            e["_owner_artist_name"] = art_name or ""
        external_rows.extend(exts)

    all_open = _finalize_variant_gigs(opens_by_gig)
    all_nearby = _finalize_variant_gigs(nearby_by_gig)
    all_booked = _finalize_variant_gigs(booked_by_gig)
    # Merge external gigs into the booked section + re-sort by date.
    if external_rows:
        all_booked = sorted(
            all_booked + external_rows,
            key=lambda g: (str(g.get("date") or ""), str(g.get("start_time") or ""))
        )

    if not all_open and not all_booked and not all_nearby:
        return {"ok": False, "reason": "nothing to send", "email": artist_email,
                "subject": None, "body": None,
                "counts": {"open": 0, "booked": 0, "nearby": 0}}

    # Salutation: list the artists whose qualified gigs are in this
    # digest (may differ from "all of the user's artists").
    if not contributing_artist_names:
        salutation = artists[0][1] or "there"
    elif len(contributing_artist_names) == 1:
        salutation = contributing_artist_names[0]
    else:
        salutation = " + ".join(contributing_artist_names)

    subj, body = _render_digest_email_live(
        salutation=salutation, rows=all_open, booked_rows=all_booked,
        nearby_rows=all_nearby, nearby_radius_miles=_NEARBY_RADIUS_MI,
        multi_artist_user=(len(artists) > 1),
    )
    return {
        "ok": True, "reason": None, "email": artist_email,
        "subject": subj, "body": body,
        "counts": {"open": len(all_open), "booked": len(all_booked),
                   "nearby": len(all_nearby)},
        "venue_ids": (
            {g['venue_id'] for g in all_open}
            | {g['venue_id'] for g in all_booked}
        ),
    }


def send_daily_artist_digest(cursor, smtp_config) -> int:
    """Send the morning digest for any user whose local hour matches the
    configured digest_hour. Returns the number of emails sent.

    LIVE-SNAPSHOT model (Jun 2026 rewrite): instead of replaying a queue of
    window-triggered events (4w / 2w / 1w / 36h), the digest now does a
    live query across every OPEN gig in the next 4 weeks at venues the
    artist is either preferred-approved at OR within blast-radius of (when
    the venue has blast_all_enabled for any window). Per-artist filters:
    artist_type match, no blackout, no ban, not already booked. Gigs are
    listed in a single-line-per-gig format sorted nearest-date-first,
    not grouped by venue. The legacy queue is still drained for cleanup
    purposes so the table doesn't grow forever.
    """
    enabled = cursor.execute(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'open_gig_daily_digest_enabled'"
    ).fetchone()
    if not (enabled and (enabled[0] or "").lower() in ("true", "1")):
        return 0
    hour_row = cursor.execute(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'open_gig_daily_digest_hour'"
    ).fetchone()
    try:
        digest_hour = int((hour_row[0] if hour_row else "6").strip())
    except Exception:
        digest_hour = 6

    due_user_ids = _due_users_live(cursor, digest_hour)
    if not due_user_ids:
        return 0

    from backend.scheduler import send_email

    # 2026-08-08 audit fix (finding #10): duplicate-send protection.
    # `_scheduler_loop`'s `last_email_run` is an in-process variable
    # initialized to 0, so the FIRST tick of any newly-started process
    # trivially passes the `now - last_email_run >= 3600` gate. If
    # `gigsfill-scheduler.service` is restarted (deploy, crash, admin
    # kick) during the digest hour, every eligible user gets a second
    # (identical) digest email. Guard here with a durable per-user
    # per-day marker keyed off artist_email_digest_snapshot — its
    # sent_at is set on successful send, so a user already emailed
    # today is silently skipped. Uses the same platform timezone as
    # the "due user" calc so day boundaries line up.
    # 2026-08-21: was `from backend.services.notification_service import
    # get_platform_timezone` — that helper doesn't exist in
    # notification_service; the real one lives in backend/utils.py and
    # takes a `db` session, not a raw cursor. The scheduler passes a
    # sqlite3 cursor here (not SQLAlchemy), so read the setting directly.
    # Silent-broken since Aug 18 — no digest emails went out for 3+ days.
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Zi
    try:
        _tz_row = cursor.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'"
        ).fetchone()
        _tz_name = (_tz_row[0] if _tz_row else None) or "America/Los_Angeles"
        _tz = _Zi(_tz_name)
    except Exception:
        _tz = _Zi("UTC")
    _today_local = _dt.now(_tz).strftime("%Y-%m-%d")

    sent = 0
    for user_id in due_user_ids:
        # Master toggle: user opt-out from the daily digest
        _master = cursor.execute(
            "SELECT enabled FROM email_preferences WHERE user_id = ? AND notification_type = 'open_gig_daily_digest'",
            (user_id,)
        ).fetchone()
        if _master and not _master[0]:
            continue

        # Duplicate-send guard: skip when this user already got a
        # digest today (platform local calendar date). Cheap indexed
        # lookup — the snapshot table is indexed on (user_id, sent_at DESC).
        try:
            _dup = cursor.execute(
                "SELECT 1 FROM artist_email_digest_snapshot "
                "WHERE user_id = ? AND DATE(sent_at) = ? LIMIT 1",
                (user_id, _today_local)
            ).fetchone()
            if _dup:
                logger.info(f"[DIGEST] skip user={user_id} — already sent today ({_today_local})")
                continue
        except Exception as _dg:
            # Table missing / query error: don't block the send — this
            # is a safety net, not a hard gate.
            logger.warning(f"[DIGEST] duplicate-send check failed user={user_id}: {_dg}")

        result = build_digest_for_user(cursor, user_id)
        if not result["ok"]:
            continue
        try:
            ok = send_email(smtp_config, result["email"], result["subject"], result["body"])
        except Exception as e:
            logger.error(
                f"[DIGEST] EXCEPTION sending to user={user_id} email={result['email']} "
                f"counts={result['counts']} — {e}",
                exc_info=True,
            )
            continue
        if ok:
            sent += 1
            # Drain any leftover queue rows for this user so the legacy
            # queue doesn't accrete (no longer the source of truth, but
            # historic rows might still exist).
            try:
                cursor.execute(
                    "UPDATE artist_email_digest_queue SET sent_at = CURRENT_TIMESTAMP WHERE user_id = ? AND sent_at IS NULL",
                    (user_id,)
                )
                cursor.connection.commit()
            except Exception:
                pass
            # 2026-08-08 audit fix (finding #9): snapshot the exact
            # subject + body we just sent so /api/me/digest-preview and
            # /api/me/digest-resend can serve the true historical
            # content instead of reconstructing against current (post-
            # edit) gig fields with the legacy per-venue renderer.
            try:
                _counts = result.get("counts") or {}
                cursor.execute(
                    "INSERT INTO artist_email_digest_snapshot "
                    "(user_id, subject, body_html, open_count, booked_count, nearby_count) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, result["subject"], result["body"],
                     int(_counts.get("open") or 0),
                     int(_counts.get("booked") or 0),
                     int(_counts.get("nearby") or 0))
                )
                cursor.connection.commit()
            except Exception as _se:
                logger.warning(f"[DIGEST] snapshot insert failed user={user_id}: {_se}")
            logger.info(
                f"[DIGEST] LIVE sent to user={user_id} email={result['email']} "
                f"counts={result['counts']} venues={len(result.get('venue_ids') or set())}"
            )
        else:
            logger.error(
                f"[DIGEST] SMTP FAILURE for user={user_id} email={result['email']} "
                f"counts={result['counts']}"
            )
    return sent


def prune_old_queue_rows(cursor, days: int = 30) -> int:
    """Drop sent rows older than `days` so the queue doesn't grow
    forever. Unsent rows are NEVER pruned — they'll get sent the next
    time their owner's local hour matches.
    """
    try:
        # Audit fix (Jul 1 2026 R6): `datetime('now', ?)` with a
        # parameterized interval is a SQLite-only form that the
        # `_sqlite_to_pg` regex can't rewrite (regex requires the interval
        # literal to be inline). Compute the cutoff in Python and pass
        # a plain timestamp — works on both engines.
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.utcnow() - _td(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "DELETE FROM artist_email_digest_queue "
            "WHERE sent_at IS NOT NULL AND sent_at < ?",
            (cutoff,)
        )
        return cursor.rowcount or 0
    except Exception as e:
        logger.warning(f"prune_old_queue_rows failed: {e}")
        return 0

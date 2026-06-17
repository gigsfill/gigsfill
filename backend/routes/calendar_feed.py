"""
iCal calendar feed — read-only export of a user's booked gigs.

Token-gated (no session cookie required). The token IS the auth: it's a
random UUID stamped on the `users` row when the user opens the calendar
settings panel for the first time. Anyone with the URL can read the
feed, but nobody can write to it.

Endpoint:
    GET /api/calendar/{token}.ics

Returns:
    text/calendar — RFC 5545 VCALENDAR document with one VEVENT per
    booked gig the user is connected to. For an artist-owner, every
    gig where any of their artists is booked. For a venue-owner, every
    booked gig at their venues. Multi-user accounts (entity_users) are
    handled the same way as primary owners.

Subscribing instructions for the user:
    Google Calendar → Other calendars → From URL → paste the feed URL.
    Apple Calendar  → File → New Calendar Subscription → paste the URL.
    Outlook         → Add calendar → Subscribe from web → paste the URL.

Both clients re-poll the feed periodically (typically every 1–24 hours
depending on the client). New bookings and cancellations propagate
automatically.
"""

import uuid
from datetime import datetime, timedelta
from backend.utils import US_STATE_TIMEZONES, get_platform_timezone


# Hand-rolled VTIMEZONE blocks for the IANA zones that ever appear in
# US_STATE_TIMEZONES. We emit one block per unique zone the user's gigs
# touch — Google / Apple / Outlook need the VTIMEZONE in the calendar
# header before they'll honor `DTSTART;TZID=X:...` on events. These are
# the standard US DST rules (second Sunday of March → first Sunday of
# November) plus stationary-offset zones for Arizona, Hawaii, and the
# US territories. Encoded as raw VTIMEZONE bodies — RFC 5545 §3.6.5.
_VTZ_DST_US = (
    "BEGIN:STANDARD\r\n"
    "DTSTART:19701101T020000\r\n"
    "TZOFFSETFROM:{daylight_offset}\r\n"
    "TZOFFSETTO:{standard_offset}\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
    "TZNAME:{standard_name}\r\n"
    "END:STANDARD\r\n"
    "BEGIN:DAYLIGHT\r\n"
    "DTSTART:19700308T020000\r\n"
    "TZOFFSETFROM:{standard_offset}\r\n"
    "TZOFFSETTO:{daylight_offset}\r\n"
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
    "TZNAME:{daylight_name}\r\n"
    "END:DAYLIGHT\r\n"
)
_VTZ_FIXED = (
    "BEGIN:STANDARD\r\n"
    "DTSTART:19700101T000000\r\n"
    "TZOFFSETFROM:{offset}\r\n"
    "TZOFFSETTO:{offset}\r\n"
    "TZNAME:{name}\r\n"
    "END:STANDARD\r\n"
)

# All IANA zones referenced from US_STATE_TIMEZONES (plus the platform
# default fallback). Each maps to a VTIMEZONE body string. Keeping these
# in code (vs. relying on a library like ics or icalendar) avoids adding
# a dependency for a feature that only needs ~10 zones.
_VTIMEZONE_BODIES = {
    "America/New_York":     _VTZ_DST_US.format(standard_offset="-0500", daylight_offset="-0400", standard_name="EST", daylight_name="EDT"),
    "America/Chicago":      _VTZ_DST_US.format(standard_offset="-0600", daylight_offset="-0500", standard_name="CST", daylight_name="CDT"),
    "America/Denver":       _VTZ_DST_US.format(standard_offset="-0700", daylight_offset="-0600", standard_name="MST", daylight_name="MDT"),
    "America/Boise":        _VTZ_DST_US.format(standard_offset="-0700", daylight_offset="-0600", standard_name="MST", daylight_name="MDT"),
    "America/Detroit":      _VTZ_DST_US.format(standard_offset="-0500", daylight_offset="-0400", standard_name="EST", daylight_name="EDT"),
    "America/Indiana/Indianapolis": _VTZ_DST_US.format(standard_offset="-0500", daylight_offset="-0400", standard_name="EST", daylight_name="EDT"),
    "America/Los_Angeles":  _VTZ_DST_US.format(standard_offset="-0800", daylight_offset="-0700", standard_name="PST", daylight_name="PDT"),
    "America/Anchorage":    _VTZ_DST_US.format(standard_offset="-0900", daylight_offset="-0800", standard_name="AKST", daylight_name="AKDT"),
    # Fixed-offset zones (no DST).
    "America/Phoenix":      _VTZ_FIXED.format(offset="-0700", name="MST"),
    "Pacific/Honolulu":     _VTZ_FIXED.format(offset="-1000", name="HST"),
    "America/Puerto_Rico":  _VTZ_FIXED.format(offset="-0400", name="AST"),
    "Pacific/Guam":         _VTZ_FIXED.format(offset="+1000", name="ChST"),
    "Pacific/Pago_Pago":    _VTZ_FIXED.format(offset="-1100", name="SST"),
    "Pacific/Saipan":       _VTZ_FIXED.format(offset="+1000", name="ChST"),
}


def _tz_for_state(state):
    """Map a US state code → IANA timezone string. Falls back to the
    platform default for unknown / blank states."""
    if not state:
        return None
    return US_STATE_TIMEZONES.get(str(state).strip().upper())


def _build_vtimezone(tz_name):
    """Render a VTIMEZONE block for the given IANA zone, or '' if we
    don't have a body for it (event will fall back to floating time
    and the calendar app will use the subscriber's local TZ)."""
    body = _VTIMEZONE_BODIES.get(tz_name)
    if not body:
        return ""
    return f"BEGIN:VTIMEZONE\r\nTZID:{tz_name}\r\n{body}END:VTIMEZONE\r\n"
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy import text

from backend.db import get_db
from backend.routes.auth import get_current_user

router = APIRouter()


def _ensure_user_token(db, user_id):
    """Return the user's calendar_token, minting one if missing."""
    row = db.execute(
        text("SELECT calendar_token FROM users WHERE id = :uid"),
        {"uid": user_id}
    ).mappings().first()
    if row and row.get("calendar_token"):
        return row["calendar_token"]
    new_token = uuid.uuid4().hex
    db.execute(
        text("UPDATE users SET calendar_token = :tok WHERE id = :uid"),
        {"tok": new_token, "uid": user_id}
    )
    db.commit()
    return new_token


def _ics_escape(s):
    """Escape special chars per RFC 5545."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def _fold_line(line):
    """Fold long lines to 75 octets per RFC 5545. Continuation lines
    are indented with a single space."""
    out = []
    while len(line.encode("utf-8")) > 75:
        # Walk byte-by-byte to find the safe 75-octet split point
        cut = 75
        while len(line[:cut].encode("utf-8")) > 75:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def _build_vevent(gig, tz_name=None):
    """Render one gig as a VEVENT block. tz_name (IANA, e.g.
    'America/Los_Angeles') anchors DTSTART / DTEND so subscribers in
    other timezones see the gig at the venue's wall clock time, not their
    own. When None, the event uses floating time (the subscriber's local
    TZ wins) — that's the legacy pre-TZ behavior, kept as a safety net
    for venues whose state we can't map to a zone."""
    gig_id = gig["gig_id"]
    date_str = str(gig["date"])[:10].replace("-", "")
    start = (gig.get("start_time") or "00:00").replace(":", "") + "00"
    end = (gig.get("end_time") or "23:59").replace(":", "") + "00"
    # Overnight slots (e.g. 23:00 → 02:00) — bump end date by 1 day.
    end_date_str = date_str
    if (gig.get("start_time") or "") > (gig.get("end_time") or ""):
        try:
            d = datetime.strptime(date_str, "%Y%m%d") + timedelta(days=1)
            end_date_str = d.strftime("%Y%m%d")
        except Exception:
            pass

    title_parts = []
    if gig.get("title"):
        title_parts.append(gig["title"])
    if gig.get("artist_name"):
        title_parts.append(f"with {gig['artist_name']}")
    elif gig.get("venue_name"):
        title_parts.append(f"@ {gig['venue_name']}")
    summary = " ".join(title_parts) or f"Gig {gig_id}"

    description_lines = []
    if gig.get("venue_name"):
        description_lines.append(f"Venue: {gig['venue_name']}")
    if gig.get("artist_name"):
        description_lines.append(f"Artist: {gig['artist_name']}")
    if gig.get("city"):
        description_lines.append(f"City: {gig['city']}, {gig.get('state','')}")
    # NOTE — DO NOT add a "Pay:" line here.
    # This feed is served at /api/calendar/{token}.ics with the token as
    # the only credential. Users routinely paste the URL into their public
    # website / social bio / shared band calendar so subscribers can see
    # the show schedule. Including pay (whether flat "$60.00" or door
    # split terms) on a publishable feed leaks sensitive deal info to
    # any subscriber — competitors, fans, anyone with the URL.
    # The owner can always see pay terms in the GigsFill app itself;
    # the iCal feed is intentionally a "schedule view", not a "financial
    # view". If we later want owner-visible pay back in the feed, ship
    # it as an explicit opt-in toggle with a warning that the feed
    # should not be shared publicly.
    if gig.get("notes"):
        description_lines.append(f"Notes: {gig['notes']}")
    description = "\\n".join(_ics_escape(l) for l in description_lines)

    location = []
    if gig.get("venue_name"):
        location.append(gig["venue_name"])
    if gig.get("city"):
        location.append(gig["city"])
    if gig.get("state"):
        location.append(gig["state"])
    location_str = ", ".join(location)

    uid_suffix = f"slot{gig['slot_id']}" if gig.get("slot_id") else "gig"
    # When we know the venue's IANA zone, anchor DTSTART/DTEND to it via
    # TZID. The matching VTIMEZONE block is emitted in the VCALENDAR
    # header — Google / Apple / Outlook need both halves to render the
    # event at the venue's wall clock time across subscriber timezones.
    if tz_name:
        dtstart = f"DTSTART;TZID={tz_name}:{date_str}T{start}"
        dtend   = f"DTEND;TZID={tz_name}:{end_date_str}T{end}"
    else:
        dtstart = f"DTSTART:{date_str}T{start}"
        dtend   = f"DTEND:{end_date_str}T{end}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:gigsfill-{gig_id}-{uid_suffix}@gigsfill.com",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        dtstart,
        dtend,
        f"SUMMARY:{_ics_escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if location_str:
        lines.append(f"LOCATION:{_ics_escape(location_str)}")
    lines.append("STATUS:CONFIRMED")
    lines.append("END:VEVENT")
    return [_fold_line(l) for l in lines]


@router.get("/api/calendar/{token}.ics")
def calendar_feed(token: str, db=Depends(get_db)):
    """Serve the iCal feed for the user owning this token."""
    if not token or len(token) < 16 or len(token) > 64:
        raise HTTPException(404, "Calendar not found")

    user = db.execute(
        text("SELECT id, first_name, last_name FROM users WHERE calendar_token = :tok"),
        {"tok": token}
    ).mappings().first()
    if not user:
        raise HTTPException(404, "Calendar not found")
    user_id = user["id"]

    # Walk every relevant booked gig for this user. Covers:
    #   - Artist-owner: their own artists, plus any artists they're an
    #     entity_user of (multi-user accounts).
    #   - Venue-owner: their venues + entity_user venues.
    # Single query union'd — duplicates collapsed by (gig_id, slot_id).
    rows = db.execute(
        text("""
            SELECT DISTINCT
                g.id as gig_id,
                NULL as slot_id,
                g.date,
                g.start_time,
                g.end_time,
                g.title,
                g.pay,
                g.notes,
                v.venue_name,
                v.city, v.state,
                a.name as artist_name,
                -- Door deals are slot-level only — single-slot gigs are always
                -- flat, but we select these columns as NULL so both arms of the
                -- UNION have matching shape. CAST(NULL AS ...) is required on
                -- Postgres which strictly type-checks UNION columns; SQLite is
                -- lenient but accepts the cast as a no-op.
                CAST(NULL AS TEXT)    as deal_type,
                CAST(NULL AS INTEGER) as door_pct,
                CAST(NULL AS INTEGER) as guarantee_cents
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            LEFT JOIN artists a ON a.id = g.artist_id
            WHERE g.status IN ('booked','awaiting_venue_contract')
              AND g.is_multi_slot = 0
              AND (
                v.user_id = :uid
                OR a.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu
                           WHERE eu.user_id = :uid
                             AND ((eu.entity_type = 'venue'  AND eu.entity_id = v.id)
                               OR (eu.entity_type = 'artist' AND eu.entity_id = a.id)))
              )

            UNION

            SELECT DISTINCT
                g.id as gig_id,
                gs.id as slot_id,
                g.date,
                gs.start_time,
                gs.end_time,
                g.title,
                gs.pay,
                g.notes,
                v.venue_name,
                v.city, v.state,
                a.name as artist_name,
                gs.deal_type,
                gs.door_pct,
                gs.guarantee_cents
            FROM gig_slots gs
            JOIN gigs g  ON g.id = gs.gig_id
            JOIN venues v ON v.id = g.venue_id
            LEFT JOIN artists a ON a.id = gs.artist_id
            WHERE gs.status IN ('booked','awaiting_venue_contract')
              AND (
                v.user_id = :uid
                OR a.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu
                           WHERE eu.user_id = :uid
                             AND ((eu.entity_type = 'venue'  AND eu.entity_id = v.id)
                               OR (eu.entity_type = 'artist' AND eu.entity_id = a.id)))
              )
            ORDER BY date, start_time
        """),
        {"uid": user_id}
    ).mappings().all()

    # First pass — resolve each row's timezone via its venue's state and
    # collect the unique set so we know which VTIMEZONE blocks to emit
    # in the header. Falls back to the platform default for venues whose
    # state we can't map.
    platform_tz = get_platform_timezone(db)
    resolved = []  # list of (row_dict, tz_name_or_None)
    used_tzs = set()
    for r in rows:
        d = dict(r)
        tz = _tz_for_state(d.get("state")) or platform_tz
        if tz not in _VTIMEZONE_BODIES:
            tz = None  # unknown zone → floating time (subscriber's local)
        resolved.append((d, tz))
        if tz:
            used_tzs.add(tz)

    body_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GigsFill//Calendar Feed 1.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:GigsFill — {_ics_escape((user.get('first_name') or '') + ' ' + (user.get('last_name') or '')).strip() or 'My Calendar'}",
        "X-PUBLISHED-TTL:PT1H",
    ]
    # Emit one VTIMEZONE per unique zone in the feed. The blocks are
    # pre-rendered with CRLF inside; split on CRLF and feed line-by-line
    # so the surrounding "\r\n".join(...) below stays correct.
    for tz in sorted(used_tzs):
        block = _build_vtimezone(tz)
        if block:
            for line in block.rstrip("\r\n").split("\r\n"):
                body_lines.append(line)
    for d, tz in resolved:
        body_lines.extend(_build_vevent(d, tz_name=tz))
    body_lines.append("END:VCALENDAR")

    body = "\r\n".join(body_lines) + "\r\n"
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": "inline; filename=gigsfill.ics",
        },
    )


@router.get("/api/me/calendar-feed-url")
def my_calendar_feed_url(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Return the authenticated user's iCal feed URL — minting a token
    on first call. Surfaced by the user-profile UI as a copyable URL."""
    token = _ensure_user_token(db, user.id)
    host = request.headers.get("host") or "gigsfill.com"
    proto = "https" if "localhost" not in host and "127.0.0.1" not in host else "http"
    return {"url": f"{proto}://{host}/api/calendar/{token}.ics", "token": token}


@router.post("/api/me/calendar-feed-url/rotate")
def rotate_calendar_token(user=Depends(get_current_user), db=Depends(get_db)):
    """Mint a fresh calendar_token, invalidating the old URL. Use if
    the user accidentally shared their feed URL publicly."""
    new_token = uuid.uuid4().hex
    db.execute(
        text("UPDATE users SET calendar_token = :tok WHERE id = :uid"),
        {"tok": new_token, "uid": user.id}
    )
    db.commit()
    return {"ok": True, "token": new_token}

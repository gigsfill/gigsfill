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
from backend.services.email_dispatch import format_pay_summary_with_sign
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


def _build_vevent(gig):
    """Render one gig as a VEVENT block."""
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
    if gig.get("pay") is not None or gig.get("deal_type"):
        # Door-aware: for slots with deal_type='door' this renders
        # "$50.00 guarantee + 20% of door" so calendar subscribers (Google,
        # Apple, Outlook) see the same terms the artist sees on the site.
        description_lines.append(f"Pay: {format_pay_summary_with_sign(gig)}")
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
    lines = [
        "BEGIN:VEVENT",
        f"UID:gigsfill-{gig_id}-{uid_suffix}@gigsfill.com",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{date_str}T{start}",
        f"DTEND:{end_date_str}T{end}",
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
                -- UNION have matching shape (so the formatter can run unconditionally).
                NULL as deal_type,
                NULL as door_pct,
                NULL as guarantee_cents
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

    body_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GigsFill//Calendar Feed 1.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:GigsFill — {_ics_escape((user.get('first_name') or '') + ' ' + (user.get('last_name') or '')).strip() or 'My Calendar'}",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for r in rows:
        body_lines.extend(_build_vevent(dict(r)))
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

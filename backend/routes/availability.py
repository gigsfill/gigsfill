"""
Artist Availability / Blackout Dates
=====================================
Artists can block date ranges to prevent bookings on dates they're unavailable.
Venues see unavailable artists greyed out in search.
Booking attempts on blacked-out dates are rejected.

Endpoints:
  GET    /api/artists/{artist_id}/availability          — get blackout dates
  POST   /api/artists/{artist_id}/availability          — add blackout range
  DELETE /api/artists/{artist_id}/availability/{id}     — remove blackout
  GET    /api/artists/{artist_id}/available             — check if available on date
"""
import logging
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from backend.routes.auth import get_current_user
from backend.db import get_db
from backend.utils import check_artist_access
from backend.services.member_availability import COMMITTED_SLOT_STATUSES as _COMMITTED

logger = logging.getLogger("gigsfill.availability")
router = APIRouter()

_TABLE_CREATED_ARTIST_AVAILABILITY = False

def _parse_date(s) -> date:
    """Parse YYYY-MM-DD string to date object."""
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(400, f"Invalid date format: {s}. Use YYYY-MM-DD")


# ── GET BLACKOUT DATES ─────────────────────────────────────────────────────────
def _other_band_commitments(db, artist_id: int, start: str, end: str):
    """Days a member of this band is already playing with a DIFFERENT band.

    Derived, never stored. Writing these as rows at booking time would mean
    deleting them again on every cancel, decline, date change and roster
    change — and the three cancellation endpoints in gigs.py are already the
    place this codebase most often fixes one path and misses another. A stale
    "unavailable" is worse than none, so this is computed on read and is
    always right.

    Returns {day: [{user_id, name}]}. Deliberately nothing about WHICH band or
    venue: unavailable is unavailable, and band A has no business being told
    its singer is playing with band B.
    """
    rows = db.execute(text(f"""
        SELECT date(g.date) AS day,
               eu.user_id AS user_id,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) AS name
        FROM (
            SELECT user_id FROM artists WHERE id = :aid AND user_id IS NOT NULL
            UNION
            SELECT user_id FROM entity_users
            WHERE entity_type = 'artist' AND entity_id = :aid
        ) eu
        JOIN users u ON u.id = eu.user_id
        JOIN (
            SELECT id, name, user_id AS member_id FROM artists
            WHERE user_id IS NOT NULL AND deleted_at IS NULL
            UNION
            SELECT a.id, a.name, e.user_id FROM artists a
            JOIN entity_users e ON e.entity_type = 'artist' AND e.entity_id = a.id
            WHERE a.deleted_at IS NULL
        ) a2 ON a2.member_id = eu.user_id AND a2.id <> :aid
        JOIN gig_slots gs ON gs.artist_id = a2.id AND gs.status IN {_COMMITTED}
        JOIN gigs g ON g.id = gs.gig_id
        WHERE date(g.date) BETWEEN date(:s) AND date(:e)
    """), {"aid": artist_id, "s": start, "e": end}).mappings().all()

    out = {}
    for r in rows:
        day = str(r["day"])[:10]
        bucket = out.setdefault(day, [])
        # One member double-booked with two other bands on one night is still
        # one unavailable person for this band; keep the first.
        if any(x["user_id"] == r["user_id"] for x in bucket):
            continue
        bucket.append({"user_id": r["user_id"], "name": r["name"]})
    return out


def _member_blackouts_for_gig(db, artist_id: int, gig_date: str,
                              current_user_id: int = None):
    """Members who have marked themselves off on a gig date.

    Drives the booking warning: "Scott is away that night — book anyway?".
    SOFT by design. A four-piece that also plays as a duo has to stay bookable
    when the drummer is out, so this never blocks; it only names who is away
    and lets the band decide.

    Returns {user_id, name, scope, is_self, blackout_start, blackout_end,
    reason}. The two blackout_* keys are the gig date repeated and reason is
    always "": both are kept so the existing booking-precheck modal keeps
    working without a rewrite.

    NOTE gigs.py imports this inside a try/except that falls back to an empty
    list, so if this function goes missing the warning silently stops
    appearing rather than erroring. tests/test_band_calendar.py pins it.
    """
    if not gig_date:
        return []
    gd = str(gig_date)[:10]
    rows = db.execute(text("""
        SELECT d.user_id, d.artist_id,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) AS name
        FROM member_days_off d
        JOIN users u ON u.id = d.user_id
        WHERE (d.artist_id = 0 OR d.artist_id = :aid)
          AND d.user_id IN (
            SELECT user_id FROM artists WHERE id = :aid AND user_id IS NOT NULL
            UNION
            SELECT user_id FROM entity_users
            WHERE entity_type = 'artist' AND entity_id = :aid
          )
          AND date(d.day) = date(:gd)
    """), {"aid": artist_id, "gd": gd}).mappings().all()

    out, seen = [], set()
    for r in rows:
        # Someone can hold both a global row and a band row for the same day.
        # That is one unavailable person, not two.
        if r["user_id"] in seen:
            continue
        seen.add(r["user_id"])
        out.append({
            "user_id": r["user_id"],
            "name": r["name"],
            "scope": "all" if not r["artist_id"] else "band",
            "blackout_start": gd,
            "blackout_end": gd,
            "reason": "",
            "is_self": (current_user_id is not None and r["user_id"] == current_user_id),
        })

    # A member already playing with another band that night is unavailable
    # whether or not they remembered to click the day — which is the point:
    # nobody has to maintain it.
    for p in _other_band_commitments(db, artist_id, gd, gd).get(gd, []):
        prior = next((x for x in out if x["user_id"] == p["user_id"]), None)
        if prior:
            prior["locked"] = True
            continue
        out.append({
            "user_id": p["user_id"],
            "name": p["name"],
            "scope": "band",
            "locked": True,
            "blackout_start": gd,
            "blackout_end": gd,
            "reason": "",
            "is_self": (current_user_id is not None and p["user_id"] == current_user_id),
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# BAND CALENDAR (2026-10-04)
# ══════════════════════════════════════════════════════════════════════════════
# Replaces the date-range + reason forms above. Members click days on a
# calendar; a click is one row in `member_days_off`, toggled on and off. No
# reason is collected — in practice it was either blank or "Out of Town",
# and it was never shown to anyone making a booking decision.
#
# Scope follows where you clicked, which is also what it means:
#   • your own profile  → artist_id 0, every band you play in ("I'm away")
#   • a band's calendar → that band only ("I'm booked with the other band")
#
# (2026-10-05) Artist-level blackouts have been removed entirely. They hard-
# blocked bookings and pulled the band out of blasts, the digest and hold
# offers — which took the call away from the band, since a four-piece missing
# its drummer is still a trio. Member days are now the only input; the one
# thing still suppressed is an email no possible line-up could accept, and
# that is derived (services/member_availability.everyone_off).

def _member_ids_for_artist(db, artist_id: int):
    """Owner plus every entity_user. Both legs matter: the owning user is on
    artists.user_id and does not necessarily have an entity_users row."""
    rows = db.execute(text("""
        SELECT user_id FROM artists WHERE id = :aid AND user_id IS NOT NULL
        UNION
        SELECT user_id FROM entity_users
        WHERE entity_type = 'artist' AND entity_id = :aid
    """), {"aid": artist_id}).fetchall()
    return [r[0] for r in rows]


@router.get("/api/artists/{artist_id}/calendar")
def get_band_calendar(artist_id: int, start: str = None, end: str = None,
                      user=Depends(get_current_user), db=Depends(get_db)):
    """Everything the band calendar needs for a date window, in one call:
    who is off on which day, and which days the whole band is blocked.

    Shaped as a day → members map rather than a flat list so the grid can
    render straight from it; the month view asks "who is out on the 14th",
    never "when is Jim out".
    """
    check_artist_access(db, artist_id, user.id)
    if not start or not end:
        today = date.today()
        start = start or today.replace(day=1).isoformat()
        end = end or (today + timedelta(days=120)).isoformat()
    s, e = _parse_date(start), _parse_date(end)
    if e < s:
        raise HTTPException(400, "end must not be before start")
    if (e - s).days > 400:
        raise HTTPException(400, "Range too wide — ask for a year or less")

    members = _member_ids_for_artist(db, artist_id)
    if not members:
        return {"days": {}, "members": []}

    # Expanded inline rather than via a helper so the member list and the
    # day rows are guaranteed to come from the same set of ids.
    ph = ",".join(f":m{i}" for i in range(len(members)))
    binds = {f"m{i}": m for i, m in enumerate(members)}
    binds.update({"aid": artist_id, "s": s.isoformat(), "e": e.isoformat()})

    rows = db.execute(text(f"""
        SELECT d.day, d.user_id, d.artist_id,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) AS name
        FROM member_days_off d
        JOIN users u ON u.id = d.user_id
        WHERE d.user_id IN ({ph})
          AND (d.artist_id = 0 OR d.artist_id = :aid)
          AND date(d.day) BETWEEN date(:s) AND date(:e)
    """), binds).mappings().all()

    days = {}
    for r in rows:
        key = str(r["day"])[:10]
        entry = days.setdefault(key, [])
        # A member can hold both a global row and a band row for the same
        # day; the calendar should show them once.
        if any(x["user_id"] == r["user_id"] for x in entry):
            continue
        entry.append({
            "user_id": r["user_id"],
            "name": r["name"],
            "scope": "all" if not r["artist_id"] else "band",
            "is_self": r["user_id"] == user.id,
        })

    # Members already playing with another band that night. Derived, so it is
    # merged in after the clicked days rather than stored alongside them.
    for day, people in _other_band_commitments(db, artist_id, s.isoformat(), e.isoformat()).items():
        entry = days.setdefault(day, [])
        for p in people:
            prior = next((x for x in entry if x["user_id"] == p["user_id"]), None)
            if prior:
                # `locked`, not a "gig" scope: the UI only needs to know this
                # entry cannot be cleared by un-clicking. Naming the cause in
                # the payload would tell band B exactly what we decided not to
                # show it, to anyone who opens the network tab.
                prior["locked"] = True
                continue
            entry.append({
                "user_id": p["user_id"],
                "name": p["name"],
                "locked": True,
                "is_self": p["user_id"] == user.id,
            })

    # (2026-10-05) Artist-level blackouts are gone; nothing reports or sets
    # band-wide blocks any more. What a band is unavailable for is derived
    # from its members — see services/member_availability.py.
    roster = db.execute(text(f"""
        SELECT u.id,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) AS name
        FROM users u WHERE u.id IN ({ph})
    """), binds).mappings().all()

    return {
        "days": days,
        "members": [{"user_id": r["id"], "name": r["name"],
                     "is_self": r["id"] == user.id} for r in roster],
    }


def _toggle_day(db, user_id: int, artist_id: int, day: str):
    """Insert the day, or delete it if it is already there. Returns the new
    state so the caller does not have to re-read."""
    d = _parse_date(day)
    existing = db.execute(text("""
        SELECT id FROM member_days_off
        WHERE user_id = :u AND artist_id = :a AND date(day) = date(:d)
    """), {"u": user_id, "a": artist_id, "d": d.isoformat()}).first()
    if existing:
        db.execute(text("DELETE FROM member_days_off WHERE id = :i"), {"i": existing[0]})
        db.commit()
        return False
    db.execute(text("""
        INSERT INTO member_days_off (user_id, artist_id, day) VALUES (:u, :a, :d)
    """), {"u": user_id, "a": artist_id, "d": d.isoformat()})
    # They engaged, so the unanswered-reminder counter starts over; otherwise a
    # member who ignored five emails then finally updated would be silenced
    # after one more lapse.
    db.execute(text("""
        INSERT INTO availability_reminders (user_id, send_count) VALUES (:u, 0)
        ON CONFLICT(user_id) DO UPDATE SET send_count = 0
    """), {"u": user_id})
    db.commit()
    return True


@router.post("/api/artists/{artist_id}/days-off/toggle")
def toggle_band_day_off(artist_id: int, payload: dict,
                        user=Depends(get_current_user), db=Depends(get_db)):
    """Mark yourself off for this band only. Any member of the band may do
    this for themselves — it is a soft signal, not a block."""
    if user.id not in _member_ids_for_artist(db, artist_id):
        raise HTTPException(403, "You are not a member of this artist")
    on = _toggle_day(db, user.id, artist_id, payload.get("day"))
    return {"day": str(payload.get("day"))[:10], "off": on, "scope": "band"}


@router.post("/api/me/days-off/toggle")
def toggle_my_day_off(payload: dict,
                      user=Depends(get_current_user), db=Depends(get_db)):
    """Mark yourself off across every band you play in."""
    on = _toggle_day(db, user.id, 0, payload.get("day"))
    return {"day": str(payload.get("day"))[:10], "off": on, "scope": "all"}


@router.get("/api/me/days-off")
def get_my_days_off(start: str = None, end: str = None,
                    user=Depends(get_current_user), db=Depends(get_db)):
    """The personal calendar: the user's own days, both global and per-band,
    so they can see where each one applies."""
    if not start or not end:
        today = date.today()
        start = start or today.replace(day=1).isoformat()
        end = end or (today + timedelta(days=365)).isoformat()
    s, e = _parse_date(start), _parse_date(end)
    if e < s:
        raise HTTPException(400, "end must not be before start")

    rows = db.execute(text("""
        SELECT d.day, d.artist_id, a.name AS artist_name
        FROM member_days_off d
        LEFT JOIN artists a ON a.id = d.artist_id
        WHERE d.user_id = :u AND date(d.day) BETWEEN date(:s) AND date(:e)
    """), {"u": user.id, "s": s.isoformat(), "e": e.isoformat()}).mappings().all()

    days = {}
    for r in rows:
        key = str(r["day"])[:10]
        days.setdefault(key, []).append({
            "artist_id": r["artist_id"],
            "artist_name": r["artist_name"] if r["artist_id"] else None,
            "scope": "all" if not r["artist_id"] else "band",
        })
    return {"days": days}




@router.get("/api/availability/all-clear", include_in_schema=False)
def availability_all_clear(token: str = "", db=Depends(get_db)):
    """One-click "I'm free, stop reminding me" from the weekly email.

    Deliberately unauthenticated: it is reached from an email client, often on
    a phone that is not logged in, and forcing a login would make the quick
    answer the slow one. Safety comes from the token — signed, its own salt,
    60-day expiry — so it cannot be forged or guessed, and the worst a leaked
    link can do is silence one person's reminder for a month.
    """
    from fastapi.responses import HTMLResponse
    from backend.services.availability_reminder import read_ack_token, record_ack
    from backend.db import get_db_connection

    # Three outcomes, three headings. A failed save is not an invalid link, and
    # telling someone their good link is bad sends them hunting for a problem
    # that is ours.
    uid = read_ack_token(token)
    if not uid:
        heading = "Link not valid"
        body = ("This link has expired or was altered. Open your availability "
                "calendar to update it directly.")
    else:
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            record_ack(cur, uid)
            conn.commit()
            heading = "All clear"
            body = ("Thanks — we'll leave you alone for a month. Your band "
                    "will see you as available until you mark days off.")
        except Exception:
            logger.exception("all-clear ack failed for uid=%s", uid)
            heading = "Couldn't save that"
            body = ("Your link was fine, but we couldn't record it. Open your "
                    "availability calendar and mark your days there instead.")

    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Availability — GigsFill</title></head>
<body style="margin:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="max-width:520px;margin:48px auto;background:#fff;border-radius:8px;
     box-shadow:0 1px 3px rgba(0,0,0,0.08);padding:32px 40px;">
  <img src="/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40"
       style="display:block;border:0;margin-bottom:24px;">
  <h1 style="margin:0 0 12px;font-size:20px;color:{'#1d4ed8' if heading == 'All clear' else '#b45309'};">
    {heading}</h1>
  <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#4b5563;">{body}</p>
  <a href="/app/user-profile.html?tab=availability"
     style="display:inline-block;padding:12px 24px;background:#1d4ed8;color:#fff;
            border-radius:6px;text-decoration:none;font-size:15px;font-weight:600;">
    Open my calendar</a>
</div></body></html>""")

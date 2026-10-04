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

logger = logging.getLogger("gigsfill.availability")
router = APIRouter()

_TABLE_CREATED_ARTIST_AVAILABILITY = False

def _ensure_artist_availability_table(db):
    global _TABLE_CREATED_ARTIST_AVAILABILITY
    if _TABLE_CREATED_ARTIST_AVAILABILITY:
        return
    try:
        db.execute(text("""CREATE TABLE IF NOT EXISTS artist_availability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_id INTEGER NOT NULL,
                blackout_start DATE NOT NULL,
                blackout_end DATE NOT NULL,
                reason TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""))
        db.commit()
        _TABLE_CREATED_ARTIST_AVAILABILITY = True
    except Exception:
        pass



def _parse_date(s) -> date:
    """Parse YYYY-MM-DD string to date object."""
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(400, f"Invalid date format: {s}. Use YYYY-MM-DD")


# ── GET BLACKOUT DATES ─────────────────────────────────────────────────────────
@router.get("/api/artists/{artist_id}/availability")
def get_availability(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get artist's blackout dates. Artist and their team can view."""
    check_artist_access(db, artist_id, user.id)

    rows = db.execute(
        text("""
            SELECT id, blackout_start, blackout_end, reason, created_at
            FROM artist_availability
            WHERE artist_id = :aid
              AND date(blackout_end) >= date('now', '-1 day')
            ORDER BY blackout_start ASC
        """),
        {"aid": artist_id}
    ).mappings().all()

    return {"blackouts": [dict(r) for r in rows]}


# ── PUBLIC: CHECK AVAILABILITY ON A DATE ─────────────────────────────────────
@router.get("/api/artists/{artist_id}/available")
def check_available(artist_id: int, check_date: str, db=Depends(get_db)):
    """Public — returns whether artist is available on a given date (for booking UX)."""
    d = _parse_date(check_date)

    conflict = db.execute(
        text("""
            SELECT id FROM artist_availability
            WHERE artist_id = :aid
              AND date(:d) BETWEEN date(blackout_start) AND date(blackout_end)
            LIMIT 1
        """),
        {"aid": artist_id, "d": str(d)}
    ).fetchone()

    return {"available": conflict is None, "date": str(d)}


# ── ADD BLACKOUT RANGE ─────────────────────────────────────────────────────────
@router.delete("/api/artists/{artist_id}/availability/{blackout_id}")
def delete_blackout(artist_id: int, blackout_id: int,
                    user=Depends(get_current_user), db=Depends(get_db)):
    """Remove a blackout date range."""
    check_artist_access(db, artist_id, user.id)

    existing = db.execute(
        text("SELECT id FROM artist_availability WHERE id = :id AND artist_id = :aid"),
        {"id": blackout_id, "aid": artist_id}
    ).fetchone()

    if not existing:
        raise HTTPException(404, "Blackout not found")

    db.execute(
        text("DELETE FROM artist_availability WHERE id = :id AND artist_id = :aid"),
        {"id": blackout_id, "aid": artist_id}
    )
    db.commit()
    return {"ok": True}


# ── UPDATE BLACKOUT ────────────────────────────────────────────────────────────
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
# Band-wide blackouts stay in `artist_availability` and keep their hard-block
# semantics — they remove the artist from preferred-artist blasts, the open-gig
# digest and venue holds, which a member being out never should. They are now
# set from the same calendar rather than a separate section.

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


def _is_artist_admin(db, artist_id: int, user) -> bool:
    """Only an admin may mark the whole band out, because that hard-blocks
    bookings and silences blasts for everyone."""
    try:
        check_artist_access(db, artist_id, user.id)
    except HTTPException:
        return False
    return True


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
        return {"days": {}, "band_blocked": [], "members": []}

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

    band_rows = db.execute(text("""
        SELECT id, blackout_start, blackout_end, reason FROM artist_availability
        WHERE artist_id = :aid
          AND date(blackout_end) >= date(:s) AND date(blackout_start) <= date(:e)
    """), {"aid": artist_id, "s": s.isoformat(), "e": e.isoformat()}).mappings().all()
    band_blocked = []
    for b in band_rows:
        try:
            d0 = max(_parse_date(str(b["blackout_start"])[:10]), s)
            d1 = min(_parse_date(str(b["blackout_end"])[:10]), e)
        except HTTPException:
            continue
        while d0 <= d1:
            band_blocked.append(d0.isoformat())
            d0 += timedelta(days=1)

    roster = db.execute(text(f"""
        SELECT u.id,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) AS name
        FROM users u WHERE u.id IN ({ph})
    """), binds).mappings().all()

    return {
        "days": days,
        "band_blocked": sorted(set(band_blocked)),
        "members": [{"user_id": r["id"], "name": r["name"],
                     "is_self": r["id"] == user.id} for r in roster],
        "can_block_band": _is_artist_admin(db, artist_id, user),
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


@router.post("/api/artists/{artist_id}/band-days-off/toggle")
def toggle_band_wide_day(artist_id: int, payload: dict,
                         user=Depends(get_current_user), db=Depends(get_db)):
    """Mark the WHOLE band unavailable for a day — a hard block.

    Stored in artist_availability as a single-day row so every existing
    consumer (booking, preferred-artist blast, open-gig digest, venue holds)
    keeps working untouched. Toggling off deletes only rows that cover
    exactly this day; a multi-day range created by the old form is left
    alone rather than silently torn in half.
    """
    check_artist_access(db, artist_id, user.id)
    _ensure_artist_availability_table(db)
    d = _parse_date(payload.get("day"))

    same_day = db.execute(text("""
        SELECT id FROM artist_availability
        WHERE artist_id = :a AND date(blackout_start) = date(:d)
                            AND date(blackout_end) = date(:d)
    """), {"a": artist_id, "d": d.isoformat()}).first()

    if same_day:
        db.execute(text("DELETE FROM artist_availability WHERE id = :i"), {"i": same_day[0]})
        db.commit()
        return {"day": d.isoformat(), "blocked": False}

    spanning = db.execute(text("""
        SELECT id FROM artist_availability
        WHERE artist_id = :a
          AND date(:d) BETWEEN date(blackout_start) AND date(blackout_end)
    """), {"a": artist_id, "d": d.isoformat()}).first()
    if spanning:
        raise HTTPException(
            409,
            "That day is inside a longer band blackout. Remove the range first.")

    db.execute(text("""
        INSERT INTO artist_availability (artist_id, blackout_start, blackout_end, reason)
        VALUES (:a, :d, :d, '')
    """), {"a": artist_id, "d": d.isoformat()})
    db.commit()
    return {"day": d.isoformat(), "blocked": True}

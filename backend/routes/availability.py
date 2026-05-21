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
@router.post("/api/artists/{artist_id}/availability")
def add_blackout(artist_id: int, data: dict,
                 user=Depends(get_current_user), db=Depends(get_db)):
    """Add a blackout date range. Artist/team only.

    Conflict detection (May 2026):
      - BOOKED gigs in the range → 409 with conflict_type='booked' (always blocking;
        artist must cancel the booking first).
      - WAITLISTED gigs in the range → 409 with conflict_type='waitlist' UNLESS
        `force=true` is set in the body. With force, the artist is removed from
        those waitlists and the blackout is created.

    The frontend sees the 409 with a structured payload and shows a confirmation
    modal asking the user to keep waitlist (cancel blackout) or remove from
    waitlist (proceed with blackout via force=true).
    """
    check_artist_access(db, artist_id, user.id)

    start = _parse_date(data.get("blackout_start"))
    end = _parse_date(data.get("blackout_end"))
    reason = str(data.get("reason", "")).strip()[:200]
    force = bool(data.get("force", False))

    if end < start:
        raise HTTPException(400, "End date must be on or after start date")
    if (end - start).days > 365:
        raise HTTPException(400, "Blackout range cannot exceed 1 year")

    # ─── Check 1: Booked gigs (always blocking — never overridable) ───
    # Looks at both single-slot bookings (gigs.artist_id) and multi-slot (gig_slots)
    # because the codebase has both shapes (see Section 16 item #21).
    booked_conflicts = db.execute(
        text("""
            SELECT DISTINCT g.id, g.date, g.title FROM gigs g
            WHERE g.status = 'booked'
              AND date(g.date) BETWEEN date(:start) AND date(:end)
              AND (
                  g.artist_id = :aid
                  OR EXISTS (
                      SELECT 1 FROM gig_slots gs
                      WHERE gs.gig_id = g.id AND gs.artist_id = :aid AND gs.status = 'booked'
                  )
              )
            ORDER BY g.date
            LIMIT 5
        """),
        {"aid": artist_id, "start": str(start), "end": str(end)}
    ).mappings().all()

    if booked_conflicts:
        conflict_list = ", ".join(f"{c['date']} ({c['title']})" for c in booked_conflicts)
        raise HTTPException(
            409,
            f"You have existing bookings in this date range: {conflict_list}. "
            f"Cancel those gigs before blocking this period."
        )

    # ─── Check 2: Waitlisted gigs (overridable with force=true) ───
    waitlist_conflicts = db.execute(
        text("""
            SELECT DISTINCT g.id, g.date, g.title, v.venue_name
            FROM gig_waitlist w
            JOIN gigs g ON g.id = w.gig_id
            LEFT JOIN venues v ON v.id = g.venue_id
            WHERE w.artist_id = :aid
              AND (w.offer_declined = 0 OR w.offer_declined IS NULL)
              AND date(g.date) BETWEEN date(:start) AND date(:end)
              AND g.status NOT IN ('cancelled', 'deleted')
            ORDER BY g.date
        """),
        {"aid": artist_id, "start": str(start), "end": str(end)}
    ).mappings().all()
    # Also include active offers (artist is in waitlist_offered but maybe no longer
    # in gig_waitlist if the schema separates them)
    offered_conflicts = db.execute(
        text("""
            SELECT DISTINCT g.id, g.date, g.title, v.venue_name
            FROM waitlist_offered wo
            JOIN gigs g ON g.id = wo.gig_id
            LEFT JOIN venues v ON v.id = g.venue_id
            WHERE wo.artist_id = :aid
              AND wo.offer_expires_at > datetime('now')
              AND date(g.date) BETWEEN date(:start) AND date(:end)
              AND g.status NOT IN ('cancelled', 'deleted')
            ORDER BY g.date
        """),
        {"aid": artist_id, "start": str(start), "end": str(end)}
    ).mappings().all()
    # Merge by gig_id
    all_waitlist_gig_ids = {c["id"] for c in waitlist_conflicts} | {c["id"] for c in offered_conflicts}
    merged_conflicts = list({c["id"]: dict(c) for c in (list(waitlist_conflicts) + list(offered_conflicts))}.values())

    if all_waitlist_gig_ids and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "waitlist_conflict",
                "conflict_type": "waitlist",
                "message": "You are on the waitlist for gigs in this date range.",
                "conflicts": [
                    {
                        "gig_id": c["id"],
                        "date": str(c["date"]),
                        "title": c.get("title") or "",
                        "venue_name": c.get("venue_name") or "",
                    }
                    for c in merged_conflicts
                ]
            }
        )

    # If force=true and there are waitlist conflicts, remove the artist from those waitlists
    if all_waitlist_gig_ids and force:
        for gid in all_waitlist_gig_ids:
            db.execute(
                text("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gid, "aid": artist_id}
            )
            # Also remove any active offer (if artist was the current offer holder)
            db.execute(
                text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gid, "aid": artist_id}
            )
        # If we removed an offer holder, advance the waitlist so the gig isn't stuck
        try:
            from backend.routes.waitlist import advance_waitlist_offer
            for gid in all_waitlist_gig_ids:
                # Only advance if the gig is still in a state that uses the waitlist
                gig_status = db.execute(text("SELECT status FROM gigs WHERE id = :gid"), {"gid": gid}).scalar()
                if gig_status in ('open', 'cancelled_blast'):
                    advance_waitlist_offer(db, gid)
        except Exception as _adv_err:
            logger.warning(f"advance_waitlist_offer after blackout failed: {_adv_err}")

    result = db.execute(
        text("""
            INSERT INTO artist_availability (artist_id, blackout_start, blackout_end, reason)
            VALUES (:aid, :start, :end, :reason)
        """),
        {"aid": artist_id, "start": str(start), "end": str(end), "reason": reason}
    )
    db.commit()

    return {
        "ok": True,
        "id": result.lastrowid,
        "blackout_start": str(start),
        "blackout_end": str(end),
        "reason": reason,
        "removed_from_waitlists": list(all_waitlist_gig_ids) if force else []
    }


# ── DELETE BLACKOUT ────────────────────────────────────────────────────────────
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
@router.put("/api/artists/{artist_id}/availability/{blackout_id}")
def update_blackout(artist_id: int, blackout_id: int, data: dict,
                    user=Depends(get_current_user), db=Depends(get_db)):
    """Update a blackout date range."""
    check_artist_access(db, artist_id, user.id)

    existing = db.execute(
        text("SELECT id FROM artist_availability WHERE id = :id AND artist_id = :aid"),
        {"id": blackout_id, "aid": artist_id}
    ).fetchone()
    if not existing:
        raise HTTPException(404, "Blackout not found")

    start = _parse_date(data.get("blackout_start"))
    end = _parse_date(data.get("blackout_end"))
    reason = str(data.get("reason", "")).strip()[:200]

    if end < start:
        raise HTTPException(400, "End date must be on or after start date")

    db.execute(
        text("""
            UPDATE artist_availability
            SET blackout_start = :start, blackout_end = :end, reason = :reason
            WHERE id = :id AND artist_id = :aid
        """),
        {"start": str(start), "end": str(end), "reason": reason,
         "id": blackout_id, "aid": artist_id}
    )
    db.commit()
    return {"ok": True}


# ── PUBLIC: BULK CHECK DATES (for venue calendar rendering) ──────────────────
@router.post("/api/artists/{artist_id}/availability/check-bulk")
def check_bulk(artist_id: int, data: dict, db=Depends(get_db)):
    """
    Check availability for multiple dates at once.
    Body: { "dates": ["2026-03-15", "2026-03-22", ...] }
    Returns: { "unavailable": ["2026-03-15", ...] }
    """
    dates = data.get("dates", [])
    if not dates or len(dates) > 366:
        raise HTTPException(400, "Provide between 1 and 366 dates")

    blackouts = db.execute(
        text("""
            SELECT blackout_start, blackout_end
            FROM artist_availability
            WHERE artist_id = :aid
        """),
        {"aid": artist_id}
    ).fetchall()

    unavailable = []
    for d_str in dates:
        try:
            d = _parse_date(d_str)
        except Exception:
            continue
        for b_start, b_end in blackouts:
            try:
                bs = datetime.strptime(str(b_start), "%Y-%m-%d").date()
                be = datetime.strptime(str(b_end), "%Y-%m-%d").date()
                if bs <= d <= be:
                    unavailable.append(str(d))
                    break
            except Exception:
                continue

    return {"unavailable": unavailable}


# ─── PER-MEMBER (USER) AVAILABILITY ─────────────────────────────────────────
# Sibling to per-artist availability above. Lives in `user_availability` —
# scoped by user_id with an optional artist_id ("applies to this band only").
# Band-level blackouts (artist_availability) hard-block bookings; member-level
# blackouts surface as a soft warning at booking time that the band can confirm
# through ("the band is performing as a duo without Jim that night").


def _user_artist_ids(db, user_id: int):
    """Return list of artist ids this user has access to (owner OR entity_users)."""
    rows = db.execute(text("""
        SELECT a.id FROM artists a WHERE a.user_id = :uid
        UNION
        SELECT eu.entity_id FROM entity_users eu
        WHERE eu.entity_type = 'artist' AND eu.user_id = :uid
    """), {"uid": user_id}).fetchall()
    return [r[0] for r in rows]


@router.get("/api/me/availability")
def me_get_availability(user=Depends(get_current_user), db=Depends(get_db)):
    """Return this user's member-level blackouts, plus a read-only list of
    band-level blackouts for each artist they belong to (so they can see all
    their unavailable dates in one place)."""
    user_rows = db.execute(text("""
        SELECT ua.id, ua.artist_id, ua.blackout_start, ua.blackout_end, ua.reason,
               ua.created_at, a.name as artist_name
        FROM user_availability ua
        LEFT JOIN artists a ON a.id = ua.artist_id
        WHERE ua.user_id = :uid
        ORDER BY ua.blackout_start ASC
    """), {"uid": user.id}).mappings().all()

    # The artists this user is on (owner or entity_users)
    artist_ids = _user_artist_ids(db, user.id)
    my_artists = []
    band_rows = []
    if artist_ids:
        ph = ",".join(f":a{i}" for i in range(len(artist_ids)))
        params = {f"a{i}": aid for i, aid in enumerate(artist_ids)}
        my_artists = [dict(r) for r in db.execute(text(
            f"SELECT id, name FROM artists WHERE id IN ({ph}) ORDER BY name"
        ), params).mappings().all()]
        band_rows = [dict(r) for r in db.execute(text(f"""
            SELECT ab.id, ab.artist_id, ab.blackout_start, ab.blackout_end, ab.reason,
                   ab.created_at, a.name as artist_name
            FROM artist_availability ab
            JOIN artists a ON a.id = ab.artist_id
            WHERE ab.artist_id IN ({ph})
            ORDER BY ab.blackout_start ASC
        """), params).mappings().all()]

    return {
        "user_blackouts": [dict(r) for r in user_rows],
        "band_blackouts": band_rows,  # read-only — edited via artist-edit
        "my_artists": my_artists,
    }


@router.post("/api/me/availability")
def me_add_blackout(data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Add a member-level blackout for this user. If artist_ids is omitted
    or empty, the blackout applies to ALL artists this user is a member of
    (stored as artist_id=NULL). If artist_ids is a list, one row per id is
    inserted, each scoped to that artist."""
    blackout_start = (data.get("blackout_start") or "").strip()
    blackout_end   = (data.get("blackout_end")   or "").strip()
    reason         = (data.get("reason") or "").strip()[:300]
    artist_ids     = data.get("artist_ids")  # None, [], or list of ints

    if not blackout_start:
        raise HTTPException(400, "blackout_start required")
    if not blackout_end:
        blackout_end = blackout_start

    # Validate dates parse
    try:
        bs = _parse_date(blackout_start)
        be = _parse_date(blackout_end)
        if be < bs:
            raise HTTPException(400, "blackout_end must be on or after blackout_start")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Invalid date format")

    # Verify the user owns/manages each artist in artist_ids (no cross-account writes)
    my_artist_ids = set(_user_artist_ids(db, user.id))
    if artist_ids:
        for aid in artist_ids:
            if int(aid) not in my_artist_ids:
                raise HTTPException(403, f"You're not a member of artist {aid}")
        # Insert one row per artist
        new_ids = []
        for aid in artist_ids:
            db.execute(text("""
                INSERT INTO user_availability (user_id, artist_id, blackout_start, blackout_end, reason)
                VALUES (:uid, :aid, :bs, :be, :r)
            """), {"uid": user.id, "aid": int(aid),
                   "bs": blackout_start, "be": blackout_end, "r": reason})
            new_ids.append(db.execute(text("SELECT last_insert_rowid()")).scalar())
        db.commit()
        return {"ok": True, "ids": new_ids, "count": len(new_ids)}
    else:
        # NULL artist_id → applies to all my artists
        db.execute(text("""
            INSERT INTO user_availability (user_id, artist_id, blackout_start, blackout_end, reason)
            VALUES (:uid, NULL, :bs, :be, :r)
        """), {"uid": user.id, "bs": blackout_start, "be": blackout_end, "r": reason})
        db.commit()
        return {"ok": True, "ids": [db.execute(text("SELECT last_insert_rowid()")).scalar()], "count": 1}


@router.delete("/api/me/availability/{blackout_id}")
def me_delete_blackout(blackout_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.execute(text(
        "SELECT id FROM user_availability WHERE id = :bid AND user_id = :uid"
    ), {"bid": blackout_id, "uid": user.id}).first()
    if not row:
        raise HTTPException(404, "Blackout not found or not yours")
    db.execute(text("DELETE FROM user_availability WHERE id = :bid"), {"bid": blackout_id})
    db.commit()
    return {"ok": True}


@router.put("/api/me/availability/{blackout_id}")
def me_update_blackout(blackout_id: int, data: dict,
                       user=Depends(get_current_user), db=Depends(get_db)):
    row = db.execute(text(
        "SELECT id FROM user_availability WHERE id = :bid AND user_id = :uid"
    ), {"bid": blackout_id, "uid": user.id}).first()
    if not row:
        raise HTTPException(404, "Blackout not found or not yours")
    bs = (data.get("blackout_start") or "").strip()
    be = (data.get("blackout_end") or "").strip() or bs
    reason = (data.get("reason") or "").strip()[:300]
    try:
        d1 = _parse_date(bs); d2 = _parse_date(be)
        if d2 < d1:
            raise HTTPException(400, "blackout_end must be on or after blackout_start")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Invalid date format")
    db.execute(text("""
        UPDATE user_availability SET blackout_start = :bs, blackout_end = :be, reason = :r
        WHERE id = :bid
    """), {"bs": bs, "be": be, "r": reason, "bid": blackout_id})
    db.commit()
    return {"ok": True}


@router.get("/api/artists/{artist_id}/member-availability")
def artist_member_availability(artist_id: int,
                                user=Depends(get_current_user), db=Depends(get_db)):
    """Return aggregated member-level blackouts for an artist's roster.
    Used by artist-edit's 'Member Availability' section. Caller must have
    access to the artist (check_artist_access enforces this).

    Returns list grouped by user:
      [{user_id, name, blackouts: [{id, blackout_start, blackout_end, reason}, ...]}, ...]
    """
    check_artist_access(db, artist_id, user.id)

    # All users who are members of this artist (owner + entity_users)
    members = db.execute(text("""
        SELECT u.id as user_id,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) as name
        FROM users u
        WHERE u.id = (SELECT user_id FROM artists WHERE id = :aid)
        UNION
        SELECT u.id as user_id,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) as name
        FROM users u
        JOIN entity_users eu ON eu.user_id = u.id
        WHERE eu.entity_type = 'artist' AND eu.entity_id = :aid
    """), {"aid": artist_id}).mappings().all()

    result = []
    for m in members:
        # For each member, pull their blackouts that apply to THIS artist
        # (either artist_id IS NULL = "all my bands", or artist_id matches)
        rows = db.execute(text("""
            SELECT id, blackout_start, blackout_end, reason, artist_id, created_at
            FROM user_availability
            WHERE user_id = :uid
              AND (artist_id IS NULL OR artist_id = :aid)
              AND date(blackout_end) >= date('now', '-1 day')
            ORDER BY blackout_start ASC
        """), {"uid": m["user_id"], "aid": artist_id}).mappings().all()
        if rows:
            result.append({
                "user_id": m["user_id"],
                "name": m["name"],
                "blackouts": [dict(r) for r in rows],
            })
    return {"members": result}


def _member_blackouts_for_gig(db, artist_id: int, gig_date: str):
    """Return member-level blackouts that conflict with a given gig date,
    for booking-precheck. Returns list of {user_id, name, blackout_start,
    blackout_end, reason}. Empty list when no conflicts."""
    if not gig_date:
        return []
    rows = db.execute(text("""
        SELECT ua.id, ua.user_id, ua.blackout_start, ua.blackout_end, ua.reason,
               COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name), ''), u.email) as name
        FROM user_availability ua
        JOIN users u ON u.id = ua.user_id
        WHERE (ua.artist_id IS NULL OR ua.artist_id = :aid)
          AND ua.user_id IN (
            SELECT user_id FROM artists WHERE id = :aid
            UNION
            SELECT user_id FROM entity_users
            WHERE entity_type = 'artist' AND entity_id = :aid
          )
          AND date(:gd) BETWEEN date(ua.blackout_start) AND date(ua.blackout_end)
    """), {"aid": artist_id, "gd": str(gig_date)[:10]}).mappings().all()
    return [dict(r) for r in rows]

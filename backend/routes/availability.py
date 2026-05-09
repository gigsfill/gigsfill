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

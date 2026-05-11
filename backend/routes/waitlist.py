"""
Waitlist for fully-booked gigs.

Sequential offer logic:
  1. When a gig re-opens, notify_waitlist() sends an offer to the #1 artist.
  2. They have 24 hours to click "Book" in the email (offer_token link).
  3. If they decline or time out, advance_waitlist_offer() is called, moves to #2, etc.
  4. <36 hrs before gig + venue has preferences → blast to all preferred artists within radius.
  5. Venue can always view the full waitlist.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from backend.routes.auth import get_current_user
from backend.db import get_db
from backend.rate_limiter import limiter

logger = logging.getLogger("gigsfill.waitlist")

router = APIRouter(tags=["waitlist"])


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _get_platform_now(db=None):
    """Return current datetime in platform timezone (falls back to local time)."""
    try:
        import pytz as _pytz
        from sqlalchemy import text as _tx
        if db:
            _tz_str = db.execute(_tx(
                "SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'"
            )).scalar() or "America/Los_Angeles"
        else:
            _tz_str = "America/Los_Angeles"
        _tz = _pytz.timezone(_tz_str)
        return datetime.now(_tz).replace(tzinfo=None)  # naive in platform tz
    except Exception:
        return datetime.now()  # fallback: server local time (UTC on DigitalOcean)


def _offer_hours_for_gig(hours_until: float) -> float:
    """Return offer window in hours based on how soon the gig is.
    > 1 week (168h) → 24 hours
    36h – 1 week    →  2 hours
    < 36h           →  0.5 hours (30 minutes)
    """
    if hours_until < 36:
        return 0.5
    elif hours_until <= 168:
        return 2
    else:
        return 24


def _format_deadline(expires_at_utc, db=None):
    """Format offer expiry time in platform timezone (e.g. '2:43 PM')."""
    try:
        from zoneinfo import ZoneInfo
        from sqlalchemy import text as _tx
        _tz_str = "America/Los_Angeles"
        if db:
            try:
                _tz_str = db.execute(_tx(
                    "SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'"
                )).scalar() or "America/Los_Angeles"
            except Exception:
                pass
        _tz = ZoneInfo(_tz_str)
        # Ensure expires_at_utc is timezone-aware
        if expires_at_utc.tzinfo is None:
            from datetime import timezone as _tz_mod
            expires_at_utc = expires_at_utc.replace(tzinfo=_tz_mod.utc)
        _local = expires_at_utc.astimezone(_tz)
        # Include timezone abbreviation so artists in other timezones know exactly what time this is
        _abbr = _local.strftime("%Z")  # e.g. "PST", "EST", "CDT"
        return _local.strftime("%-I:%M %p") + f" {_abbr}"
    except Exception:
        try:
            from zoneinfo import ZoneInfo as _ZI
            _local = expires_at_utc.astimezone(_ZI("America/Los_Angeles"))
            _abbr = _local.strftime("%Z")
            return _local.strftime("%-I:%M %p") + f" {_abbr}"
        except Exception:
            return ""

def _get_position(db, gig_id: int, row_id: int) -> int:
    """Return 1-based position of row_id in the waitlist for gig_id."""
    our_row = db.execute(
        text("SELECT id FROM gig_waitlist WHERE gig_id = :gid AND id = :rid"),
        {"gid": gig_id, "rid": row_id}
    ).scalar()
    if not our_row:
        return 1
    pos = db.execute(
        text("SELECT COUNT(*) FROM gig_waitlist WHERE gig_id = :gid AND id <= :rid"),
        {"gid": gig_id, "rid": our_row}
    ).scalar()
    return pos or 1


def _clear_waitlist_offer_notification(db, gig_id: int, artist_id: int):
    """Delete the artist's "Waitlist Spot Available" Activity Center notification
    for this gig. Called when the offer is consumed (booked, declined, or
    timed-out) so the artist's feed doesn't keep showing a stale invitation.
    Targets all users of the artist (multi-user accounts) via artist_id."""
    try:
        db.execute(
            text("""DELETE FROM notifications
                    WHERE notification_type = 'waitlist_offer'
                      AND gig_id = :gid AND artist_id = :aid"""),
            {"gid": gig_id, "aid": artist_id}
        )
        db.commit()
    except Exception as _ne:
        logger.warning(f"_clear_waitlist_offer_notification gig={gig_id} artist={artist_id}: {_ne}")


def _has_active_waitlist(db, gig_id: int) -> bool:
    """Return True if there are any artists on the waitlist OR with an active offer for this gig."""
    count = db.execute(
        text("SELECT COUNT(*) FROM gig_waitlist WHERE gig_id = :gid AND (offer_declined = 0 OR offer_declined IS NULL)"),
        {"gid": gig_id}
    ).scalar()
    if (count or 0) > 0:
        return True
    # Also check waitlist_offered — artist moved here when offer was sent
    offered = db.execute(
        text("SELECT COUNT(*) FROM waitlist_offered WHERE gig_id = :gid AND offer_expires_at > datetime('now')"),
        {"gid": gig_id}
    ).scalar()
    return (offered or 0) > 0


# ─── JOIN / LEAVE ─────────────────────────────────────────────────────────────

@router.post("/api/gigs/{gig_id}/waitlist")
@router.post("/api/gigs/{gig_id}/waitlist/join")
def join_waitlist(gig_id: int, artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Artist joins the waitlist for a booked gig."""
    # Audit fix (May 2026): use multi-user-aware access check so co-managers
    # of a multi-user artist account can join. Owner-only check rejected them.
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    artist = db.execute(
        text("SELECT id, name FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist:
        raise HTTPException(404, "Artist not found")

    gig = db.execute(
        text("SELECT id, status, venue_id, date, start_time, end_time, pay, title, artist_type FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    if gig["status"] not in ("booked", "pending_contract", "awaiting_venue_contract", "pending_venue_approval"):
        # For multi-slot gigs, also allow waitlist join when some slots are taken
        has_taken_slot = db.execute(
            text("SELECT 1 FROM gig_slots WHERE gig_id=:gid AND status IN ('booked','pending_contract') LIMIT 1"),
            {"gid": gig_id}
        ).first()
        if not has_taken_slot:
            raise HTTPException(400, "This gig is not fully booked — you can book it directly")

    existing = db.execute(
        text("SELECT id, offer_declined FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()
    if existing:
        if existing["offer_declined"]:
            # Previously declined — reset so they can rejoin
            db.execute(
                text("UPDATE gig_waitlist SET offer_declined = 0, offer_sent = 0, offer_expires_at = NULL WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": artist_id}
            )
            db.commit()
        else:
            return {"status": "already_on_waitlist"}
    else:
        db.execute(
            text("INSERT INTO gig_waitlist (gig_id, artist_id) VALUES (:gid, :aid)"),
            {"gid": gig_id, "aid": artist_id}
        )
        db.commit()

    row_id = db.execute(
        text("SELECT id FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
        {"gid": gig_id, "aid": artist_id}
    ).scalar()
    pos = _get_position(db, gig_id, row_id)

    return {"status": "joined", "position": pos}


@router.delete("/api/gigs/{gig_id}/waitlist")
def leave_waitlist(gig_id: int, artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Artist leaves the waitlist or declines an active offer."""
    # Audit fix (May 2026): multi-user-aware access check.
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)

    # Check if this artist holds an active offer in waitlist_offered
    had_active_offer = db.execute(
        text("""SELECT id FROM waitlist_offered
                WHERE gig_id = :gid AND artist_id = :aid
                  AND offer_expires_at > datetime('now')"""),
        {"gid": gig_id, "aid": artist_id}
    ).first()

    # Delete from waitlist_offered always
    db.execute(
        text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
        {"gid": gig_id, "aid": artist_id}
    )
    if had_active_offer:
        # Artist is declining an active offer — mark as declined so blast emails exclude them
        # Keep the row with offer_declined=1 so fire_cancelled_gig_blast skips them
        existing = db.execute(
            text("SELECT id FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
            {"gid": gig_id, "aid": artist_id}
        ).first()
        if existing:
            db.execute(
                text("UPDATE gig_waitlist SET offer_declined = 1 WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": artist_id}
            )
        else:
            # Insert a declined record so blast knows to skip this artist
            db.execute(
                text("INSERT INTO gig_waitlist (gig_id, artist_id, offer_declined, created_at) VALUES (:gid, :aid, 1, CURRENT_TIMESTAMP)"),
                {"gid": gig_id, "aid": artist_id}
            )
    else:
        # Just leaving waitlist (not declining an offer) — remove entirely
        db.execute(
            text("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
            {"gid": gig_id, "aid": artist_id}
        )
    db.commit()
    # Either way (decline or plain leave), clear the in-app offer notification
    _clear_waitlist_offer_notification(db, gig_id, artist_id)

    # Always advance — whether they had an active offer or were just waiting.
    # If no more artists remain, this exhausts the waitlist and triggers the blast + venue email.
    logger.info(f"[WAITLIST] leave_waitlist: artist={artist_id} left gig={gig_id}, advancing")
    try:
        advance_waitlist_offer(db, gig_id)
    except Exception as _e:
        logger.warning(f"[WAITLIST] advance_waitlist_offer after leave: {_e}", exc_info=True)

    return {"status": "removed", "had_offer": bool(had_active_offer)}


# ─── STATUS ───────────────────────────────────────────────────────────────────

@router.get("/api/gigs/{gig_id}/waitlist/status")
def get_waitlist_status(gig_id: int, artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    # Audit fix (May 2026): multi-user-aware access check.
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    row = db.execute(
        text("""SELECT id, offer_sent, offer_declined, offer_expires_at
                FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid
                  AND (offer_declined = 0 OR offer_declined IS NULL)"""),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()

    # Also check waitlist_offered — row is deleted from gig_waitlist on notification
    # Only count as active if not yet expired
    offered_row = db.execute(
        text("""SELECT offer_expires_at FROM waitlist_offered
                WHERE gig_id = :gid AND artist_id = :aid
                  AND offer_expires_at > datetime('now')"""),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()

    if not row and not offered_row:
        return {"on_waitlist": False, "position": None, "has_offer": False}

    if offered_row:
        # Artist was notified and removed from visible waitlist — they hold an active offer
        now_iso = datetime.now(timezone.utc).isoformat()[:19]
        exp = str(offered_row["offer_expires_at"])
        has_offer = exp > now_iso if exp else True
        return {"on_waitlist": True, "position": 1, "total": 1, "has_offer": has_offer,
                "offer_expires_at": exp}

    pos = _get_position(db, gig_id, row["id"])
    total = db.execute(
        text("SELECT COUNT(*) FROM gig_waitlist WHERE gig_id = :gid AND (offer_declined = 0 OR offer_declined IS NULL)"),
        {"gid": gig_id}
    ).scalar()
    has_offer = bool(
        row["offer_sent"] == 1
        and not row["offer_declined"]
        and (row["offer_expires_at"] is None or str(row["offer_expires_at"]) > datetime.now(timezone.utc).isoformat()[:19])
    )
    return {"on_waitlist": True, "position": pos, "total": total, "has_offer": has_offer,
            "offer_expires_at": str(row["offer_expires_at"]) if row["offer_expires_at"] else None}


# ─── VENUE: VIEW WAITLIST ─────────────────────────────────────────────────────

@router.get("/api/venues/{venue_id}/gigs/{gig_id}/waitlist")
def get_gig_waitlist(venue_id: int, gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Venue sees who is waitlisted for one of their gigs."""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)

    rows = db.execute(
        text("""
            SELECT w.id, w.artist_id, w.created_at, w.notified, w.notified_at,
                   w.offer_sent, w.offer_expires_at, w.offer_declined,
                   a.name as artist_name, a.artist_type,
                   (SELECT COUNT(*) FROM gig_waitlist w2 WHERE w2.gig_id = w.gig_id AND w2.id <= w.id) as position
            FROM gig_waitlist w
            JOIN artists a ON a.id = w.artist_id
            WHERE w.gig_id = :gid
            ORDER BY w.id ASC
        """),
        {"gid": gig_id}
    ).mappings().all()

    return [dict(r) for r in rows]


# ─── ARTIST: LIST MY WAITLISTS ────────────────────────────────────────────────

@router.get("/api/artists/{artist_id}/waitlist")
def get_artist_waitlists(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    # Audit fix (May 2026): multi-user-aware access check.
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)

    # Rows from gig_waitlist (waiting or declined)
    rows = db.execute(
        text("""
            SELECT w.id, w.gig_id, w.created_at, w.notified,
                   w.offer_sent, w.offer_expires_at,
                   g.date, g.start_time, g.end_time, g.pay, g.title, g.artist_type, g.status,
                   v.venue_name, v.id as venue_id,
                   (SELECT COUNT(*) FROM gig_waitlist w2 WHERE w2.gig_id = w.gig_id AND w2.id <= w.id) as position,
                   (SELECT COUNT(*) FROM gig_waitlist w3 WHERE w3.gig_id = w.gig_id) as total_waiting,
                   0 as has_offer
            FROM gig_waitlist w
            JOIN gigs g ON g.id = w.gig_id
            LEFT JOIN venues v ON v.id = g.venue_id
            WHERE w.artist_id = :aid
              AND (w.offer_declined = 0 OR w.offer_declined IS NULL)
              AND g.date >= date('now', '-1 day')
            ORDER BY g.date ASC
        """),
        {"aid": artist_id}
    ).mappings().all()

    # Also include active offers from waitlist_offered (row moved here on notification)
    offered_rows = db.execute(
        text("""
            SELECT wo.id, wo.gig_id, wo.created_at, 0 as notified,
                   1 as offer_sent, wo.offer_expires_at,
                   g.date, g.start_time, g.end_time, g.pay, g.title, g.artist_type, g.status,
                   v.venue_name, v.id as venue_id,
                   1 as position, 1 as total_waiting,
                   1 as has_offer
            FROM waitlist_offered wo
            JOIN gigs g ON g.id = wo.gig_id
            LEFT JOIN venues v ON v.id = g.venue_id
            WHERE wo.artist_id = :aid
              AND g.date >= date('now', '-1 day')
              AND wo.offer_expires_at > datetime('now')
            ORDER BY g.date ASC
        """),
        {"aid": artist_id}
    ).mappings().all()

    # Combine, dedup by gig_id (offered takes priority)
    seen_gig_ids = set()
    result = []
    for r in list(offered_rows) + list(rows):
        if r["gig_id"] not in seen_gig_ids:
            seen_gig_ids.add(r["gig_id"])
            result.append(dict(r))
    return sorted(result, key=lambda x: x["date"])


# ─── OFFER RESPONSE (email deep-link) ─────────────────────────────────────────

@router.get("/api/waitlist/respond")
@limiter.limit("20/minute")
def respond_to_offer(request: Request, token: str, action: str, db=Depends(get_db)):
    """
    Artist clicks Book or Decline in their email.
    action = "book" or "decline"
    """
    from fastapi.responses import HTMLResponse, RedirectResponse

    def _splash(icon: str, heading: str, message: str, base_url: str = "https://gigsfill.com") -> HTMLResponse:
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GigsFill</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0a0e1a; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; }}
    .card {{ background: #111827; border: 1px solid rgba(255,255,255,0.08); border-radius: 16px;
             padding: 40px 48px; max-width: 480px; width: 100%; text-align: center; box-shadow: 0 20px 60px rgba(0,0,0,0.6); }}
    .icon {{ font-size: 3.5rem; margin-bottom: 20px; }}
    h1 {{ font-size: 1.5rem; font-weight: 700; color: #f1f5f9; margin-bottom: 12px; }}
    p {{ font-size: 0.95rem; color: #94a3b8; line-height: 1.6; margin-bottom: 24px; }}
    a.btn {{ display: inline-block; background: linear-gradient(135deg,#06b6d4,#0891b2);
             color: #000; font-weight: 700; font-size: 0.9rem; padding: 12px 28px;
             border-radius: 8px; text-decoration: none; transition: opacity 0.2s; }}
    a.btn:hover {{ opacity: 0.85; }}
    .logo {{ font-size: 1.1rem; font-weight: 800; color: #06b6d4; letter-spacing: 0.04em; margin-bottom: 32px; }}
  </style>
</head>
<body>
  <div class="logo">
    <img src="{base_url}/app/static/img/gigsfill-logo.png" alt="GigsFill" style="height:40px;width:auto;">
  </div>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{heading}</h1>
    <p>{message}</p>
    <a href="{base_url}/app/artist-book-gigs.html" class="btn">View My Calendar</a>
    <p style="margin-top:16px;font-size:0.82rem;color:#64748b;">
      <a href="{base_url}/login" style="color:#06b6d4;">Log in</a> to manage your gigs and waitlists.
    </p>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html)

    base_url = db.execute(
        text("SELECT setting_value FROM platform_settings WHERE setting_key = 'site_url'")
    ).scalar() or "https://gigsfill.com"

    # Check gig_waitlist first (legacy), then waitlist_offered (new: row deleted on notification)
    row = db.execute(
        text("SELECT * FROM gig_waitlist WHERE offer_token = :tok"),
        {"tok": token}
    ).mappings().first()

    _from_offered_table = False
    if not row:
        # Check waitlist_offered — artist was already removed from waitlist on notification
        offered = db.execute(
            text("SELECT * FROM waitlist_offered WHERE offer_token = :tok"),
            {"tok": token}
        ).mappings().first()
        if offered:
            # Reconstruct a minimal row-like dict for the rest of the handler
            row = {
                "id": None,
                "gig_id": offered["gig_id"],
                "artist_id": offered["artist_id"],
                "user_id": offered["user_id"],
                "offer_token": token,
                "offer_expires_at": offered["offer_expires_at"],
            }
            _from_offered_table = True

    if not row:
        return _splash("🔗", "Link No Longer Valid", "This offer link has already been used or has expired.", base_url)

    now_utc = datetime.now(timezone.utc)

    # Check expiry
    if row["offer_expires_at"]:
        expires = datetime.fromisoformat(str(row["offer_expires_at"])).replace(tzinfo=timezone.utc) \
            if not str(row["offer_expires_at"]).endswith("+00:00") and "+" not in str(row["offer_expires_at"]) \
            else datetime.fromisoformat(str(row["offer_expires_at"]))
        if now_utc > expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else now_utc > expires:
            return _splash("⏰", "Offer Expired", "Sorry, this offer has expired. The next artist on the waitlist has been contacted.", base_url)

    gig = db.execute(
        text("SELECT * FROM gigs WHERE id = :gid"),
        {"gid": row["gig_id"]}
    ).mappings().first()

    if not gig or gig["status"] not in ("open",):
        return _splash("🎸", "Gig No Longer Available", "Sorry, this gig has already been filled. Keep an eye out for future openings!", base_url)

    if action == "decline":
        # BUG FIX (May 11 2026): previously this path DELETEd the row. Compare
        # to the in-app DELETE /api/gigs/{id}/waitlist which sets offer_declined=1
        # and explicitly comments "so fire_cancelled_gig_blast skips them."
        # When the row was deleted here, the subsequent advance_waitlist_offer
        # → blast saw no declined-row and emailed the artist back. We must
        # preserve a row with offer_declined=1 in gig_waitlist so the blast
        # exclusion subquery finds them.
        if _from_offered_table:
            # Active offer lived in waitlist_offered; tear it down, then
            # insert/upsert a declined marker into gig_waitlist.
            db.execute(text("DELETE FROM waitlist_offered WHERE offer_token = :tok"), {"tok": token})
            _existing = db.execute(
                text("SELECT id FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": row["gig_id"], "aid": row["artist_id"]}
            ).first()
            if _existing:
                db.execute(
                    text("UPDATE gig_waitlist SET offer_declined = 1 WHERE id = :wid"),
                    {"wid": _existing[0]}
                )
            else:
                db.execute(
                    text("INSERT INTO gig_waitlist (gig_id, artist_id, offer_declined, created_at) VALUES (:gid, :aid, 1, CURRENT_TIMESTAMP)"),
                    {"gid": row["gig_id"], "aid": row["artist_id"]}
                )
        elif row["id"]:
            # Row still in gig_waitlist — flag declined instead of deleting.
            db.execute(
                text("UPDATE gig_waitlist SET offer_declined = 1, offer_sent = 0, offer_expires_at = NULL WHERE id = :wid"),
                {"wid": row["id"]}
            )
        db.commit()
        # Clear the in-app "Waitlist Spot Available" notification for this artist
        _clear_waitlist_offer_notification(db, row["gig_id"], row["artist_id"])
        advance_waitlist_offer(db, row["gig_id"])
        return _splash(
            "👋", "No Problem!",
            "You've been removed from this waitlist. We'll keep you in mind for future gigs at this venue.",
            base_url
        )

    if action == "book":
        # DO NOT delete waitlist_offered yet — keep the row so:
        # 1. _has_active_waitlist remains true (venue calendar stays in waitlist mode, not amber blast)
        # 2. The decline link in the email stays valid if artist changes their mind
        # 3. The artist can still be shown the correct modal on the calendar
        # Cleanup happens in the booking endpoint when artist actually completes the booking.
        artist_id = row["artist_id"]
        # Clear the "Waitlist Spot Available" Activity Center notification —
        # they've consumed the offer (by accepting). Whether or not they
        # complete the booking flow, the notification has served its purpose.
        _clear_waitlist_offer_notification(db, row["gig_id"], artist_id)
        url = f"{base_url}/app/artist-book-gigs.html?artist_id={artist_id}&open_gig={row['gig_id']}&waitlist_token={token}"
        return RedirectResponse(url=url, status_code=302)

    return _splash("❓", "Unknown Action", "Something went wrong. Please return to your calendar.", base_url)


# ─── NOTIFY WAITLIST (called internally when gig re-opens) ───────────────────

def cleanup_gig_waitlist(db, gig_id: int):
    """Remove all waitlist entries for a gig (called when gig is deleted or fully cancelled)."""
    try:
        db.execute(
            text("DELETE FROM gig_waitlist WHERE gig_id = :gid"),
            {"gid": gig_id}
        )
        db.commit()
        logger.info(f"Cleared waitlist for deleted/cancelled gig {gig_id}")
    except Exception as e:
        logger.error(f"cleanup_gig_waitlist error for gig {gig_id}: {e}")


def notify_waitlist(db, gig_id: int):
    """
    When a gig opens up, send sequential offers to waitlisted artists.
    Within 36h: 2-hour window per artist. Otherwise: 24-hour window.
    Blast fires only after all waitlisted artists have passed.
    """
    try:
        # Reset offer cycle state so offers can be re-sent after a new cancellation.
        # Only reset offer_sent/token/expiry — NOT offer_declined.
        # Artists who explicitly said "Not Available" stay excluded from future offers.
        db.execute(
            text("""UPDATE gig_waitlist
                     SET offer_sent=0, offer_token=NULL, offer_expires_at=NULL
                     WHERE gig_id=:gid AND (offer_declined=0 OR offer_declined IS NULL)"""),
            {"gid": gig_id}
        )
        db.commit()
        # Verify reset worked
        _check = db.execute(text("SELECT COUNT(*) FROM gig_waitlist WHERE gig_id=:gid AND offer_sent=0"), {"gid": gig_id}).scalar()
        logger.info(f"[NOTIFY_WAITLIST] After reset: {_check} entries with offer_sent=0 for gig {gig_id}")

        gig = db.execute(
            text("""
                SELECT g.*, v.venue_name, v.id as venue_id, v.city, v.state,
                       v.latitude as venue_lat, v.longitude as venue_lng,
                       COALESCE(ven.radius_miles, 20) as blast_radius_miles
                FROM gigs g
                LEFT JOIN venues v ON v.id = g.venue_id
                LEFT JOIN venue_email_notifications ven ON ven.venue_id = g.venue_id AND ven.notification_key = 'radius_blast'
                WHERE g.id = :gid
            """),
            {"gid": gig_id}
        ).mappings().first()

        if not gig:
            return

        # Decide: sequential offer vs. immediate blast
        gig_date_str = str(gig.get("date", ""))
        start_time_str = str(gig.get("start_time", "00:00"))
        try:
            gig_dt = datetime.fromisoformat(f"{gig_date_str}T{start_time_str}")
        except Exception:
            gig_dt = _get_platform_now(db) + timedelta(days=7)

        hours_until = (gig_dt - _get_platform_now(db)).total_seconds() / 3600

        # Always send sequential offer to waitlisted artists first.
        # If within 36h, use a 2-hour window per artist instead of 24h.
        # The blast fires only after all waitlist artists have passed.
        _send_sequential_offer(db, gig_id, gig, hours_until=hours_until)

    except Exception as e:
        logger.error(f"notify_waitlist error for gig {gig_id}: {e}")


def _build_open_slots_html(db, gig_id: int, gig: dict) -> str:
    """Build HTML rows for open slots only — used in waitlist offer emails."""
    try:
        from sqlalchemy import text as _st
        from backend.services.notification_service import format_time_12hr as _fmt
        slots = db.execute(_st("""
            SELECT slot_number, start_time, end_time, pay, artist_type
            FROM gig_slots WHERE gig_id=:gid AND status='open' ORDER BY slot_number
        """), {"gid": gig_id}).mappings().all()
        if not slots:
            return ""
        sl = [dict(s) for s in slots]
        gp = float(gig.get("pay") or 0)
        ROW = '<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">{label}</td><td style="padding:6px 0;font-size:14px;color:{color};font-weight:{weight};">{value}</td></tr>'
        SEP = '<tr><td colspan="2" style="padding:6px 0;border-top:1px solid #e5e7eb;"></td></tr>'
        HDR = '<tr><td colspan="2" style="padding:8px 0 4px 0;font-size:13px;font-weight:700;color:#374151;">Slot {num}</td></tr>'
        html = ""
        for i, s in enumerate(sl):
            if i > 0: html += SEP
            if len(sl) > 1: html += HDR.format(num=s.get("slot_number") or (i+1))
            t0 = _fmt(s.get("start_time") or "")
            t1 = _fmt(s.get("end_time") or "")
            html += ROW.format(label="Time", color="#111827", weight="500", value=f"{t0} \u2013 {t1}" if t1 else t0)
            p = float(s.get("pay") or gp)
            html += ROW.format(label="Pay", color="#059669", weight="600", value=f"${p:,.2f}")
            at = s.get("artist_type") or gig.get("artist_type") or ""
            if at: html += ROW.format(label="Type", color="#111827", weight="500", value=at)
        return html
    except Exception as e:
        logger.warning(f"_build_open_slots_html error gig {gig_id}: {e}")
        return ""


def _send_sequential_offer(db, gig_id: int, gig, hours_until: float = 999):
    """Send an exclusive offer to the top unnotified artist on the waitlist.
    Offer window is tiered: >36h=24h, 36h-4h=2h, <4h=30min."""
    try:
        # Find the top artist who hasn't been offered yet and hasn't declined
        entry = db.execute(
            text("""
                SELECT w.id, w.artist_id, a.name as artist_name, u.email, u.id as user_id
                FROM gig_waitlist w
                JOIN artists a ON a.id = w.artist_id
                JOIN users u ON u.id = a.user_id
                WHERE w.gig_id = :gid
                  AND (w.offer_sent = 0 OR w.offer_sent IS NULL)
                  AND (w.offer_declined = 0 OR w.offer_declined IS NULL)
                ORDER BY w.id ASC
                LIMIT 1
            """),
            {"gid": gig_id}
        ).mappings().first()

        if not entry:
            logger.info(f"[WAITLIST] No more waitlisted artists for gig {gig_id} — exhausted, firing blast")

            # Check venue's radius blast setting BEFORE firing, so email can report accurately
            venue_id = gig.get("venue_id")
            radius_notif = db.execute(
                text("SELECT enabled, radius_miles FROM venue_email_notifications WHERE venue_id = :vid AND notification_key = 'radius_blast'"),
                {"vid": venue_id}
            ).mappings().first()
            radius_enabled = (radius_notif is None) or bool(radius_notif.get("enabled", True))
            radius_miles = int((radius_notif and radius_notif.get("radius_miles")) or 20) if radius_enabled else 0
            logger.info(f"[WAITLIST] radius_enabled={radius_enabled}, radius_miles={radius_miles}, venue_id={venue_id}")

            # Find the artist who cancelled — stored directly on the gig row
            try:
                cancelled_artist_id = db.execute(
                    text("SELECT last_cancelled_artist_id FROM gigs WHERE id = :gid"),
                    {"gid": gig_id}
                ).scalar()
            except Exception:
                cancelled_artist_id = None  # column may not exist yet on older deployments
            logger.info(f"[WAITLIST] cancelled_artist_id={cancelled_artist_id} (excluded from blast)")

            try:
                from backend.routes.gigs import fire_cancelled_gig_blast
                fire_cancelled_gig_blast(db, gig_id, venue_id,
                                         exclude_artist_id=cancelled_artist_id)
            except Exception as blast_err:
                logger.error(f"[WAITLIST] Post-waitlist-exhausted blast error: {blast_err}", exc_info=True)

            # Email the venue explaining what happened (within 14 days of gig date)
            try:
                gig_date_str = str(gig.get("date", ""))[:10]
                start_time_str = str(gig.get("start_time", "00:00"))
                # Use platform now (correct timezone) for all comparisons
                _now_platform = _get_platform_now(db).replace(tzinfo=None)
                gig_dt = datetime.fromisoformat(f"{gig_date_str}T{start_time_str[:5]}")
                # days_away from platform perspective (not raw UTC server date)
                _days_away = (gig_dt.date() - _now_platform.date()).days
                hours_until = max(0.0, (gig_dt - _now_platform).total_seconds() / 3600)
                # Send for gigs up to 14 days away OR same day (even if start time just passed)
                logger.info(f"[WAITLIST] gig_dt={gig_dt}, now_platform={_now_platform}, days_away={_days_away}, hours_until={hours_until:.1f}, will_email={-1 <= _days_away <= 14}")
                if -1 <= _days_away <= 14:
                    _send_waitlist_exhausted_email(db, gig_id, gig, hours_until,
                                                   radius_enabled=radius_enabled,
                                                   radius_miles=radius_miles)
            except Exception as _wee:
                logger.warning(f"[WAITLIST] waitlist_exhausted_email error: {_wee}", exc_info=True)
            return

        from backend.email_service import EmailService
        from backend.services.email_dispatch import format_email_date
        from backend.services.notification_service import format_time_12hr

        token = secrets.token_urlsafe(32)
        offer_hours = _offer_hours_for_gig(hours_until)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=offer_hours)

        db.execute(
            text("""UPDATE gig_waitlist
                    SET offer_sent = 1, offer_sent_at = CURRENT_TIMESTAMP,
                        offer_expires_at = :exp, offer_token = :tok, notified = 1, notified_at = CURRENT_TIMESTAMP
                    WHERE id = :wid"""),
            {"exp": expires_at.isoformat(), "tok": token, "wid": entry["id"]}
        )
        db.commit()

        base_url = db.execute(
            text("SELECT setting_value FROM platform_settings WHERE setting_key = 'site_url'")
        ).scalar() or "https://gigsfill.com"

        # Use the highest open-slot pay as the base (for multi-slot gigs where slots may
        # have different pay rates than the gig-level pay field).
        try:
            _slot_pay = db.execute(
                text("SELECT MAX(pay) FROM gig_slots WHERE gig_id=:gid AND status='open'"),
                {"gid": gig_id}
            ).scalar()
            gig_base_pay = float(_slot_pay or 0) or float(gig.get("pay") or 0)
        except Exception:
            gig_base_pay = float(gig.get("pay") or 0)
        # Apply per-artist pay override: use MAX(slot_pay, override) only when override is actually set.
        # If pay_dollars_override IS NULL the venue has not set an override — use slot pay as-is.
        try:
            _ov = db.execute(
                text("""SELECT pay_dollars_override, pay_cents_override
                        FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid"""),
                {"vid": gig.get("venue_id"), "aid": entry["artist_id"]}
            ).mappings().first()
            if _ov and _ov["pay_dollars_override"] is not None:
                override_pay = float(_ov["pay_dollars_override"]) + float(_ov["pay_cents_override"] or 0) / 100
                pay = max(gig_base_pay, override_pay)
            else:
                pay = gig_base_pay
        except Exception:
            pay = gig_base_pay
        end_time_str = format_time_12hr(gig.get("end_time")) if gig.get("end_time") else ""
        book_url = f"{base_url}/api/waitlist/respond?token={token}&action=book"
        decline_url = f"{base_url}/api/waitlist/respond?token={token}&action=decline"

        _wl_slots_html = _build_open_slots_html(db, gig_id, gig)
        logger.info(f"[OFFER] gig_id={gig_id} slots_html_len={len(_wl_slots_html)} preview={_wl_slots_html[:80] if _wl_slots_html else 'EMPTY'}")

        email_service = EmailService(db)
        variables = {
            "artist_name": entry["artist_name"] or "Artist",
            "venue_name": gig.get("venue_name") or "the venue",
            "date": format_email_date(gig.get("date", "")),
            "start_time": format_time_12hr(gig.get("start_time")),
            "end_time": end_time_str,
            "pay": f"{pay:,.2f}",
            "title": gig.get("title") or "",
            "artist_type": gig.get("artist_type") or "",
            "book_url": book_url,
            "decline_url": decline_url,
            "expires_hours": "30 minutes" if offer_hours < 1 else f"{int(offer_hours)} hour{'s' if offer_hours != 1 else ''}",
            "offer_deadline": _format_deadline(expires_at, db),
            "booking_url": book_url,
            "slots_html": _wl_slots_html,
        }
        email_service.send_notification_email(
            user_email=entry["email"],
            user_id=entry["user_id"],
            notification_type="waitlist_offer",
            variables=variables,
        )

        # Preserve token so respond_to_offer still works after row deletion
        db.execute(
            text("""INSERT OR IGNORE INTO waitlist_offered
                    (gig_id, artist_id, user_id, offer_token, offer_expires_at)
                    VALUES (:gig, :aid, :uid, :tok, :exp)"""),
            {"gig": gig_id, "aid": entry["artist_id"],
             "uid": entry["user_id"], "tok": token, "exp": expires_at.isoformat()}
        )

        # Remove from visible waitlist immediately — artist has been notified
        db.execute(
            text("DELETE FROM gig_waitlist WHERE id = :wid"),
            {"wid": entry["id"]}
        )
        db.commit()

        # Add Activity Center notification for the artist
        try:
            from backend.services.notification_service import create_notification
            from backend.services.email_dispatch import format_email_date
            _deadline_display = _format_deadline(expires_at, db)
            _deadline_phrase = (
                f"You have until {_deadline_display} to book it!"
                if _deadline_display else f"You have {offer_hours} hours to book it!"
            )
            _gig_date = format_email_date(gig.get("date", ""))
            _venue = gig.get("venue_name") or "the venue"
            create_notification(
                db,
                user_id=entry["user_id"],
                notification_type="waitlist_offer",
                title=f"⚡ Waitlist Spot Available — {_venue}",
                message=(
                    f"You're next on the waitlist for a gig at {_venue} on {_gig_date}. "
                    f"{_deadline_phrase} "
                    f"Check your email for the Book / Decline links."
                ),
                gig_id=gig_id,
                venue_id=gig.get("venue_id"),
                artist_id=entry["artist_id"],
            )
            db.commit()
        except Exception as _ne:
            logger.warning(f"Could not create waitlist notification: {_ne}")

        logger.info(f"Sent sequential waitlist offer to artist {entry['artist_id']} for gig {gig_id}, expires {expires_at}")

    except Exception as e:
        logger.error(f"_send_sequential_offer error for gig {gig_id}: {e}")


def _send_waitlist_exhausted_email(db, gig_id: int, gig, hours_until: float,
                                    radius_enabled: bool = False, radius_miles: int = 20):
    """Email the venue + create Activity Center notification when waitlist exhausted.
    Explains what was automatically done (preferred blasted, radius blasted if enabled)."""
    try:
        from backend.email_service import EmailService
        from backend.services.email_dispatch import format_email_date
        from backend.services.notification_service import format_time_12hr, create_notification
        from backend.utils import get_all_entity_users

        venue_id = gig.get("venue_id")
        venue_name = gig.get("venue_name") or "Your Venue"
        gig_date = format_email_date(str(gig.get("date", "")))
        start_time = format_time_12hr(gig.get("start_time", ""))
        end_time = gig.get("end_time")
        end_time_str = f" – {format_time_12hr(end_time)}" if end_time else ""
        # FIX (May 2026): format hours_until as human-readable "X hours and Y minutes"
        # instead of "3.9 hours". Handles edge cases (<1hr → just minutes, exact hours → no minutes).
        # Template was updated to remove the trailing " hours" — this string is now self-contained.
        _h_total_minutes = max(0, int(round(hours_until * 60)))
        _h_hrs  = _h_total_minutes // 60
        _h_mins = _h_total_minutes % 60
        if _h_hrs <= 0:
            hours_display = f"{_h_mins} minute{'s' if _h_mins != 1 else ''}"
        elif _h_mins <= 0:
            hours_display = f"{_h_hrs} hour{'s' if _h_hrs != 1 else ''}"
        else:
            hours_display = f"{_h_hrs} hour{'s' if _h_hrs != 1 else ''} and {_h_mins} minute{'s' if _h_mins != 1 else ''}"

        # Build blast summary for email
        if radius_enabled:
            blast_summary = f"We've already notified all of your preferred artists and all artists within {radius_miles} miles of your venue."
            radius_line = f"✅ All artists within {radius_miles} miles notified"
        else:
            blast_summary = "We've already notified all of your preferred artists. Radius blasting is not enabled for your venue."
            radius_line = "ℹ️ Radius blast not enabled (update in Email Settings)"

        # Build slots_html for multi-slot gigs
        try:
            from sqlalchemy import text as _wt
            _open_slots = db.execute(_wt("""
                SELECT slot_number, start_time, end_time, pay, artist_type, band_formats, styles, status
                FROM gig_slots WHERE gig_id=:gid ORDER BY slot_number
            """), {"gid": gig_id}).mappings().all()
            _slot_list = [dict(s) for s in _open_slots] if _open_slots else []
            _gig_pay = float(gig.get("pay") or 0)
            _at = gig.get("artist_type") or ""
            _slots_html = ""
            ROW = '<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">{label}</td><td style="padding:6px 0;font-size:14px;color:{color};font-weight:{weight};">{value}</td></tr>'
            SEP = '<tr><td colspan="2" style="padding:6px 0;border-top:1px solid #e5e7eb;"></td></tr>'
            HDR = '<tr><td colspan="2" style="padding:8px 0 4px 0;font-size:13px;font-weight:700;color:#374151;">Slot {num}</td></tr>'
            for i, s in enumerate(_slot_list):
                if i > 0: _slots_html += SEP
                if len(_slot_list) > 1:
                    _slots_html += HDR.format(num=s.get("slot_number") or (i+1))
                t0 = format_time_12hr(s.get("start_time") or "")
                t1 = format_time_12hr(s.get("end_time") or "")
                _slots_html += ROW.format(label="Time", color="#111827", weight="500", value=f"{t0} \u2013 {t1}" if t1 else t0)
                _pay = float(s.get("pay") or _gig_pay)
                _slots_html += ROW.format(label="Pay", color="#059669", weight="600", value=f"${_pay:,.2f}")
        except Exception as _she:
            _slots_html = ""
            logger.warning(f"slots_html build failed in exhausted email: {_she}")

        email_service = EmailService(db)
        venue_users = get_all_entity_users(db, "venue", venue_id)
        _exhausted_sent = set()
        for vu in venue_users:
            if not vu.get("email") or vu["email"] in _exhausted_sent:
                continue
            _exhausted_sent.add(vu["email"])
            try:
                # Bypass preferences — venue must always receive this summary
                from backend.email_service import _smtp_send as _we_smtp
                from email.mime.multipart import MIMEMultipart as _WM
                from email.mime.text import MIMEText as _WT
                from email.utils import formataddr as _wefa

                _we_vars = {
                    "venue_name":    venue_name,
                    "venue_id":      str(venue_id),
                    "gig_id":        str(gig_id),
                    "date":          gig_date,
                    "start_time":    start_time,
                    "end_time_str":  end_time_str,
                    "hours_until":   hours_display,
                    "blast_summary": blast_summary,
                    "radius_line":   radius_line,
                    "slots_html":    _slots_html,
                }
                tpl = email_service.get_template("waitlist_exhausted_venue")
                if tpl and email_service.enabled:
                    subj = email_service.render_template(tpl['subject'], _we_vars)
                    body = email_service.render_template(tpl['body'], _we_vars)
                    msg = _WM("alternative")
                    msg['Subject'] = subj
                    msg['From'] = _wefa((email_service.from_name, email_service.from_email)) if email_service.from_name else email_service.from_email
                    msg['To'] = vu["email"]
                    msg['X-Mailer'] = 'GigsFill'
                    msg.attach(_WT(body, 'html'))
                    _we_smtp(email_service.smtp_server, email_service.smtp_port,
                             email_service.smtp_username, email_service.smtp_password, msg)
                else:
                    email_service.send_notification_email(
                        user_email=vu["email"], user_id=vu["user_id"],
                        notification_type="waitlist_exhausted_venue", variables=_we_vars)
                logger.info(f"Sent waitlist_exhausted_venue email to {vu['email']} for gig {gig_id}")
            except Exception as _se:
                logger.warning(f"waitlist_exhausted send failed for {vu.get('email')}: {_se}")

            # Activity Center notification — inform venue what happened, no action needed
            ac_message = (
                f"Your {start_time}{end_time_str} gig on {gig_date} had a cancellation and the waitlist "
                f"was exhausted. {blast_summary} The gig is still open."
            )
            try:
                # Verify gig_id exists before inserting (FK constraint)
                _gig_exists = db.execute(
                    text("SELECT 1 FROM gigs WHERE id = :gid"), {"gid": gig_id}
                ).first()
                create_notification(
                    db,
                    user_id=vu["user_id"],
                    notification_type="waitlist_exhausted_venue",
                    title="⚠️ Gig Unfilled — Artists Notified",
                    message=ac_message,
                    gig_id=gig_id if _gig_exists else None,
                    venue_id=venue_id,
                )
            except Exception as _ne:
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.warning(f"Activity Center notification failed for venue {venue_id}: {_ne}")

    except Exception as e:
        logger.error(f"_send_waitlist_exhausted_email error gig {gig_id}: {e}")


def advance_waitlist_offer(db, gig_id: int):
    """Called when the current offer is declined or expires. Moves to next artist."""
    logger.info(f"[WAITLIST] advance_waitlist_offer called for gig {gig_id}")
    try:
        gig = db.execute(
            text("""
                SELECT g.*, v.venue_name, v.city, v.state,
                       v.latitude as venue_lat, v.longitude as venue_lng
                FROM gigs g LEFT JOIN venues v ON v.id = g.venue_id
                WHERE g.id = :gid
            """),
            {"gid": gig_id}
        ).mappings().first()
        if not gig:
            logger.warning(f"[WAITLIST] advance_waitlist_offer: gig {gig_id} not found")
            return
        logger.info(f"[WAITLIST] advance_waitlist_offer: gig {gig_id} date={gig.get('date')} status={gig.get('status')}")
        gig_date_str = str(gig.get("date", ""))
        start_time_str = str(gig.get("start_time", "00:00"))
        try:
            gig_dt = datetime.fromisoformat(f"{gig_date_str}T{start_time_str}")
        except Exception:
            gig_dt = _get_platform_now(db) + timedelta(days=7)
        hours_until = (gig_dt - _get_platform_now(db)).total_seconds() / 3600
        logger.info(f"[WAITLIST] advance_waitlist_offer: hours_until={hours_until:.1f}")
        _send_sequential_offer(db, gig_id, gig, hours_until=hours_until)
    except Exception as e:
        logger.error(f"[WAITLIST] advance_waitlist_offer error: {e}", exc_info=True)


def _blast_waitlist_and_nearby(db, gig_id: int, gig, hours_until: float):
    """<36hrs: Send sequential offer to waitlisted artist (tiered: 2h or 30min window) AND blast preferred/nearby artists."""
    try:
        from backend.email_service import EmailService
        from backend.services.email_dispatch import format_email_date
        from backend.services.notification_service import format_time_12hr

        email_service = EmailService(db)
        base_url = db.execute(
            text("SELECT setting_value FROM platform_settings WHERE setting_key = 'site_url'")
        ).scalar() or "https://gigsfill.com"
        gig_base_pay = float(gig.get("pay") or 0)
        end_time_str = format_time_12hr(gig.get("end_time")) if gig.get("end_time") else ""

        notified_ids = set()

        def _get_effective_pay(artist_id):
            try:
                _ov = db.execute(
                    text("""SELECT COALESCE(pay_dollars_override,0) + COALESCE(pay_cents_override,0)/100.0 as op
                            FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid"""),
                    {"vid": gig.get("venue_id"), "aid": artist_id}
                ).mappings().first()
                return max(gig_base_pay, float(_ov["op"] or 0)) if _ov else gig_base_pay
            except Exception:
                return gig_base_pay

        # Step 1: Send sequential OFFER (with 24hr book/decline links) to the top waitlisted artist
        # This sets offer_sent=1 so has_active_waitlist=1 → red blinking bubble on calendar
        waitlist_entry = db.execute(
            text("""
                SELECT w.id, w.artist_id, a.name as artist_name, u.email, u.id as user_id
                FROM gig_waitlist w
                JOIN artists a ON a.id = w.artist_id
                JOIN users u ON u.id = a.user_id
                WHERE w.gig_id = :gid
                  AND (w.offer_sent = 0 OR w.offer_sent IS NULL)
                  AND (w.offer_declined = 0 OR w.offer_declined IS NULL)
                ORDER BY w.id ASC
                LIMIT 1
            """),
            {"gid": gig_id}
        ).mappings().first()

        if waitlist_entry:
            import secrets
            token = secrets.token_urlsafe(32)
            _blast_offer_hours = _offer_hours_for_gig(hours_until)
            expires_at = datetime.now(timezone.utc) + timedelta(hours=_blast_offer_hours)
            db.execute(
                text("""UPDATE gig_waitlist
                        SET offer_sent = 1, offer_sent_at = CURRENT_TIMESTAMP,
                            offer_expires_at = :exp, offer_token = :tok,
                            notified = 1, notified_at = CURRENT_TIMESTAMP
                        WHERE id = :wid"""),
                {"exp": expires_at.isoformat(), "tok": token, "wid": waitlist_entry["id"]}
            )
            db.commit()

            pay = _get_effective_pay(waitlist_entry["artist_id"])
            book_url = f"{base_url}/api/waitlist/respond?token={token}&action=book"
            decline_url = f"{base_url}/api/waitlist/respond?token={token}&action=decline"

            email_service.send_notification_email(
                user_email=waitlist_entry["email"],
                user_id=waitlist_entry["user_id"],
                notification_type="waitlist_offer",
                variables={
                    "artist_name": waitlist_entry["artist_name"] or "Artist",
                    "venue_name": gig.get("venue_name") or "the venue",
                    "date": format_email_date(gig.get("date", "")),
                    "start_time": format_time_12hr(gig.get("start_time")),
                    "end_time": end_time_str,
                    "pay": f"{pay:,.2f}",
                    "title": gig.get("title") or "",
                    "artist_type": gig.get("artist_type") or "",
                    "book_url": book_url,
                    "decline_url": decline_url,
                    "expires_hours": "30 minutes" if _blast_offer_hours < 1 else f"{int(_blast_offer_hours)} hour{'s' if _blast_offer_hours != 1 else ''}",
                    "offer_deadline": _format_deadline(expires_at, db),
                    "booking_url": book_url,
                    "slots_html": _build_open_slots_html(db, gig_id, gig),
                },
            )
            notified_ids.add(waitlist_entry["artist_id"])
            logger.info(f"Sent urgent waitlist offer to artist {waitlist_entry['artist_id']} for gig {gig_id}")

        # Also notify any other waitlisted artists (not yet offered) with generic available email
        other_waitlisted = db.execute(
            text("""
                SELECT w.id, w.artist_id, a.name as artist_name, u.email, u.id as user_id
                FROM gig_waitlist w
                JOIN artists a ON a.id = w.artist_id
                JOIN users u ON u.id = a.user_id
                WHERE w.gig_id = :gid AND w.notified = 0
            """),
            {"gid": gig_id}
        ).mappings().all()

        for entry in other_waitlisted:
            if entry["artist_id"] in notified_ids:
                continue
            try:
                booking_url = f"{base_url}/app/artist-book-gigs.html?artist_id={entry['artist_id']}"
                pay = _get_effective_pay(entry["artist_id"])
                email_service.send_notification_email(
                    user_email=entry["email"],
                    user_id=entry["user_id"],
                    notification_type="waitlist_gig_available",
                    variables={
                        "artist_name": entry["artist_name"] or "Artist",
                        "venue_name": gig.get("venue_name") or "the venue",
                        "date": format_email_date(gig.get("date", "")),
                        "start_time": format_time_12hr(gig.get("start_time")),
                        "end_time": end_time_str,
                        "pay": f"{pay:,.2f}",
                        "title": gig.get("title") or "",
                        "artist_type": gig.get("artist_type") or "",
                        "booking_url": booking_url,
                    },
                )
                db.execute(
                    text("UPDATE gig_waitlist SET notified = 1, notified_at = CURRENT_TIMESTAMP WHERE id = :wid"),
                    {"wid": entry["id"]}
                )
                notified_ids.add(entry["artist_id"])
            except Exception as e:
                logger.error(f"Blast waitlist notify failed for artist {entry['artist_id']}: {e}")

        # Step 2: ALSO blast preferred artists via fire_cancelled_gig_blast
        # Since time is short (<36hrs), preferred artists get notified in parallel with the waitlist offer
        # fire_cancelled_gig_blast will skip artists already notified (notified_ids)
        try:
            from backend.routes.gigs import fire_cancelled_gig_blast as _fcgb
            _fcgb(db, gig_id, gig.get("venue_id"), skip_waitlist_check=True)
        except Exception as blast_err:
            logger.error(f"Urgent blast after waitlist offer failed: {blast_err}")

        db.commit()
        logger.info(f"Urgent blast+offer notified {len(notified_ids)} waitlist artists for gig {gig_id} ({hours_until:.1f}hrs out)")

    except Exception as e:
        logger.error(f"_blast_waitlist_and_nearby error for gig {gig_id}: {e}")


from fastapi import APIRouter, Depends, HTTPException, Request
import logging
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.utils import get_all_entity_users
from backend.utils import utcnow_naive
logger = logging.getLogger("gigsfill.preferred_artists")

router = APIRouter()

# ARTIST → REQUEST PREFERRED STATUS
@router.post("/api/venues/{venue_id}/preferred/request")
def request_preferred_artist(
    venue_id: int,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    # CRITICAL FIX: Get artist_id from query params
    artist_id = request.query_params.get('artist_id')
    
    if not artist_id:
        # Fallback - check both ownership and entity_users
        artist = db.execute(
            text("""
                SELECT DISTINCT a.id, a.name FROM artists a
                LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                WHERE a.user_id = :uid OR eu.user_id = :uid
                LIMIT 1
            """),
            {"uid": user.id}
        ).mappings().first()

        if not artist:
            raise HTTPException(400, "Artist profile not found")
        artist_id = artist["id"]
        artist_name = artist["name"]
    else:
        # Verify ownership OR entity_users access
        artist = db.execute(
            text("""
                SELECT a.id, a.name FROM artists a
                LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                WHERE a.id = :aid AND (a.user_id = :uid OR eu.user_id = :uid)
            """),
            {"aid": int(artist_id), "uid": user.id}
        ).mappings().first()
        
        if not artist:
            raise HTTPException(403, "Not your artist")
        artist_id = int(artist["id"])
        artist_name = artist["name"]

    # Check existing request
    existing = db.execute(
        text("""
            SELECT status
            FROM preferred_artists
            WHERE venue_id = :vid AND artist_id = :aid
        """),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()

    if existing:
        raise HTTPException(
            400,
            f"Request already exists (status: {existing['status']})"
        )

    # Get venue info for notification
    venue_info = db.execute(
        text("""
            SELECT v.user_id, v.venue_name, u.email as venue_email
            FROM venues v
            LEFT JOIN users u ON v.user_id = u.id
            WHERE v.id = :vid
        """),
        {"vid": venue_id}
    ).mappings().first()

    # Insert request
    db.execute(
        text("""
            INSERT INTO preferred_artists (venue_id, artist_id, status)
            VALUES (:vid, :aid, 'pending')
        """),
        {"vid": venue_id, "aid": artist_id}
    )

    # Create notification for venue
    if venue_info:
        from datetime import datetime
        db.execute(
            text("""
                INSERT INTO notifications
                    (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
            """),
            {
                "user_id": venue_info["user_id"],
                "type": "preferred_request",
                "title": "New Preferred Artist Request",
                "message": f"{artist_name} has requested to become a preferred artist at {venue_info['venue_name']}.",
                "venue_id": venue_id,
                "artist_id": artist_id,
                "created_at": utcnow_naive()
            }
        )
        
        # v73: Also notify the artist that their request was sent
        db.execute(
            text("""
                INSERT INTO notifications
                    (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
            """),
            {
                "user_id": user.id,
                "type": "preferred_request",
                "title": "Preferred Status Requested",
                "message": f"You requested preferred status at {venue_info['venue_name']}.",
                "venue_id": venue_id,
                "artist_id": artist_id,
                "created_at": utcnow_naive()
            }
        )

    # v76: Send email notifications
    # v97: Send to ALL users with access to artist/venue
    try:
        from backend.email_service import EmailService
        email_service = EmailService(db)
        
        # Send confirmation email to ALL artist users
        artist_users = get_all_entity_users(db, 'artist', artist_id)
        for au in artist_users:
            email_service.send_notification_email(
                user_email=au["email"],
                user_id=au["user_id"],
                notification_type='artist_preferred_request',
                variables={
                    'venue_name': venue_info['venue_name'],
                    'artist_name': artist_name,
                    'artist_id': str(artist_id),
                    'venue_id': str(venue_id)
                }
            )
        
        # Send notification email to ALL venue users
        venue_users = get_all_entity_users(db, 'venue', venue_id)
        for vu in venue_users:
            email_service.send_notification_email(
                user_email=vu["email"],
                user_id=vu["user_id"],
                notification_type='venue_preferred_request',
                variables={
                    'venue_name': venue_info['venue_name'],
                    'artist_name': artist_name,
                    'artist_id': str(artist_id),
                    'venue_id': str(venue_id)
                }
            )
    except Exception as e:
        logger.error(f"Email error: {e}")

    db.commit()

    return {"success": True}

# ARTIST → CHECK PREFERRED STATUS
@router.get("/api/venues/{venue_id}/preferred/status")
def preferred_status(
    venue_id: int,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    # CRITICAL FIX: Get artist_id from query params
    artist_id = request.query_params.get('artist_id')
    
    if not artist_id:
        # Check both ownership and entity_users access
        artist = db.execute(
            text("""
                SELECT DISTINCT a.id FROM artists a
                LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                WHERE a.user_id = :uid OR eu.user_id = :uid
                LIMIT 1
            """),
            {"uid": user.id}
        ).mappings().first()

        if not artist:
            return {"status": None}
        artist_id = artist["id"]
    else:
        # Verify ownership OR entity_users access
        artist = db.execute(
            text("""
                SELECT a.id FROM artists a
                LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                WHERE a.id = :aid AND (a.user_id = :uid OR eu.user_id = :uid)
            """),
            {"aid": int(artist_id), "uid": user.id}
        ).mappings().first()
        
        if not artist:
            return {"status": None}
        artist_id = int(artist_id)

    row = db.execute(
        text("""
            SELECT status
            FROM preferred_artists
            WHERE venue_id = :vid AND artist_id = :aid
        """),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()

    # Check ban — overrides all other statuses
    is_banned = db.execute(
        text("SELECT 1 FROM venue_artist_bans WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": venue_id, "aid": artist_id}
    ).first()
    if is_banned:
        return {"status": "banned"}

    return {"status": row["status"] if row else None}

# ARTIST → GET PREFERRED VENUES
@router.get("/api/artist/preferred-venues")
def artist_preferred_venues(
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    # CRITICAL FIX: Get artist_id from query params
    artist_id = request.query_params.get('artist_id')
    
    if not artist_id:
        # Check both ownership and entity_users access
        artist = db.execute(
            text("""
                SELECT DISTINCT a.id FROM artists a
                LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                WHERE a.user_id = :uid OR eu.user_id = :uid
                LIMIT 1
            """),
            {"uid": user.id}
        ).mappings().first()

        if not artist:
            return []
        artist_id = artist["id"]
    else:
        # Verify ownership OR entity_users access
        artist = db.execute(
            text("""
                SELECT a.id FROM artists a
                LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                WHERE a.id = :aid AND (a.user_id = :uid OR eu.user_id = :uid)
            """),
            {"aid": int(artist_id), "uid": user.id}
        ).mappings().first()
        
        if not artist:
            raise HTTPException(403, "Not your artist")
        artist_id = int(artist_id)

    rows = db.execute(
        text("""
            SELECT
                v.id AS venue_id,
                v.venue_name,
                pa.status,
                pa.frequency_days_override,
                pa.pay_dollars_override,
                pa.pay_cents_override,
                pa.created_at
            FROM preferred_artists pa
            JOIN venues v ON v.id = pa.venue_id
            WHERE pa.artist_id = :aid
            ORDER BY
                CASE pa.status
                    WHEN 'pending' THEN 1
                    WHEN 'approved' THEN 2
                    WHEN 'denied' THEN 3
                END,
                pa.created_at DESC
        """),
        {"aid": artist_id}
    ).mappings().all()

    return rows
# v73: My Artists endpoints
@router.get("/api/venues/{venue_id}/preferred-artists-with-gigs")
def get_preferred_artists_with_gigs(venue_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    """Get all preferred artists for a venue with their booked gigs"""
    # Authorization: only users with access to this venue may see its preferred-artist
    # roster, pay overrides, gig history, and private notes. Without this guard any
    # authenticated user could enumerate any venue's private data.
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)

    # Get venue defaults
    venue_defaults = db.execute(
        text("SELECT default_pay_dollars, default_pay_cents, artist_frequency_days FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    
    default_pay_dollars = venue_defaults["default_pay_dollars"] if venue_defaults else 0
    default_pay_cents = venue_defaults["default_pay_cents"] if venue_defaults else 0
    default_freq_days = venue_defaults["artist_frequency_days"] if venue_defaults else 0
    
    # v97: First get all preferred artists with override fields
    artists_result = db.execute(
        text("""
            SELECT 
                pa.id as preferred_id,
                pa.artist_id,
                pa.status as preferred_status,
                pa.pay_dollars_override,
                pa.pay_cents_override,
                pa.frequency_days_override,
                a.name as artist_name,
                a.city as artist_city,
                a.state as artist_state,
                (SELECT MAX(gd) FROM (
                    SELECT g2.date as gd FROM gigs g2 
                    WHERE g2.artist_id = pa.artist_id 
                    AND g2.venue_id = pa.venue_id 
                    AND g2.status = 'booked'
                    UNION ALL
                    SELECT g3.date as gd FROM gig_slots gs 
                    JOIN gigs g3 ON gs.gig_id = g3.id
                    WHERE gs.artist_id = pa.artist_id 
                    AND g3.venue_id = pa.venue_id 
                    AND gs.status = 'booked'
                )) as latest_gig_date
            FROM preferred_artists pa
            JOIN artists a ON pa.artist_id = a.id
            WHERE pa.venue_id = :venue_id
            ORDER BY CASE WHEN latest_gig_date IS NULL THEN 1 ELSE 0 END, latest_gig_date DESC, a.name
        """),
        {"venue_id": venue_id}
    ).mappings().all()
    
    # v97: Then get all booked gigs for this venue (regular + slot bookings).
    # Door-deal terms live on gig_slots; LEFT JOIN by (gig_id, artist_id)
    # so my-artists.js can render the same "$X guarantee + Y% of door"
    # pay summary that every other surface uses. Single-slot bookings
    # also create a gig_slots row, so the JOIN finds the right deal row
    # for both shapes.
    gigs_result = db.execute(
        text("""
            SELECT
                g.id, g.artist_id, g.date, g.start_time, g.end_time,
                g.status, g.title, g.pay, g.notes, g.artist_type, g.band_formats,
                COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                gs.deal_type, gs.door_pct, gs.guarantee_cents,
                gs.door_receipts_cents, gs.settled_pay_cents, gs.settled_at
            FROM gigs g
            LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = g.artist_id
            WHERE g.venue_id = :venue_id
                AND g.status = 'booked'
            ORDER BY g.date
        """),
        {"venue_id": venue_id}
    ).mappings().all()

    # Also get slot-booked gigs
    slot_gigs_result = db.execute(
        text("""
            SELECT DISTINCT
                g.id, gs.artist_id, g.date, g.start_time, g.end_time,
                'booked' as status, g.title, gs.pay, g.notes, g.artist_type, g.band_formats,
                COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                gs.deal_type, gs.door_pct, gs.guarantee_cents,
                gs.door_receipts_cents, gs.settled_pay_cents, gs.settled_at
            FROM gig_slots gs
            JOIN gigs g ON gs.gig_id = g.id
            WHERE g.venue_id = :venue_id
                AND gs.status = 'booked'
            ORDER BY g.date
        """),
        {"venue_id": venue_id}
    ).mappings().all()
    
    # v97: Build a map of artist_id -> gigs
    gigs_by_artist = {}
    seen_gig_artist = set()  # Track (gig_id, artist_id) to avoid duplicates
    
    for gig in gigs_result:
        aid = gig["artist_id"]
        if aid and (gig["id"], aid) not in seen_gig_artist:
            if aid not in gigs_by_artist:
                gigs_by_artist[aid] = []
            gigs_by_artist[aid].append(dict(gig))
            seen_gig_artist.add((gig["id"], aid))
    
    for gig in slot_gigs_result:
        aid = gig["artist_id"]
        if aid and (gig["id"], aid) not in seen_gig_artist:
            if aid not in gigs_by_artist:
                gigs_by_artist[aid] = []
            gigs_by_artist[aid].append(dict(gig))
            seen_gig_artist.add((gig["id"], aid))
    
    # v97: Combine artists with their gigs; add effective_pay (venue override) per gig.
    # Override only applies when the artist is currently 'approved' at this venue —
    # revoked/denied/pending rows still carry the override columns from before, but
    # the per-artist negotiated rate no longer applies.
    result = []
    for artist in artists_result:
        artist_dict = dict(artist)
        artist_gigs = gigs_by_artist.get(artist["artist_id"], [])
        override_d = artist.get("pay_dollars_override")
        override_c = artist.get("pay_cents_override") or 0
        is_approved = (artist.get("preferred_status") == "approved")
        override_val = (float(override_d) + float(override_c) / 100) if (is_approved and override_d is not None) else None
        for g in artist_gigs:
            pay = float(g.get("pay") or 0)
            if override_val is not None and override_val > pay:
                pay = override_val
            g["effective_pay"] = round(pay, 2)
        # Stamp pay_summary ONLY for door deals — the flat-pay summary
        # would just be "$<listed pay>" and would mask any venue
        # override (frontend prefers pay_summary when present). Door
        # deals don't honor overrides (the guarantee is the contract),
        # so the door summary is always correct.
        try:
            from backend.routes.gigs import _enrich_pay_summary
            _door_only = [g for g in artist_gigs
                          if str(g.get('deal_type') or '').lower() == 'door']
            if _door_only:
                _enrich_pay_summary(_door_only)
        except Exception:
            pass
        artist_dict["gigs"] = artist_gigs
        artist_dict["gigs_count"] = len(artist_gigs)
        
        # Include venue defaults and effective values
        artist_dict["venue_default_pay_dollars"] = default_pay_dollars or 0
        artist_dict["venue_default_pay_cents"] = default_pay_cents or 0
        artist_dict["venue_default_freq_days"] = default_freq_days or 0
        
        # Calculate next_gig_date — use platform timezone so gigs tonight aren't excluded
        # TZ FIX: use the VENUE's timezone (the gigs belong to this venue),
        # not platform tz. Otherwise next-gig-date is off by 1 day near UTC
        # midnight for venues outside the platform tz.
        try:
            from backend.utils import get_venue_timezone as _gvt
            from datetime import datetime as _pa_dt
            today = _pa_dt.now(_gvt(db, venue_id)).strftime("%Y-%m-%d")
        except Exception:
            from datetime import date
            today = date.today().isoformat()
        future_gigs = [g for g in artist_gigs if g["date"] >= today]
        artist_dict["next_gig_date"] = future_gigs[0]["date"] if future_gigs else None
        
        result.append(artist_dict)
    
    # Sort: artists with gigs first (by most recent gig date DESC), then those without gigs alphabetically
    def artist_sort_key(a):
        if a["gigs"]:
            most_recent = max(g["date"] for g in a["gigs"])
            return (0, most_recent, "")  # 0 = has gigs, sort by date
        return (1, "", a["artist_name"].lower())  # 1 = no gigs, sort by name
    
    result.sort(key=artist_sort_key, reverse=False)
    # Reverse gig-date portion only (we want most recent first)
    with_gigs = [a for a in result if a["gigs"]]
    without_gigs = [a for a in result if not a["gigs"]]
    with_gigs.sort(key=lambda a: max(g["date"] for g in a["gigs"]), reverse=True)
    without_gigs.sort(key=lambda a: a["artist_name"].lower())
    result = with_gigs + without_gigs
    
    # Add waitlist data per artist for this venue's gigs
    waitlist_rows = db.execute(
        text("""
            SELECT
                w.artist_id,
                w.gig_id as waitlist_gig_id,
                ROW_NUMBER() OVER (PARTITION BY w.gig_id ORDER BY w.created_at) as waitlist_position,
                COUNT(*) OVER (PARTITION BY w.gig_id) as waitlist_total,
                g.date as waitlist_gig_date,
                COALESCE(
                    (SELECT gs.start_time FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status='open' ORDER BY gs.start_time LIMIT 1),
                    g.start_time
                ) as waitlist_gig_start,
                COALESCE(
                    (SELECT gs.end_time FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status='open' ORDER BY gs.start_time LIMIT 1),
                    g.end_time
                ) as waitlist_gig_end
            FROM gig_waitlist w
            JOIN gigs g ON g.id = w.gig_id
            WHERE g.venue_id = :venue_id
        """),
        {"venue_id": venue_id}
    ).mappings().all()

    # Build waitlist lookup by artist_id (take first/earliest waitlist entry per artist)
    waitlist_by_artist = {}
    for row in waitlist_rows:
        aid = row["artist_id"]
        if aid not in waitlist_by_artist:
            waitlist_by_artist[aid] = dict(row)

    # Attach waitlist info to each artist
    for artist_dict in result:
        wl = waitlist_by_artist.get(artist_dict["artist_id"])
        if wl:
            artist_dict["waitlist_gig_id"] = wl["waitlist_gig_id"]
            artist_dict["waitlist_position"] = wl["waitlist_position"]
            artist_dict["waitlist_total"] = wl["waitlist_total"]
            artist_dict["waitlist_gig_date"] = wl["waitlist_gig_date"]
            artist_dict["waitlist_gig_start"] = wl["waitlist_gig_start"]
            artist_dict["waitlist_gig_end"] = wl["waitlist_gig_end"]
        else:
            artist_dict["waitlist_gig_id"] = None

    # Attach ban status
    banned_ids = {r[0] for r in db.execute(
        text("SELECT artist_id FROM venue_artist_bans WHERE venue_id = :vid"),
        {"vid": venue_id}
    ).all()}
    for artist_dict in result:
        artist_dict["is_banned"] = artist_dict["artist_id"] in banned_ids

    # Also fetch banned artists not in preferred_artists list
    banned_rows = db.execute(
        text("""SELECT vab.artist_id, a.name as artist_name, u.email,
                       vab.reason, vab.created_at
                FROM venue_artist_bans vab
                JOIN artists a ON a.id = vab.artist_id
                JOIN users u ON u.id = a.user_id
                WHERE vab.venue_id = :vid
                  AND vab.artist_id NOT IN (
                      SELECT artist_id FROM preferred_artists WHERE venue_id = :vid
                  )
                ORDER BY vab.created_at DESC"""),
        {"vid": venue_id}
    ).mappings().all()
    for br in banned_rows:
        result.append({
            "artist_id": br["artist_id"],
            "artist_name": br["artist_name"],
            "preferred_id": None,
            "preferred_status": "banned",
            "is_banned": True,
            "email": br["email"],
            "waitlist_gig_id": None,
        })

    # Part 10k: "Non-Preferred Artists" — artists who have gig history at this
    # venue (via gigs/slots/contracts/transactions) but have NO preferred_artists
    # row and are NOT banned. These are last-minute / blast-window bookings by
    # non-preferred artists; their history needs a home too.
    non_pref_rows = db.execute(text("""
        SELECT a.id AS artist_id, a.name AS artist_name, a.city AS artist_city, a.state AS artist_state,
               COUNT(DISTINCT g.id) AS gigs_count
        FROM artists a
        JOIN gigs g ON g.venue_id = :vid AND (
              g.artist_id = a.id
              OR EXISTS (SELECT 1 FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.artist_id = a.id)
              OR EXISTS (SELECT 1 FROM gig_contracts gc WHERE gc.gig_id = g.id AND gc.artist_id = a.id)
              OR EXISTS (SELECT 1 FROM transactions t WHERE t.gig_id = g.id AND t.artist_id = a.id)
        )
        WHERE a.id NOT IN (SELECT artist_id FROM preferred_artists WHERE venue_id = :vid)
          AND a.id NOT IN (SELECT artist_id FROM venue_artist_bans WHERE venue_id = :vid)
        GROUP BY a.id
    """), {"vid": venue_id}).mappings().all()
    for npr in non_pref_rows:
        result.append({
            "artist_id": npr["artist_id"],
            "artist_name": npr["artist_name"],
            "artist_city": npr["artist_city"],
            "artist_state": npr["artist_state"],
            "preferred_id": None,
            "preferred_status": "non_preferred",
            "is_banned": False,
            "gigs_count": npr["gigs_count"],
            "gigs": [],
            "waitlist_gig_id": None,
            "venue_default_pay_dollars": default_pay_dollars or 0,
            "venue_default_pay_cents": default_pay_cents or 0,
            "venue_default_freq_days": default_freq_days or 0,
        })

    return result


@router.post("/api/venues/{venue_id}/artists/{artist_id}/make-preferred")
def make_artist_preferred(venue_id: int, artist_id: int,
                          user=Depends(get_current_user), db=Depends(get_db)):
    """Part 10k: venue promotes a non-preferred (guest) or revoked artist to
    approved preferred status. Upserts the preferred_artists row to 'approved'.
    Refuses if the artist is currently banned (must unban first)."""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    banned = db.execute(text(
        "SELECT 1 FROM venue_artist_bans WHERE venue_id = :vid AND artist_id = :aid"
    ), {"vid": venue_id, "aid": artist_id}).first()
    if banned:
        raise HTTPException(400, "Artist is banned — remove the ban before making them preferred.")
    existing = db.execute(text(
        "SELECT id FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"
    ), {"vid": venue_id, "aid": artist_id}).first()
    if existing:
        db.execute(text("UPDATE preferred_artists SET status = 'approved' WHERE id = :id"), {"id": existing[0]})
    else:
        db.execute(text("""
            INSERT INTO preferred_artists (venue_id, artist_id, status)
            VALUES (:vid, :aid, 'approved')
        """), {"vid": venue_id, "aid": artist_id})
    db.commit()
    return {"ok": True, "status": "approved"}


@router.get("/api/venues/{venue_id}/artists/{artist_id}/past-gigs")
def get_artist_past_gigs_at_venue(venue_id: int, artist_id: int,
                                  user=Depends(get_current_user), db=Depends(get_db)):
    """Past-gig history for one artist at one venue — powers the My Artists →
    Past Gigs modal (May 2026 part 10i).

    Returns every PAST-dated gig where this artist was involved through ANY
    channel (gigs.artist_id, gig_slots.artist_id, gig_contracts.artist_id,
    transactions.artist_id) so cancellations — where the live artist link is
    cleared but the contract/transaction record survives — still appear.
    Each row carries a derived display status (Booked / Cancelled / ...),
    the effective pay, the slot times, and the venue's private note.
    """
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)

    # Venue-local "today" so a gig earlier today counts as past once it's done.
    try:
        from backend.utils import get_venue_timezone as _gvt
        from datetime import datetime as _dt
        today = _dt.now(_gvt(db, venue_id)).strftime("%Y-%m-%d")
    except Exception:
        from datetime import date as _d
        today = _d.today().isoformat()

    # Pay override for this artist at this venue (for effective pay calc)
    ov = db.execute(text("""
        SELECT pay_dollars_override, pay_cents_override
        FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid
    """), {"vid": venue_id, "aid": artist_id}).mappings().first()
    override_val = None
    if ov and ov.get("pay_dollars_override") is not None:
        override_val = float(ov["pay_dollars_override"]) + float(ov.get("pay_cents_override") or 0) / 100

    # Collect candidate gig ids from all four linkage sources.
    rows = db.execute(text("""
        SELECT DISTINCT g.id AS gig_id, g.date, g.status AS gig_status,
               g.start_time AS gig_start, g.end_time AS gig_end, g.pay AS gig_pay,
               g.title, g.notes AS public_notes, g.artist_type
        FROM gigs g
        WHERE g.venue_id = :vid
          AND date(g.date) < date(:today)
          AND (
            g.artist_id = :aid
            OR EXISTS (SELECT 1 FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.artist_id = :aid)
            OR EXISTS (SELECT 1 FROM gig_contracts gc WHERE gc.gig_id = g.id AND gc.artist_id = :aid)
            OR EXISTS (SELECT 1 FROM transactions t WHERE t.gig_id = g.id AND t.artist_id = :aid)
          )
        ORDER BY date(g.date) DESC
    """), {"vid": venue_id, "aid": artist_id, "today": today}).mappings().all()

    # Pre-fetch this artist's per-gig slot (for accurate times/pay on multi-slot)
    slot_map = {}
    for s in db.execute(text("""
        SELECT gs.gig_id, gs.start_time, gs.end_time, gs.pay, gs.status
        FROM gig_slots gs
        JOIN gigs g ON g.id = gs.gig_id
        WHERE g.venue_id = :vid AND gs.artist_id = :aid
    """), {"vid": venue_id, "aid": artist_id}).mappings().all():
        slot_map[s["gig_id"]] = s

    # Venue private notes
    note_map = {}
    for n in db.execute(text("""
        SELECT gig_id, notes FROM venue_gig_notes
        WHERE venue_id = :vid AND artist_id = :aid
    """), {"vid": venue_id, "aid": artist_id}).mappings().all():
        note_map[n["gig_id"]] = n["notes"]

    out = []
    for r in rows:
        gid = r["gig_id"]
        slot = slot_map.get(gid)
        start = (slot["start_time"] if slot else None) or r["gig_start"]
        end   = (slot["end_time"]   if slot else None) or r["gig_end"]
        base_pay = float((slot["pay"] if slot and slot.get("pay") is not None else r["gig_pay"]) or 0)
        pay = max(base_pay, override_val) if override_val is not None else base_pay

        # Derive display status. Currently-booked (gig or this artist's slot) → Booked.
        # Gig cancelled → Cancelled. Artist's slot cancelled but gig still open → Cancelled
        # (their involvement ended). Else fall back to the gig status, title-cased.
        gig_status = (r["gig_status"] or "").lower()
        slot_status = (slot["status"] if slot else "") or ""
        if gig_status == "cancelled":
            disp = "Cancelled"
        elif slot_status == "booked" or gig_status == "booked":
            disp = "Booked"
        elif slot_status == "cancelled":
            disp = "Cancelled"
        elif slot_status in ("pending_contract", "awaiting_venue_contract", "pending_venue_approval"):
            disp = "Contract Pending"
        else:
            disp = (gig_status or "unknown").replace("_", " ").title()

        out.append({
            "gig_id": gid,
            "date": r["date"],
            "start_time": start,
            "end_time": end,
            "pay": round(pay, 2),
            "status": disp,
            "title": r["title"] or "",
            "notes": note_map.get(gid, "") or "",
            "artist_type": r["artist_type"] or "",
        })

    artist_name = db.execute(text("SELECT name FROM artists WHERE id = :aid"), {"aid": artist_id}).scalar() or "Artist"
    return {"artist_id": artist_id, "artist_name": artist_name, "gigs": out}


@router.put("/api/venues/{venue_id}/gigs/{gig_id}/artist-note/{artist_id}")
async def save_venue_gig_note(venue_id: int, gig_id: int, artist_id: int,
                              request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Save the venue's private note for a (gig, artist) pair. Upsert."""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    data = await request.json()
    notes = (data.get("notes") or "").strip()[:4000]
    # Upsert on the UNIQUE(venue_id, gig_id, artist_id) constraint
    db.execute(text("""
        INSERT INTO venue_gig_notes (venue_id, gig_id, artist_id, notes, updated_at)
        VALUES (:vid, :gid, :aid, :notes, CURRENT_TIMESTAMP)
        ON CONFLICT(venue_id, gig_id, artist_id)
        DO UPDATE SET notes = :notes, updated_at = CURRENT_TIMESTAMP
    """), {"vid": venue_id, "gid": gig_id, "aid": artist_id, "notes": notes})
    db.commit()
    return {"ok": True}


@router.get("/api/artists/{artist_id}/venues/{venue_id}/past-gigs")
def get_venue_past_gigs_for_artist(artist_id: int, venue_id: int,
                                   user=Depends(get_current_user), db=Depends(get_db)):
    """Artist-side mirror of get_artist_past_gigs_at_venue (May 2026 part 10j).
    Past-gig history for one venue from the artist's perspective. Same linkage
    union (gigs/slots/contracts/transactions) so cancellations still appear.
    Notes come from artist_gig_notes (the artist's private notes)."""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)

    try:
        from backend.utils import get_venue_timezone as _gvt
        from datetime import datetime as _dt
        today = _dt.now(_gvt(db, venue_id)).strftime("%Y-%m-%d")
    except Exception:
        from datetime import date as _d
        today = _d.today().isoformat()

    rows = db.execute(text("""
        SELECT DISTINCT g.id AS gig_id, g.date, g.status AS gig_status,
               g.start_time AS gig_start, g.end_time AS gig_end, g.pay AS gig_pay,
               g.title, g.artist_type
        FROM gigs g
        WHERE g.venue_id = :vid
          AND date(g.date) < date(:today)
          AND (
            g.artist_id = :aid
            OR EXISTS (SELECT 1 FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.artist_id = :aid)
            OR EXISTS (SELECT 1 FROM gig_contracts gc WHERE gc.gig_id = g.id AND gc.artist_id = :aid)
            OR EXISTS (SELECT 1 FROM transactions t WHERE t.gig_id = g.id AND t.artist_id = :aid)
          )
        ORDER BY date(g.date) DESC
    """), {"vid": venue_id, "aid": artist_id, "today": today}).mappings().all()

    slot_map = {}
    for s in db.execute(text("""
        SELECT gs.gig_id, gs.start_time, gs.end_time, gs.pay, gs.status
        FROM gig_slots gs JOIN gigs g ON g.id = gs.gig_id
        WHERE g.venue_id = :vid AND gs.artist_id = :aid
    """), {"vid": venue_id, "aid": artist_id}).mappings().all():
        slot_map[s["gig_id"]] = s

    note_map = {}
    for n in db.execute(text("""
        SELECT gig_id, notes FROM artist_gig_notes
        WHERE artist_id = :aid AND venue_id = :vid
    """), {"aid": artist_id, "vid": venue_id}).mappings().all():
        note_map[n["gig_id"]] = n["notes"]

    out = []
    for r in rows:
        gid = r["gig_id"]
        slot = slot_map.get(gid)
        start = (slot["start_time"] if slot else None) or r["gig_start"]
        end   = (slot["end_time"]   if slot else None) or r["gig_end"]
        pay = float((slot["pay"] if slot and slot.get("pay") is not None else r["gig_pay"]) or 0)

        gig_status = (r["gig_status"] or "").lower()
        slot_status = (slot["status"] if slot else "") or ""
        if gig_status == "cancelled":
            disp = "Cancelled"
        elif slot_status == "booked" or gig_status == "booked":
            disp = "Booked"
        elif slot_status == "cancelled":
            disp = "Cancelled"
        elif slot_status in ("pending_contract", "awaiting_venue_contract", "pending_venue_approval"):
            disp = "Contract Pending"
        else:
            disp = (gig_status or "unknown").replace("_", " ").title()

        out.append({
            "gig_id": gid, "date": r["date"],
            "start_time": start, "end_time": end,
            "pay": round(pay, 2), "status": disp,
            "title": r["title"] or "", "notes": note_map.get(gid, "") or "",
            "artist_type": r["artist_type"] or "",
        })

    venue_name = db.execute(text("SELECT venue_name FROM venues WHERE id = :vid"), {"vid": venue_id}).scalar() or "Venue"
    return {"venue_id": venue_id, "venue_name": venue_name, "gigs": out}


@router.put("/api/artists/{artist_id}/gigs/{gig_id}/venue-note/{venue_id}")
async def save_artist_gig_note(artist_id: int, gig_id: int, venue_id: int,
                               request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    """Save the artist's private note for a (gig, venue) pair. Upsert."""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    data = await request.json()
    notes = (data.get("notes") or "").strip()[:4000]
    db.execute(text("""
        INSERT INTO artist_gig_notes (artist_id, gig_id, venue_id, notes, updated_at)
        VALUES (:aid, :gid, :vid, :notes, CURRENT_TIMESTAMP)
        ON CONFLICT(artist_id, gig_id, venue_id)
        DO UPDATE SET notes = :notes, updated_at = CURRENT_TIMESTAMP
    """), {"aid": artist_id, "gid": gig_id, "vid": venue_id, "notes": notes})
    db.commit()
    return {"ok": True}


@router.get("/api/artists/{artist_id}/gigs-at-venue/{venue_id}")
def get_artist_gigs_at_venue(artist_id: int, venue_id: int, db=Depends(get_db)):
    """Get artist's gigs at a specific venue ordered by closest date first"""
    # Regular gigs
    regular = db.execute(
        text("""
            SELECT id, date, start_time, end_time, status, title, pay
            FROM gigs
            WHERE artist_id = :artist_id 
                AND venue_id = :venue_id
            ORDER BY 
                CASE WHEN date >= DATE('now', 'localtime') THEN 0 ELSE 1 END,
                ABS(JULIANDAY(date) - JULIANDAY('now'))
        """),
        {"artist_id": artist_id, "venue_id": venue_id}
    ).mappings().all()
    
    # Slot bookings
    slot_gigs = db.execute(
        text("""
            SELECT DISTINCT g.id, g.date, gs.start_time, gs.end_time, 'booked' as status, g.title, gs.pay
            FROM gig_slots gs
            JOIN gigs g ON gs.gig_id = g.id
            WHERE gs.artist_id = :artist_id
                AND g.venue_id = :venue_id
                AND gs.status = 'booked'
            ORDER BY 
                CASE WHEN g.date >= DATE('now', 'localtime') THEN 0 ELSE 1 END,
                ABS(JULIANDAY(g.date) - JULIANDAY('now'))
        """),
        {"artist_id": artist_id, "venue_id": venue_id}
    ).mappings().all()
    
    seen_ids = set()
    result = []
    for r in regular:
        seen_ids.add(r['id'])
        result.append(dict(r))
    for r in slot_gigs:
        if r['id'] not in seen_ids:
            seen_ids.add(r['id'])
            result.append(dict(r))
    
    return result

@router.put("/api/preferred-artists/{id}/approve")
def approve_preferred_artist(id: int, db=Depends(get_db), user=Depends(get_current_user)):
    """Approve a preferred artist request"""
    from datetime import datetime
    from backend.utils import check_venue_access

    # Get request details
    request_info = db.execute(
        text("""
            SELECT pa.artist_id, pa.venue_id,
                   a.name as artist_name, a.user_id as artist_user_id,
                   v.venue_name, v.user_id as venue_user_id,
                   u_artist.email as artist_email
            FROM preferred_artists pa
            JOIN artists a ON pa.artist_id = a.id
            JOIN venues v ON pa.venue_id = v.id
            LEFT JOIN users u_artist ON a.user_id = u_artist.id
            WHERE pa.id = :id
        """),
        {"id": id}
    ).mappings().first()
    if not request_info:
        raise HTTPException(404, "Request not found")
    # Audit fix (May 2026): authorize the venue side. Without this any
    # authenticated user could approve themselves into preferred status at
    # any venue (frontend silently swallowed the response so the bug was
    # invisible during venue testing).
    check_venue_access(db, request_info["venue_id"], user.id)

    db.execute(
        text("UPDATE preferred_artists SET status = 'approved' WHERE id = :id"),
        {"id": id}
    )
    
    if request_info:
        # v73: Delete the "preferred_request" notification for artist
        db.execute(
            text("""
                DELETE FROM notifications 
                WHERE user_id = :artist_user_id 
                AND notification_type = 'preferred_request'
                AND artist_id = :artist_id 
                AND venue_id = :venue_id
            """),
            {
                "artist_user_id": request_info["artist_user_id"],
                "artist_id": request_info["artist_id"],
                "venue_id": request_info["venue_id"]
            }
        )
        
        # Notify artist
        db.execute(
            text("""
                INSERT INTO notifications
                    (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
            """),
            {
                "user_id": request_info["artist_user_id"],
                "type": "preferred_approved",
                "title": "Preferred Status Approved!",
                "message": f"{request_info['venue_name']} has approved you as a preferred artist!",
                "venue_id": request_info["venue_id"],
                "artist_id": request_info["artist_id"],
                "created_at": utcnow_naive()
            }
        )
        
        # Notify venue
        db.execute(
            text("""
                INSERT INTO notifications
                    (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
            """),
            {
                "user_id": request_info["venue_user_id"],
                "type": "preferred_approved",
                "title": "Preferred Artist Approved",
                "message": f"You approved {request_info['artist_name']} as a preferred artist.",
                "venue_id": request_info["venue_id"],
                "artist_id": request_info["artist_id"],
                "created_at": utcnow_naive()
            }
        )
    
    # v76: Send email notifications
    # v97: Send to ALL users with access to artist/venue
    try:
        from backend.email_service import EmailService
        email_service = EmailService(db)
        
        # Send notification email to ALL artist users
        artist_users = get_all_entity_users(db, 'artist', request_info["artist_id"])
        for au in artist_users:
            email_service.send_notification_email(
                user_email=au["email"],
                user_id=au["user_id"],
                notification_type='artist_preferred_approved',
                variables={
                    'artist_name': request_info['artist_name'],
                    'venue_name': request_info['venue_name'],
                    'artist_id': str(request_info['artist_id']),
                    'venue_id': str(request_info['venue_id'])
                }
            )
        
        # Send confirmation email to ALL venue users
        venue_users = get_all_entity_users(db, 'venue', request_info["venue_id"])
        for vu in venue_users:
            email_service.send_notification_email(
                user_email=vu["email"],
                user_id=vu["user_id"],
                notification_type='venue_preferred_approved',
                variables={
                    'artist_name': request_info['artist_name'],
                    'venue_name': request_info['venue_name'],
                    'artist_id': str(request_info['artist_id']),
                    'venue_id': str(request_info['venue_id'])
                }
            )
    except Exception as e:
        pass
    
    db.commit()
    return {"ok": True}

@router.put("/api/preferred-artists/{id}/deny")
def deny_preferred_artist(id: int, db=Depends(get_db), user=Depends(get_current_user)):
    """Deny a preferred artist request"""
    from datetime import datetime
    from backend.utils import check_venue_access

    # Get request details
    request_info = db.execute(
        text("""
            SELECT pa.artist_id, pa.venue_id,
                   a.name as artist_name, a.user_id as artist_user_id,
                   v.venue_name, v.user_id as venue_user_id,
                   u_artist.email as artist_email
            FROM preferred_artists pa
            JOIN artists a ON pa.artist_id = a.id
            JOIN venues v ON pa.venue_id = v.id
            LEFT JOIN users u_artist ON a.user_id = u_artist.id
            WHERE pa.id = :id
        """),
        {"id": id}
    ).mappings().first()
    if not request_info:
        raise HTTPException(404, "Request not found")
    # Audit fix (May 2026): authorize the venue side.
    check_venue_access(db, request_info["venue_id"], user.id)

    db.execute(
        text("UPDATE preferred_artists SET status = 'denied' WHERE id = :id"),
        {"id": id}
    )
    
    if request_info:
        # v73: Delete the "preferred_request" notification for artist
        db.execute(
            text("""
                DELETE FROM notifications 
                WHERE user_id = :artist_user_id 
                AND notification_type = 'preferred_request'
                AND artist_id = :artist_id 
                AND venue_id = :venue_id
            """),
            {
                "artist_user_id": request_info["artist_user_id"],
                "artist_id": request_info["artist_id"],
                "venue_id": request_info["venue_id"]
            }
        )
        
        # Notify artist
        db.execute(
            text("""
                INSERT INTO notifications
                    (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
            """),
            {
                "user_id": request_info["artist_user_id"],
                "type": "preferred_denied",
                "title": "Preferred Status Denied",
                "message": f"{request_info['venue_name']} has denied your preferred artist request.",
                "venue_id": request_info["venue_id"],
                "artist_id": request_info["artist_id"],
                "created_at": utcnow_naive()
            }
        )
        
        # Notify venue
        db.execute(
            text("""
                INSERT INTO notifications
                    (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
            """),
            {
                "user_id": request_info["venue_user_id"],
                "type": "preferred_denied",
                "title": "Preferred Artist Denied",
                "message": f"You denied {request_info['artist_name']}'s preferred artist request.",
                "venue_id": request_info["venue_id"],
                "artist_id": request_info["artist_id"],
                "created_at": utcnow_naive()
            }
        )
    
    # v76: Send email notifications
    # v97: Send to ALL users with access to artist/venue
    try:
        from backend.email_service import EmailService
        email_service = EmailService(db)
        
        # Send notification email to ALL artist users
        artist_users = get_all_entity_users(db, 'artist', request_info["artist_id"])
        for au in artist_users:
            email_service.send_notification_email(
                user_email=au["email"],
                user_id=au["user_id"],
                notification_type='artist_preferred_denied',
                variables={
                    'artist_name': request_info['artist_name'],
                    'venue_name': request_info['venue_name'],
                    'artist_id': str(request_info['artist_id']),
                    'venue_id': str(request_info['venue_id'])
                }
            )
        
        # Send confirmation email to ALL venue users
        venue_users = get_all_entity_users(db, 'venue', request_info["venue_id"])
        for vu in venue_users:
            email_service.send_notification_email(
                user_email=vu["email"],
                user_id=vu["user_id"],
                notification_type='venue_preferred_denied',
                variables={
                    'artist_name': request_info['artist_name'],
                    'venue_name': request_info['venue_name'],
                    'artist_id': str(request_info['artist_id']),
                    'venue_id': str(request_info['venue_id'])
                }
            )
    except Exception as e:
        pass
    
    db.commit()
    return {"ok": True}

@router.put("/api/preferred-artists/{id}/revoke")
def revoke_preferred_artist(id: int, db=Depends(get_db), user=Depends(get_current_user)):
    """
    v73: Revoke preferred status for an artist
    - Changes status from 'approved' to 'revoked'
    - Existing gigs remain untouched
    - Artist cannot book future gigs at this venue
    v97: Added email notifications
    """
    from datetime import datetime
    
    # v97: Get request details INCLUDING email addresses
    request_info = db.execute(
        text("""
            SELECT pa.artist_id, pa.venue_id,
                   a.name as artist_name, a.user_id as artist_user_id,
                   v.venue_name, v.user_id as venue_user_id,
                   u_artist.email as artist_email,
                   u_venue.email as venue_email
            FROM preferred_artists pa
            JOIN artists a ON pa.artist_id = a.id
            JOIN venues v ON pa.venue_id = v.id
            LEFT JOIN users u_artist ON a.user_id = u_artist.id
            LEFT JOIN users u_venue ON v.user_id = u_venue.id
            WHERE pa.id = :id AND pa.status = 'approved'
        """),
        {"id": id}
    ).mappings().first()
    
    if not request_info:
        raise HTTPException(404, "Preferred artist relationship not found or not approved")

    # Audit fix (May 2026): authorize the venue side.
    from backend.utils import check_venue_access
    check_venue_access(db, request_info["venue_id"], user.id)

    # v73: Update status to 'revoked'
    db.execute(
        text("UPDATE preferred_artists SET status = 'revoked' WHERE id = :id"),
        {"id": id}
    )
    
    # Notify artist
    db.execute(
        text("""
            INSERT INTO notifications
                (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
            VALUES
                (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
        """),
        {
            "user_id": request_info["artist_user_id"],
            "type": "preferred_revoked",
            "title": "Preferred Status Revoked",
            "message": f"{request_info['venue_name']} has revoked your preferred artist status. You can no longer book future gigs at this venue.",
            "venue_id": request_info["venue_id"],
            "artist_id": request_info["artist_id"],
            "created_at": utcnow_naive()
        }
    )
    
    # Notify venue
    db.execute(
        text("""
            INSERT INTO notifications
                (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
            VALUES
                (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
        """),
        {
            "user_id": request_info["venue_user_id"],
            "type": "preferred_revoked",
            "title": "Preferred Status Revoked",
            "message": f"You revoked preferred status for {request_info['artist_name']}.",
            "venue_id": request_info["venue_id"],
            "artist_id": request_info["artist_id"],
            "created_at": utcnow_naive()
        }
    )
    
    # v97: Send email notifications to ALL users with access
    try:
        from backend.email_service import EmailService
        email_service = EmailService(db)
        
        # Send notification email to ALL artist users
        artist_users = get_all_entity_users(db, 'artist', request_info["artist_id"])
        for au in artist_users:
            email_service.send_notification_email(
                user_email=au["email"],
                user_id=au["user_id"],
                notification_type='artist_preferred_revoked',
                variables={
                    'artist_name': request_info['artist_name'],
                    'venue_name': request_info['venue_name'],
                    'artist_id': str(request_info['artist_id']),
                    'venue_id': str(request_info['venue_id'])
                }
            )
        
        # Send confirmation email to ALL venue users
        venue_users = get_all_entity_users(db, 'venue', request_info["venue_id"])
        for vu in venue_users:
            email_service.send_notification_email(
                user_email=vu["email"],
                user_id=vu["user_id"],
                notification_type='venue_preferred_revoked',
                variables={
                    'artist_name': request_info['artist_name'],
                    'venue_name': request_info['venue_name'],
                    'artist_id': str(request_info['artist_id']),
                    'venue_id': str(request_info['venue_id'])
                }
            )
    except Exception as e:
        pass
    
    db.commit()
    return {"ok": True}


@router.put("/api/preferred-artists/{id}/override")
async def update_preferred_artist_override(id: int, request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    """Update pay/frequency overrides for a preferred artist"""
    data = await request.json()
    
    # Verify the preferred artist record exists
    record = db.execute(
        text("""
            SELECT pa.id, pa.venue_id, v.user_id
            FROM preferred_artists pa
            JOIN venues v ON pa.venue_id = v.id
            WHERE pa.id = :id
        """),
        {"id": id}
    ).mappings().first()
    
    if not record:
        raise HTTPException(404, "Preferred artist not found")

    # Audit fix (May 2026): authorize the venue side. Without this any
    # authenticated user could rewrite pay overrides on any preferred-artist
    # record at any venue, silently inflating future payouts.
    from backend.utils import check_venue_access
    check_venue_access(db, record["venue_id"], user.id)

    # Update override fields
    updates = []
    params = {"id": id}
    
    if "pay_dollars_override" in data:
        val = data["pay_dollars_override"]
        updates.append("pay_dollars_override = :pay_d")
        params["pay_d"] = int(val) if val is not None and val != '' else None
    
    if "pay_cents_override" in data:
        val = data["pay_cents_override"]
        updates.append("pay_cents_override = :pay_c")
        params["pay_c"] = int(val) if val is not None and val != '' else None
    
    if "frequency_days_override" in data:
        val = data["frequency_days_override"]
        updates.append("frequency_days_override = :freq")
        params["freq"] = int(val) if val is not None and val != '' else None
    
    if updates:
        # Safe: `updates` list only contains hardcoded column assignment strings
        db.execute(
            text(f"UPDATE preferred_artists SET {', '.join(updates)} WHERE id = :id"),
            params
        )
        db.commit()
    
    return {"ok": True}


# ─── BAN ARTIST ──────────────────────────────────────────────────────────────

@router.post("/api/venues/{venue_id}/ban-artist/{artist_id}")
def ban_artist(venue_id: int, artist_id: int, data: dict = {},
               user=Depends(get_current_user), db=Depends(get_db)):
    """Permanently ban an artist from a venue. Removes preferred status too."""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)

    # Verify artist exists
    artist = db.execute(
        text("SELECT id, name, user_id FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist:
        raise HTTPException(404, "Artist not found")

    reason = (data.get("reason") or "").strip()

    # Insert ban (ignore if already banned)
    db.execute(
        text("""INSERT OR IGNORE INTO venue_artist_bans
                (venue_id, artist_id, banned_by, reason)
                VALUES (:vid, :aid, :uid, :reason)"""),
        {"vid": venue_id, "aid": artist_id, "uid": user.id, "reason": reason}
    )

    # Remove from preferred_artists entirely
    db.execute(
        text("DELETE FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": venue_id, "aid": artist_id}
    )

    # Remove from waitlists at this venue
    db.execute(
        text("""DELETE FROM gig_waitlist WHERE artist_id = :aid
                AND gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid)"""),
        {"aid": artist_id, "vid": venue_id}
    )
    db.execute(
        text("""DELETE FROM waitlist_offered WHERE artist_id = :aid
                AND gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid)"""),
        {"aid": artist_id, "vid": venue_id}
    )
    db.commit()

    # Notify artist via Activity Center + email
    venue = db.execute(
        text("SELECT venue_name FROM venues WHERE id = :vid"), {"vid": venue_id}
    ).mappings().first()
    venue_name = venue["venue_name"] if venue else "A venue"
    try:
        from backend.services.notification_service import create_notification
        create_notification(
            db, artist["user_id"], "preferred_revoked", "Booking Access Removed",
            f"You are no longer able to book gigs at {venue_name}.",
            venue_id=venue_id, artist_id=artist_id
        )
        db.commit()
    except Exception as _e:
        pass

    # Audit fix (May 2026 part 10g): email the artist about the access removal.
    # Previously only an in-app notification fired; the artist had no idea
    # unless they happened to open the app. Tone is neutral ("access removed")
    # rather than punitive. Fans out to all of the artist's entity_users.
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users
        es = EmailService(db)
        if es.enabled:
            logo = "https://gigsfill.com/app/static/img/gigsfill-logo_light.png"
            html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fa;"><tr><td style="padding:40px 20px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<img src="{logo}" alt="GigsFill" width="160" height="40" style="display:block;border:0;">
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 16px;font-size:20px;font-weight:600;color:#111827;">Booking access updated</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#4b5563;">
Hi {artist['name']}, <strong>{venue_name}</strong> has removed your access to book gigs at their venue,
and you are no longer one of their preferred artists.</p>
<p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:#6b7280;">
You can still discover and book gigs at other venues on GigsFill. If you believe this was a mistake,
you're welcome to reach out to the venue directly.</p>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#9ca3af;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#9ca3af;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table></td></tr></table></body></html>"""
            subject = f"Your booking access at {venue_name} has been removed"
            # Email every user on the artist account (owner + team members)
            sent_to = set()
            for u in get_all_entity_users(db, "artist", artist_id):
                _em = db.execute(text("SELECT email FROM users WHERE id = :uid"), {"uid": u["user_id"]}).scalar()
                if _em and _em not in sent_to:
                    es._send_raw_email(to_email=_em, subject=subject, html_body=html)
                    sent_to.add(_em)
    except Exception as _e:
        logger.warning(f"Ban-notification email failed for artist {artist_id}: {_e}")

    return {"ok": True, "banned": True}


@router.delete("/api/venues/{venue_id}/ban-artist/{artist_id}")
def unban_artist(venue_id: int, artist_id: int,
                 user=Depends(get_current_user), db=Depends(get_db)):
    """Remove a ban — artist returns to 'revoked' status (they were preferred-
    capable before the ban; lifting the ban doesn't auto-restore Preferred, but
    it shouldn't erase their history either).

    Part 10j: on unban, if the artist has ANY gig history at this venue, restore
    a preferred_artists row with status='revoked' so they land in the
    "Preferred Revoked" bubble (their past gigs stay reachable). Artists with no
    history get no row — nothing to surface. Only inserts if no row exists.
    """
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    db.execute(
        text("DELETE FROM venue_artist_bans WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": venue_id, "aid": artist_id}
    )

    # Does the artist have gig history at this venue?
    has_history = db.execute(text("""
        SELECT 1 FROM gigs g
        WHERE g.venue_id = :vid AND (
            g.artist_id = :aid
            OR EXISTS (SELECT 1 FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.artist_id = :aid)
            OR EXISTS (SELECT 1 FROM gig_contracts gc WHERE gc.gig_id = g.id AND gc.artist_id = :aid)
            OR EXISTS (SELECT 1 FROM transactions t WHERE t.gig_id = g.id AND t.artist_id = :aid)
        ) LIMIT 1
    """), {"vid": venue_id, "aid": artist_id}).first()

    if has_history:
        existing = db.execute(text(
            "SELECT id FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"
        ), {"vid": venue_id, "aid": artist_id}).first()
        if not existing:
            db.execute(text("""
                INSERT INTO preferred_artists (venue_id, artist_id, status)
                VALUES (:vid, :aid, 'revoked')
            """), {"vid": venue_id, "aid": artist_id})
    db.commit()
    return {"ok": True, "unbanned": True}


@router.get("/api/venues/{venue_id}/banned-artists")
def get_banned_artists(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get list of banned artists for a venue."""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    rows = db.execute(
        text("""SELECT vab.artist_id, a.name as artist_name, vab.reason, vab.created_at
                FROM venue_artist_bans vab
                JOIN artists a ON a.id = vab.artist_id
                WHERE vab.venue_id = :vid
                ORDER BY vab.created_at DESC"""),
        {"vid": venue_id}
    ).mappings().all()
    return [dict(r) for r in rows]

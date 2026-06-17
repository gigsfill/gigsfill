"""
Notification Service
=====================
Single source of truth for creating in-app notifications for gig events.
Replaces 16+ copy-pasted INSERT INTO notifications blocks in gigs.py.

Handles same-user deduplication (when artist and venue are owned by same user).
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import text

logger = logging.getLogger("gigsfill.services.notifications")


def format_time_12hr(time_str):
    """Format 24h time string to 12h format."""
    if not time_str:
        return ""
    try:
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        period = "AM" if h < 12 else "PM"
        h = h % 12 or 12
        return f"{h}:{m:02d} {period}"
    except (ValueError, IndexError):
        return time_str


def create_notification(db, user_id: int, notification_type: str, title: str,
                       message: str, gig_id: int = None, venue_id: int = None,
                       artist_id: int = None, cancellation_reason: str = None):
    """
    Insert a single notification row.
    
    Args:
        db: SQLAlchemy session
        user_id: User to notify
        notification_type: e.g. 'gig_booked', 'gig_cancelled'
        title: Notification title
        message: Notification body
        gig_id, venue_id, artist_id: Optional context IDs
        cancellation_reason: Optional reason text
    """
    params = {
        "user_id": user_id,
        "type": notification_type,
        "title": title,
        "message": message,
        "gig_id": gig_id,
        "venue_id": venue_id,
        "artist_id": artist_id,
        "created_at": datetime.now(timezone.utc),
        "reason": cancellation_reason or "",
    }
    
    if cancellation_reason is not None:
        db.execute(
            text("""
                INSERT INTO notifications 
                    (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at, cancellation_reason)
                VALUES
                    (:user_id, :type, :title, :message, :gig_id, :venue_id, :artist_id, FALSE, :created_at, :reason)
            """),
            params
        )
    else:
        db.execute(
            text("""
                INSERT INTO notifications 
                    (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :gig_id, :venue_id, :artist_id, FALSE, :created_at)
            """),
            params
        )


def notify_gig_booked(db, gig_details: dict, gig_id: int, venue_id: int, artist_id: int):
    """
    Create booking notifications for ALL artist and venue users (entity_users aware).

    Multi-slot disambiguation (May 2026): if the gig has more than one slot,
    append "Slot N" to the message so the venue can immediately tell which
    of their slots was just filled. Activity Center already splits messages
    on "Slot" into two lines so this renders nicely.
    """
    from backend.utils import get_all_entity_users
    from sqlalchemy import text as _t
    time_str = format_time_12hr(gig_details.get("start_time"))
    venue_name = gig_details.get("venue_name", "venue")
    artist_name = gig_details.get("artist_name", "artist")
    date = gig_details.get("date", "")

    # Look up slot_number for this artist on multi-slot gigs (best-effort).
    # Empty string for single-slot — keeps the legacy message phrasing intact.
    slot_suffix = ""
    try:
        total_slots = db.execute(
            _t("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid"),
            {"gid": gig_id}
        ).scalar() or 0
        if total_slots > 1:
            # Audit fix (May 2026 part 6): include awaiting_venue_contract + pending_venue_approval
            sn = db.execute(_t(
                "SELECT slot_number FROM gig_slots "
                "WHERE gig_id = :gid AND artist_id = :aid "
                "AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval') "
                "ORDER BY slot_number LIMIT 1"
            ), {"gid": gig_id, "aid": artist_id}).scalar()
            if sn:
                slot_suffix = f". Slot {sn}"
    except Exception:
        pass

    # Far-away-booking notice (May 2026 part 10h). Mirrors the email notices in
    # email_dispatch.send_booking_emails — if the artist and venue are farther
    # apart than far_booking_alert_miles, append a heads-up to the Activity
    # Center notification for both sides. Booking is never blocked; this is
    # purely informational. Computed once and reused for all entity_users.
    far_artist_suffix = ""   # appended to the artist-side notification
    far_venue_suffix  = ""   # appended to the venue-side notification
    try:
        threshold = float(db.execute(_t(
            "SELECT COALESCE(setting_value,'50') FROM platform_settings WHERE setting_key='far_booking_alert_miles'"
        )).scalar() or 50)
        ag = db.execute(_t("SELECT latitude, longitude, city, state FROM artists WHERE id = :aid"),
                        {"aid": artist_id}).mappings().first()
        vg = db.execute(_t("SELECT latitude, longitude, city, state FROM venues WHERE id = :vid"),
                        {"vid": venue_id}).mappings().first()
        if ag and vg and None not in (ag["latitude"], ag["longitude"], vg["latitude"], vg["longitude"]):
            import math
            R = 3959.0
            p1, p2 = math.radians(ag["latitude"]), math.radians(vg["latitude"])
            dphi = math.radians(vg["latitude"] - ag["latitude"])
            dlmb = math.radians(vg["longitude"] - ag["longitude"])
            a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
            dist = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            if dist > threshold:
                mi = int(round(dist))
                v_loc = ", ".join([p for p in [vg.get("city"), vg.get("state")] if p]) or "the venue area"
                a_loc = ", ".join([p for p in [ag.get("city"), ag.get("state")] if p]) or "out of the area"
                far_artist_suffix = (f" ⚠️ Note: this venue is in {v_loc}, ~{mi} mi away — "
                                     f"make sure you can perform in person.")
                far_venue_suffix  = (f" ⚠️ Note: {artist_name} is based in {a_loc}, ~{mi} mi away "
                                     f"(booked via your open-gig window). Cancel from the gig if this looks wrong.")
    except Exception:
        pass

    artist_users = get_all_entity_users(db, "artist", artist_id)
    venue_users  = get_all_entity_users(db, "venue",  venue_id)

    artist_user_ids = {u["user_id"] for u in artist_users}
    venue_user_ids  = {u["user_id"] for u in venue_users}
    shared_ids      = artist_user_ids & venue_user_ids

    for u in artist_users:
        uid = u["user_id"]
        if uid in shared_ids:
            # Same human owns both sides — show the venue-side framing + the
            # venue suffix (they're the one who needs the "artist is far" heads-up).
            create_notification(db, uid, "gig_booked", "Gig Booked",
                f"Your artist {artist_name} booked your venue {venue_name} on {date} at {time_str}{slot_suffix}{far_venue_suffix}",
                gig_id=gig_id, venue_id=venue_id, artist_id=artist_id)
        else:
            create_notification(db, uid, "gig_booked", "Gig Booked",
                f"You booked a gig at {venue_name} on {date} at {time_str}{slot_suffix}{far_artist_suffix}",
                gig_id=gig_id, venue_id=venue_id, artist_id=artist_id)

    for u in venue_users:
        uid = u["user_id"]
        if uid in shared_ids:
            continue  # already notified above
        create_notification(db, uid, "gig_booked", "Gig Booked",
            f"{artist_name} booked your gig on {date} at {time_str}{slot_suffix}{far_venue_suffix}",
            gig_id=gig_id, venue_id=venue_id, artist_id=artist_id)


def notify_gig_edited(db, gig_id: int, venue_id: int, venue_name: str, date: str):
    """
    Notify all booked artists (and their entity users) that the gig has been edited.
    Creates a gig_edited notification with the gig_id stored so the frontend can
    render a clickable link to open the gig modal directly.
    """
    from backend.utils import get_all_entity_users
    from sqlalchemy import text

    # Find all booked artists on this gig (single-slot + multi-slot).
    # Audit fix (May 2026 part 8): include all in-flight contract states.
    # Previously only `gs.status='booked'` slot rows were notified — artists
    # mid-contract (pending_contract, awaiting_venue_contract, pending_venue_approval)
    # didn't get edited-gig notifications even though their booking was directly
    # affected by the venue's change.
    booked_artists = db.execute(text("""
        SELECT DISTINCT a.id as artist_id, a.name as artist_name
        FROM artists a
        WHERE a.id IN (
            SELECT artist_id FROM gigs WHERE id = :gid AND artist_id IS NOT NULL
            UNION
            SELECT artist_id FROM gig_slots
            WHERE gig_id = :gid
              AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
              AND artist_id IS NOT NULL
        )
    """), {"gid": gig_id}).mappings().all()

    # Check if multi-slot so we can include slot time per artist
    is_multi = True  # all gigs use slots

    # Audit fix (May 2026 part 4): dedupe by user_id across all booked
    # artists on this gig. A user who owns multiple booked acts (or who
    # owns both an artist AND the venue) used to receive one notification
    # per artist for a single edit. Now they get one combined notification.
    notified_user_ids = set()
    for row in booked_artists:
        slot_suffix = ""
        if is_multi:
            # Audit fix (May 2026 part 8): include in-flight states for slot lookup.
            slot = db.execute(text("""
                SELECT slot_number, start_time, end_time FROM gig_slots
                WHERE gig_id = :gid AND artist_id = :aid
                  AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                LIMIT 1
            """), {"gid": gig_id, "aid": row["artist_id"]}).mappings().first()
            if slot:
                from backend.services.email_dispatch import format_time_12hr
                slot_suffix = (
                    f" Updated Slot {slot['slot_number']}: "
                    f"{format_time_12hr(slot['start_time'])} \u2013 {format_time_12hr(slot['end_time'])}."
                )

        artist_users = get_all_entity_users(db, "artist", row["artist_id"])
        for u in artist_users:
            if u["user_id"] in notified_user_ids:
                continue
            notified_user_ids.add(u["user_id"])
            create_notification(
                db, u["user_id"], "gig_edited", "Gig Updated",
                f"{venue_name} updated your gig on {date}.{slot_suffix}",
                gig_id=gig_id, venue_id=venue_id, artist_id=row["artist_id"]
            )


def notify_gig_cancelled(db, gig_details: dict, gig_id: int, venue_id: int,
                         artist_id: int, cancelled_by: str = "venue",
                         cancellation_reason: str = "", slot_info: str = ""):
    """
    Create cancellation notifications for artist and venue users.
    
    Args:
        db: SQLAlchemy session
        gig_details: Dict with venue_name, artist_name, date, start_time, end_time,
                     artist_user_id, venue_user_id
        gig_id, venue_id, artist_id: Context IDs
        cancelled_by: "venue" or "artist"
        cancellation_reason: Optional reason text
        slot_info: Optional slot descriptor e.g. "9:00 PM – 10:00 PM"
    """
    venue_name = gig_details.get("venue_name", "venue")
    artist_name = gig_details.get("artist_name", "artist")
    date = gig_details.get("date", "")
    
    artist_user_id = gig_details.get("artist_user_id")
    venue_user_id = gig_details.get("venue_user_id")
    
    # Build messages
    slot_suffix = f" {slot_info}" if slot_info else ""
    reason_suffix = f" Reason: {cancellation_reason}" if cancellation_reason else ""
    
    if cancelled_by == "venue":
        artist_msg = f"Your gig at {venue_name.upper() if venue_name else venue_name} on {date} has been cancelled by the venue.{slot_suffix}{reason_suffix}"
        venue_msg = f"{venue_name} has cancelled the Gig on {date}.{slot_suffix}{reason_suffix}"
    else:
        artist_msg = f"You cancelled your gig at {venue_name} on {date}.{slot_suffix}"
        venue_msg = f"{artist_name} cancelled gig at {venue_name} on {date}.{slot_suffix}"
    
    # Audit fix (May 2026 part 5): fan out to ALL entity_users (owner + team
    # members) on both sides instead of just the primary user_id. Previously
    # multi-user artist/venue accounts only delivered notifications to the
    # owner — team members managing the booking would miss the cancellation.
    # Dedupe in case the same user_id appears on both sides (owns artist AND
    # has access to venue).
    from backend.utils import get_all_entity_users
    try:
        artist_users = get_all_entity_users(db, 'artist', artist_id) if artist_id else []
    except Exception:
        artist_users = [{"user_id": artist_user_id}] if artist_user_id else []
    try:
        venue_users = get_all_entity_users(db, 'venue', venue_id) if venue_id else []
    except Exception:
        venue_users = [{"user_id": venue_user_id}] if venue_user_id else []
    artist_uids = {u["user_id"] for u in artist_users if u.get("user_id")}
    venue_uids = {u["user_id"] for u in venue_users if u.get("user_id")}
    overlap = artist_uids & venue_uids
    artist_only = artist_uids - overlap
    venue_only = venue_uids - overlap

    for uid in overlap:
        create_notification(
            db, uid, "gig_cancelled", "Gig Cancelled",
            f"{artist_name} cancelled gig at {venue_name} on {date}.{slot_suffix}",
            gig_id=gig_id, venue_id=venue_id, artist_id=artist_id,
            cancellation_reason=cancellation_reason
        )
    for uid in artist_only:
        create_notification(
            db, uid, "gig_cancelled", "Gig Cancelled",
            artist_msg,
            gig_id=gig_id, venue_id=venue_id, artist_id=artist_id,
            cancellation_reason=cancellation_reason
        )
    for uid in venue_only:
        create_notification(
            db, uid, "gig_cancelled", "Gig Cancelled",
            venue_msg,
            gig_id=gig_id, venue_id=venue_id, artist_id=artist_id,
            cancellation_reason=cancellation_reason
        )


def notify_all_entity_users_cancelled(db, gig_details: dict, gig_id: int,
                                      venue_id: int, artist_id: int,
                                      cancelled_by: str = "venue",
                                      cancellation_reason: str = "",
                                      slot_info: str = ""):
    """
    Create cancellation notifications for ALL entity users (owner + team members)
    of both the artist and venue. Used for venue-initiated cancellations.
    """
    from backend.utils import get_all_entity_users
    
    venue_name = gig_details.get("venue_name", "venue")
    artist_name = gig_details.get("artist_name", "artist")
    date = gig_details.get("date", "")
    
    slot_suffix = f" {slot_info}" if slot_info else ""
    reason = cancellation_reason or "No reason provided"
    
    artist_msg = f"Your gig at {venue_name} on {date} has been cancelled by the venue.{slot_suffix}"
    venue_msg = f"{venue_name} has cancelled the Gig on {date}.{slot_suffix} - Reason: {reason}"
    
    now = datetime.now(timezone.utc)
    
    # Notify ALL artist entity users
    try:
        artist_users = get_all_entity_users(db, "artist", artist_id)
        for au in artist_users:
            if au.get("user_id"):
                create_notification(
                    db, au["user_id"], "gig_cancelled", "Gig Cancelled",
                    artist_msg,
                    gig_id=gig_id, venue_id=venue_id, artist_id=artist_id,
                    cancellation_reason=cancellation_reason
                )
    except Exception as e:
        logger.error(f"Failed to notify artist entity users: {e}")
    
    # Notify ALL venue entity users
    try:
        venue_users = get_all_entity_users(db, "venue", venue_id)
        for vu in venue_users:
            if vu.get("user_id"):
                create_notification(
                    db, vu["user_id"], "gig_cancelled", "Gig Cancelled",
                    venue_msg,
                    gig_id=gig_id, venue_id=venue_id, artist_id=artist_id,
                    cancellation_reason=cancellation_reason
                )
    except Exception as e:
        logger.error(f"Failed to notify venue entity users: {e}")
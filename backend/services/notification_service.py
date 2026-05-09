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
    """
    from backend.utils import get_all_entity_users
    time_str = format_time_12hr(gig_details.get("start_time"))
    venue_name = gig_details.get("venue_name", "venue")
    artist_name = gig_details.get("artist_name", "artist")
    date = gig_details.get("date", "")

    artist_users = get_all_entity_users(db, "artist", artist_id)
    venue_users  = get_all_entity_users(db, "venue",  venue_id)

    artist_user_ids = {u["user_id"] for u in artist_users}
    venue_user_ids  = {u["user_id"] for u in venue_users}
    shared_ids      = artist_user_ids & venue_user_ids

    for u in artist_users:
        uid = u["user_id"]
        if uid in shared_ids:
            create_notification(db, uid, "gig_booked", "Gig Booked",
                f"Your artist {artist_name} booked your venue {venue_name} on {date} at {time_str}",
                gig_id=gig_id, venue_id=venue_id, artist_id=artist_id)
        else:
            create_notification(db, uid, "gig_booked", "Gig Booked",
                f"You booked a gig at {venue_name} on {date} at {time_str}",
                gig_id=gig_id, venue_id=venue_id, artist_id=artist_id)

    for u in venue_users:
        uid = u["user_id"]
        if uid in shared_ids:
            continue  # already notified above
        create_notification(db, uid, "gig_booked", "Gig Booked",
            f"{artist_name} booked your gig on {date} at {time_str}",
            gig_id=gig_id, venue_id=venue_id, artist_id=artist_id)


def notify_gig_edited(db, gig_id: int, venue_id: int, venue_name: str, date: str):
    """
    Notify all booked artists (and their entity users) that the gig has been edited.
    Creates a gig_edited notification with the gig_id stored so the frontend can
    render a clickable link to open the gig modal directly.
    """
    from backend.utils import get_all_entity_users
    from sqlalchemy import text

    # Find all booked artists on this gig (single-slot + multi-slot)
    booked_artists = db.execute(text("""
        SELECT DISTINCT a.id as artist_id, a.name as artist_name
        FROM artists a
        WHERE a.id IN (
            SELECT artist_id FROM gigs WHERE id = :gid AND artist_id IS NOT NULL
            UNION
            SELECT artist_id FROM gig_slots WHERE gig_id = :gid AND status = 'booked' AND artist_id IS NOT NULL
        )
    """), {"gid": gig_id}).mappings().all()

    # Check if multi-slot so we can include slot time per artist
    is_multi = True  # all gigs use slots

    for row in booked_artists:
        slot_suffix = ""
        if is_multi:
            slot = db.execute(text("""
                SELECT slot_number, start_time, end_time FROM gig_slots
                WHERE gig_id = :gid AND artist_id = :aid AND status = 'booked'
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
    
    if artist_user_id == venue_user_id and artist_user_id:
        # Same user — one combined notification
        create_notification(
            db, artist_user_id, "gig_cancelled", "Gig Cancelled",
            f"{artist_name} cancelled gig at {venue_name} on {date}.{slot_suffix}",
            gig_id=gig_id, venue_id=venue_id, artist_id=artist_id,
            cancellation_reason=cancellation_reason
        )
    else:
        if artist_user_id:
            create_notification(
                db, artist_user_id, "gig_cancelled", "Gig Cancelled",
                artist_msg,
                gig_id=gig_id, venue_id=venue_id, artist_id=artist_id,
                cancellation_reason=cancellation_reason
            )
        if venue_user_id:
            create_notification(
                db, venue_user_id, "gig_cancelled", "Gig Cancelled",
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
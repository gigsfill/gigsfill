from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
from datetime import datetime
from backend.utils import utcnow_naive

router = APIRouter()

# =====================================================
# NOTIFICATION HELPERS
# =====================================================

def create_notification(db, user_id: int, notification_type: str, title: str, message: str, 
                       gig_id: int = None, venue_id: int = None, artist_id: int = None):
    """Helper function to create a notification"""
    db.execute(
        text("""
            INSERT INTO notifications
                (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
            VALUES
                (:user_id, :type, :title, :message, :gig_id, :venue_id, :artist_id, FALSE, :created_at)
        """),
        {
            "user_id": user_id,
            "type": notification_type,
            "title": title,
            "message": message,
            "gig_id": gig_id,
            "venue_id": venue_id,
            "artist_id": artist_id,
            "created_at": utcnow_naive()
        }
    )
    db.commit()

# =====================================================
# GET USER NOTIFICATIONS
# =====================================================

@router.get("/api/notifications")
def get_notifications(user=Depends(get_current_user), db=Depends(get_db)):
    """Get all notifications for current user"""
    rows = db.execute(
        text("""
            SELECT
                n.id,
                n.user_id,
                n.notification_type,
                n.title,
                n.message,
                n.gig_id,
                n.venue_id,
                n.artist_id,
                n.cancellation_reason,
                n.is_read,
                n.created_at,
                n.entity_type,
                n.entity_id,
                n.action_token,
                v.venue_name,
                a.name as artist_name,
                g.date as gig_date,
                g.title as gig_title,
                g.start_time as gig_start_time
            FROM notifications n
            LEFT JOIN venues v ON n.venue_id = v.id
            LEFT JOIN artists a ON n.artist_id = a.id
            LEFT JOIN gigs g ON n.gig_id = g.id
            WHERE n.user_id = :user_id
            ORDER BY n.created_at DESC
            LIMIT 50
        """),
        {"user_id": user.id}
    ).mappings().all()

    # Enrich slot-specific notifications with the slot's actual start_time.
    # Without this, Activity Center shows the parent gig start_time (e.g. 7pm
    # = slot 1) for a notification that's about slot 2 at 9pm.
    # Jul 2026 audit (P-H7): was N+1 (one SELECT per notification with a
    # "Slot N" suffix — up to 50/open). Now: parse ALL (gig_id, slot_num)
    # pairs first, batch-fetch every referenced slot in ONE query, then
    # attach.
    import re as _re
    parsed_rows = []
    needed_pairs: set[tuple[int, int]] = set()
    for row in rows:
        d = dict(row)
        msg = d.get("message") or ""
        m = _re.search(r"\bSlot\s+(\d+)\b", msg)
        if m and d.get("gig_id"):
            try:
                slot_num = int(m.group(1))
                d["_slot_pair"] = (int(d["gig_id"]), slot_num)
                needed_pairs.add(d["_slot_pair"])
            except Exception:
                pass
        parsed_rows.append(d)

    slot_start_by_pair: dict[tuple[int, int], str] = {}
    if needed_pairs:
        # gig_slots is indexed on gig_id; a WHERE gig_id IN (...) followed
        # by a Python filter on slot_number is faster + more portable than
        # a compound (gig_id, slot_number) OR chain.
        gid_list = list({p[0] for p in needed_pairs})
        # SQLite / Postgres both accept `IN :gids` when we expand.
        _placeholders = ",".join([f":gid{i}" for i in range(len(gid_list))])
        _params = {f"gid{i}": g for i, g in enumerate(gid_list)}
        _slot_rows = db.execute(
            text(f"SELECT gig_id, slot_number, start_time FROM gig_slots WHERE gig_id IN ({_placeholders})"),
            _params
        ).mappings().all()
        for sr in _slot_rows:
            slot_start_by_pair[(int(sr["gig_id"]), int(sr["slot_number"]))] = sr["start_time"]

    out = []
    for d in parsed_rows:
        pair = d.pop("_slot_pair", None)
        if pair and pair in slot_start_by_pair:
            d["slot_start_time"] = slot_start_by_pair[pair]
            d["slot_number"] = pair[1]
        out.append(d)
    return out

# =====================================================
# GET UNREAD COUNT
# =====================================================

@router.get("/api/notifications/unread-count")
def get_unread_count(user=Depends(get_current_user), db=Depends(get_db)):
    """Get count of unread notifications"""
    result = db.execute(
        text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id AND is_read = FALSE
        """),
        {"user_id": user.id}
    ).mappings().first()
    
    return {"count": result["count"] if result else 0}

# =====================================================
# MARK AS READ
# =====================================================

@router.post("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Mark a notification as read"""
    db.execute(
        text("""
            UPDATE notifications
            SET is_read = TRUE
            WHERE id = :notif_id AND user_id = :user_id
        """),
        {"notif_id": notification_id, "user_id": user.id}
    )
    db.commit()
    return {"ok": True}

# =====================================================
# MARK ALL AS READ
# =====================================================

@router.post("/api/notifications/mark-all-read")
def mark_all_read(user=Depends(get_current_user), db=Depends(get_db)):
    """Mark all notifications as read"""
    db.execute(
        text("""
            UPDATE notifications
            SET is_read = TRUE
            WHERE user_id = :user_id
        """),
        {"user_id": user.id}
    )
    db.commit()
    return {"ok": True}

# =====================================================
# DELETE NOTIFICATION
# =====================================================

@router.delete("/api/notifications/{notification_id}")
def delete_notification(notification_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Delete a notification. Only the owning user can delete their own.

    Audit fix (Jul 2026): return 404 when no row matched instead of
    silent ok — helps the UI distinguish "already gone" from "wrong id
    / not yours" and prevents test scripts from thinking phantom
    deletions succeeded.
    """
    result = db.execute(
        text("""
            DELETE FROM notifications
            WHERE id = :notif_id AND user_id = :user_id
        """),
        {"notif_id": notification_id, "user_id": user.id}
    )
    db.commit()
    if getattr(result, "rowcount", 0) == 0:
        raise HTTPException(404, "Notification not found")
    return {"ok": True}
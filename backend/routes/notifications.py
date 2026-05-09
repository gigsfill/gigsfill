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
    
    return [dict(row) for row in rows]

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
    """Delete a notification"""
    db.execute(
        text("""
            DELETE FROM notifications
            WHERE id = :notif_id AND user_id = :user_id
        """),
        {"notif_id": notification_id, "user_id": user.id}
    )
    db.commit()
    return {"ok": True}
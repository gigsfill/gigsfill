"""
In-App Gig Messaging
====================
Simple per-gig message threads between venue and artist.
Messages are tied to a gig_id so both parties see full context.
Email notifications on new messages with deep links back to the gig.

Endpoints:
  GET    /api/gigs/{gig_id}/messages          — load message thread
  POST   /api/gigs/{gig_id}/messages          — send a message
  PUT    /api/gigs/{gig_id}/messages/read      — mark all as read
  GET    /api/me/messages/unread-count         — badge count for header nav
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from backend.routes.auth import get_current_user
from backend.db import get_db

logger = logging.getLogger("gigsfill.messages")
router = APIRouter()

_TABLE_CREATED = False

def _ensure_gig_messages_table(db):
    """
    Idempotent: create gig_messages table if it doesn't exist yet.
    Uses a raw sqlite3 connection to avoid corrupting the SQLAlchemy session state.
    """
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    from backend.db import get_db_connection as _msg_setup_conn
    try:
        _conn = _msg_setup_conn()
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS gig_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gig_id INTEGER NOT NULL,
                sender_user_id INTEGER NOT NULL,
                sender_type TEXT NOT NULL,
                sender_name TEXT NOT NULL DEFAULT \'\',
                body TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add sender_entity_id column if missing (stores artist_id or venue_id)
        try:
            _conn.execute("ALTER TABLE gig_messages ADD COLUMN sender_entity_id INTEGER")
        except Exception:
            pass  # column already exists
        # Add target_artist_id column if missing (venue→artist messages: which artist this is for)
        try:
            _conn.execute("ALTER TABLE gig_messages ADD COLUMN target_artist_id INTEGER")
        except Exception:
            pass  # column already exists
        _conn.commit()
        _conn.close()
        _TABLE_CREATED = True
        logger.info("gig_messages table ready")
    except Exception as e:
        logger.error(f"_ensure_gig_messages_table failed: {e}")


def _get_user_role_for_gig(db, gig_id: int, user_id: int):
    """
    Returns ('venue', venue_id, name) or ('artist', artist_id, name)
    or raises 403 if the user has no relationship to this gig.
    """
    try:
        db.rollback()  # clear any stale transaction state before querying
    except Exception:
        pass
    # Check venue ownership / team membership
    venue_row = db.execute(
        text("""
            SELECT v.id, v.venue_name FROM venues v
            JOIN gigs g ON g.venue_id = v.id
            WHERE g.id = :gid AND (
                v.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid
                )
            )
            LIMIT 1
        """),
        {"gid": gig_id, "uid": user_id}
    ).mappings().first()

    if venue_row:
        return "venue", venue_row["id"], venue_row["venue_name"]

    # Check artist booking on this gig — try gig_slots first (any status), then gigs.artist_id
    artist_row = db.execute(
        text("""
            SELECT a.id, a.name FROM artists a
            JOIN gig_slots gs ON gs.artist_id = a.id
            WHERE gs.gig_id = :gid AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
            LIMIT 1
        """),
        {"gid": gig_id, "uid": user_id}
    ).mappings().first()

    if artist_row:
        return "artist", artist_row["id"], artist_row["name"]

    # Fallback: single-artist gig stored directly on gigs.artist_id (any status)
    single_artist_row = db.execute(
        text("""
            SELECT a.id, a.name FROM artists a
            JOIN gigs g ON g.artist_id = a.id
            WHERE g.id = :gid AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
            LIMIT 1
        """),
        {"gid": gig_id, "uid": user_id}
    ).mappings().first()

    if single_artist_row:
        return "artist", single_artist_row["id"], single_artist_row["name"]

    # Last resort: check if this user has ever sent a message on this gig as an artist
    msg_row = db.execute(
        text("""
            SELECT a.id, a.name FROM artists a
            JOIN gig_messages gm ON gm.sender_entity_id = a.id
            WHERE gm.gig_id = :gid AND gm.sender_type = 'artist' AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
            LIMIT 1
        """),
        {"gid": gig_id, "uid": user_id}
    ).mappings().first()

    if msg_row:
        return "artist", msg_row["id"], msg_row["name"]

    # Final check: artist was ever booked/contracted on this gig (handles cancelled gigs
    # where artist_id and slot artist_id have been cleared but messages still exist)
    contract_row = db.execute(
        text("""
            SELECT a.id, a.name FROM artists a
            JOIN gig_contracts gc ON gc.artist_id = a.id
            WHERE gc.gig_id = :gid AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
            LIMIT 1
        """),
        {"gid": gig_id, "uid": user_id}
    ).mappings().first()

    if contract_row:
        return "artist", contract_row["id"], contract_row["name"]

    # Also check transactions (covers cases where no contract but payment record exists)
    txn_row = db.execute(
        text("""
            SELECT a.id, a.name FROM artists a
            JOIN transactions t ON t.artist_id = a.id
            WHERE t.gig_id = :gid AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
            LIMIT 1
        """),
        {"gid": gig_id, "uid": user_id}
    ).mappings().first()

    if txn_row:
        return "artist", txn_row["id"], txn_row["name"]

    # Final check: artist is the target of a venue message on this gig
    target_row = db.execute(
        text("""
            SELECT a.id, a.name FROM artists a
            JOIN gig_messages gm ON gm.target_artist_id = a.id
            WHERE gm.gig_id = :gid AND (
                a.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                )
            )
            LIMIT 1
        """),
        {"gid": gig_id, "uid": user_id}
    ).mappings().first()

    if target_row:
        return "artist", target_row["id"], target_row["name"]

    raise HTTPException(403, "You are not a participant in this gig")


# ── GET MESSAGE THREAD ─────────────────────────────────────────────────────────
@router.get("/api/gigs/{gig_id}/messages")
def get_messages(gig_id: int, artist_id: int = None, user=Depends(get_current_user), db=Depends(get_db)):
    """Load messages for a gig. If artist_id provided, scopes thread to that artist."""
    try:
        _ensure_gig_messages_table(db)
    except Exception as e:
        logger.error(f"get_messages ensure failed for gig {gig_id}: {e}")
    try:
        role, entity_id, entity_name = _get_user_role_for_gig(db, gig_id, user.id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_messages _get_user_role failed for gig {gig_id} user {user.id}: {type(e).__name__}: {e}")
        raise HTTPException(500, f"Server error loading messages: {type(e).__name__}")

    # Build artist_id filter: venue scoping by specific artist, or artist scoping to self
    filter_entity_id = None
    if artist_id:
        filter_entity_id = artist_id
    elif role == "artist":
        filter_entity_id = entity_id

    messages = db.execute(
        text("""
            SELECT m.id, m.sender_user_id, m.sender_type, m.sender_name,
                   m.body, m.is_read, m.created_at,
                   CASE WHEN m.sender_user_id = :uid THEN 1 ELSE 0 END as is_mine
            FROM gig_messages m
            WHERE m.gig_id = :gid
              AND (
                :filter_eid IS NULL
                OR m.sender_entity_id = :filter_eid
                OR (m.sender_type = 'venue' AND m.target_artist_id = :filter_eid)
              )
            ORDER BY m.created_at ASC
        """),
        {"gid": gig_id, "uid": user.id, "filter_eid": filter_entity_id}
    ).mappings().all()

    # Gig summary for context header
    gig = db.execute(
        text("""
            SELECT g.title, g.date, v.venue_name as venue_name, a.name as artist_name
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
            LEFT JOIN artists a ON a.id = gs.artist_id
            WHERE g.id = :gid
            LIMIT 1
        """),
        {"gid": gig_id}
    ).mappings().first()

    return {
        "gid": gig_id,
        "gig": dict(gig) if gig else None,
        "my_role": role,
        "my_entity_id": entity_id,
        "messages": [dict(m) for m in messages]
    }


# ── SEND MESSAGE ──────────────────────────────────────────────────────────────
@router.post("/api/gigs/{gig_id}/messages")
def send_message(gig_id: int, data: dict,
                 user=Depends(get_current_user), db=Depends(get_db)):
    """Send a message in a gig thread. Triggers email notification to the other party."""
    _ensure_gig_messages_table(db)
    role, entity_id, sender_name = _get_user_role_for_gig(db, gig_id, user.id)

    body = str(data.get("body", "")).strip()[:3000]
    if not body:
        raise HTTPException(400, "Message body is required")

    # For venue sender: capture which artist this message is directed to
    target_artist_id = None
    if role == "venue":
        raw_aid = data.get("artist_id")
        if raw_aid:
            target_artist_id = int(raw_aid)

    # For venue sender: if the frontend passed a venue_id (the specific venue
    # the user is messaging from), verify it and override entity_id. This handles
    # the case where one user owns multiple venues.
    if role == "venue":
        raw_vid = data.get("venue_id")
        if raw_vid:
            candidate_vid = int(raw_vid)
            ok_v = db.execute(
                text("""
                    SELECT 1 FROM venues v
                    WHERE v.id = :vid AND (
                        v.user_id = :uid
                        OR EXISTS (SELECT 1 FROM entity_users eu
                                   WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid)
                    )
                """),
                {"vid": candidate_vid, "uid": user.id}
            ).first()
            if ok_v:
                entity_id = candidate_vid
                venue_name_row = db.execute(
                    text("SELECT venue_name FROM venues WHERE id = :vid"),
                    {"vid": candidate_vid}
                ).first()
                if venue_name_row:
                    sender_name = venue_name_row[0]

    # For artist sender: if the frontend passed an artist_id (the specific artist
    # the user is messaging as), use it to override the entity_id that
    # _get_user_role_for_gig returned. This handles the case where one user
    # owns multiple artists — we must store the message under the correct one.
    if role == "artist":
        raw_aid = data.get("artist_id")
        if raw_aid:
            candidate_id = int(raw_aid)
            # Verify this artist actually belongs to the current user
            ok = db.execute(
                text("""
                    SELECT 1 FROM artists a
                    WHERE a.id = :aid AND (
                        a.user_id = :uid
                        OR EXISTS (SELECT 1 FROM entity_users eu
                                   WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
                    )
                """),
                {"aid": candidate_id, "uid": user.id}
            ).first()
            if ok:
                entity_id = candidate_id
                # Update sender_name to match the correct artist
                artist_name_row = db.execute(
                    text("SELECT name FROM artists WHERE id = :aid"),
                    {"aid": candidate_id}
                ).first()
                if artist_name_row:
                    sender_name = artist_name_row[0]

    # Insert message — store target_artist_id for venue messages so threads stay scoped
    result = db.execute(
        text("""
            INSERT INTO gig_messages (gig_id, sender_user_id, sender_type, sender_name, body, sender_entity_id, target_artist_id)
            VALUES (:gid, :uid, :role, :name, :body, :eid, :taid)
        """),
        {"gid": gig_id, "uid": user.id, "role": role, "name": sender_name, "body": body,
         "eid": entity_id, "taid": target_artist_id}
    )
    msg_id = result.lastrowid
    db.commit()

    # For venue sender: email the specific target artist
    notify_entity_id = entity_id  # default: use sender's entity_id
    if role == "venue" and target_artist_id:
        notify_entity_id = target_artist_id

    # Send email notification to the other party
    try:
        _notify_other_party(db, gig_id, user.id, role, sender_name, body, sender_entity_id=notify_entity_id)
    except Exception as e:
        logger.warning(f"Message notification failed for gig {gig_id}: {e}")

    return {"ok": True, "message_id": msg_id}


# ── MARK THREAD AS READ ───────────────────────────────────────────────────────
@router.put("/api/gigs/{gig_id}/messages/read")
def mark_read(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Mark all messages in this gig thread as read for the current user."""
    try:
        _ensure_gig_messages_table(db)
    except Exception as e:
        logger.error(f"mark_read ensure failed for gig {gig_id}: {e}")
    try:
        _get_user_role_for_gig(db, gig_id, user.id)  # auth check
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"mark_read _get_user_role failed gig {gig_id} user {user.id}: {type(e).__name__}: {e}")
        raise HTTPException(500, f"Server error: {type(e).__name__}")

    db.execute(
        text("""
            UPDATE gig_messages
            SET is_read = 1
            WHERE gig_id = :gid AND sender_user_id != :uid AND is_read = 0
        """),
        {"gid": gig_id, "uid": user.id}
    )
    db.commit()
    return {"ok": True}



# ── FULL INBOX (all messages across all gigs) ─────────────────────────────────
@router.get("/api/me/messages")
def get_inbox(artist_id: int = None, venue_id: int = None, user=Depends(get_current_user), db=Depends(get_db)):
    """Returns all messages across all gigs the current user is party to, newest first.
    If artist_id is provided, only returns messages for gigs that specific artist is party to."""
    # Ensure table exists (graceful handling before db migration runs)
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS gig_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gig_id INTEGER NOT NULL,
                sender_user_id INTEGER NOT NULL,
                sender_type TEXT NOT NULL CHECK(sender_type IN ('venue', 'artist', 'admin')),
                sender_name TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.commit()
    except Exception:
        pass
    # Add missing columns (idempotent)
    for col_sql in [
        "ALTER TABLE gig_messages ADD COLUMN sender_entity_id INTEGER",
        "ALTER TABLE gig_messages ADD COLUMN target_artist_id INTEGER",
    ]:
        try:
            db.execute(text(col_sql)); db.commit()
        except Exception:
            pass

    try:
        rows = db.execute(
            text("""
                WITH thread_pairs AS (
                    SELECT DISTINCT
                        m.gig_id,
                        COALESCE(
                            CASE WHEN m.sender_type='artist' THEN m.sender_entity_id END,
                            CASE WHEN m.sender_type='venue'  THEN m.target_artist_id  END
                        ) as artist_id
                    FROM gig_messages m
                    WHERE COALESCE(
                        CASE WHEN m.sender_type='artist' THEN m.sender_entity_id END,
                        CASE WHEN m.sender_type='venue'  THEN m.target_artist_id  END
                    ) IS NOT NULL
                )
                SELECT
                    latest.id,
                    latest.gig_id,
                    latest.body,
                    latest.sender_type,
                    latest.sender_name,
                    latest.is_read,
                    latest.created_at,
                    g.date    as gig_date,
                    g.start_time,
                    g.end_time,
                    g.title   as gig_title,
                    v.id      as venue_id,
                    v.venue_name,
                    tp.artist_id,
                    a.name    as artist_name,
                    (SELECT COUNT(*) FROM gig_messages um
                     WHERE um.gig_id = tp.gig_id
                       AND um.sender_user_id != :uid
                       AND um.is_read = 0
                       AND (um.sender_entity_id = tp.artist_id
                            OR (um.sender_type='venue' AND (um.target_artist_id = tp.artist_id OR um.target_artist_id IS NULL)))
                    ) as unread_count
                FROM thread_pairs tp
                JOIN gigs    g ON g.id = tp.gig_id
                JOIN venues  v ON v.id = g.venue_id
                JOIN artists a ON a.id = tp.artist_id
                JOIN gig_messages latest ON latest.id = (
                    SELECT MAX(m2.id) FROM gig_messages m2
                    WHERE m2.gig_id = tp.gig_id
                      AND (m2.sender_entity_id = tp.artist_id
                           OR (m2.sender_type='venue' AND (m2.target_artist_id = tp.artist_id OR m2.target_artist_id IS NULL)))
                )
                WHERE tp.gig_id IN (
                    SELECT g2.id FROM gigs g2 JOIN venues v2 ON v2.id=g2.venue_id WHERE v2.user_id=:uid
                    UNION
                    SELECT g2.id FROM gigs g2 JOIN venues v2 ON v2.id=g2.venue_id
                      JOIN entity_users eu ON eu.entity_type='venue' AND eu.entity_id=v2.id WHERE eu.user_id=:uid
                    UNION
                    SELECT g2.id FROM gigs g2 JOIN artists a2 ON a2.id=g2.artist_id WHERE a2.user_id=:uid
                    UNION
                    SELECT gs.gig_id FROM gig_slots gs JOIN artists a2 ON a2.id=gs.artist_id
                      WHERE gs.status='booked' AND a2.user_id=:uid
                    UNION
                    SELECT gs.gig_id FROM gig_slots gs JOIN artists a2 ON a2.id=gs.artist_id
                      JOIN entity_users eu ON eu.entity_type='artist' AND eu.entity_id=a2.id
                      WHERE gs.status='booked' AND eu.user_id=:uid
                    UNION
                    SELECT gs.gig_id FROM gig_slots gs JOIN artists a2 ON a2.id=gs.artist_id
                      WHERE gs.status='pending_venue_approval' AND a2.user_id=:uid
                    UNION
                    SELECT gs.gig_id FROM gig_slots gs JOIN artists a2 ON a2.id=gs.artist_id
                      JOIN entity_users eu ON eu.entity_type='artist' AND eu.entity_id=a2.id
                      WHERE gs.status='pending_venue_approval' AND eu.user_id=:uid
                    UNION
                    SELECT gm.gig_id FROM gig_messages gm
                      JOIN artists a2 ON a2.id=gm.target_artist_id WHERE a2.user_id=:uid
                    UNION
                    SELECT gm.gig_id FROM gig_messages gm
                      JOIN artists a2 ON a2.id=gm.target_artist_id
                      JOIN entity_users eu ON eu.entity_type='artist' AND eu.entity_id=a2.id
                      WHERE eu.user_id=:uid
                )
                AND (:artist_id IS NULL OR tp.artist_id = :artist_id)
                AND (:venue_id IS NULL OR g.venue_id = :venue_id)
                ORDER BY latest.created_at DESC
                LIMIT 200
            """),
            {"uid": user.id, "artist_id": artist_id, "venue_id": venue_id}
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"get_inbox error: {e}")
        return []


# ── UNREAD COUNT (for nav badge) ─────────────────────────────────────────────
@router.get("/api/me/messages/unread-count")
def unread_count(venue_id: int = None, artist_id: int = None, user=Depends(get_current_user), db=Depends(get_db)):
    """Returns total unread message count for the current user, scoped to venue or artist if provided."""
    # Build gig_id set scoped to the specific entity if provided
    if venue_id:
        # Only count unread for this specific venue
        venue_gigs = db.execute(
            text("""
                SELECT g.id FROM gigs g
                JOIN venues v ON v.id = g.venue_id
                WHERE g.venue_id = :vid AND (
                    v.user_id = :uid
                    OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid)
                )
            """),
            {"vid": venue_id, "uid": user.id}
        ).fetchall()
        all_gig_ids = [r[0] for r in venue_gigs]
    elif artist_id:
        # Only count unread for this specific artist
        artist_gigs = db.execute(
            text("""
                SELECT gs.gig_id FROM gig_slots gs
                JOIN artists a ON a.id = gs.artist_id
                WHERE a.id = :aid AND (
                    a.user_id = :uid
                    OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
                )
                UNION
                SELECT g2.id FROM gigs g2 JOIN artists a2 ON a2.id=g2.artist_id
                WHERE a2.id = :aid AND (a2.user_id=:uid OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a2.id AND eu.user_id=:uid))
                UNION
                SELECT gm.gig_id FROM gig_messages gm JOIN artists a2 ON a2.id=gm.target_artist_id
                WHERE a2.id = :aid AND (a2.user_id=:uid OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a2.id AND eu.user_id=:uid))
            """),
            {"aid": artist_id, "uid": user.id}
        ).fetchall()
        all_gig_ids = [r[0] for r in artist_gigs]
    else:
        # No scope — count all gigs for this user (fallback, shouldn't normally be used)
        venue_gigs = db.execute(
            text("""
                SELECT g.id FROM gigs g JOIN venues v ON v.id = g.venue_id WHERE v.user_id = :uid
                UNION
                SELECT g.id FROM gigs g JOIN venues v ON v.id = g.venue_id
                  JOIN entity_users eu ON eu.entity_type='venue' AND eu.entity_id=v.id WHERE eu.user_id=:uid
            """),
            {"uid": user.id}
        ).fetchall()
        artist_gigs = db.execute(
            text("""
                SELECT gs.gig_id FROM gig_slots gs JOIN artists a ON a.id=gs.artist_id
                WHERE gs.status='booked' AND a.user_id=:uid
                UNION
                SELECT gs.gig_id FROM gig_slots gs JOIN artists a ON a.id=gs.artist_id
                  JOIN entity_users eu ON eu.entity_type='artist' AND eu.entity_id=a.id
                WHERE gs.status='booked' AND eu.user_id=:uid
                UNION
                SELECT gs.gig_id FROM gig_slots gs JOIN artists a ON a.id=gs.artist_id
                WHERE gs.status='pending_venue_approval' AND a.user_id=:uid
                UNION
                SELECT g2.id FROM gigs g2 JOIN artists a ON a.id=g2.artist_id WHERE a.user_id=:uid
                UNION
                SELECT gm.gig_id FROM gig_messages gm JOIN artists a ON a.id=gm.target_artist_id WHERE a.user_id=:uid
            """),
            {"uid": user.id}
        ).fetchall()
        all_gig_ids = list({r[0] for r in venue_gigs} | {r[0] for r in artist_gigs})

    if not all_gig_ids:
        return {"unread": 0}

    placeholders = ", ".join(f":g{i}" for i in range(len(all_gig_ids)))
    params = {f"g{i}": gid for i, gid in enumerate(all_gig_ids)}
    params["uid"] = user.id

    count = db.execute(
        text(f"""
            SELECT COUNT(*) FROM gig_messages
            WHERE gig_id IN ({placeholders})
              AND sender_user_id != :uid
              AND is_read = 0
        """),
        params
    ).scalar() or 0

    return {"unread": count}


# ── EMAIL NOTIFICATION HELPER ─────────────────────────────────────────────────
def _notify_other_party(db, gig_id: int, sender_user_id: int, sender_role: str,
                        sender_name: str, message_preview: str, sender_entity_id: int = None):
    """Send email to the other party (venue emails artist, artist emails venue)."""
    from sqlalchemy import text as T

    # Get gig info — resolve artist from sender_entity_id (if artist) or booked slot
    if sender_role == "artist" and sender_entity_id:
        # Use the specific artist who sent the message
        gig = db.execute(
            T("""
                SELECT g.title, g.date, v.venue_name as venue_name, v.id as venue_id,
                       a.name as artist_name, a.id as artist_id,
                       vu.email as venue_email, au.email as artist_email
                FROM gigs g
                JOIN venues v ON v.id = g.venue_id
                JOIN users vu ON vu.id = v.user_id
                JOIN artists a ON a.id = :aid
                JOIN users au ON au.id = a.user_id
                WHERE g.id = :gid
                LIMIT 1
            """),
            {"gid": gig_id, "aid": sender_entity_id}
        ).mappings().first()
    else:
        # Venue sender: sender_entity_id is the TARGET artist_id (passed from frontend)
        if sender_entity_id:
            gig = db.execute(
                T("""
                    SELECT g.title, g.date, v.venue_name as venue_name, v.id as venue_id,
                           a.name as artist_name, a.id as artist_id,
                           vu.email as venue_email, au.email as artist_email
                    FROM gigs g
                    JOIN venues v ON v.id = g.venue_id
                    JOIN users vu ON vu.id = v.user_id
                    JOIN artists a ON a.id = :aid
                    JOIN users au ON au.id = a.user_id
                    WHERE g.id = :gid
                    LIMIT 1
                """),
                {"gid": gig_id, "aid": sender_entity_id}
            ).mappings().first()
        else:
            # Fallback: find artist from first booked slot
            gig = db.execute(
                T("""
                    SELECT g.title, g.date, v.venue_name as venue_name, v.id as venue_id,
                           COALESCE(a_direct.name, a_slot.name) as artist_name,
                           COALESCE(a_direct.id, a_slot.id) as artist_id,
                           vu.email as venue_email,
                           COALESCE(au_direct.email, au_slot.email) as artist_email
                    FROM gigs g
                    JOIN venues v ON v.id = g.venue_id
                    JOIN users vu ON vu.id = v.user_id
                    LEFT JOIN artists a_direct ON a_direct.id = g.artist_id
                    LEFT JOIN users au_direct ON au_direct.id = a_direct.user_id
                    LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
                        AND gs.id = (SELECT MIN(gs2.id) FROM gig_slots gs2
                                     WHERE gs2.gig_id = g.id AND gs2.status = 'booked')
                    LEFT JOIN artists a_slot ON a_slot.id = gs.artist_id
                    LEFT JOIN users au_slot ON au_slot.id = a_slot.user_id
                    WHERE g.id = :gid
                    LIMIT 1
                """),
                {"gid": gig_id}
            ).mappings().first()

    if not gig:
        logger.warning(f"_notify_other_party: no gig found for gig_id={gig_id}")
        return

    # Who gets the notification?
    if sender_role == "venue":
        to_email = gig["artist_email"]
        to_name = gig["artist_name"] or "Artist"
    else:
        to_email = gig["venue_email"]
        to_name = gig["venue_name"] or "Venue"

    if not to_email:
        logger.warning(f"_notify_other_party: no email found for recipient (gig={gig_id}, role={sender_role})")
        return

    # Build correct deep-link
    site_url = db.execute(
        T("SELECT setting_value FROM platform_settings WHERE setting_key='site_url'")
    ).scalar() or "https://gigsfill.com"
    if sender_role == "artist":
        gig_link = f"{site_url}/app/venue-create-gigs.html?venue_id={gig.get('venue_id', '')}#messages"
    else:
        artist_id_for_link = sender_entity_id or gig.get("artist_id", "")
        gig_link = f"{site_url}/app/artist-book-gigs.html?artist_id={artist_id_for_link}#messages"

    gig_date_str = gig.get('date', '') or ''
    gig_title_str = gig.get('title') or 'Gig'
    venue_name_str = gig.get('venue_name', '') or ''

    # Fetch thread filtered to this specific artist's conversation
    import sqlite3 as _sq
    from pathlib import Path as _P
    _dbp = _P(__file__).parent.parent.parent / "backend.db"
    thread_rows = []
    try:
        _conn = _sq.connect(str(_dbp))
        _conn.row_factory = _sq.Row
        thread_artist_id = sender_entity_id if sender_entity_id else None
        if thread_artist_id:
            thread_rows = _conn.execute(
                "SELECT sender_name, sender_type, body, created_at FROM gig_messages "
                "WHERE gig_id=? AND ("
                "  sender_entity_id=? "
                "  OR (sender_type='venue' AND target_artist_id=?)"
                ") "
                "ORDER BY created_at DESC LIMIT 20",
                (gig_id, thread_artist_id, thread_artist_id)
            ).fetchall()
        else:
            thread_rows = _conn.execute(
                "SELECT sender_name, sender_type, body, created_at FROM gig_messages "
                "WHERE gig_id=? ORDER BY created_at DESC LIMIT 20",
                (gig_id,)
            ).fetchall()
        _conn.close()
    except Exception as _e:
        logger.warning(f"Thread fetch failed: {_e}")

    # Build thread HTML (most recent first)
    thread_html = ""
    for row in thread_rows:
        is_venue = row["sender_type"] == "venue"
        bg = "#e8f4fd" if is_venue else "#f0fdf4"
        border = "#0ea5e9" if is_venue else "#22c55e"
        ts = ""
        if row["created_at"]:
            try:
                from datetime import datetime as _dt
                ts = _dt.strptime(row["created_at"][:19], "%Y-%m-%d %H:%M:%S").strftime("%b %-d, %Y %-I:%M %p")
            except Exception:
                ts = row["created_at"][:16]
        thread_html += f"""
        <tr><td style="padding:2px 0;">
          <div style="background:{bg};border-left:3px solid {border};border-radius:4px;padding:7px 14px;margin-bottom:2px;">
            <div style="font-size:11px;color:#6b7280;margin-bottom:2px;"><strong style="color:#374151;">{row["sender_name"]}</strong> &nbsp;·&nbsp; {ts}</div>
            <div style="font-size:13px;color:#111827;line-height:1.5;">{row["body"]}</div>
          </div>
        </td></tr>"""

    styled = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#f8f9fa;">
<tbody><tr><td style="padding:40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr><td style="padding:32px 40px 24px 40px;border-bottom:1px solid #eee;">
  <img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;" />
</td></tr>
<tr><td style="padding:32px 40px;">
  <h1 style="margin:0 0 6px;font-size:20px;font-weight:600;color:#111827;">New message from {sender_name}</h1>
  <p style="margin:0 0 24px;font-size:13px;color:#6b7280;">
    Re: <strong>{gig_title_str}</strong> at <strong>{venue_name_str}</strong> &nbsp;·&nbsp; {gig_date_str}
  </p>
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin-bottom:24px;">
  <tbody>{thread_html}</tbody>
  </table>
  <a href="{gig_link}" style="display:inline-block;background:#059669;color:#fff;text-decoration:none;padding:11px 22px;border-radius:6px;font-weight:600;font-size:14px;">View Full Conversation</a>
</td></tr>
<tr><td style="padding:20px 40px;border-top:1px solid #eee;text-align:center;">
  <p style="margin:0;font-size:11px;color:#9ca3af;">GigsFill · <a href="https://gigsfill.com" style="color:#9ca3af;">gigsfill.com</a></p>
</td></tr>
</tbody></table>
</td></tr></tbody></table>
</body></html>"""

    # Use EmailService for SMTP — it handles all key name variants and fallbacks
    try:
        from backend.email_service import EmailService
        email_service = EmailService(db)
        if not email_service.enabled:
            logger.warning(f"_notify_other_party: email not enabled, skipping notification")
            return
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"New message from {sender_name} — {to_name}"
        if email_service.from_name:
            from email.utils import formataddr
            msg["From"] = formataddr((email_service.from_name, email_service.from_email))
        else:
            msg["From"] = email_service.from_email
        msg["To"] = to_email
        msg.attach(MIMEText(styled, "html"))
        if email_service.smtp_port == 465:
            with smtplib.SMTP_SSL(email_service.smtp_server, email_service.smtp_port, timeout=15) as server:
                server.login(email_service.smtp_username, email_service.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(email_service.smtp_server, email_service.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(email_service.smtp_username, email_service.smtp_password)
                server.send_message(msg)
        logger.info(f"Message notification sent to {to_email} (gig={gig_id}, role={sender_role})")
    except Exception as e:
        logger.warning(f"Message email notification failed: {e}")

    # Get gig info — resolve artist from sender_entity_id (if artist) or booked slot
    if sender_role == "artist" and sender_entity_id:
        # Use the specific artist who sent the message
        gig = db.execute(
            T("""
                SELECT g.title, g.date, v.venue_name as venue_name, v.id as venue_id,
                       a.name as artist_name, a.id as artist_id,
                       vu.email as venue_email, au.email as artist_email
                FROM gigs g
                JOIN venues v ON v.id = g.venue_id
                JOIN users vu ON vu.id = v.user_id
                JOIN artists a ON a.id = :aid
                JOIN users au ON au.id = a.user_id
                WHERE g.id = :gid
                LIMIT 1
            """),
            {"gid": gig_id, "aid": sender_entity_id}
        ).mappings().first()
    else:
        # Venue sender: sender_entity_id is now the TARGET artist_id (passed from frontend)
        # Use it directly to look up the correct artist email
        if sender_entity_id:
            gig = db.execute(
                T("""
                    SELECT g.title, g.date, v.venue_name as venue_name, v.id as venue_id,
                           a.name as artist_name, a.id as artist_id,
                           vu.email as venue_email, au.email as artist_email
                    FROM gigs g
                    JOIN venues v ON v.id = g.venue_id
                    JOIN users vu ON vu.id = v.user_id
                    JOIN artists a ON a.id = :aid
                    JOIN users au ON au.id = a.user_id
                    WHERE g.id = :gid
                    LIMIT 1
                """),
                {"gid": gig_id, "aid": sender_entity_id}
            ).mappings().first()
        else:
            # Fallback: find artist from first booked slot
            gig = db.execute(
                T("""
                    SELECT g.title, g.date, v.venue_name as venue_name, v.id as venue_id,
                           COALESCE(a_direct.name, a_slot.name) as artist_name,
                           COALESCE(a_direct.id, a_slot.id) as artist_id,
                           vu.email as venue_email,
                           COALESCE(au_direct.email, au_slot.email) as artist_email
                    FROM gigs g
                    JOIN venues v ON v.id = g.venue_id
                    JOIN users vu ON vu.id = v.user_id
                    LEFT JOIN artists a_direct ON a_direct.id = g.artist_id
                    LEFT JOIN users au_direct ON au_direct.id = a_direct.user_id
                    LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
                        AND gs.id = (SELECT MIN(gs2.id) FROM gig_slots gs2
                                     WHERE gs2.gig_id = g.id AND gs2.status = 'booked')
                    LEFT JOIN artists a_slot ON a_slot.id = gs.artist_id
                    LEFT JOIN users au_slot ON au_slot.id = a_slot.user_id
                    WHERE g.id = :gid
                    LIMIT 1
                """),
                {"gid": gig_id}
            ).mappings().first()

    if not gig:
        return

    # Who gets the notification?
    if sender_role == "venue":
        to_email = gig["artist_email"]
        to_name = gig["artist_name"] or "Artist"
    else:
        to_email = gig["venue_email"]
        to_name = gig["venue_name"] or "Venue"

    if not to_email:
        return

    # Build correct deep-link — artist link uses their actual artist_id
    site_url = db.execute(
        T("SELECT setting_value FROM platform_settings WHERE setting_key='site_url'")
    ).scalar() or "https://gigsfill.com"
    if sender_role == "artist":
        gig_link = f"{site_url}/app/venue-create-gigs.html?venue_id={gig.get('venue_id', '')}#messages"
    else:
        artist_id_for_link = sender_entity_id or gig.get("artist_id", "")
        gig_link = f"{site_url}/app/artist-book-gigs.html?artist_id={artist_id_for_link}#messages"

    gig_date_str = gig.get('date', '') or ''
    gig_title_str = gig.get('title') or 'Gig'
    venue_name_str = gig.get('venue_name', '') or ''

    # Fetch thread filtered to this specific artist's conversation
    import sqlite3 as _sq
    from pathlib import Path as _P
    _dbp = _P(__file__).parent.parent.parent / "backend.db"
    thread_rows = []
    try:
        _conn = _sq.connect(str(_dbp))
        _conn.row_factory = _sq.Row
        # The artist_id for this thread — used to scope both sides
        # sender_entity_id is artist_id when sender=artist, or target artist_id when sender=venue
        thread_artist_id = sender_entity_id if sender_entity_id else None
        if thread_artist_id:
            thread_rows = _conn.execute(
                "SELECT sender_name, sender_type, body, created_at FROM gig_messages "
                "WHERE gig_id=? AND ("
                "  sender_entity_id=? "
                "  OR (sender_type='venue' AND (target_artist_id=? OR target_artist_id IS NULL))"
                ") "
                "ORDER BY created_at DESC LIMIT 20",
                (gig_id, thread_artist_id, thread_artist_id)
            ).fetchall()
        else:
            thread_rows = _conn.execute(
                "SELECT sender_name, sender_type, body, created_at FROM gig_messages "
                "WHERE gig_id=? ORDER BY created_at DESC LIMIT 20",
                (gig_id,)
            ).fetchall()
        _conn.close()
    except Exception as _e:
        logger.warning(f"Thread fetch failed: {_e}")

    # Build thread HTML (most recent first)
    thread_html = ""
    for row in thread_rows:
        is_venue = row["sender_type"] == "venue"
        bg = "#e8f4fd" if is_venue else "#f0fdf4"
        border = "#0ea5e9" if is_venue else "#22c55e"
        ts = ""
        if row["created_at"]:
            try:
                from datetime import datetime as _dt
                ts = _dt.strptime(row["created_at"][:19], "%Y-%m-%d %H:%M:%S").strftime("%b %-d, %Y %-I:%M %p")
            except Exception:
                ts = row["created_at"][:16]
        thread_html += f"""
        <tr><td style="padding:2px 0;">
          <div style="background:{bg};border-left:3px solid {border};border-radius:4px;padding:7px 14px;margin-bottom:2px;">
            <div style="font-size:11px;color:#6b7280;margin-bottom:2px;"><strong style="color:#374151;">{row["sender_name"]}</strong> &nbsp;·&nbsp; {ts}</div>
            <div style="font-size:13px;color:#111827;line-height:1.5;">{row["body"]}</div>
          </div>
        </td></tr>"""

    styled = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#f8f9fa;">
<tbody><tr><td style="padding:40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr><td style="padding:32px 40px 24px 40px;border-bottom:1px solid #eee;">
  <img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;" />
</td></tr>
<tr><td style="padding:32px 40px;">
  <h1 style="margin:0 0 6px;font-size:20px;font-weight:600;color:#111827;">New message from {sender_name}</h1>
  <p style="margin:0 0 24px;font-size:13px;color:#6b7280;">
    Re: <strong>{gig_title_str}</strong> at <strong>{venue_name_str}</strong> &nbsp;·&nbsp; {gig_date_str}
  </p>
  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin-bottom:24px;">
  <tbody>{thread_html}</tbody>
  </table>
  <a href="{gig_link}" style="display:inline-block;background:#059669;color:#fff;text-decoration:none;padding:11px 22px;border-radius:6px;font-weight:600;font-size:14px;">View Full Conversation</a>
</td></tr>
<tr><td style="padding:20px 40px;border-top:1px solid #eee;text-align:center;">
  <p style="margin:0;font-size:11px;color:#9ca3af;">GigsFill · <a href="https://gigsfill.com" style="color:#9ca3af;">gigsfill.com</a></p>
</td></tr>
</tbody></table>
</td></tr></tbody></table>
</body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"New message from {sender_name} — {gig.get('venue_name', 'GigsFill')}"
        msg["From"] = from_email
        msg["To"] = to_email
        msg.attach(MIMEText(styled, "html"))
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        server.starttls()
        server.login(from_email, email_pass)
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        logger.warning(f"Message email notification failed: {e}")


# ── DEBUG: diagnose gig messaging issues ──────────────────────────────────────
@router.get("/api/gigs/{gig_id}/messages/debug")
def debug_gig_messages(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Diagnostic endpoint - remove after debugging."""
    import sqlite3 as _sq
    from pathlib import Path as _P
    _dbp = _P(__file__).parent.parent.parent / "backend.db"
    _c = _sq.connect(str(_dbp))
    
    # Check table exists
    tables = [r[0] for r in _c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    
    # Check gig details
    gig = _c.execute("SELECT id, status, artist_id, venue_id FROM gigs WHERE id=?", (gig_id,)).fetchone()
    
    # Check gig_slots
    slots = _c.execute("SELECT id, status, artist_id FROM gig_slots WHERE gig_id=?", (gig_id,)).fetchall()
    
    # Check current user's artist/venue
    user_artists = _c.execute("SELECT id, name, user_id FROM artists WHERE user_id=?", (user.id,)).fetchall()
    user_venues = _c.execute("SELECT id, name, user_id FROM venues WHERE user_id=?", (user.id,)).fetchall()
    
    _c.close()
    
    return {
        "gid": gig_id,
        "current_user_id": user.id,
        "tables_exist": {
            "gig_messages": "gig_messages" in tables,
            "gig_slots": "gig_slots" in tables,
            "artists": "artists" in tables,
        },
        "gig": {"id": gig[0], "status": gig[1], "artist_id": gig[2], "venue_id": gig[3]} if gig else None,
        "gig_slots": [{"id": s[0], "status": s[1], "artist_id": s[2]} for s in slots],
        "user_artists": [{"id": a[0], "name": a[1]} for a in user_artists],
        "user_venues": [{"id": v[0], "name": v[1]} for v in user_venues],
    }

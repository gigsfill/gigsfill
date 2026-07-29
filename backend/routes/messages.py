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
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from backend.routes.auth import get_current_user
from backend.db import get_db
from backend.rate_limiter import limiter, rate_email_send_limit

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

    # Jul 2026 refactor: legacy gigs.artist_id fallback removed. Every
    # booking has a `gig_slots` row post-backfill so the slot lookup above
    # covers every case. Kept as an empty variable for the return-None
    # branch below without further code changes.
    single_artist_row = db.execute(
        text("""
            SELECT NULL as id, NULL as name WHERE 0=1
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

    # Build artist_id filter: venue scoping by specific artist, or artist scoping to self.
    # Audit fix (May 2026 part 10): when caller is an artist, IGNORE any
    # artist_id query param and force-scope to their own entity. Previously,
    # an artist on a multi-slot gig could pass ?artist_id=<other_artist_on_same_gig>
    # and read that artist's private thread with the venue.
    filter_entity_id = None
    if role == "artist":
        # Artists may also own multiple acts on the same gig. If they explicitly
        # name one of THEIR OWN artist IDs, honor it; otherwise default to the
        # first match resolved by _get_user_role_for_gig.
        if artist_id:
            try:
                owned = db.execute(text("""
                    SELECT 1 FROM artists a
                    WHERE a.id = :aid AND (
                        a.user_id = :uid
                        OR EXISTS (SELECT 1 FROM entity_users eu
                                   WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
                    )
                """), {"aid": int(artist_id), "uid": user.id}).first()
            except Exception:
                owned = None
            filter_entity_id = int(artist_id) if owned else entity_id
        else:
            filter_entity_id = entity_id
    elif artist_id:
        # Venue caller — can scope to any artist on their gig (existing behavior)
        filter_entity_id = artist_id

    # Resolve sender_name LIVE via JOIN so a venue/artist rename
    # updates the "from" label on every past message in the thread —
    # the stored m.sender_name is only used as a fallback when the
    # entity was deleted or the sender_type is something other than
    # venue/artist. Jul 20 2026 fix.
    messages = db.execute(
        text("""
            SELECT m.id, m.sender_user_id, m.sender_type,
                   COALESCE(v.venue_name, a.name, m.sender_name) AS sender_name,
                   m.body, m.is_read, m.created_at,
                   CASE WHEN m.sender_user_id = :uid THEN 1 ELSE 0 END as is_mine
            FROM gig_messages m
            LEFT JOIN venues  v ON m.sender_type = 'venue'  AND v.id = m.sender_entity_id
            LEFT JOIN artists a ON m.sender_type = 'artist' AND a.id = m.sender_entity_id
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

    # Gig summary for context header.
    # FIX (May 21 2026): the previous query joined gig_slots with status='booked'
    # LIMIT 1, returning whichever booked artist came first in the DB — wrong
    # for multi-slot gigs where the thread is scoped to a specific artist.
    # Now: if filter_entity_id is set (artist_id param OR artist viewing their
    # own thread), look up THAT artist by id. Otherwise fall back to whichever
    # artist is on the gig (single-slot or multi-slot summary view).
    if filter_entity_id:
        gig = db.execute(
            text("""
                SELECT g.title, g.date, v.venue_name as venue_name,
                       a.name as artist_name
                FROM gigs g
                JOIN venues v ON v.id = g.venue_id
                LEFT JOIN artists a ON a.id = :aid
                WHERE g.id = :gid
                LIMIT 1
            """),
            {"gid": gig_id, "aid": filter_entity_id}
        ).mappings().first()
    else:
        gig = db.execute(
            text("""
                SELECT g.title, g.date, v.venue_name as venue_name,
                       a.name as artist_name
                FROM gigs g
                JOIN venues v ON v.id = g.venue_id
                -- Audit fix (May 2026 part 6): include awaiting_venue_contract + pending_venue_approval
                LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                LEFT JOIN artists a ON a.id = gs.artist_id
                WHERE g.id = :gid
                ORDER BY gs.slot_number ASC
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
@limiter.limit(rate_email_send_limit)
def send_message(request: Request, gig_id: int, data: dict,
                 user=Depends(get_current_user), db=Depends(get_db)):
    """Send a message in a gig thread. Triggers email notification to the other party.

    Rate-limited (May 2026 audit): 10/minute per IP. Each message sends an
    email; without a cap, a malicious user could grief the counterparty's
    inbox or burn through SMTP quota."""
    _ensure_gig_messages_table(db)
    role, entity_id, sender_name = _get_user_role_for_gig(db, gig_id, user.id)

    body = str(data.get("body", "")).strip()[:3000]
    if not body:
        raise HTTPException(400, "Message body is required")

    # Audit fix (May 2026 part 10): refuse new messages on cancelled gigs.
    # Threads themselves remain readable (post-cancel follow-up about pay,
    # logistics, etc. is fine), but writes are blocked so the cancelled
    # thread doesn't keep growing or generating notifications.
    try:
        _g_status = db.execute(text(
            "SELECT status FROM gigs WHERE id = :gid"
        ), {"gid": gig_id}).scalar()
        if _g_status == "cancelled":
            raise HTTPException(409, "This gig has been cancelled — messages are read-only.")
    except HTTPException:
        raise
    except Exception:
        pass

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

    # Audit fix (May 2026 part 10): per-thread email coalescing. Suppress the
    # email if there's an unread message from THIS sender on THIS thread sent
    # in the last 5 minutes. A chatty exchange of 10 quick replies used to
    # send 10 separate emails to the recipient, burning SMTP reputation and
    # generating inbox spam. The in-app notification (below) still fires
    # every time so the recipient gets a live indicator without inbox noise.
    skip_email = False
    try:
        _recent_dup = db.execute(text("""
            SELECT 1 FROM gig_messages
            WHERE gig_id = :gid
              AND sender_user_id = :uid
              AND id < :this_id
              AND is_read = 0
              AND created_at >= datetime('now', '-5 minutes')
            LIMIT 1
        """), {"gid": gig_id, "uid": user.id, "this_id": msg_id}).first()
        if _recent_dup:
            skip_email = True
            logger.info(f"Message {msg_id}: skipping email (coalesced — prior unread from same sender within 5 min)")
    except Exception:
        pass

    # Send email notification to the other party
    if not skip_email:
        try:
            _notify_other_party(db, gig_id, user.id, role, sender_name, body, sender_entity_id=notify_entity_id)
        except Exception as e:
            logger.warning(f"Message notification failed for gig {gig_id}: {e}")

    # Audit fix (May 2026 part 10): in-app notification on every send, fanned
    # out across entity_users. Previously only email fired — a user with
    # email muted got no live indicator beyond the 30s-polled badge.
    try:
        from backend.services.notification_service import create_notification
        from backend.utils import get_all_entity_users
        # Determine which entity should receive the notification
        if role == "venue":
            # Notify the target artist's users
            if target_artist_id:
                _recipients = get_all_entity_users(db, "artist", target_artist_id)
                _ctx_aid = target_artist_id
                _ctx_vid = entity_id
            else:
                _recipients, _ctx_aid, _ctx_vid = [], None, entity_id
        else:  # artist sender → venue users
            venue_row = db.execute(text(
                "SELECT v.id FROM venues v JOIN gigs g ON g.venue_id = v.id WHERE g.id = :gid"
            ), {"gid": gig_id}).first()
            _ctx_vid = venue_row[0] if venue_row else None
            _ctx_aid = entity_id
            _recipients = get_all_entity_users(db, "venue", _ctx_vid) if _ctx_vid else []

        _body_preview = (body or "")[:120] + ("…" if (body or "") and len(body) > 120 else "")
        for _u in _recipients:
            if _u["user_id"] == user.id:
                continue  # don't notify the sender themselves
            create_notification(db, _u["user_id"], "new_message",
                f"New message from {sender_name}",
                _body_preview, gig_id=gig_id, venue_id=_ctx_vid, artist_id=_ctx_aid)
        db.commit()
    except Exception as _ne:
        logger.warning(f"new-message in-app notification failed for gig {gig_id}: {_ne}")

    return {"ok": True, "message_id": msg_id}


# ── MARK THREAD AS READ ───────────────────────────────────────────────────────
@router.put("/api/gigs/{gig_id}/messages/read")
def mark_read(gig_id: int, last_message_id: int = None, artist_id: int = None,
              user=Depends(get_current_user), db=Depends(get_db)):
    """Mark messages in this gig thread as read for the current user.

    Audit fix (May 2026 part 10):
      - Optional `last_message_id` query param caps the UPDATE to messages
        with id <= last_message_id. Closes the race where a polling refresh
        at the same instant a new message lands silently marks the new
        message read before the user has seen it on screen.
      - Optional `artist_id` (for artists who own multiple acts) scopes
        the mark-read to one thread, so A1's mark-read doesn't clear A2's
        unread state on the same gig.
    """
    try:
        _ensure_gig_messages_table(db)
    except Exception as e:
        logger.error(f"mark_read ensure failed for gig {gig_id}: {e}")
    try:
        role, entity_id, _name = _get_user_role_for_gig(db, gig_id, user.id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"mark_read _get_user_role failed gig {gig_id} user {user.id}: {type(e).__name__}: {e}")
        raise HTTPException(500, f"Server error: {type(e).__name__}")

    # Determine which thread to mark — artists may own multiple acts on
    # one gig; default to the act the helper resolved, but accept an
    # explicit override that must be one of THEIR own artists.
    filter_eid = None
    if role == "artist":
        if artist_id:
            owned = db.execute(text("""
                SELECT 1 FROM artists a
                WHERE a.id = :aid AND (
                    a.user_id = :uid
                    OR EXISTS (SELECT 1 FROM entity_users eu
                               WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
                )
            """), {"aid": int(artist_id), "uid": user.id}).first()
            filter_eid = int(artist_id) if owned else entity_id
        else:
            filter_eid = entity_id

    params = {"gid": gig_id, "uid": user.id, "feid": filter_eid, "lid": last_message_id}
    db.execute(
        text("""
            UPDATE gig_messages
            SET is_read = 1
            WHERE gig_id = :gid AND sender_user_id != :uid AND is_read = 0
              AND (:lid IS NULL OR id <= :lid)
              AND (:feid IS NULL
                   OR sender_entity_id = :feid
                   OR (sender_type = 'venue' AND target_artist_id = :feid))
        """),
        params
    )
    db.commit()
    return {"ok": True}



# ── FULL INBOX (all messages across all gigs) ─────────────────────────────────
@router.get("/api/me/messages")
def get_inbox(artist_id: int = None, venue_id: int = None,
              include_hidden: int = 0,
              user=Depends(get_current_user), db=Depends(get_db)):
    """Returns all messages across all gigs the current user is party to, newest first.
    If artist_id is provided, only returns messages for gigs that specific artist is party to.

    Jul 2026: `include_hidden=1` opts out of the hide-for-me filter and
    additionally returns a boolean `is_hidden` per row so the UI can
    render a "restore" button on the ones the user previously trashed."""
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

    # Jul 21 2026: per-user "hide this thread from my inbox" table.
    # Stores (user_id, gig_id, artist_id, hidden_at). Threads are only
    # filtered out of the inbox when hidden_at >= latest_message_time —
    # a fresh incoming message re-surfaces the thread automatically.
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS gig_message_hides (
                user_id INTEGER NOT NULL,
                gig_id INTEGER NOT NULL,
                artist_id INTEGER NOT NULL,
                hidden_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, gig_id, artist_id)
            )
        """))
        db.commit()
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
                    -- Live sender name via the thread's venue/artist rows
                    -- (already joined below as v/a). Falls back to the
                    -- snapshot when sender_type is neither venue nor
                    -- artist (defensive — shouldn't happen in practice).
                    CASE
                      WHEN latest.sender_type = 'venue'  THEN v.venue_name
                      WHEN latest.sender_type = 'artist' THEN a.name
                      ELSE latest.sender_name
                    END as sender_name,
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
                    -- Audit fix (May 2026 part 10): exclude unread on cancelled gigs to
                    -- match the header-badge filter at /api/me/messages/unread-count;
                    -- otherwise the inbox sum ≠ the badge.
                    (SELECT COUNT(*) FROM gig_messages um
                     WHERE um.gig_id = tp.gig_id
                       AND um.sender_user_id != :uid
                       AND um.is_read = 0
                       AND (um.sender_entity_id = tp.artist_id
                            OR (um.sender_type='venue' AND (um.target_artist_id = tp.artist_id OR um.target_artist_id IS NULL)))
                       AND EXISTS (
                         SELECT 1 FROM gigs ug WHERE ug.id = um.gig_id AND ug.status NOT IN ('cancelled')
                       )
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
                      WHERE gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval') AND a2.user_id=:uid
                    UNION
                    SELECT gs.gig_id FROM gig_slots gs JOIN artists a2 ON a2.id=gs.artist_id
                      JOIN entity_users eu ON eu.entity_type='artist' AND eu.entity_id=a2.id
                      WHERE gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval') AND eu.user_id=:uid
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
                -- Hide-for-me filter: exclude threads this user has hidden
                -- IF no newer message has arrived since they hid it.
                -- Bypassed when `include_hidden=1` (called by the inbox
                -- "Show hidden" toggle so the user can restore rows).
                AND (:include_hidden = 1 OR NOT EXISTS (
                    SELECT 1 FROM gig_message_hides h
                     WHERE h.user_id = :uid
                       AND h.gig_id = tp.gig_id
                       AND h.artist_id = tp.artist_id
                       AND h.hidden_at >= latest.created_at
                ))
                ORDER BY latest.created_at DESC
                LIMIT 200
            """),
            {"uid": user.id, "artist_id": artist_id, "venue_id": venue_id,
             "include_hidden": 1 if include_hidden else 0}
        ).mappings().all()
        results = [dict(r) for r in rows]

        # When include_hidden=1 the frontend also needs to know WHICH
        # rows are hidden so it can render a "restore" button instead
        # of the trash. Cheap lookup — one query for the user's hide
        # records, then flag each result row in a single pass.
        if include_hidden and results:
            try:
                hidden_rows = db.execute(text("""
                    SELECT gig_id, artist_id, hidden_at
                      FROM gig_message_hides
                     WHERE user_id = :uid
                """), {"uid": user.id}).mappings().all()
                hide_map = {(int(h["gig_id"]), int(h["artist_id"])): h["hidden_at"]
                            for h in hidden_rows}
                for r in results:
                    h_at = hide_map.get((int(r.get("gig_id") or 0),
                                          int(r.get("artist_id") or 0)))
                    # A row is only "still hidden" if the latest message
                    # is not newer than hidden_at — otherwise a new
                    # message re-surfaced it and the flag is stale.
                    r["is_hidden"] = bool(
                        h_at and str(h_at) >= str(r.get("created_at") or '')
                    )
            except Exception as _he:
                logger.warning(f"is_hidden flag lookup failed: {_he}")

        return results
    except Exception as e:
        logger.error(f"get_inbox error: {e}")
        return []


# ── HIDE-FOR-ME THREAD DELETE ────────────────────────────────────────────────
# Jul 21 2026: per-user "delete this thread from my inbox" action. Does
# NOT hard-delete the messages (the counterparty still sees them) —
# instead upserts a row in gig_message_hides so this user's inbox query
# filters the thread out until a new message arrives.

@router.delete("/api/me/messages/threads/{gig_id}/{artist_id}")
def hide_thread(gig_id: int, artist_id: int,
                user=Depends(get_current_user), db=Depends(get_db)):
    """Hide a gig-thread row from this user's inbox. Idempotent —
    re-hiding an already-hidden thread just refreshes hidden_at so a
    thread that had re-surfaced after a new message can be hidden again."""
    _ensure_gig_messages_table(db)
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS gig_message_hides (
                user_id INTEGER NOT NULL,
                gig_id INTEGER NOT NULL,
                artist_id INTEGER NOT NULL,
                hidden_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, gig_id, artist_id)
            )
        """))
        db.commit()
    except Exception:
        pass
    # Upsert: refresh hidden_at whether or not a row already exists.
    # DELETE-then-INSERT keeps the SQL portable across sqlite + postgres
    # (avoids ON CONFLICT dialect differences).
    db.execute(text("""
        DELETE FROM gig_message_hides
         WHERE user_id = :uid AND gig_id = :gid AND artist_id = :aid
    """), {"uid": user.id, "gid": gig_id, "aid": artist_id})
    db.execute(text("""
        INSERT INTO gig_message_hides (user_id, gig_id, artist_id, hidden_at)
        VALUES (:uid, :gid, :aid, CURRENT_TIMESTAMP)
    """), {"uid": user.id, "gid": gig_id, "aid": artist_id})
    db.commit()
    return {"ok": True}


@router.post("/api/me/messages/threads/{gig_id}/{artist_id}/restore")
def restore_thread(gig_id: int, artist_id: int,
                   user=Depends(get_current_user), db=Depends(get_db)):
    """Un-hide a previously hidden thread from this user's inbox.
    Deletes the row in `gig_message_hides` so the inbox query stops
    filtering it. Idempotent — a no-op if the thread was never hidden."""
    try:
        db.execute(text("""
            DELETE FROM gig_message_hides
             WHERE user_id = :uid AND gig_id = :gid AND artist_id = :aid
        """), {"uid": user.id, "gid": gig_id, "aid": artist_id})
        db.commit()
    except Exception:
        pass
    return {"ok": True}


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
                WHERE gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval') AND a.user_id=:uid
                UNION
                SELECT gs.gig_id FROM gig_slots gs JOIN artists a ON a.id=gs.artist_id
                  JOIN entity_users eu ON eu.entity_type='artist' AND eu.entity_id=a.id
                WHERE gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval') AND eu.user_id=:uid
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

    # Audit fix (May 2026 part 9): exclude gigs that have been cancelled or
    # deleted entirely from the unread badge — they stick around with stale
    # messages because cleanup_gig_records only DELETEs gig_messages on full
    # gig deletion; cancellation paths that reopen the gig keep the thread.
    # The badge should reflect actionable conversations only.
    count = db.execute(
        text(f"""
            SELECT COUNT(*) FROM gig_messages gm
            JOIN gigs g ON g.id = gm.gig_id
            WHERE gm.gig_id IN ({placeholders})
              AND gm.sender_user_id != :uid
              AND gm.is_read = 0
              AND g.status NOT IN ('cancelled')
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
                    LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
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


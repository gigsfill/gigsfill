"""Gig Hold feature — venue-initiated private offer cycle at gig creation.

Sibling to the existing post-cancellation waitlist (services/scheduler.py
flow). Shares the gig_waitlist table via the `source` column:
  - source='cancellation' (default, legacy) — fires when a booked
    artist cancels, finds backfill from preferred + radius
  - source='hold' (new) — venue picks N artists at gig creation,
    sequentially offers the gig 24h each (configurable). Reuses
    the same scheduler advancement logic.

State on gigs:
  gigs.status         stays 'open' throughout (so the normal booking
                      flow + contract flow + transaction flow still
                      apply on acceptance — no parallel code paths)
  gigs.hold_status    'active'    : currently in offer cycle
                      'exhausted' : everyone declined, venue must
                                    Cancel or Open
                      NULL        : not a held gig (or hold cleared)

Visibility rule: an artist searching for open gigs sees the gig
exactly when status='open' AND hold_status IS NULL. Active+exhausted
holds are hidden until the venue resolves them (or the current artist
in the cycle accepts).

The held artist's "Pending Offers" view (Phase 3) is keyed off the
gig_waitlist row where source='hold' AND offer_sent_at IS NOT NULL
AND offer_expires_at > now AND offer_declined = 0.
"""
import logging
import secrets
from typing import Optional

logger = logging.getLogger("gigsfill.gig_hold")


def create_hold_waitlist(db, gig_id: int, artist_ids: list, send_email_now: bool,
                         offer_window_hours: int = 24):
    """Called from create_gig after the gig row + slot rows are inserted.

    artist_ids: ordered list of preferred-artist ids (length 1 = single-
                artist hold; length N = waitlist). Position 0 = first
                offer.
    send_email_now: if True, send the first offer immediately. If False,
                    rows exist but nobody has been notified yet (the
                    venue can manually trigger later via 'Send offer'
                    button in Phase 4).
    offer_window_hours: 24 default. Per-gig override at creation time.
    """
    from sqlalchemy import text
    if not artist_ids:
        return
    # Mark the gig as a held gig. Status stays 'open' so the booking
    # flow + contract flow work unchanged on acceptance.
    db.execute(
        text("""UPDATE gigs SET hold_status = 'active',
                                hold_offer_window_hours = :hrs,
                                hold_email_artists = :em,
                                hold_created_at = CURRENT_TIMESTAMP
                WHERE id = :gid"""),
        {"gid": gig_id, "hrs": int(offer_window_hours), "em": 1 if send_email_now else 0},
    )
    # Insert waitlist rows in venue-chosen order. UNIQUE(gig_id, artist_id)
    # prevents dupes; if venue listed the same artist twice we keep the
    # first position.
    for i, aid in enumerate(artist_ids):
        try:
            db.execute(
                text("""INSERT INTO gig_waitlist
                          (gig_id, artist_id, source, position, notified)
                        VALUES (:gid, :aid, 'hold', :pos, 0)"""),
                {"gid": gig_id, "aid": int(aid), "pos": i},
            )
        except Exception as e:
            # UNIQUE violation on duplicate — skip and continue
            logger.warning(f"[HOLD] skipped duplicate artist={aid} on gig={gig_id}: {e}")
    db.commit()

    # If venue checked "Email Artist?" → fire the first offer now.
    # Otherwise the rows just sit there waiting for manual trigger
    # (Phase 4 will add the UI to fire them).
    if send_email_now:
        send_next_hold_offer(db, gig_id)


def send_next_hold_offer(db, gig_id: int) -> Optional[int]:
    """Find the next hold-waitlist row with no offer in flight, mark
    it offered (token + expires_at), and (Phase 2) email the artist.

    Returns the artist_id offered, or None if waitlist is exhausted.
    """
    from sqlalchemy import text
    # Find the next position that hasn't been offered AND hasn't been declined
    row = db.execute(
        text("""SELECT id, artist_id, position FROM gig_waitlist
                WHERE gig_id = :gid
                  AND source = 'hold'
                  AND offer_sent = 0
                  AND offer_declined = 0
                ORDER BY position ASC LIMIT 1"""),
        {"gid": gig_id},
    ).mappings().first()
    if not row:
        # Nothing left — mark gig as exhausted, venue must decide
        db.execute(
            text("UPDATE gigs SET hold_status = 'exhausted' WHERE id = :gid"),
            {"gid": gig_id},
        )
        db.commit()
        logger.info(f"[HOLD] gig={gig_id} waitlist exhausted")
        # Phase 2 will fire the venue notification here
        return None
    # Get the offer window from the gig (set at create_hold_waitlist)
    win = db.execute(
        text("SELECT COALESCE(hold_offer_window_hours, 24) FROM gigs WHERE id = :gid"),
        {"gid": gig_id},
    ).scalar()
    token = secrets.token_urlsafe(24)
    db.execute(
        text("""UPDATE gig_waitlist
                SET offer_sent = 1,
                    offer_sent_at = CURRENT_TIMESTAMP,
                    offer_expires_at = datetime('now', '+' || :hrs || ' hours'),
                    offer_token = :tok,
                    notified = 1,
                    notified_at = CURRENT_TIMESTAMP
                WHERE id = :id"""),
        {"id": row["id"], "tok": token, "hrs": int(win or 24)},
    )
    db.commit()
    logger.info(f"[HOLD] gig={gig_id} offered to artist={row['artist_id']} (position={row['position']})")
    # Phase 2 will send the email here
    return int(row["artist_id"])

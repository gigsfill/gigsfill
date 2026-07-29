"""
Shared entity-deletion pipeline.

Used by:
  - `DELETE /api/me/delete`     (delete-account flow, deletes user + owned entities)
  - `DELETE /api/artists/{id}`  (standalone: owner keeps their user account)
  - `DELETE /api/venues/{id}`   (same)

Ownership authorization, refund-guard, booked-gig cancellation + emails, and
the full child-table cascade all live here so the flows can't diverge.

Callers must have already checked that `user_id` matches the OWNER of the
entity — every function below raises `HTTPException(403, "NOT_YOUR_...")` if
the ownership predicate doesn't hold, and no cleanup runs unless it does.
"""
import os
import shutil
import logging
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text

from backend.utils import utcnow_naive
from backend.services.email_dispatch import format_email_date

logger = logging.getLogger("gigsfill.entity_delete")


def _rm_tree(path_str: str) -> None:
    """rm -rf that logs but doesn't raise.

    Deletion cascades run inside a DB transaction, and we don't want a
    permission error on the filesystem side to roll back a valid entity
    wipe. The disk state converges eventually; the DB is the source of
    truth.
    """
    p = Path(path_str)
    if not p.exists():
        return
    try:
        shutil.rmtree(p)
    except Exception as e:
        logger.warning(f"[ENT_DEL] rmtree failed for {path_str}: {e}")


# States considered "in-flight" for the booked-gig cancellation cascade.
_IN_FLIGHT_GIG_STATUSES = (
    'booked', 'awaiting_venue_contract', 'pending_contract', 'pending_venue_approval'
)

# Transaction states that are TRULY live — money is committed or moving right
# now, and the entity can't be deleted until they resolve. Settled states
# (`paid`, `transferred`, `dispute_won`, `dispute_lost`) do NOT block: the
# gig is over, money is where it's supposed to be, keeping the transactions
# is just audit history. Cancellable pre-charge states (`scheduled`,
# `charge_retry`) are handled downstream — the cleanup marks them
# `account_deleted` so the payout scheduler skips them.
#
# - `processing`: Stripe is actively processing the charge (very brief window)
# - `charged`: venue's card was charged, artist NOT yet paid
# - `pending_transfer`: transfer to artist is queued/awaiting retry
# - `transfer_failed`: transfer was rejected by Stripe — needs manual retry
# - `disputed`: an active Stripe dispute is open on the venue charge
_LIVE_TXN_STATUSES = (
    'processing', 'charged', 'pending_transfer', 'transfer_failed', 'disputed'
)


# ─────────────────────────────────────────────────────────────────────────────
# Ownership + team-member helpers
# ─────────────────────────────────────────────────────────────────────────────

def assert_owner(db, etype: str, eid: int, user_id: int) -> None:
    """Raise 403 unless `user_id` is the OWNER of a LIVE entity.

    The initial creator of an artist/venue is recorded on the entity row
    itself (`artists.user_id` / `venues.user_id`). Team members added via
    `entity_users` are NOT owners — they have delegated access only.

    Also refuses tombstoned entities (`deleted_at IS NOT NULL`) with 410 so
    a double-delete request doesn't re-tombstone or accidentally strip the
    "[Deleted]" prefix.
    """
    tbl = "artists" if etype == "artist" else "venues"
    row = db.execute(
        text(f"SELECT deleted_at FROM {tbl} WHERE id = :eid AND user_id = :uid"),
        {"eid": eid, "uid": user_id}
    ).mappings().first()
    if not row:
        raise HTTPException(
            403,
            f"NOT_YOUR_{etype.upper()}: Only the original owner of this "
            f"{etype} can delete it. If you were given access as a team "
            f"member, ask the owner to delete or transfer it."
        )
    if row.get("deleted_at"):
        raise HTTPException(
            410,
            f"ALREADY_DELETED: This {etype} has already been deleted."
        )


def count_other_team_members(db, etype: str, eid: int, user_id: int) -> int:
    """Count team members OTHER than the given user for this entity.

    Team members live in `entity_users`. The owner is NOT required to have
    a row there (their ownership is on the entity row itself), so a return
    value of 0 means "no other humans use this entity" — which is what the
    "auto-delete solo entities on user-account delete" rule keys off.
    """
    row = db.execute(
        text("""
            SELECT COUNT(*) FROM entity_users
            WHERE entity_type = :et AND entity_id = :eid AND user_id != :uid
        """),
        {"et": etype, "eid": eid, "uid": user_id}
    ).first()
    return int(row[0]) if row else 0


def list_live_transactions(db, etype: str, eid: int) -> list:
    """Return a summary of transactions that would BLOCK deletion of this
    entity — the same states `assert_no_live_transactions` refuses on.

    Used by the frontend delete-preview endpoints so the modal can explain
    the blocker upfront ("You have 2 gigs mid-payment: Jul 15 with X, Aug 3
    with Y — contact support to resolve them before deleting") rather than
    forcing the user to click Delete just to get a 409.
    """
    placeholders = ", ".join(f"'{s}'" for s in _LIVE_TXN_STATUSES)
    if etype == "venue":
        rows = db.execute(
            text(f"""
                SELECT t.id, t.status, t.artist_id, t.gig_id,
                       g.date, a.name as artist_name
                FROM transactions t
                JOIN gigs g ON g.id = t.gig_id
                LEFT JOIN artists a ON a.id = t.artist_id
                WHERE g.venue_id = :eid AND t.status IN ({placeholders})
                ORDER BY g.date ASC
            """),
            {"eid": eid}
        ).mappings().all()
    else:  # artist
        rows = db.execute(
            text(f"""
                SELECT t.id, t.status, t.gig_id,
                       g.date, g.venue_id, v.venue_name
                FROM transactions t
                JOIN gigs g ON g.id = t.gig_id
                LEFT JOIN venues v ON v.id = g.venue_id
                WHERE t.artist_id = :eid AND t.status IN ({placeholders})
                ORDER BY g.date ASC
            """),
            {"eid": eid}
        ).mappings().all()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Refund / charged-transaction guard
# ─────────────────────────────────────────────────────────────────────────────

def assert_no_live_transactions(db, etype: str, eid: int) -> None:
    """Raise 409 if any transaction for this entity is TRULY live/mid-flight
    (money committed but not yet settled).

    Settled transactions (`paid`, `transferred`, `dispute_won`, `dispute_lost`)
    from past gigs don't block deletion — they're just audit history and the
    money is where it's supposed to be. This differs from the historical
    guard which blocked on any charged/paid state; the settled cases are
    fine to keep as-is in the transactions table (they'll remain there for
    reporting even after the entity row is gone).
    """
    placeholders = ", ".join(f"'{s}'" for s in _LIVE_TXN_STATUSES)
    if etype == "venue":
        stuck = db.execute(
            text(f"""
                SELECT t.id, t.status
                FROM transactions t
                JOIN gigs g ON g.id = t.gig_id
                WHERE g.venue_id = :eid AND t.status IN ({placeholders})
                LIMIT 1
            """),
            {"eid": eid}
        ).mappings().first()
    else:  # artist
        stuck = db.execute(
            text(f"""
                SELECT id, status FROM transactions
                WHERE artist_id = :eid AND status IN ({placeholders})
                LIMIT 1
            """),
            {"eid": eid}
        ).mappings().first()
    if stuck:
        # Human-friendly explanation. All of these transition to a settled
        # state on their own once the payout scheduler finishes them (day
        # after each gig), so the correct advice is "wait, then retry" —
        # NOT "contact support" (there's no self-service refund UI a venue
        # can use, and steering users to admin dilutes what admin actually
        # gets escalated).
        friendly = {
            'processing':       "a charge is currently being processed by Stripe",
            'charged':          "a charge has been made to the venue's card but the artist hasn't been paid yet",
            'pending_transfer': "an artist payout is queued and hasn't been sent yet",
            'transfer_failed':  "an artist payout is being retried",
            'disputed':         "a Stripe dispute is currently open on a charge",
        }
        why = friendly.get(stuck['status'], f"a transaction is in '{stuck['status']}' state")
        raise HTTPException(
            409,
            f"LIVE_TRANSACTION_EXISTS: Can't delete this {etype} yet — {why}. "
            f"These clear on their own once each gig's payout completes; "
            f"try again once they've all settled."
        )


# Kept as an alias for any older imports; delete once nothing calls it.
assert_no_charged_transactions = assert_no_live_transactions


# ─────────────────────────────────────────────────────────────────────────────
# Booked-gig cancellation + counterparty notifications
# ─────────────────────────────────────────────────────────────────────────────

def _notify_cancelled_gigs_for_artist(db, artist_id: int) -> None:
    """Notify venues whose FUTURE booked gigs the artist was on.

    Called BEFORE the tombstone so joins to the artist row still resolve.
    Failures are swallowed — email is non-critical, the tombstone should
    complete regardless.

    Bug fix (Jul 7 2026): previously flagged ANY gig with status='booked'
    regardless of date, which mis-notified venues about PAST already-played
    gigs (venues in prod have plenty of past bookings that never got status-
    transitioned to `completed`). Also missing `slot_times` and
    `waitlist_message` template variables, which rendered as literal
    `{{slot_times}}` placeholders in the email body. Fixed both.
    """
    try:
        from backend.email_service import EmailService
        from backend.services.notification_service import create_notification
        from backend.services.email_dispatch import compute_slot_times
        email_service = EmailService(db)

        artist_name = db.execute(
            text("SELECT name FROM artists WHERE id = :aid"),
            {"aid": artist_id}
        ).scalar() or ""

        # Distinct gigs so a multi-slot booking (2 slots one artist) sends 1 email.
        # AND g.date >= today: only FUTURE gigs actually get cancelled by the
        # tombstone. Past gigs happened, the venue was paid, they don't need
        # a cancellation notice today.
        booked = db.execute(text("""
            SELECT DISTINCT g.id, g.date, g.venue_id, g.title,
                   v.venue_name, v.user_id as venue_user_id,
                   u_venue.email as venue_email
            FROM gigs g
            JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
                             AND gs.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
            LEFT JOIN venues v ON g.venue_id = v.id
            LEFT JOIN users u_venue ON v.user_id = u_venue.id
            WHERE g.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
              AND g.date >= DATE('now', 'localtime')
        """), {"aid": artist_id}).mappings().all()

        for gig in booked:
            # Per-artist slot times for THIS gig — computes "8:00 PM - 10:00 PM"
            # or a `|`-joined list for multi-slot gigs. Fallback to gig-level
            # times if the compute helper 500s for any reason.
            try:
                slot_times_str = compute_slot_times(db, gig['id'], artist_id=artist_id) or ""
            except Exception:
                slot_times_str = ""
            if gig.get("venue_email"):
                try:
                    email_service.send_notification_email(
                        user_email=gig["venue_email"],
                        user_id=gig["venue_user_id"],
                        notification_type='venue_gig_cancelled',
                        variables={
                            'user_name':    gig['venue_name'],
                            'venue_name':   gig['venue_name'],
                            'artist_name':  artist_name or 'An artist',
                            'artist_id':    str(artist_id),
                            'venue_id':     str(gig['venue_id']),
                            'date':         format_email_date(gig['date']),
                            'title':        gig.get('title') or '',
                            'slot_times':   slot_times_str,
                            # The venue_gig_cancelled template still references
                            # {{waitlist_message}} (a legacy variable from the
                            # normal cancel path). Blank string collapses the
                            # placeholder cleanly.
                            'waitlist_message': '',
                            'cancellation_reason': 'Artist account has been deleted',
                        }
                    )
                except Exception as _me:
                    logger.warning(f"[ENT_DEL] artist email skip gig={gig['id']}: {_me}")
                try:
                    create_notification(
                        db, gig["venue_user_id"],
                        "gig_cancelled", "Gig Cancelled",
                        f"Your gig on {gig['date']} with {artist_name or 'an artist'} has been cancelled (artist account deleted).",
                        gig_id=gig['id'], venue_id=gig['venue_id'], artist_id=artist_id,
                        cancellation_reason="Artist account deleted"
                    )
                except Exception as _ne:
                    logger.warning(f"[ENT_DEL] artist notify skip gig={gig['id']}: {_ne}")
    except Exception as e:
        logger.warning(f"[ENT_DEL] artist cancel-notify outer error: {e}")


def _notify_cancelled_gigs_for_venue(db, venue_id: int) -> None:
    """Notify artists booked at this venue on FUTURE in-flight gigs.

    Same fixes as _notify_cancelled_gigs_for_artist (Jul 7 2026): future-
    date filter + slot_times + waitlist_message variables.
    """
    try:
        from backend.email_service import EmailService
        from backend.services.notification_service import create_notification
        from backend.services.email_dispatch import compute_slot_times
        email_service = EmailService(db)

        venue_name = db.execute(
            text("SELECT venue_name FROM venues WHERE id = :vid"),
            {"vid": venue_id}
        ).scalar() or "the venue"

        booked = db.execute(text("""
            SELECT DISTINCT g.id, g.date, g.title, gs.artist_id,
                   a.name as artist_name, a.user_id as artist_user_id,
                   u_artist.email as artist_email
            FROM gigs g
            JOIN gig_slots gs ON gs.gig_id = g.id
                             AND gs.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
            LEFT JOIN artists a ON a.id = gs.artist_id
            LEFT JOIN users u_artist ON a.user_id = u_artist.id
            WHERE g.venue_id = :vid
              AND g.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
              AND g.date >= DATE('now', 'localtime')
        """), {"vid": venue_id}).mappings().all()

        for gig in booked:
            try:
                slot_times_str = compute_slot_times(db, gig['id'], artist_id=gig.get('artist_id')) or ""
            except Exception:
                slot_times_str = ""
            if gig.get("artist_email"):
                try:
                    email_service.send_notification_email(
                        user_email=gig["artist_email"],
                        user_id=gig["artist_user_id"],
                        notification_type='artist_gig_cancelled',
                        variables={
                            'user_name':    gig.get('artist_name', 'Artist'),
                            'venue_name':   venue_name,
                            'artist_name':  gig.get('artist_name', 'Artist'),
                            'artist_id':    str(gig.get('artist_id', '')),
                            'venue_id':     str(venue_id),
                            'date':         format_email_date(gig['date']),
                            'title':        gig.get('title') or '',
                            'slot_times':   slot_times_str,
                            'waitlist_message': '',
                            'cancellation_reason': 'Venue account has been deleted',
                        }
                    )
                except Exception as _me:
                    logger.warning(f"[ENT_DEL] venue email skip gig={gig['id']}: {_me}")
                try:
                    create_notification(
                        db, gig["artist_user_id"],
                        "gig_cancelled", "Gig Cancelled",
                        f"Your gig on {gig['date']} at {venue_name} has been cancelled (venue account deleted).",
                        gig_id=gig['id'], venue_id=venue_id, artist_id=gig.get('artist_id'),
                        cancellation_reason="Venue account deleted"
                    )
                except Exception as _ne:
                    logger.warning(f"[ENT_DEL] venue notify skip gig={gig['id']}: {_ne}")
    except Exception as e:
        logger.warning(f"[ENT_DEL] venue cancel-notify outer error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Full data cascade + entity row delete
# ─────────────────────────────────────────────────────────────────────────────

def delete_artist(db, artist_id: int, user_id: int) -> None:
    """Owner-authorized SOFT delete (tombstone) of an artist.

    History-preserving. The artist row survives with `deleted_at` set and
    a "[Deleted]" name prefix so every venue's payment history, every past
    review, and every completed gig keeps rendering correctly. Nothing
    active gets left behind:

      1. assert owner
      2. assert no live transactions (409 — see assert_no_live_transactions)
      3. notify counter-parties on all in-flight booked gigs
      4. mark in-flight transactions as `account_deleted` (payout scheduler
         skips them; settled txns are preserved as audit history)
      5. release the artist's grip on FUTURE in-flight gig slots to 'open'
         so venues can rebook. PAST slots keep their `artist_id` so history
         shows the (tombstoned) artist correctly.
      6. cancel in-flight contracts; leave signed/completed contracts alone
      7. delete OWNER-PRIVATE data: preferred_artists relationships (venue
         won't want a deleted artist in their preferred list), media
         uploads on disk (files), entity_users memberships, invitations,
         payment settings, vanity URLs, in-flight waitlist rows.
      8. LEAVE historical rows in place: past gig_slots artist_id links,
         reviews (both directions), settled transactions, past gig_messages.
      9. tombstone the artist row: prepend "[Deleted] " to the name, set
         `deleted_at` to now. Owner user_id is left as-is (dangling FK is
         fine — the account-delete cascade nulls it via users-table wipe
         only if the user is going away too).

    Caller is responsible for `db.commit()`.
    """
    assert_owner(db, "artist", artist_id, user_id)
    assert_no_live_transactions(db, "artist", artist_id)

    _notify_cancelled_gigs_for_artist(db, artist_id)

    # Preserve audit trail: mark still-pending transactions as `account_deleted`
    # rather than deleting them. Settled `paid`/`transferred` rows keep their
    # status — the artist's tombstoned name will render fine in venue history.
    db.execute(text("""
        UPDATE transactions SET status = 'account_deleted',
            notes = COALESCE(notes, '') || ' [Artist account deleted]'
        WHERE artist_id = :aid
          AND status IN ('scheduled', 'charge_retry',
                         'pending_transfer', 'transfer_failed')
    """), {"aid": artist_id})

    # Release IN-FLIGHT slots so venues can rebook. Past slots (`booked` on
    # a gig where g.date < today, or slots in any terminal state) stay
    # attached — that's the artist's completed-gig history.
    db.execute(text("""
        UPDATE gigs SET status = 'open', artist_id = NULL,
            contract_hold_artist_id = NULL, contract_hold_expires_at = NULL
        WHERE artist_id = :aid
          AND status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
          AND date >= DATE('now', 'localtime')
    """), {"aid": artist_id})
    db.execute(text("""
        UPDATE gig_slots SET status = 'open', artist_id = NULL
        WHERE artist_id = :aid
          AND status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
          AND gig_id IN (SELECT id FROM gigs WHERE date >= DATE('now', 'localtime'))
    """), {"aid": artist_id})

    # In-flight contracts get cancelled. Signed/completed contracts stay so
    # the audit trail for past events is intact.
    try:
        db.execute(text("""
            UPDATE gig_contracts SET status = 'cancelled'
            WHERE artist_id = :aid AND status IN ('pending','awaiting_venue_upload','artist_signed')
        """), {"aid": artist_id})
    except Exception:
        pass

    # BUG FIX (Jul 2026 audit): normalize parent gigs.status for gigs where
    # the artist held the last active slot. book_gig flips gigs.status to
    # 'booked' when the LAST slot books; releasing that slot in the loop
    # above doesn't roll status back → the gig would linger as 'booked'
    # with zero booked slots, hidden from public search but still showing
    # in venue "booked gigs" lists. Reset to 'open' for any gig with no
    # remaining booked/pending slots.
    try:
        db.execute(text("""
            UPDATE gigs SET status = 'open',
                            artist_id = NULL,
                            contract_hold_artist_id = NULL,
                            contract_hold_expires_at = NULL
            WHERE status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
              AND NOT EXISTS (
                SELECT 1 FROM gig_slots gs
                WHERE gs.gig_id = gigs.id
                  AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
              )
        """))
    except Exception as _norm_e:
        logger.warning(f"[ENT_DEL] gig-status normalize skip: {_norm_e}")

    # Owner-private / active-relationship wipes. History-preserving rows
    # (reviews, past waitlist entries, past gig_messages) are NOT touched.
    # Log every raw DELETE against preferred_artists so we can trace
    # missing rows post-hoc (Jul 2026 audit — one row went missing with
    # no reconstructable cause; this is the last defense line).
    try:
        _pa_gone = db.execute(
            text("SELECT COUNT(*) FROM preferred_artists WHERE artist_id = :aid"),
            {"aid": artist_id}
        ).scalar() or 0
        if _pa_gone:
            logger.info(f"[ENT_DEL] artist={artist_id} → deleting {_pa_gone} preferred_artists row(s)")
    except Exception:
        pass
    db.execute(text("DELETE FROM preferred_artists WHERE artist_id = :aid"), {"aid": artist_id})
    db.execute(text("DELETE FROM artist_media WHERE artist_id = :aid"), {"aid": artist_id})
    db.execute(text("DELETE FROM entity_users WHERE entity_type = 'artist' AND entity_id = :aid"), {"aid": artist_id})
    db.execute(text("DELETE FROM entity_invitations WHERE entity_type = 'artist' AND entity_id = :aid"), {"aid": artist_id})
    db.execute(text("DELETE FROM entity_payment_settings WHERE entity_type = 'artist' AND entity_id = :aid"), {"aid": artist_id})
    # BUG FIX (Jul 2026 audit): wipe W-9 (encrypted TIN, legal name, mailing
    # address) — was previously left indefinitely, a real PII data-retention
    # leak. Reviews stay (see docstring — history preservation).
    try:
        db.execute(text("DELETE FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :aid"), {"aid": artist_id})
    except Exception:
        pass
    # Only drop UNNOTIFIED waitlist rows (still awaiting an offer). Notified
    # ones are historical breadcrumbs of "this artist was offered gig X".
    db.execute(text("DELETE FROM gig_waitlist WHERE artist_id = :aid AND (notified = 0 OR notified IS NULL)"), {"aid": artist_id})
    try:
        db.execute(text("DELETE FROM waitlist_offered WHERE artist_id = :aid"), {"aid": artist_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM vanity_urls WHERE entity_type='artist' AND entity_id = :aid"), {"aid": artist_id})
    except Exception:
        pass
    # Jul 2026 delete-audit sweep: additional tables the delete-artist
    # flow was silently orphaning. Each guarded try/except so a table
    # that doesn't exist on an older DB doesn't break the whole cascade.
    for _sql in (
        # connect_account_health: keeps a stripe_connect_account_id
        # (real Stripe account handle) — leaving it lets the payout
        # scheduler poll a ghost account indefinitely.
        "DELETE FROM connect_account_health WHERE artist_id = :aid",
        # digest queue: pending open-gig digest rows for a tombstoned
        # artist would re-send referencing a "[Deleted]" name.
        "DELETE FROM artist_email_digest_queue WHERE artist_id = :aid",
        # blackouts: owner-private availability windows.
        "DELETE FROM artist_availability WHERE artist_id = :aid",
        # bans: purge this artist's row on every venue's ban list.
        "DELETE FROM venue_artist_bans WHERE artist_id = :aid",
        # artist-owned flyers.
        "DELETE FROM flyers WHERE artist_id = :aid",
        # owner-private per-venue notes.
        "DELETE FROM artist_gig_notes WHERE artist_id = :aid",
        # invalidate any pending review-link tokens tied to this artist.
        "DELETE FROM review_link_tokens WHERE artist_id = :aid",
    ):
        try:
            db.execute(text(_sql), {"aid": artist_id})
        except Exception as _e:
            logger.info(f"[ENT_DEL] artist={artist_id} cascade skip ({_sql[:60]}...): {_e}")

    # Tombstone the artist row. Ownership is re-asserted in the WHERE so a
    # racing call from another session that lost ownership between our
    # assert_owner check and here still can't tombstone this row.
    #
    # 2026-07-26: also NULL out user_id at tombstone time. Previous version
    # left the FK pointing at the deleting user, which then blocked their
    # DELETE FROM users with a FOREIGN KEY constraint failed. The tombstoned
    # row is orphan-by-design (history preservation), so releasing the
    # owner FK is safe and matches the intent of the "dangling FK is
    # intentional" comment in the docstring — now made a real NULL instead
    # of an FK-violating dangle. Requires artists.user_id to be nullable
    # (schema migration also applied 2026-07-26).
    db.execute(text("""
        UPDATE artists
        SET deleted_at = CURRENT_TIMESTAMP,
            user_id = NULL,
            name = CASE
                     WHEN name LIKE '[Deleted]%' THEN name
                     ELSE '[Deleted] ' || name
                   END
        WHERE id = :aid AND user_id = :uid
    """), {"aid": artist_id, "uid": user_id})

    # Audit trail — this is a destructive, cascading operation. Log the
    # tombstone at admin_audit_log level so we can see who tombstoned
    # which artist, when, without having to reconstruct from journalctl.
    # Best-effort — the operation must not fail if audit write fails.
    try:
        from backend.utils import log_admin_action
        class _ActorShim:
            id = user_id
            email = None
        log_admin_action(
            db, _ActorShim(), "entity_tombstone_artist",
            target_table="artists", target_id=artist_id,
        )
    except Exception:
        pass

    # Filesystem cleanup. Deferred to the route wrapper — running _rm_tree
    # here means a commit-failure below would leave the entity live but
    # media gone. Return the list of dirs so the caller can rmtree AFTER
    # a successful commit.
    return [
        f"app/static/uploads/artist/{artist_id}",
        f"media/artists/{artist_id}",
    ]


def delete_venue(db, venue_id: int, user_id: int) -> None:
    """Owner-authorized SOFT delete (tombstone) of a venue.

    History-preserving. Every artist who played a gig at this venue keeps
    seeing it in their past-gigs list — the venue name just renders as
    "[Deleted] X". Payment history, reviews, and completed contracts all
    stay intact.

    Order:
      1. assert owner
      2. assert no live transactions (409 for mid-flight only)
      3. notify counter-parties on all in-flight booked gigs
      4. mark in-flight transactions as `account_deleted` (audit-preserved)
      5. DELETE future in-flight gigs (they're cancelled; the venue is gone)
         and the child rows attached to those FUTURE gigs (slots, contracts,
         waitlist, email logs). Past gigs and their children are LEFT
         intact so artist history stays whole.
      6. wipe owner-private / active-relationship data: preferred_artists,
         venue_media (upload table), entity_users, invitations, email
         notification prefs, payment settings/overrides, vanity_urls,
         email history. Past reviews (both directions), settled
         transactions, and past gig_slots are PRESERVED.
      7. tombstone the venue row: "[Deleted] " prefix on venue_name,
         `deleted_at = now`.

    Caller is responsible for `db.commit()`.
    """
    assert_owner(db, "venue", venue_id, user_id)
    assert_no_live_transactions(db, "venue", venue_id)

    # Snapshot FUTURE gig ids — those get deleted (cancelled). Signed
    # contract PDFs live per-gig on disk; we clean only the future ones,
    # past ones stay for the artist's records.
    _future_gig_ids = [
        row[0] for row in db.execute(
            text("SELECT id FROM gigs WHERE venue_id = :vid AND date >= DATE('now', 'localtime')"),
            {"vid": venue_id}
        ).fetchall()
    ]

    _notify_cancelled_gigs_for_venue(db, venue_id)

    # Mark in-flight transactions for audit trail.
    db.execute(text("""
        UPDATE transactions SET status = 'account_deleted',
            notes = COALESCE(notes, '') || ' [Venue account deleted]'
        WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid)
          AND status IN ('scheduled', 'charge_retry',
                         'pending_transfer', 'transfer_failed')
    """), {"vid": venue_id})

    # Delete FUTURE gigs' child rows, then the future gig rows themselves.
    # PAST gigs (and every child row attached to them) stay put — that's
    # the artist's completed-gig history at this venue.
    if _future_gig_ids:
        for stmt in (
            "DELETE FROM gig_messages          WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid AND date >= DATE('now','localtime'))",
            "DELETE FROM gig_slots             WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid AND date >= DATE('now','localtime'))",
            "DELETE FROM gig_contracts         WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid AND date >= DATE('now','localtime'))",
            "DELETE FROM gig_waitlist          WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid AND date >= DATE('now','localtime'))",
            "DELETE FROM gig_email_log         WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid AND date >= DATE('now','localtime'))",
            "DELETE FROM gig_cancelled_artists WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :vid AND date >= DATE('now','localtime'))",
        ):
            try:
                db.execute(text(stmt), {"vid": venue_id})
            except Exception as _e:
                logger.warning(f"[ENT_DEL] future-gig child cleanup skip ({stmt[:60]}): {_e}")
        db.execute(text("DELETE FROM gigs WHERE venue_id = :vid AND date >= DATE('now','localtime')"), {"vid": venue_id})

    # Owner-private / active-relationship wipes. Historical rows (reviews,
    # settled transactions, past gig_slots) are NOT touched.
    # Log every raw DELETE against preferred_artists so we can trace
    # missing rows post-hoc (Jul 2026 audit — same rationale as the
    # artist-side delete above).
    try:
        _pa_gone = db.execute(
            text("SELECT COUNT(*) FROM preferred_artists WHERE venue_id = :vid"),
            {"vid": venue_id}
        ).scalar() or 0
        if _pa_gone:
            logger.info(f"[ENT_DEL] venue={venue_id} → deleting {_pa_gone} preferred_artists row(s)")
    except Exception:
        pass
    db.execute(text("DELETE FROM preferred_artists WHERE venue_id = :vid"), {"vid": venue_id})
    db.execute(text("DELETE FROM venue_media WHERE venue_id = :vid"), {"vid": venue_id})
    db.execute(text("DELETE FROM entity_users WHERE entity_type = 'venue' AND entity_id = :vid"), {"vid": venue_id})
    db.execute(text("DELETE FROM entity_invitations WHERE entity_type = 'venue' AND entity_id = :vid"), {"vid": venue_id})
    db.execute(text("DELETE FROM venue_email_notifications WHERE venue_id = :vid"), {"vid": venue_id})
    try:
        db.execute(text("DELETE FROM venue_email_history WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM artist_invitations WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    db.execute(text("DELETE FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"), {"vid": venue_id})
    # BUG FIX (Jul 2026 audit): wipe W-9 + PRO licenses + other venue-scoped
    # owner-private tables that were missed. Reviews stay (history).
    try:
        db.execute(text("DELETE FROM w9_forms WHERE entity_type = 'venue' AND entity_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM pro_licenses WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM venue_tax_settings WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM venue_contracts WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM venue_gig_notes WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM gig_templates WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    try:
        db.execute(text("DELETE FROM flyers WHERE venue_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    db.execute(text("DELETE FROM venue_payment_overrides WHERE venue_id = :vid"), {"vid": venue_id})
    try:
        db.execute(text("DELETE FROM vanity_urls WHERE entity_type='venue' AND entity_id = :vid"), {"vid": venue_id})
    except Exception:
        pass
    # Jul 2026 delete-audit sweep: additional venue-scoped tables that
    # were silently orphaning. Each guarded so a missing table doesn't
    # break the whole cascade.
    for _sql in (
        # digest queue: rows keyed by venue_id would re-send digests
        # referencing a "[Deleted]" venue.
        "DELETE FROM artist_email_digest_queue WHERE venue_id = :vid",
        # this venue's ban list — the artists banned here shouldn't
        # carry the tombstone's ghost list forward.
        "DELETE FROM venue_artist_bans WHERE venue_id = :vid",
        # invalidate any pending review-link tokens tied to this venue.
        "DELETE FROM review_link_tokens WHERE venue_id = :vid",
    ):
        try:
            db.execute(text(_sql), {"vid": venue_id})
        except Exception as _e:
            logger.info(f"[ENT_DEL] venue={venue_id} cascade skip ({_sql[:60]}...): {_e}")

    # Tombstone the venue row. 2026-07-26: NULL user_id at tombstone
    # (same rationale as the artist tombstone above — releases the FK so
    # the deleting user's DELETE FROM users doesn't fail; requires
    # venues.user_id to be nullable via the matching schema migration).
    db.execute(text("""
        UPDATE venues
        SET deleted_at = CURRENT_TIMESTAMP,
            user_id = NULL,
            venue_name = CASE
                            WHEN venue_name LIKE '[Deleted]%' THEN venue_name
                            ELSE '[Deleted] ' || venue_name
                         END
        WHERE id = :vid AND user_id = :uid
    """), {"vid": venue_id, "uid": user_id})

    # Audit trail — matches the artist tombstone log above (Jul 2026).
    try:
        from backend.utils import log_admin_action
        class _ActorShim:
            id = user_id
            email = None
        log_admin_action(
            db, _ActorShim(), "entity_tombstone_venue",
            target_table="venues", target_id=venue_id,
        )
    except Exception:
        pass

    # Filesystem cleanup deferred to the route wrapper — running _rm_tree
    # here means a commit-failure below would leave the venue live but
    # media gone. Return the list of dirs so the caller can rmtree AFTER
    # a successful commit. Future-gig signed contract PDFs are included
    # so we don't leak signed docs for cancelled gigs.
    _paths = [
        f"app/static/uploads/venue/{venue_id}",
        f"app/static/uploads/flyers/{venue_id}",
        f"app/static/uploads/contracts/venue_{venue_id}",
        f"app/static/uploads/contracts/gig_contracts/venue_{venue_id}",
        f"media/venues/{venue_id}",
    ]
    for gid in _future_gig_ids:
        _paths.append(f"app/static/uploads/contracts/signed/gig_{gid}")
    return _paths

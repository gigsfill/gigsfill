"""
Gig Cleanup Service
====================
Single source of truth for cleaning up all related records when a gig/slot
is cancelled or deleted. Eliminates the 5+ copy-pasted cleanup blocks that
existed across cancel_gig, delete_gig, cancel_slot, and delete_gig_with_slots.
"""

import logging
import os
import shutil
from sqlalchemy import text

logger = logging.getLogger("gigsfill.services.cleanup")

# Notification types that are tied to contract/booking flow and should be
# removed when the booking is undone:
CONTRACT_NOTIFICATION_TYPES = (
    'contract_signed',
    'contract_countersign_needed',
    'contract_countersigned',
    'contract_artist_signed',
    'contract_pending',
    'gig_booked',
)

# Transaction statuses that mean real money has moved (or is in flight).
# Any cancel / delete that would discard a row in one of these statuses
# without first refunding/reversing through Stripe is a data-integrity
# bug — the audit trail of money already charged would simply vanish.
# Audit fix (May 2026 part 5): widen the tuple to include dispute and
# processing states. Without 'disputed' / 'dispute_won' / 'dispute_lost' /
# 'processing', a gig with an open chargeback or in-flight payment intent
# would pass the assert, then delete_gig_with_slots' bulk
# `DELETE FROM transactions WHERE gig_id=:gid` (gigs.py:4783) would wipe the
# dispute audit trail and the in-flight PI row.
CHARGED_TRANSACTION_STATUSES = (
    'charged', 'paid', 'transferred', 'transfer_failed', 'pending_transfer',
    'disputed', 'dispute_won', 'dispute_lost', 'processing'
)


def assert_no_charged_transactions(db, gig_id: int, artist_id: int = None):
    """Raise HTTPException(409, "CHARGED_TRANSACTION_EXISTS: ...") if any
    transaction tied to this gig (and optionally this artist) is in a
    money-moving state.

    Callers should run this BEFORE invoking ``cleanup_gig_records`` /
    ``delete_gig_completely`` so the venue/admin is forced through the
    explicit refund + transfer-reversal flow in admin_payments.py
    instead of silently dropping the audit trail.

    Door-deal extension (Jun 2026): also block cancellation when any of the
    gig's slots have been SETTLED (settled_at IS NOT NULL).

    Race-safety (Jul 2026 audit): the check + subsequent cleanup are not
    atomic against the payout_scheduler which atomically claims a
    'scheduled' row into 'processing'. Reduce the window by explicitly
    committing before returning (so any concurrent 'processing' transition
    is visible on our next read), and include 'processing' in the state
    set. Full atomicity would need SELECT FOR UPDATE / BEGIN IMMEDIATE
    which isn't portable across our SQLite/Postgres shim — the narrow
    window that remains is acceptable given the scheduler's own idempotency
    on the txn id.
    """
    from fastapi import HTTPException
    # Force any pending state changes to be visible before we assert.
    # Cheap on SQLite; on Postgres a no-op transaction boundary.
    try:
        db.commit()
    except Exception:
        try: db.rollback()
        except Exception: pass
    placeholders = ", ".join(f"'{s}'" for s in CHARGED_TRANSACTION_STATUSES)
    if artist_id is None:
        row = db.execute(
            text(f"""SELECT id, status FROM transactions
                     WHERE gig_id = :gid
                       AND status IN ({placeholders})
                     LIMIT 1"""),
            {"gid": gig_id}
        ).mappings().first()
    else:
        row = db.execute(
            text(f"""SELECT id, status FROM transactions
                     WHERE gig_id = :gid AND artist_id = :aid
                       AND status IN ({placeholders})
                     LIMIT 1"""),
            {"gid": gig_id, "aid": artist_id}
        ).mappings().first()
    if row:
        who = f"for this artist" if artist_id else "on this gig"
        raise HTTPException(
            409,
            f"CHARGED_TRANSACTION_EXISTS: Cannot proceed — a transaction {who} "
            f"is in status '{row['status']}'. Use the cancel-payment / refund "
            f"flow in Admin → Payments first."
        )

    # Door-settle guard: settled slots have a finalized payout amount
    # baked into the transaction. Cancelling without going through the
    # refund flow would leak money in either direction.
    if artist_id is None:
        settled = db.execute(
            text("""SELECT id FROM gig_slots
                    WHERE gig_id = :gid AND settled_at IS NOT NULL LIMIT 1"""),
            {"gid": gig_id}
        ).mappings().first()
    else:
        settled = db.execute(
            text("""SELECT id FROM gig_slots
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND settled_at IS NOT NULL LIMIT 1"""),
            {"gid": gig_id, "aid": artist_id}
        ).mappings().first()
    if settled:
        who = f"for this artist" if artist_id else "on this gig"
        raise HTTPException(
            409,
            f"SETTLED_DOOR_DEAL_EXISTS: Cannot cancel — a door deal {who} "
            f"has already been settled. Use the refund/reversal flow in "
            f"Admin → Payments to reverse the settlement first."
        )


def cleanup_gig_records(db, gig_id: int, artist_id: int = None):
    """
    Remove transactions, contracts, payment_cancellations, and contract-related
    notifications for a gig. If artist_id is provided, only removes records for
    that specific artist (for multi-slot cancellations).
    
    Args:
        db: SQLAlchemy session
        gig_id: The gig ID to clean up
        artist_id: Optional — if set, only clean up records for this artist
    """
    try:
        if artist_id is not None:
            # Slot-level cleanup: only this artist's records

            # ── Fix venue_charge parent row BEFORE deleting artist records ──
            # Get this artist's payout amount so we can subtract it from the
            # venue_charge row (which has artist_id=NULL and persists across slot changes)
            artist_payout_row = db.execute(
                text("""SELECT t.amount_cents, t.artist_payout_cents, t.commission_cents, t.parent_transaction_id
                        FROM transactions t
                        WHERE t.gig_id = :gid AND t.artist_id = :aid
                          AND t.transaction_type IN ('artist_payout', 'single')
                        LIMIT 1"""),
                {"gid": gig_id, "aid": artist_id}
            ).mappings().first()

            if artist_payout_row:
                slot_amount   = artist_payout_row["amount_cents"] or 0
                slot_fee      = artist_payout_row["commission_cents"] or 0
                parent_id     = artist_payout_row["parent_transaction_id"]

                if parent_id:
                    # Multi-slot: figure out whether this is the last booked artist.
                    # If yes, the parent venue_charge gets deleted here.
                    # If no, we leave the parent alone — the artist's payout child
                    # is deleted further below, and _recompute_gig_fees() at the end
                    # of this branch normalizes the remaining children's numbers.
                    remaining_payouts = db.execute(
                        text("""SELECT COUNT(*) FROM transactions
                                WHERE parent_transaction_id = :pid AND artist_id != :aid
                                AND transaction_type = 'artist_payout'
                                AND status NOT IN ('payment_cancelled')"""),
                        {"pid": parent_id, "aid": artist_id}
                    ).scalar() or 0
                    if remaining_payouts == 0:
                        # No other artists on this gig — delete the venue_charge entirely
                        db.execute(
                            text("DELETE FROM payment_cancellations WHERE transaction_id = :pid"),
                            {"pid": parent_id}
                        )
                        db.execute(
                            text("DELETE FROM transactions WHERE id = :pid"),
                            {"pid": parent_id}
                        )
                        logger.info(f"Deleted venue_charge txn {parent_id} (no remaining slots for gig {gig_id})")
                else:
                    # Single-slot gig ('single' type, no parent): delete it directly.
                    # Audit fix #12 (May 2026): scope to this artist (or NULL) so
                    # an unrelated single/venue_charge row doesn't get dropped if
                    # one ever exists with a different artist_id on the same gig.
                    db.execute(
                        text("""DELETE FROM payment_cancellations
                                WHERE transaction_id IN (
                                    SELECT id FROM transactions WHERE gig_id = :gid
                                    AND transaction_type IN ('venue_charge','single')
                                    AND (artist_id = :aid OR artist_id IS NULL)
                                )"""),
                        {"gid": gig_id, "aid": artist_id}
                    )
                    db.execute(
                        text("""DELETE FROM transactions
                                WHERE gig_id = :gid
                                  AND transaction_type IN ('venue_charge','single')
                                  AND (artist_id = :aid OR artist_id IS NULL)"""),
                        {"gid": gig_id, "aid": artist_id}
                    )
                    logger.info(f"Deleted single/venue_charge txn for gig {gig_id} (artist {artist_id} cancelled)")

            db.execute(
                text("""DELETE FROM payment_cancellations
                        WHERE transaction_id IN (
                            SELECT id FROM transactions WHERE gig_id = :gid AND artist_id = :aid
                        )"""),
                {"gid": gig_id, "aid": artist_id}
            )
            # 2026-08-21: preserve free_trial audit rows on cancel — they
            # carry no money but they DO carry the record that this booking
            # existed under a Free Trial. Losing them means we can't later
            # prove "yes, that artist was booked here under trial" for
            # dispute or support. Just stamp the notes column and leave the
            # row; if the gig itself gets hard-deleted, the row goes with
            # it via FK (acceptable — the gig is gone too).
            db.execute(
                text("""UPDATE transactions
                           SET notes = COALESCE(notes || ' | ', '') || 'Booking cancelled'
                         WHERE gig_id = :gid AND artist_id = :aid
                           AND status = 'free_trial'"""),
                {"gid": gig_id, "aid": artist_id}
            )
            db.execute(
                text("""DELETE FROM transactions
                         WHERE gig_id = :gid AND artist_id = :aid
                           AND status != 'free_trial'"""),
                {"gid": gig_id, "aid": artist_id}
            )
            # Delete signed PDF files for this artist's contracts
            contracts = db.execute(
                text("SELECT signed_pdf_path, pdf_file_path FROM gig_contracts WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": artist_id}
            ).mappings().all()
            for gc in contracts:
                for path_key in ("signed_pdf_path",):  # NEVER delete pdf_file_path — it is the venue template PDF shared across gigs
                    fpath = gc.get(path_key)
                    if fpath:
                        abs_path = fpath.lstrip("/")
                        if os.path.isfile(abs_path):
                            try:
                                os.remove(abs_path)
                                logger.debug(f"Deleted contract file: {abs_path}")
                            except OSError as fe:
                                logger.warning(f"Could not delete {abs_path}: {fe}")
            # Delete contract directory if empty
            contract_dir = os.path.join("app", "static", "uploads", "contracts", "signed", f"gig_{gig_id}")
            if os.path.isdir(contract_dir) and not os.listdir(contract_dir):
                try:
                    os.rmdir(contract_dir)
                except OSError:
                    pass

            db.execute(
                text("DELETE FROM gig_contracts WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": artist_id}
            )

            # Re-normalize the parent venue_charge and any remaining children
            # under the gig-level fee model (no-op if parent was deleted above
            # or the gig is past 'scheduled'/'test' status).
            # Deferred import: avoids a circular import at module load time.
            from backend.routes.gigs import _recompute_gig_fees
            _recompute_gig_fees(db, gig_id)
        else:
            # Full gig cleanup: all artists
            db.execute(
                text("""DELETE FROM payment_cancellations
                        WHERE transaction_id IN (
                            SELECT id FROM transactions WHERE gig_id = :gid
                        )"""),
                {"gid": gig_id}
            )
            # 2026-08-21: same free-trial-audit-preservation policy as the
            # artist-scoped branch above — see comment there for rationale.
            db.execute(
                text("""UPDATE transactions
                           SET notes = COALESCE(notes || ' | ', '') || 'Gig cancelled'
                         WHERE gig_id = :gid AND status = 'free_trial'"""),
                {"gid": gig_id}
            )
            db.execute(
                text("DELETE FROM transactions WHERE gig_id = :gid AND status != 'free_trial'"),
                {"gid": gig_id}
            )
            # Delete signed PDF files for all contracts
            contracts = db.execute(
                text("SELECT signed_pdf_path, pdf_file_path FROM gig_contracts WHERE gig_id = :gid"),
                {"gid": gig_id}
            ).mappings().all()
            for gc in contracts:
                for path_key in ("signed_pdf_path",):  # NEVER delete pdf_file_path — it is the venue template PDF shared across gigs
                    fpath = gc.get(path_key)
                    if fpath:
                        abs_path = fpath.lstrip("/")
                        if os.path.isfile(abs_path):
                            try:
                                os.remove(abs_path)
                                logger.debug(f"Deleted contract file: {abs_path}")
                            except OSError as fe:
                                logger.warning(f"Could not delete {abs_path}: {fe}")
            # Delete contract directory if exists
            contract_dir = os.path.join("app", "static", "uploads", "contracts", "signed", f"gig_{gig_id}")
            if os.path.isdir(contract_dir):
                try:
                    shutil.rmtree(contract_dir)
                    logger.debug(f"Deleted contract dir: {contract_dir}")
                except OSError as fe:
                    logger.warning(f"Could not delete dir {contract_dir}: {fe}")

            db.execute(
                text("DELETE FROM gig_contracts WHERE gig_id = :gid"),
                {"gid": gig_id}
            )
        
        # Always remove contract-related notifications for the gig
        type_list = ", ".join(f"'{t}'" for t in CONTRACT_NOTIFICATION_TYPES)
        db.execute(
            text(f"""DELETE FROM notifications WHERE gig_id = :gid 
                     AND notification_type IN ({type_list})"""),
            {"gid": gig_id}
        )
        
        logger.debug(f"Cleaned up records for gig={gig_id}, artist={artist_id}")

    except Exception as e:
        # Audit fix (May 2026 part 2): previously this swallowed the
        # exception, called rollback, returned silently — callers kept
        # mutating (slot reset, emails, blasts) assuming cleanup
        # succeeded, leaving the DB in a half-cleaned state. Now we log
        # with the stack trace AND re-raise so the caller can react.
        logger.error(
            f"Cleanup failed for gig={gig_id}, artist={artist_id}: {e}",
            exc_info=True
        )
        try: db.rollback()
        except Exception: pass
        raise


def cleanup_hold_records(db, gig_id: int, *, delete_rows: bool = False) -> None:
    """Cancellation-side cleanup for the Hold-Gig feature (Jun 2026 audit).

    Called whenever a gig is cancelled / deleted / has its booked artist
    cancelled. Three of the cancellation endpoints had no hold-aware
    cleanup, so:
      - hold_status stayed 'active'/'exhausted' (gig still hidden from
        public search forever)
      - In-flight email links stayed valid (an artist could click their
        old email Accept link and try to book a slot that no longer exists)
      - On a subsequent /hold/start the old hold_waitlist rows would be
        replayed, causing duplicate offer emails to artists who had
        already declined the previous cycle.

    Two modes:
      - delete_rows=False (default — used by "keep_open" cancellation
        paths): mark in-flight offers consumed (offer_declined=1,
        offer_expires_at=now) and clear hold_status on the gig. Rows
        survive for audit history.
      - delete_rows=True (used by full-gig delete paths): also DELETE
        every hold-source waitlist row + matching waitlist_offered
        tokens. Cleaner state for downstream replay.

    Idempotent — safe to call on a gig with no hold history.
    """
    try:
        # Invalidate any in-flight offer first so a stale email click
        # hits respond_to_hold_offer's "no longer active" branch.
        db.execute(
            text("""UPDATE gig_waitlist
                    SET offer_declined = 1,
                        offer_expires_at = CURRENT_TIMESTAMP
                    WHERE gig_id = :gid
                      AND source = 'hold'
                      AND offer_sent = 1
                      AND offer_declined = 0"""),
            {"gid": gig_id}
        )
        if delete_rows:
            db.execute(
                text("DELETE FROM gig_waitlist WHERE gig_id = :gid AND source = 'hold'"),
                {"gid": gig_id}
            )
            try:
                db.execute(
                    text("DELETE FROM waitlist_offered WHERE gig_id = :gid"),
                    {"gid": gig_id}
                )
            except Exception as _woe:
                logger.warning(f"cleanup_hold_records waitlist_offered skip: {_woe}")
        # Clear hold state on the gig itself so the gig becomes
        # publicly visible again (or is fully wound down).
        db.execute(
            text("UPDATE gigs SET hold_status = NULL WHERE id = :gid"),
            {"gid": gig_id}
        )
    except Exception as e:
        logger.warning(f"cleanup_hold_records failed gig={gig_id}: {e}")


def delete_gig_completely(db, gig_id: int):
    """
    Delete a gig and ALL related records: slots, transactions, contracts,
    notifications. Used by venue-initiated delete operations.
    
    Args:
        db: SQLAlchemy session
        gig_id: The gig to delete
    """
    cleanup_gig_records(db, gig_id)
    # Clean up tables that cleanup_gig_records doesn't cover — wrapped for safety
    # Optional tables — commit each separately
    for _sql, _p in [
        ("DELETE FROM gig_email_log WHERE gig_id = :gid", {"gid": gig_id}),
        ("DELETE FROM public_activity WHERE gig_id = :gid", {"gid": gig_id}),
    ]:
        try:
            db.execute(text(_sql), _p)
            db.commit()
        except Exception as _de:
            logger.warning(f"delete_gig_completely optional skip ({_sql[:40]}): {_de}")
            db.rollback()
    # Core deletes — ALL tables with FK to gigs must be cleared first
    db.execute(text("DELETE FROM notifications WHERE gig_id = :gid"), {"gid": gig_id})
    db.execute(text("DELETE FROM gig_messages WHERE gig_id = :gid"), {"gid": gig_id})
    db.execute(text("DELETE FROM gig_waitlist WHERE gig_id = :gid"), {"gid": gig_id})
    # waitlist_offered persists tokens after row deletion — must also clean on gig delete
    try:
        db.execute(text("DELETE FROM waitlist_offered WHERE gig_id = :gid"), {"gid": gig_id})
    except Exception as _woe:
        logger.warning(f"delete_gig_completely waitlist_offered skip: {_woe}")
    # Audit YELLOW fix (Jul 1 2026): gig_cancelled_artists was left behind
    # on gig delete. If a gig id ever recycles (Postgres serial gap +
    # admin manual reset), a stale row could exclude an artist from a
    # totally unrelated future gig's blast. Optional table — wrapped.
    try:
        db.execute(text("DELETE FROM gig_cancelled_artists WHERE gig_id = :gid"), {"gid": gig_id})
    except Exception as _gcae:
        logger.warning(f"delete_gig_completely gig_cancelled_artists skip: {_gcae}")
    # pending_approval_tokens carries per-(gig, artist) email approval
    # tokens that never expire on their own — clear on gig delete so a
    # recycled id can't replay.
    try:
        db.execute(text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid"), {"gid": gig_id})
    except Exception as _pate:
        logger.warning(f"delete_gig_completely pending_approval_tokens skip: {_pate}")
    db.execute(text("DELETE FROM artist_reviews WHERE gig_id = :gid"), {"gid": gig_id})
    # BUG FIX (Jul 2026 audit): venue_reviews.gig_id is nullable, so a stale
    # row was left behind pointing at the deleted gig. The public
    # list_venue_reviews payload then exposed a dead gig_id. NULL the FK
    # instead of deleting so the review (rating + text) survives — that's
    # the same policy user-account delete now uses for authored reviews.
    try:
        db.execute(text("UPDATE venue_reviews SET gig_id = NULL WHERE gig_id = :gid"), {"gid": gig_id})
    except Exception as _vre:
        logger.warning(f"delete_gig_completely venue_reviews.gig_id NULL skip: {_vre}")
    db.execute(text("DELETE FROM gig_slots WHERE gig_id = :gid"), {"gid": gig_id})
    # Jul 1 2026: capture the recurring_group_id BEFORE deleting so we
    # can clean up an orphaned singleton series after the row is gone.
    try:
        _rgid_row = db.execute(
            text("SELECT recurring_group_id FROM gigs WHERE id = :gid"),
            {"gid": gig_id}
        ).mappings().first()
        _rgid = (_rgid_row or {}).get("recurring_group_id")
    except Exception:
        _rgid = None
    db.execute(text("DELETE FROM gigs WHERE id = :gid"), {"gid": gig_id})
    # Series-shrink cleanup (Jul 1 2026): if this delete leaves the
    # recurring group with fewer than 2 members, the survivor(s) aren't
    # really a "series" anymore — a series-of-one is just a standalone
    # gig. Null out the recurring_* fields on the survivors so the
    # frontend doesn't render "🔁 This gig is part of a recurring
    # series" for a lone gig. User-visible case: venue deletes all
    # other members of a series; the last one still claims series
    # membership in the modal.
    if _rgid:
        try:
            _remaining = db.execute(
                text("SELECT COUNT(*) FROM gigs WHERE recurring_group_id = :rgid"),
                {"rgid": _rgid}
            ).scalar() or 0
            if int(_remaining) < 2:
                db.execute(
                    text("""UPDATE gigs SET
                                recurring_group_id = NULL,
                                is_recurring = 0,
                                recurring_interval_weeks = NULL,
                                recurring_days_of_week = NULL,
                                recurring_end_type = NULL,
                                recurring_end_after = NULL,
                                recurring_end_by_date = NULL
                            WHERE recurring_group_id = :rgid"""),
                    {"rgid": _rgid}
                )
                logger.info(f"[SERIES_CLEANUP] delete_gig_completely: recurring group {_rgid[:12] if isinstance(_rgid,str) else _rgid} shrank to {int(_remaining)} member(s); cleared recurring fields on survivor(s).")
        except Exception as _sce:
            logger.warning(f"delete_gig_completely series-shrink cleanup skip for rgid={_rgid}: {_sce}")
    logger.debug(f"Deleted gig={gig_id} and all related records")

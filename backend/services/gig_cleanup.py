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
            db.execute(
                text("DELETE FROM transactions WHERE gig_id = :gid AND artist_id = :aid"),
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
            db.execute(
                text("DELETE FROM transactions WHERE gig_id = :gid"),
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
        logger.error(f"Cleanup failed for gig={gig_id}, artist={artist_id}: {e}")
        try: db.rollback()
        except: pass


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
    db.execute(text("DELETE FROM artist_reviews WHERE gig_id = :gid"), {"gid": gig_id})
    db.execute(text("DELETE FROM gig_slots WHERE gig_id = :gid"), {"gid": gig_id})
    db.execute(text("DELETE FROM gigs WHERE id = :gid"), {"gid": gig_id})
    logger.debug(f"Deleted gig={gig_id} and all related records")

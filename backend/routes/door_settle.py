"""
Door-deal settlement — finalize artist pay based on actual door receipts.

Many small-venue gigs are structured as "guarantee + % of door over X
attendance" or "pure door split." The base pay column on gig_slots
captures the guarantee; this endpoint takes the actual door receipts
filed by the venue after the show and computes the final pay, updating
the matching `transactions` row so the existing payout pipeline pays
the right amount.

Schema additions (db.py):
    gig_slots.deal_type             ('flat' | 'door')
    gig_slots.door_pct              (int 0-100, % of receipts to artist)
    gig_slots.guarantee_cents       (floor pay regardless of receipts)
    gig_slots.door_receipts_cents   (entered post-show)
    gig_slots.settled_pay_cents     (computed final)
    gig_slots.settled_at            (when filed)

Math:
    final_pay = max(guarantee, guarantee + (receipts * door_pct / 100))
    — i.e. the guarantee is the floor, the door split is additive over it.

For pure-door deals, set guarantee_cents = 0 and door_pct = 100.

Endpoint:
    POST /api/gigs/{gig_id}/slots/{slot_id}/settle
        body: { "door_receipts_cents": int }
        access: venue only
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.utils import check_venue_access

logger = logging.getLogger("gigsfill.door_settle")
router = APIRouter()


def _compute_settled_pay(guarantee_cents, door_pct, receipts_cents):
    """Door deal math. Guarantee is the floor; door % is additive."""
    g = int(guarantee_cents or 0)
    pct = int(door_pct or 0)
    r = int(receipts_cents or 0)
    door_share = (r * pct) // 100
    return max(g, g + door_share)


@router.post("/api/gigs/{gig_id}/slots/{slot_id}/settle")
def settle_door_deal(gig_id: int, slot_id: int, data: dict,
                     user=Depends(get_current_user), db=Depends(get_db)):
    """Venue files door receipts; we update the transaction with the
    final payable amount."""
    receipts = data.get("door_receipts_cents")
    if not isinstance(receipts, int) or receipts < 0 or receipts > 10_000_000:
        raise HTTPException(400, "door_receipts_cents must be a non-negative integer (cents)")

    slot = db.execute(
        text("""
            SELECT gs.id, gs.gig_id, gs.artist_id, gs.status, gs.deal_type,
                   gs.door_pct, gs.guarantee_cents, gs.settled_at,
                   g.venue_id, g.date
            FROM gig_slots gs JOIN gigs g ON g.id = gs.gig_id
            WHERE gs.id = :sid AND gs.gig_id = :gid
        """),
        {"sid": slot_id, "gid": gig_id}
    ).mappings().first()
    if not slot:
        raise HTTPException(404, "Slot not found")
    check_venue_access(db, slot["venue_id"], user.id)

    if slot["status"] != "booked":
        raise HTTPException(400, f"Slot must be booked to settle (current: {slot['status']})")
    if slot["deal_type"] != "door":
        raise HTTPException(400, "Slot is not a door deal — final pay is the listed `pay` field")
    if not slot["artist_id"]:
        raise HTTPException(400, "Slot has no booked artist to settle with")
    # Allow RE-SETTLE up until the scheduled payout actually fires
    # (status='scheduled'/'test'). Once the parent venue_charge has
    # moved past those states (charged / paid / transferred) the money
    # is in motion and changing the amount would desync the books.
    # Mirrors the gate already used by _save_bounce_check_result logic
    # — money-state, not time-state, is the canonical lock.
    if slot["settled_at"]:
        _parent = db.execute(
            text("""SELECT status FROM transactions
                    WHERE gig_id = :gid AND transaction_type = 'venue_charge'
                    ORDER BY id DESC LIMIT 1"""),
            {"gid": gig_id}
        ).mappings().first()
        _parent_status = (_parent and _parent.get("status")) or ""
        if _parent_status not in ("scheduled",):
            raise HTTPException(
                400,
                "Settlement already in flight — the venue charge has moved past 'scheduled' "
                f"(currently '{_parent_status}'). Contact admin to revise."
            )

    final_pay_cents = _compute_settled_pay(
        slot["guarantee_cents"], slot["door_pct"], receipts
    )

    db.execute(
        text("""
            UPDATE gig_slots
            SET door_receipts_cents = :rec,
                settled_pay_cents = :final,
                settled_at = CURRENT_TIMESTAMP,
                pay = :final_dollars
            WHERE id = :sid
        """),
        {"rec": receipts, "final": final_pay_cents,
         "final_dollars": final_pay_cents / 100.0, "sid": slot_id}
    )

    # Update the existing booking transaction so the payout pipeline pays
    # the settled amount instead of the original guarantee.
    #
    # IMPORTANT: only update `amount_cents` (and append an audit note).
    # DO NOT also write artist_payout_cents / venue_charge_cents here —
    # those need to be re-derived from the platform fee math, not just
    # set to the new gross. The earlier version of this code wrote
    # `artist_payout_cents = :final, venue_charge_cents = :final` which:
    #   - left the artist receiving the full settled pay (no platform
    #     fee deducted)
    #   - put venue_charge_cents on what's supposed to be a child row
    #     (that field belongs to the parent venue_charge)
    #   - left the parent venue_charge stuck at the booking-time
    #     amount, so the Venue Payments page showed the old total
    # _recompute_gig_fees() below handles all of that consistently —
    # it re-sums the children, recomputes platform fee, redistributes
    # to each child, and rewrites the parent. Single source of truth.
    audit_note = (
        f" [door-settled receipts={int(receipts)}c "
        f"pct={int(slot['door_pct'] or 0)}% "
        f"guarantee={int(slot['guarantee_cents'] or 0)}c]"
    )
    # Strip any prior " [door-settled ...]" suffix before appending the
    # new one — re-settles otherwise accrete suffixes, so notes grow
    # 60+ chars per attempt. The persistent audit trail lives in the
    # logger.info call below; the notes column just shows "the most
    # recent settle terms."
    # Portable SUBSTR trick: INSTR(notes, ' [door-settled') in SQLite
    # and POSITION/STRPOS in Postgres aren't compatible, so do the
    # strip in Python: read the row, trim, write back. Two-step SQL,
    # but the row is tiny.
    slot_note_prefix = f"Slot {int(slot_id)}"
    # Prefer transactions.slot_id (Jun 2026 audit) — it's an unambiguous
    # FK on rows booked after the migration. Falls back to the notes-LIKE
    # pattern for rows from before. Both filters guard against a same-
    # artist-multiple-slots gig.
    _cur = db.execute(
        text("""
            SELECT id, notes FROM transactions
            WHERE gig_id = :gid
              AND artist_id = :aid
              AND status = 'scheduled'
              AND (transaction_type IS NULL
                   OR transaction_type IN ('artist_payout', 'single'))
              AND (
                slot_id = :sid
                OR (slot_id IS NULL
                    AND (notes LIKE :slot_like OR notes LIKE :slot_like_mid))
              )
        """),
        {"gid": gig_id, "aid": slot["artist_id"], "sid": int(slot_id),
         "slot_like": slot_note_prefix + '%',
         "slot_like_mid": '%' + slot_note_prefix + ' %'}
    ).mappings().all()

    def _strip_settle_suffix(s):
        if not s:
            return s
        i = s.find(' [door-settled')
        return s if i < 0 else s[:i]

    # BUG FIX (Jul 2026 audit): re-check `status = 'scheduled'` in the UPDATE
    # WHERE to protect against a race where the payout scheduler atomically
    # claims the row into 'processing' between our SELECT at line 156 and
    # this UPDATE. If rowcount is 0 for a given row, the scheduler beat us
    # to it — leaving the child at the OLD amount_cents while the settle
    # audit note describes NEW terms. Log and drop into the off-platform
    # fallback so the venue is told the settle didn't move money.
    txn_updated_count = 0
    for r in _cur:
        new_notes = (_strip_settle_suffix(r["notes"]) or '') + audit_note
        _res = db.execute(
            text("UPDATE transactions SET amount_cents = :final, notes = :notes "
                 "WHERE id = :id AND status = 'scheduled'"),
            {"final": final_pay_cents, "notes": new_notes, "id": r["id"]}
        )
        if (_res.rowcount or 0) > 0:
            txn_updated_count += 1
        else:
            logger.warning(
                f"[SETTLE] txn {r['id']} slipped out of 'scheduled' before we could "
                f"anchor the door-settle amount — scheduler likely claimed it. "
                f"Falling through to off-platform delta path."
            )

    # Fallback path: legacy rows (pre-multi-slot) wrote notes='Artist N'
    # with no Slot prefix. If nothing matched the slot-scoped filter,
    # fall back to the original artist+gig match — safe because legacy
    # single-slot gigs have exactly one artist_payout row per artist.
    if txn_updated_count == 0:
        _legacy = db.execute(
            text("""
                SELECT id, notes FROM transactions
                WHERE gig_id = :gid
                  AND artist_id = :aid
                  AND status = 'scheduled'
                  AND (transaction_type IS NULL
                       OR transaction_type IN ('artist_payout', 'single'))
                  AND (notes IS NULL OR notes NOT LIKE 'Slot %')
            """),
            {"gid": gig_id, "aid": slot["artist_id"]}
        ).mappings().all()
        for r in _legacy:
            new_notes = (_strip_settle_suffix(r["notes"]) or '') + audit_note
            db.execute(
                text("UPDATE transactions SET amount_cents = :final, notes = :notes WHERE id = :id"),
                {"final": final_pay_cents, "notes": new_notes, "id": r["id"]}
            )
            txn_updated_count += 1

    # Re-derive the parent venue_charge total + per-child platform fees
    # from the updated children. _recompute_gig_fees is a no-op if the
    # parent's status has moved past 'scheduled'/'test', which matches
    # our "money is in flight, freeze the books" rule above. Imported
    # lazily because backend.routes.gigs has its own import-time work
    # and door_settle stays a leaf module.
    if txn_updated_count > 0:
        try:
            from backend.routes.gigs import _recompute_gig_fees
            _recompute_gig_fees(db, gig_id)
        except Exception as _e:
            logger.warning(
                f"[SETTLE] _recompute_gig_fees failed for gig {gig_id}: {_e}",
                exc_info=True,
            )

    # If we couldn't update any scheduled transaction it means the payout
    # has already fired (charged → paid → transferred). Settle bookkeeping
    # is still useful (the slot row carries the audit trail), but the
    # bonus % won't flow through the platform — venue must pay the
    # door delta to the artist in person. Surface this clearly so the
    # venue knows the operation didn't move money instead of silently
    # appearing to succeed.
    settled_via_platform = txn_updated_count > 0
    delta_cents = max(0, final_pay_cents - int(slot["guarantee_cents"] or 0))

    db.commit()

    # Audit log — every settle is a financial event. If a venue later
    # disputes "I never settled that gig," this gives operators the full
    # picture without grep'ing prod logs.
    logger.info(
        "[SETTLE] gig_id=%s slot_id=%s venue_id=%s artist_id=%s by_user=%s "
        "receipts=%dc guarantee=%sc door_pct=%s%% final_pay=%dc "
        "txn_updated=%d via_platform=%s off_platform_due=%dc",
        gig_id, slot_id, slot["venue_id"], slot["artist_id"], user.id,
        int(receipts), slot["guarantee_cents"] or 0, slot["door_pct"] or 0,
        final_pay_cents, txn_updated_count, settled_via_platform, delta_cents,
    )

    response = {
        "ok": True,
        "settled_pay_cents": final_pay_cents,
        "settled_pay_dollars": round(final_pay_cents / 100.0, 2),
        "guarantee_cents": slot["guarantee_cents"] or 0,
        "door_pct": slot["door_pct"] or 0,
        "door_receipts_cents": receipts,
        "transactions_updated": txn_updated_count,
        "settled_via_platform": settled_via_platform,
    }
    if not settled_via_platform:
        response["warning"] = (
            f"Receipts recorded, but the artist's payout has already been processed. "
            f"The platform transaction was not adjusted — please pay the artist "
            f"the door bonus of ${delta_cents/100:,.2f} directly."
        )
        response["off_platform_due_cents"] = delta_cents
    return response


@router.put("/api/gigs/{gig_id}/slots/{slot_id}/deal")
def configure_deal(gig_id: int, slot_id: int, data: dict,
                   user=Depends(get_current_user), db=Depends(get_db)):
    """Configure the deal structure on a slot. Can be called before OR
    after booking; the venue's `pay` is still respected for `flat`
    deals (default). For door deals, the venue sets:
        deal_type: 'flat' | 'door'
        door_pct: 0-100 (only used when deal_type='door')
        guarantee_cents: floor pay (only used when deal_type='door')
    The actual settled pay is computed at POST /settle once door
    receipts are filed."""
    slot = db.execute(
        text("""SELECT gs.id, gs.status, gs.settled_at, gs.artist_id,
                       g.venue_id
                FROM gig_slots gs JOIN gigs g ON g.id = gs.gig_id
                WHERE gs.id = :sid AND gs.gig_id = :gid"""),
        {"sid": slot_id, "gid": gig_id}
    ).mappings().first()
    if not slot:
        raise HTTPException(404, "Slot not found")
    check_venue_access(db, slot["venue_id"], user.id)
    if slot["settled_at"]:
        raise HTTPException(400, "Cannot change deal after settlement")

    # BUG FIX (Jul 2026 audit): once a slot is booked, its deal terms are
    # anchored into the accompanying artist_payout transaction (amount_cents,
    # artist_payout_cents, commission_cents — see _create_booking_transaction).
    # Editing guarantee_cents / door_pct / deal_type post-booking without
    # re-anchoring the txn would let the venue silently underpay the artist
    # (or overpay themselves via _recompute_gig_fees). Refuse.
    if slot["status"] in ("booked","pending_contract","awaiting_venue_contract","pending_venue_approval"):
        _live_txn = db.execute(
            text("""SELECT 1 FROM transactions
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND transaction_type IN ('artist_payout','single')
                      AND status NOT IN ('payment_cancelled','account_deleted')
                    LIMIT 1"""),
            {"gid": gig_id, "aid": slot.get("artist_id")}
        ).first()
        if _live_txn:
            raise HTTPException(
                409,
                "DEAL_LOCKED_BY_BOOKING: This slot is booked and has an active "
                "artist payout tied to the current deal terms. Cancel the booking "
                "first to change guarantee / door percent, or wait until after "
                "settlement to adjust."
            )

    deal_type = (data.get("deal_type") or "flat").lower()
    if deal_type not in ("flat", "door"):
        raise HTTPException(400, "deal_type must be 'flat' or 'door'")
    door_pct = int(data.get("door_pct") or 0)
    guarantee_cents = int(data.get("guarantee_cents") or 0)
    if deal_type == "door":
        if door_pct < 0 or door_pct > 100:
            raise HTTPException(400, "door_pct must be between 0 and 100")
        if guarantee_cents < 0 or guarantee_cents > 10_000_000:
            raise HTTPException(400, "guarantee_cents out of range")

    db.execute(
        text("""UPDATE gig_slots
                SET deal_type = :dt, door_pct = :pct, guarantee_cents = :gua
                WHERE id = :sid"""),
        {"dt": deal_type, "pct": door_pct, "gua": guarantee_cents, "sid": slot_id}
    )
    db.commit()
    return {"ok": True, "deal_type": deal_type, "door_pct": door_pct,
            "guarantee_cents": guarantee_cents}


@router.get("/api/gigs/{gig_id}/slots/pending-settlement")
def list_pending_settlements(gig_id: int,
                             user=Depends(get_current_user), db=Depends(get_db)):
    """Door-deal slots on this gig that are booked but not yet settled."""
    gig = db.execute(
        text("SELECT venue_id FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)

    # Door slots that are still editable: either never settled
    # (settled_at IS NULL) OR settled but the parent venue charge is
    # still in the queue (status='scheduled'/'test') — the venue can
    # bump receipts up if extra cash gets counted before payout time.
    # Once the scheduler has charged the card, the row freezes.
    rows = db.execute(
        text("""
            SELECT gs.id as slot_id, gs.slot_number, gs.start_time, gs.end_time,
                   gs.deal_type, gs.door_pct, gs.guarantee_cents,
                   gs.settled_at, gs.door_receipts_cents, a.name as artist_name
            FROM gig_slots gs
            LEFT JOIN artists a ON a.id = gs.artist_id
            WHERE gs.gig_id = :gid
              AND gs.deal_type = 'door'
              AND gs.status = 'booked'
              AND (
                gs.settled_at IS NULL
                OR EXISTS (
                  SELECT 1 FROM transactions t
                  WHERE t.gig_id = gs.gig_id
                    AND t.transaction_type = 'venue_charge'
                    AND t.status = 'scheduled'
                )
              )
            ORDER BY gs.slot_number
        """),
        {"gid": gig_id}
    ).mappings().all()
    return {"pending": [dict(r) for r in rows]}

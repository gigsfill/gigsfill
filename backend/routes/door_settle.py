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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.utils import check_venue_access

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
    if slot["settled_at"]:
        raise HTTPException(400, "This slot has already been settled. Contact admin to revise.")
    if not slot["artist_id"]:
        raise HTTPException(400, "Slot has no booked artist to settle with")

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
    # the settled amount instead of the original guarantee. Match on
    # (gig_id, artist_id) and pre-charge status — only `scheduled` rows
    # are safe to revise (we don't want to alter anything already
    # charged or in flight).
    # Pre-build the audit note in Python so we don't rely on SQL string ||
    # int concatenation, which works in SQLite but throws on Postgres
    # ("operator does not exist: text || integer"). Now a plain TEXT param.
    audit_note = (
        f" [door-settled receipts={int(receipts)}c "
        f"pct={int(slot['door_pct'] or 0)}% "
        f"guarantee={int(slot['guarantee_cents'] or 0)}c]"
    )
    # Defensive: restrict to artist-payout-shaped rows. artist_id IS NOT
    # NULL is already implied by the slot row's artist_id (checked above),
    # but adding transaction_type filtering makes the intent explicit and
    # protects against future schema changes that introduce other row
    # shapes sharing the same (gig_id, artist_id) tuple.
    updated = db.execute(
        text("""
            UPDATE transactions
            SET amount_cents = :final,
                venue_charge_cents = :final,
                artist_payout_cents = :final,
                notes = COALESCE(notes, '') || :audit_note
            WHERE gig_id = :gid
              AND artist_id = :aid
              AND status = 'scheduled'
              AND (transaction_type IS NULL
                   OR transaction_type IN ('artist_payout', 'single'))
        """),
        {"final": final_pay_cents,
         "audit_note": audit_note,
         "gid": gig_id, "aid": slot["artist_id"]}
    )
    txn_updated_count = updated.rowcount or 0

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
        text("""SELECT gs.id, gs.status, gs.settled_at, g.venue_id
                FROM gig_slots gs JOIN gigs g ON g.id = gs.gig_id
                WHERE gs.id = :sid AND gs.gig_id = :gid"""),
        {"sid": slot_id, "gid": gig_id}
    ).mappings().first()
    if not slot:
        raise HTTPException(404, "Slot not found")
    check_venue_access(db, slot["venue_id"], user.id)
    if slot["settled_at"]:
        raise HTTPException(400, "Cannot change deal after settlement")

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

    rows = db.execute(
        text("""
            SELECT gs.id as slot_id, gs.slot_number, gs.start_time, gs.end_time,
                   gs.deal_type, gs.door_pct, gs.guarantee_cents,
                   gs.settled_at, a.name as artist_name
            FROM gig_slots gs
            LEFT JOIN artists a ON a.id = gs.artist_id
            WHERE gs.gig_id = :gid
              AND gs.deal_type = 'door'
              AND gs.status = 'booked'
              AND gs.settled_at IS NULL
            ORDER BY gs.slot_number
        """),
        {"gid": gig_id}
    ).mappings().all()
    return {"pending": [dict(r) for r in rows]}

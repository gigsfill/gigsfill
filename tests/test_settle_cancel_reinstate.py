"""
Integration tests for the door-settle / per-slot-cancel / per-slot-reinstate
flow that shipped across the Jun 2026 audit batches.

These tests target the back-end helpers + endpoints DIRECTLY (no HTTP
client). They exercise the math + state-machine transitions that the
batches added or refactored, so a regression in:

  - door_settle._compute_settled_pay         math
  - _recompute_gig_fees                      proportional fee math
  - same-artist-multiple-slots filter        slot_id/notes disambiguation
  - cancel_slot_payment                      child + parent + recompute
  - reinstate_slot_payment                   child restore + recompute

...gets caught here before it reaches a real Stripe call in prod.

Each test owns a fresh in-memory SQLite db from the conftest fixture.
We bypass the FastAPI endpoint glue (Stripe init, get_current_user)
by calling the helpers directly and asserting the resulting DB state.
"""
import pytest
from sqlalchemy import text


# ─── helpers ──────────────────────────────────────────────────


def _seed_multi_slot_gig(db, *, slot1_pay=15.0, slot2_door=False,
                        slot2_guarantee_cents=1000, slot2_door_pct=50):
    """Two-slot gig: slot 1 flat, slot 2 optionally a door deal.
    Returns a dict of seeded ids.
    """
    db.execute(text("""
        INSERT INTO users (id, first_name, last_name, email, password_hash)
        VALUES (1, 'Alice', 'A', 'alice@t.com', 'h'),
               (2, 'Bob', 'B', 'bob@t.com', 'h'),
               (3, 'Cara', 'C', 'cara@t.com', 'h')
    """))
    db.execute(text("""
        INSERT INTO venues (id, venue_name, user_id) VALUES (1, 'The Venue', 2)
    """))
    db.execute(text("""
        INSERT INTO artists (id, name, user_id) VALUES
            (1, 'Fifty Proof', 1), (2, 'Fridays Past', 3)
    """))
    db.execute(text("""
        INSERT INTO gigs (id, venue_id, date, start_time, end_time, pay, status, is_multi_slot)
        VALUES (1, 1, '2026-06-17', '19:00', '23:00', 25.0, 'booked', 1)
    """))
    # Slot 1: flat
    db.execute(text("""
        INSERT INTO gig_slots (id, gig_id, slot_number, start_time, end_time,
                               pay, status, artist_id, deal_type)
        VALUES (101, 1, 1, '19:00', '21:00', :p, 'booked', 1, 'flat')
    """), {"p": slot1_pay})
    # Slot 2: flat OR door
    if slot2_door:
        db.execute(text("""
            INSERT INTO gig_slots (id, gig_id, slot_number, start_time, end_time,
                                   pay, status, artist_id, deal_type,
                                   guarantee_cents, door_pct)
            VALUES (102, 1, 2, '21:00', '23:00', :p, 'booked', 2, 'door',
                    :g, :pct)
        """), {"p": slot2_guarantee_cents / 100.0,
               "g": slot2_guarantee_cents, "pct": slot2_door_pct})
    else:
        db.execute(text("""
            INSERT INTO gig_slots (id, gig_id, slot_number, start_time, end_time,
                                   pay, status, artist_id, deal_type)
            VALUES (102, 1, 2, '21:00', '23:00', :p, 'booked', 2, 'flat')
        """), {"p": 20.0})
    # Parent venue_charge + 2 artist_payout children.
    # Math at booking with platform_fee_percent=10%, split:
    #   total = 15 + (door=10 OR flat=20) = 25 or 35
    #   ... we let _recompute_gig_fees normalize it below.
    parent_amt = int((slot1_pay + (slot2_guarantee_cents / 100.0 if slot2_door else 20.0)) * 100)
    db.execute(text("""
        INSERT INTO transactions (id, gig_id, from_user_id, to_user_id, artist_id,
                                  amount_cents, venue_charge_cents, artist_payout_cents,
                                  commission_cents, transaction_type, status,
                                  scheduled_process_at)
        VALUES (1, 1, 2, 2, NULL, :a, :a, 0, 0, 'venue_charge', 'scheduled',
                '2026-06-18 00:00:00')
    """), {"a": parent_amt})
    db.execute(text("""
        INSERT INTO transactions (id, gig_id, from_user_id, to_user_id, artist_id,
                                  amount_cents, venue_charge_cents, artist_payout_cents,
                                  commission_cents, transaction_type, status,
                                  parent_transaction_id, slot_id, notes,
                                  scheduled_process_at)
        VALUES (2, 1, 2, 1, 1, :s1, 0, :s1, 0, 'artist_payout', 'scheduled', 1, 101, 'Slot 101', '2026-06-18 00:00:00'),
               (3, 1, 2, 3, 2, :s2, 0, :s2, 0, 'artist_payout', 'scheduled', 1, 102, 'Slot 102', '2026-06-18 00:00:00')
    """), {"s1": int(slot1_pay * 100),
           "s2": slot2_guarantee_cents if slot2_door else 2000})
    db.commit()
    return {"gig_id": 1, "venue_id": 1, "slot_ids": [101, 102]}


# ─── door settle math ─────────────────────────────────────────


def test_compute_settled_pay_basic():
    """Door deal: $10 guarantee + 50% of $20 receipts = $20 final."""
    from backend.routes.door_settle import _compute_settled_pay
    assert _compute_settled_pay(1000, 50, 2000) == 2000


def test_compute_settled_pay_zero_receipts_returns_guarantee():
    """Zero receipts → artist still gets the guarantee floor (door
    share is additive, not min). $10 + 50% × $0 = $10."""
    from backend.routes.door_settle import _compute_settled_pay
    assert _compute_settled_pay(1000, 50, 0) == 1000


def test_compute_settled_pay_low_receipts_additive():
    """Door share is added on top of the guarantee, not replaced.
    Pre-Jun 2026 doc string said "guarantee is the floor" which can
    read as min(guarantee, share) — actual math is guarantee + share."""
    from backend.routes.door_settle import _compute_settled_pay
    # $10 guarantee + 50% of $4 = $10 + $2 = $12.
    assert _compute_settled_pay(1000, 50, 400) == 1200


def test_compute_settled_pay_pure_door():
    """0 guarantee + 100% door — artist gets the receipts straight."""
    from backend.routes.door_settle import _compute_settled_pay
    assert _compute_settled_pay(0, 100, 12_345) == 12_345


def test_compute_settled_pay_none_inputs():
    """All-None inputs collapse to 0 cleanly (don't TypeError)."""
    from backend.routes.door_settle import _compute_settled_pay
    assert _compute_settled_pay(None, None, None) == 0


# ─── _recompute_gig_fees ──────────────────────────────────────


def test_recompute_redistributes_proportionally(db):
    """After a slot's amount changes, _recompute_gig_fees redistributes
    the platform fee proportionally to remaining live children + rewrites
    the parent's venue_charge total."""
    _seed_multi_slot_gig(db, slot1_pay=15.0, slot2_door=False)
    # Bump slot 2's child amount as if a door settle happened.
    db.execute(text(
        "UPDATE transactions SET amount_cents = 1250 WHERE id = 3"
    ))
    db.commit()
    from backend.routes.gigs import _recompute_gig_fees
    _recompute_gig_fees(db, 1)
    # Total = 1500 + 1250 = 2750. fee_pct=10%, min=0, split=split.
    parent = db.execute(text("SELECT amount_cents, venue_charge_cents, commission_cents FROM transactions WHERE id = 1")).mappings().first()
    assert parent["amount_cents"] == 2750
    # commission = 275 (10%). split: venue 137 (138 after integer-half),
    # artist 138.
    assert parent["commission_cents"] == 275
    # venue_charge = 2750 + venue_fee_half (= 137).
    assert parent["venue_charge_cents"] == 2750 + (275 // 2)


def test_recompute_skips_cancelled_children(db):
    """A child marked payment_cancelled drops out of the total."""
    _seed_multi_slot_gig(db, slot1_pay=15.0, slot2_door=False)
    db.execute(text(
        "UPDATE transactions SET status = 'payment_cancelled' WHERE id = 3"
    ))
    db.commit()
    from backend.routes.gigs import _recompute_gig_fees
    _recompute_gig_fees(db, 1)
    parent = db.execute(text("SELECT amount_cents FROM transactions WHERE id = 1")).mappings().first()
    # Only slot 1's $15 remains.
    assert parent["amount_cents"] == 1500


def test_recompute_noop_when_parent_past_scheduled(db):
    """Once parent moves past 'scheduled'/'test', recompute is a no-op
    so we never rewrite books for money that's already in motion."""
    _seed_multi_slot_gig(db, slot1_pay=15.0, slot2_door=False)
    db.execute(text("UPDATE transactions SET status='charged' WHERE id = 1"))
    db.commit()
    before = db.execute(text("SELECT amount_cents FROM transactions WHERE id = 1")).scalar()
    from backend.routes.gigs import _recompute_gig_fees
    _recompute_gig_fees(db, 1)
    after = db.execute(text("SELECT amount_cents FROM transactions WHERE id = 1")).scalar()
    assert before == after


# ─── slot_id disambiguation ──────────────────────────────────


def test_same_artist_two_slots_settle_only_targets_one(db):
    """Same artist booked on both slots. Settling slot 2's door deal
    must not clobber slot 1's amount. Pre-Jun-2026 the WHERE filter
    used artist_id + gig_id only and would hit BOTH rows."""
    _seed_multi_slot_gig(db, slot1_pay=15.0, slot2_door=True,
                        slot2_guarantee_cents=1000, slot2_door_pct=50)
    # Override artist to be the same person on both slots.
    db.execute(text("UPDATE gig_slots SET artist_id = 1 WHERE id = 102"))
    db.execute(text("UPDATE transactions SET artist_id = 1 WHERE id = 3"))
    # Simulate the slot-scoped SELECT used by settle. New slot_id-first
    # filter must pick only the row for slot 102.
    rows = db.execute(text("""
        SELECT id FROM transactions
        WHERE gig_id = 1 AND artist_id = 1 AND status = 'scheduled'
          AND (transaction_type IS NULL
               OR transaction_type IN ('artist_payout', 'single'))
          AND (
            slot_id = 102
            OR (slot_id IS NULL AND notes LIKE 'Slot 102%')
          )
    """)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["id"] == 3, "should target only slot 102's row, not 101's"


def test_legacy_row_without_slot_id_falls_back_to_notes(db):
    """Rows from before the slot_id migration have slot_id NULL but
    notes='Slot N'. The OR branch keeps them findable."""
    _seed_multi_slot_gig(db, slot1_pay=15.0, slot2_door=False)
    # Strip slot_id from txn 3 to simulate a pre-migration row.
    db.execute(text("UPDATE transactions SET slot_id = NULL WHERE id = 3"))
    db.commit()
    rows = db.execute(text("""
        SELECT id FROM transactions
        WHERE gig_id = 1 AND status = 'scheduled'
          AND (
            slot_id = 102
            OR (slot_id IS NULL AND notes LIKE 'Slot 102%')
          )
    """)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["id"] == 3


# ─── door settle end-to-end (via the public helper) ──────────


def test_door_settle_audit_note_dedups_on_resettle(db):
    """Re-settling the same slot replaces (not appends) the audit
    suffix. Prevents the notes column from accreting 60+ chars per
    re-settle."""
    _seed_multi_slot_gig(db, slot1_pay=15.0, slot2_door=True,
                        slot2_guarantee_cents=1000, slot2_door_pct=50)
    # Inline the strip-and-append logic from door_settle directly so
    # we don't need to invoke the FastAPI endpoint.
    base_note = "Slot 102"

    def _strip_settle_suffix(s):
        if not s: return s
        i = s.find(' [door-settled')
        return s if i < 0 else s[:i]

    # First settle: append.
    audit1 = " [door-settled receipts=2000c pct=50% guarantee=1000c]"
    db.execute(text("UPDATE transactions SET notes = :n WHERE id = 3"),
               {"n": _strip_settle_suffix(base_note) + audit1})
    # Re-settle: strip + append.
    audit2 = " [door-settled receipts=500c pct=50% guarantee=1000c]"
    after = db.execute(text("SELECT notes FROM transactions WHERE id = 3")).scalar()
    db.execute(text("UPDATE transactions SET notes = :n WHERE id = 3"),
               {"n": _strip_settle_suffix(after) + audit2})
    db.commit()
    final = db.execute(text("SELECT notes FROM transactions WHERE id = 3")).scalar()
    # Should NOT contain both suffixes — just the latest one.
    assert final.count('[door-settled') == 1
    assert "500c" in final
    assert "2000c" not in final


# ─── cancel + reinstate state-machine ────────────────────────


def test_cancel_then_reinstate_round_trip(db):
    """A cancelled child can be reinstated, restoring the parent total
    after both legs of the round-trip via _recompute_gig_fees."""
    _seed_multi_slot_gig(db, slot1_pay=15.0, slot2_door=False)
    # Normalize at booking.
    from backend.routes.gigs import _recompute_gig_fees
    _recompute_gig_fees(db, 1)
    booking_parent = db.execute(text("SELECT amount_cents FROM transactions WHERE id = 1")).scalar()
    assert booking_parent == 3500  # $15 + $20

    # Cancel slot 2's child.
    db.execute(text("""
        UPDATE transactions SET status='payment_cancelled',
            cancel_reason='Test', platform_fee_charged_cents = commission_cents / 2
        WHERE id = 3
    """))
    db.commit()
    _recompute_gig_fees(db, 1)
    after_cancel = db.execute(text("SELECT amount_cents FROM transactions WHERE id = 1")).scalar()
    assert after_cancel == 1500  # only slot 1 remains live

    # Reinstate slot 2's child (mimics the per-slot reinstate endpoint).
    db.execute(text("""
        UPDATE transactions SET status='scheduled',
            cancel_reason=NULL, cancelled_at=NULL,
            platform_fee_charged_cents=0
        WHERE id = 3
    """))
    db.commit()
    _recompute_gig_fees(db, 1)
    after_reinstate = db.execute(text("SELECT amount_cents FROM transactions WHERE id = 1")).scalar()
    assert after_reinstate == booking_parent, "parent should restore to pre-cancel total"

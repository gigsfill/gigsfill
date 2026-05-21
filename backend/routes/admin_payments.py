"""
Admin Payments Console
======================
Unified searchable view of every transaction across every venue and artist,
plus admin-initiated actions on individual rows.

Tier 1 (read-only — shipped 2026-05-13):
  GET  /api/admin/payments/search        — paginated, filterable txn list
  GET  /api/admin/payments/{txn_id}      — full details + related rows
  GET  /api/admin/payments/stats         — aggregate KPIs for the filtered set

Tier 2 (single-row mutations):
  POST /api/admin/payments/{txn_id}/refund
        Full or partial refund on a venue charge. Calls stripe.Refund.create,
        writes admin_audit_log, optionally cancels still-scheduled child
        artist_payouts (full refund only). Refuses if any child has already
        transferred — that needs Tier 3's reverse-transfer flow.

Stripe Dashboard webhook handler in stripe_connect.py (`charge.refunded`) will
also fire and sync the DB, so manual Stripe Dashboard refunds and refunds
initiated here both converge to the same state.
"""
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from backend.db import get_db
from backend.routes.admin import check_admin
from backend.utils import log_admin_action

router = APIRouter()


# ─── helpers ────────────────────────────────────────────────────────────────

# Allowed status / type values. Anything outside these is rejected to keep
# the comma-separated query params from becoming a SQL surface.
ALLOWED_STATUSES = {
    'scheduled', 'charged', 'transferred', 'paid', 'pending', 'pending_transfer',
    'charge_retry', 'transfer_failed', 'payment_failed', 'payment_cancelled',
    'suspended', 'free_trial', 'test', 'processing', 'disputed', 'dispute_lost',
    'dispute_won',
}
ALLOWED_TYPES = {
    'venue_charge', 'artist_payout', 'single', 'free_trial', 'payment_cancelled',
}


def _parse_csv(val: Optional[str], allowed: set) -> list:
    """Split a comma-separated query param, intersect with allowed set."""
    if not val:
        return []
    return [s.strip() for s in val.split(',') if s.strip() in allowed]


# ─── /api/admin/payments/search ─────────────────────────────────────────────

@router.get("/api/admin/payments/search")
def search_payments(
    q: Optional[str] = None,
    status: Optional[str] = None,
    transaction_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    min_amount_cents: Optional[int] = None,
    max_amount_cents: Optional[int] = None,
    venue_id: Optional[int] = None,
    artist_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Searchable, filterable, paginated transaction list across all entities."""
    statuses = _parse_csv(status, ALLOWED_STATUSES)
    types    = _parse_csv(transaction_type, ALLOWED_TYPES)

    where = ["1=1"]
    params = {}

    if statuses:
        where.append(
            "t.status IN (" + ",".join(f":st_{i}" for i in range(len(statuses))) + ")"
        )
        for i, s in enumerate(statuses):
            params[f"st_{i}"] = s

    if types:
        where.append(
            "COALESCE(t.transaction_type, 'single') IN (" +
            ",".join(f":tt_{i}" for i in range(len(types))) + ")"
        )
        for i, ty in enumerate(types):
            params[f"tt_{i}"] = ty

    if from_date:
        where.append("date(g.date) >= date(:fd)")
        params["fd"] = from_date
    if to_date:
        where.append("date(g.date) <= date(:td)")
        params["td"] = to_date

    if min_amount_cents is not None:
        where.append("COALESCE(t.venue_charge_cents, t.amount_cents, 0) >= :mn")
        params["mn"] = int(min_amount_cents)
    if max_amount_cents is not None:
        where.append("COALESCE(t.venue_charge_cents, t.amount_cents, 0) <= :mx")
        params["mx"] = int(max_amount_cents)

    if venue_id is not None:
        where.append("g.venue_id = :vid")
        params["vid"] = int(venue_id)
    if artist_id is not None:
        where.append("(t.artist_id = :aid OR g.artist_id = :aid)")
        params["aid"] = int(artist_id)

    if q:
        # Free-text search: venue name, artist name, gig title, stripe IDs,
        # explicit txn id (if numeric)
        where.append("""(
            v.venue_name LIKE :ql
            OR a.name LIKE :ql
            OR a2.name LIKE :ql
            OR g.title LIKE :ql
            OR t.stripe_payment_intent_id LIKE :ql
            OR t.stripe_transfer_id LIKE :ql
            OR CAST(t.id AS TEXT) = :qexact
            OR CAST(t.gig_id AS TEXT) = :qexact
        )""")
        params["ql"]    = f"%{q}%"
        params["qexact"] = q.strip()

    where_sql = " AND ".join(where)

    # Count first (cheap because the search is paginated)
    total_row = db.execute(text(f"""
        SELECT COUNT(*) as c
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        LEFT JOIN venues  v  ON v.id = g.venue_id
        LEFT JOIN artists a  ON a.id = t.artist_id
        LEFT JOIN artists a2 ON a2.id = g.artist_id
        WHERE {where_sql}
    """), params).mappings().first()
    total = (total_row and total_row["c"]) or 0

    # Page of rows
    params["lim"] = per_page
    params["off"] = (page - 1) * per_page
    rows = db.execute(text(f"""
        SELECT
            t.id, t.gig_id, t.parent_transaction_id,
            COALESCE(t.transaction_type, 'single') as transaction_type,
            t.status,
            t.amount_cents, t.venue_charge_cents, t.artist_payout_cents,
            t.commission_cents, t.credit_card_fee_cents,
            t.from_user_id, t.to_user_id,
            t.artist_id, g.venue_id,
            t.stripe_payment_intent_id, t.stripe_transfer_id,
            t.scheduled_process_at, t.processed_at, t.created_at,
            t.notes,
            g.date as gig_date, g.start_time as gig_start_time,
            g.title as gig_title, g.status as gig_status,
            v.venue_name,
            COALESCE(a.name, a2.name) as artist_name
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        LEFT JOIN venues  v  ON v.id = g.venue_id
        LEFT JOIN artists a  ON a.id = t.artist_id
        LEFT JOIN artists a2 ON a2.id = g.artist_id
        WHERE {where_sql}
        ORDER BY COALESCE(t.processed_at, t.scheduled_process_at, t.created_at) DESC,
                 t.id DESC
        LIMIT :lim OFFSET :off
    """), params).mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ─── /api/admin/payments/stats ──────────────────────────────────────────────

@router.get("/api/admin/payments/stats")
def payment_stats(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Aggregate KPIs across the filtered date window — for the top of the
    admin Payments page. Only counts parent venue_charge / single rows for
    revenue totals so multi-slot gigs aren't double-counted."""
    where = ["1=1"]
    params = {}
    if from_date:
        where.append("date(g.date) >= date(:fd)")
        params["fd"] = from_date
    if to_date:
        where.append("date(g.date) <= date(:td)")
        params["td"] = to_date
    where_sql = " AND ".join(where)

    # Parent rows for revenue / commission
    parent = db.execute(text(f"""
        SELECT
            COUNT(*) as count,
            COALESCE(SUM(CASE WHEN t.status IN ('paid','transferred','charged') THEN t.venue_charge_cents ELSE 0 END), 0) as revenue_cents,
            COALESCE(SUM(CASE WHEN t.status IN ('paid','transferred','charged') THEN t.commission_cents   ELSE 0 END), 0) as commission_cents,
            SUM(CASE WHEN t.status = 'scheduled'           THEN 1 ELSE 0 END) as scheduled,
            SUM(CASE WHEN t.status = 'charged'             THEN 1 ELSE 0 END) as charged,
            SUM(CASE WHEN t.status = 'paid'                THEN 1 ELSE 0 END) as paid,
            SUM(CASE WHEN t.status = 'payment_failed'      THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN t.status = 'payment_cancelled'   THEN 1 ELSE 0 END) as cancelled,
            SUM(CASE WHEN t.status = 'disputed'            THEN 1 ELSE 0 END) as disputed,
            SUM(CASE WHEN t.status = 'free_trial'          THEN 1 ELSE 0 END) as free_trial,
            SUM(CASE WHEN t.status IN ('transfer_failed','charge_retry','pending_transfer') THEN 1 ELSE 0 END) as needs_attention
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        WHERE COALESCE(t.transaction_type, 'single') IN ('venue_charge','single','free_trial','payment_cancelled')
          AND {where_sql}
    """), params).mappings().first()

    # Child rows for payout total
    child = db.execute(text(f"""
        SELECT COALESCE(SUM(CASE WHEN t.status IN ('paid','transferred') THEN t.artist_payout_cents ELSE 0 END), 0) as payouts_cents
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        WHERE COALESCE(t.transaction_type, 'single') IN ('artist_payout','single')
          AND {where_sql}
    """), params).mappings().first()

    return {
        "count": parent["count"] if parent else 0,
        "revenue_cents":    parent["revenue_cents"] if parent else 0,
        "commission_cents": parent["commission_cents"] if parent else 0,
        "payouts_cents":    child["payouts_cents"]    if child  else 0,
        "by_status": {
            "scheduled":       parent["scheduled"]       or 0,
            "charged":         parent["charged"]         or 0,
            "paid":            parent["paid"]            or 0,
            "payment_failed":  parent["failed"]          or 0,
            "payment_cancelled": parent["cancelled"]     or 0,
            "disputed":        parent["disputed"]        or 0,
            "free_trial":      parent["free_trial"]      or 0,
            "needs_attention": parent["needs_attention"] or 0,
        } if parent else {},
    }


# ─── /api/admin/payments/{txn_id} ───────────────────────────────────────────

@router.get("/api/admin/payments/{txn_id}")
def payment_detail(
    txn_id: int,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Full details for one transaction: the row itself, parent (if child),
    children (if parent), gig + slots, venue, artist, recent admin actions."""
    row = db.execute(text("""
        SELECT
            t.*,
            COALESCE(t.transaction_type, 'single') as transaction_type_resolved,
            g.date as gig_date, g.start_time as gig_start_time, g.end_time as gig_end_time,
            g.title as gig_title, g.status as gig_status, g.venue_id,
            v.venue_name, v.city as venue_city, v.state as venue_state,
            COALESCE(a.name, a2.name) as artist_name,
            COALESCE(t.artist_id, g.artist_id) as resolved_artist_id
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        LEFT JOIN venues  v  ON v.id = g.venue_id
        LEFT JOIN artists a  ON a.id = t.artist_id
        LEFT JOIN artists a2 ON a2.id = g.artist_id
        WHERE t.id = :tid
    """), {"tid": txn_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Transaction not found")

    result = dict(row)

    # Sibling rows: if this is a child, fetch parent + sibling children.
    # If this is a parent, fetch children. If standalone ('single'), nothing.
    siblings = []
    if result.get("parent_transaction_id"):
        sibs = db.execute(text("""
            SELECT id, COALESCE(transaction_type,'single') as transaction_type,
                   status, amount_cents, venue_charge_cents, artist_payout_cents,
                   artist_id, parent_transaction_id, processed_at, stripe_transfer_id
            FROM transactions
            WHERE id = :pid OR parent_transaction_id = :pid
            ORDER BY (id = :pid) DESC, id ASC
        """), {"pid": result["parent_transaction_id"]}).mappings().all()
        siblings = [dict(s) for s in sibs]
    else:
        # Look for children whose parent_transaction_id = this row
        sibs = db.execute(text("""
            SELECT id, COALESCE(transaction_type,'single') as transaction_type,
                   status, amount_cents, venue_charge_cents, artist_payout_cents,
                   artist_id, parent_transaction_id, processed_at, stripe_transfer_id
            FROM transactions
            WHERE parent_transaction_id = :pid
            ORDER BY id ASC
        """), {"pid": txn_id}).mappings().all()
        siblings = [dict(s) for s in sibs]

    # Gig slots (so admin can see the full context of a multi-slot gig)
    slots = db.execute(text("""
        SELECT id, slot_number, start_time, end_time, pay, status, artist_id,
               (SELECT name FROM artists WHERE id = gs.artist_id) as artist_name
        FROM gig_slots gs
        WHERE gs.gig_id = :gid
        ORDER BY slot_number ASC
    """), {"gid": result["gig_id"]}).mappings().all()

    # Recent admin actions on this transaction or its gig. Uses admin_audit_log
    # (defined in db.py — there's no plain `audit_log` table). Best-effort —
    # never blocks the detail view on audit errors.
    audit = []
    try:
        audit_rows = db.execute(text("""
            SELECT id, admin_user_id, admin_email, action,
                   target_table, target_id, before_json, after_json,
                   metadata_json, ip_address, created_at
            FROM admin_audit_log
            WHERE (target_table = 'transactions' AND target_id = :tid_str)
               OR (metadata_json LIKE :gid_like)
            ORDER BY id DESC
            LIMIT 20
        """), {"tid_str": str(txn_id),
               "gid_like": f'%"gig_id": {result["gig_id"]}%'}).mappings().all()
        audit = [dict(a) for a in audit_rows]
    except Exception:
        audit = []

    return {
        "transaction": result,
        "siblings": siblings,
        "slots": [dict(s) for s in slots],
        "audit": audit,
    }


# ─── Tier 2: REFUND ─────────────────────────────────────────────────────────

# Stripe accepts only these three values for the `reason` field on a Refund.
# Anything else is silently coerced; we map our friendly options to these.
_STRIPE_REFUND_REASONS = {'requested_by_customer', 'duplicate', 'fraudulent'}

# Statuses where a refund still makes sense. Anything else (scheduled,
# payment_failed, payment_cancelled, etc.) means there's no charge to refund
# or it's already been undone.
_REFUNDABLE_STATUSES = {'charged', 'paid', 'transferred'}

# Parent transaction types we allow refunds on. Children (artist_payout) are
# refunded by reversing their own transfer in Tier 3, not by refunding the
# venue charge they roll up to.
_REFUNDABLE_TXN_TYPES = {'venue_charge', 'single'}


@router.post("/api/admin/payments/{txn_id}/refund")
def refund_payment(
    txn_id: int,
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Refund a venue charge in full or part.

    Body:
      amount_cents     (optional int) — omit or pass venue_charge_cents for a
                                        full refund; less for partial.
      reason           (optional str) — 'requested_by_customer' (default),
                                        'duplicate', or 'fraudulent'. Sent to
                                        Stripe and recorded in our notes.
      notes            (optional str) — admin's free-text explanation, saved
                                        on the txn and in admin_audit_log.
      cancel_pending_payouts (optional bool, default True for full refunds) —
                                        when refunding in full, cancel any
                                        child artist_payout rows that are
                                        still 'scheduled' (haven't transferred
                                        yet). No-op for partial refunds.

    Hard refusals (400):
      • Txn is a child (parent_transaction_id IS NOT NULL).
      • transaction_type not in {'venue_charge','single'}.
      • status not in {'charged','paid','transferred'}.
      • No stripe_payment_intent_id on the row.
      • A full refund is requested but a child payout has already transferred
        — operator needs Tier 3 (reverse_transfer) first.
      • amount_cents > venue_charge_cents or <= 0 (when amount specified).

    On success the row's status becomes 'payment_cancelled' (full refund) or
    stays as-is with a partial-refund note appended (partial refund). The
    Stripe `charge.refunded` webhook will also fire and re-converge, which
    is harmless — we use the same status sink.
    """
    # 1. Load the txn + sanity-check it's refundable ───────────────────────
    row = db.execute(text("""
        SELECT id, gig_id, parent_transaction_id, transaction_type,
               status, amount_cents, venue_charge_cents, artist_payout_cents,
               commission_cents, stripe_payment_intent_id, notes
        FROM transactions
        WHERE id = :tid
    """), {"tid": txn_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Transaction not found")

    if row["parent_transaction_id"] is not None:
        raise HTTPException(400,
            "This is a child payout row. Refund the parent venue charge, "
            "or use the (Tier 3) reverse-transfer action on this row.")

    txn_type = row["transaction_type"] or "single"
    if txn_type not in _REFUNDABLE_TXN_TYPES:
        raise HTTPException(400,
            f"Cannot refund transactions of type '{txn_type}'.")

    if row["status"] not in _REFUNDABLE_STATUSES:
        raise HTTPException(400,
            f"Cannot refund a transaction in status '{row['status']}'. "
            f"Refundable: {', '.join(sorted(_REFUNDABLE_STATUSES))}.")

    pi_id = row["stripe_payment_intent_id"]
    if not pi_id:
        raise HTTPException(400,
            "No Stripe payment_intent on this transaction — nothing to refund.")

    # dry_run mode: validate everything but skip both the Stripe call AND the
    # DB writes. Caller gets the "would have done X" payload back. Useful for
    # smoke-testing the UI / endpoint without moving real money. The frontend
    # surfaces this as a "Test (dry run)" toggle on the modal.
    dry_run = bool(payload.get("dry_run"))

    # 2. Validate the requested amount ─────────────────────────────────────
    charge_cents = int(row["venue_charge_cents"] or 0)
    requested = payload.get("amount_cents")
    if requested is None or requested == "":
        amount_cents = charge_cents  # full refund
    else:
        try:
            amount_cents = int(requested)
        except (TypeError, ValueError):
            raise HTTPException(400, "amount_cents must be an integer")
    if amount_cents <= 0:
        raise HTTPException(400, "amount_cents must be > 0")
    if amount_cents > charge_cents:
        raise HTTPException(400,
            f"Refund amount (${amount_cents/100:.2f}) exceeds charge "
            f"(${charge_cents/100:.2f}).")
    is_full = (amount_cents == charge_cents)

    # 3. For FULL refunds, ensure no child has already transferred ─────────
    children = db.execute(text("""
        SELECT id, status, stripe_transfer_id, artist_payout_cents
        FROM transactions
        WHERE parent_transaction_id = :pid
    """), {"pid": txn_id}).mappings().all()
    transferred_children = [c for c in children
                            if c["status"] in ('transferred', 'paid')]
    if is_full and transferred_children:
        ids = ", ".join(f"#{c['id']}" for c in transferred_children)
        raise HTTPException(400,
            f"Cannot full-refund: child payout(s) {ids} have already "
            f"transferred to artists. Use the (Tier 3) reverse-transfer "
            f"action on each transferred child first, then come back.")

    # 4. Pick & sanitize the Stripe `reason` ───────────────────────────────
    reason = (payload.get("reason") or "requested_by_customer").strip()
    if reason not in _STRIPE_REFUND_REASONS:
        reason = "requested_by_customer"

    notes_in = (payload.get("notes") or "").strip()[:500]

    # 5. Fire the Stripe refund (or skip on dry_run) ──────────────────────
    if dry_run:
        refund = type("_DryRefund", (), {"id": "re_dryrun_" + str(int(time.time()))})()
    else:
        from backend.routes.stripe_connect import init_stripe
        stripe, _keys = init_stripe(db)

        idem_key = f"admin_refund_txn_{txn_id}_{int(time.time())}"
        try:
            refund = stripe.Refund.create(
                payment_intent=pi_id,
                amount=amount_cents,         # cents
                reason=reason,
                idempotency_key=idem_key,
                metadata={
                    "txn_id": str(txn_id),
                    "gig_id": str(row["gig_id"]),
                    "initiated_by": "admin_payments_console",
                    "admin_user_id": str(getattr(admin, "id", "") or ""),
                },
            )
        except Exception as e:
            # Surface the Stripe error verbatim so the admin sees what went wrong
            msg = getattr(e, "user_message", None) or str(e)
            raise HTTPException(502, f"Stripe refund failed: {msg}")

    # 6. Update our DB ─────────────────────────────────────────────────────
    flavor = "FULL" if is_full else "PARTIAL"
    refund_note = (f"Admin refund {flavor} ${amount_cents/100:.2f} "
                   f"of ${charge_cents/100:.2f} (reason: {reason}, "
                   f"refund_id: {getattr(refund, 'id', '?')})"
                   + (f" — {notes_in}" if notes_in else ""))

    new_status = 'payment_cancelled' if is_full else row["status"]
    cancelled_children = []
    cancel_kids = payload.get("cancel_pending_payouts")
    if cancel_kids is None:
        cancel_kids = is_full

    if not dry_run:
        db.execute(text("""
            UPDATE transactions
               SET status = :status,
                   notes  = COALESCE(notes || ' | ', '') || :note
             WHERE id = :tid
        """), {"status": new_status, "note": refund_note, "tid": txn_id})

        # Cancel pending child payouts when doing a full refund (default on).
        if is_full and cancel_kids:
            for c in children:
                if c["status"] == 'scheduled':
                    db.execute(text("""
                        UPDATE transactions
                           SET status = 'payment_cancelled',
                               notes  = COALESCE(notes || ' | ', '') || :note
                         WHERE id = :cid
                    """), {"cid": c["id"],
                           "note": f"Cancelled by admin refund of parent #{txn_id}"})
                    cancelled_children.append(c["id"])

        db.commit()
    else:
        # In dry_run, project what the children-cancel would have touched
        if is_full and cancel_kids:
            cancelled_children = [c["id"] for c in children if c["status"] == 'scheduled']

    # 7. Audit ─────────────────────────────────────────────────────────────
    if not dry_run:
        log_admin_action(
            db, admin, "payments_refund",
            target_table="transactions", target_id=txn_id,
            before={"status": row["status"]},
            after={"status": new_status},
            metadata={
                "gig_id": row["gig_id"],
                "amount_cents": amount_cents,
                "charge_cents": charge_cents,
                "is_full": is_full,
                "stripe_refund_id": getattr(refund, "id", None),
                "reason": reason,
                "notes": notes_in,
                "cancelled_child_payouts": cancelled_children,
            },
            request=request,
        )
        db.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "stripe_refund_id": getattr(refund, "id", None),
        "amount_cents": amount_cents,
        "is_full": is_full,
        "new_status": new_status,
        "cancelled_child_payouts": cancelled_children,
        # NOTE on Stripe fees: Stripe does NOT return the original processing
        # fee on a refund. The venue gets `amount_cents` back; the platform
        # eats the original ~2.9% + 30¢. Our `credit_card_fee_cents` column
        # records that fee from the original charge — refund does not zero
        # it out, since the fee was actually paid and is not recoverable.
        "stripe_fees_note": (
            "Stripe keeps the original processing fee on this charge. "
            "The venue is refunded the full amount above; the platform "
            "absorbs the credit-card fee from the original transaction."
        ),
    }


# ─── Tier 2: MARK-RESOLVED ──────────────────────────────────────────────────

# Statuses an admin can force a row into. Kept tight on purpose — the broader
# `ALLOWED_STATUSES` set is for filtering / display, not for arbitrary writes.
_MARK_RESOLVED_TARGETS = {
    'paid', 'transferred', 'payment_cancelled', 'suspended',
    'dispute_won', 'dispute_lost',
}


@router.post("/api/admin/payments/{txn_id}/mark-resolved")
def mark_resolved(
    txn_id: int,
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Force a transaction's status without calling Stripe. Use for stuck
    states (e.g. dispute_won that didn't sync from a webhook, free-trial
    rows admin wants to lock as cancelled). Reason is REQUIRED and ends
    up both in `transactions.notes` and `admin_audit_log.metadata_json`.

    Body:
      new_status   (required) — must be in _MARK_RESOLVED_TARGETS.
      reason       (required) — free-text, min 5 chars.
    """
    new_status = (payload.get("new_status") or "").strip()
    reason     = (payload.get("reason") or "").strip()[:1000]
    if new_status not in _MARK_RESOLVED_TARGETS:
        raise HTTPException(400,
            f"new_status must be one of: {', '.join(sorted(_MARK_RESOLVED_TARGETS))}")
    if len(reason) < 5:
        raise HTTPException(400, "reason is required (min 5 characters)")

    row = db.execute(text("""
        SELECT id, gig_id, status FROM transactions WHERE id = :tid
    """), {"tid": txn_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Transaction not found")
    if row["status"] == new_status:
        raise HTTPException(400, f"Transaction is already in status '{new_status}'.")

    note = (f"Admin mark-resolved: {row['status']} → {new_status} "
            f"({getattr(admin, 'email', 'admin')}) — {reason}")
    db.execute(text("""
        UPDATE transactions
           SET status = :s, notes = COALESCE(notes || ' | ', '') || :n
         WHERE id = :tid
    """), {"s": new_status, "n": note, "tid": txn_id})
    db.commit()

    log_admin_action(
        db, admin, "payments_mark_resolved",
        target_table="transactions", target_id=txn_id,
        before={"status": row["status"]},
        after={"status": new_status},
        metadata={"gig_id": row["gig_id"], "reason": reason},
        request=request,
    )
    db.commit()
    return {"ok": True, "from": row["status"], "to": new_status}


# ─── Tier 2: RE-FIRE ────────────────────────────────────────────────────────

# Only meaningful when the scheduler gave up or a transient Stripe error
# left a row in a failed/retry state. Resetting to 'scheduled' + bumping
# scheduled_process_at to now lets the next hourly sweep pick it up.
_REFIRABLE_STATUSES = {
    'payment_failed', 'charge_retry', 'transfer_failed', 'pending_transfer',
}


@router.post("/api/admin/payments/{txn_id}/refire")
def refire_payment(
    txn_id: int,
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Reset a stuck/failed txn back to 'scheduled' so the next scheduler
    sweep retries it. Does NOT call Stripe directly — defense-in-depth:
    the scheduler is the only place that mints Stripe writes, so re-fire
    can't double-charge.

    Body:
      notes    (optional) — free-text, saved in transactions.notes and audit.
      dry_run  (optional bool) — preview without writing.

    Stripe idempotency: the scheduler builds keys from
      (gig_id, slot_id, artist_id, attempt #). Re-firing increments
    `charge_attempts`, so the next attempt uses a fresh key — no duplicate
    charge risk.
    """
    notes_in = (payload.get("notes") or "").strip()[:500]
    dry_run  = bool(payload.get("dry_run"))

    row = db.execute(text("""
        SELECT id, gig_id, status, charge_attempts, scheduled_process_at,
               charge_failure_reason
        FROM transactions WHERE id = :tid
    """), {"tid": txn_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Transaction not found")
    if row["status"] not in _REFIRABLE_STATUSES:
        raise HTTPException(400,
            f"Cannot re-fire status '{row['status']}'. "
            f"Re-firable: {', '.join(sorted(_REFIRABLE_STATUSES))}.")

    from datetime import datetime, timezone
    new_sched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    note = (f"Admin re-fire: status {row['status']} → scheduled "
            f"({getattr(admin, 'email', 'admin')})"
            + (f" — {notes_in}" if notes_in else ""))

    if not dry_run:
        db.execute(text("""
            UPDATE transactions
               SET status = 'scheduled',
                   scheduled_process_at = :sched,
                   charge_failure_reason = NULL,
                   notes = COALESCE(notes || ' | ', '') || :n
             WHERE id = :tid
        """), {"sched": new_sched, "n": note, "tid": txn_id})
        db.commit()

        log_admin_action(
            db, admin, "payments_refire",
            target_table="transactions", target_id=txn_id,
            before={"status": row["status"],
                    "scheduled_process_at": str(row["scheduled_process_at"])},
            after={"status": "scheduled", "scheduled_process_at": new_sched},
            metadata={"gig_id": row["gig_id"], "notes": notes_in,
                      "prior_failure_reason": row["charge_failure_reason"]},
            request=request,
        )
        db.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "from": row["status"],
        "to": "scheduled",
        "new_scheduled_process_at": new_sched,
    }


# ─── Tier 2: RESEND EMAIL ───────────────────────────────────────────────────

# Maps the admin-friendly template id to the underlying sender function in
# payout_scheduler.py. Both senders speak the raw sqlite3 Row format, so we
# open a separate sqlite connection just for them rather than trying to
# adapt them to SQLAlchemy.
_RESEND_TEMPLATES = {
    'venue_charged':      'venue',   # txn must be parent venue_charge / single
    'artist_payout_sent': 'artist',  # txn must be artist_payout child
}


@router.post("/api/admin/payments/{txn_id}/resend-email")
def resend_email(
    txn_id: int,
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Re-send a payment-event email for this transaction.

    Body:
      template (required) — 'venue_charged' or 'artist_payout_sent'.
      dry_run  (optional)  — return the resolved recipient + subject without
                             actually sending. Use for testing the wiring.

    The actual templates live in backend/email_templates.py and are rendered
    by the same payout_scheduler functions the live charge/transfer path uses
    — so the resent email is byte-identical to what would have gone out the
    first time.
    """
    template = (payload.get("template") or "").strip()
    dry_run  = bool(payload.get("dry_run"))
    if template not in _RESEND_TEMPLATES:
        raise HTTPException(400,
            f"template must be one of: {', '.join(sorted(_RESEND_TEMPLATES))}")

    row = db.execute(text("""
        SELECT id, gig_id, parent_transaction_id, transaction_type, status,
               to_user_id, from_user_id, artist_id
        FROM transactions WHERE id = :tid
    """), {"tid": txn_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Transaction not found")

    txn_type = row["transaction_type"] or "single"
    # Validate the template matches the row shape — sending "artist payout
    # sent" for a row that has no artist makes no sense.
    if template == 'venue_charged' and txn_type not in ('venue_charge', 'single'):
        raise HTTPException(400,
            f"'venue_charged' template needs a venue_charge/single txn; this is '{txn_type}'.")
    if template == 'artist_payout_sent' and txn_type != 'artist_payout':
        raise HTTPException(400,
            f"'artist_payout_sent' template needs an artist_payout txn; this is '{txn_type}'.")

    if dry_run:
        log_admin_action(
            db, admin, "payments_resend_email_dryrun",
            target_table="transactions", target_id=txn_id,
            metadata={"template": template, "gig_id": row["gig_id"]},
            request=request,
        )
        db.commit()
        return {"ok": True, "dry_run": True, "template": template,
                "note": "Dry run — no email sent. Endpoint validation passed."}

    # Re-use the scheduler's senders by passing them a raw sqlite Row conn.
    # They handle template loading + recipient resolution + SMTP via
    # email_dispatch internally.
    try:
        import sqlite3
        from backend.db import get_db_connection
        from backend.payout_scheduler import (_send_venue_charged_email,
                                              _send_payout_email)
        rconn = get_db_connection()
        try:
            rconn.row_factory = sqlite3.Row
            t_row = rconn.execute(
                "SELECT * FROM transactions WHERE id = ?",
                (txn_id,),
            ).fetchone()
            if not t_row:
                raise HTTPException(404, "Transaction not found")

            if template == 'venue_charged':
                gig = rconn.execute(
                    "SELECT venue_id FROM gigs WHERE id = ?",
                    (row["gig_id"],),
                ).fetchone()
                if not gig:
                    raise HTTPException(404, "Gig not found")
                _send_venue_charged_email(rconn, t_row, gig["venue_id"])
            else:  # artist_payout_sent
                _send_payout_email(rconn, t_row)
        finally:
            try: rconn.close()
            except Exception: pass
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Email resend failed: {e}")

    log_admin_action(
        db, admin, "payments_resend_email",
        target_table="transactions", target_id=txn_id,
        metadata={"template": template, "gig_id": row["gig_id"]},
        request=request,
    )
    db.commit()
    return {"ok": True, "dry_run": False, "template": template}

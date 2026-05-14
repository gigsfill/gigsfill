"""
Admin Payments Console — Tier 1 (read-only)
============================================
Unified searchable view of every transaction across every venue and artist.

Tier 1 is read-only — no Stripe writes. Tier 2 (refunds, mark-resolved, etc.)
will live in this same file behind separate endpoints with confirmation/audit.

Endpoints:
  GET  /api/admin/payments/search        — paginated, filterable txn list
  GET  /api/admin/payments/{txn_id}      — full details + related rows
  GET  /api/admin/payments/stats         — aggregate KPIs for the filtered set
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from backend.db import get_db
from backend.routes.admin import check_admin

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

    # Recent admin actions on this transaction or its gig (best-effort —
    # the audit_log table may not store every action with a direct txn pointer)
    audit = []
    try:
        audit_rows = db.execute(text("""
            SELECT id, admin_user_id, action_type, target_table, target_id,
                   before_state, after_state, metadata, ip_address, created_at
            FROM audit_log
            WHERE (target_table = 'transactions' AND target_id = :tid)
               OR (metadata LIKE :gid_like)
            ORDER BY id DESC
            LIMIT 20
        """), {"tid": txn_id, "gid_like": f'%"gig_id": {result["gig_id"]}%'}).mappings().all()
        audit = [dict(a) for a in audit_rows]
    except Exception:
        # audit_log may not exist on older databases — non-fatal
        audit = []

    return {
        "transaction": result,
        "siblings": siblings,
        "slots": [dict(s) for s in slots],
        "audit": audit,
    }

"""
Admin Payments Console
======================
Unified searchable view of every transaction across every venue and artist,
plus admin-initiated actions on individual rows.

Tier 1 (read-only — shipped 2026-05-13):
  GET  /api/admin/payments/search        — paginated, filterable txn list
  GET  /api/admin/payments/{txn_id}      — full details + related rows
  GET  /api/admin/payments/stats         — aggregate KPIs for the filtered set

Tier 2 (single-row mutations, shipped 2026-05-21):
  POST /api/admin/payments/{id}/refund         — full or partial venue refund
  POST /api/admin/payments/{id}/mark-resolved  — force status (whitelisted)
  POST /api/admin/payments/{id}/refire         — reset failed row to scheduled
  POST /api/admin/payments/{id}/resend-email   — replay payment-event email

Tier 3 (destructive/sensitive single-row mutations):
  POST /api/admin/payments/{id}/reverse-transfer
        Reverse an artist Connect transfer that has already gone out. Money
        comes back to the platform balance. Admin can then refund the venue
        separately if needed.
  POST /api/admin/payments/{id}/reroute-payout
        Change the destination artist on a still-scheduled child payout
        (e.g. original artist's Connect account was closed).

Tier 4 (bulk + reconciliation):
  POST /api/admin/payments/bulk
        Apply a safe action (mark-resolved / resend-email / refire) to
        multiple txns in one call. Hard-limited to actions that don't move
        money — bulk refund/reverse is intentionally NOT supported.
  GET  /api/admin/payments/reconcile
        Diff our `transactions` table against Stripe (charges, transfers,
        refunds) for a date range. Highlights status mismatches and orphans
        on either side. Read-only; admin acts on findings via single-row
        endpoints.

All write endpoints (Tier 2 + 3) accept `dry_run: true` to validate without
making the Stripe call OR the DB write. Stripe Dashboard webhook handlers in
stripe_connect.py (`charge.refunded`, `transfer.reversed`, etc.) also sync
the DB, so manual Stripe Dashboard actions and actions initiated here both
converge to the same state.
"""
import uuid


# Idempotency keys for admin-initiated Stripe writes. We used to suffix the
# key with `int(time.time())` (second-resolution), which collides on the
# common "admin double-clicks Refund" pattern — Stripe returns the first
# refund and silently no-ops the second, leaving admin thinking they double-
# refunded when only one landed. UUID4 hex gives us a fresh key per call
# while keeping the prefix readable in the Stripe Dashboard logs.
def _idem(prefix: str) -> str:
    """Build a unique idempotency key with a descriptive prefix."""
    return f"{prefix}_{uuid.uuid4().hex}"
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from backend.db import get_db
from backend.routes.admin import check_admin
from backend.utils import log_admin_action, get_all_entity_users


# ─── Admin-action email helpers ─────────────────────────────────────────────
# Refund / reverse-transfer always notify the affected venue/artist — these
# bypass user notification preferences (admin moving money is consequential).
# Best-effort: never raise if email fails.

def _format_amount(cents):
    try:
        return f"{int(cents) / 100:.2f}"
    except Exception:
        return "0.00"


def _send_admin_email(db, recipients, template_name, variables):
    """Send a template email to a list of {email, user_id} recipients,
    bypassing the per-user notification-preference check. Used for admin-
    initiated payment actions where opt-out doesn't make sense."""
    if not recipients:
        return
    try:
        # NOTE: email_service.py exposes the EmailService *class* (not a
        # module-level singleton). Instantiate it here, same pattern as
        # services/email_dispatch.py uses. Earlier code tried to import a
        # non-existent `email_service` symbol and crashed in the best-effort
        # try/except — meaning these admin-action emails never actually sent.
        from backend.email_service import EmailService, _smtp_send
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.utils import formataddr
        email_service = EmailService(db)
        tpl = email_service.get_template(template_name)
        if not tpl or not email_service.enabled:
            return
        subj = email_service.render_template(tpl['subject'], variables)
        body = email_service.render_template(tpl['body'], variables)
        import logging
        log = logging.getLogger("gigsfill.admin_payments")
        sent_to = set()
        for r in recipients:
            email = r.get('email') if isinstance(r, dict) else None
            if not email or email in sent_to:
                continue
            sent_to.add(email)
            try:
                msg = MIMEMultipart("alternative")
                msg['Subject'] = subj
                msg['From'] = (formataddr((email_service.from_name, email_service.from_email))
                               if email_service.from_name else email_service.from_email)
                msg['To'] = email
                msg['X-Mailer'] = 'GigsFill'
                msg.attach(MIMEText(body, 'html'))
                _smtp_send(email_service.smtp_server, email_service.smtp_port,
                           email_service.smtp_username, email_service.smtp_password, msg)
                # Confirmation line — without it, the success path is silent
                # and admin can't tell from logs whether the auto-notify went
                # out. Same one-liner pattern as email_dispatch.py.
                log.info(f"[ADMIN ACTION EMAIL] {template_name} sent to {email}")
            except Exception:
                # One bad recipient shouldn't block the others. The endpoint's
                # own success doesn't depend on this — admin already saw the
                # Stripe receipt in the modal.
                log.warning(
                    f"[ADMIN ACTION EMAIL] {template_name} to {email} FAILED (best-effort)",
                    exc_info=True)
    except Exception:
        import logging
        logging.getLogger("gigsfill.admin_payments").warning(
            f"admin-action email batch failed (best-effort)", exc_info=True)


def _send_venue_refund_email(db, parent_txn_id, refund_amount_cents, is_full,
                             reason, stripe_refund_id):
    """Look up venue + gig context for parent_txn_id and email all venue
    users that a refund just landed. Best-effort."""
    try:
        info = db.execute(text("""
            SELECT v.id as venue_id, v.venue_name,
                   g.date, t.venue_charge_cents
            FROM transactions t
            JOIN gigs g ON g.id = t.gig_id
            LEFT JOIN venues v ON v.id = g.venue_id
            WHERE t.id = :tid
        """), {"tid": parent_txn_id}).mappings().first()
        if not info or not info.get("venue_id"):
            return

        # Collect every distinct artist on this gig — for multi-slot gigs the
        # parent venue_charge has artist_id=NULL, so we have to walk the
        # children's artist_id columns. (Previously we joined t.artist_id and
        # g.artist_id, which were both NULL on a multi-slot gig, so the
        # venue's refund email rendered "your gig with Artist" instead of
        # listing the actual artists.)
        artist_rows = db.execute(text("""
            SELECT DISTINCT a.name
            FROM artists a
            WHERE a.id IN (
                SELECT artist_id FROM transactions
                WHERE (id = :tid OR parent_transaction_id = :tid)
                  AND artist_id IS NOT NULL
            )
              AND a.name IS NOT NULL AND a.name <> ''
            ORDER BY a.name
        """), {"tid": parent_txn_id}).fetchall()
        artist_names = [r[0] for r in artist_rows]
        artist_display = ', '.join(artist_names) if artist_names else 'the artist'

        recipients = get_all_entity_users(db, 'venue', info['venue_id'])
        variables = {
            'venue_name':       info.get('venue_name') or 'Venue',
            'venue_id':         str(info['venue_id']),
            'artist_name':      artist_display,
            'date':             str(info.get('date') or ''),
            'original_charge':  _format_amount(info.get('venue_charge_cents')),
            'refund_amount':    _format_amount(refund_amount_cents),
            'refund_type':      'full' if is_full else 'partial',
            'refund_reason':    reason or 'requested_by_customer',
            'stripe_refund_id': stripe_refund_id or '',
        }
        _send_admin_email(db, recipients, 'payment_refunded_venue', variables)
    except Exception:
        import logging
        logging.getLogger("gigsfill.admin_payments").warning(
            "_send_venue_refund_email failed (best-effort)", exc_info=True)


def _send_artist_reversal_email(db, child_txn_id, reverse_amount_cents, is_full,
                                stripe_reversal_id):
    """Look up artist + gig context for child_txn_id and email all artist
    users that a transfer just got reversed. Best-effort."""
    try:
        info = db.execute(text("""
            SELECT t.artist_id, t.artist_payout_cents,
                   g.date, v.venue_name,
                   COALESCE(a.name, a2.name) as artist_name
            FROM transactions t
            JOIN gigs g ON g.id = t.gig_id
            LEFT JOIN venues v ON v.id = g.venue_id
            LEFT JOIN artists a ON a.id = t.artist_id
            LEFT JOIN artists a2 ON a2.id = g.artist_id
            WHERE t.id = :tid
        """), {"tid": child_txn_id}).mappings().first()
        if not info or not info.get("artist_id"):
            return
        recipients = get_all_entity_users(db, 'artist', info['artist_id'])
        variables = {
            'artist_name':        info.get('artist_name') or 'Artist',
            'artist_id':          str(info['artist_id']),
            'venue_name':         info.get('venue_name') or 'Venue',
            'date':               str(info.get('date') or ''),
            'original_payout':    _format_amount(info.get('artist_payout_cents')),
            'reverse_amount':     _format_amount(reverse_amount_cents),
            'reverse_type':       'full' if is_full else 'partial',
            'stripe_reversal_id': stripe_reversal_id or '',
        }
        _send_admin_email(db, recipients, 'payment_transfer_reversed_artist', variables)
    except Exception:
        import logging
        logging.getLogger("gigsfill.admin_payments").warning(
            "_send_artist_reversal_email failed (best-effort)", exc_info=True)

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
                   commission_cents, credit_card_fee_cents,
                   artist_id, parent_transaction_id, scheduled_process_at,
                   processed_at, created_at,
                   stripe_transfer_id, stripe_payment_intent_id
            FROM transactions
            WHERE id = :pid OR parent_transaction_id = :pid
            ORDER BY (id = :pid) DESC, id ASC
        """), {"pid": result["parent_transaction_id"]}).mappings().all()
        siblings = [dict(s) for s in sibs]
    else:
        # Look for children whose parent_transaction_id = this row, plus self
        # so the frontend always has the full gig view (parent + all children)
        # regardless of which row admin clicked first.
        sibs = db.execute(text("""
            SELECT id, COALESCE(transaction_type,'single') as transaction_type,
                   status, amount_cents, venue_charge_cents, artist_payout_cents,
                   commission_cents, credit_card_fee_cents,
                   artist_id, parent_transaction_id, scheduled_process_at,
                   processed_at, created_at,
                   stripe_transfer_id, stripe_payment_intent_id
            FROM transactions
            WHERE id = :pid OR parent_transaction_id = :pid
            ORDER BY (id = :pid) DESC, id ASC
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
        refund = type("_DryRefund", (), {"id": "re_dryrun_" + uuid.uuid4().hex[:12]})()
    else:
        from backend.routes.stripe_connect import init_stripe
        stripe, _keys = init_stripe(db)

        idem_key = _idem(f"admin_refund_txn_{txn_id}")
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

        # 8. Notify the venue — always-on, bypasses prefs.
        _send_venue_refund_email(db, txn_id, amount_cents, is_full,
                                 reason, getattr(refund, "id", None))

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


# ─── Tier 3: REVERSE TRANSFER ───────────────────────────────────────────────

_REVERSIBLE_TRANSFER_STATUSES = {'transferred', 'paid'}


@router.post("/api/admin/payments/{txn_id}/reverse-transfer")
def reverse_transfer(
    txn_id: int,
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Reverse an artist Connect transfer. Optionally refund the venue in
    the same call.

    Two-step flow when also_refund_venue=true:
      1. Reverse the child transfer (pulls funds from artist Connect back
         to platform balance).
      2. If step 1 succeeds, refund the parent venue_charge (pushes funds
         from platform balance back to the venue's card).
    If step 2 fails after step 1 succeeded, the reverse stays applied and
    the response carries `refund.error`. Admin then retries the refund via
    the standard ↩ Refund action on the parent — no auto-rollback (a
    failed Stripe refund on a reversed transfer means the venue is owed
    money, which is the platform's debt, which is the safe place for the
    money to sit while admin figures out what went wrong).

    Body:
      amount_cents          (optional int) — partial reversal. Default full.
      refund_app_fee        (optional bool) — refund platform fee captured
                                              at transfer time. Default false.
      notes                 (optional str)
      dry_run               (optional bool)

      also_refund_venue     (optional bool) — also run a refund on the parent.
      refund_amount_cents   (optional int)  — default = same as reverse amount.
                                              Independent — admin can refund
                                              more/less than the reverse.
      refund_reason         (optional str)  — Stripe reason for the refund.
      refund_cancel_kids    (optional bool) — when refund is full, cancel any
                                              still-scheduled siblings. Default
                                              true for full refunds.

    Hard refusals (400):
      • Not a child artist_payout row.
      • status not in {transferred, paid}.
      • No stripe_transfer_id on the row.
      • amount_cents > artist_payout_cents or <= 0.
      • also_refund_venue with no parent venue_charge / not refundable / no PI.

    Stripe Dashboard webhooks (`transfer.reversed`, `charge.refunded`) will
    also fire and sync the DB — harmless, same status sink.
    """
    dry_run = bool(payload.get("dry_run"))

    row = db.execute(text("""
        SELECT id, gig_id, parent_transaction_id, transaction_type, status,
               artist_id, artist_payout_cents, stripe_transfer_id, notes
        FROM transactions WHERE id = :tid
    """), {"tid": txn_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Transaction not found")

    if row["parent_transaction_id"] is None and row["transaction_type"] != 'artist_payout':
        raise HTTPException(400,
            "Reverse-transfer only applies to child artist_payout rows.")
    if row["status"] not in _REVERSIBLE_TRANSFER_STATUSES:
        raise HTTPException(400,
            f"Cannot reverse status '{row['status']}'. "
            f"Reversible: {', '.join(sorted(_REVERSIBLE_TRANSFER_STATUSES))}.")
    if not row["stripe_transfer_id"]:
        raise HTTPException(400, "No stripe_transfer_id on this row.")

    payout_cents = int(row["artist_payout_cents"] or 0)
    requested = payload.get("amount_cents")
    if requested in (None, ""):
        amount_cents = payout_cents
    else:
        try:
            amount_cents = int(requested)
        except (TypeError, ValueError):
            raise HTTPException(400, "amount_cents must be an integer")
    if amount_cents <= 0:
        raise HTTPException(400, "amount_cents must be > 0")
    if amount_cents > payout_cents:
        raise HTTPException(400,
            f"Reversal amount (${amount_cents/100:.2f}) exceeds transfer "
            f"(${payout_cents/100:.2f}).")
    is_full = (amount_cents == payout_cents)

    refund_app_fee = bool(payload.get("refund_app_fee"))
    notes_in = (payload.get("notes") or "").strip()[:500]

    if dry_run:
        reversal = type("_DryReversal", (), {"id": "trr_dryrun_" + uuid.uuid4().hex[:12]})()
    else:
        from backend.routes.stripe_connect import init_stripe
        stripe, _keys = init_stripe(db)
        idem_key = _idem(f"admin_reverse_txn_{txn_id}")
        try:
            reversal = stripe.Transfer.create_reversal(
                row["stripe_transfer_id"],
                amount=amount_cents,
                refund_application_fee=refund_app_fee,
                idempotency_key=idem_key,
                metadata={
                    "txn_id": str(txn_id),
                    "gig_id": str(row["gig_id"]),
                    "initiated_by": "admin_payments_console",
                    "admin_user_id": str(getattr(admin, "id", "") or ""),
                },
            )
        except Exception as e:
            msg = getattr(e, "user_message", None) or str(e)
            raise HTTPException(502, f"Stripe transfer reversal failed: {msg}")

    flavor = "FULL" if is_full else "PARTIAL"
    rev_note = (f"Admin reverse-transfer {flavor} ${amount_cents/100:.2f} "
                f"of ${payout_cents/100:.2f} (reversal_id: "
                f"{getattr(reversal, 'id', '?')}, refund_app_fee="
                f"{refund_app_fee})"
                + (f" — {notes_in}" if notes_in else ""))

    new_status = 'payment_cancelled' if is_full else row["status"]
    if not dry_run:
        db.execute(text("""
            UPDATE transactions
               SET status = :s,
                   notes  = COALESCE(notes || ' | ', '') || :n
             WHERE id = :tid
        """), {"s": new_status, "n": rev_note, "tid": txn_id})
        db.commit()

        log_admin_action(
            db, admin, "payments_reverse_transfer",
            target_table="transactions", target_id=txn_id,
            before={"status": row["status"]},
            after={"status": new_status},
            metadata={
                "gig_id": row["gig_id"],
                "amount_cents": amount_cents,
                "transfer_cents": payout_cents,
                "is_full": is_full,
                "refund_app_fee": refund_app_fee,
                "stripe_reversal_id": getattr(reversal, "id", None),
                "notes": notes_in,
            },
            request=request,
        )
        db.commit()

        # Notify the affected artist — always-on, bypasses prefs.
        _send_artist_reversal_email(db, txn_id, amount_cents, is_full,
                                    getattr(reversal, "id", None))

    # ── Optional combined venue refund ─────────────────────────────────────
    refund_result = None
    if payload.get("also_refund_venue"):
        parent_id = row["parent_transaction_id"]
        if not parent_id:
            raise HTTPException(400,
                "also_refund_venue=true but this row has no parent_transaction_id.")
        parent = db.execute(text("""
            SELECT id, transaction_type, status, venue_charge_cents,
                   amount_cents, stripe_payment_intent_id, gig_id
            FROM transactions WHERE id = :pid
        """), {"pid": parent_id}).mappings().first()
        if not parent:
            raise HTTPException(400,
                f"Parent transaction #{parent_id} not found.")
        if (parent["transaction_type"] or "single") not in _REFUNDABLE_TXN_TYPES:
            raise HTTPException(400,
                f"Parent #{parent_id} is not a refundable type "
                f"('{parent['transaction_type']}').")
        if parent["status"] not in _REFUNDABLE_STATUSES:
            raise HTTPException(400,
                f"Parent #{parent_id} is in status '{parent['status']}' — not refundable.")
        if not parent["stripe_payment_intent_id"]:
            raise HTTPException(400,
                f"Parent #{parent_id} has no stripe_payment_intent_id — cannot refund.")

        parent_charge = int(parent["venue_charge_cents"] or 0)
        refund_req = payload.get("refund_amount_cents")
        if refund_req in (None, ""):
            r_amount = amount_cents  # default: refund the same amount we just reversed
        else:
            try:
                r_amount = int(refund_req)
            except (TypeError, ValueError):
                raise HTTPException(400, "refund_amount_cents must be an integer")
        if r_amount <= 0:
            raise HTTPException(400, "refund_amount_cents must be > 0")
        if r_amount > parent_charge:
            raise HTTPException(400,
                f"Refund amount (${r_amount/100:.2f}) exceeds parent charge "
                f"(${parent_charge/100:.2f}).")
        r_is_full = (r_amount == parent_charge)
        r_reason = (payload.get("refund_reason") or "requested_by_customer").strip()
        if r_reason not in _STRIPE_REFUND_REASONS:
            r_reason = "requested_by_customer"
        r_cancel_kids = payload.get("refund_cancel_kids")
        if r_cancel_kids is None:
            r_cancel_kids = r_is_full

        if dry_run:
            refund_result = {
                "stripe_refund_id": "re_dryrun_" + uuid.uuid4().hex[:12],
                "amount_cents": r_amount,
                "is_full": r_is_full,
                "parent_txn_id": parent_id,
                "parent_new_status": ('payment_cancelled' if r_is_full else parent["status"]),
                "reason": r_reason,
                "error": None,
            }
        else:
            try:
                from backend.routes.stripe_connect import init_stripe as _is2
                stripe2, _k2 = _is2(db)
                idem_key = _idem(f"admin_reverse_refund_txn_{parent_id}")
                refund_obj = stripe2.Refund.create(
                    payment_intent=parent["stripe_payment_intent_id"],
                    amount=r_amount,
                    reason=r_reason,
                    idempotency_key=idem_key,
                    metadata={
                        "txn_id": str(parent_id),
                        "child_txn_id": str(txn_id),
                        "gig_id": str(parent["gig_id"]),
                        "initiated_by": "admin_payments_console_reverse",
                        "admin_user_id": str(getattr(admin, "id", "") or ""),
                    },
                )
                # Update parent in same DB session
                parent_new_status = 'payment_cancelled' if r_is_full else parent["status"]
                refund_note = (f"Auto-refund alongside reverse-transfer "
                               f"({'FULL' if r_is_full else 'PARTIAL'}) "
                               f"${r_amount/100:.2f} of ${parent_charge/100:.2f} "
                               f"(refund_id: {refund_obj.id})")
                db.execute(text("""
                    UPDATE transactions
                       SET status = :s,
                           notes  = COALESCE(notes || ' | ', '') || :n
                     WHERE id = :pid
                """), {"s": parent_new_status, "n": refund_note, "pid": parent_id})

                cancelled = []
                if r_is_full and r_cancel_kids:
                    sched_kids = db.execute(text("""
                        SELECT id FROM transactions
                        WHERE parent_transaction_id = :pid AND status = 'scheduled'
                    """), {"pid": parent_id}).mappings().all()
                    for k in sched_kids:
                        db.execute(text("""
                            UPDATE transactions
                               SET status = 'payment_cancelled',
                                   notes  = COALESCE(notes || ' | ', '') || :n
                             WHERE id = :cid
                        """), {"cid": k["id"],
                               "n": f"Cancelled by combined reverse+refund on parent #{parent_id}"})
                        cancelled.append(k["id"])
                db.commit()

                log_admin_action(
                    db, admin, "payments_refund_via_reverse",
                    target_table="transactions", target_id=parent_id,
                    before={"status": parent["status"]},
                    after={"status": parent_new_status},
                    metadata={
                        "child_txn_id": txn_id,
                        "gig_id": parent["gig_id"],
                        "amount_cents": r_amount,
                        "charge_cents": parent_charge,
                        "is_full": r_is_full,
                        "stripe_refund_id": refund_obj.id,
                        "reason": r_reason,
                        "cancelled_child_payouts": cancelled,
                    },
                    request=request,
                )
                db.commit()

                # Notify the venue about the refund (artist reversal email
                # already sent above).
                _send_venue_refund_email(db, parent_id, r_amount, r_is_full,
                                         r_reason, refund_obj.id)
                refund_result = {
                    "stripe_refund_id": refund_obj.id,
                    "amount_cents": r_amount,
                    "is_full": r_is_full,
                    "parent_txn_id": parent_id,
                    "parent_new_status": parent_new_status,
                    "reason": r_reason,
                    "cancelled_child_payouts": cancelled,
                    "error": None,
                }
            except Exception as e:
                # Reverse already landed. Surface the failure; admin retries
                # the refund manually via ↩ Refund on the parent. We do NOT
                # try to roll the reversal back.
                msg = getattr(e, "user_message", None) or str(e)
                refund_result = {
                    "parent_txn_id": parent_id,
                    "error": (
                        f"Reverse succeeded but venue refund failed: {msg}. "
                        f"Retry the refund manually on transaction #{parent_id}."
                    ),
                }

    return {
        "ok": True,
        "dry_run": dry_run,
        "stripe_reversal_id": getattr(reversal, "id", None),
        "amount_cents": amount_cents,
        "is_full": is_full,
        "new_status": new_status,
        "refund": refund_result,
        "note": ("Reversal pulls funds from the artist's Connect balance back "
                 "to the platform. Venue refund "
                 + ("issued in the same call." if refund_result and not refund_result.get("error")
                    else "NOT issued — admin must run it separately if desired.")),
    }


# ─── Tier 3: REVERSE TRANSFER — BATCH (multi-slot) ──────────────────────────

# Multi-slot gigs end up as one venue_charge parent + N artist_payout children.
# Admin commonly wants to claw back several (or all) payouts at once when an
# entire gig went wrong — this endpoint reverses a caller-specified subset in
# one call, then optionally refunds the venue. Each reversal is processed
# independently (one Stripe API call per row) so a single Stripe failure on
# one child doesn't roll back the others — the response carries per-row
# success/error and the optional refund still runs as long as the caller
# requested it.
@router.post("/api/admin/payments/{parent_id}/reverse-batch")
def reverse_batch(
    parent_id: int,
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Reverse N artist_payout children of a venue_charge parent in one call.

    Body:
      reversals  (required list) — [{child_id, amount_cents}, ...].
                                    Each child must be a child of `parent_id`
                                    with status in {transferred, paid} and a
                                    stripe_transfer_id. amount_cents must be
                                    1..artist_payout_cents.
      notes      (optional str)
      dry_run    (optional bool)

      also_refund_venue   (optional bool)  — fire a refund on parent_id after
                                              the reversals, in the same call.
      refund_amount_cents (optional int)   — default = sum of reversal amounts.
      refund_reason       (optional str)   — Stripe reason. Default
                                              'requested_by_customer'.
      refund_cancel_kids  (optional bool)  — also cancel any still-scheduled
                                              child payouts on this parent.
                                              Default true when refund is full.

    Returns:
      { ok, dry_run, parent_id,
        reversals: [{child_id, ok, stripe_reversal_id?, amount_cents?, error?}, ...],
        ok_count, fail_count,
        refund: {stripe_refund_id?, amount_cents?, is_full?, error?} | null
      }
    """
    dry_run = bool(payload.get("dry_run"))
    notes_in = (payload.get("notes") or "").strip()[:500]
    reversals = payload.get("reversals") or []
    also_refund = bool(payload.get("also_refund_venue"))
    if not isinstance(reversals, list):
        raise HTTPException(400, "reversals must be a list of {child_id, amount_cents}")
    if not reversals and not also_refund:
        # Need at least one of: reversal entries OR the venue refund flag.
        # Otherwise the call is a no-op.
        raise HTTPException(400, "reversals or also_refund_venue is required")
    if len(reversals) > 50:
        raise HTTPException(400, "Max 50 reversals per batch")

    # Validate the parent up front.
    parent = db.execute(text("""
        SELECT id, transaction_type, status, venue_charge_cents, amount_cents,
               stripe_payment_intent_id, gig_id
        FROM transactions WHERE id = :pid
    """), {"pid": parent_id}).mappings().first()
    if not parent:
        raise HTTPException(404, f"Parent transaction #{parent_id} not found")
    p_type = parent["transaction_type"] or "single"
    if p_type not in _REFUNDABLE_TXN_TYPES:
        raise HTTPException(400,
            f"Parent #{parent_id} type '{p_type}' is not a venue charge")

    # Process each reversal independently — Stripe call + DB update + audit.
    # Init Stripe only when we actually need it (at least one Stripe write
    # in either the reversal loop or the optional refund step). Saves the
    # "Stripe not configured" precheck for pure dry-run + venue-only-refund
    # corner cases.
    if not dry_run and reversals:
        from backend.routes.stripe_connect import init_stripe
        stripe, _keys = init_stripe(db)

    results = []
    ok_count = 0
    fail_count = 0
    total_amount = 0

    for entry in reversals:
        if not isinstance(entry, dict):
            results.append({"child_id": entry, "ok": False, "error": "entry not a dict"})
            fail_count += 1
            continue
        try:
            child_id = int(entry.get("child_id"))
            amt = int(entry.get("amount_cents"))
        except (TypeError, ValueError):
            results.append({"child_id": entry.get("child_id"),
                            "ok": False, "error": "child_id and amount_cents must be ints"})
            fail_count += 1
            continue

        try:
            row = db.execute(text("""
                SELECT id, gig_id, parent_transaction_id, transaction_type,
                       status, artist_id, artist_payout_cents, stripe_transfer_id
                FROM transactions WHERE id = :cid
            """), {"cid": child_id}).mappings().first()
            if not row:
                raise HTTPException(404, f"Child #{child_id} not found")
            if row["parent_transaction_id"] != parent_id:
                raise HTTPException(400,
                    f"Child #{child_id} is not a child of parent #{parent_id}")
            if row["status"] not in _REVERSIBLE_TRANSFER_STATUSES:
                raise HTTPException(400,
                    f"Child #{child_id} status '{row['status']}' is not reversible")
            if not row["stripe_transfer_id"]:
                raise HTTPException(400, f"Child #{child_id} has no stripe_transfer_id")

            payout_cents = int(row["artist_payout_cents"] or 0)
            if amt <= 0 or amt > payout_cents:
                raise HTTPException(400,
                    f"Child #{child_id} amount ${amt/100:.2f} out of range "
                    f"(1..${payout_cents/100:.2f})")
            is_full = (amt == payout_cents)

            if dry_run:
                rev_id = "trr_dryrun_" + uuid.uuid4().hex[:12] + "_" + str(child_id)
            else:
                idem_key = _idem(f"admin_reverse_batch_{child_id}")
                rev = stripe.Transfer.create_reversal(
                    row["stripe_transfer_id"],
                    amount=amt,
                    idempotency_key=idem_key,
                    metadata={
                        "txn_id": str(child_id),
                        "parent_id": str(parent_id),
                        "gig_id": str(row["gig_id"]),
                        "initiated_by": "admin_payments_console_batch",
                        "admin_user_id": str(getattr(admin, "id", "") or ""),
                    },
                )
                rev_id = rev.id

                new_status = 'payment_cancelled' if is_full else row["status"]
                note = (f"Admin reverse-batch ({'FULL' if is_full else 'PARTIAL'}) "
                        f"${amt/100:.2f} of ${payout_cents/100:.2f} "
                        f"(reversal: {rev_id})"
                        + (f" — {notes_in}" if notes_in else ""))
                db.execute(text("""
                    UPDATE transactions
                       SET status = :s,
                           notes  = COALESCE(notes || ' | ', '') || :n
                     WHERE id = :tid
                """), {"s": new_status, "n": note, "tid": child_id})
                db.commit()

                log_admin_action(
                    db, admin, "payments_reverse_transfer",
                    target_table="transactions", target_id=child_id,
                    before={"status": row["status"]},
                    after={"status": new_status},
                    metadata={
                        "gig_id": row["gig_id"],
                        "parent_id": parent_id,
                        "amount_cents": amt,
                        "transfer_cents": payout_cents,
                        "is_full": is_full,
                        "stripe_reversal_id": rev_id,
                        "batch": True,
                    },
                    request=request,
                )
                db.commit()

                # Notify the affected artist for this reversed payout.
                _send_artist_reversal_email(db, child_id, amt, is_full, rev_id)

            results.append({
                "child_id": child_id,
                "ok": True,
                "stripe_reversal_id": rev_id,
                "amount_cents": amt,
                "is_full": is_full,
            })
            ok_count += 1
            total_amount += amt
        except HTTPException as e:
            results.append({"child_id": child_id, "ok": False, "error": str(e.detail)})
            fail_count += 1
        except Exception as e:
            msg = getattr(e, "user_message", None) or str(e)
            results.append({"child_id": child_id, "ok": False, "error": msg})
            fail_count += 1

    # ── Optional combined venue refund (same logic as the single-row endpoint
    # but driven by the batch's total amount as the default) ───────────────
    # Safety gate: if ANY reversal in the batch failed (and we're not dry-run),
    # block the refund so the venue isn't refunded for funds the platform
    # hasn't actually clawed back from artists. Admin sees the per-row errors
    # in the response and can either retry the failed reversals first or
    # issue the venue refund manually with full context.
    refund_result = None
    if payload.get("also_refund_venue"):
        if fail_count > 0 and not dry_run:
            refund_result = {"error":
                f"Refund skipped: {fail_count} reversal{'s' if fail_count != 1 else ''} "
                f"failed in this batch. Resolve those first, then issue the venue refund "
                f"separately via ↩ Refund on the parent."}
        elif parent["status"] not in _REFUNDABLE_STATUSES:
            refund_result = {"error":
                f"Parent #{parent_id} is in status '{parent['status']}' — not refundable."}
        elif not parent["stripe_payment_intent_id"]:
            refund_result = {"error": f"Parent #{parent_id} has no stripe_payment_intent_id."}
        else:
            parent_charge = int(parent["venue_charge_cents"] or 0)
            r_req = payload.get("refund_amount_cents")
            r_amount = int(r_req) if r_req not in (None, "") else total_amount
            if r_amount <= 0 or r_amount > parent_charge:
                refund_result = {"error":
                    f"Refund amount ${r_amount/100:.2f} out of range "
                    f"(1..${parent_charge/100:.2f})."}
            else:
                r_is_full = (r_amount == parent_charge)
                r_reason = (payload.get("refund_reason") or "requested_by_customer").strip()
                if r_reason not in _STRIPE_REFUND_REASONS:
                    r_reason = "requested_by_customer"
                r_cancel_kids = payload.get("refund_cancel_kids")
                if r_cancel_kids is None:
                    r_cancel_kids = r_is_full

                if dry_run:
                    refund_result = {
                        "stripe_refund_id": "re_dryrun_" + uuid.uuid4().hex[:12],
                        "amount_cents": r_amount,
                        "is_full": r_is_full,
                        "parent_txn_id": parent_id,
                        "parent_new_status": ('payment_cancelled' if r_is_full else parent["status"]),
                        "reason": r_reason,
                        "error": None,
                    }
                else:
                    try:
                        from backend.routes.stripe_connect import init_stripe as _is2
                        stripe2, _k2 = _is2(db)
                        ref_idem = _idem(f"admin_reverse_batch_refund_{parent_id}")
                        ref_obj = stripe2.Refund.create(
                            payment_intent=parent["stripe_payment_intent_id"],
                            amount=r_amount,
                            reason=r_reason,
                            idempotency_key=ref_idem,
                            metadata={
                                "txn_id": str(parent_id),
                                "gig_id": str(parent["gig_id"]),
                                "initiated_by": "admin_payments_console_batch",
                                "admin_user_id": str(getattr(admin, "id", "") or ""),
                            },
                        )
                        parent_new_status = 'payment_cancelled' if r_is_full else parent["status"]
                        ref_note = (f"Auto-refund alongside reverse-batch "
                                    f"({'FULL' if r_is_full else 'PARTIAL'}) "
                                    f"${r_amount/100:.2f} of ${parent_charge/100:.2f} "
                                    f"(refund: {ref_obj.id})")
                        db.execute(text("""
                            UPDATE transactions
                               SET status = :s,
                                   notes  = COALESCE(notes || ' | ', '') || :n
                             WHERE id = :pid
                        """), {"s": parent_new_status, "n": ref_note, "pid": parent_id})

                        cancelled = []
                        if r_is_full and r_cancel_kids:
                            sched_kids = db.execute(text("""
                                SELECT id FROM transactions
                                WHERE parent_transaction_id = :pid AND status = 'scheduled'
                            """), {"pid": parent_id}).mappings().all()
                            for k in sched_kids:
                                db.execute(text("""
                                    UPDATE transactions
                                       SET status = 'payment_cancelled',
                                           notes  = COALESCE(notes || ' | ', '') || :n
                                     WHERE id = :cid
                                """), {"cid": k["id"],
                                       "n": f"Cancelled by reverse-batch+refund on parent #{parent_id}"})
                                cancelled.append(k["id"])
                        db.commit()

                        log_admin_action(
                            db, admin, "payments_refund_via_reverse_batch",
                            target_table="transactions", target_id=parent_id,
                            before={"status": parent["status"]},
                            after={"status": parent_new_status},
                            metadata={
                                "gig_id": parent["gig_id"],
                                "amount_cents": r_amount,
                                "charge_cents": parent_charge,
                                "is_full": r_is_full,
                                "stripe_refund_id": ref_obj.id,
                                "reason": r_reason,
                                "cancelled_child_payouts": cancelled,
                                "batch_size": len(reversals),
                            },
                            request=request,
                        )
                        db.commit()

                        # Notify the venue about the refund. (Artist reversal
                        # emails already fired per-row inside the reversal
                        # loop above.)
                        _send_venue_refund_email(db, parent_id, r_amount, r_is_full,
                                                  r_reason, ref_obj.id)

                        refund_result = {
                            "stripe_refund_id": ref_obj.id,
                            "amount_cents": r_amount,
                            "is_full": r_is_full,
                            "parent_txn_id": parent_id,
                            "parent_new_status": parent_new_status,
                            "reason": r_reason,
                            "cancelled_child_payouts": cancelled,
                            "error": None,
                        }
                    except Exception as e:
                        msg = getattr(e, "user_message", None) or str(e)
                        refund_result = {
                            "parent_txn_id": parent_id,
                            "error": f"Reversals applied but venue refund failed: {msg}. Retry via ↩ Refund on the parent.",
                        }

    return {
        "ok": True,
        "dry_run": dry_run,
        "parent_id": parent_id,
        "reversals": results,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "refund": refund_result,
    }


# ─── Batch: MARK ROWS RESOLVED + RESEND EMAILS ──────────────────────────────

# Gig-wide variants of the single-row Mark Resolved and Resend Email actions.
# Same audit + same per-row validation as the single-row endpoints, just
# applied across several rows of the same gig in one admin click. Admin can
# pick a different target status per row (different from /bulk, which forces
# one status for all rows). Reason is shared across the batch — it's one
# logical action.


@router.post("/api/admin/payments/mark-resolved-batch")
def mark_resolved_batch(
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Body:
      rows     (required list) — [{txn_id, new_status}, ...]
      reason   (required str)  — shared across the batch, min 5 chars.
      dry_run  (optional bool)
    Returns: {ok, dry_run, ok_count, fail_count, results: [...]}
    """
    rows = payload.get("rows") or []
    reason = (payload.get("reason") or "").strip()[:1000]
    dry_run = bool(payload.get("dry_run"))
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "rows must be a non-empty list")
    if len(rows) > 50:
        raise HTTPException(400, "Max 50 rows per batch")
    if len(reason) < 5:
        raise HTTPException(400, "reason is required (min 5 chars)")

    results = []
    ok_count = 0
    fail_count = 0
    for entry in rows:
        if not isinstance(entry, dict):
            results.append({"txn_id": entry, "ok": False, "error": "entry not a dict"})
            fail_count += 1
            continue
        tid_raw = entry.get("txn_id")
        ns = (entry.get("new_status") or "").strip()
        try:
            tid = int(tid_raw)
        except (TypeError, ValueError):
            results.append({"txn_id": tid_raw, "ok": False, "error": "txn_id must be an integer"})
            fail_count += 1
            continue
        if ns not in _MARK_RESOLVED_TARGETS:
            results.append({"txn_id": tid, "ok": False,
                            "error": f"new_status '{ns}' not allowed"})
            fail_count += 1
            continue
        try:
            row = db.execute(text("""
                SELECT id, gig_id, status FROM transactions WHERE id = :tid
            """), {"tid": tid}).mappings().first()
            if not row:
                raise HTTPException(404, "not found")
            if row["status"] == ns:
                raise HTTPException(400, f"already '{ns}'")

            if not dry_run:
                note = (f"Admin mark-resolved (batch): {row['status']} → {ns} "
                        f"({getattr(admin, 'email', 'admin')}) — {reason}")
                db.execute(text("""
                    UPDATE transactions
                       SET status = :s, notes = COALESCE(notes || ' | ', '') || :n
                     WHERE id = :tid
                """), {"s": ns, "n": note, "tid": tid})
                db.commit()
                log_admin_action(
                    db, admin, "payments_mark_resolved",
                    target_table="transactions", target_id=tid,
                    before={"status": row["status"]},
                    after={"status": ns},
                    metadata={"gig_id": row["gig_id"], "reason": reason, "batch": True},
                    request=request,
                )
                db.commit()
            results.append({"txn_id": tid, "ok": True,
                            "from": row["status"], "to": ns})
            ok_count += 1
        except HTTPException as e:
            results.append({"txn_id": tid, "ok": False, "error": str(e.detail)})
            fail_count += 1
        except Exception as e:
            results.append({"txn_id": tid, "ok": False, "error": str(e)})
            fail_count += 1

    return {"ok": True, "dry_run": dry_run, "ok_count": ok_count,
            "fail_count": fail_count, "results": results}


@router.post("/api/admin/payments/resend-email-batch")
def resend_email_batch(
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Body:
      txn_ids  (required list[int])  — rows to resend for.
      dry_run  (optional bool)
    Template is auto-derived per row:
      venue_charge / single  → venue_charged
      artist_payout           → artist_payout_sent
    Each row also has to be in a status the template applies to (paid /
    charged for venue, transferred / paid for payout). Otherwise that row
    fails with a friendly per-row error.
    """
    txn_ids = payload.get("txn_ids") or []
    dry_run = bool(payload.get("dry_run"))
    if not isinstance(txn_ids, list) or not txn_ids:
        raise HTTPException(400, "txn_ids must be a non-empty list")
    if len(txn_ids) > 50:
        raise HTTPException(400, "Max 50 rows per batch")

    results = []
    ok_count = 0
    fail_count = 0

    # Open one raw sqlite connection for the run since the underlying sender
    # functions expect sqlite Row-shaped data.
    rconn = None
    if not dry_run:
        try:
            import sqlite3
            from backend.db import get_db_connection
            from backend.payout_scheduler import (_send_venue_charged_email,
                                                  _send_payout_email)
            rconn = get_db_connection()
            rconn.row_factory = sqlite3.Row
        except Exception as e:
            raise HTTPException(502, f"Email subsystem unavailable: {e}")

    try:
        for tid_raw in txn_ids:
            try:
                tid = int(tid_raw)
            except (TypeError, ValueError):
                results.append({"txn_id": tid_raw, "ok": False, "error": "txn_id must be an integer"})
                fail_count += 1
                continue
            try:
                row = db.execute(text("""
                    SELECT id, gig_id, parent_transaction_id, transaction_type, status
                    FROM transactions WHERE id = :tid
                """), {"tid": tid}).mappings().first()
                if not row:
                    raise HTTPException(404, "not found")
                ttype = row["transaction_type"] or "single"
                if ttype in ('venue_charge', 'single'):
                    if row["status"] not in ('paid', 'charged'):
                        raise HTTPException(400, f"venue row status '{row['status']}' has no email template")
                    template = "venue_charged"
                elif ttype == 'artist_payout':
                    if row["status"] not in ('transferred', 'paid'):
                        raise HTTPException(400, f"payout row status '{row['status']}' has no email template")
                    template = "artist_payout_sent"
                else:
                    raise HTTPException(400, f"unsupported transaction_type '{ttype}'")

                if not dry_run:
                    t_row = rconn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()
                    if not t_row:
                        raise HTTPException(404, "not found (raw conn)")
                    if template == 'venue_charged':
                        gig = rconn.execute("SELECT venue_id FROM gigs WHERE id = ?", (row["gig_id"],)).fetchone()
                        if not gig:
                            raise HTTPException(404, "gig not found")
                        _send_venue_charged_email(rconn, t_row, gig["venue_id"])
                    else:
                        _send_payout_email(rconn, t_row)

                    log_admin_action(
                        db, admin, "payments_resend_email",
                        target_table="transactions", target_id=tid,
                        metadata={"template": template, "gig_id": row["gig_id"], "batch": True},
                        request=request,
                    )
                    db.commit()

                results.append({"txn_id": tid, "ok": True, "template": template})
                ok_count += 1
            except HTTPException as e:
                results.append({"txn_id": tid, "ok": False, "error": str(e.detail)})
                fail_count += 1
            except Exception as e:
                results.append({"txn_id": tid, "ok": False, "error": str(e)})
                fail_count += 1
    finally:
        if rconn:
            try: rconn.close()
            except Exception: pass

    return {"ok": True, "dry_run": dry_run, "ok_count": ok_count,
            "fail_count": fail_count, "results": results}


# ─── Tier 3: RE-ROUTE PAYOUT ────────────────────────────────────────────────

@router.post("/api/admin/payments/{txn_id}/reroute-payout")
def reroute_payout(
    txn_id: int,
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Change the destination artist on a still-scheduled artist_payout
    child row. Used when the original artist's Connect account closed /
    suspended and the gig needs to be paid out to a different account
    (e.g. a band's secondary account, or a platform-owned holding account).

    Body:
      new_artist_id  (required int) — must own a Connect account with
                                       onboarding complete.
      reason         (required str) — min 5 chars.
      dry_run        (optional bool)

    Hard refusals (400):
      • Not a child artist_payout row.
      • status != 'scheduled' (can't re-route in-flight or completed).
      • new_artist_id is missing / unknown / has no Connect / onboarding incomplete.
      • new_artist_id == current artist_id.
    """
    dry_run    = bool(payload.get("dry_run"))
    new_artist = payload.get("new_artist_id")
    reason     = (payload.get("reason") or "").strip()[:1000]
    if not new_artist:
        raise HTTPException(400, "new_artist_id is required")
    try:
        new_artist_id = int(new_artist)
    except (TypeError, ValueError):
        raise HTTPException(400, "new_artist_id must be an integer")
    if len(reason) < 5:
        raise HTTPException(400, "reason is required (min 5 chars)")

    row = db.execute(text("""
        SELECT id, gig_id, parent_transaction_id, transaction_type, status,
               artist_id
        FROM transactions WHERE id = :tid
    """), {"tid": txn_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Transaction not found")
    if row["parent_transaction_id"] is None and row["transaction_type"] != 'artist_payout':
        raise HTTPException(400,
            "Re-route only applies to child artist_payout rows.")
    if row["status"] != 'scheduled':
        raise HTTPException(400,
            f"Cannot re-route a payout in status '{row['status']}'. "
            f"Only 'scheduled' payouts can be re-routed; for transferred ones "
            f"use reverse-transfer first.")
    if int(row["artist_id"] or 0) == new_artist_id:
        raise HTTPException(400,
            "new_artist_id matches current artist — nothing to change.")

    # Validate destination artist has Connect onboarding complete
    dest = db.execute(text("""
        SELECT a.id, a.name,
               eps.stripe_connect_account_id,
               eps.stripe_connect_onboarding_complete
        FROM artists a
        LEFT JOIN entity_payment_settings eps
               ON eps.entity_type = 'artist' AND eps.entity_id = a.id
        WHERE a.id = :aid
    """), {"aid": new_artist_id}).mappings().first()
    if not dest:
        raise HTTPException(400, f"Artist #{new_artist_id} not found.")
    if not dest["stripe_connect_account_id"]:
        raise HTTPException(400,
            f"Artist #{new_artist_id} ({dest['name']}) has no Stripe Connect account.")
    if not dest["stripe_connect_onboarding_complete"]:
        raise HTTPException(400,
            f"Artist #{new_artist_id} ({dest['name']}) hasn't finished Connect "
            f"onboarding — payouts can't go there yet.")

    note = (f"Admin re-route: artist #{row['artist_id']} → "
            f"#{new_artist_id} ({dest['name']}). Reason: {reason}")

    if not dry_run:
        db.execute(text("""
            UPDATE transactions
               SET artist_id = :new_aid,
                   notes     = COALESCE(notes || ' | ', '') || :n
             WHERE id = :tid
        """), {"new_aid": new_artist_id, "n": note, "tid": txn_id})
        db.commit()

        log_admin_action(
            db, admin, "payments_reroute_payout",
            target_table="transactions", target_id=txn_id,
            before={"artist_id": row["artist_id"]},
            after={"artist_id": new_artist_id},
            metadata={"gig_id": row["gig_id"], "reason": reason,
                      "destination_artist_name": dest["name"]},
            request=request,
        )
        db.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "from_artist_id": row["artist_id"],
        "to_artist_id": new_artist_id,
        "to_artist_name": dest["name"],
    }


# ─── Tier 4: BULK ACTIONS ───────────────────────────────────────────────────

# Hard-coded list of actions safe for bulk apply. Money-moving actions
# (refund, reverse-transfer) are intentionally NOT here — those require
# per-row review.
_BULK_ACTIONS = {'mark-resolved', 'resend-email', 'refire'}
_BULK_MAX = 100


@router.post("/api/admin/payments/bulk")
def bulk_action(
    payload: dict,
    request: Request,
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Apply one of the safe single-row actions to many txns at once.

    Body:
      txn_ids  (required list[int])    — up to _BULK_MAX rows.
      action   (required str)          — one of _BULK_ACTIONS.
      params   (optional dict)         — extra params for the action
                                          (e.g. new_status for mark-resolved).
      dry_run  (optional bool)

    Returns: { results: [{txn_id, ok, error?}], ok_count, fail_count }
    """
    txn_ids = payload.get("txn_ids") or []
    action  = (payload.get("action") or "").strip()
    params  = payload.get("params") or {}
    dry_run = bool(payload.get("dry_run"))

    if action not in _BULK_ACTIONS:
        raise HTTPException(400,
            f"action must be one of: {', '.join(sorted(_BULK_ACTIONS))}")
    if not isinstance(txn_ids, list) or not txn_ids:
        raise HTTPException(400, "txn_ids must be a non-empty list")
    if len(txn_ids) > _BULK_MAX:
        raise HTTPException(400, f"Max {_BULK_MAX} txns per bulk call")

    # Validate action-specific params up front so we fail fast.
    if action == 'mark-resolved':
        if not (params.get("new_status") and params.get("reason")):
            raise HTTPException(400,
                "mark-resolved requires params.new_status and params.reason")
    elif action == 'resend-email':
        if not params.get("template"):
            raise HTTPException(400, "resend-email requires params.template")

    # Dispatch to the single-row handlers — same validation, same audit log,
    # same dry_run support. Errors on individual rows don't abort the batch;
    # they're collected and returned per-row.
    results = []
    ok_count = 0
    fail_count = 0
    for tid in txn_ids:
        try:
            tid_int = int(tid)
        except (TypeError, ValueError):
            results.append({"txn_id": tid, "ok": False, "error": "invalid id"})
            fail_count += 1
            continue
        try:
            if action == 'mark-resolved':
                r = mark_resolved(tid_int, {
                    "new_status": params["new_status"],
                    "reason":     params["reason"],
                }, request, admin, db)
            elif action == 'refire':
                r = refire_payment(tid_int, {
                    "notes":   params.get("notes", ""),
                    "dry_run": dry_run,
                }, request, admin, db)
            elif action == 'resend-email':
                r = resend_email(tid_int, {
                    "template": params["template"],
                    "dry_run":  dry_run,
                }, request, admin, db)
            results.append({"txn_id": tid_int, "ok": True, "result": r})
            ok_count += 1
        except HTTPException as e:
            results.append({"txn_id": tid_int, "ok": False, "error": str(e.detail)})
            fail_count += 1
        except Exception as e:
            results.append({"txn_id": tid_int, "ok": False, "error": str(e)})
            fail_count += 1

    return {
        "ok": True,
        "dry_run": dry_run,
        "action": action,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "results": results,
    }


# ─── Tier 4: STRIPE ⇄ DB RECONCILIATION ─────────────────────────────────────

# Cap the per-call scope so a runaway range can't burn Stripe rate-limit
# budget on a single button click. Admin can re-run with a narrower range
# to dig into specific days.
_RECON_MAX_TXNS = 200


# NOTE: Path uses /reports/reconcile (not /reconcile) on purpose. FastAPI
# matches routes in registration order, and the earlier-registered GET
# /api/admin/payments/{txn_id} with `txn_id: int` would catch any single-
# segment path and fail with 422 ("Input should be a valid integer") before
# this handler could be tried. Putting "reconcile" under /reports/ avoids the
# clash without having to reorder the file.
@router.get("/api/admin/payments/reports/reconcile")
def reconcile(
    from_date: str = Query(..., description="YYYY-MM-DD, gig date inclusive"),
    to_date:   str = Query(..., description="YYYY-MM-DD, gig date inclusive"),
    only_mismatches: bool = Query(True),
    admin=Depends(check_admin),
    db=Depends(get_db),
):
    """Diff our transactions table against Stripe for a gig-date window.

    Pulls each txn that has a stripe_payment_intent_id or stripe_transfer_id,
    fetches the corresponding Stripe object, and compares:
      • Our `status` vs Stripe's lifecycle state.
      • Our amount vs Stripe's amount.
      • Whether refunds/reversals we know about exist on Stripe (and vice versa).

    Hard-limited to _RECON_MAX_TXNS rows per call so this is bounded — for
    larger windows, narrow the date range. Read-only; admin acts on findings
    via the single-row endpoints (Mark Resolved, Refund, Reverse Transfer).

    Returns:
      { window: {from,to}, scanned, ok_count, mismatch_count, no_stripe_id,
        mismatches: [{txn_id, our_status, stripe_state, summary}, ...] }
    """
    # Pull candidate rows
    rows = db.execute(text("""
        SELECT t.id, t.gig_id, t.transaction_type, t.status,
               t.amount_cents, t.venue_charge_cents, t.artist_payout_cents,
               t.stripe_payment_intent_id, t.stripe_transfer_id,
               g.date as gig_date, v.venue_name,
               COALESCE(a.name, a2.name) as artist_name
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        LEFT JOIN venues  v  ON v.id = g.venue_id
        LEFT JOIN artists a  ON a.id = t.artist_id
        LEFT JOIN artists a2 ON a2.id = g.artist_id
        WHERE date(g.date) BETWEEN date(:fd) AND date(:td)
        ORDER BY g.date ASC, t.id ASC
        LIMIT :lim
    """), {"fd": from_date, "td": to_date, "lim": _RECON_MAX_TXNS + 1}).mappings().all()

    truncated = len(rows) > _RECON_MAX_TXNS
    rows = list(rows)[:_RECON_MAX_TXNS]

    # Map our status set to a coarse Stripe-equivalent for comparison.
    # We don't try to match every status exactly — that's hopeless. The goal
    # is to flag the obvious "we say paid, Stripe says canceled" mismatches.
    def expected_stripe_state(status, is_transfer):
        if is_transfer:
            if status in ('transferred', 'paid'):           return 'succeeded'
            if status == 'payment_cancelled':               return 'reversed'
            if status in ('pending_transfer', 'scheduled'): return 'pending'
            return None
        # Venue charge / single
        if status in ('paid', 'charged', 'transferred'):    return 'succeeded'
        if status == 'payment_cancelled':                   return 'canceled_or_refunded'
        if status in ('scheduled', 'pending'):              return 'requires_action_or_processing'
        if status in ('payment_failed', 'charge_retry'):    return 'failed_or_canceled'
        return None

    from backend.routes.stripe_connect import init_stripe
    try:
        stripe, _keys = init_stripe(db)
    except HTTPException:
        # No Stripe keys → return what we can show without API calls.
        return {
            "window": {"from": from_date, "to": to_date},
            "scanned": 0, "ok_count": 0, "mismatch_count": 0,
            "no_stripe_id": 0, "truncated": truncated,
            "error": "Stripe not configured — cannot reconcile.",
            "mismatches": [],
        }

    mismatches = []
    ok_count = 0
    no_stripe = 0

    for r in rows:
        ref_id = r["stripe_payment_intent_id"] or r["stripe_transfer_id"]
        if not ref_id:
            no_stripe += 1
            continue

        is_transfer = bool(r["stripe_transfer_id"]) and not r["stripe_payment_intent_id"]
        expected = expected_stripe_state(r["status"], is_transfer)
        observed = None
        observed_amount_cents = None
        notes_bits = []

        try:
            if is_transfer:
                obj = stripe.Transfer.retrieve(ref_id)
                # Transfers don't have a single "status" — they're either
                # reversed (transfer.reversed = amount_reversed > 0) or not.
                amt = getattr(obj, "amount", None) or 0
                rev = getattr(obj, "amount_reversed", None) or 0
                observed_amount_cents = amt
                if rev >= amt and amt > 0:
                    observed = 'reversed'
                elif rev > 0:
                    observed = 'partially_reversed'
                    notes_bits.append(f"Stripe shows ${rev/100:.2f} of ${amt/100:.2f} reversed")
                else:
                    observed = 'succeeded'
            else:
                obj = stripe.PaymentIntent.retrieve(ref_id)
                stripe_status = getattr(obj, "status", None)
                amt = getattr(obj, "amount", None) or 0
                amt_refunded = (getattr(obj, "amount_refunded", None)
                                or getattr(obj, "amount_received", None) and 0
                                or 0)
                # Be defensive — PI doesn't always carry amount_refunded directly;
                # the canonical source is the charges sub-collection.
                try:
                    charges = getattr(obj, "charges", None)
                    if charges and getattr(charges, "data", None):
                        for ch in charges.data:
                            amt_refunded += getattr(ch, "amount_refunded", 0) or 0
                except Exception:
                    pass
                observed_amount_cents = amt
                if stripe_status == 'succeeded' and amt_refunded >= amt > 0:
                    observed = 'canceled_or_refunded'
                    notes_bits.append('PI fully refunded on Stripe')
                elif stripe_status == 'succeeded':
                    observed = 'succeeded'
                    if amt_refunded > 0:
                        notes_bits.append(f"Stripe shows ${amt_refunded/100:.2f} partial refund")
                elif stripe_status in ('canceled', 'requires_payment_method'):
                    observed = 'canceled_or_refunded'
                elif stripe_status in ('processing', 'requires_action'):
                    observed = 'requires_action_or_processing'
                else:
                    observed = stripe_status or 'unknown'
        except Exception as e:
            observed = 'STRIPE_ERROR'
            notes_bits.append(str(e)[:120])

        amount_mismatch = False
        if observed_amount_cents is not None:
            our_amt = (r["venue_charge_cents"] if not is_transfer
                       else r["artist_payout_cents"]) or 0
            if our_amt and observed_amount_cents and observed_amount_cents != our_amt:
                amount_mismatch = True
                notes_bits.append(
                    f"Amount differs: our ${our_amt/100:.2f} vs Stripe ${observed_amount_cents/100:.2f}"
                )

        is_match = (expected == observed) and not amount_mismatch
        if is_match:
            ok_count += 1
            if only_mismatches:
                continue

        mismatches.append({
            "txn_id":      r["id"],
            "gig_id":      r["gig_id"],
            "gig_date":    str(r["gig_date"]),
            "venue_name":  r["venue_name"],
            "artist_name": r["artist_name"],
            "transaction_type": r["transaction_type"],
            "our_status":  r["status"],
            "stripe_state": observed,
            "expected":    expected,
            "stripe_ref":  ref_id,
            "amount_mismatch": amount_mismatch,
            "summary":     "; ".join(notes_bits) if notes_bits else
                           (None if is_match else f"Our '{r['status']}' ≠ Stripe '{observed}'"),
            "match":       is_match,
        })

    return {
        "window": {"from": from_date, "to": to_date},
        "scanned": len(rows),
        "ok_count": ok_count,
        "mismatch_count": sum(1 for m in mismatches if not m["match"]),
        "no_stripe_id": no_stripe,
        "truncated": truncated,
        "max_per_call": _RECON_MAX_TXNS,
        "mismatches": mismatches,
    }

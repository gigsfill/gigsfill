from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import text, bindparam
from backend.db import get_db
from datetime import date, timedelta, datetime, timezone
from backend.routes.auth import get_current_user, get_optional_user
import logging


# Audit fix (May 2026): canonical `utcnow_naive` lives in backend.utils now.
# Local alias kept so existing call sites in this file continue to work
# without a global rename pass.
from backend.utils import utcnow_naive as _utcnow_naive

# Services — centralized logic replacing copy-pasted blocks
from backend.services.gig_cleanup import cleanup_gig_records, delete_gig_completely, cleanup_hold_records
from backend.services.notification_service import (
    format_time_12hr, notify_gig_booked, notify_gig_cancelled,
    notify_all_entity_users_cancelled, create_notification
)
from backend.services.email_dispatch import (
    send_booking_emails, send_cancellation_emails, format_email_date,
    send_approval_request_emails, send_approval_decision_emails,
    format_pay_summary_with_sign, slot_has_door_terms,
)
from backend.rate_limiter import limiter


def _enrich_pay_summary(rows):
    """Mutate each dict in `rows` (or a single dict) so it carries a
    `pay_summary` key — door-aware "$X guarantee + Y% of door" or flat
    "$X.XX". Idempotent: existing pay_summary is left alone. Accepts a
    list, a single dict, or None (returns input unchanged in odd cases).
    Use to enrich slot / gig dicts before returning from a route.
    """
    if rows is None:
        return rows
    items = rows if isinstance(rows, list) else [rows]
    for r in items:
        if not isinstance(r, dict):
            continue
        if r.get('pay_summary'):
            continue
        r['pay_summary'] = format_pay_summary_with_sign(r)
        # Convenience boolean for frontend
        if 'is_door_deal' not in r:
            r['is_door_deal'] = slot_has_door_terms(r)
    return rows


def _get_flyer_helpers():
    """Lazy import to avoid circular/load-order issues"""
    try:
        from backend.routes.flyers import auto_create_flyer, auto_update_flyer_artist
        return auto_create_flyer, auto_update_flyer_artist
    except Exception:
        return (lambda *a, **kw: None), (lambda *a, **kw: None)


def _delete_flyer_if_no_bookings_remain(db, gig_id: int, cancelled_artist_id=None):
    """Delete the gig-specific flyer ONLY if the gig has zero booked artists left.

    Rationale: for multi-slot gigs, the venue may have invested time creating a
    custom flyer with multiple artist images/details. If only ONE slot is
    cancelled (other slots still booked), we preserve the flyer so the venue
    doesn't lose that work. We only delete when the gig is fully open again.

    When the flyer is PRESERVED and `cancelled_artist_id` is provided, also
    strip any logo objects tagged with that artist's id — leaves the rest of
    the venue's design work intact.

    Called from all cancel paths in this file. Single-slot gigs naturally hit
    this when their one booked slot is cancelled (count drops to zero).
    """
    try:
        # Count slots that still have an artist booked
        # (status='booked' is the canonical "an artist is committed" state).
        booked_count = db.execute(
            text("""
                SELECT COUNT(*) FROM gig_slots
                WHERE gig_id = :gid
                  AND artist_id IS NOT NULL
                  AND status = 'booked'
            """),
            {"gid": gig_id}
        ).scalar() or 0

        if booked_count == 0:
            db.execute(
                text("DELETE FROM flyers WHERE gig_id = :gid AND is_template = 0"),
                {"gid": gig_id}
            )
            db.commit()
            logger.info(f"[FLYER] Deleted gig-specific flyer for gig {gig_id} (no bookings remain)")
        else:
            logger.info(f"[FLYER] Preserved flyer for gig {gig_id} ({booked_count} booked slot(s) remain)")
            # FIX (May 2026): strip the cancelled artist's logo from the preserved flyer.
            if cancelled_artist_id:
                _remove_artist_logo_from_flyer(db, gig_id, int(cancelled_artist_id))
    except Exception as _fe:
        logger.warning(f"[FLYER] _delete_flyer_if_no_bookings_remain failed for gig {gig_id}: {_fe}")


def _remove_artist_logo_from_flyer(db, gig_id: int, artist_id: int):
    """Remove a specific artist's logo objects from the gig's flyer canvas.

    FIX (May 2026): when a multi-slot gig has one slot cancelled and the flyer
    is preserved (because other slots remain booked), the cancelled artist's
    logo would otherwise still appear on the flyer. The frontend tags each
    artist-specific logo with `_tplArtistId` when added via the picker. This
    helper strips out objects matching the cancelled artist_id, leaving the
    rest of the venue's design work intact.

    No-op if no flyer exists for this gig, or if the flyer's canvas_data has
    no objects tagged with this artist_id.
    """
    try:
        row = db.execute(
            text("SELECT id, canvas_data FROM flyers WHERE gig_id = :gid AND is_template = 0 LIMIT 1"),
            {"gid": gig_id}
        ).mappings().first()
        if not row or not row["canvas_data"]:
            return  # No flyer, nothing to strip

        import json as _json
        try:
            canvas = _json.loads(row["canvas_data"])
        except Exception:
            return  # Malformed JSON — leave alone

        objects = canvas.get("objects") if isinstance(canvas, dict) else None
        if not isinstance(objects, list):
            return

        before = len(objects)
        # Keep objects that are NOT tagged with this artist_id
        kept = [o for o in objects if not (
            isinstance(o, dict)
            and o.get("_tplArtistId") is not None
            and int(o.get("_tplArtistId")) == int(artist_id)
        )]
        removed = before - len(kept)
        if removed == 0:
            return  # Nothing to remove

        canvas["objects"] = kept
        # Also clear thumbnail_data: it's a JPEG snapshot taken at the venue's last
        # save, so it still contains the cancelled artist's logo. The public flyer
        # endpoint (gigs.py:4920) prefers thumbnail_data over canvas_data — leaving
        # the stale JPEG would render the cancelled artist on every public view
        # until the venue manually re-saves. Clearing it forces the endpoint to
        # fall through to canvas_data live rendering, which is now correct.
        db.execute(
            text("UPDATE flyers SET canvas_data = :cd, thumbnail_data = '', updated_at = CURRENT_TIMESTAMP WHERE id = :fid"),
            {"cd": _json.dumps(canvas), "fid": row["id"]}
        )
        db.commit()
        logger.info(f"[FLYER] Removed {removed} object(s) tagged artist_id={artist_id} from gig {gig_id}'s flyer (thumbnail invalidated)")
    except Exception as _e:
        logger.warning(f"[FLYER] _remove_artist_logo_from_flyer failed for gig {gig_id} artist {artist_id}: {_e}")

logger = logging.getLogger("gigsfill.gigs")

def _record_artist_cancellation(db, gig_id, artist_id):
    """Audit fix (May 2026 part 9): track every cancellation in gig_cancelled_artists
    so the blast filter can exclude ALL previously-cancelled artists. Also writes the
    legacy gigs.last_cancelled_artist_id (which only retains the most-recent canceller —
    insufficient for multi-slot gigs where multiple artists may have cancelled the same gig).
    """
    if not gig_id or not artist_id:
        return
    try:
        db.execute(
            text("UPDATE gigs SET last_cancelled_artist_id = :aid WHERE id = :gid"),
            {"aid": artist_id, "gid": gig_id}
        )
        # Idempotent insert — UNIQUE(gig_id, artist_id) blocks duplicates
        try:
            db.execute(
                text("INSERT INTO gig_cancelled_artists (gig_id, artist_id) VALUES (:gid, :aid)"),
                {"gid": gig_id, "aid": artist_id}
            )
        except Exception:
            # Duplicate or backfill conflict — safe to ignore
            pass
    except Exception as _rae:
        logger.warning(f"_record_artist_cancellation failed for gig {gig_id} artist {artist_id}: {_rae}")


def _get_effective_pay(db, venue_id, artist_id, published_pay):
    """Return effective pay: published pay or the venue's per-artist preferred override
    (whichever is higher when an override is set).

    The override only applies when the relationship is 'approved' — a pending request,
    a denied row, a revoked row (which still carries the old override columns), or no
    row at all all fall back to published_pay.
    """
    pay = float(published_pay or 0)
    if not artist_id:
        return pay
    override = db.execute(
        text("SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid AND status = 'approved'"),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()
    if override and override.get("pay_dollars_override") is not None:
        override_pay = float(override["pay_dollars_override"]) + float(override.get("pay_cents_override") or 0) / 100
        if override_pay > pay:
            pay = override_pay
    return pay


def _recompute_gig_fees(db, gig_id):
    """
    Recompute the parent venue_charge and all artist_payout children for a gig
    based on currently-booked slots and current platform settings.

    Fee model (gig-level, not per-slot):
      - total_fee = max(SUM(slot pays) * platform_fee_percent, platform_min_fee)
        — applied ONCE for the gig, not separately for each slot
      - Split per platform_fee_split into venue_fee + artist_fee
      - artist_fee distributed PROPORTIONALLY by each artist's pay (so every
        artist nets the same % of their gig pay)
      - Integer-cents math; rounding remainder absorbed by the last child so
        sums tie exactly

    Guards:
      - No-op if parent is past 'scheduled' status (real money has moved
        or is committed — recomputing would corrupt accounting)
      - No-op if no active children (caller is expected to delete the parent)

    Caller is responsible for db.commit().
    """
    parent = db.execute(
        text("""SELECT id, status FROM transactions
                WHERE gig_id = :gid
                  AND transaction_type = 'venue_charge'
                  AND status NOT IN ('payment_cancelled')"""),
        {"gid": gig_id}
    ).mappings().first()
    if not parent:
        return
    if parent["status"] != "scheduled":
        # Audit fix #13 (May 2026): log the skip so future incidents leave a
        # trail. Silent skip is intentional safety (real money has moved or is
        # committed) but if a recompute is REQUESTED on a charged/paid parent,
        # something upstream may be wrong (e.g. cancel triggered after charge).
        logger.warning(
            f"[FEES] _recompute_gig_fees skipped for gig {gig_id}: parent txn {parent['id']} "
            f"status={parent['status']!r} (only 'scheduled' is recomputable)"
        )
        return

    children = db.execute(
        text("""SELECT id, artist_id, amount_cents
                FROM transactions
                WHERE parent_transaction_id = :pid
                  AND transaction_type = 'artist_payout'
                  AND status NOT IN ('payment_cancelled')
                ORDER BY id"""),
        {"pid": parent["id"]}
    ).mappings().all()
    if not children:
        return

    total_amount = sum(c["amount_cents"] or 0 for c in children)
    if total_amount <= 0:
        return

    settings = {}
    for r in db.execute(
        text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_fee_percent', 'platform_fee_split', 'platform_min_fee')")
    ).fetchall():
        settings[r[0]] = r[1]
    fee_pct       = float(settings.get('platform_fee_percent', '10')) / 100
    # Per-venue fee_pct override — must mirror _create_booking_transaction
    # so settle/recompute uses the same rate as booking. Look up the venue
    # from the parent transaction's gig.
    try:
        _vid_row = db.execute(
            text("SELECT venue_id FROM gigs WHERE id = :gid"), {"gid": gig_id}
        ).mappings().first()
        if _vid_row:
            _ov = db.execute(
                text("SELECT fee_pct_override FROM venue_payment_overrides WHERE venue_id = :vid"),
                {"vid": _vid_row["venue_id"]}
            ).mappings().first()
            if _ov and _ov.get("fee_pct_override") is not None:
                fee_pct = float(_ov["fee_pct_override"]) / 100
    except Exception:
        pass
    min_fee_cents = int(float(settings.get('platform_min_fee', '0')) * 100)
    fee_split     = settings.get('platform_fee_split', 'split')

    total_fee = max(int(total_amount * fee_pct), min_fee_cents)
    if fee_split == 'venue_only':
        venue_fee_total, artist_fee_total = total_fee, 0
    elif fee_split == 'artist_only':
        venue_fee_total, artist_fee_total = 0, total_fee
    else:
        venue_fee_total  = total_fee // 2
        artist_fee_total = total_fee - venue_fee_total

    venue_charge_total = total_amount + venue_fee_total

    db.execute(
        text("""UPDATE transactions
                SET amount_cents = :am,
                    venue_charge_cents = :vc,
                    commission_cents = :cm
                WHERE id = :pid"""),
        {"am": total_amount, "vc": venue_charge_total, "cm": total_fee, "pid": parent["id"]}
    )

    # Snapshot pre-existing child amounts so we can log any material changes
    # for audit visibility (May 2026 part 9 audit). A new slot booking on an
    # existing gig recomputes the whole-gig fee proportionally; existing
    # artists may see their netted payout shift by a few cents from rounding,
    # or by a non-trivial amount if min-fee crossed a threshold. Log so we
    # can answer "why did my pay change" from a single grep.
    _prior_children = {c["id"]: int(c["amount_cents"] or 0) for c in children}
    _prior_payouts = {}
    for _pc in db.execute(
        text("""SELECT id, artist_payout_cents, commission_cents FROM transactions
                WHERE parent_transaction_id = :pid AND transaction_type = 'artist_payout'"""),
        {"pid": parent["id"]}
    ).mappings().all():
        _prior_payouts[_pc["id"]] = (int(_pc["artist_payout_cents"] or 0),
                                     int(_pc["commission_cents"] or 0))

    # Distribute artist_fee_total proportionally by pay; remainder lands on the last child
    # so the children's commission_cents and artist_fee allocations sum exactly.
    artist_fee_assigned = 0
    commission_assigned = 0
    n = len(children)
    for i, c in enumerate(children):
        if i < n - 1:
            this_artist_fee  = (artist_fee_total * c["amount_cents"]) // total_amount
            this_commission  = (total_fee        * c["amount_cents"]) // total_amount
        else:
            this_artist_fee  = artist_fee_total - artist_fee_assigned
            this_commission  = total_fee        - commission_assigned
        artist_fee_assigned += this_artist_fee
        commission_assigned += this_commission
        artist_payout = max(0, c["amount_cents"] - this_artist_fee)
        prior = _prior_payouts.get(c["id"], (None, None))
        if prior[0] is not None and (prior[0] != artist_payout or prior[1] != this_commission):
            logger.info(
                f"[FEES] gig {gig_id} child txn {c['id']} (artist {c['artist_id']}) re-rated: "
                f"payout ${prior[0]/100:.2f}→${artist_payout/100:.2f}, "
                f"commission ${prior[1]/100:.2f}→${this_commission/100:.2f}"
            )
        db.execute(
            text("UPDATE transactions SET commission_cents = :cm, artist_payout_cents = :ap WHERE id = :cid"),
            {"cm": this_commission, "ap": artist_payout, "cid": c["id"]}
        )

    logger.info(
        f"[gig {gig_id}] recomputed fees: pay=${total_amount/100:.2f}, "
        f"fee=${total_fee/100:.2f}, venue charged=${venue_charge_total/100:.2f}, "
        f"artists={n}"
    )


def _flag_zero_pay_booking(db, venue_id, gig_id, artist_id, slot_id, reason):
    """Record a $0-pay booking event for off-platform-pay detection.

    Triggered when a venue books an artist with amount_cents=0 — either
    flat-pay $0 or a pure-door deal (guarantee_cents=0, door_pct>0)
    that may never be settled (so the artist's real pay is happening
    off-platform via cash at the gig).

    GigsFill still earns the platform_min_fee, so this isn't blocked —
    but repeated $0 bookings from the same venue suggest the venue is
    routing real pay off-site, and admin should know. We:
      1. Bump the venue's zero_pay_booking_count + last_zero_pay_at.
      2. Insert one in-app notification per admin user so the activity
         shows up in their notifications feed.
      3. logger.info for the audit trail.
    Admin can then bump venue_payment_overrides.fee_pct_override to
    recoup the lost percentage on this venue's future bookings.

    Caller is responsible for db.commit().
    """
    try:
        # Upsert the override row's counter — venue_payment_overrides has
        # a UNIQUE(venue_id) constraint. Try update first, then insert if
        # no row exists (portable across SQLite + Postgres without needing
        # ON CONFLICT syntax differences).
        from datetime import datetime as _dt
        _now = _dt.utcnow()
        _r = db.execute(
            text("""UPDATE venue_payment_overrides
                    SET zero_pay_booking_count = COALESCE(zero_pay_booking_count, 0) + 1,
                        last_zero_pay_at = :now
                    WHERE venue_id = :vid"""),
            {"vid": venue_id, "now": _now}
        )
        if (_r.rowcount or 0) == 0:
            db.execute(
                text("""INSERT INTO venue_payment_overrides
                        (venue_id, payments_suspended, zero_pay_booking_count, last_zero_pay_at, notes)
                        VALUES (:vid, 0, 1, :now, :note)"""),
                {"vid": venue_id, "now": _now,
                 "note": "Auto-created on first zero-pay booking flag"}
            )

        # Look up venue name + artist name for the notification body.
        _v = db.execute(
            text("SELECT venue_name FROM venues WHERE id = :vid"), {"vid": venue_id}
        ).mappings().first()
        _a = db.execute(
            text("SELECT name FROM artists WHERE id = :aid"), {"aid": artist_id}
        ).mappings().first()
        _vname = (_v and _v.get("venue_name")) or f"Venue #{venue_id}"
        _aname = (_a and _a.get("name")) or f"Artist #{artist_id}"

        # Get current count for the notification message.
        _cnt = db.execute(
            text("SELECT zero_pay_booking_count FROM venue_payment_overrides WHERE venue_id = :vid"),
            {"vid": venue_id}
        ).scalar() or 1

        # Notify every admin user (is_admin is TEXT in this codebase).
        # Use the canonical check: LOWER(is_admin) IN ('true','1').
        _admins = db.execute(
            text("""SELECT id FROM users
                    WHERE LOWER(COALESCE(CAST(is_admin AS TEXT), 'false')) IN ('true','1')""")
        ).mappings().all()
        try:
            from backend.services.notification_service import create_notification
            for _au in _admins:
                create_notification(
                    db,
                    user_id=_au["id"],
                    notification_type="admin_zero_pay_booking",
                    title=f"⚠ Zero-pay booking at {_vname}",
                    message=(
                        f"{_vname} just booked {_aname} for ${0.00:.2f} "
                        f"({reason}). This venue's running count: {_cnt}. "
                        f"GigsFill will still collect the platform minimum fee, "
                        f"but real pay may be flowing off-platform. "
                        f"Consider raising this venue's fee_pct_override."
                    ),
                    gig_id=gig_id,
                    venue_id=venue_id,
                    artist_id=artist_id,
                )
        except Exception as _ne:
            logger.warning(f"[ZERO_PAY_FLAG] notification dispatch failed: {_ne}")

        logger.info(
            "[ZERO_PAY_FLAG] venue=%s artist=%s gig=%s slot=%s reason=%s count=%d",
            venue_id, artist_id, gig_id, slot_id, reason, _cnt
        )
    except Exception as e:
        # Never block the booking itself if the flagging plumbing fails.
        logger.warning(f"[ZERO_PAY_FLAG] tracking failed for venue {venue_id}: {e}", exc_info=True)


def _create_booking_transaction(db, gig_id, venue_id, artist_id, pay_amount, gig_date, slot_id=None):
    """
    Create/update transaction records when a slot is booked.

    MODEL:
    - Multi-slot gig: one 'venue_charge' parent transaction, plus one
      'artist_payout' child per booked artist. After insert/update, the parent
      and ALL children are normalized by _recompute_gig_fees() so the platform
      fee is computed once at the gig level and split proportionally — not
      per-slot (which double-applied the min-fee).
    - Single-slot gig: same shape (parent + 1 child); recompute is a no-op
      since the gig-level math equals the per-slot math when there's one slot.
    """
    try:
        # Door-deal defensive: if this slot is a door deal, the canonical
        # booking-time charge is the GUARANTEE in cents, regardless of
        # what pay_amount the caller passed or what gs.pay happens to be.
        # The door % share gets added at /settle (door_settle.py) by
        # bumping this scheduled transaction's amount_cents before the
        # payout scheduler actually runs the Stripe charge. Without this
        # check, a stale gs.pay (or a wrong pay_amount handed in) could
        # over- or under-charge for door deals; this anchors the booking
        # row to the deal's intent.
        if slot_id:
            _deal_row = db.execute(
                text("SELECT deal_type, guarantee_cents FROM gig_slots WHERE id = :sid"),
                {"sid": slot_id}
            ).mappings().first()
            if _deal_row and (_deal_row.get("deal_type") or "").lower() == "door":
                _g_cents = int(_deal_row.get("guarantee_cents") or 0)
                pay_amount = _g_cents / 100.0
                # Skip override application for door deals — overrides are
                # for raising flat pay; the door guarantee is the contract.
            else:
                pay_amount = _get_effective_pay(db, venue_id, artist_id, pay_amount)
        else:
            pay_amount = _get_effective_pay(db, venue_id, artist_id, pay_amount)

        # Check if venue is on free trial — Stripe charge is skipped (free trial
        # venues pay artists directly), but we still record a 'free_trial' audit
        # row so analytics joining gigs ⨝ transactions don't treat free-trial
        # gigs as missing data. The row carries amount_cents so reporting can
        # still show "what would have been charged" for free-trial bookings.
        free_trial = db.execute(
            text("SELECT payments_suspended FROM venue_payment_overrides WHERE venue_id = :vid"),
            {"vid": venue_id}
        ).mappings().first()
        if free_trial and free_trial["payments_suspended"]:
            try:
                _ft_amt = int(float(pay_amount or 0) * 100)
                _venue_user = db.execute(text("SELECT user_id FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
                _artist_user = db.execute(text("SELECT user_id FROM artists WHERE id = :aid"), {"aid": artist_id}).mappings().first()
                if _venue_user and _artist_user:
                    db.execute(
                        text("""
                            INSERT INTO transactions
                                (gig_id, from_user_id, to_user_id, artist_id,
                                 amount_cents, venue_charge_cents, artist_payout_cents, commission_cents,
                                 credit_card_fee_cents, payment_method_type, status,
                                 created_at, notes, transaction_type)
                            VALUES
                                (:gig_id, :from_uid, :to_uid, :artist_id,
                                 :amount, 0, :amount, 0,
                                 0, 'free_trial', 'free_trial',
                                 :now, :notes, 'free_trial')
                        """),
                        {
                            "gig_id": gig_id,
                            "from_uid": _venue_user["user_id"],
                            "to_uid": _artist_user["user_id"],
                            "artist_id": artist_id,
                            "amount": _ft_amt,
                            "now": _utcnow_naive(),
                            "notes": f"Free-trial booking — venue {venue_id} pays artist directly",
                        }
                    )
                    db.commit()
            except Exception as _ft_err:
                logger.warning(f"[FREE_TRIAL] could not record audit row: {_ft_err}")
            logger.info(f"SKIPPED Stripe transaction: venue {venue_id} is on free trial — direct payment (audit row inserted)")
            return

        # Read platform settings (Test Mode removed Jul 1 2026 — no longer
        # reads `payments_enabled`; every booking writes status='scheduled').
        settings = {}
        for r in db.execute(
            text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_fee_percent', 'platform_fee_split', 'platform_min_fee', 'payment_processing_hour')")
        ).fetchall():
            settings[r[0]] = r[1]

        fee_pct       = float(settings.get('platform_fee_percent', '10')) / 100
        # Per-venue fee_pct override — admin tool. When set, the venue
        # pays the override rate instead of the platform default. Used
        # to recoup lost revenue from venues that route real pay off-
        # platform via $0 listings ($0 flat / $0-guarantee door deals
        # are flagged by _flag_zero_pay_booking below; admin can then
        # bump THIS venue's percentage in venue_payment_overrides.fee_pct_override).
        try:
            _ov = db.execute(
                text("SELECT fee_pct_override FROM venue_payment_overrides WHERE venue_id = :vid"),
                {"vid": venue_id}
            ).mappings().first()
            if _ov and _ov.get("fee_pct_override") is not None:
                fee_pct = float(_ov["fee_pct_override"]) / 100
        except Exception:
            pass
        min_fee_cents = int(float(settings.get('platform_min_fee', '0')) * 100)
        fee_split     = settings.get('platform_fee_split', 'split')
        tx_status     = 'scheduled'

        amount_cents = int(float(pay_amount or 0) * 100)
        # Pure-door deals (guarantee_cents=0, door_pct>0) legitimately
        # book at $0 — the artist's pay is determined entirely by door
        # receipts settled after the show. Without this carve-out the
        # `<= 0` short-circuit would skip transaction creation, and
        # /api/gigs/{id}/slots/{sid}/settle would later find no row to
        # bump → the artist would receive no platform payout.
        # Detect by re-reading the slot's deal columns when slot_id is
        # known. Flat-pay $0 rows are still skipped (no booking should
        # truly be free for the venue under flat terms).
        _is_door_zero = False
        if slot_id and amount_cents <= 0:
            try:
                _dz = db.execute(
                    text("SELECT deal_type, door_pct FROM gig_slots WHERE id = :sid"),
                    {"sid": slot_id}
                ).mappings().first()
                if _dz and (_dz.get("deal_type") or "").lower() == "door" and int(_dz.get("door_pct") or 0) > 0:
                    _is_door_zero = True
            except Exception:
                pass
        if amount_cents < 0 or (amount_cents == 0 and not _is_door_zero):
            return

        # Flag $0 bookings — flat-pay $0 OR pure-door (no guarantee, % door)
        # both leave GigsFill collecting only the platform minimum fee. Real
        # pay may be flowing off-platform via cash at the gig. The flag
        # bumps a per-venue counter and pings admin so persistent offenders
        # can have their fee_pct_override raised manually.
        if amount_cents == 0:
            _reason = "pure-door, no guarantee" if _is_door_zero else "flat $0 pay"
            _flag_zero_pay_booking(db, venue_id, gig_id, artist_id, slot_id, _reason)

        # Per-artist fee calculation
        total_fee_cents = max(int(amount_cents * fee_pct), min_fee_cents)
        if fee_split == 'venue_only':
            venue_fee, artist_fee = total_fee_cents, 0
        elif fee_split == 'artist_only':
            venue_fee, artist_fee = 0, total_fee_cents
        else:
            venue_fee  = total_fee_cents // 2
            artist_fee = total_fee_cents - venue_fee

        artist_payout_cents = max(0, amount_cents - artist_fee)  # never negative

        # User IDs
        venue_user  = db.execute(text("SELECT user_id FROM venues WHERE id = :vid"),   {"vid": venue_id}).mappings().first()
        artist_user = db.execute(text("SELECT user_id FROM artists WHERE id = :aid"),  {"aid": artist_id}).mappings().first()
        if not venue_user or not artist_user:
            logger.info(f"SKIPPED no user: gig={gig_id}, venue_id={venue_id}, artist_id={artist_id}")
            return

        # Payout date: day after gig at platform_payout_hour, in venue's local tz, stored as naive UTC.
        # Per-venue tz is required so a NY venue's 5pm fires at 21:00 UTC and a CA venue's 5pm
        # fires at 00:00 UTC, instead of all venues firing at 17:00 in the platform tz.
        # See doc Changelog 2026-05-04 (original fix) and 2026-05-07 (regression + re-fix).
        if gig_date:
            from backend.utils import get_venue_timezone_str
            from zoneinfo import ZoneInfo
            payout_hour = int(settings.get('payment_processing_hour') or 17)
            venue_tz_str = get_venue_timezone_str(db, venue_id)
            gig_date_obj = datetime.strptime(str(gig_date)[:10], "%Y-%m-%d").date()
            local_dt = datetime.combine(
                gig_date_obj + timedelta(days=1),
                datetime.min.time().replace(hour=payout_hour),
            ).replace(tzinfo=ZoneInfo(venue_tz_str))
            payout_date = local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        else:
            payout_date  = _utcnow_naive() + timedelta(days=2)

        # Check whether other slots on this gig already have transactions
        existing_charge = db.execute(
            text("SELECT id, amount_cents, venue_charge_cents, commission_cents FROM transactions WHERE gig_id = :gid AND transaction_type = 'venue_charge' AND status NOT IN ('payment_cancelled')"),
            {"gid": gig_id}
        ).mappings().first()

        # Check for duplicate artist payout
        existing_payout = db.execute(
            text("SELECT id FROM transactions WHERE gig_id = :gid AND artist_id = :aid AND transaction_type IN ('artist_payout', 'single') AND status NOT IN ('payment_cancelled')"),
            {"gid": gig_id, "aid": artist_id}
        ).mappings().first()
        if existing_payout:
            logger.info(f"SKIPPED duplicate payout: gig={gig_id}, artist={artist_id}")
            return

        if existing_charge:
            # ── Multi-slot: parent already exists; values will be normalized by
            # _recompute_gig_fees() after the new child is inserted below. ──
            #
            # Audit fix (Jun 2026): refuse to add a child to a parent that's
            # already past 'scheduled'/'test'. _recompute_gig_fees would
            # silently no-op for a 'charged'/'paid'/'transferred' parent
            # (it has to — money has moved), leaving the parent's total
            # short by this artist's pay AND the new child stranded outside
            # both the pending-charge and stalled-transfer queries. If the
            # stalled-transfer loop ever picks the child up it transfers
            # money the parent never raised, eating into the platform's
            # Connect balance. Bail out early with a clear error so the
            # booking gate surfaces it instead of silently ghosting.
            _parent_status = db.execute(
                text("SELECT status FROM transactions WHERE id = :pid"),
                {"pid": existing_charge["id"]}
            ).scalar()
            if _parent_status not in ("scheduled",):
                logger.error(
                    f"[BOOKING] gig {gig_id} artist {artist_id}: parent venue_charge "
                    f"#{existing_charge['id']} already in status {_parent_status!r} — "
                    f"refusing new payout child to prevent ghost transaction"
                )
                from fastapi import HTTPException as _HE
                raise _HE(409, (
                    "This gig's payment has already been processed. "
                    "Contact support to add this artist — a new charge needs to be "
                    "raised separately."
                ))
            parent_id = existing_charge["id"]
        else:
            # Check if there are already other booked slots (means this is a multi-slot gig
            # and we're adding the first transaction — or it truly is a single-slot gig).
            # We'll create a venue_charge parent regardless; if it ends up being single-slot
            # the type is updated to 'single' at the end if there's only 1 artist.
            # Jul 2026 audit fix: wrap the INSERT to catch IntegrityError
            # from the uidx_transactions_venue_charge_per_gig partial UNIQUE
            # (added in db.py). Under Postgres the post-INSERT race-loser
            # cleanup below was not atomic — two concurrent bookings could
            # both pass the existing_charge SELECT and both INSERT before
            # either committed. The UNIQUE now makes the second INSERT
            # fail; here we catch, re-read the winner, and reuse it.
            from sqlalchemy.exc import IntegrityError
            try:
                # 2026-07-25: RETURNING id inline (was: separate
                # last_insert_rowid, per-connection and vulnerable to pool
                # swaps returning a stale id from another connection).
                our_parent_id = db.execute(
                    text("""
                        INSERT INTO transactions
                            (gig_id, from_user_id, to_user_id, artist_id,
                             amount_cents, venue_charge_cents, artist_payout_cents, commission_cents,
                             credit_card_fee_cents, payment_method_type, status,
                             scheduled_process_at, created_at, notes, transaction_type)
                        VALUES
                            (:gig_id, :from_uid, :from_uid, NULL,
                             :amount, :venue_charge, 0, :commission,
                             0, 'stripe', :status,
                             :scheduled, :now, :notes, 'venue_charge') RETURNING id
                    """),
                    {
                        "gig_id":       gig_id,
                        "from_uid":     venue_user["user_id"],
                        "amount":       amount_cents,
                        "venue_charge": amount_cents + venue_fee,
                        "commission":   total_fee_cents,
                        "status":       tx_status,
                        "scheduled":    payout_date,
                        "now":          _utcnow_naive(),
                        "notes":        f"Gig {gig_id} — consolidated venue charge",
                    }
                ).scalar()
            except IntegrityError as _uv:
                # Another concurrent booking beat us to the parent INSERT.
                # Roll back this session's dirty state and grab the winner.
                logger.warning(f"[BOOKING_RACE] gig {gig_id}: uidx blocked duplicate parent INSERT — reading winner")
                try: db.rollback()
                except Exception: pass
                _winner = db.execute(
                    text("SELECT id FROM transactions WHERE gig_id = :gid AND transaction_type = 'venue_charge' AND status NOT IN ('payment_cancelled') ORDER BY id ASC LIMIT 1"),
                    {"gid": gig_id}
                ).mappings().first()
                if not _winner:
                    logger.error(f"[BOOKING_RACE] gig {gig_id}: IntegrityError but no winner found — surfacing")
                    raise
                our_parent_id = _winner["id"]
            # Audit fix (May 2026 part 5): post-insert race-loser cleanup.
            # Two concurrent bookings on different slots of the same multi-slot
            # gig can both pass the existing_charge SELECT above and both INSERT
            # a parent venue_charge — the scheduler would then double-charge the
            # venue. Now we re-SELECT for ALL non-cancelled venue_charge rows on
            # this gig and keep only the lowest id; if our INSERT wasn't the
            # winner, we delete it and reuse the established parent.
            _all_parents = db.execute(
                text("SELECT id FROM transactions WHERE gig_id = :gid AND transaction_type = 'venue_charge' AND status NOT IN ('payment_cancelled') ORDER BY id ASC"),
                {"gid": gig_id}
            ).fetchall()
            if _all_parents and len(_all_parents) > 1:
                _canonical = _all_parents[0][0]
                if our_parent_id != _canonical:
                    logger.warning(f"[BOOKING_RACE] gig {gig_id}: lost venue_charge parent race — keeping #{_canonical}, deleting our #{our_parent_id}")
                    db.execute(text("DELETE FROM transactions WHERE id = :id AND transaction_type = 'venue_charge'"), {"id": our_parent_id})
                    parent_id = _canonical
                else:
                    parent_id = our_parent_id
            else:
                parent_id = our_parent_id
            logger.info(f"Created venue_charge txn {parent_id}: ${(amount_cents + venue_fee)/100:.2f}")

        # ── Always create an artist_payout child row ──
        # FIX (May 2026): Initial status MUST be 'scheduled', NOT 'pending_transfer'.
        # Previously this used 'pending_transfer' which is the same status the
        # scheduler's retry-stalled-transfers query looks for. Result: brand-new
        # children would get treated as "stalled" and transfer immediately on the
        # next scheduler tick — paying the artist BEFORE the venue was charged.
        # 'scheduled' = "waiting for normal processing flow"
        # 'pending_transfer' = "transfer was attempted and is awaiting retry"
        # The post-charge transfer flow (_transfer_to_artists) accepts both.
        db.execute(
            text("""
                INSERT INTO transactions
                    (gig_id, from_user_id, to_user_id, artist_id,
                     amount_cents, venue_charge_cents, artist_payout_cents, commission_cents,
                     credit_card_fee_cents, payment_method_type, status,
                     scheduled_process_at, created_at, notes,
                     transaction_type, parent_transaction_id, slot_id)
                VALUES
                    (:gig_id, :from_uid, :to_uid, :artist_id,
                     :amount, 0, :artist_payout, :commission,
                     0, 'stripe', 'scheduled',
                     :scheduled, :now, :notes,
                     'artist_payout', :parent_id, :slot_id)
            """),
            {
                "gig_id":        gig_id,
                "from_uid":      venue_user["user_id"],
                "to_uid":        artist_user["user_id"],
                "artist_id":     artist_id,
                "amount":        amount_cents,
                "artist_payout": artist_payout_cents,
                "commission":    total_fee_cents,
                "scheduled":     payout_date,
                "now":           _utcnow_naive(),
                "notes":         f"Slot {slot_id}" if slot_id else f"Artist {artist_id}",
                "parent_id":     parent_id,
                "slot_id":       slot_id,
            }
        )
        logger.info(f"Created artist_payout txn: gig={gig_id}, artist={artist_id}, payout=${artist_payout_cents/100:.2f}")

        # Normalize parent + all children to the gig-level fee model.
        # This is the single source of truth for fee math on multi-slot gigs;
        # the per-slot values written above are placeholders that get overwritten.
        _recompute_gig_fees(db, gig_id)

        db.commit()

    except Exception as e:
        logger.error(f"Error creating transaction: {e}", exc_info=True)

router = APIRouter()


def _ensure_approval_columns(db):
    """Add approval_requested_at and approval_token columns if not present.
    Only commits when a column is actually added to avoid disrupting caller transactions.

    Audit fix (May 2026 part 6): the previous implementation used SQLite-only
    `PRAGMA table_info(...)`, which silently failed on Postgres — so the
    columns were never added AND the `pending_approval_tokens` CREATE TABLE
    inside the same try-block also never ran. Now branches on the engine,
    and the canonical schema declarations live in `db.py:setup_database()`
    so this function is a defensive backstop for in-flight live deploys."""
    try:
        from backend.db import _IS_POSTGRES
        if _IS_POSTGRES:
            cols = [r[0] for r in db.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name='gigs'"
            )).fetchall()]
            slot_cols = [r[0] for r in db.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name='gig_slots'"
            )).fetchall()]
        else:
            cols = [r[1] for r in db.execute(text("PRAGMA table_info(gigs)")).fetchall()]
            slot_cols = [r[1] for r in db.execute(text("PRAGMA table_info(gig_slots)")).fetchall()]
        if 'approval_requested_at' not in cols:
            db.execute(text("ALTER TABLE gigs ADD COLUMN approval_requested_at TEXT"))
            db.commit()
        if 'approval_token' not in cols:
            db.execute(text("ALTER TABLE gigs ADD COLUMN approval_token TEXT"))
            db.commit()
        if 'approval_requested_at' not in slot_cols:
            db.execute(text("ALTER TABLE gig_slots ADD COLUMN approval_requested_at TEXT"))
            db.commit()
        # Backstop create — setup_database() is the canonical declaration site.
        # Jul 2026 audit (B-C2): added `expires_at` so a replayable approval
        # token can't sit valid forever. All approve/deny endpoints check
        # this on read; expired tokens 410.
        try:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS pending_approval_tokens (
                    token TEXT PRIMARY KEY,
                    gig_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                )
            """))
            # Additive migration for existing installs.
            try:
                _cols = {r[1] for r in db.execute(text("PRAGMA table_info(pending_approval_tokens)")).fetchall()}
                if "expires_at" not in _cols:
                    db.execute(text("ALTER TABLE pending_approval_tokens ADD COLUMN expires_at TEXT"))
            except Exception:
                pass
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_pending_approval_gig_artist ON pending_approval_tokens(gig_id, artist_id)"))
            db.commit()
        except Exception as _te:
            logger.warning(f"_ensure_approval_columns: pending_approval_tokens setup: {_te}")
    except Exception as e:
        logger.warning(f"_ensure_approval_columns: {e}")


def _is_same_day_booking(gig_date_str: str, gig_start_time: str = None,
                         venue_id: int = None) -> bool:
    """Return True if gig start is within 36 hours from now (requires venue approval).

    Audit fix (May 2026 part 9): when a venue_id is provided, evaluate
    "same day / within 36h" in the VENUE's timezone — not the platform's.
    A Hawaii venue's 11 PM gig is the day before a 7 AM "tomorrow" in NY,
    and treating it in NY-local time was producing wrong answers for venues
    far from the platform tz. Falls back to platform tz / server local when
    venue_id is missing or the venue has no derivable timezone.
    """
    try:
        from datetime import datetime as _dt
        from backend.db import SessionLocal as _SL
        _tz_str = None
        if venue_id:
            try:
                from backend.utils import get_venue_timezone_str
                _db = _SL()
                try:
                    _tz_str = get_venue_timezone_str(_db, venue_id)
                finally:
                    _db.close()
            except Exception:
                _tz_str = None
        if not _tz_str:
            try:
                _db = _SL()
                try:
                    _tz_str = _db.execute(text(
                        "SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'"
                    )).scalar() or "America/Los_Angeles"
                finally:
                    _db.close()
            except Exception:
                _tz_str = "America/Los_Angeles"

        try:
            import pytz as _pytz
            _tz = _pytz.timezone(_tz_str)
            _now = _dt.now(_tz)
        except Exception:
            _now = _dt.now()

        gig_date = str(gig_date_str)[:10]
        gig_time = str(gig_start_time or "00:00")[:5]
        gig_dt_str = f"{gig_date}T{gig_time}"
        try:
            if hasattr(_now, 'tzinfo') and _now.tzinfo:
                import pytz as _pytz2
                gig_dt = _pytz2.timezone(str(_now.tzinfo)).localize(
                    _dt.fromisoformat(gig_dt_str)
                )
            else:
                gig_dt = _dt.fromisoformat(gig_dt_str)
        except Exception:
            gig_dt = _dt.fromisoformat(gig_dt_str)

        hours_until = (gig_dt - _now).total_seconds() / 3600
        return 0 <= hours_until <= 36
    except Exception:
        # Fallback: same-day check
        try:
            from datetime import date as _d
            return str(gig_date_str)[:10] == _d.today().isoformat()
        except Exception:
            return False


def _venue_requires_same_day_approval(db, venue_id: int) -> bool:
    """Return True if the venue wants to gatekeep same-day bookings
    (i.e. force a pending_venue_approval hold before the artist is confirmed).

    2026-08-10 rewrite — moved from user-level to venue-level. Previously
    this read the venue OWNER's `email_preferences.venue_booking_approval_request`
    row, which conflated "does the gate fire" (policy) with "does the
    owner want an email" (notification) and forced multi-venue owners
    into a single global setting. Now:

      • POLICY (does the gate fire?) → venues.require_same_day_approval,
        editable per-venue by any authorized team member on the venue's
        Email Notifications tab.
      • NOTIFICATION (does THIS user want an email when the gate fires?) →
        email_preferences.venue_booking_approval_request, per-user opt-out.

    A one-shot backfill on deploy copies each venue owner's old email
    pref into the new column so existing behavior is preserved.

    Defaults to True (safe): venue not found OR column NULL → require approval.
    """
    try:
        row = db.execute(text("""
            SELECT COALESCE(require_same_day_approval, 1) as req
              FROM venues
             WHERE id = :vid
        """), {"vid": venue_id}).first()
        if not row:
            return True  # Venue not found → default to safer behavior
        return bool(int(row[0] or 0))
    except Exception:
        # If anything goes wrong reading the pref, default to the pre-fix
        # behavior (require approval) so we never silently lose the gate.
        return True


# CREATE GIG (VENUE)
def _validate_door_slots(slots: list) -> None:
    """Door slots must carry BOTH a positive guarantee_cents AND a
    positive door_pct (Jun 2026 — tightened from 'at least one' to
    'both required'). Raise HTTPException(400) with a per-slot message
    on the first offender so the frontend can surface it inline."""
    for i, s in enumerate(slots or []):
        if (s.get("deal_type") or "flat").lower() != "door":
            continue
        gua = int(s.get("guarantee_cents") or 0)
        pct = int(s.get("door_pct") or 0)
        if gua <= 0 or pct <= 0:
            raise HTTPException(400,
                f"Slot {i + 1}: Door Split needs BOTH a Guarantee Pay and a % of door. Enter both, or switch back to flat pay.")


@router.post("/venues/{venue_id}/gigs")
def create_gig(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    # PDF + Hold + Recurring incompatibility (Jul 2026 Phase 5).
    # PDF contracts (pdf_upload / per_gig_pdf) can't be bulk-signed:
    # each gig is a separate document the artist must download + sign +
    # upload individually. Bundling N dates into one signature on a
    # multi-date contract isn't a thing in the existing PDF pipeline.
    # Refuse the combo at create-time so the venue gets an actionable
    # error before staging the series — vs. the artist hitting it when
    # they try to sign 30 minutes later.
    if (data.get("hold_artist_ids") or []) and data.get("is_recurring"):
        _pdf_ct = db.execute(
            text("""SELECT contract_type FROM venue_contracts
                    WHERE venue_id = :vid AND is_active = 1
                      AND require_for_booking = 1
                      AND contract_type IN ('pdf_upload', 'per_gig_pdf')
                    LIMIT 1"""),
            {"vid": venue_id}
        ).first()
        if _pdf_ct:
            raise HTTPException(
                400,
                "Hold + Recurring isn't supported for PDF contracts. PDFs can't "
                "be bulk-signed across many dates in one step. To use this combo, "
                "switch your venue contract to digital (Custom Builder or "
                "Auto-Generated) in Venue Settings → Contracts. Or create this "
                "series without Hold, and artists will sign each PDF individually "
                "when they book."
            )
    try:
        slots = data.get("slots", [])
        _validate_door_slots(slots)

        # Derive gig start/end from slots.
        # PROD BUG (May 10 2026): the original sorted slots by string
        # start_time. For overnight gigs (slot 1: 23:00-01:00, slot 2:
        # 01:00-03:00) the lexical sort puts "01:00" before "23:00", so
        # slot 2 became "first" and parent gigs.start_time was set to the
        # later slot's start — breaking every overnight-slot heuristic
        # in the frontend.
        # Fix: respect the venue-defined slot_number ordering (chronological
        # by construction). slot 1 is always the earliest; the last
        # slot_number's end is always the gig's chronological end.
        if slots:
            ordered = sorted(slots, key=lambda s: int(s.get("slot_number") or 0) or 0)
            # slot_number is 1-indexed; if missing, fall back to incoming
            # array order (which the venue-create-gigs UI guarantees is
            # chronological as the user added rows).
            if not any((s.get("slot_number") or 0) for s in slots):
                ordered = slots
            start_time = ordered[0].get("start_time")
            end_time = ordered[-1].get("end_time")
        else:
            start_time = data.get("start_time")
            end_time = data.get("end_time")

        # ── Overlap check ────────────────────────────────────────────────────
        windows_to_check = []
        for sl in slots:
            st, et = sl.get("start_time"), sl.get("end_time")
            if st and et:
                windows_to_check.append((st, et))
        if not windows_to_check and start_time and end_time:
            windows_to_check.append((start_time, end_time))

        if windows_to_check:
            gig_date = data.get("date")
            existing = db.execute(
                text("""
                    SELECT g.id, g.start_time, g.end_time, g.title,
                           gs.start_time as slot_start, gs.end_time as slot_end
                    FROM gigs g
                    LEFT JOIN gig_slots gs ON gs.gig_id = g.id
                    WHERE g.venue_id = :vid AND g.date = :date
                      AND g.status NOT IN ('cancelled','deleted')
                """),
                {"vid": venue_id, "date": gig_date}
            ).mappings().all()

            def overlaps(s1, e1, s2, e2):
                """True if (s1,e1) overlaps (s2,e2). 4pm–7pm vs 7pm–9pm is NOT an overlap."""
                # Treat as strings HH:MM — works for same-day times
                return s1 < e2 and e1 > s2

            existing_windows = set()
            for row in existing:
                if row["slot_start"] and row["slot_end"]:
                    existing_windows.add((row["slot_start"], row["slot_end"]))
                elif row["start_time"] and row["end_time"]:
                    existing_windows.add((row["start_time"], row["end_time"]))

            for (ns, ne) in windows_to_check:
                for (es, ee) in existing_windows:
                    if overlaps(ns, ne, es, ee):
                        raise HTTPException(
                            409,
                            f"This gig's time ({ns}–{ne}) overlaps with an existing gig "
                            f"at this venue on the same day ({es}–{ee}). "
                            "Please choose a different time."
                        )
        # ────────────────────────────────────────────────────────────────────

        # 2026-07-25: RETURNING id inline (was: separate last_insert_rowid,
        # per-connection and vulnerable to pool swaps returning a stale
        # id from another pool connection).
        gig_id = db.execute(
            text("""
                INSERT INTO gigs
                    (venue_id, artist_id, date, start_time, end_time, title, pay, notes, status, artist_type, band_formats, styles,
                     is_recurring, recurring_group_id, recurring_interval_weeks, recurring_days_of_week,
                     recurring_end_type, recurring_end_after, recurring_end_by_date, is_multi_slot)
                VALUES
                    (:venue_id, NULL, :date, :start_time, :end_time, :title, :pay, :notes, 'open', :artist_type, :band_formats, :styles,
                     :is_recurring, :recurring_group_id, :recurring_interval_weeks, :recurring_days_of_week,
                     :recurring_end_type, :recurring_end_after, :recurring_end_by_date, 1) RETURNING id
            """),
            {
                "venue_id": venue_id,
                "date": data["date"],
                "start_time": start_time,
                "end_time": end_time,
                "title": data.get("title"),
                "pay": data.get("pay", 0),
                "notes": data.get("notes"),
                "artist_type": data.get("artist_type"),
                "band_formats": data.get("band_formats"),
                "styles": data.get("styles"),
                "is_recurring": data.get("is_recurring", 0),
                "recurring_group_id": data.get("recurring_group_id"),
                "recurring_interval_weeks": data.get("recurring_interval_weeks"),
                "recurring_days_of_week": data.get("recurring_days_of_week"),
                "recurring_end_type": data.get("recurring_end_type"),
                "recurring_end_after": data.get("recurring_end_after"),
                "recurring_end_by_date": data.get("recurring_end_by_date"),
                "is_multi_slot": 1
            }
        ).scalar()
        
        # Always create slots (every gig uses slots)
        if slots:
            for i, slot in enumerate(slots):
                db.execute(
                    text("""
                        INSERT INTO gig_slots (gig_id, slot_number, start_time, end_time, pay, status,
                                               artist_type, band_formats, styles, deal_type, door_pct, guarantee_cents, apply_override, requires_equipment)
                        VALUES (:gig_id, :slot_number, :start_time, :end_time, :pay, 'open',
                                :artist_type, :band_formats, :styles, COALESCE(:deal_type, 'flat'), COALESCE(:door_pct, 0), COALESCE(:guarantee_cents, 0), COALESCE(:apply_override, 0), :requires_equipment)
                    """),
                    {
                        "gig_id": gig_id,
                        "slot_number": i + 1,
                        "start_time": slot.get("start_time"),
                        "end_time": slot.get("end_time"),
                        "pay": slot.get("pay", 0),
                        "artist_type": slot.get("artist_type"),
                        "band_formats": slot.get("band_formats"),
                        "styles": slot.get("styles"),
                    "deal_type": slot.get("deal_type"),
                    "door_pct": slot.get("door_pct"),
                    "guarantee_cents": slot.get("guarantee_cents"),
                    "apply_override": 1 if slot.get("apply_override") else 0,
                    # Jul 1 2026: MC-type equipment gate. None = venue
                    # left it unspecified → no filter.
                    "requires_equipment": (1 if bool(slot.get("requires_equipment")) else 0) if slot.get("requires_equipment") is not None else None,
                    }
                )
        
        db.commit()
        
        # Auto-create flyer if venue has auto_flyers enabled
        try:
            _acf, _ = _get_flyer_helpers()
            _acf(db, gig_id, venue_id)
        except Exception:
            pass  # Don't fail gig creation if flyer creation fails

        # Catch-up: fire any open-gig blast windows that have already passed
        # e.g. gig created today for a date within the 1w/2w/4w window
        # Those windows will never fire from the scheduler (target date already past)
        #
        # Audit fix (May 2026 part 10): skip the catch-up thread for recurring
        # series children except the first/nearest one. A 104-week series
        # used to spawn 104 daemon threads in a 30-second window — most of
        # which immediately exited (days_until > 28) but each opened its
        # own SQLite connection. Now: only the first occurrence (the one
        # closest to today) fires catch-up; the rest will be picked up by
        # the regular hourly scheduler.
        is_recurring_child = bool(data.get("is_recurring") and data.get("recurring_group_id"))
        skip_catchup = False
        if is_recurring_child:
            try:
                _gd = data.get("date") or ""
                _existing_sooner = db.execute(text("""
                    SELECT 1 FROM gigs
                    WHERE recurring_group_id = :rgid
                      AND venue_id = :vid
                      AND id != :gid
                      AND date <= :gd
                    LIMIT 1
                """), {"rgid": data.get("recurring_group_id"),
                       "vid": venue_id, "gid": gig_id, "gd": _gd}).first()
                if _existing_sooner:
                    skip_catchup = True
            except Exception:
                pass

        try:
            if skip_catchup:
                raise StopIteration  # skip the thread entirely
            import threading as _th
            _gig_id_cu  = gig_id
            _venue_id_cu = venue_id
            def _catchup_notifications():
                try:
                    from backend.db import DB_PATH as _DB
                    from backend.scheduler import process_open_gig_notifications, get_smtp_settings
                    from backend.db import get_db_connection as _gigs_raw_conn
                    from datetime import date as _date, datetime as _dt
                    conn = _gigs_raw_conn()
                    cur = conn.cursor()
                    smtp = get_smtp_settings(cur)
                    if not smtp.get("smtp_user"):
                        conn.close(); return
                    gig_row = conn.execute(
                        "SELECT date FROM gigs WHERE id=?", (_gig_id_cu,)
                    ).fetchone()
                    if not gig_row: conn.close(); return
                    # TZ FIX: use the VENUE's timezone (gig.date is venue-local).
                    # Falls back to platform tz, then to a UTC date as last resort.
                    try:
                        from zoneinfo import ZoneInfo as _cup_zi
                        _vrow = conn.execute("SELECT timezone, state FROM venues WHERE id=?", (_venue_id_cu,)).fetchone()
                        _vtz_str = (_vrow[0] if _vrow and _vrow[0] else None)
                        if not _vtz_str:
                            from backend.utils import US_STATE_TIMEZONES as _ust
                            _st = (_vrow[1] if _vrow and _vrow[1] else '').strip().upper()
                            _vtz_str = _ust.get(_st)
                        if not _vtz_str:
                            _pf = conn.execute("SELECT setting_value FROM platform_settings WHERE setting_key='platform_timezone'").fetchone()
                            _vtz_str = (_pf[0] if _pf else None) or "America/Los_Angeles"
                        _cup_today = __import__('datetime').datetime.now(_cup_zi(_vtz_str)).date()
                    except Exception:
                        _cup_today = _date.today()
                    days_until = (_dt.strptime(gig_row["date"], "%Y-%m-%d").date() - _cup_today).days
                    # For each window that has already closed, fire now if not already sent
                    for notif_key, window_days in [("open_gig_4w",28),("open_gig_2w",14),("open_gig_1w",7)]:
                        if days_until <= window_days:
                            already = conn.execute(
                                "SELECT 1 FROM gig_email_log WHERE gig_id=? AND notification_key=?",
                                (_gig_id_cu, notif_key)
                            ).fetchone()
                            if not already:
                                process_open_gig_notifications(cur, smtp, notif_key)
                    conn.close()
                except Exception as _ce:
                    logger.warning(f"Catch-up notification error: {_ce}")
            _th.Thread(target=_catchup_notifications, daemon=True).start()
        except StopIteration:
            pass  # intentionally skipped for recurring children
        except Exception:
            pass

        # ── Hold feature (Jun 2026 / series Jul 2026) ──
        # Venue can mark a gig as held at creation time with an ordered
        # list of preferred-artist IDs.
        #   - Single gig: first artist receives the offer immediately (if
        #     `hold_email_artists` is true). Same UX as before.
        #   - Recurring gig: every instance gets the same waitlist staged
        #     but DOES NOT auto-fire its rotation. The frontend POSTs one
        #     gig per occurrence; after the loop finishes it calls
        #     /api/series/{recurring_group_id}/hold/start once which fires
        #     a single bundled offer per artist (all dates in one email).
        #     This avoids 26 separate emails to the same artist for a 26-
        #     week series.
        _hold_artist_ids = data.get("hold_artist_ids") or []
        if _hold_artist_ids:
            try:
                from backend.services.gig_hold import create_hold_waitlist
                create_hold_waitlist(
                    db,
                    gig_id=gig_id,
                    artist_ids=_hold_artist_ids,
                    send_email_now=bool(data.get("hold_email_artists", True)),
                    offer_window_hours=int(data.get("hold_offer_window_hours") or 24),
                    # Recurring → defer rotation kickoff. Per-instance
                    # waitlist rows are still inserted + hold_status set
                    # to 'active' so the gig is hidden from public search
                    # while staging. The series-start endpoint generates
                    # the shared offer token and fires the bundled email.
                    defer_kickoff=bool(data.get("is_recurring")),
                )
            except Exception as _he:
                # Hold setup failure shouldn't kill the gig creation —
                # log and let admin set it up manually.
                logger.error(f"[HOLD] create_hold_waitlist failed for gig {gig_id}: {_he}")

        return {"ok": True, "gig_id": gig_id}

    except Exception as e:
        logger.error(f"create_gig error for venue {venue_id}: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to create gig: {str(e)}")

# LIST ALL GIGS (AUTHENTICATED — used by artist book-gigs calendar and
# venue create-gigs countersign lookup). Historically anonymous — the
# 2026-08-08 audit surfaced that anonymous callers could scrape the
# entire gig catalog including venue notes and per-slot door-deal
# economics. Both frontend callers are authenticated pages, so
# requiring get_current_user closes the exposure without changing the
# UI. Rate-limited (100/min) as defense-in-depth against authenticated
# enumeration. The hardened anonymous-safe view lives at
# /api/gigs/public (30/min, 7d…+90d window, notes/door-terms stripped).
@router.get("/gigs")
@limiter.limit("100/minute")
def list_gigs(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    try:
        rows = db.execute(
            text("""
                SELECT
                    g.id,
                    g.venue_id,
                    g.artist_id,
                    g.date,
                    g.start_time,
                    g.end_time,
                    g.status,
                    g.title,
                    g.pay,
                    g.notes,
                    g.artist_type,
                    g.band_formats, g.styles,
                    COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                    g.contract_hold_expires_at,
                    g.contract_hold_artist_id,
                    g.hold_status,
                    g.hold_offer_window_hours,
                    (SELECT gc.artist_id FROM gig_contracts gc WHERE gc.gig_id = g.id ORDER BY gc.id DESC LIMIT 1) as contract_artist_id,
                    (SELECT gc.status FROM gig_contracts gc WHERE gc.gig_id = g.id ORDER BY gc.id DESC LIMIT 1) as contract_status,
                    CASE WHEN g.radius_blast_token IS NOT NULL AND g.status = 'open' THEN 1 ELSE 0 END as is_blast_open,
                    COALESCE(ven.radius_miles, 20) as blast_radius_miles,
                    COALESCE(g.frequency_exempt, 0) as frequency_exempt,
                    -- has_active_waitlist drives the old 'WAITLIST IN PROGRESS'
                    -- purple banner. Filter out hold-source rows (Jun 2026) —
                    -- hold gigs render the new amber hold-management panel
                    -- via window._renderHoldMgmtPanel instead.
                    CASE WHEN (
                        EXISTS (SELECT 1 FROM gig_waitlist wl WHERE wl.gig_id = g.id AND wl.offer_sent = 1 AND (wl.offer_declined = 0 OR wl.offer_declined IS NULL) AND (wl.offer_expires_at IS NULL OR wl.offer_expires_at > datetime('now')) AND (wl.source IS NULL OR wl.source = 'cancellation'))
                        OR EXISTS (SELECT 1 FROM waitlist_offered wo WHERE wo.gig_id = g.id AND wo.offer_expires_at > datetime('now'))
                    ) THEN 1 ELSE 0 END as has_active_waitlist,
                    (SELECT el.notification_key FROM gig_email_log el
                     WHERE el.gig_id = g.id
                     AND el.notification_key IN ('open_gig_4w','open_gig_2w','open_gig_1w','open_gig_36h','cancelled_blast','radius_blast','new_gig_blast')
                     ORDER BY el.sent_at DESC LIMIT 1) as last_notification_key,
                    (SELECT el.recipient_count FROM gig_email_log el
                     WHERE el.gig_id = g.id
                     AND el.notification_key IN ('open_gig_4w','open_gig_2w','open_gig_1w','open_gig_36h','cancelled_blast','radius_blast','new_gig_blast')
                     ORDER BY el.sent_at DESC LIMIT 1) as last_notification_recipient_count,
                    v.venue_name,
                    v.address_line_1,
                    v.address_line_2,
                    v.city,
                    v.state,
                    v.latitude as venue_lat,
                    v.longitude as venue_lon,
                    v.has_stage,
                    v.has_sound_equipment,
                    v.has_lighting,
                    COALESCE(a.name,
                        (SELECT a2.name FROM artists a2
                         JOIN gig_contracts gc2 ON gc2.artist_id = a2.id
                         WHERE gc2.gig_id = g.id
                         ORDER BY gc2.id DESC LIMIT 1)
                    ) as artist_name
                FROM gigs g
                JOIN venues v ON g.venue_id = v.id
                LEFT JOIN artists a ON g.artist_id = a.id
                LEFT JOIN venue_email_notifications ven ON ven.venue_id = g.venue_id AND ven.notification_key = 'radius_blast'
                ORDER BY g.date ASC
            """)
        ).mappings().all()

        # Enrich all gigs with their slots
        result = []
        for row in rows:
            gig = dict(row)
            slots = db.execute(
                text("""
                    SELECT gs.id as slot_id, gs.slot_number, gs.start_time, gs.end_time,
                           gs.pay, gs.status, gs.artist_id,
                           gs.artist_type, gs.band_formats, gs.styles,
                           gs.deal_type, gs.door_pct, gs.guarantee_cents, COALESCE(gs.apply_override,0) as apply_override, gs.requires_equipment,
                           a.name as artist_name,
                           -- Booked artist's actual profile attributes — used
                           -- by the hover card to filter the lineup/style
                           -- chips to just the booked artist's matches. NULL
                           -- on open slots; falls back to gig requirements then.
                           a.band_formats as artist_band_formats,
                           a.styles      as artist_styles
                    FROM gig_slots gs
                    LEFT JOIN artists a ON gs.artist_id = a.id
                    WHERE gs.gig_id = :gid
                    ORDER BY gs.slot_number ASC
                """),
                {"gid": gig["id"]}
            ).mappings().all()
            gig["slots"] = _enrich_pay_summary([dict(s) for s in slots])
            _enrich_pay_summary(gig)
            result.append(gig)
        return result

    except Exception as e:
        logger.error(f"Failed to load gigs. Please try again.: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to load gigs. Please try again.: {str(e)}")

# v96: PUBLIC GIGS ENDPOINT for public-gigs.html
@router.get("/api/artists/{artist_id}/gigs/public")
@limiter.limit("60/minute")
def list_artist_gigs_public(artist_id: int, request: Request, db=Depends(get_db)):
    """Get all booked gigs for an artist (public view) - including slot bookings.
    Rate-limited (Jul 1 2026 audit fix): 60/min per IP prevents anonymous
    scraping of every artist's gig history."""
    try:
        # Jul 2026 refactor: collapsed the "regular gigs" + "slot bookings"
        # two-query merge into a single slot-only read. Post-backfill every
        # gig has ≥1 `gig_slots` row so slot-only covers both shapes.
        # Removes an extra DB round-trip + the Python-side seen_ids dedup.
        slot_gigs = db.execute(
            text("""
                SELECT DISTINCT g.id, g.venue_id, g.date, gs.start_time, gs.end_time,
                       'booked' as status, g.title, gs.pay, g.artist_type, g.band_formats,
                       COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                       v.venue_name, v.address_line_1, v.address_line_2, v.city, v.state,
                       a.name as artist_name, a.artist_type as artist_actual_type, a.band_formats as artist_band_formats, a.styles as artist_styles,
                       (SELECT COUNT(*) FROM gig_slots gs2 WHERE gs2.gig_id = g.id
                          AND gs2.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')) as booked_slots_count,
                       (SELECT COUNT(*) FROM gig_slots gs3 WHERE gs3.gig_id = g.id) as total_slots_count
                FROM gig_slots gs
                JOIN gigs g ON gs.gig_id = g.id
                JOIN venues v ON g.venue_id = v.id
                JOIN artists a ON a.id = :aid
                WHERE gs.artist_id = :aid AND gs.status = 'booked'
                ORDER BY g.date ASC
            """),
            {"aid": artist_id}
        ).mappings().all()

        result = [dict(r) for r in slot_gigs]
        result.sort(key=lambda x: x.get('date', ''))

        # Attach per-slot data to multi-slot gigs in ONE batched query so
        # the artist-profile hover card / day modal can render per-slot
        # breakdowns without an N+1 fan-out. Same redacted shape as
        # /api/gigs/{id}/slots/public.
        multi_ids = [r["id"] for r in result if r.get("is_multi_slot")]
        if multi_ids:
            placeholders = ",".join([f":id{i}" for i, _ in enumerate(multi_ids)])
            params = {f"id{i}": gid for i, gid in enumerate(multi_ids)}
            slot_rows = db.execute(
                text(f"""
                    SELECT gs.id, gs.gig_id, gs.slot_number, gs.start_time, gs.end_time,
                           gs.artist_id, gs.status,
                           gs.artist_type, gs.band_formats, gs.styles,
                           gs.deal_type, gs.requires_equipment,
                           a.name as artist_name
                    FROM gig_slots gs
                    LEFT JOIN artists a ON gs.artist_id = a.id
                    WHERE gs.gig_id IN ({placeholders})
                    ORDER BY gs.gig_id, gs.slot_number ASC
                """),
                params,
            ).mappings().all()
            by_gig = {}
            for s in slot_rows:
                by_gig.setdefault(s["gig_id"], []).append(dict(s))
            for r in result:
                if r.get("is_multi_slot"):
                    r["slots"] = by_gig.get(r["id"], [])

        return result
    except Exception as e:
        logger.error(f"Failed to load gigs. Please try again.: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to load gigs. Please try again.: {str(e)}")

@router.get("/api/gigs/public")
@limiter.limit("30/minute")
def list_public_gigs(request: Request, db=Depends(get_db)):
    """Get all gigs (open and booked) for public discovery page.

    Audit fix (May 2026 part 5): added a date filter so this only returns
    today + the next 90 days (plus 7 days of recent past for "just-cancelled"
    indicators). Previously the endpoint returned every gig in the database
    — a scraper could pull the entire historical catalog in one request.
    Also rate-limited to 30 req/min per IP since the endpoint is anonymous.
    """
    try:
        rows = db.execute(
            text("""
                SELECT
                    g.id,
                    g.venue_id,
                    g.artist_id,
                    g.date,
                    g.start_time,
                    g.end_time,
                    g.status,
                    g.title,
                    g.pay,
                    g.notes,
                    g.artist_type,
                    g.band_formats, g.styles,
                    COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                    v.venue_name,
                    v.address_line_1,
                    v.address_line_2,
                    v.city,
                    v.state,
                    v.latitude as venue_lat,
                    v.longitude as venue_lon,
                    v.has_stage,
                    v.has_sound_equipment,
                    v.has_lighting,
                    COALESCE(a.name,
                        (SELECT a2.name FROM artists a2
                         JOIN gig_contracts gc2 ON gc2.artist_id = a2.id
                         WHERE gc2.gig_id = g.id
                         ORDER BY gc2.id DESC LIMIT 1)
                    ) as artist_name,
                    (SELECT COUNT(*) FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')) as booked_slots_count,
                    (SELECT COUNT(*) FROM gig_slots gs WHERE gs.gig_id = g.id) as total_slots_count
                FROM gigs g
                JOIN venues v ON g.venue_id = v.id
                LEFT JOIN artists a ON g.artist_id = a.id AND a.deleted_at IS NULL
                WHERE COALESCE(v.payment_status, 'active') != 'suspended'
                  AND v.deleted_at IS NULL
                  AND g.date >= date('now', '-7 days')
                  AND g.date <= date('now', '+90 days')
                  -- Hide active/exhausted holds from artist search.
                  -- They become visible (and bookable) only after the
                  -- venue resolves the hold (accept → 'booked', open → NULL).
                  AND (g.hold_status IS NULL OR g.hold_status NOT IN ('active','exhausted'))
                ORDER BY g.date ASC
            """)
        ).mappings().all()

        # Public listing is gig-level only (door deals are per-slot). Enrich
        # each row so pay_summary is present in a consistent shape — for
        # vanilla gig rows this is just "$X.XX", matching what the artist /
        # venue dashboards return for door-less slots.
        rows = _enrich_pay_summary([dict(r) for r in rows])

        # Attach per-slot data to multi-slot gigs in ONE batched query so
        # public calendar consumers (venue-profile, artist-profile, public-
        # gigs) can render per-slot bubble colors + hover breakdowns without
        # an N+1 fan-out. Financial fields are excluded — same redaction as
        # /api/gigs/{id}/slots/public above.
        multi_ids = [r["id"] for r in rows if r.get("is_multi_slot")]
        if multi_ids:
            placeholders = ",".join([f":id{i}" for i, _ in enumerate(multi_ids)])
            params = {f"id{i}": gid for i, gid in enumerate(multi_ids)}
            slot_rows = db.execute(
                text(f"""
                    SELECT gs.id, gs.gig_id, gs.slot_number, gs.start_time, gs.end_time,
                           gs.artist_id, gs.status,
                           gs.artist_type, gs.band_formats, gs.styles,
                           gs.deal_type, gs.requires_equipment,
                           a.name as artist_name
                    FROM gig_slots gs
                    LEFT JOIN artists a ON gs.artist_id = a.id
                    WHERE gs.gig_id IN ({placeholders})
                    ORDER BY gs.gig_id, gs.slot_number ASC
                """),
                params,
            ).mappings().all()
            by_gig = {}
            for s in slot_rows:
                by_gig.setdefault(s["gig_id"], []).append(dict(s))
            for r in rows:
                if r.get("is_multi_slot"):
                    r["slots"] = by_gig.get(r["id"], [])

        return rows

    except Exception as e:
        logger.error(f"Failed to load gigs. Please try again.: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to load gigs. Please try again.: {str(e)}")

# LIST GIGS FOR A VENUE (VENUE DASHBOARD)
@router.get("/venues/{venue_id}/gigs")
def list_venue_gigs(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    try:
        rows = db.execute(
            text("""
                SELECT
                    g.id,
                    g.venue_id,
                    g.date,
                    g.start_time,
                    g.end_time,
                    g.status,
                    g.artist_id,
                    g.title,
                    g.pay,
                    g.notes,
                    g.artist_type,
                    g.band_formats, g.styles,
                    g.recurring_group_id,
                    g.is_recurring,
                    g.recurring_interval_weeks,
                    g.recurring_days_of_week,
                    g.recurring_end_type,
                    g.recurring_end_after,
                    g.recurring_end_by_date,
                    COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                    -- Hold status (Jun 2026) drives the Hold-mgmt panel
                    -- + suppresses the legacy blast banner. Without this
                    -- column the frontend can't tell held gigs apart.
                    g.hold_status,
                    g.hold_offer_window_hours,
                    CASE WHEN g.radius_blast_token IS NOT NULL AND g.status = 'open' THEN 1 ELSE 0 END as is_blast_open,
                    COALESCE(g.frequency_exempt, 0) as frequency_exempt,
                    -- has_active_waitlist drives the old 'WAITLIST IN PROGRESS'
                    -- purple banner. Filter out hold-source rows (Jun 2026) —
                    -- hold gigs render the new amber hold-management panel
                    -- via window._renderHoldMgmtPanel instead.
                    CASE WHEN (
                        EXISTS (SELECT 1 FROM gig_waitlist wl WHERE wl.gig_id = g.id AND wl.offer_sent = 1 AND (wl.offer_declined = 0 OR wl.offer_declined IS NULL) AND (wl.offer_expires_at IS NULL OR wl.offer_expires_at > datetime('now')) AND (wl.source IS NULL OR wl.source = 'cancellation'))
                        OR EXISTS (SELECT 1 FROM waitlist_offered wo WHERE wo.gig_id = g.id AND wo.offer_expires_at > datetime('now'))
                    ) THEN 1 ELSE 0 END as has_active_waitlist,
                    (SELECT el.notification_key FROM gig_email_log el
                     WHERE el.gig_id = g.id
                     AND el.notification_key IN ('open_gig_4w','open_gig_2w','open_gig_1w','open_gig_36h','cancelled_blast','radius_blast','new_gig_blast')
                     ORDER BY el.sent_at DESC LIMIT 1) as last_notification_key,
                    (SELECT el.recipient_count FROM gig_email_log el
                     WHERE el.gig_id = g.id
                     AND el.notification_key IN ('open_gig_4w','open_gig_2w','open_gig_1w','open_gig_36h','cancelled_blast','radius_blast','new_gig_blast')
                     ORDER BY el.sent_at DESC LIMIT 1) as last_notification_recipient_count,
                    v.venue_name,
                    COALESCE(a.name,
                        (SELECT a2.name FROM artists a2
                         JOIN gig_contracts gc2 ON gc2.artist_id = a2.id
                         WHERE gc2.gig_id = g.id
                         ORDER BY gc2.id DESC LIMIT 1)
                    ) as artist_name,
                    a.artist_type as artist_actual_type,
                    a.band_formats as artist_band_formats, a.styles as artist_styles,
                    (SELECT COUNT(*) FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')) as booked_slots_count,
                    (SELECT COUNT(*) FROM gig_slots gs WHERE gs.gig_id = g.id) as total_slots_count,
                    (SELECT gc.status FROM gig_contracts gc WHERE gc.gig_id = g.id ORDER BY gc.id DESC LIMIT 1) as contract_status
                FROM gigs g
                JOIN venues v ON g.venue_id = v.id
                LEFT JOIN artists a ON g.artist_id = a.id
                WHERE g.venue_id = :venue_id
                ORDER BY g.date ASC
            """),
            {"venue_id": venue_id}
        ).mappings().all()

        # Enrich all gigs with slot details.
        # Audit fix (May 2026): fetch all slots in ONE query and group in
        # Python instead of issuing one SELECT per gig (N+1). At a venue
        # with a 100-occurrence recurring series this dropped 100 queries
        # off every calendar render.
        gig_ids = [r["id"] for r in rows]
        slots_by_gig = {gid: [] for gid in gig_ids}
        if gig_ids:
            slot_rows = db.execute(
                text("""
                    SELECT gs.gig_id, gs.id as slot_id, gs.slot_number, gs.start_time, gs.end_time, gs.pay, gs.status, gs.artist_id,
                           gs.artist_type, gs.band_formats, gs.styles,
                           gs.deal_type, gs.door_pct, gs.guarantee_cents, COALESCE(gs.apply_override,0) as apply_override, gs.requires_equipment,
                           gs.door_receipts_cents, gs.settled_pay_cents, gs.settled_at,
                           a.name as artist_name,
                           a.band_formats as artist_band_formats,
                           a.styles      as artist_styles
                    FROM gig_slots gs
                    LEFT JOIN artists a ON gs.artist_id = a.id
                    WHERE gs.gig_id IN :gids
                    ORDER BY gs.gig_id ASC, gs.slot_number ASC
                """).bindparams(bindparam("gids", expanding=True)),
                {"gids": gig_ids}
            ).mappings().all()
            for s in slot_rows:
                slots_by_gig.setdefault(s["gig_id"], []).append(dict(s))

        result = []
        for row in rows:
            gig = dict(row)
            gig["slots"] = _enrich_pay_summary(slots_by_gig.get(gig["id"], []))
            _enrich_pay_summary(gig)
            result.append(gig)

        return result

    except Exception as e:
        logger.error(f"Failed to load gigs. Please try again.: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to load gigs. Please try again.: {str(e)}")


@router.get("/api/gigs/{gig_id}/detail")
def get_gig_detail(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Return full gig detail for deep-link fallback when gig isn't in artist's calendar view."""
    row = db.execute(text("""
        SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.notes,
               g.title, g.artist_type, g.band_formats, g.styles,
               g.status, g.artist_id, g.venue_id,
               COALESCE(g.is_multi_slot, 0) as is_multi_slot,
               CASE WHEN g.radius_blast_token IS NOT NULL AND g.status = 'open' THEN 1 ELSE 0 END as is_blast_open,
               COALESCE(ven.radius_miles, 20) as blast_radius_miles,
               COALESCE(g.frequency_exempt, 0) as frequency_exempt,
               CASE WHEN (
                        EXISTS (SELECT 1 FROM gig_waitlist wl WHERE wl.gig_id = g.id AND wl.offer_sent = 1 AND (wl.offer_declined = 0 OR wl.offer_declined IS NULL) AND (wl.offer_expires_at IS NULL OR wl.offer_expires_at > datetime('now')))
                        OR EXISTS (SELECT 1 FROM waitlist_offered wo WHERE wo.gig_id = g.id AND wo.offer_expires_at > datetime('now'))
                    ) THEN 1 ELSE 0 END as has_active_waitlist,
               (SELECT el.notification_key FROM gig_email_log el
                WHERE el.gig_id = g.id
                AND el.notification_key IN ('open_gig_4w','open_gig_2w','open_gig_1w','open_gig_36h','cancelled_blast','radius_blast','new_gig_blast')
                ORDER BY el.sent_at DESC LIMIT 1) as last_notification_key,
               (SELECT el.recipient_count FROM gig_email_log el
                WHERE el.gig_id = g.id
                AND el.notification_key IN ('open_gig_4w','open_gig_2w','open_gig_1w','open_gig_36h','cancelled_blast','radius_blast','new_gig_blast')
                ORDER BY el.sent_at DESC LIMIT 1) as last_notification_recipient_count,
               v.venue_name, v.address_line_1, v.address_line_2, v.city, v.state,
               v.latitude as venue_lat, v.longitude as venue_lon,
               a.name as artist_name
        FROM gigs g
        JOIN venues v ON g.venue_id = v.id
        LEFT JOIN artists a ON g.artist_id = a.id
        LEFT JOIN venue_email_notifications ven ON ven.venue_id = g.venue_id AND ven.notification_key = 'radius_blast'
        WHERE g.id = :gid
    """), {"gid": gig_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Gig not found")
    result = dict(row)
    slots = db.execute(text("""
        SELECT gs.id as slot_id, gs.slot_number, gs.start_time, gs.end_time, gs.pay,
               gs.artist_id, a.name as artist_name, gs.status,
               gs.artist_type, gs.band_formats, gs.styles,
               gs.deal_type, gs.door_pct, gs.guarantee_cents, COALESCE(gs.apply_override,0) as apply_override,
               gs.requires_equipment
        FROM gig_slots gs LEFT JOIN artists a ON gs.artist_id = a.id
        WHERE gs.gig_id = :gid ORDER BY gs.slot_number
    """), {"gid": gig_id}).fetchall()
    result["slots"] = _enrich_pay_summary([dict(s._mapping) for s in slots])
    # Gig-level pay_summary too (single-slot gigs don't have door deals yet
    # but this keeps the response shape consistent for the frontend).
    _enrich_pay_summary(result)
    return result


@router.get("/api/gigs/{gig_id}/effective-pay")
def get_gig_effective_pay(gig_id: int, artist_id: int = None, user=Depends(get_current_user), db=Depends(get_db)):
    """Return effective pay for a gig (published pay or venue override for artist). Requires venue access."""
    from backend.utils import check_venue_access
    gig = db.execute(
        text("SELECT id, venue_id, artist_id, pay FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    aid = artist_id or gig.get("artist_id")
    pay = _get_effective_pay(db, gig["venue_id"], aid, gig.get("pay"))
    return {"pay": round(pay, 2)}


# Mapping of open-gig blast notification keys to their time window in hours.
# Used by _open_blast_bypass_active() to determine if a venue's "blast-all" setting
# for any of these blasts has activated for a given gig date.
_OPEN_GIG_BLAST_WINDOWS = {
    'open_gig_36h': 36,
    'open_gig_1w':  168,    # 7 * 24
    'open_gig_2w':  336,    # 14 * 24
    'open_gig_4w':  672,    # 28 * 24
}


def _open_blast_bypass_active(db, venue_id: int, gig_id: int) -> bool:
    """Return True if the venue's open-gig "blast all nearby artists" has fired
    (or is about to fire) for this gig — meaning non-preferred artists in the
    radius are expected to be able to book.

    Conditions for True:
      - Venue has at least one open-gig blast notification with `blast_all_enabled=1`
        (e.g. open_gig_36h.blast_all_enabled=1)
      - Gig is within or past that notification's time window (so the blast has
        either already gone out, or is currently in its window).

    Without this bypass, a non-preferred artist who received an open-gig blast
    email saying "any artist can book this gig" would hit a 403 "Artist is not
    approved for this venue" — because the existing `radius_blast_token` bypass
    is only set by cancellation blasts, not open-gig blasts.

    See May 2026 changelog for context.
    """
    from sqlalchemy import text as _t
    try:
        # Get the gig's start datetime and the venue's blast-all flags
        gig_row = db.execute(
            _t("SELECT date, start_time FROM gigs WHERE id = :gid AND venue_id = :vid"),
            {"gid": gig_id, "vid": venue_id}
        ).mappings().first()
        if not gig_row or not gig_row.get("date"):
            return False

        # Compute hours until gig (naive interpretation is fine here — we're
        # checking "is now within X hours of gig?" which is tz-independent
        # to within a few hours).
        from datetime import datetime as _dt
        try:
            gig_start = _dt.strptime(
                f"{str(gig_row['date'])[:10]} {(gig_row.get('start_time') or '00:00')[:5]}",
                "%Y-%m-%d %H:%M"
            )
        except Exception:
            return False
        hours_until = (gig_start - _dt.utcnow()).total_seconds() / 3600.0
        if hours_until < 0:
            # Gig already started — no bypass; let other checks (e.g. past-gig) handle.
            return False

        # Look up venue's blast-all flags for each open-gig notification.
        rows = db.execute(
            _t("""
                SELECT notification_key,
                       COALESCE(blast_all_enabled, 0) AS blast_all_enabled,
                       COALESCE(time_value, 0) AS time_value,
                       COALESCE(time_unit, '') AS time_unit
                FROM venue_email_notifications
                WHERE venue_id = :vid
                  AND notification_key IN ('open_gig_36h','open_gig_1w','open_gig_2w','open_gig_4w')
            """),
            {"vid": venue_id}
        ).mappings().all()

        for r in rows:
            if not r["blast_all_enabled"]:
                continue
            # Compute the window-in-hours for this notification key.
            # Prefer the venue-configured time_value/time_unit if set; otherwise
            # fall back to the default mapping above.
            window_h = _OPEN_GIG_BLAST_WINDOWS.get(r["notification_key"], 0)
            tv, tu = r["time_value"], (r["time_unit"] or "").lower()
            if tv and tu:
                try:
                    if tu.startswith("hour"):
                        window_h = int(tv)
                    elif tu.startswith("day"):
                        window_h = int(tv) * 24
                    elif tu.startswith("week"):
                        window_h = int(tv) * 24 * 7
                    elif tu.startswith("month"):
                        window_h = int(tv) * 24 * 30
                except (ValueError, TypeError):
                    pass

            # Audit fix #15 (May 2026): require evidence the blast email
            # actually fired (gig_email_log row) before granting the bypass.
            # Previously the bypass activated on time-window alone, so a
            # non-preferred artist could book the moment the gig entered the
            # window — even before any email had gone out.
            #
            # Audit fix (May 2026 part 10g): accept ANY blast-type email as
            # that evidence, not just the exact open_gig_* key. A recurring
            # gig created INSIDE an already-passed window gets a `new_gig_blast`
            # (which itself sends to non-preferred nearby artists within radius
            # when blast_all is on — see batch_blast at gigs.py:6252) but never
            # an `open_gig_1w` row, because the scheduled 1-week send already
            # passed by creation time. Tying the bypass strictly to the
            # open_gig_* key meant those gigs stayed un-bookable for the very
            # non-preferred artists the venue intended to reach. All of these
            # keys mean "this gig was advertised to non-preferred artists."
            if window_h > 0 and hours_until <= window_h:
                fired = db.execute(
                    _t("""SELECT 1 FROM gig_email_log
                          WHERE gig_id = :gid
                            AND notification_key IN
                                ('open_gig_36h','open_gig_1w','open_gig_2w','open_gig_4w',
                                 'new_gig_blast','radius_blast','cancelled_blast')
                          LIMIT 1"""),
                    {"gid": gig_id}
                ).first()
                if fired:
                    return True

        return False
    except Exception:
        # Any failure → deny bypass; existing preferred-only flow applies.
        return False


PROXIMITY_HOURS = 3  # warn (but don't block) if another booking is within this window


@router.get("/api/gigs/{gig_id}/booking-precheck")
def booking_precheck(gig_id: int, artist_id: int, slot_id: int = None,
                     user=Depends(get_current_user), db=Depends(get_db)):
    """Pre-booking conflict check for the frontend's confirm flow.
    Returns {'overlap': bool, 'within_window': bool, 'minutes_apart': N,
    'other': {gig info}}. Frontend uses 'overlap' to hard-stop the booking
    and 'within_window' to confirm the user wants to proceed despite proximity.

    Does NOT mutate any state — purely informational. The real overlap
    enforcement happens inside the book_gig / book_slot / book_with_contract
    endpoints, so a malicious caller can't skip the precheck."""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)

    gig = db.execute(
        text("SELECT id, venue_id, date, start_time, end_time FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")

    if slot_id:
        slot = db.execute(
            text("SELECT start_time, end_time FROM gig_slots WHERE id = :sid AND gig_id = :gid"),
            {"sid": slot_id, "gid": gig_id}
        ).mappings().first()
        if not slot:
            raise HTTPException(404, "Slot not found")
        new_dt = _parse_gig_slot_dt(db, gig["venue_id"], gig["date"],
                                     slot["start_time"], slot["end_time"])
    else:
        new_dt = _parse_gig_slot_dt(db, gig["venue_id"], gig["date"],
                                     gig.get("start_time"), gig.get("end_time"))

    if not new_dt:
        return {"overlap": False, "within_window": False, "member_blackouts": []}

    new_start, new_end = new_dt
    result = _check_artist_time_conflict(db, artist_id, gig_id, new_start, new_end)

    # ALSO surface member-level (user_availability) blackouts that fall on
    # this gig date. These are SOFT warnings — the booking artist confirms
    # through them (band might perform without the unavailable member).
    # Band-level (artist_availability) is enforced separately as a hard
    # block inside book_gig/book_slot/book_with_contract — we don't duplicate
    # that here. Per user direction: band-block wins, member warning ignored.
    try:
        from backend.routes.availability import _member_blackouts_for_gig
        result["member_blackouts"] = _member_blackouts_for_gig(
            db, artist_id, gig.get("date"), current_user_id=user.id
        )
    except Exception:
        result["member_blackouts"] = []
    return result


def _check_artist_time_conflict(db, artist_id: int, gig_id: int,
                                 new_start_dt, new_end_dt):
    """Check if booking this slot would conflict with the artist's other bookings.
    Returns dict:
      {'overlap': True,  'other': {gig info}}            → HARD BLOCK
      {'overlap': False, 'within_window': True,  ...}    → WARN ONLY (caller decides)
      {'overlap': False, 'within_window': False}         → CLEAR

    new_start_dt / new_end_dt are timezone-aware datetimes (or both naive in
    the same reference). Caller is responsible for tz-normalizing.
    """
    from sqlalchemy import text as _t
    from datetime import datetime as _dt, timedelta as _td
    from backend.utils import get_venue_timezone_str
    from zoneinfo import ZoneInfo

    # Pull every gig+slot the artist is committed to (booked, pending_contract,
    # pending_venue_approval, awaiting_venue_contract) EXCEPT this one.
    # Jul 2026 refactor: dropped the "single-slot: g.artist_id" leg. Post-
    # backfill every booking has a `gig_slots` row so the slot leg alone
    # covers both shapes. Changed LEFT JOIN → INNER JOIN so the WHERE is
    # simpler.
    rows = db.execute(_t("""
        SELECT g.id as gig_id, g.venue_id, g.date as gig_date,
               g.start_time as g_start, g.end_time as g_end,
               g.title, v.venue_name,
               gs.start_time as s_start, gs.end_time as s_end,
               gs.slot_number, gs.status as slot_status,
               g.status as gig_status
        FROM gigs g
        JOIN venues v ON v.id = g.venue_id
        JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
        WHERE g.id != :gid
          AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
    """), {"aid": artist_id, "gid": gig_id}).mappings().all()

    if not rows:
        return {"overlap": False, "within_window": False}

    closest = None
    closest_minutes = None
    overlap_row = None

    for r in rows:
        # Prefer slot times if present (multi-slot), else gig-level times
        st_str = r["s_start"] or r["g_start"]
        et_str = r["s_end"]   or r["g_end"]
        if not st_str or not et_str or not r["gig_date"]:
            continue
        try:
            venue_tz = ZoneInfo(get_venue_timezone_str(db, r["venue_id"]))
            other_start = _dt.strptime(f"{str(r['gig_date'])[:10]} {st_str}", "%Y-%m-%d %H:%M").replace(tzinfo=venue_tz)
            other_end   = _dt.strptime(f"{str(r['gig_date'])[:10]} {et_str}", "%Y-%m-%d %H:%M").replace(tzinfo=venue_tz)
            # Overnight roll: if end <= start, the slot ends after midnight
            if other_end <= other_start:
                other_end = other_end + _td(days=1)
        except Exception:
            continue

        # Overlap: max(starts) < min(ends)
        latest_start  = max(new_start_dt, other_start)
        earliest_end  = min(new_end_dt, other_end)
        if latest_start < earliest_end:
            overlap_row = r
            break  # hard block, no need to look further

        # Proximity: closest distance between this booking and the new one
        if other_end <= new_start_dt:
            gap = (new_start_dt - other_end).total_seconds() / 60.0
            direction = "before"
        elif new_end_dt <= other_start:
            gap = (other_start - new_end_dt).total_seconds() / 60.0
            direction = "after"
        else:
            gap = 0.0
            direction = "overlap"  # caught above
        if closest_minutes is None or gap < closest_minutes:
            closest_minutes = gap
            closest = {"row": r, "direction": direction,
                       "other_start": other_start, "other_end": other_end}

    def _fmt(r):
        return {
            "gig_id": r["gig_id"],
            "venue_name": r["venue_name"],
            "gig_date": str(r["gig_date"])[:10],
            "start_time": r["s_start"] or r["g_start"],
            "end_time":   r["s_end"]   or r["g_end"],
            "title": r["title"] or "",
            "slot_number": r["slot_number"],
        }

    if overlap_row is not None:
        return {"overlap": True, "other": _fmt(overlap_row)}

    if closest_minutes is not None and closest_minutes <= PROXIMITY_HOURS * 60:
        r = closest["row"]
        return {
            "overlap": False,
            "within_window": True,
            "minutes_apart": int(closest_minutes),
            "direction": closest["direction"],  # 'before' = other ends before new starts
            "other": _fmt(r),
        }

    return {"overlap": False, "within_window": False}


def _enforce_no_artist_time_overlap(db, artist_id: int, gig_id: int, venue_id: int,
                                     gig_date, start_time, end_time):
    """Raise HTTPException(403, 'TIME_OVERLAP: ...') if the artist is already
    committed to another gig that overlaps this slot's time window. No-op
    when slot times are missing or the artist has no conflict."""
    dt_pair = _parse_gig_slot_dt(db, venue_id, gig_date, start_time, end_time)
    if not dt_pair:
        return
    new_start, new_end = dt_pair
    conflict = _check_artist_time_conflict(db, artist_id, gig_id, new_start, new_end)
    if conflict.get("overlap"):
        other = conflict["other"]
        raise HTTPException(403,
            f"TIME_OVERLAP: You're already booked at {other['venue_name']} "
            f"on {other['gig_date']} from {other['start_time']} to {other['end_time']}. "
            f"Cancel that booking first if you want to take this one instead.")


def _parse_gig_slot_dt(db, venue_id: int, gig_date, start_time, end_time):
    """Build timezone-aware (start_dt, end_dt) for a gig/slot in the venue's tz.
    Handles overnight (end <= start → end is next day). Returns None on bad input."""
    from datetime import datetime as _dt, timedelta as _td
    from backend.utils import get_venue_timezone_str
    from zoneinfo import ZoneInfo
    if not gig_date or not start_time or not end_time:
        return None
    try:
        tz = ZoneInfo(get_venue_timezone_str(db, venue_id))
        s = _dt.strptime(f"{str(gig_date)[:10]} {start_time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        e = _dt.strptime(f"{str(gig_date)[:10]} {end_time}",   "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        if e <= s:
            e = e + _td(days=1)
        return (s, e)
    except Exception:
        return None


# BOOK GIG (ARTIST) - COMPLETELY REWRITTEN

def _check_artist_matches_gig(db, gig: dict, artist_id: int, slot_id: int = None) -> None:
    """Hard-block bookings where the artist's type or lineup doesn't match
    what the gig (or specific slot) requires.

    Mirrors the artist.book-gigs.js frontend 'blocked' classification
    (lines 604-612) — the UI hides the Book button on a mismatch, but a
    direct API call would otherwise still go through. This server-side
    gate closes that gap so the rules apply at the only layer that
    matters: artist.book-gigs.js doing the UI, this function doing
    the contract.

    Rules:
      - If the requirement has artist_type set, artist.artist_type must
        equal it. A Comedian cannot book a Live Band gig.
      - If the requirement is 'Live Band' AND band_formats is set AND
        the artist's profile has band_formats, require at least one
        overlap. A Full-Band-only artist cannot book a Solo-only gig.
      - Styles (Country, Hip-Hop, etc.) are NOT gated — venues set
        styles as guidance, not a hard filter. A country band playing
        a rock-listed gig is a venue/artist decision, not a system one.

    slot_id: when provided, prefer the slot's per-slot artist_type +
    band_formats (multi-slot gigs can require different lineups per
    slot). Falls back to gig-level when slot has no per-slot override.
    """
    artist = db.execute(
        text("SELECT artist_type, band_formats FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist:
        return  # auth check is elsewhere; nothing to compare against

    # Pull the requirement — slot-level when slot_id provided (multi-slot
    # gigs can override per slot), else gig-level.
    req_type = (gig.get("artist_type") or "").strip()
    req_fmts = gig.get("band_formats") or ""
    if slot_id:
        _slot = db.execute(
            text("SELECT artist_type, band_formats FROM gig_slots WHERE id = :sid"),
            {"sid": slot_id}
        ).mappings().first()
        if _slot:
            _st = (_slot.get("artist_type") or "").strip()
            _sf = _slot.get("band_formats") or ""
            if _st: req_type = _st
            if _sf: req_fmts = _sf

    a_type = (artist.get("artist_type") or "").strip()
    if req_type and a_type and req_type != a_type:
        raise HTTPException(
            400,
            f"ARTIST_TYPE_MISMATCH: This gig requires {req_type}; your artist "
            f"is registered as {a_type}. Switch to a matching artist account "
            f"or skip this gig."
        )

    if req_type == "Live Band":
        req_set = {f.strip() for f in str(req_fmts).split(",") if f.strip()}
        artist_set = {f.strip() for f in str(artist.get("band_formats") or "").split(",") if f.strip()}
        if req_set and artist_set and not (req_set & artist_set):
            req_str = ", ".join(sorted(req_set))
            yours_str = ", ".join(sorted(artist_set)) or "none on file"
            raise HTTPException(
                400,
                f"LINEUP_MISMATCH: This gig requires lineup {req_str}; your "
                f"artist offers {yours_str}. At least one option must overlap."
            )


def _run_prebooking_checks(db, gig_id: int, artist_id: int, venue_id: int,
                           gig_date: str, blast_token: str = "") -> dict:
    """
    Run ALL pre-booking checks for any booking path.
    Returns dict with check results (pref, token_valid, etc).
    Raises HTTPException on any failure.
    """
    from sqlalchemy import text as _t

    # 1. Ban check — always blocks
    if db.execute(_t("SELECT 1 FROM venue_artist_bans WHERE venue_id=:vid AND artist_id=:aid"),
                  {"vid": venue_id, "aid": artist_id}).first():
        raise HTTPException(403, "BANNED: You are not permitted to book at this venue.")

    # 2. Blast token / preferred check
    gig_token = db.execute(_t("SELECT radius_blast_token FROM gigs WHERE id=:gid"),
                           {"gid": gig_id}).scalar()
    token_valid = bool(gig_token) and (not blast_token or blast_token == gig_token)

    if not token_valid:
        pref = db.execute(_t("""SELECT status, frequency_days_override
                                FROM preferred_artists
                                WHERE venue_id=:vid AND artist_id=:aid"""),
                          {"vid": venue_id, "aid": artist_id}).mappings().first()
        if not pref or pref["status"] != "approved":
            # FIX (May 2026): also bypass preferred-status check if the venue has
            # `blast_all_enabled=1` for an open-gig notification whose window has
            # been reached. Otherwise non-preferred artists who received the
            # "any artist can book this gig" email would hit this 403.
            if _open_blast_bypass_active(db, venue_id, gig_id):
                pref = {"status": "blast", "frequency_days_override": None}
            else:
                raise HTTPException(403, "Artist is not approved for this venue")
    else:
        pref = {"status": "approved", "frequency_days_override": None}

    # 3. W9 check
    vtax = db.execute(_t("SELECT require_w9 FROM venue_tax_settings WHERE venue_id=:vid"),
                      {"vid": venue_id}).first()
    if vtax and vtax[0]:
        # TZ FIX (May 21 2026): venue-local year, not server UTC date.
        from datetime import datetime as _dt
        from backend.utils import get_venue_timezone as _gvt
        _current_year = _dt.now(_gvt(db, venue_id)).year
        w9 = db.execute(_t("SELECT tax_year FROM w9_forms WHERE entity_type='artist' AND entity_id=:aid ORDER BY tax_year DESC LIMIT 1"),
                        {"aid": artist_id}).first()
        if not w9 or w9[0] < _current_year:
            raise HTTPException(403, "W9_REQUIRED: This venue requires an up-to-date W-9 on file before booking.")

    # 4. Frequency check (waived on blast token or blast window)
    # TZ FIX (May 21 2026): use venue-local "today" so days_until matches the
    # venue's calendar day, not server UTC. Same bug pattern fixed in
    # book_slot on 2026-05-11 (commit a932ea8) — _run_prebooking_checks
    # was missed at the time, so book-with-contract still 403'd a TONIGHT
    # multi-slot booking when the server (UTC) had already rolled to
    # tomorrow but the venue (Pacific) hadn't.
    _blast_waives = token_valid
    if not _blast_waives:
        try:
            from datetime import date as _dc, datetime as _dt
            from backend.utils import get_venue_timezone as _gvt
            _today = _dt.now(_gvt(db, venue_id)).date()
            _gig_d = _dc.fromisoformat(str(gig_date)[:10])
            _days = (_gig_d - _today).days
            if _days >= 0:
                # Jul 2026 fix: also require waive_frequency=1 on the row. When
                # the venue enables a reminder but explicitly turns waive_freq
                # OFF, we should NOT lift the frequency limit — send the email
                # but respect the venue's cadence policy. Prior version treated
                # every enabled window as auto-waive, which contradicted the
                # per-row toggle we shipped in the July 3 consolidation.
                _brows = db.execute(_t("""SELECT time_value, time_unit FROM venue_email_notifications
                                         WHERE venue_id=:vid AND notification_key IN ('open_gig_36h','open_gig_1w')
                                           AND enabled=1 AND COALESCE(waive_frequency, 1) = 1"""),
                                    {"vid": venue_id}).mappings().all()
                for _br in _brows:
                    _tv, _tu = _br["time_value"], _br["time_unit"]
                    _w = _tv/24 if _tu == "hours" else (_tv if _tu == "days" else _tv*7)
                    if _days <= _w:
                        _blast_waives = True
                        break
        except Exception:
            pass

    # Venue-set frequency-exempt waiver — mirrors book_gig at line ~2407.
    # Without this branch, multi-slot bookings via book_with_contract +
    # contract-required venues silently 403'd freq-exempt artists.
    _exempt_row = db.execute(_t("SELECT COALESCE(frequency_exempt,0) as fx FROM gigs WHERE id=:gid"),
                              {"gid": gig_id}).mappings().first()
    if _exempt_row and _exempt_row.get("fx"):
        _blast_waives = True

    if not _blast_waives:
        freq = db.execute(_t("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
                                FROM preferred_artists pa JOIN venues v ON v.id=pa.venue_id
                                WHERE pa.venue_id=:vid AND pa.artist_id=:aid"""),
                          {"vid": venue_id, "aid": artist_id}).mappings().first()
        if freq and (freq["freq_days"] or 0) > 0:
            # Jul 2026 refactor: dropped `g.artist_id=:aid OR` leg — slot-
            # only covers both shapes post-backfill.
            # 2026-08-08 audit fix (same class as #5-7): outer `g.status IN`
            # excluded partial multi-slot gigs at this venue where the
            # artist held a booked slot but the parent was still 'open'.
            # An artist could then book too frequently. Slot-side status
            # filter alone is correct — a booked slot at the venue counts
            # against the freq policy regardless of parent status.
            close = db.execute(_t("""SELECT g.date FROM gigs g
                                     JOIN gig_slots gs ON gs.gig_id=g.id AND gs.artist_id=:aid
                                     WHERE g.venue_id=:vid AND g.id!=:gid
                                       AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                                     ORDER BY ABS(JULIANDAY(g.date)-JULIANDAY(:d)) LIMIT 1"""),
                               {"vid": venue_id, "aid": artist_id, "gid": gig_id, "d": gig_date}).mappings().first()
            if close:
                from datetime import datetime as _dt
                d1 = _dt.strptime(str(gig_date)[:10], "%Y-%m-%d")
                d2 = _dt.strptime(str(close["date"])[:10], "%Y-%m-%d")
                apart = abs((d1-d2).days)
                if apart <= freq["freq_days"]:
                    needed = freq["freq_days"] + 1 - apart
                    dir_msg = f"You have a gig {'before' if d2>d1 else 'after'} this one on {d2.strftime('%b %d, %Y')}."
                    raise HTTPException(403,
                        f"This venue requires more than {freq['freq_days']} days between performances. "
                        f"{dir_msg} Gigs must be at least {needed} more day{'s' if needed!=1 else ''} apart.")

    # 5. Waitlist lock
    active_offer = db.execute(_t("""SELECT artist_id FROM gig_waitlist
                                    WHERE gig_id=:gid AND offer_sent=1
                                    AND (offer_declined=0 OR offer_declined IS NULL)
                                    AND (offer_expires_at IS NULL OR offer_expires_at > datetime('now'))
                                    ORDER BY id ASC LIMIT 1"""), {"gid": gig_id}).mappings().first()
    if not active_offer:
        active_offer = db.execute(_t("""SELECT artist_id FROM waitlist_offered
                                        WHERE gig_id=:gid AND offer_expires_at>datetime('now')
                                        ORDER BY id ASC LIMIT 1"""), {"gid": gig_id}).mappings().first()
    if active_offer and active_offer["artist_id"] != artist_id:
        raise HTTPException(403, "WAITLIST_LOCKED: This gig has an active waitlist offer to another artist.")

    # 6. Blackout check (FIX May 2026 audit #5: book_slot enforces it inline,
    #    book_gig and contracts.book_with_contract both go through this helper —
    #    centralizing here closes the gap so all three paths reject blackouts.)
    if gig_date:
        _bo = db.execute(_t("""SELECT reason FROM artist_availability
                               WHERE artist_id=:aid
                                 AND date(:d) BETWEEN date(blackout_start) AND date(blackout_end)
                               LIMIT 1"""),
                         {"aid": artist_id, "d": str(gig_date)[:10]}).mappings().first()
        if _bo:
            _r = _bo.get("reason") or "marked as unavailable"
            raise HTTPException(403, f"You have a blackout on this date: {_r}")

    # 7. Stripe Connect onboarding gate (Audit fix May 2026).
    #    Frontend `artist-stripe-payment.js:checkArtistPaymentMethod` already
    #    blocks the Book button when onboarding isn't complete, but a direct
    #    API call (or stale frontend state) could still book the gig — and
    #    the payout would silently `transfer_failed` the day after.
    # Test Mode removed (Jul 1 2026): this now hard-blocks unconditionally.
    try:
        _eps = db.execute(_t("""SELECT COALESCE(stripe_connect_onboarding_complete, 0) as ok
                                 FROM entity_payment_settings
                                 WHERE entity_type = 'artist' AND entity_id = :aid"""),
                          {"aid": artist_id}).mappings().first()
        if not _eps or not int(_eps["ok"] or 0):
            raise HTTPException(
                402,
                "STRIPE_ONBOARDING_INCOMPLETE: Connect your payout account in your artist Payments tab before booking."
            )
    except HTTPException:
        raise
    except Exception:
        # Defensive: if the settings table or column is missing, fall through
        # rather than blocking bookings. The scheduler is the real backstop.
        pass

    # Artist type + lineup match — hard gate. Frontend already hides the
    # Book button for mismatches, but a curl-direct caller would skip
    # that. Pulled here so every booking path that uses _run_prebooking_
    # checks (book_with_contract today, future ones tomorrow) gets it
    # for free. book_gig / book_slot, which inline their own pref check,
    # call _check_artist_matches_gig directly below.
    try:
        _gig_for_check = db.execute(
            text("SELECT artist_type, band_formats FROM gigs WHERE id = :gid"),
            {"gid": gig_id}
        ).mappings().first()
        if _gig_for_check:
            _check_artist_matches_gig(db, dict(_gig_for_check), artist_id)
    except HTTPException:
        raise

    return {"token_valid": token_valid, "pref": pref}


@router.post("/api/gigs/{gig_id}/book")
def book_gig(
    gig_id: int,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    # CRITICAL FIX: Get artist_id from query params
    artist_id = request.query_params.get('artist_id')
    
    if not artist_id:
        # Fallback: try to get from user's artists
        artist = db.execute(
            text("SELECT id FROM artists WHERE user_id = :uid LIMIT 1"),
            {"uid": user.id}
        ).mappings().first()
        
        if not artist:
            raise HTTPException(403, "No artist profile found")
        artist_id = artist["id"]
    else:
        # v97: Check BOTH direct ownership AND entity_users access.
        # Jul 2026: also exclude tombstoned artists (deleted_at IS NOT NULL)
        # so a deleted artist can't be re-attached to a new booking. The
        # row survives for historical joins but is inert for future actions.
        artist = db.execute(
            text("""
                SELECT a.id FROM artists a
                WHERE a.id = :aid
                AND a.deleted_at IS NULL
                AND (
                    a.user_id = :uid
                    OR EXISTS (
                        SELECT 1 FROM entity_users eu
                        WHERE eu.entity_type = 'artist'
                        AND eu.entity_id = a.id
                        AND eu.user_id = :uid
                    )
                )
            """),
            {"aid": int(artist_id), "uid": user.id}
        ).mappings().first()

        if not artist:
            raise HTTPException(403, "Artist does not belong to you or has been deleted")
        artist_id = int(artist_id)

    # Load gig — also pull artist_type + band_formats so the match-gate
    # below can run without a second SELECT.
    gig = db.execute(
        text("""
            SELECT id, venue_id, status, hold_status, date, artist_id, artist_type, band_formats,
                   COALESCE(frequency_exempt, 0) as frequency_exempt
            FROM gigs
            WHERE id = :gid
        """),
        {"gid": gig_id}
    ).mappings().first()

    if not gig:
        raise HTTPException(404, "Gig not found")

    # 2026-08-08 audit fix (finding #12): mirror book_slot's hold_status
    # guard (routes/gigs.py:5003). The current frontend routes all
    # held-gig bookings through /book-with-contract with a hold_token,
    # so this is not reachable via the UI — but a curl-direct call by
    # the currently-offered artist would claim the slot AND freeze the
    # hold rotation (no re-offer to the next artist, hold_status stays
    # 'active', gig hidden from public search). Defense-in-depth.
    _hold_token = (request.query_params.get("hold_token") or "").strip()
    if gig.get("hold_status") in ("active", "exhausted") and not _hold_token:
        raise HTTPException(
            403,
            "This gig is currently held for another artist and cannot be booked directly."
        )

    # Artist type + lineup match gate — see _check_artist_matches_gig
    # docstring. Hard 400 on mismatch. Mirrors the frontend's "blocked"
    # state so curl-direct callers can't slip past.
    _check_artist_matches_gig(db, dict(gig), artist_id)

    # ── Waitlist lock ────────────────────────────────────────────────────────
    # If an exclusive sequential offer is active for this gig, ONLY the artist
    # who received that offer may book. Everyone else is blocked until the offer
    # expires or is declined, then the next artist gets their turn.
    active_offer = db.execute(
        text("""
            SELECT artist_id, offer_expires_at
            FROM gig_waitlist
            WHERE gig_id = :gid
              AND offer_sent = 1
              AND (offer_declined = 0 OR offer_declined IS NULL)
              AND (offer_expires_at IS NULL OR offer_expires_at > datetime('now'))
            ORDER BY id ASC LIMIT 1
        """),
        {"gid": gig_id}
    ).mappings().first()

    if not active_offer:
        # Also check waitlist_offered (row deleted from gig_waitlist on notification)
        active_offer = db.execute(
            text("""SELECT artist_id, offer_expires_at FROM waitlist_offered
                     WHERE gig_id = :gid AND offer_expires_at > datetime('now')
                     ORDER BY id ASC LIMIT 1"""),
            {"gid": gig_id}
        ).mappings().first()

    if active_offer and active_offer["artist_id"] != artist_id:
        raise HTTPException(403, "WAITLIST_LOCKED: This gig has an active waitlist offer to another artist. Please wait for their response before booking.")

    # Allow re-booking if this artist already has a pending_venue_approval on this gig
    if gig["status"] == "pending_venue_approval" and gig.get("artist_id") == artist_id:
        pass  # Artist re-submitting their own pending gig — allow through
    elif gig["status"] != "open":
        raise HTTPException(403, "Gig is not open")

    # FIX (May 21 2026): hard-block if booking this gig would overlap the
    # artist's other commitments at ANY venue. Proximity warning (within 3h)
    # is surfaced by the precheck endpoint — frontend handles confirm flow.
    _enforce_no_artist_time_overlap(db, artist_id, gig_id, gig["venue_id"],
                                    gig.get("date"), gig.get("start_time"), gig.get("end_time"))

    # Check preferred approval — bypass if:
    #   a) a valid radius_blast_token is presented (email deep-link path), OR
    #   b) gig.is_blast_open (calendar direct path — token already stamped on gig)
    blast_token_param = request.query_params.get('blast_token') or ''
    gig_token_row = db.execute(
        text("SELECT radius_blast_token FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    token_valid = (
        # Email token match
        (
            blast_token_param
            and gig_token_row
            and gig_token_row["radius_blast_token"]
            and blast_token_param == gig_token_row["radius_blast_token"]
        )
        # OR calendar direct — gig simply has blast token set (is_blast_open)
        or (gig_token_row and gig_token_row["radius_blast_token"] is not None)
    )

    # Ban check — always blocks, even on blast
    is_banned = db.execute(
        text("SELECT 1 FROM venue_artist_bans WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": gig["venue_id"], "aid": artist_id}
    ).first()
    if is_banned:
        raise HTTPException(403, "BANNED: You are not permitted to book at this venue.")

    if not token_valid:
        pref = db.execute(
            text("""
                SELECT status, frequency_days_override
                FROM preferred_artists
                WHERE venue_id = :vid
                  AND artist_id = :aid
            """),
            {
                "vid": gig["venue_id"],
                "aid": artist_id
            }
        ).mappings().first()

        if not pref or pref["status"] not in ("approved",):
            # FIX (May 2026): same bypass as elsewhere — if the venue has
            # blast_all_enabled=1 for an open-gig blast whose window has been
            # reached, allow non-preferred artists to book.
            if _open_blast_bypass_active(db, gig["venue_id"], gig_id):
                logger.info(f"[BOOK_GIG] open-blast-all bypass — artist {artist_id} booking gig {gig_id}")
                pref = {"status": "blast", "frequency_days_override": None}
            else:
                raise HTTPException(403, "Artist is not approved for this venue")
    else:
        logger.info(f"[BOOK_GIG] blast bypass — artist {artist_id} booking open blast gig {gig_id}")
        pref = {"status": "approved", "frequency_days_override": None}

    # Check if venue requires W9
    venue_tax = db.execute(
        text("SELECT require_w9 FROM venue_tax_settings WHERE venue_id = :vid"),
        {"vid": gig["venue_id"]}
    ).first()
    if venue_tax and venue_tax[0]:
        # TZ FIX: use venue-local "today" so year boundary follows the venue's
        # clock, not server UTC. Otherwise a Pacific venue at 11pm Dec 31 would
        # see UTC = Jan 1 and fail a Dec 31 booking on W-9 grounds.
        from backend.utils import get_venue_timezone
        _w9_tz = get_venue_timezone(db, gig["venue_id"])
        current_year = datetime.now(_w9_tz).year
        w9 = db.execute(
            text("SELECT tax_year FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :aid ORDER BY tax_year DESC LIMIT 1"),
            {"aid": artist_id}
        ).first()
        if not w9 or w9[0] < current_year:
            raise HTTPException(403, "W9_REQUIRED: This venue requires an up-to-date W-9 on file before booking. Please complete your W-9 in the Taxes tab.")

    # Check frequency limitation (skip if gig is frequency_exempt OR valid blast token OR within blast window)
    # Blast window: if the venue has open_gig_36h/open_gig_1w enabled and the gig is within
    # that window, preferred artists have frequency limits waived (they should be able to book).
    _within_blast_window = False
    if not gig["frequency_exempt"] and not token_valid:
        try:
            from datetime import date as _date_cls
            # TZ FIX: use venue-local "today" so days_until matches the venue's
            # calendar day, not server UTC. Off-by-1 near UTC midnight would
            # mis-classify blast windows for west-coast venues.
            from backend.utils import get_venue_timezone as _gvt
            _today = datetime.now(_gvt(db, gig["venue_id"])).date()
            _gig_date = _date_cls.fromisoformat(str(gig["date"])[:10])
            _days_until = (_gig_date - _today).days
            if _days_until >= 0:
                # Jul 2026 fix: gate on waive_frequency — see explanation in
                # _run_prebooking_checks. Venue that disables waive on 1w wants
                # reminders sent but NOT freq waived — respect that here.
                _blast_rows = db.execute(
                    text("""SELECT notification_key, time_value, time_unit
                            FROM venue_email_notifications
                            WHERE venue_id = :vid
                              AND notification_key IN ('open_gig_36h','open_gig_1w')
                              AND enabled = 1
                              AND COALESCE(waive_frequency, 1) = 1"""),
                    {"vid": gig["venue_id"]}
                ).mappings().all()
                for _br in _blast_rows:
                    _tv, _tu = _br["time_value"], _br["time_unit"]
                    _window_days = _tv / 24 if _tu == "hours" else (_tv if _tu == "days" else _tv * 7)
                    if _days_until <= _window_days:
                        _within_blast_window = True
                        break
        except Exception:
            pass

    if not gig["frequency_exempt"] and not token_valid and not _within_blast_window:
        freq_limit = db.execute(
            text("""
                SELECT 
                    COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
                FROM preferred_artists pa
                JOIN venues v ON v.id = pa.venue_id
                WHERE pa.venue_id = :vid AND pa.artist_id = :aid
            """),
            {"vid": gig["venue_id"], "aid": artist_id}
        ).mappings().first()
        
        # 0 = no restriction (venue override allows any booking)
        if freq_limit and (freq_limit["freq_days"] or 0) > 0:
            # Find closest booked gig in EITHER direction (past or future)
            # Check both gigs.artist_id (legacy) AND gig_slots.artist_id (slot-based)
            # gigs.artist_id is NULL for slot-based gigs until all slots booked
            closest_gig = db.execute(
                text("""
                    SELECT g.date
                    FROM gigs g
                    WHERE g.venue_id = :vid
                      AND g.id != :current_gig_id
                      -- Audit fix (May 2026 part 8): include in-flight states
                      -- so an artist with a contract-pending booking can't
                      -- bypass the venue's frequency policy.
                      AND g.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                      AND (
                          g.artist_id = :aid
                          OR EXISTS (
                              SELECT 1 FROM gig_slots gs
                              WHERE gs.gig_id = g.id AND gs.artist_id = :aid
                                AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                          )
                      )
                    ORDER BY ABS(JULIANDAY(g.date) - JULIANDAY(:gig_date))
                    LIMIT 1
                """),
                {"vid": gig["venue_id"], "aid": artist_id, "current_gig_id": gig_id, "gig_date": gig["date"]}
            ).mappings().first()
            
            if closest_gig:
                closest_date = datetime.strptime(closest_gig["date"], "%Y-%m-%d")
                current_date = datetime.strptime(gig["date"], "%Y-%m-%d")
                
                days_apart = abs((current_date - closest_date).days)
                
                if days_apart <= freq_limit["freq_days"]:
                    days_needed = freq_limit["freq_days"] + 1 - days_apart
                    if closest_date > current_date:
                        direction_msg = f"You have a gig booked {days_apart} days later on {closest_date.strftime('%b %d, %Y')}."
                    else:
                        direction_msg = f"You last performed {days_apart} days ago on {closest_date.strftime('%b %d, %Y')}."
                    raise HTTPException(
                        403,
                        f"This venue requires more than {freq_limit['freq_days']} days between performances. "
                        f"{direction_msg} "
                        f"Gigs must be at least {days_needed} more day{'s' if days_needed != 1 else ''} apart."
                    )

    # FIX (May 2026 audit #5): block booking if the artist has a blackout
    # covering the gig date. book_slot already enforces this (~line 3091); the
    # single-slot path was missing it, so an artist with a blackout could still
    # book single-slot gigs through this endpoint.
    if gig.get("date"):
        _blackout = db.execute(
            text("""
                SELECT id, reason FROM artist_availability
                WHERE artist_id = :aid
                  AND date(:d) BETWEEN date(blackout_start) AND date(blackout_end)
                LIMIT 1
            """),
            {"aid": artist_id, "d": str(gig["date"])[:10]}
        ).mappings().first()
        if _blackout:
            _reason = _blackout.get("reason") or "marked as unavailable"
            raise HTTPException(403, f"You have a blackout on this date: {_reason}")

    # Audit fix (May 2026): Stripe Connect onboarding gate. Frontend already
    # blocks the Book button, but a direct API call would otherwise produce
    # a confirmed booking whose payout silently transfer_fails next day.
    # Test Mode removed (Jul 1 2026): this hard-blocks unconditionally.
    try:
        _eps = db.execute(
            text("""SELECT COALESCE(stripe_connect_onboarding_complete, 0) as ok
                    FROM entity_payment_settings
                    WHERE entity_type = 'artist' AND entity_id = :aid"""),
            {"aid": artist_id}
        ).mappings().first()
        if not _eps or not int(_eps["ok"] or 0):
            raise HTTPException(
                402,
                "STRIPE_ONBOARDING_INCOMPLETE: Connect your payout account in your artist Payments tab before booking."
            )
    except HTTPException:
        raise
    except Exception:
        pass

    # (Dead code removed Jun 2026: an unused override query lived here
    # — the real override is applied later at line ~2561 against the
    # OPEN SLOT, not the parent gig.pay. Keeping a leftover query
    # invited the bug of someone reading the wrong-status value.)

    # Same-day booking: only require approval for non-preferred (radius) artists.
    # Preferred artists book directly even on same-day.
    #
    # Audit fix (May 2026 part 2): the previous check inspected `pref`, which
    # may have been REPLACED above with a synthetic `{"status":"approved"}`
    # dict when a blast token was used (line 1741) or with `{"status":"blast"}`
    # for an open-blast bypass (line 1736). A blast-bypassed non-preferred
    # artist would then incorrectly skip the venue-approval flow on a
    # same-day booking. Re-read the underlying preferred_artists row so the
    # check reflects real preferred status, not the synthetic bypass value.
    _real_pref_status_row = db.execute(
        text("""SELECT status FROM preferred_artists
                WHERE venue_id = :vid AND artist_id = :aid"""),
        {"vid": gig["venue_id"], "aid": artist_id}
    ).first()
    _is_preferred_artist = bool(
        _real_pref_status_row and _real_pref_status_row[0] == "approved"
    )
    _ensure_approval_columns(db)
    # Same-day gate now respects the venue owner's `venue_booking_approval_request`
    # preference (Jul 21 2026). Opted-out → auto-confirm same-day; opted-in
    # or default → hold in pending_venue_approval as before.
    if (_is_same_day_booking(gig["date"], gig.get("start_time"), venue_id=gig.get("venue_id"))
            and not _is_preferred_artist
            and _venue_requires_same_day_approval(db, gig.get("venue_id"))):
        db.execute(
            text("""
                UPDATE gigs
                SET artist_id = :aid,
                    status = 'pending_venue_approval',
                    approval_requested_at = :now
                WHERE id = :gid
            """),
            {"aid": artist_id, "now": _utcnow_naive().isoformat(), "gid": gig_id}
        )
        # FIX (May 2026 audit #3): also mark the open slot as pending_venue_approval
        # with the requesting artist's id. Previously only the gigs row was updated,
        # leaving gig_slots in 'open' with artist_id=NULL. On approval, the gig-level
        # branch in approve_booking would only flip gigs.status to 'booked' without
        # touching the slot — slot stayed open, transactions were created with
        # slot_id=None, and later cancel paths matching by gig_slots.artist_id
        # couldn't find the booking.
        _open_slot_for_approval = db.execute(
            text("SELECT id FROM gig_slots WHERE gig_id = :gid AND status = 'open' ORDER BY slot_number ASC LIMIT 1"),
            {"gid": gig_id}
        ).mappings().first()
        if _open_slot_for_approval:
            # Audit RED fix (Jul 1 2026): the same-day-approval slot claim
            # was missing the `WHERE status='open'` + rowcount race guard
            # that every other slot-promotion path uses. Two artists
            # racing "Book" on the same open slot would both pass the
            # SELECT above and both UPDATE — last write wins, but the
            # loser still got a {ok:True, pending_approval:True} response.
            # Now the loser gets 409 SLOT_TAKEN so their UI knows.
            _sd_claim = db.execute(
                text("""UPDATE gig_slots
                        SET artist_id = :aid, status = 'pending_venue_approval',
                            approval_requested_at = :now
                        WHERE id = :sid AND status = 'open'"""),
                {"aid": artist_id, "now": _utcnow_naive().isoformat(), "sid": _open_slot_for_approval["id"]}
            )
            if (_sd_claim.rowcount or 0) == 0:
                db.commit()
                raise HTTPException(409, "SLOT_TAKEN: This slot was just claimed by another artist. Please refresh and try again.")
        gig_details = db.execute(
            text("""
                SELECT g.id, g.date, g.title, g.start_time, g.end_time, g.pay, g.notes,
                       g.artist_type, g.venue_id, g.artist_id,
                       v.venue_name, v.user_id as venue_user_id,
                       a.name as artist_name, a.user_id as artist_user_id
                FROM gigs g
                JOIN venues v ON g.venue_id = v.id
                JOIN artists a ON g.artist_id = a.id
                WHERE g.id = :gid
            """),
            {"gid": gig_id}
        ).mappings().first()
        if gig_details:
            try:
                send_approval_request_emails(db, dict(gig_details), artist_id)
            except Exception as e:
                logger.error(f"[BOOK_GIG] approval request email error: {e}")
        db.commit()  # Commit after email func so approval_token flush is included
        if gig_details:
            # In-app notification to venue
            try:
                create_notification(
                    db,
                    gig_details["venue_user_id"],
                    "booking_approval_request",
                    "⏳ Booking Approval Needed",
                    f"{gig_details['artist_name']} is requesting same-day booking approval for today's gig.",
                    gig_id=gig_id,
                    venue_id=gig['venue_id'],
                    artist_id=artist_id
                )
                db.commit()
            except Exception as e:
                logger.error(f"[BOOK_GIG] approval notification error: {e}")
        return {"ok": True, "pending_approval": True}

    # Find the open slot for this gig (there should be exactly one for single-artist gigs)
    open_slot = db.execute(
        text("SELECT id FROM gig_slots WHERE gig_id = :gid AND status = 'open' ORDER BY slot_number ASC LIMIT 1"),
        {"gid": gig_id}
    ).mappings().first()

    if not open_slot:
        raise HTTPException(403, "No open slots available for this gig")

    # Apply pay override on the slot. Rule (Jun 2026):
    #   - Flat: bump slot.pay to override if higher (always).
    #   - Door: bump guarantee_cents AND mirror into pay ONLY when
    #           slot.apply_override = 1. Default 0 means the door
    #           guarantee floor is final regardless of override —
    #           venue's per-slot opt-in toggle on the door config UI.
    pay_override = db.execute(
        text("""
            SELECT pa.pay_dollars_override, pa.pay_cents_override,
                   gs.pay, gs.deal_type, gs.guarantee_cents,
                   COALESCE(gs.apply_override, 0) as apply_override
            FROM preferred_artists pa
            JOIN gig_slots gs ON gs.gig_id = :gid AND gs.id = :sid
            WHERE pa.venue_id = :vid AND pa.artist_id = :aid
              AND pa.status = 'approved'
        """),
        {"gid": gig_id, "sid": open_slot["id"], "vid": gig["venue_id"], "aid": artist_id}
    ).mappings().first()

    if pay_override and pay_override["pay_dollars_override"] is not None:
        override_pay = float(pay_override["pay_dollars_override"]) + float(pay_override["pay_cents_override"] or 0) / 100
        is_door = (pay_override.get("deal_type") or "").lower() == "door"
        door_opted_in = is_door and int(pay_override.get("apply_override") or 0) == 1
        if is_door and door_opted_in:
            cur_gua_dollars = float(pay_override.get("guarantee_cents") or 0) / 100.0
            if override_pay > cur_gua_dollars:
                db.execute(
                    text("UPDATE gig_slots SET guarantee_cents = :gc, pay = :pay WHERE id = :sid"),
                    {"gc": int(round(override_pay * 100)), "pay": override_pay, "sid": open_slot["id"]}
                )
        elif not is_door:
            if override_pay > float(pay_override["pay"] or 0):
                db.execute(
                    text("UPDATE gig_slots SET pay = :pay WHERE id = :sid"),
                    {"pay": override_pay, "sid": open_slot["id"]}
                )

    # Book the slot — atomic claim guarded by status='open'.
    # Audit fix (May 2026): without the status guard, two artists hitting
    # "Book this slot" within the same few ms could both pass the prior
    # status check and both UPDATE — last write wins, but the first artist
    # would already have a confirmation email + transaction row pointing at
    # a slot that was reassigned to someone else. The conditional UPDATE +
    # rowcount check ensures only one booking can win the race.
    _claim = db.execute(
        text("""UPDATE gig_slots
                SET artist_id = :aid, status = 'booked'
                WHERE id = :sid AND status = 'open'"""),
        {"aid": artist_id, "sid": open_slot["id"]}
    )
    if (_claim.rowcount or 0) == 0:
        # Slot was taken in flight (race lost) or status changed.
        raise HTTPException(409, "SLOT_TAKEN: This slot was just booked by someone else. Please refresh and try a different slot.")
    # Clear cancelled-artist exclusion now that the booking is committed
    db.execute(
        text("UPDATE gigs SET last_cancelled_artist_id = NULL WHERE id = :gid"),
        {"gid": gig_id}
    )

    # Remove artist from waitlist — they booked, no longer waiting
    try:
        db.execute(text("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
                   {"gid": gig_id, "aid": artist_id})
        db.execute(text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
                   {"gid": gig_id, "aid": artist_id})
        # Clear the in-app "Waitlist Spot Available" Activity Center notification.
        from backend.routes.waitlist import _clear_waitlist_offer_notification as _cwon
        _cwon(db, gig_id, artist_id)
    except Exception as _wlr:
        logger.warning(f"[BOOK_GIG] waitlist remove on book error: {_wlr}")

    # Check if all slots now booked → update parent gig status
    open_count = db.execute(
        text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
        {"gid": gig_id}
    ).scalar()
    if open_count == 0:
        db.execute(
            text("UPDATE gigs SET status = 'booked', artist_id = :aid WHERE id = :gid"),
            {"aid": artist_id, "gid": gig_id}
        )

    # Remove ONLY the booking artist from waitlist — keep remaining artists on list
    # The waitlist stays active until gig start time passes or all artists are worked through
    try:
        from sqlalchemy import text as _t2
        db.execute(_t2("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"), {"gid": gig_id, "aid": artist_id})
        db.execute(_t2("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"), {"gid": gig_id, "aid": artist_id})
        db.commit()
    except Exception as _we:
        logger.warning(f"[BOOK_GIG] waitlist cleanup error: {_we}")

    db.commit()

    # Notifications and email — use the unified send_booking_emails
    try:
        # Jul 2026 fix: pull venue_name, artist_name, and the artist's slot
        # start_time so the in-app notification renders "You booked a gig at
        # V on DATE at 8:00 PM" instead of "at " (empty time). Previously the
        # book_gig hot path passed an empty stub dict here — book_slot's
        # equivalent call (gigs.py:5117) already fetched the full details.
        _bg_ctx = db.execute(
            text("""SELECT v.venue_name, a.name as artist_name, gs.start_time
                    FROM gigs g
                    JOIN venues v ON v.id = g.venue_id
                    LEFT JOIN artists a ON a.id = :aid
                    LEFT JOIN gig_slots gs ON gs.id = :sid
                    WHERE g.id = :gid"""),
            {"gid": gig_id, "aid": artist_id, "sid": open_slot["id"]}
        ).mappings().first()
        _bg_details = {
            "venue_name":  (_bg_ctx or {}).get("venue_name") or "",
            "artist_name": (_bg_ctx or {}).get("artist_name") or "",
            "date":        gig["date"],
            "start_time":  (_bg_ctx or {}).get("start_time"),
        }
        notify_gig_booked(db, _bg_details, gig_id, gig["venue_id"], artist_id)
    except Exception as e:
        logger.error(f"[BOOK_GIG] notify error: {e}")
    try:
        send_booking_emails(db, gig_id, slot_id=open_slot["id"])
    except Exception as e:
        logger.error(f"[BOOK_GIG] email error: {e}")

    # Create transaction. A failure here is dangerous: the slot is already
    # booked (atomic UPDATE above) and the artist's confirmation email has
    # fired, but no transaction row → scheduler will never charge the venue
    # or transfer the artist. We swallow rather than re-raise because
    # rolling back the booking would create more confusion (artist already
    # got their confirmation email). Instead: log with FULL context at
    # ERROR level + exc_info so an admin can grep + fix manually via the
    # Payments console (Mark Resolved or manual transaction insert).
    try:
        slot_pay = db.execute(
            text("SELECT pay FROM gig_slots WHERE id = :sid"), {"sid": open_slot["id"]}
        ).scalar()
        _create_booking_transaction(db, gig_id, gig["venue_id"], artist_id, slot_pay, gig["date"], slot_id=open_slot["id"])
    except Exception as e:
        logger.error(
            f"[BOOK_GIG][TXN_MISSING] _create_booking_transaction FAILED — "
            f"gig_id={gig_id} venue_id={gig['venue_id']} artist_id={artist_id} "
            f"slot_id={open_slot['id']} pay={slot_pay if 'slot_pay' in dir() else '?'} "
            f"date={gig.get('date')}. Booking + email already landed; admin must "
            f"create the missing transaction row manually. Error: {e}",
            exc_info=True,
        )

    # Auto-update flyer
    try:
        _, _auf = _get_flyer_helpers()
        _auf(db, gig_id, artist_id)
    except Exception:
        pass

    return {"ok": True}

@router.delete("/api/gigs/{gig_id}/cancel")
async def cancel_gig(gig_id: int, request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    """Cancel a gig (from venue or artist) with optional reason. Body is read explicitly because many clients/servers do not inject DELETE body into a dict param."""
    data = {}
    try:
        body = await request.body()
        if body:
            import json
            data = json.loads(body)
    except Exception:
        pass
    cancelled_by = data.get("cancelled_by", "venue")  # "venue" or "artist"
    cancellation_reason = data.get("cancellation_reason", "")
    keep_open = data.get("keep_open", False)  # If True, reset to open instead of deleting
    request_artist_id = data.get("artist_id")  # For slot bookings where gig.artist_id is NULL

    result = db.execute(
        text("""
            SELECT g.id, g.status, g.artist_id, g.venue_id, g.date, g.title,
                   g.start_time, g.end_time,
                   COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                   v.venue_name, v.user_id as venue_user_id,
                   a.name as artist_name, a.user_id as artist_user_id,
                   u_artist.email as artist_email,
                   u_venue.email as venue_email
            FROM gigs g
            LEFT JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON g.artist_id = a.id
            LEFT JOIN users u_artist ON a.user_id = u_artist.id
            LEFT JOIN users u_venue ON v.user_id = u_venue.id
            WHERE g.id = :gid
        """),
        {"gid": gig_id}
    ).mappings().first()

    if not result:
        raise HTTPException(404, "Gig not found")

    # ── AUTHORIZATION: verify caller owns the venue or artist, AND force
    # `cancelled_by` to match the caller's real role. Without this a
    # venue user could pass `cancelled_by="artist"` (or vice versa),
    # passing either access check but then firing the wrong email
    # subject + notification copy. `cancel_slot` was hardened earlier;
    # this sibling endpoint hadn't been. Audit fix (May 2026 part 3).
    aid_for_role = result.get("artist_id") or (int(request_artist_id) if request_artist_id else None)
    has_venue_access = bool(db.execute(
        text("""
            SELECT 1 FROM venues v WHERE v.id = :vid AND (
                v.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid)
            )
        """),
        {"vid": result["venue_id"], "uid": user.id}
    ).first())
    has_artist_access = False
    if aid_for_role:
        has_artist_access = bool(db.execute(
            text("""
                SELECT 1 FROM artists a WHERE a.id = :aid AND (
                    a.user_id = :uid
                    OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
                )
            """),
            {"aid": aid_for_role, "uid": user.id}
        ).first())
    if not has_venue_access and not has_artist_access:
        raise HTTPException(403, "You don't have access to cancel this gig")
    if cancelled_by == "artist" and not has_artist_access:
        cancelled_by = "venue"
    elif cancelled_by == "venue" and not has_venue_access:
        cancelled_by = "artist"

    # BUG FIX (Jul 2026 audit): refuse cancellation on already-past gigs.
    # A gig that already ended can't be "cancelled" — the show either
    # happened or was a no-show. Firing cancellation emails to venue and
    # artist after the fact is confusing (they may have already played
    # and been paid) and can trigger the transaction cleanup helper
    # against past-settled txns. Compare against the venue's local
    # timezone so we don't false-block a gig that's still upcoming in
    # its own tz.
    try:
        from backend.utils import get_venue_timezone
        _vtz = get_venue_timezone(db, result["venue_id"])
        _end_str = result.get("end_time") or "23:59"
        _gig_end_naive = datetime.strptime(
            f"{str(result['date'])[:10]} {_end_str}",
            "%Y-%m-%d %H:%M"
        )
        _gig_end = _gig_end_naive.replace(tzinfo=_vtz)
        if datetime.now(_vtz) >= _gig_end:
            raise HTTPException(
                409,
                "GIG_ALREADY_ENDED: This gig has already ended — it can't be "
                "cancelled. If the artist was a no-show or the show didn't "
                "happen, contact GigsFill to resolve the payment."
            )
    except HTTPException:
        raise
    except Exception:
        # Malformed date/time — fall through and let downstream handle.
        pass

    # For multi-slot gigs where gig.artist_id is NULL, resolve from request or slot
    effective_artist_id = result.get("artist_id") or request_artist_id
    effective_result = dict(result)
    
    if not result.get("artist_id") and effective_artist_id:
        # Look up artist info from artists table
        artist_info = db.execute(
            text("""
                SELECT a.id as artist_id, a.name as artist_name, a.user_id as artist_user_id,
                       u.email as artist_email
                FROM artists a LEFT JOIN users u ON a.user_id = u.id
                WHERE a.id = :aid
            """),
            {"aid": int(effective_artist_id)}
        ).mappings().first()
        if artist_info:
            effective_result["artist_id"] = artist_info["artist_id"]
            effective_result["artist_name"] = artist_info["artist_name"]
            effective_result["artist_user_id"] = artist_info["artist_user_id"]
            effective_result["artist_email"] = artist_info["artist_email"]

    # Artist cancels: cleanup transactions and contracts.
    # Audit fix (May 2026 part 2): refuse to silently DELETE rows whose
    # money has already moved. The artist needs to coordinate with the
    # venue + admin for a refund instead.
    from backend.services.gig_cleanup import assert_no_charged_transactions
    if cancelled_by == "artist":
        aid = int(effective_artist_id) if effective_artist_id else None
        if aid:
            assert_no_charged_transactions(db, gig_id, aid)
            cleanup_gig_records(db, gig_id, aid)
        else:
            assert_no_charged_transactions(db, gig_id)
            cleanup_gig_records(db, gig_id)

    # Update gig/slot status
    if cancelled_by == "artist":
        aid = int(effective_artist_id) if effective_artist_id else None
        if aid:
            # BUG FIX (Jul 2026 audit): snapshot whether the artist held an
            # active slot BEFORE we reset it. Used below at ~line 3183 to
            # decide whether to fire notify_gig_cancelled. Previously the
            # check ran AFTER this reset, so for a partially-booked multi-slot
            # gig (parent status='open') the reset cleared the slot artist_id
            # to NULL → later check found nothing → venue never got the in-app
            # notification (only the email fired).
            effective_result['_artist_had_active_slot'] = bool(db.execute(
                text("SELECT 1 FROM gig_slots WHERE gig_id=:gid AND artist_id=:aid AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval') LIMIT 1"),
                {"gid": gig_id, "aid": aid}
            ).first())
            # Clear this artist's slot(s), reopen them and restore original gig pay
            db.execute(
                text("""UPDATE gig_slots
                        SET status = 'open', artist_id = NULL,
                            pay = (SELECT g.pay FROM gigs g WHERE g.id = gig_id)
                        WHERE gig_id = :gid AND artist_id = :aid"""),
                {"gid": gig_id, "aid": aid}
            )
            # Track who cancelled so blast emails can exclude them
            _record_artist_cancellation(db, gig_id, aid)
            # Remove any stale waitlist_offered for the cancelling artist
            db.execute(
                text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": aid}
            )
            db.execute(
                text("UPDATE gig_waitlist SET offer_declined = 1 WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": aid}
            )
        # Reopen parent gig if it was booked/pending (includes pending_venue_approval from same-day bookings).
        # Audit fix (May 2026 part 5): NULL `gigs.artist_id` only when it actually points at the
        # cancelling artist. Multi-slot gigs with other slots still booked would otherwise lose the
        # link to whichever artist gigs.artist_id was already pointing at (typically the first booked
        # artist on the gig). Mirrors the venue-cancel keep_open branch below.
        db.execute(
            text("""UPDATE gigs
                    SET status = 'open',
                        artist_id = CASE WHEN artist_id = :aid OR :aid IS NULL THEN NULL ELSE artist_id END,
                        radius_blast_token = NULL,
                        contract_hold_artist_id = NULL,
                        contract_hold_expires_at = NULL,
                        approval_token = NULL,
                        approval_requested_at = NULL
                    WHERE id = :gid
                      AND status IN ('booked', 'pending_contract', 'awaiting_venue_contract', 'open', 'pending_venue_approval')"""),
            {"gid": gig_id, "aid": aid}
        )
        db.commit()
        # FIX (May 2026): delete flyer ONLY if no bookings remain. Multi-slot gigs
        # with other slots still booked keep their custom flyer (the venue's design work).
        # When preserved, also strip just this artist's logo from the canvas.
        _delete_flyer_if_no_bookings_remain(db, gig_id, cancelled_artist_id=aid)
    else:
        # Venue cancels: notify artist, then either reset to open or delete entirely
        if effective_result.get("artist_id"):
            # FIX (May 2026): scope cleanup to JUST the cancelled artist's records.
            # Previously this passed no artist_id, which deletes ALL transactions
            # for the gig — wiping other booked artists' transaction rows on a
            # multi-slot gig before the keep_open safety check at line ~1812
            # forces multi-slot to take the keep-open branch. The "delete entire
            # gig" branch below still runs full cleanup via delete_gig_completely.
            #
            # Audit fix (May 2026 part 2): refuse if the artist's payout already
            # moved money — caller must refund/reverse through Admin → Payments
            # first or the audit trail vanishes.
            assert_no_charged_transactions(db, gig_id, int(effective_result["artist_id"]))
            cleanup_gig_records(db, gig_id, int(effective_result["artist_id"]))
            # FIX (May 2026): record last_cancelled_artist_id so the subsequent
            # cancellation blast excludes this artist. Previously this was only
            # done in the artist-cancels branch, so a venue-cancelled gig would
            # blast back to the artist whose booking was just cancelled — they
            # received "🎵 Gig just opened up" for the gig they were just on.
            _record_artist_cancellation(db, gig_id, effective_result["artist_id"])
            db.commit()
            try:
                notify_all_entity_users_cancelled(
                    db, effective_result, gig_id,
                    effective_result["venue_id"], effective_result["artist_id"],
                    cancelled_by="venue",
                    cancellation_reason=cancellation_reason
                )
                db.commit()
            except Exception as e:
                logger.error(f"Venue cancel notification error: {e}")

        # Safety guard: if other slots in this gig are still booked by OTHER artists,
        # we must NOT delete the whole gig — force keep_open so only the target slot is cleared.
        # Audit fix (May 2026 part 8): include all in-flight states. Previously
        # only 'booked' slots blocked the full-delete path — a slot mid-contract
        # (pending_contract, awaiting_venue_contract) or pending venue approval
        # would be silently destroyed when one artist's booking was venue-cancelled.
        if not keep_open:
            other_booked = db.execute(
                text("""SELECT COUNT(*) FROM gig_slots
                        WHERE gig_id = :gid
                          AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                          AND (artist_id != :aid OR :aid IS NULL)"""),
                {"gid": gig_id, "aid": effective_result.get("artist_id")}
            ).scalar() or 0
            if other_booked > 0:
                logger.info(f"[CANCEL_GIG] venue cancel: {other_booked} other in-flight slot(s) remain — forcing keep_open for gig {gig_id}")
                keep_open = True

        if keep_open:
            # Reset to open — keep gig on calendar, clear artist/contract
            # Only reset slots belonging to the cancelled artist (leaves other booked slots intact)
            if effective_result.get("artist_id"):
                db.execute(
                    text("""UPDATE gig_slots
                            SET status = 'open', artist_id = NULL,
                                pay = (SELECT g.pay FROM gigs g WHERE g.id = :gid)
                            WHERE gig_id = :gid AND artist_id = :aid"""),
                    {"gid": gig_id, "aid": effective_result["artist_id"]}
                )
            else:
                # No specific artist — reset all slots
                db.execute(
                    text("""UPDATE gig_slots
                            SET status = 'open', artist_id = NULL,
                                pay = (SELECT g.pay FROM gigs g WHERE g.id = :gid)
                            WHERE gig_id = :gid"""),
                    {"gid": gig_id}
                )
            # Audit fix (May 2026 part 3): NULL `gigs.artist_id` only when
            # it actually points at the cancelling artist. Multi-slot
            # gigs with other slots still booked would otherwise lose
            # the link to whichever artist `gigs.artist_id` was already
            # pointing at (typically the first booked artist on the gig).
            # Also drop the stray `:gig` bound parameter — never used.
            _cancelled_aid_for_clear = effective_result.get("artist_id")
            db.execute(
                text("""UPDATE gigs
                        SET status = 'open',
                            artist_id = CASE WHEN artist_id = :aid OR :aid IS NULL THEN NULL ELSE artist_id END,
                            radius_blast_token = NULL,
                            contract_hold_artist_id = NULL,
                            contract_hold_expires_at = NULL
                        WHERE id = :gid"""),
                {"gid": gig_id, "aid": _cancelled_aid_for_clear}
            )
            # BUG FIX (Jul 2026 audit): mirror the artist-cancel and cancel_slot
            # branches' waitlist cleanup here. Without it, the cancelled
            # artist's stale gig_waitlist row (created by any previous
            # waitlist-join that silently succeeded on top of an active
            # booking) would let notify_waitlist re-offer them the very slot
            # they just lost.
            if _cancelled_aid_for_clear:
                try:
                    db.execute(
                        text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
                        {"gid": gig_id, "aid": _cancelled_aid_for_clear}
                    )
                    db.execute(
                        text("UPDATE gig_waitlist SET offer_declined = 1 WHERE gig_id = :gid AND artist_id = :aid"),
                        {"gid": gig_id, "aid": _cancelled_aid_for_clear}
                    )
                except Exception as _wl_e:
                    logger.warning(f"cancel_gig venue keep_open waitlist cleanup skip: {_wl_e}")
            # Hold cleanup (Jun 2026): if the gig had an active or
            # exhausted hold cycle, wind it down so the gig actually
            # becomes publicly bookable. Without this, hold_status
            # stayed 'active'/'exhausted' and the gig remained hidden
            # from /api/gigs/public + blasts. Mark in-flight offers
            # consumed (no email-link revival) but keep the rows for
            # audit history.
            cleanup_hold_records(db, gig_id, delete_rows=False)
            # Audit fix (Jun 2026): also kill the cancelled artist's
            # pending_approval_tokens. The full-delete branch already
            # nukes them gig-wide (line 2990) — the keep_open branch
            # was missing this, so the artist could click a stale
            # email-approval link after their slot was reopened.
            try:
                _aid_for_tok = effective_result.get("artist_id")
                if _aid_for_tok:
                    db.execute(
                        text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid AND artist_id = :aid"),
                        {"gid": gig_id, "aid": _aid_for_tok}
                    )
                else:
                    db.execute(
                        text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid"),
                        {"gid": gig_id}
                    )
            except Exception as _ptke:
                logger.warning(f"cancel_gig keep_open: pending_approval_tokens cleanup failed for gig {gig_id}: {_ptke}")
            db.commit()
            # FIX (May 2026): delete flyer ONLY if no bookings remain. Multi-slot gigs
            # with other slots still booked keep their custom flyer. When preserved,
            # also strip just this artist's logo from the canvas.
            _delete_flyer_if_no_bookings_remain(db, gig_id, cancelled_artist_id=effective_result.get("artist_id"))
        else:
            # Delete entirely.
            # Audit fix (May 2026 part 6): re-assert at GIG scope (artist_id=None)
            # before delete_gig_completely. The earlier artist-scope assert above
            # only covers transactions tied to a specific artist; the parent
            # venue_charge row has artist_id=NULL on multi-slot gigs, so a
            # charged parent could slip past and get wiped by the full-gig
            # cleanup below.
            assert_no_charged_transactions(db, gig_id)
            # Audit fix (May 2026 part 4): each cleanup was silently
            # swallowing all errors, which hid table-doesn't-exist /
            # FK-violation problems alike. Now we log with the stack
            # trace; the cleanups themselves are still best-effort so
            # one missing table doesn't block the rest.
            try:
                db.execute(text("DELETE FROM flyers WHERE gig_id = :gid AND is_template = 0"), {"gid": gig_id})
            except Exception as _e:
                logger.warning(f"cancel_gig: flyers cleanup failed for gig {gig_id}: {_e}", exc_info=True)
            try:
                db.execute(text("DELETE FROM notification_sent_log WHERE gig_id = :gid"), {"gid": gig_id})
            except Exception as _e:
                logger.warning(f"cancel_gig: notification_sent_log cleanup failed for gig {gig_id}: {_e}", exc_info=True)
            try:
                db.execute(text("DELETE FROM public_activity WHERE gig_id = :gid"), {"gid": gig_id})
            except Exception as _e:
                logger.warning(f"cancel_gig: public_activity cleanup failed for gig {gig_id}: {_e}", exc_info=True)
            try:
                db.execute(text("DELETE FROM notifications WHERE gig_id = :gid"), {"gid": gig_id})
            except Exception as _e:
                logger.warning(f"cancel_gig: notifications cleanup failed for gig {gig_id}: {_e}", exc_info=True)
            # Audit fix (May 2026 part 7): drop pending_approval_tokens for the
            # cancelled gig so future email-link clicks land on the friendly
            # "Already Cancelled" page instead of "no pending approval" + 404.
            try:
                db.execute(text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid"), {"gid": gig_id})
            except Exception as _e:
                logger.warning(f"cancel_gig: pending_approval_tokens cleanup failed for gig {gig_id}: {_e}", exc_info=True)
            delete_gig_completely(db, gig_id)
            db.commit()

    # If gig was booked OR in contract flow (and artist cancel path), notify BOTH parties
    # pending_contract / awaiting_venue_contract = contract was signed but not yet countersigned
    # For multi-slot: gig.status may be 'open' even when a slot was booked/pending
    # BUG FIX (Jul 2026 audit): prefer the pre-reset snapshot when available.
    # For the artist-cancel branch the snapshot was taken before slot mutation
    # (see effective_result['_artist_had_active_slot'] set above); for
    # venue-cancel we still run the post-hoc query. The old code always did
    # the post-hoc query and mis-reported "not active" for partial multi-slot
    # gigs, silently skipping the in-app notification to venue.
    _slot_was_active = bool(effective_result.get('_artist_had_active_slot'))
    if not _slot_was_active and effective_result.get("artist_id"):
        try:
            _slot_was_active = bool(db.execute(
                text("SELECT 1 FROM gig_slots WHERE gig_id=:gid AND artist_id=:aid AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval') LIMIT 1"),
                {"gid": gig_id, "aid": effective_result["artist_id"]}
            ).first())
        except Exception:
            pass
    _was_active = effective_result["status"] in ("booked", "pending_contract", "awaiting_venue_contract") or _slot_was_active
    try:
      if _was_active and effective_result.get("artist_id") and cancelled_by == "artist":
        notify_gig_cancelled(
            db, effective_result, gig_id,
            effective_result["venue_id"], effective_result["artist_id"],
            cancelled_by="artist", cancellation_reason=cancellation_reason
        )
        db.commit()
    except Exception as e:
        logger.error(f"Cancel notification error: {e}")

    # Send cancellation emails in background thread so cancel returns fast
    # Also fire for contract cancellations (pending_contract / awaiting_venue_contract)
    _email_artist_id = effective_result.get("artist_id") or (int(request_artist_id) if request_artist_id else None)
    if _email_artist_id:
        import threading as _threading
        import copy as _copy
        _details = dict(effective_result)
        # Ensure artist_id is always populated — may be None on contract gigs
        if not _details.get("artist_id"):
            _details["artist_id"] = _email_artist_id
        # Ensure artist_name is populated if missing
        if not _details.get("artist_name") and _email_artist_id:
            try:
                _a = db.execute(
                    text("SELECT name FROM artists WHERE id = :aid"),
                    {"aid": _email_artist_id}
                ).mappings().first()
                if _a:
                    _details["artist_name"] = _a["name"]
            except Exception:
                pass
        # For multi-slot gigs: use the cancelled SLOT's time, not parent gig time
        # Parent gig start/end spans ALL slots (e.g. 7pm-11pm for two slots)
        if request_artist_id and effective_result.get("is_multi_slot"):
            try:
                _slot = db.execute(
                    text("""SELECT start_time, end_time, pay FROM gig_slots
                            WHERE gig_id = :gid AND artist_id = :aid
                            ORDER BY id DESC LIMIT 1"""),
                    {"gid": gig_id, "aid": int(request_artist_id)}
                ).mappings().first()
                if _slot:
                    if _slot["start_time"]: _details["start_time"] = _slot["start_time"]
                    if _slot["end_time"]:   _details["end_time"]   = _slot["end_time"]
                    if _slot["pay"]:        _details["pay"]        = _slot["pay"]
            except Exception:
                pass
        _reason  = cancellation_reason
        _cancelled_by = cancelled_by
        def _send_cancel_emails_bg():
            try:
                from backend.db import SessionLocal as _SL
                _db2 = _SL()
                try:
                    logger.info(f"[CANCEL EMAIL] thread: gig={_details.get('id')}, artist_id={_details.get('artist_id')}, venue_id={_details.get('venue_id')}, cancelled_by={_cancelled_by}")
                    send_cancellation_emails(_db2, _details, cancellation_reason=_reason, cancelled_by=_cancelled_by)
                finally:
                    _db2.close()
            except Exception as _e:
                logger.error(f"[CANCEL EMAIL] thread exception: {_e}", exc_info=True)
        _threading.Thread(target=_send_cancel_emails_bg, daemon=True).start()

    # Fire cancelled-gig preferred blast if: gig is now open (keep_open or artist cancel) AND within 7 days
    gig_is_now_open = keep_open or (cancelled_by == "artist")
    logger.info(f"[BLAST] cancel_gig: keep_open={keep_open}, cancelled_by={cancelled_by}, gig_is_now_open={gig_is_now_open}, gig_id={gig_id}, venue_id={effective_result.get('venue_id')}")
    if gig_is_now_open:
        _gig_id_bg  = gig_id
        _venue_id_bg = effective_result.get("venue_id")
        # Run notify_waitlist SYNCHRONOUSLY so offer_sent=1 is written to DB before we
        # return — the frontend immediately refetches /gigs and must see has_active_waitlist=1
        try:
            from backend.routes.waitlist import notify_waitlist, _has_active_waitlist
            db.expire_all()
            if _has_active_waitlist(db, _gig_id_bg):
                notify_waitlist(db, _gig_id_bg)
                # Blast (if needed) runs in background after waitlist is handled
                return {"ok": True}
        except Exception as _wle:
            logger.error(f"notify_waitlist sync error: {_wle}", exc_info=True)
        # No waitlist — run blast then send venue summary email
        import threading as _threading2
        def _blast_bg():
            try:
                from backend.db import SessionLocal as _SL2
                _db3 = _SL2()
                try:
                    fire_cancelled_gig_blast(_db3, _gig_id_bg, _venue_id_bg)
                    # Send venue summary email explaining what blast was sent
                    # Guard: only send if gig is still open/unbooked
                    try:
                        _gig_still_open = _db3.execute(
                            __import__('sqlalchemy').text("SELECT status FROM gigs WHERE id=:gid"),
                            {"gid": _gig_id_bg}
                        ).scalar()
                        if _gig_still_open not in ('open', 'pending_contract', 'awaiting_venue_contract'):
                            logger.info(f"[BLAST] Skipping venue summary — gig {_gig_id_bg} is now {_gig_still_open}")
                            return
                    except Exception:
                        pass
                    try:
                        from backend.routes.waitlist import _send_waitlist_exhausted_email, _get_platform_now
                        from datetime import datetime
                        gig_row = _db3.execute(
                            __import__('sqlalchemy').text("""
                                SELECT g.*, v.venue_name, v.id as venue_id, v.city, v.state,
                                       v.latitude as venue_lat, v.longitude as venue_lng,
                                       COALESCE(ven.radius_miles, 20) as blast_radius_miles
                                FROM gigs g
                                LEFT JOIN venues v ON v.id = g.venue_id
                                LEFT JOIN venue_email_notifications ven ON ven.venue_id = g.venue_id AND ven.notification_key = 'radius_blast'
                                WHERE g.id = :gid
                            """),
                            {"gid": _gig_id_bg}
                        ).mappings().first()
                        if gig_row:
                            gig_date_str = str(gig_row.get("date", ""))[:10]
                            start_time_str = str(gig_row.get("start_time", "00:00"))
                            try:
                                gig_dt = datetime.fromisoformat(f"{gig_date_str}T{start_time_str[:5]}")
                                _now_p = _get_platform_now(_db3).replace(tzinfo=None)
                                hours_until = max(0.0, (gig_dt - _now_p).total_seconds() / 3600)
                                _days_away = (gig_dt.date() - _now_p.date()).days
                            except Exception:
                                hours_until = 0.0
                                _days_away = 0
                            if -1 <= _days_away <= 14:
                                radius_notif = _db3.execute(
                                    __import__('sqlalchemy').text("SELECT enabled, radius_miles FROM venue_email_notifications WHERE venue_id = :vid AND notification_key = 'radius_blast'"),
                                    {"vid": _venue_id_bg}
                                ).mappings().first()
                                radius_enabled = (radius_notif is None) or bool(radius_notif.get("enabled", True))
                                radius_miles = int((radius_notif and radius_notif.get("radius_miles")) or 20) if radius_enabled else 0
                                _send_waitlist_exhausted_email(_db3, _gig_id_bg, dict(gig_row),
                                                               hours_until, radius_enabled=radius_enabled,
                                                               radius_miles=radius_miles)
                                logger.info(f"[BLAST] Sent venue summary email for gig {_gig_id_bg}")
                    except Exception as _vse:
                        logger.error(f"[BLAST] Venue summary email error: {_vse}", exc_info=True)
                finally:
                    _db3.close()
            except Exception as _e:
                logger.error(f"Background blast error: {_e}", exc_info=True)
        _threading2.Thread(target=_blast_bg, daemon=True).start()

    # Audit trail — cancel_gig deletes money-adjacent state
    # (transactions, contracts, flyers). Log so a future incident can
    # reconstruct what was cancelled by whom. (Jul 2026 delete-audit.)
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, user, "gig_cancel",
            target_table="gigs", target_id=gig_id,
            metadata={
                "endpoint": "cancel_gig",
                "keep_open": bool(data.get("keep_open", False)),
                "cancellation_reason": (data.get("cancellation_reason") or "")[:200],
            },
        )
    except Exception:
        pass

    return {"ok": True}

@router.delete("/gigs/{gig_id}")
def delete_gig(gig_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    from backend.utils import check_venue_access
    result = db.execute(
        text("""
            SELECT g.status, g.artist_id, g.venue_id, g.date, g.title,
                   g.start_time, g.end_time,
                   v.venue_name, a.name as artist_name, a.user_id as artist_user_id
            FROM gigs g
            LEFT JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON g.artist_id = a.id
            WHERE g.id = :gid
        """),
        {"gid": gig_id}
    ).mappings().first()

    if not result:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, result["venue_id"], user.id)

    # Audit fix #14 (May 2026): refuse if a charged/paid transaction exists.
    # Previously delete_gig_completely silently DELETEd transactions of any
    # status — wiping the audit trail of money already moved. Use the
    # cancel-payment refund flow for charged gigs instead.
    # Audit fix (May 2026 part 6): use the canonical helper so the tuple stays
    # in sync with services/gig_cleanup.CHARGED_TRANSACTION_STATUSES (which
    # now includes dispute / processing states).
    from backend.services.gig_cleanup import assert_no_charged_transactions as _assert_no_charged
    _assert_no_charged(db, gig_id)

    # Audit fix #14 (May 2026): fan out cancellation to ALL artist/venue
    # users and send proper cancellation emails. Previously only the primary
    # artist_user_id got an in-app notification and no email was sent.
    #
    # Audit fix (Jul 2026 delete-audit): enumerate EVERY booked slot's
    # artist across every in-flight status (booked / pending_contract /
    # awaiting_venue_contract / pending_venue_approval), not just the
    # legacy parent `result["artist_id"]` at status='booked'. Multi-slot
    # gigs stored the booked artist on `gig_slots.artist_id` — the old
    # branch here silently missed them and mid-contract artists.
    try:
        _booked_slots = db.execute(
            text("""
                SELECT DISTINCT gs.artist_id, a.name as artist_name
                FROM gig_slots gs
                JOIN artists a ON a.id = gs.artist_id
                WHERE gs.gig_id = :gid
                  AND gs.artist_id IS NOT NULL
                  AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
            """),
            {"gid": gig_id}
        ).mappings().all()
    except Exception:
        _booked_slots = []
    for _bs in _booked_slots:
        try:
            notify_gig_cancelled(
                db, dict(result), gig_id, result["venue_id"], _bs["artist_id"],
                cancelled_by="venue", cancellation_reason=""
            )
        except Exception as _ne:
            logger.warning(f"delete_gig notify_gig_cancelled error (artist={_bs['artist_id']}): {_ne}")
        try:
            send_cancellation_emails(
                db,
                {
                    "id": gig_id,
                    "artist_name": _bs["artist_name"] or result.get("artist_name", "Artist"),
                    "venue_name": result.get("venue_name", ""),
                    "artist_id": _bs["artist_id"],
                    "venue_id": result["venue_id"],
                    "date": result["date"],
                    "start_time": result.get("start_time"),
                    "end_time": result.get("end_time"),
                },
                cancellation_reason="",
                cancelled_by="venue",
            )
        except Exception as _ee:
            logger.warning(f"delete_gig send_cancellation_emails error (artist={_bs['artist_id']}): {_ee}")

    # If gig is in waitlist mode, notify the artist who holds the active offer
    try:
        offered = db.execute(
            text("""SELECT wo.artist_id, wo.user_id, a.name as artist_name
                    FROM waitlist_offered wo
                    JOIN artists a ON a.id = wo.artist_id
                    WHERE wo.gig_id = :gid
                      AND wo.offer_expires_at > datetime('now')
                    LIMIT 1"""),
            {"gid": gig_id}
        ).mappings().first()
        if offered:
            _date_str = format_email_date(str(result["date"])) if result["date"] else "your upcoming date"
            create_notification(
                db, offered["user_id"], "gig_cancelled", "Waitlist Offer Cancelled",
                f"Sorry — the gig at {result['venue_name']} on {_date_str} that you were offered "
                f"has been deleted by the venue. The offer is no longer valid.",
                gig_id=gig_id, venue_id=result["venue_id"], artist_id=offered["artist_id"]
            )
    except Exception as _we:
        logger.warning(f"Could not notify waitlist artist on gig delete: {_we}")

    # Clean up all related records and delete the gig
    try:
        try:
            db.execute(text("DELETE FROM flyers WHERE gig_id = :gid AND is_template = 0"), {"gid": gig_id})
        except Exception: pass
        try:
            # Audit fix (May 2026 part 5): bind-param key was "gig_id" but the
            # placeholder is :gid — SQLAlchemy raised, the surrounding bare
            # except: pass swallowed it, so notification_sent_log was never
            # cleaned. Stale rows persisted and suppressed emails when a gig
            # was re-created with the same id.
            db.execute(text("DELETE FROM notification_sent_log WHERE gig_id = :gid"), {"gid": gig_id})
        except Exception:
            pass  # table may not exist on all deployments
        # Audit fix (May 2026 part 7): drop pending_approval_tokens for this gig.
        try:
            db.execute(text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid"), {"gid": gig_id})
        except Exception:
            pass
        try:
            db.execute(text("DELETE FROM public_activity WHERE gig_id = :gid"), {"gid": gig_id})
        except Exception: pass
        try:
            db.execute(text("DELETE FROM notifications WHERE gig_id = :gid"), {"gid": gig_id})
        except Exception: pass
        delete_gig_completely(db, gig_id)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to delete gig {gig_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(500, f"Delete failed: {str(e)}")
    
    return {"ok": True}

@router.put("/gigs/{gig_id}")
def update_gig(gig_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    from backend.utils import check_venue_access
    _validate_door_slots(data.get("slots", []))
    gig = db.execute(
        text("SELECT venue_id, date, start_time, end_time FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)

    # FIX (May 2026 audit #10): backend in-progress guard.
    # The frontend hides Save Changes when a gig is mid-window (Changelog
    # 2026-05-08), but a direct API call or a stale browser tab can still
    # corrupt an in-progress gig — overwrite pay/times, delete open slots,
    # renumber. Refuse the edit if the gig has already started.
    #
    # TZ FIX (May 11 2026): the prod server runs UTC but gig.start_time is
    # stored as venue-local (e.g. "19:00" = 7pm Pacific). Naive datetime.now()
    # returns UTC, so a gig 6 hours before its actual start was being flagged
    # in-progress and the venue's Save click was 409'd silently. Compare in
    # the venue's timezone instead.
    if gig.get("date") and gig.get("start_time"):
        try:
            from backend.utils import get_venue_timezone
            _venue_tz = get_venue_timezone(db, gig["venue_id"])
            _start_naive = datetime.strptime(
                f"{str(gig['date'])[:10]} {gig['start_time']}",
                "%Y-%m-%d %H:%M"
            )
            _start = _start_naive.replace(tzinfo=_venue_tz)
            if datetime.now(_venue_tz) >= _start:
                raise HTTPException(409, "GIG_IN_PROGRESS: Cannot edit a gig that has already started.")
        except ValueError:
            # Malformed time string — fall through; the rest of the endpoint
            # has its own validators and will reject if the data is broken.
            pass
    # Normal gig update — check for time overlap with OTHER gigs at same venue/date first
    new_start = data.get("start_time")
    new_end   = data.get("end_time")
    new_date  = data.get("date")

    if new_start and new_end and new_date:
        venue_id_for_check = gig[0]
        conflict = db.execute(
            text("""
                SELECT g.id, g.start_time, g.end_time
                FROM gigs g
                WHERE g.venue_id = :vid AND g.date = :date
                  AND g.id != :gid
                  AND g.status NOT IN ('cancelled','deleted')
                  AND g.start_time < :new_end
                  AND g.end_time > :new_start
            """),
            {"vid": venue_id_for_check, "date": new_date, "gid": gig_id,
             "new_start": new_start, "new_end": new_end}
        ).first()
        if conflict:
            raise HTTPException(
                409,
                f"Updated time ({new_start}–{new_end}) overlaps with an existing gig "
                f"at this venue on the same day. Please choose a different time."
            )

    db.execute(
        text("""
            UPDATE gigs
            SET
              title = :title,
              start_time = :start_time,
              end_time = :end_time,
              pay = :pay,
              notes = :notes,
              artist_type = :artist_type,
              band_formats = :band_formats,
              styles = :styles
            WHERE id = :gid AND status NOT IN ('cancelled', 'deleted')
        """),
        {
            "gid": gig_id,
            "title": data.get("title"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "pay": data.get("pay"),
            "notes": data.get("notes"),
            "artist_type": data.get("artist_type"),
            "band_formats": data.get("band_formats"),
            "styles": data.get("styles"),
        }
    )
    
    # Handle slots update if provided
    slots = data.get("slots")
    if slots is not None:
        
        # Delete only OPEN (unbooked) slots
        db.execute(
            text("DELETE FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
            {"gid": gig_id}
        )
        
        # Get max slot_number from remaining booked slots
        row = db.execute(
            text("SELECT COALESCE(MAX(slot_number), 0) as max_num FROM gig_slots WHERE gig_id = :gid"),
            {"gid": gig_id}
        ).fetchone()
        next_num = row[0] + 1
        
        # Insert new open slots
        for s in slots:
            db.execute(
                text("""
                    INSERT INTO gig_slots (gig_id, slot_number, start_time, end_time, pay, status,
                                           artist_type, band_formats, styles, deal_type, door_pct, guarantee_cents, apply_override, requires_equipment)
                    VALUES (:gig_id, :slot_number, :start_time, :end_time, :pay, 'open',
                            :artist_type, :band_formats, :styles, COALESCE(:deal_type, 'flat'), COALESCE(:door_pct, 0), COALESCE(:guarantee_cents, 0), COALESCE(:apply_override, 0), :requires_equipment)
                """),
                {
                    "gig_id": gig_id,
                    "slot_number": next_num,
                    "start_time": s["start_time"],
                    "end_time": s["end_time"],
                    "pay": s.get("pay", 0),
                    "artist_type": s.get("artist_type"),
                    "band_formats": s.get("band_formats"),
                    "styles": s.get("styles"),
                    "deal_type": s.get("deal_type"),
                    "door_pct": s.get("door_pct"),
                    "guarantee_cents": s.get("guarantee_cents"),
                    "apply_override": 1 if s.get("apply_override") else 0,
                    "requires_equipment": (1 if bool(s.get("requires_equipment")) else 0) if s.get("requires_equipment") is not None else None,
                }
            )
            next_num += 1
        
        # Renumber all slots by start_time
        all_slots = db.execute(
            text("SELECT id FROM gig_slots WHERE gig_id = :gid ORDER BY start_time ASC"),
            {"gid": gig_id}
        ).fetchall()
        for i, slot_row in enumerate(all_slots, 1):
            db.execute(
                text("UPDATE gig_slots SET slot_number = :num WHERE id = :sid"),
                {"num": i, "sid": slot_row[0]}
            )
        
        # Update parent gig start/end + pay from all slots so other endpoints
        # (gig list, modal, effective-pay, scheduler) read the correct values.
        # FIX (May 2026): pay was previously NOT synced — only start/end. Result:
        # editing a slot's pay would update emails (which read slot.pay) but the
        # venue's gig details modal still showed the stale parent gig.pay value.
        # PROD BUG (May 10 2026): MIN(start_time)/MAX(end_time) string-sorts
        # times, which breaks overnight gigs ("01:00" < "23:00" lexically).
        # Use slot_number ordering — slot 1's start = chronological start,
        # last slot's end = chronological end.
        times = db.execute(
            text("""
                SELECT
                  (SELECT start_time FROM gig_slots WHERE gig_id = :gid ORDER BY slot_number ASC  LIMIT 1) as st,
                  (SELECT end_time   FROM gig_slots WHERE gig_id = :gid ORDER BY slot_number DESC LIMIT 1) as et,
                  (SELECT MAX(pay)   FROM gig_slots WHERE gig_id = :gid)                                  as max_pay
            """),
            {"gid": gig_id}
        ).fetchone()
        if times and times[0]:
            db.execute(
                text("UPDATE gigs SET start_time = :st, end_time = :et, pay = :pay WHERE id = :gid"),
                {"gid": gig_id, "st": times[0], "et": times[1], "pay": (times[2] if times[2] is not None else 0)}
            )

    db.commit()
    return {"ok": True}


@router.post("/api/gigs/{gig_id}/detach-series")
def detach_from_series(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Remove a single gig from its recurring series, making it a standalone gig."""
    from backend.utils import check_venue_access
    gig = db.execute(text("SELECT venue_id FROM gigs WHERE id = :gid"), {"gid": gig_id}).first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig[0], user.id)
    db.execute(
        text("UPDATE gigs SET recurring_group_id = NULL, is_recurring = 0 WHERE id = :gid"),
        {"gid": gig_id}
    )
    db.commit()
    return {"ok": True}


@router.put("/api/gigs/{gig_id}/booked-edit")
def booked_edit_gig(gig_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Update a booked gig: title, notes, slot times/pay. Does NOT change artist type/styles/lineup."""
    from backend.utils import check_venue_access
    gig = db.execute(text("SELECT venue_id FROM gigs WHERE id = :gid"), {"gid": gig_id}).first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig[0], user.id)

    # Update title, notes on the gig (any status)
    db.execute(
        text("""
            UPDATE gigs
            SET title = :title, notes = :notes
            WHERE id = :gid
        """),
        {"gid": gig_id, "title": data.get("title"), "notes": data.get("notes")}
    )

    # Update each slot: times and pay only (preserve status, artist, etc.)
    slots = data.get("slots", [])
    for s in slots:
        slot_num = s.get("slot_number")
        if slot_num is None:
            continue
        db.execute(
            text("""
                UPDATE gig_slots
                SET start_time = :start_time,
                    end_time   = :end_time,
                    pay        = :pay,
                    deal_type        = COALESCE(:deal_type, deal_type),
                    door_pct         = COALESCE(:door_pct, door_pct),
                    guarantee_cents  = COALESCE(:guarantee_cents, guarantee_cents),
                    apply_override   = COALESCE(:apply_override, apply_override)
                WHERE gig_id = :gig_id AND slot_number = :slot_number
            """),
            {
                "gig_id":     gig_id,
                "slot_number": slot_num,
                "start_time": s.get("start_time"),
                "end_time":   s.get("end_time"),
                "pay":        s.get("pay", 0),
                "deal_type":       s.get("deal_type"),
                "door_pct":        s.get("door_pct"),
                "guarantee_cents": s.get("guarantee_cents"),
                "apply_override":  (1 if s.get("apply_override") else 0) if s.get("apply_override") is not None else None,
            }
        )

    # Delete OPEN slots that were removed in the UI (not present in submitted list)
    submitted_nums = [int(s.get("slot_number")) for s in slots if s.get("slot_number") is not None]
    if submitted_nums:
        # Build parameterized placeholders to avoid any SQL injection risk
        param_keys = {f"sn{i}": n for i, n in enumerate(submitted_nums)}
        ph = ", ".join(f":{k}" for k in param_keys)
        params = {"gid": gig_id, **param_keys}
        db.execute(
            text(f"DELETE FROM gig_slots WHERE gig_id = :gid AND status = \'open\' AND slot_number NOT IN ({ph})"),
            params
        )
    else:
        # No slots submitted — delete all open slots
        db.execute(
            text("DELETE FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
            {"gid": gig_id}
        )

    # Add any NEW open slots (slot_number not yet in DB)
    existing_nums = set(
        r[0] for r in db.execute(
            text("SELECT slot_number FROM gig_slots WHERE gig_id = :gid"),
            {"gid": gig_id}
        ).fetchall()
    )
    for s in slots:
        slot_num = s.get("slot_number")
        if slot_num not in existing_nums:
            db.execute(
                text("""
                    INSERT INTO gig_slots (gig_id, slot_number, start_time, end_time, pay, status,
                                           artist_type, band_formats, styles, deal_type, door_pct, guarantee_cents, requires_equipment)
                    VALUES (:gig_id, :slot_number, :start_time, :end_time, :pay, 'open',
                            :artist_type, :band_formats, :styles, COALESCE(:deal_type, 'flat'), COALESCE(:door_pct, 0), COALESCE(:guarantee_cents, 0), :requires_equipment)
                """),
                {
                    "gig_id":      gig_id,
                    "slot_number": slot_num,
                    "start_time":  s.get("start_time"),
                    "end_time":    s.get("end_time"),
                    "pay":         s.get("pay", 0),
                    "artist_type": s.get("artist_type"),
                    "band_formats":s.get("band_formats"),
                    "styles":      s.get("styles"),
                    "deal_type": s.get("deal_type"),
                    "door_pct": s.get("door_pct"),
                    "guarantee_cents": s.get("guarantee_cents"),
                    "requires_equipment": (1 if bool(s.get("requires_equipment")) else 0) if s.get("requires_equipment") is not None else None,
                }
            )

    # Update parent gig start/end + pay from all slots so other endpoints
    # (gig list, modal, effective-pay, scheduler) read the correct values.
    # FIX (May 2026): pay was previously NOT synced — only start/end. Result:
    # editing a slot's pay would update emails (which read slot.pay) but the
    # venue's gig details modal still showed the stale parent gig.pay value.
    # PROD BUG (May 10 2026): see comment on the sibling site — overnight
    # gigs break string-time MIN/MAX. Use slot_number ordering.
    times = db.execute(
        text("""
            SELECT
              (SELECT start_time FROM gig_slots WHERE gig_id = :gid ORDER BY slot_number ASC  LIMIT 1) as st,
              (SELECT end_time   FROM gig_slots WHERE gig_id = :gid ORDER BY slot_number DESC LIMIT 1) as et,
              (SELECT MAX(pay)   FROM gig_slots WHERE gig_id = :gid)                                  as max_pay
        """),
        {"gid": gig_id}
    ).fetchone()
    if times and times[0]:
        db.execute(
            text("UPDATE gigs SET start_time = :st, end_time = :et, pay = :pay WHERE id = :gid"),
            {"gid": gig_id, "st": times[0], "et": times[1], "pay": (times[2] if times[2] is not None else 0)}
        )

    # Audit fix (May 2026 part 9): when slot pay changes, the artist_payout
    # child txn's amount_cents must follow so fee recomputation is correct.
    # Without this, _recompute_gig_fees reads stale child amounts and the
    # parent venue_charge / artist fee distribution drift from slot.pay.
    # Only safe while parent is still 'scheduled'/'test' — past that, money
    # has moved and rebalancing would corrupt accounting (the same guard
    # _recompute_gig_fees enforces).
    try:
        parent_status = db.execute(
            text("""SELECT status FROM transactions
                    WHERE gig_id = :gid AND transaction_type = 'venue_charge'
                      AND status NOT IN ('payment_cancelled')
                    ORDER BY id DESC LIMIT 1"""),
            {"gid": gig_id}
        ).scalar()
        if parent_status in ("scheduled",):
            booked_slots = db.execute(
                text("""SELECT artist_id, COALESCE(SUM(pay), 0) AS pay_sum
                        FROM gig_slots
                        WHERE gig_id = :gid AND status = 'booked' AND artist_id IS NOT NULL
                        GROUP BY artist_id"""),
                {"gid": gig_id}
            ).mappings().all()
            for r in booked_slots:
                _new_cents = int(float(r["pay_sum"] or 0) * 100)
                db.execute(
                    text("""UPDATE transactions
                            SET amount_cents = :amt
                            WHERE gig_id = :gid AND artist_id = :aid
                              AND transaction_type = 'artist_payout'
                              AND status NOT IN ('payment_cancelled')"""),
                    {"amt": _new_cents, "gid": gig_id, "aid": r["artist_id"]}
                )
            _recompute_gig_fees(db, gig_id)
        elif parent_status is not None:
            logger.warning(
                f"[booked_edit_gig] gig {gig_id}: parent venue_charge status={parent_status!r} — "
                f"slot.pay edits made but transaction amounts NOT recomputed (real money moved)"
            )
    except Exception as _rce:
        logger.warning(f"[booked_edit_gig] fee recompute failed for gig {gig_id}: {_rce}")

    db.commit()

    # Notify all booked artists that the gig has been edited
    try:
        from backend.services.notification_service import notify_gig_edited
        from backend.services.email_dispatch import send_gig_edited_emails
        gig_info = db.execute(text("""
            SELECT g.date, v.venue_name, v.id as venue_id
            FROM gigs g JOIN venues v ON g.venue_id = v.id WHERE g.id = :gid
        """), {"gid": gig_id}).mappings().first()
        if gig_info:
            notify_gig_edited(db, gig_id, gig_info["venue_id"], gig_info["venue_name"], str(gig_info["date"]))
            db.commit()
        send_gig_edited_emails(db, gig_id)
    except Exception as e:
        logger.error(f"[BOOKED_EDIT] gig-edited notify/email error: {e}", exc_info=True)

    return {"ok": True}
@router.put("/venues/{venue_id}/gigs/recurring/{recurring_group_id}")
def update_recurring_gigs(venue_id: int, recurring_group_id: str, data: dict, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    from_date = request.query_params.get('from_date')

    # Audit fix (Jul 2026 audit — B-H2): bulk time update was skipping
    # the per-date overlap check that both `create_gig` and `update_gig`
    # enforce. A venue re-timing a recurring series could produce
    # overlapping OPEN gigs on the same date. Validate first, fail 409
    # with the conflicting dates listed.
    _new_start = data.get("start_time")
    _new_end   = data.get("end_time")
    if _new_start and _new_end and from_date:
        _conflicts = db.execute(
            text("""
                WITH affected AS (
                    SELECT id, date FROM gigs
                    WHERE recurring_group_id = :group_id
                      AND venue_id = :venue_id
                      AND date >= :from_date
                      AND status = 'open'
                )
                SELECT a.date, g.id AS other_id,
                       g.start_time AS other_start, g.end_time AS other_end
                FROM affected a
                JOIN gigs g
                  ON g.venue_id = :venue_id
                 AND g.date = a.date
                 AND g.id != a.id
                WHERE
                  -- overlap: NOT (new_end <= other_start OR new_start >= other_end)
                  NOT (
                    time(:new_end)   <= time(g.start_time)
                    OR
                    time(:new_start) >= time(g.end_time)
                  )
                ORDER BY a.date
                LIMIT 8
            """),
            {
                "group_id": recurring_group_id,
                "venue_id": venue_id,
                "from_date": from_date,
                "new_start": _new_start,
                "new_end": _new_end,
            }
        ).mappings().all()
        if _conflicts:
            _dates = [f"{c['date']} ({c['other_start']}–{c['other_end']})" for c in _conflicts]
            raise HTTPException(
                409,
                "Time change would overlap existing gigs on: " + ", ".join(_dates) +
                ". Adjust the time or edit those gigs first."
            )

    result = db.execute(
        text("""
            UPDATE gigs
            SET
              title = :title,
              start_time = :start_time,
              end_time = :end_time,
              pay = :pay,
              notes = :notes,
              artist_type = :artist_type,
              band_formats = :band_formats,
              styles = :styles
            WHERE recurring_group_id = :group_id
              AND venue_id = :venue_id
              AND date >= :from_date
              AND status = 'open'
        """),
        {
            "group_id": recurring_group_id,
            "venue_id": venue_id,
            "from_date": from_date,
            "title": data.get("title"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "pay": data.get("pay"),
            "notes": data.get("notes"),
            "artist_type": data.get("artist_type"),
            "band_formats": data.get("band_formats"),
            "styles": data.get("styles")
        }
    )
    # Audit fix #16 (May 2026): also propagate the new times/pay to gig_slots
    # rows on the affected open gigs. Previously only the gigs row was updated,
    # leaving slot rows with stale start_time/end_time/pay — every downstream
    # query that reads slots (gig list, public flyer, slot booking) would
    # display the OLD values until something else mutated the slots.
    try:
        # Door-deal fields are propagated alongside times/pay so a recurring
        # series edited with new door split terms updates ALL future open
        # slots in the series — not just the gig-level fields. COALESCE
        # preserves existing slot values when the payload doesn't carry
        # door config (e.g. edits that leave deal_type out).
        db.execute(
            text("""
                UPDATE gig_slots
                SET start_time = :st,
                    end_time   = :et,
                    pay        = :pay,
                    deal_type        = COALESCE(:deal_type, deal_type),
                    door_pct         = COALESCE(:door_pct, door_pct),
                    guarantee_cents  = COALESCE(:guarantee_cents, guarantee_cents)
                WHERE gig_id IN (
                    SELECT id FROM gigs
                    WHERE recurring_group_id = :group_id
                      AND venue_id = :venue_id
                      AND date >= :from_date
                      AND status = 'open'
                )
                  AND status = 'open'
            """),
            {
                "group_id": recurring_group_id, "venue_id": venue_id, "from_date": from_date,
                "st": data.get("start_time"), "et": data.get("end_time"), "pay": data.get("pay"),
                "deal_type":       data.get("deal_type"),
                "door_pct":        data.get("door_pct"),
                "guarantee_cents": data.get("guarantee_cents"),
            }
        )
    except Exception as _se:
        logger.warning(f"update_recurring_gigs slot sync skipped: {_se}")

    db.commit()

    return {"ok": True, "updated": result.rowcount}

# v97: UPDATE RECURRING SERIES WITH ADD/REMOVE GIGS
@router.put("/venues/{venue_id}/gigs/recurring/{recurring_group_id}/update-series")
def update_recurring_series(venue_id: int, recurring_group_id: str, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """
    Update a recurring series: update fields AND add/remove gigs based on new settings.
    - New gigs added if occurrences increased or new days selected
    - Non-booked gigs removed if occurrences decreased
    - Booked gigs are NEVER deleted
    """
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    from_date = data.get('from_date')
    
    
    # 1. Get existing gigs in the series (from this date forward)
    existing_gigs = db.execute(
        text("""
            SELECT id, date, status
            FROM gigs
            WHERE recurring_group_id = :group_id
              AND venue_id = :venue_id
              AND date >= :from_date
            ORDER BY date
        """),
        {"group_id": recurring_group_id, "venue_id": venue_id, "from_date": from_date}
    ).mappings().all()
    
    existing_dates = {g['date']: {'id': g['id'], 'status': g['status']} for g in existing_gigs}
    
    # 2. Calculate target dates ONLY if recurring schedule settings were provided
    # If days_of_week is empty/null, skip add/delete — just update field values
    days_of_week_raw = data.get('recurring_days_of_week', '') or ''
    skip_schedule_recalc = not days_of_week_raw.strip()

    target_dates = []
    if not skip_schedule_recalc:
        target_dates = generate_recurring_dates_backend(
            start_date=from_date,
            interval_weeks=data.get('recurring_interval_weeks', 1),
            days_of_week=days_of_week_raw,
            end_type=data.get('recurring_end_type', 'never'),
            end_after=data.get('recurring_end_after'),
            end_by_date=data.get('recurring_end_by_date')
        )
    
    # 3. Add missing gigs (only when schedule was provided)
    gigs_added = 0
    conflicts = []  # dates where another gig already exists at overlapping time
    skip_dates = set(data.get('skip_dates', []) or [])
    force_overlap = bool(data.get('force_overlap', False))

    # Detect series-hold so we can propagate it to any newly inserted
    # instances (Jul 2026 audit fix #12). If ANY existing gig in this
    # series has hold_status='active' AND hold-source waitlist rows,
    # the venue is mid-rotation — the new dates need to join the hold
    # so the venue doesn't get a confusing mix of publicly-bookable
    # extensions and privately-held originals.
    _series_hold_artist_ids = []
    _series_hold_window = 24
    _series_hold_email_pref = True
    try:
        _h_check = db.execute(
            text("""SELECT 1 FROM gigs g
                    WHERE g.recurring_group_id = :rgid
                      AND g.hold_status = 'active'
                      AND EXISTS (SELECT 1 FROM gig_waitlist wl
                                  WHERE wl.gig_id = g.id AND wl.source = 'hold')
                    LIMIT 1"""),
            {"rgid": recurring_group_id}
        ).first()
        if _h_check:
            # Pull the canonical hold artist list from the earliest gig
            # in the series — positions are the same across instances.
            _src_gig = db.execute(
                text("""SELECT id, hold_offer_window_hours, hold_email_artists
                        FROM gigs
                        WHERE recurring_group_id = :rgid AND hold_status = 'active'
                        ORDER BY date ASC LIMIT 1"""),
                {"rgid": recurring_group_id}
            ).mappings().first()
            if _src_gig:
                _series_hold_window = int(_src_gig["hold_offer_window_hours"] or 24)
                _series_hold_email_pref = bool(int(_src_gig["hold_email_artists"] or 1))
                _ar_rows = db.execute(
                    text("""SELECT artist_id FROM gig_waitlist
                            WHERE gig_id = :gid AND source = 'hold'
                            ORDER BY position ASC"""),
                    {"gid": _src_gig["id"]}
                ).fetchall()
                _series_hold_artist_ids = [r[0] for r in _ar_rows]
    except Exception as _hxe:
        logger.warning(f"[SERIES_EXTEND] hold-detect failed rgid={recurring_group_id}: {_hxe}")

    if not skip_schedule_recalc:
      new_start = data.get('start_time')
      new_end   = data.get('end_time')
      for date_str in target_dates:
        if date_str in existing_dates:
            continue
        if date_str in skip_dates:
            continue
        # Check for time overlap with any other gig at this venue on this date
        if new_start and new_end and not force_overlap:
            conflict_row = db.execute(text("""
                SELECT g.id, g.title, g.start_time, g.end_time
                FROM gigs g
                WHERE g.venue_id = :vid AND g.date = :date
                  AND g.id NOT IN (
                      SELECT id FROM gigs WHERE recurring_group_id = :group_id
                  )
                  AND g.status NOT IN ('cancelled','deleted')
                  AND g.start_time < :new_end
                  AND g.end_time > :new_start
            """), {
                "vid": venue_id, "date": date_str,
                "group_id": recurring_group_id,
                "new_start": new_start, "new_end": new_end
            }).mappings().first()
            if conflict_row:
                def _fmt(t):
                    if not t: return ''
                    h, m = int(t[:2]), int(t[3:5])
                    ampm = 'PM' if h >= 12 else 'AM'
                    h12 = h % 12 or 12
                    return f"{h12}:{m:02d} {ampm}"
                conflicts.append({
                    "date": date_str,
                    "existing_title": conflict_row["title"] or "Existing Gig",
                    "existing_times": f"{_fmt(conflict_row['start_time'])}–{_fmt(conflict_row['end_time'])}",
                    "new_times": f"{_fmt(new_start)}–{_fmt(new_end)}"
                })
                continue  # Don't insert this one yet

        # 2026-07-25: RETURNING id inline (pool-swap safety).
        new_gig_id = db.execute(
            text("""
                    INSERT INTO gigs
                        (venue_id, date, start_time, end_time, title, pay, notes, status,
                         artist_type, band_formats, styles, is_recurring, is_multi_slot,
                         recurring_group_id,
                         recurring_interval_weeks, recurring_days_of_week,
                         recurring_end_type, recurring_end_after, recurring_end_by_date)
                    VALUES
                        (:venue_id, :date, :start_time, :end_time, :title, :pay, :notes, 'open',
                         :artist_type, :band_formats, :styles, 1, 1,
                         :recurring_group_id,
                         :recurring_interval_weeks, :recurring_days_of_week,
                         :recurring_end_type, :recurring_end_after, :recurring_end_by_date) RETURNING id
            """),
            {
                "venue_id": venue_id,
                "date": date_str,
                "start_time": data.get("start_time"),
                "end_time": data.get("end_time"),
                "title": data.get("title"),
                "pay": data.get("pay", 0),
                "notes": data.get("notes"),
                "artist_type": data.get("artist_type"),
                "band_formats": data.get("band_formats"),
                "styles": data.get("styles"),
                "recurring_group_id": recurring_group_id,
                "recurring_interval_weeks": data.get("recurring_interval_weeks"),
                "recurring_days_of_week": data.get("recurring_days_of_week"),
                "recurring_end_type": data.get("recurring_end_type"),
                "recurring_end_after": data.get("recurring_end_after"),
                "recurring_end_by_date": data.get("recurring_end_by_date")
            }
        ).scalar()

        # BUG FIX (Jul 2026 audit): create the slot-1 row so this gig satisfies
        # the "every gig has ≥1 gig_slots row" invariant. Without it the gig
        # rendered as empty in gig_modal, was skipped by every slot-only
        # reader, and the single-slot contract-upload path silently corrupted
        # the parent status. Mirrors the slot-insert pattern in create_gig.
        try:
            db.execute(text("""
                INSERT INTO gig_slots
                    (gig_id, slot_number, start_time, end_time, pay, status,
                     artist_type, band_formats, styles, deal_type,
                     door_pct, guarantee_cents, apply_override)
                VALUES
                    (:gid, 1, :st, :et, :pay, 'open',
                     :atype, :bf, :styles, 'flat',
                     0, 0, 0)
            """), {
                "gid": new_gig_id,
                "st": data.get("start_time"),
                "et": data.get("end_time"),
                "pay": data.get("pay", 0),
                "atype": data.get("artist_type"),
                "bf":    data.get("band_formats"),
                "styles": data.get("styles"),
            })
        except Exception as _slot_e:
            logger.warning(f"[SERIES_EXTEND] slot-1 insert failed for gig {new_gig_id}: {_slot_e}")

        try:
            _acf, _ = _get_flyer_helpers()
            _acf(db, new_gig_id, venue_id)
        except Exception:
            pass
        # Audit fix #12 (Jul 2026): if this series already has a live
        # hold rotation, propagate it to the new instance so the new
        # date isn't publicly bookable while the rest of the series is
        # privately held. defer_kickoff=True — new dates get staged but
        # don't fire a fresh per-gig rotation; in-flight artists pick
        # them up automatically via _list_series_eligible_gigs on next
        # modal refresh, and the next-in-line artist sees them in
        # their bundled email.
        if _series_hold_artist_ids:
            try:
                from backend.services.gig_hold import create_hold_waitlist
                create_hold_waitlist(
                    db, gig_id=new_gig_id,
                    artist_ids=_series_hold_artist_ids,
                    send_email_now=_series_hold_email_pref,
                    offer_window_hours=_series_hold_window,
                    defer_kickoff=True,
                )
                logger.info(
                    f"[SERIES_EXTEND] propagated hold to new gig={new_gig_id} "
                    f"({len(_series_hold_artist_ids)} artists)"
                )
            except Exception as _he:
                logger.error(f"[SERIES_EXTEND] hold propagation failed for new gig {new_gig_id}: {_he}")
        gigs_added += 1
    
    # 4. Delete extra gigs that shouldn't exist (only if NOT in any in-flight state,
    #    only when schedule was provided).
    # Audit fix (May 2026 part 5): previously the filter was just `status != 'booked'`,
    # which silently destroyed gigs in `pending_contract`, `awaiting_venue_contract`,
    # `pending_venue_approval`, AND their contracts/transactions/notifications.
    # Now we keep the gig (and surface a count so the venue UI can warn) for any
    # in-flight state — only purely `open` or `cancelled` gigs are removed.
    # Also check gig_slots so a parent with status='open' but slots in-flight is
    # protected (multi-slot gigs where one slot is mid-contract).
    _SAFE_TO_DELETE_PARENT = ('open', 'cancelled')
    _INFLIGHT_SLOT_STATUSES = ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval')
    gigs_deleted = 0
    gigs_skipped_inflight = 0
    if not skip_schedule_recalc:
        target_dates_set = set(target_dates)
        for date_str, gig_info in existing_dates.items():
            if date_str in target_dates_set:
                continue
            if gig_info['status'] not in _SAFE_TO_DELETE_PARENT:
                gigs_skipped_inflight += 1
                continue
            # Even when parent is 'open', any slot in an in-flight state means a
            # contract/approval is being worked — refuse to delete that gig.
            _slot_inflight = db.execute(
                text(f"SELECT 1 FROM gig_slots WHERE gig_id = :gid AND status IN ({','.join(repr(s) for s in _INFLIGHT_SLOT_STATUSES)}) LIMIT 1"),
                {"gid": gig_info['id']}
            ).first()
            if _slot_inflight:
                gigs_skipped_inflight += 1
                continue
            # Audit fix Y1 (Jun 30 2026): the bespoke 3-statement delete
            # left orphan rows in gig_contracts, notifications, gig_email_log,
            # public_activity, gig_messages, waitlist_offered, etc. The
            # parent gig is guaranteed to be in `_SAFE_TO_DELETE_PARENT`
            # (open / cancelled — no in-flight money) and no slot is in
            # an in-flight state, so the canonical cleanup helper is
            # safe here. Flyer cleanup (template-aware) stays explicit.
            #
            # Payment YELLOW fix (Jul 1 2026): also run the charged-txn
            # guard before delete_gig_completely so an unexpected orphan
            # payment_cancelled / free_trial audit row (or any FUTURE
            # money-state we forget to exclude from _SAFE_TO_DELETE_PARENT)
            # can't be silently dropped. Matches the guard already in place
            # in cancel_gig / cancel_slot / delete_gig_with_slots.
            try:
                from backend.services.gig_cleanup import assert_no_charged_transactions
                assert_no_charged_transactions(db, gig_info['id'])
            except Exception as _acte:
                logger.warning(
                    f"update_recurring_series: shrink skipped gig={gig_info['id']} — "
                    f"charged-txn guard raised: {_acte}"
                )
                gigs_skipped_inflight += 1
                continue
            try:
                db.execute(text("DELETE FROM flyers WHERE gig_id = :gid AND is_template = 0"), {"gid": gig_info['id']})
            except Exception: pass
            try:
                from backend.services.gig_cleanup import delete_gig_completely
                delete_gig_completely(db, gig_info['id'])
            except Exception as _del_e:
                # Last-resort fallback — keep the shrink moving even if
                # cleanup encounters an unexpected ancillary-table miss.
                logger.warning(f"update_recurring_series: delete_gig_completely failed for gig={gig_info['id']}: {_del_e}")
                try:
                    from backend.routes.waitlist import cleanup_gig_waitlist
                    cleanup_gig_waitlist(db, gig_info['id'])
                except Exception: pass
                db.execute(text("DELETE FROM gig_slots WHERE gig_id = :gid"), {"gid": gig_info['id']})
                db.execute(text("DELETE FROM gigs WHERE id = :gid"), {"gid": gig_info['id']})
            gigs_deleted += 1
    
    # 5. Update all remaining OPEN gigs with new field values
    update_result = db.execute(
        text("""
            UPDATE gigs
            SET
              title = :title,
              start_time = :start_time,
              end_time = :end_time,
              pay = :pay,
              notes = :notes,
              artist_type = :artist_type,
              band_formats = :band_formats,
              styles = :styles,
              recurring_interval_weeks = :recurring_interval_weeks,
              recurring_days_of_week = :recurring_days_of_week,
              recurring_end_type = :recurring_end_type,
              recurring_end_after = :recurring_end_after,
              recurring_end_by_date = :recurring_end_by_date
            WHERE recurring_group_id = :group_id
              AND venue_id = :venue_id
              AND date >= :from_date
              AND status = 'open'
        """),
        {
            "group_id": recurring_group_id,
            "venue_id": venue_id,
            "from_date": from_date,
            "title": data.get("title"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "pay": data.get("pay"),
            "notes": data.get("notes"),
            "artist_type": data.get("artist_type"),
            "band_formats": data.get("band_formats"),
            "styles": data.get("styles"),
            "recurring_interval_weeks": data.get("recurring_interval_weeks"),
            "recurring_days_of_week": data.get("recurring_days_of_week"),
            "recurring_end_type": data.get("recurring_end_type"),
            "recurring_end_after": data.get("recurring_end_after"),
            "recurring_end_by_date": data.get("recurring_end_by_date")
        }
    )

    # 6. Update slots for all open gigs in series.
    # Audit fix (May 2026 part 10): also sync slot times/pay on booked
    # slots when the gig-level UPDATE above pushed new values. Otherwise
    # the parent gig says 7-10pm $200 but a booked slot still says
    # 8-11pm $150 — artist shows up at the wrong time. Mirrors what
    # `booked_edit_gig` does for single-gig edits.
    slots = data.get("slots", [])
    if slots:
        open_gig_ids = db.execute(
            text("""
                SELECT id FROM gigs
                WHERE recurring_group_id = :group_id
                  AND venue_id = :venue_id
                  AND date >= :from_date
                  AND status = 'open'
            """),
            {"group_id": recurring_group_id, "venue_id": venue_id, "from_date": from_date}
        ).fetchall()
        for (og_id,) in open_gig_ids:
            # First, UPDATE the existing booked/in-flight slots so their
            # times/pay stay synced with the parent gig values. Pay change
            # on already-booked slots will be picked up by _recompute_gig_fees
            # only if parent txn is still 'scheduled'/'test' (real money
            # not committed) — same guard as booked_edit_gig.
            for i, s in enumerate(slots, 1):
                db.execute(text("""
                    UPDATE gig_slots
                    SET start_time = :start_time, end_time = :end_time, pay = :pay
                    WHERE gig_id = :gid AND slot_number = :sn
                """), {"gid": og_id, "sn": i,
                       "start_time": s.get("start_time"),
                       "end_time": s.get("end_time"),
                       "pay": s.get("pay", 0)})
            # Then drop and re-create the OPEN slots only (in case slot count changed)
            db.execute(text("DELETE FROM gig_slots WHERE gig_id = :gid AND status = 'open'"), {"gid": og_id})
            for i, s in enumerate(slots, 1):
                # Skip if a non-open slot already exists with this slot_number
                existing = db.execute(text(
                    "SELECT 1 FROM gig_slots WHERE gig_id = :gid AND slot_number = :sn"
                ), {"gid": og_id, "sn": i}).first()
                if existing:
                    continue
                db.execute(
                    text("""
                        INSERT INTO gig_slots (gig_id, slot_number, start_time, end_time, pay, status,
                                               artist_type, band_formats, styles, deal_type, door_pct, guarantee_cents, requires_equipment)
                        VALUES (:gig_id, :slot_number, :start_time, :end_time, :pay, 'open',
                                :artist_type, :band_formats, :styles, COALESCE(:deal_type, 'flat'), COALESCE(:door_pct, 0), COALESCE(:guarantee_cents, 0), :requires_equipment)
                    """),
                    {
                        "gig_id": og_id, "slot_number": i,
                        "start_time": s.get("start_time"), "end_time": s.get("end_time"),
                        "pay": s.get("pay", 0), "artist_type": s.get("artist_type"),
                        "band_formats": s.get("band_formats"), "styles": s.get("styles"),
                    "deal_type": s.get("deal_type"),
                    "door_pct": s.get("door_pct"),
                    "guarantee_cents": s.get("guarantee_cents"),
                    "requires_equipment": (1 if bool(s.get("requires_equipment")) else 0) if s.get("requires_equipment") is not None else None,
                    }
                )
    
    db.commit()
    
    return {
        "ok": True,
        "added": gigs_added,
        "deleted": gigs_deleted,
        # Audit fix (May 2026 part 6): surface the in-flight-skip count so the
        # venue UI can warn "We preserved N gigs that already had bookings or
        # contracts in progress." Previously this counter incremented but the
        # response shape only mentioned `deleted`, so the venue thought the
        # series was rebuilt cleanly when some in-flight gigs were preserved.
        "skipped_inflight": gigs_skipped_inflight,
        "updated": update_result.rowcount,
        "conflicts": conflicts
    }

def generate_recurring_dates_backend(start_date, interval_weeks, days_of_week, end_type, end_after, end_by_date):
    """
    Generate recurring dates based on settings.
    v97: end_after counts WEEKS, not total gigs (matches frontend behavior)
    """
    from datetime import datetime, timedelta
    
    if not days_of_week:
        return []
    
    # Parse days (0=Sunday, 1=Monday, ..., 6=Saturday)
    selected_days = [int(d) for d in days_of_week.split(',') if d.strip()]
    if not selected_days:
        return []
    selected_days.sort()
    
    # Parse start date
    start = datetime.strptime(start_date, '%Y-%m-%d')
    start_day_of_week = start.weekday()  # Monday=0, Sunday=6
    # Convert to our format (Sunday=0)
    start_day_of_week = (start_day_of_week + 1) % 7
    
    # Determine max weeks. Audit fix (May 2026 part 10): hard cap at 104
    # weeks (~2 years) even if a caller passes a larger end_after. A
    # malicious / stale client previously could pass 500 with 7 days/week
    # and create 3,500 rows in one call. The frontend caps at 104 but a
    # direct API caller bypasses that.
    _HARD_CAP_WEEKS = 104
    max_weeks = None
    if end_type == 'after' and end_after:
        try:
            req = int(end_after)
        except (TypeError, ValueError):
            req = _HARD_CAP_WEEKS
        max_weeks = max(0, min(req, _HARD_CAP_WEEKS))
    elif end_type in ('never', None, ''):
        max_weeks = 52  # No open-ended series — cap at 1 year
    
    # Parse end by date
    end_date = None
    if end_type == 'by' and end_by_date:
        end_date = datetime.strptime(end_by_date, '%Y-%m-%d')
    
    dates = []
    week_offset = 0
    weeks_generated = 0
    
    while weeks_generated < 500:  # Safety limit
        if max_weeks and weeks_generated >= max_weeks:
            break
        
        added_this_week = False
        
        for target_day in selected_days:
            # Calculate date for this target day
            days_from_start = target_day - start_day_of_week
            weeks_to_add = week_offset * interval_weeks
            total_days = (weeks_to_add * 7) + days_from_start
            
            gig_date = start + timedelta(days=total_days)
            
            # Skip if before start date
            if gig_date < start:
                continue
            
            # Check end by date
            if end_date and gig_date > end_date:
                return dates
            
            dates.append(gig_date.strftime('%Y-%m-%d'))
            added_this_week = True
        
        if added_this_week:
            weeks_generated += 1
        
        week_offset += 1
        
        if week_offset > 520:  # 10 year safety limit
            break
    
    return dates

# DELETE ALL GIGS IN RECURRING SERIES (FUTURE GIGS ONLY)
@router.delete("/venues/{venue_id}/gigs/recurring/{recurring_group_id}")
def delete_recurring_gigs(venue_id: int, recurring_group_id: str, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    from_date = request.query_params.get('from_date')
    
    # Audit fix (May 2026 part 10): refuse to delete gigs that have ANY
    # in-flight slot (booked / pending_contract / awaiting_venue_contract /
    # pending_venue_approval). Parent gig.status='open' is NOT a sufficient
    # gate — every multi-slot gig stays 'open' until all slots fill, so a
    # series occurrence with 1 of 3 slots booked previously passed this
    # filter and got hard-deleted along with the booked artist's contract +
    # signed PDF + transaction row. No refund, no notification, just silent
    # data loss. Also call assert_no_charged_transactions per gig.
    _IN_FLIGHT_SLOTS = ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
    candidate_rows = db.execute(
        text("""
            SELECT g.id, g.date,
                   (SELECT COUNT(*) FROM gig_slots gs
                    WHERE gs.gig_id = g.id AND gs.status IN
                      ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                   ) AS in_flight_slots,
                   (SELECT COUNT(*) FROM transactions t
                    WHERE t.gig_id = g.id
                      AND t.status IN ('charged','paid','transferred','pending_transfer')
                   ) AS charged_txns
            FROM gigs g
            WHERE g.recurring_group_id = :group_id
              AND g.venue_id = :venue_id
              AND g.date >= :from_date
              AND g.status = 'open'
        """),
        {"group_id": recurring_group_id, "venue_id": venue_id, "from_date": from_date}
    ).mappings().all()

    safe_to_delete = []
    additional_skips = []
    for r in candidate_rows:
        if r["in_flight_slots"] > 0 or r["charged_txns"] > 0:
            additional_skips.append(r["date"])
        else:
            safe_to_delete.append(r["id"])

    deleted_count = 0
    for gig_id in safe_to_delete:
        # Optional tables — commit each separately so failure doesn't block core deletes.
        # Audit fix (May 2026 part 10c): also clean notification_sent_log so if
        # a recurring child's id is later reused (post-VACUUM, e.g.), stale
        # notification suppression doesn't bleed into the new gig.
        for _sql, _p in [
            ("DELETE FROM gig_email_log WHERE gig_id = :gid", {"gid": gig_id}),
            ("DELETE FROM public_activity WHERE gig_id = :gid", {"gid": gig_id}),
            ("DELETE FROM flyers WHERE gig_id = :gid AND is_template = 0", {"gid": gig_id}),
            ("DELETE FROM notification_sent_log WHERE gig_id = :gid", {"gid": gig_id}),
        ]:
            try:
                db.execute(text(_sql), _p)
                db.commit()
            except Exception as _de:
                logger.warning(f"delete_recurring_gigs optional skip ({_sql[:40]}): {_de}")
                db.rollback()
        # Core deletes — must succeed
        try:
            db.execute(text("DELETE FROM payment_cancellations WHERE transaction_id IN (SELECT id FROM transactions WHERE gig_id = :gid)"), {"gid": gig_id})
        except Exception: db.rollback()
        db.execute(text("DELETE FROM transactions WHERE gig_id = :gid"), {"gid": gig_id})
        # Audit fix (May 2026 part 5): unlink signed PDF files on disk before
        # dropping the gig_contracts rows. Previously the DB rows vanished but
        # the actual PDF files persisted under app/static/uploads/contracts/signed/
        # forever — disk-leak grew unbounded and old PDFs stayed world-readable.
        # NEVER touch pdf_file_path here — that's the venue template PDF
        # shared across many gigs.
        try:
            import os as _os
            _pdf_rows = db.execute(
                text("SELECT signed_pdf_path FROM gig_contracts WHERE gig_id = :gid"),
                {"gid": gig_id}
            ).mappings().all()
            for _gc in _pdf_rows:
                _pdf = _gc.get("signed_pdf_path")
                if _pdf:
                    _abs = _pdf.lstrip("/")
                    if _os.path.isfile(_abs):
                        try:
                            _os.remove(_abs)
                        except OSError:
                            pass
        except Exception as _pe:
            logger.warning(f"delete_recurring_gigs: signed-PDF unlink failed for gig {gig_id}: {_pe}")
        db.execute(text("DELETE FROM gig_contracts WHERE gig_id = :gid"), {"gid": gig_id})
        db.execute(text("DELETE FROM notifications WHERE gig_id = :gid"), {"gid": gig_id})
        db.execute(text("DELETE FROM gig_messages WHERE gig_id = :gid"), {"gid": gig_id})
        db.execute(text("DELETE FROM gig_waitlist WHERE gig_id = :gid"), {"gid": gig_id})
        try: db.execute(text("DELETE FROM waitlist_offered WHERE gig_id = :gid"), {"gid": gig_id})
        except Exception: pass
        db.execute(text("DELETE FROM artist_reviews WHERE gig_id = :gid"), {"gid": gig_id})
        db.execute(text("DELETE FROM gig_slots WHERE gig_id = :gid"), {"gid": gig_id})
        db.execute(text("DELETE FROM gigs WHERE id = :gid"), {"gid": gig_id})
        db.commit()
        deleted_count += 1
    
    # Find booked gigs that were skipped (not deleted) — includes both the
    # legacy status != 'open' case AND the new "parent open but slots booked
    # / charged txns" case (audit fix May 2026 part 10).
    skipped_gigs = db.execute(
        text("""
            SELECT date FROM gigs
            WHERE recurring_group_id = :group_id
              AND venue_id = :venue_id
              AND date >= :from_date
              AND status != 'open'
            ORDER BY date ASC
        """),
        {"group_id": recurring_group_id, "venue_id": venue_id, "from_date": from_date}
    ).fetchall()
    skipped_dates = sorted({row[0] for row in skipped_gigs} | set(additional_skips))

    db.commit()

    return {"ok": True, "deleted": deleted_count, "skipped_dates": skipped_dates}

# MY GIGS - CRITICAL FIX FOR ARTIST INDEPENDENCE
@router.get("/api/my/gigs")
def my_gigs(
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    # CRITICAL FIX: Get artist_id from query params
    artist_id = request.query_params.get('artist_id')
    
    if not artist_id:
        # Fallback to first artist (but this shouldn't happen with proper frontend)
        artist = db.execute(
            text("SELECT id FROM artists WHERE user_id = :uid LIMIT 1"),
            {"uid": user.id}
        ).mappings().first()

        if not artist:
            return []
        artist_id = artist["id"]
    else:
        # v97: Check BOTH direct ownership AND entity_users access
        artist = db.execute(
            text("""
                SELECT a.id FROM artists a
                WHERE a.id = :aid 
                AND (
                    a.user_id = :uid
                    OR EXISTS (
                        SELECT 1 FROM entity_users eu 
                        WHERE eu.entity_type = 'artist' 
                        AND eu.entity_id = a.id 
                        AND eu.user_id = :uid
                    )
                )
            """),
            {"aid": int(artist_id), "uid": user.id}
        ).mappings().first()
        
        if not artist:
            raise HTTPException(403, "Not your artist")
        artist_id = int(artist_id)

    rows = db.execute(
        text("""
            SELECT DISTINCT
                g.id, g.venue_id, g.date, g.start_time, g.end_time,
                g.status, g.artist_id, g.title, g.pay, g.notes,
                g.artist_type, g.band_formats, g.styles,
                g.recurring_group_id, g.is_recurring,
                COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                v.venue_name,
                v.address_line_1,
                v.address_line_2,
                v.city,
                v.state,
                v.postal_code
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
            WHERE g.artist_id = :aid
               OR g.contract_hold_artist_id = :aid
               OR (gs.artist_id = :aid AND gs.status IN ('booked', 'pending_contract', 'pending_venue_approval', 'awaiting_venue_contract'))
            ORDER BY g.date ASC
        """),
        {"aid": artist_id}
    ).mappings().all()

    # Enrich with slots so frontend can find artist's slot.
    # Perf fix (Jun 2026): batch all slots in ONE query instead of issuing
    # one SELECT per gig. At 20 gigs in My Gigs view this drops 20 round
    # trips to 1; at 100 concurrent users on calendar load that's a 2000x
    # query reduction. Mirrors the same pattern used by list_venue_gigs.
    gig_ids = [row["id"] for row in rows]
    slots_by_gig = {gid: [] for gid in gig_ids}
    if gig_ids:
        slot_rows = db.execute(
            text("""
                SELECT gs.gig_id, gs.id as slot_id, gs.slot_number, gs.start_time, gs.end_time,
                       gs.pay, gs.status, gs.artist_id,
                       gs.artist_type, gs.band_formats, gs.styles,
                       gs.deal_type, gs.door_pct, gs.guarantee_cents, COALESCE(gs.apply_override,0) as apply_override, gs.requires_equipment,
                       a.name as artist_name
                FROM gig_slots gs
                LEFT JOIN artists a ON gs.artist_id = a.id
                WHERE gs.gig_id IN :gids
                ORDER BY gs.gig_id ASC, gs.slot_number ASC
            """).bindparams(bindparam("gids", expanding=True)),
            {"gids": gig_ids}
        ).mappings().all()
        for s in slot_rows:
            slots_by_gig.setdefault(s["gig_id"], []).append(dict(s))

    result = []
    for row in rows:
        gig = dict(row)
        gig["slots"] = _enrich_pay_summary(slots_by_gig.get(gig["id"], []))
        _enrich_pay_summary(gig)
        result.append(gig)
    return result

# ==========================================
# GIG SLOTS ENDPOINTS (Multi-slot Events)
# ==========================================

@router.get("/api/gigs/{gig_id}/slots/public")
@limiter.limit("60/minute")
def get_gig_slots_public(gig_id: int, request: Request, db=Depends(get_db)):
    """Public slot list — used by anonymous calendar pages
    (artist-profile, venue-profile, public-gigs) to render per-slot
    hover breakdowns. REDACTS internal financial fields that the
    authenticated endpoint exposes to venues/artists:
      - pay, guarantee_cents, door_pct, door_receipts_cents,
        settled_pay_cents, settled_at are excluded.
    Anonymous viewers should never see actual cash receipts from a
    venue's door.
    """
    try:
        slots = db.execute(
            text("""
                SELECT gs.id, gs.gig_id, gs.slot_number, gs.start_time, gs.end_time,
                       gs.artist_id, gs.status,
                       gs.artist_type, gs.band_formats, gs.styles,
                       gs.deal_type, gs.requires_equipment,
                           a.name as artist_name
                FROM gig_slots gs
                LEFT JOIN artists a ON gs.artist_id = a.id
                WHERE gs.gig_id = :gig_id
                ORDER BY gs.slot_number ASC
            """),
            {"gig_id": gig_id}
        ).mappings().all()
        return [dict(s) for s in slots]
    except Exception as e:
        logger.error(f"Failed to load public slots: {e}", exc_info=True)
        raise HTTPException(500, "Failed to load slots")

# GET SLOTS FOR A GIG
@router.get("/api/gigs/{gig_id}/slots")
def get_gig_slots(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get all slots for a multi-slot gig"""
    try:
        slots = db.execute(
            text("""
                SELECT gs.id, gs.gig_id, gs.slot_number, gs.start_time, gs.end_time,
                       gs.pay, gs.artist_id, gs.status,
                       gs.artist_type, gs.band_formats, gs.styles,
                       gs.deal_type, gs.door_pct, gs.guarantee_cents, COALESCE(gs.apply_override,0) as apply_override, gs.requires_equipment,
                       gs.door_receipts_cents, gs.settled_pay_cents, gs.settled_at,
                       a.name as artist_name
                FROM gig_slots gs
                LEFT JOIN artists a ON gs.artist_id = a.id
                WHERE gs.gig_id = :gig_id
                ORDER BY gs.slot_number ASC
            """),
            {"gig_id": gig_id}
        ).mappings().all()
        return _enrich_pay_summary([dict(s) for s in slots])
    except Exception as e:
        logger.error(f"Failed to load slots. Please try again.: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to load slots. Please try again.: {str(e)}")

# BOOK A SPECIFIC SLOT (ARTIST)
@router.post("/api/gigs/{gig_id}/slots/{slot_id}/book")
def book_slot(
    gig_id: int,
    slot_id: int,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Book a specific slot within a multi-slot gig"""
    artist_id = request.query_params.get('artist_id')
    
    if not artist_id:
        artist = db.execute(
            text("SELECT id FROM artists WHERE user_id = :uid LIMIT 1"),
            {"uid": user.id}
        ).mappings().first()
        if not artist:
            raise HTTPException(403, "No artist profile found")
        artist_id = artist["id"]
    else:
        # Verify ownership + exclude tombstoned (Jul 2026, same as book_gig).
        artist = db.execute(
            text("""
                SELECT a.id FROM artists a
                WHERE a.id = :aid
                AND a.deleted_at IS NULL
                AND (
                    a.user_id = :uid
                    OR EXISTS (
                        SELECT 1 FROM entity_users eu
                        WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                    )
                )
            """),
            {"aid": int(artist_id), "uid": user.id}
        ).mappings().first()
        if not artist:
            raise HTTPException(403, "Artist does not belong to you or has been deleted")
        artist_id = int(artist_id)

    # Load slot
    slot = db.execute(
        text("SELECT * FROM gig_slots WHERE id = :sid AND gig_id = :gid"),
        {"sid": slot_id, "gid": gig_id}
    ).mappings().first()

    if not slot:
        raise HTTPException(404, "Slot not found")

    # Artist type + lineup match gate — slot-aware version so a multi-
    # slot gig with per-slot lineup overrides enforces what THIS slot
    # requires, not the gig umbrella. Hard 400 on mismatch. See
    # _check_artist_matches_gig docstring for the rules.
    _gig_for_check = db.execute(
        text("SELECT artist_type, band_formats FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if _gig_for_check:
        _check_artist_matches_gig(db, dict(_gig_for_check), artist_id, slot_id=slot_id)

    # Allow re-booking a slot the artist already has pending approval on
    if slot["status"] == "pending_venue_approval" and slot.get("artist_id") == artist_id:
        pass  # Artist re-submitting their own pending slot — allow through
    elif slot["status"] != "open":
        raise HTTPException(403, "Slot is not open")

    # Load parent gig for venue info
    gig = db.execute(
        text("SELECT id, venue_id, date, status, hold_status FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")

    # Held-gig guard (Jun 2026): a gig with hold_status='active' or
    # 'exhausted' is privately offered to an ordered list of artists.
    # Bookings must flow through the Hold pipeline (respond_to_hold_offer)
    # to consume the waitlist row + advance rotation. Hitting book_slot
    # directly would book the slot but leave the hold state inconsistent
    # — the offered artist's email link would still appear valid, the
    # rotation wouldn't advance, and venue notifications would miss
    # the hold-specific email template. Block it.
    if gig.get("hold_status") in ("active", "exhausted"):
        raise HTTPException(403,
            "This gig is currently held — bookings must come through your "
            "Pending Offers banner (or the email offer link). Open this gig "
            "from your calendar to see the Pending Offer panel.")

    # FIX (May 21 2026): hard-block on time-overlap with another booking.
    _enforce_no_artist_time_overlap(db, artist_id, gig_id, gig["venue_id"],
                                    gig.get("date"), slot.get("start_time"), slot.get("end_time"))

    # ── Waitlist lock — same rule as single-slot booking ────────────────────
    slot_active_offer = db.execute(
        text("""
            SELECT artist_id FROM gig_waitlist
            WHERE gig_id = :gid
              AND offer_sent = 1
              AND (offer_declined = 0 OR offer_declined IS NULL)
              AND (offer_expires_at IS NULL OR offer_expires_at > datetime('now'))
            ORDER BY id ASC LIMIT 1
        """),
        {"gid": gig_id}
    ).mappings().first()

    if not slot_active_offer:
        slot_active_offer = db.execute(
            text("""SELECT artist_id FROM waitlist_offered
                     WHERE gig_id = :gid AND offer_expires_at > datetime('now')
                     ORDER BY id ASC LIMIT 1"""),
            {"gid": gig_id}
        ).mappings().first()

    if slot_active_offer and slot_active_offer["artist_id"] != artist_id:
        raise HTTPException(403, "WAITLIST_LOCKED: This gig has an active waitlist offer to another artist. Please wait for their response before booking.")

    # Check preferred approval — bypass if valid blast token OR gig is blast-open
    slot_blast_token = request.query_params.get('blast_token') or ''
    slot_gig_token_row = db.execute(
        text("SELECT radius_blast_token FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    slot_token_valid = (
        (
            slot_blast_token
            and slot_gig_token_row
            and slot_gig_token_row["radius_blast_token"]
            and slot_blast_token == slot_gig_token_row["radius_blast_token"]
        )
        or (slot_gig_token_row and slot_gig_token_row["radius_blast_token"] is not None)
    )
    # Ban check — always blocks, even on blast
    is_banned_slot = db.execute(
        text("SELECT 1 FROM venue_artist_bans WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": gig["venue_id"], "aid": artist_id}
    ).first()
    if is_banned_slot:
        raise HTTPException(403, "BANNED: You are not permitted to book at this venue.")

    if not slot_token_valid:
        pref = db.execute(
            text("SELECT status FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"),
            {"vid": gig["venue_id"], "aid": artist_id}
        ).mappings().first()
        if not pref or pref["status"] not in ("approved",):
            # FIX (May 2026): bypass preferred-status check if the venue has
            # blast_all_enabled=1 for an open-gig blast whose window has been
            # reached. Without this, non-preferred artists who got the "any
            # artist can book" email would hit a confusing 403 here.
            if _open_blast_bypass_active(db, gig["venue_id"], gig_id):
                logger.info(f"[BOOK_SLOT] open-blast-all bypass — artist {artist_id} booking slot on gig {gig_id}")
                pref = {"status": "blast"}
            else:
                raise HTTPException(403, "Artist is not approved for this venue")
    else:
        logger.info(f"[BOOK_SLOT] blast bypass — artist {artist_id} booking open blast slot on gig {gig_id}")
        pref = db.execute(
            text("SELECT status, frequency_days_override FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"),
            {"vid": gig["venue_id"], "aid": artist_id}
        ).mappings().first() or {"status": "blast", "frequency_days_override": None}

    # Check if venue requires W9
    venue_tax = db.execute(
        text("SELECT require_w9 FROM venue_tax_settings WHERE venue_id = :vid"),
        {"vid": gig["venue_id"]}
    ).first()
    if venue_tax and venue_tax[0]:
        # TZ FIX: venue-local year (see W-9 fix in book_slot above).
        from backend.utils import get_venue_timezone
        current_year = datetime.now(get_venue_timezone(db, gig["venue_id"])).year
        w9 = db.execute(
            text("SELECT tax_year FROM w9_forms WHERE entity_type = 'artist' AND entity_id = :aid ORDER BY tax_year DESC LIMIT 1"),
            {"aid": artist_id}
        ).first()
        if not w9 or w9[0] < current_year:
            raise HTTPException(403, "W9_REQUIRED: This venue requires an up-to-date W-9 on file before booking.")

    # Check artist hasn't already booked another slot in this gig.
    # FIX (May 2026 audit #8): widen status filter beyond 'booked' to include
    # in-transit states. Previously an artist with a 'pending_venue_approval' /
    # 'pending_contract' / 'awaiting_venue_contract' slot could request a
    # second slot on the same gig. On approval, _create_booking_transaction's
    # existing-payout guard would silently refuse the second insert, leaving
    # a slot marked booked without an artist_payout child → fee imbalance.
    # Allow re-booking the SAME slot the artist is already pending on (the
    # caller's slot_id), since book_slot supports re-submitting that case.
    existing_slot = db.execute(
        text("""SELECT id FROM gig_slots
                WHERE gig_id = :gid AND artist_id = :aid AND id != :sid
                  AND status IN ('booked','pending_venue_approval','pending_contract','awaiting_venue_contract')"""),
        {"gid": gig_id, "aid": artist_id, "sid": slot_id}
    ).mappings().first()
    if existing_slot:
        raise HTTPException(403, "You already have a slot booked or pending on this gig. Each artist can only hold one slot per event.")

    # Check artist blackout dates
    gig_date_str = db.execute(
        text("SELECT date FROM gigs WHERE id = :gid"), {"gid": gig_id}
    ).scalar()
    if gig_date_str:
        blackout = db.execute(
            text("""
                SELECT id, reason FROM artist_availability
                WHERE artist_id = :aid
                  AND date(:d) BETWEEN date(blackout_start) AND date(blackout_end)
                LIMIT 1
            """),
            {"aid": artist_id, "d": str(gig_date_str)[:10]}
        ).mappings().first()
        if blackout:
            reason = blackout.get("reason", "") or "marked as unavailable"
            raise HTTPException(403, f"You have a blackout on this date: {reason}")

    # ── Frequency check (FIX 2026-05-22 audit): venue's policy on how many
    # days between performances by the same artist. Was previously enforced
    # in book_gig + book_with_contract via _run_prebooking_checks, but the
    # multi-slot path here skipped it entirely — `pref.frequency_days_override`
    # was fetched at line ~3590 and then never used. So a venue saying "1 gig
    # per artist every 30 days" was a polite suggestion to multi-slot artists.
    # Same waiver rules as _run_prebooking_checks: blast-token or in-blast-window
    # bypasses the limit (blast = "venue is desperate to fill this gig").
    _freq_blast_waives = slot_token_valid
    if not _freq_blast_waives and gig_date_str:
        try:
            from datetime import date as _dc, datetime as _dt
            from backend.utils import get_venue_timezone as _gvt
            _today = _dt.now(_gvt(db, gig["venue_id"])).date()
            _gig_d = _dc.fromisoformat(str(gig_date_str)[:10])
            _days = (_gig_d - _today).days
            if _days >= 0:
                # Jul 2026 fix: also gate on waive_frequency — the multi-slot
                # book_slot path was silently waiving frequency inside any
                # enabled window regardless of the venue's waive-toggle. Now
                # matches the single-slot book_gig behavior.
                _brows = db.execute(text("""SELECT time_value, time_unit FROM venue_email_notifications
                                            WHERE venue_id=:vid AND notification_key IN ('open_gig_36h','open_gig_1w')
                                              AND enabled=1 AND COALESCE(waive_frequency, 1) = 1"""),
                                    {"vid": gig["venue_id"]}).mappings().all()
                for _br in _brows:
                    _tv, _tu = _br["time_value"], _br["time_unit"]
                    _w = _tv/24 if _tu == "hours" else (_tv if _tu == "days" else _tv*7)
                    if _days <= _w:
                        _freq_blast_waives = True
                        break
        except Exception:
            pass
    # Venue-set frequency-exempt waiver — mirrors book_gig at line 2447.
    # Without this, the multi-slot path silently 403'd exempt artists even
    # though the venue had explicitly waived the policy for them.
    if not _freq_blast_waives:
        _exempt_row2 = db.execute(text("SELECT COALESCE(frequency_exempt,0) as fx FROM gigs WHERE id=:gid"),
                                   {"gid": gig_id}).mappings().first()
        if _exempt_row2 and _exempt_row2.get("fx"):
            _freq_blast_waives = True
    if not _freq_blast_waives and gig_date_str:
        freq = db.execute(text("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
                                  FROM preferred_artists pa JOIN venues v ON v.id=pa.venue_id
                                  WHERE pa.venue_id=:vid AND pa.artist_id=:aid"""),
                          {"vid": gig["venue_id"], "aid": artist_id}).mappings().first()
        if not freq:
            # No preferred_artists row — fall back to venue default.
            _vf = db.execute(text("SELECT artist_frequency_days FROM venues WHERE id=:vid"),
                             {"vid": gig["venue_id"]}).mappings().first()
            freq = {"freq_days": (_vf or {}).get("artist_frequency_days")}
        if freq and (freq["freq_days"] or 0) > 0:
            # Jul 2026 refactor: dropped `g.artist_id=:aid OR` leg — slot-
            # only covers both shapes post-backfill.
            # 2026-08-08 audit fix (same class as #5-7): see gigs.py:2275
            # comment. Outer g.status filter dropped partial multi-slot
            # gigs from the freq check.
            close = db.execute(text("""SELECT g.date FROM gigs g
                                       JOIN gig_slots gs ON gs.gig_id=g.id AND gs.artist_id=:aid
                                       WHERE g.venue_id=:vid AND g.id!=:gid
                                         AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                                       ORDER BY ABS(JULIANDAY(g.date)-JULIANDAY(:d)) LIMIT 1"""),
                              {"vid": gig["venue_id"], "aid": artist_id, "gid": gig_id, "d": gig_date_str}).mappings().first()
            if close:
                from datetime import datetime as _dt2
                d1 = _dt2.strptime(str(gig_date_str)[:10], "%Y-%m-%d")
                d2 = _dt2.strptime(str(close["date"])[:10], "%Y-%m-%d")
                apart = abs((d1 - d2).days)
                if apart <= freq["freq_days"]:
                    needed = freq["freq_days"] + 1 - apart
                    dir_msg = f"You have a gig {'before' if d2 > d1 else 'after'} this one on {d2.strftime('%b %d, %Y')}."
                    raise HTTPException(403,
                        f"This venue requires more than {freq['freq_days']} days between performances. "
                        f"{dir_msg} Gigs must be at least {needed} more day{'s' if needed != 1 else ''} apart.")

    # Audit fix (May 2026): Stripe Connect onboarding gate (same as book_gig).
    # Test Mode removed (Jul 1 2026): hard-blocks unconditionally.
    try:
        _eps = db.execute(
            text("""SELECT COALESCE(stripe_connect_onboarding_complete, 0) as ok
                    FROM entity_payment_settings
                    WHERE entity_type = 'artist' AND entity_id = :aid"""),
            {"aid": artist_id}
        ).mappings().first()
        if not _eps or not int(_eps["ok"] or 0):
            raise HTTPException(
                402,
                "STRIPE_ONBOARDING_INCOMPLETE: Connect your payout account in your artist Payments tab before booking."
            )
    except HTTPException:
        raise
    except Exception:
        pass

    # Apply pay override. Rule (Jun 2026):
    #   - Flat: bump slot.pay to override if higher (always).
    #   - Door: bump guarantee + mirror into pay ONLY when
    #           slot.apply_override = 1 (venue's per-slot opt-in).
    slot_pay_override = db.execute(
        text("""
            SELECT pa.pay_dollars_override, pa.pay_cents_override
            FROM preferred_artists pa
            WHERE pa.venue_id = :vid AND pa.artist_id = :aid
              AND pa.status = 'approved'
        """),
        {"vid": gig["venue_id"], "aid": artist_id}
    ).mappings().first()

    _slot_deal_type = (slot.get("deal_type") or "").lower() if isinstance(slot, dict) or hasattr(slot, "get") else ""
    _slot_apply_override = int((slot.get("apply_override") if hasattr(slot, "get") else 0) or 0)
    if slot_pay_override and slot_pay_override["pay_dollars_override"] is not None:
        override_pay = float(slot_pay_override["pay_dollars_override"]) + float(slot_pay_override["pay_cents_override"] or 0) / 100
        if _slot_deal_type == "door" and _slot_apply_override == 1:
            cur_gua_dollars = float(slot.get("guarantee_cents") or 0) / 100.0
            if override_pay > cur_gua_dollars:
                db.execute(
                    text("UPDATE gig_slots SET guarantee_cents = :gc, pay = :pay WHERE id = :sid"),
                    {"gc": int(round(override_pay * 100)), "pay": override_pay, "sid": slot_id}
                )
        elif _slot_deal_type != "door":
            slot_pay = float(slot.get("pay") or 0)
            if override_pay > slot_pay:
                db.execute(
                    text("UPDATE gig_slots SET pay = :pay WHERE id = :sid"),
                    {"pay": override_pay, "sid": slot_id}
                )

    # Same-day booking: only require approval for non-preferred (radius) artists.
    # Jul 21 2026: also skip when the venue owner has opted out of the
    # `venue_booking_approval_request` preference — same reasoning as the
    # gig-level book path above.
    _is_preferred_slot = pref and pref.get("status") == "approved"
    _ensure_approval_columns(db)
    if (_is_same_day_booking(gig["date"], gig.get("start_time"), venue_id=gig.get("venue_id"))
            and not _is_preferred_slot
            and _venue_requires_same_day_approval(db, gig.get("venue_id"))):
        # Audit fix (May 2026 part 5): atomic claim guard — without
        # `AND status='open'` two concurrent same-day book_slot requests
        # would both pass and both UPDATE. Rowcount check raises SLOT_TAKEN.
        _claim_ss = db.execute(
            text("UPDATE gig_slots SET artist_id = :aid, status = 'pending_venue_approval', approval_requested_at = :now "
                 "WHERE id = :sid AND status = 'open'"),
            {"aid": artist_id, "sid": slot_id, "now": _utcnow_naive().isoformat()}
        )
        if (_claim_ss.rowcount or 0) == 0:
            raise HTTPException(409, "SLOT_TAKEN: Another booking just claimed this slot. Please refresh.")
        # For single-slot gigs: update parent gig status so calendar bubble shows correctly.
        # For multi-slot: leave gig.status='open' so other slots stay bookable.
        _other_open = db.execute(
            text("SELECT COUNT(*) FROM gig_slots WHERE gig_id=:gid AND id!=:sid AND status='open'"),
            {"gid": gig_id, "sid": slot_id}
        ).scalar()
        if not _other_open:
            # No other open slots — safe to mark whole gig pending
            db.execute(
                text("UPDATE gigs SET artist_id = :aid, status = 'pending_venue_approval', approval_requested_at = :now WHERE id = :gid"),
                {"aid": artist_id, "gid": gig_id, "now": _utcnow_naive().isoformat()}
            )
        # else: gig stays 'open', other slots remain bookable
        gig_details_approval = db.execute(
            text("""
                SELECT g.id, g.date, g.title, g.start_time, g.end_time, g.pay,
                       g.venue_id, v.venue_name, v.user_id as venue_user_id,
                       a.name as artist_name, a.user_id as artist_user_id
                FROM gigs g
                JOIN venues v ON g.venue_id = v.id
                JOIN artists a ON a.id = :aid
                WHERE g.id = :gid
            """),
            {"gid": gig_id, "aid": artist_id}
        ).mappings().first()
        if gig_details_approval:
            slot_info_str = f"Slot {slot['slot_number']}: {format_time_12hr(slot['start_time'])} – {format_time_12hr(slot['end_time'])}"
            try:
                send_approval_request_emails(db, dict(gig_details_approval), artist_id,
                                             slot_info=slot_info_str)
            except Exception as e:
                logger.error(f"[BOOK_SLOT] approval request email error: {e}")
        db.commit()  # Commit after email func so approval_token flush is included
        if gig_details_approval:
            try:
                create_notification(
                    db,
                    gig_details_approval["venue_user_id"],
                    "booking_approval_request",
                    "⏳ Booking Approval Needed",
                    f"{gig_details_approval['artist_name']} is requesting same-day slot booking approval for today's gig.",
                    gig_id=gig_id,
                    venue_id=gig['venue_id'],
                    artist_id=artist_id
                )
                db.commit()
            except Exception as e:
                logger.error(f"[BOOK_SLOT] approval notification error: {e}")
        return {"ok": True, "pending_approval": True}

    # Book the slot — atomic claim guarded by status='open' (or this artist's
    # own pending_venue_approval row, since book_slot supports re-submitting
    # a pending slot — see line ~2971). Audit fix (May 2026): the prior UPDATE
    # had no status guard, so two artists hitting "Book this slot" within the
    # same few ms could both pass the prior SELECT-status check and both
    # write — last write wins, first artist already had a confirmation email
    # and transaction row pointing at a slot reassigned to someone else.
    _claim = db.execute(
        text("""UPDATE gig_slots
                SET artist_id = :aid, status = 'booked'
                WHERE id = :sid AND (
                    status = 'open'
                    OR (status = 'pending_venue_approval' AND artist_id = :aid)
                )"""),
        {"aid": artist_id, "sid": slot_id}
    )
    if (_claim.rowcount or 0) == 0:
        raise HTTPException(409, "SLOT_TAKEN: This slot was just booked by someone else. Please refresh and try a different slot.")

    # FIX (May 2026 audit #4): clear last_cancelled_artist_id when a new booking
    # lands. book_gig already does this at line ~1586. Without it, a multi-slot
    # gig that previously had an artist cancel keeps that artist's id pinned —
    # any future cancellation that triggers a blast on this gig will silently
    # filter the original canceller out of the recipient list forever.
    db.execute(
        text("UPDATE gigs SET last_cancelled_artist_id = NULL WHERE id = :gid"),
        {"gid": gig_id}
    )

    # Remove artist from waitlist for this gig — they booked a slot, no longer waiting
    try:
        db.execute(text("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"),
                   {"gid": gig_id, "aid": artist_id})
        db.execute(text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
                   {"gid": gig_id, "aid": artist_id})
        # Clear the in-app "Waitlist Spot Available" Activity Center notification
        # so the artist's feed doesn't keep showing a stale invitation after they book.
        from backend.routes.waitlist import _clear_waitlist_offer_notification as _cwon
        _cwon(db, gig_id, artist_id)
    except Exception as _wlr:
        logger.warning(f"[BOOK_SLOT] waitlist remove on book error: {_wlr}")

    # Clear radius_blast_token — gig no longer needs blasting once a slot is booked
    db.execute(text("UPDATE gigs SET radius_blast_token = NULL WHERE id = :gid"),
               {"gid": gig_id})

    # Check if ALL slots are now booked → update parent gig status
    open_slots = db.execute(
        text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
        {"gid": gig_id}
    ).scalar()
    
    if open_slots == 0:
        db.execute(
            text("UPDATE gigs SET status = 'booked', radius_blast_token = NULL WHERE id = :gid"),
            {"gid": gig_id}
        )

    # Create notifications
    gig_details = db.execute(
        text("""
            SELECT g.date, g.title, g.artist_type, g.band_formats, g.styles, v.venue_name, v.user_id as venue_user_id,
                   a.name as artist_name, a.user_id as artist_user_id
            FROM gigs g
            JOIN venues v ON g.venue_id = v.id
            JOIN artists a ON a.id = :aid
            WHERE g.id = :gid
        """),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()

    if gig_details:
        # FIX (May 2026 audit #6): use notify_gig_booked which fans out to ALL
        # entity users on multi-user artist/venue accounts. The previous raw
        # INSERTs only notified the primary artist_user_id and venue_user_id,
        # so secondary users got booking emails but no in-app notification.
        # book_gig already uses this helper (line ~1623); book_slot now matches.
        try:
            # Pass slot's start_time so the notification message includes the
            # right time (slot may differ from the gig's overall start).
            _gd = dict(gig_details)
            _gd["start_time"] = slot.get("start_time") or _gd.get("start_time")
            notify_gig_booked(db, _gd, gig_id, gig["venue_id"], artist_id)
        except Exception as _ne:
            logger.error(f"BookSlot notify_gig_booked error: {_ne}")

    db.commit()
    
    # Send booking emails via unified function — pass slot_id so only this slot is emailed
    try:
        send_booking_emails(db, gig_id, slot_id=slot_id)
    except Exception as e:
        logger.error(f"BookSlot email error: {e}")
    
    # Create transaction record for payment tracking. See [BOOK_GIG][TXN_MISSING]
    # comment above — same swallow-and-log pattern. The slot is already booked,
    # confirmation email already sent, so re-raising would surprise the artist.
    # Loud ERROR log with full context lets admin spot + fix via Payments console.
    try:
        slot_pay_txn = db.execute(
            text("SELECT pay FROM gig_slots WHERE id = :sid"), {"sid": slot_id}
        ).mappings().first()
        _create_booking_transaction(
            db, gig_id, gig["venue_id"], artist_id,
            slot_pay_txn["pay"] if slot_pay_txn else 0, gig.get("date"), slot_id=slot_id
        )
        db.commit()
    except Exception as e:
        logger.error(
            f"[BOOK_SLOT][TXN_MISSING] _create_booking_transaction FAILED — "
            f"gig_id={gig_id} venue_id={gig['venue_id']} artist_id={artist_id} "
            f"slot_id={slot_id} pay={(slot_pay_txn or {}).get('pay') if 'slot_pay_txn' in dir() else '?'} "
            f"date={gig.get('date')}. Booking + email already landed; admin must "
            f"create the missing transaction row manually. Error: {e}",
            exc_info=True,
        )
    
    return {"ok": True, "all_booked": open_slots == 0}


# SAME-DAY BOOKING APPROVAL / DENIAL
# Audit fix (May 2026 part 5): the @router.get decorator here used to
# bind to `_styled_page` (next function below) because of the blank line
# — the GET email-link approval endpoint was completely broken, every
# venue-approval click from email returned 422. The GET is now declared
# alongside the POST decorator on the real `approve_booking` handler
# further down the file.

def _styled_page(icon: str, color: str, title: str, body: str) -> str:
    border = color.replace('#', '')
    return f'''<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — GigsFill</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: linear-gradient(135deg, #0f1419 0%, #1a1f2e 100%); min-height: 100vh;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         display: flex; align-items: center; justify-content: center; padding: 20px; }}
  .card {{ background: linear-gradient(135deg, #1a1f2e, #0f1419); border: 2px solid {color}66;
           border-radius: 16px; padding: 48px 40px; max-width: 420px; width: 100%;
           text-align: center; box-shadow: 0 8px 32px {color}26; }}
  .icon {{ font-size: 3rem; margin-bottom: 20px; }}
  h2 {{ color: {color}; font-size: 1.6rem; font-weight: 700; margin-bottom: 12px; }}
  p {{ color: #9ca3af; font-size: 1rem; line-height: 1.6; margin-bottom: 28px; }}
  a {{ display: inline-block; background: linear-gradient(135deg, #7c6bff, #06b6d4);
       color: #fff; text-decoration: none; padding: 12px 28px; border-radius: 8px;
       font-weight: 600; font-size: 0.95rem; }}
  .logo {{ color: #7c6bff; font-size: 1.1rem; font-weight: 700; margin-bottom: 32px; }}
</style></head>
<body><div class="card">
  <div class="logo">🎵 GigsFill</div>
  <div class="icon">{icon}</div>
  <h2>{title}</h2>
  <p>{body}</p>
  <a href="/app/venue-create-gigs.html">View Calendar</a>
</div></body></html>'''


@router.get("/api/gigs/{gig_id}/approve-booking")
@router.post("/api/gigs/{gig_id}/approve-booking")
def approve_booking(gig_id: int, request: Request, db=Depends(get_db), user=Depends(get_optional_user)):
    """Venue approves a pending_venue_approval booking. Accessible via email link (token) or logged-in venue user."""
    token = request.query_params.get('token')
    artist_id = request.query_params.get('artist_id')
    if not artist_id:
        raise HTTPException(400, "artist_id required")
    artist_id = int(artist_id)

    # Prefetch defense (Jul 1 2026): Gmail/Outlook/Slack link prefetchers
    # auto-GET URLs in incoming mail. A one-click GET-that-mutates would
    # approve the booking (and auto-execute a contract) before the venue
    # ever clicked. If this is a GET with an email token, render a
    # confirmation page whose form POST fires the actual approval.
    # Deny-booking has the same shape at line 5448.
    if request.method == "GET" and token:
        from fastapi.responses import HTMLResponse
        _t = _h_esc(token)
        _aid = int(artist_id)
        _gid = int(gig_id)
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Approve booking · GigsFill</title>
<style>
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#0f1419; margin:0; padding:40px 20px; color:#e4e7eb; }}
  .card {{ max-width:480px; margin:60px auto; background:#1a1f2e;
         border:1px solid #2d3548; border-radius:12px; padding:32px 36px;
         text-align:center; }}
  h1 {{ font-size:1.5rem; margin:0 0 12px;
       background:linear-gradient(135deg,#7c6bff,#06b6d4);
       background-clip:text; -webkit-background-clip:text;
       -webkit-text-fill-color:transparent; }}
  p {{ color:#9ca3af; line-height:1.55; }}
  .btn {{ display:inline-block; padding:12px 28px; border:none;
        border-radius:6px; font-size:0.95rem; font-weight:600;
        cursor:pointer; text-decoration:none; margin:6px 4px; }}
  .btn-approve {{ background:#22c55e; color:#fff; }}
  .btn-back {{ background:#374151; color:#fff; }}
</style></head><body>
<div class="card">
  <div style="font-size:2.6rem;margin-bottom:8px;">✅</div>
  <h1>Approve this booking?</h1>
  <p>Clicking <strong>Approve</strong> will confirm the artist's request,
  book the slot, and email them the decision.</p>
  <form method="POST" action="/api/gigs/{_gid}/approve-booking?token={_t}&amp;artist_id={_aid}"
        style="margin-top:18px;">
    <button class="btn btn-approve" type="submit">Yes, approve</button>
    <a class="btn btn-back" href="/app/venue-create-gigs.html">Cancel</a>
  </form>
</div></body></html>"""
        return HTMLResponse(content=html)

    _ensure_approval_columns(db)

    gig = db.execute(
        text("SELECT id, venue_id, status, date, start_time, end_time, pay, title, artist_id, approval_token FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")

    # Verify token OR logged-in venue user
    if token:
        # Audit fix (May 2026 part 5): look up by per-(gig, artist) token in
        # pending_approval_tokens FIRST. The legacy single-column gigs.approval_token
        # remains as a fallback so links emailed before this deploy still work.
        # Jul 2026 audit (B-C2): also enforce expires_at so a replayable
        # token can't sit valid forever. NULL expires_at = pre-migration
        # row, treat as valid.
        _per_artist = None
        try:
            _per_artist = db.execute(
                text("SELECT artist_id, expires_at FROM pending_approval_tokens WHERE token = :tok AND gig_id = :gid"),
                {"tok": token, "gid": gig_id}
            ).mappings().first()
        except Exception:
            _per_artist = None
        _token_matches_per_artist = False
        if _per_artist and int(_per_artist["artist_id"]) == artist_id:
            _exp = _per_artist.get("expires_at")
            if _exp:
                try:
                    from datetime import datetime as _dt
                    _exp_naive = str(_exp).split(".")[0].split("+")[0].replace("T", " ").strip()
                    _exp_dt = _dt.strptime(_exp_naive, "%Y-%m-%d %H:%M:%S")
                    if _exp_dt >= _dt.utcnow():
                        _token_matches_per_artist = True
                except Exception:
                    _token_matches_per_artist = True  # unparseable → treat as legacy
            else:
                _token_matches_per_artist = True
        _token_matches_legacy = bool(gig["approval_token"] and token == gig["approval_token"])
        if not (_token_matches_per_artist or _token_matches_legacy):
            # Token is gone — check if already actioned (already approved or denied)
            from fastapi.responses import HTMLResponse
            slot_booked = db.execute(
                text("SELECT id FROM gig_slots WHERE gig_id = :gid AND artist_id = :aid AND status = 'booked'"),
                {"gid": gig_id, "aid": artist_id}
            ).first()
            if slot_booked or gig["status"] == "booked":
                return HTMLResponse(_styled_page('✅', '#22c55e', 'Already Approved', 'This booking was already approved. The artist has been notified.'))
            return HTMLResponse(_styled_page('⏱️', '#d97706', 'Link Expired', 'This approval link has already been used or expired. Please manage bookings from your calendar.'))
    else:
        if not user:
            raise HTTPException(403, "Authentication required")
        # Audit fix (May 2026 part 2): use check_venue_access so entity_users
        # (bookers / managers) can approve from the in-UI path. The old
        # owner-only `venues.user_id = :uid` lookup blocked them with 403.
        from backend.utils import check_venue_access
        check_venue_access(db, gig["venue_id"], user.id)

    # Check gig itself is pending OR a slot for this artist is pending
    slot = db.execute(
        text("SELECT id, slot_number, start_time, end_time, pay FROM gig_slots WHERE gig_id = :gid AND artist_id = :aid AND status = 'pending_venue_approval'"),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()

    # Audit fix (May 2026 part 10 → finalized in part 10b): when the
    # contract-required venue approves a same-day non-preferred booking,
    # auto-execute a digital contract record using BOTH the artist's
    # "Book Now" click (recorded as artist_signature) AND the venue's
    # "Approve" click (recorded as venue_signature). Both parties
    # affirmatively agreed via discrete UI actions; the contract row
    # captures both signatures with timestamps + IPs for legal audit trail.
    # Without this, the same-day path silently skipped the venue's
    # `require_for_booking=1` setting — bookings happened that were
    # contractually unenforceable.
    _venue_requires_contract_now = False
    try:
        _vc_check = db.execute(text("""
            SELECT vc.id, vc.contract_type, vc.contract_body
            FROM venue_contracts vc
            WHERE vc.venue_id = :vid AND vc.is_active = 1 AND vc.require_for_booking = 1
            LIMIT 1
        """), {"vid": gig["venue_id"]}).mappings().first()
        _pref_check = db.execute(
            text("SELECT status FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid"),
            {"vid": gig["venue_id"], "aid": artist_id}
        ).first()
        if _vc_check and not (_pref_check and _pref_check[0] == "approved"):
            _venue_requires_contract_now = True
            # Create the auto-executed contract record. Both signatures are
            # the user's IPs at the moment of their respective clicks; the
            # body notes that this was a same-day-approval flow.
            try:
                _client_ip = request.client.host if request.client else "unknown"
                _fwd = request.headers.get("x-forwarded-for", "")
                if _fwd:
                    _client_ip = _fwd.split(",")[0].strip()
                _existing_contract = db.execute(text(
                    "SELECT 1 FROM gig_contracts WHERE gig_id=:gid AND artist_id=:aid"
                ), {"gid": gig_id, "aid": artist_id}).first()
                if not _existing_contract:
                    _now_iso = _utcnow_naive().isoformat() + 'Z'
                    _body = (_vc_check.get("contract_body") or
                             "Auto-executed via same-day approval flow.")
                    db.execute(text("""
                        INSERT INTO gig_contracts
                            (gig_id, venue_contract_id, venue_id, artist_id, contract_type,
                             rendered_body, filled_fields, status,
                             artist_signature_name, artist_signature_date, artist_signature_ip,
                             venue_signature_name, venue_signature_date, venue_signature_ip,
                             created_at)
                        VALUES (:gid, :vcid, :vid, :aid, 'digital',
                                :body, '{}', 'fully_signed',
                                :asig, :asig_date, :asig_ip,
                                :vsig, :vsig_date, :vsig_ip,
                                CURRENT_TIMESTAMP)
                    """), {
                        "gid": gig_id, "vcid": _vc_check["id"],
                        "vid": gig["venue_id"], "aid": artist_id,
                        "body": _body,
                        "asig": "Implied by 'Book Now' click (same-day approval flow)",
                        "asig_date": _now_iso, "asig_ip": _client_ip,
                        "vsig": "Implied by 'Approve' click (same-day approval flow)",
                        "vsig_date": _now_iso, "vsig_ip": _client_ip,
                    })
                    db.commit()
                    logger.info(
                        f"[CONTRACT_AUTOEXEC] Same-day approval auto-executed digital "
                        f"contract for gig {gig_id} artist {artist_id} venue {gig['venue_id']}."
                    )
            except Exception as _ce:
                logger.warning(f"Auto-contract create on same-day approval failed for gig {gig_id}: {_ce}")
    except Exception:
        pass

    if slot:
        # Audit YELLOW fix (Jul 1 2026): approvals can fire hours after the
        # artist's original "Book" click. In the gap the artist may have
        # booked a conflicting gig elsewhere — re-run the cross-venue
        # time-overlap check at approval time. If it raises, the pending
        # slot stays pending (venue can deny it explicitly).
        try:
            _enforce_no_artist_time_overlap(
                db, artist_id, gig_id, gig["venue_id"],
                str(gig["date"])[:10],
                slot.get("start_time"), slot.get("end_time")
            )
        except HTTPException as _overlap_exc:
            # Surface the overlap message so the venue sees WHY the
            # approval was blocked. Slot stays pending.
            _detail = getattr(_overlap_exc, "detail", None) or "Artist booked elsewhere in this time window."
            raise HTTPException(_overlap_exc.status_code, _detail)

        # Audit fix (May 2026): atomic-claim guard. The previous unconditional
        # UPDATE meant a double-clicked email link or a refresh would re-fire
        # the entire post-block — venue + artist got duplicate booking emails
        # and notifications. Conditional UPDATE + rowcount check ensures the
        # second request finds rowcount=0 and returns idempotently.
        _claim = db.execute(
            text("UPDATE gig_slots SET status = 'booked' "
                 "WHERE id = :sid AND status = 'pending_venue_approval'"),
            {"sid": slot["id"]}
        )
        if (_claim.rowcount or 0) == 0:
            # Already approved by an earlier in-flight request.
            db.commit()
            return {"ok": True, "already_approved": True}
        # Audit YELLOW fix (Jul 1 2026): clear last_cancelled_artist_id
        # so the cancelled artist isn't permanently excluded from future
        # cancellation blasts on this gig. book_slot at line 5039 does
        # the same at booking commit time; approve was missed.
        db.execute(
            text("UPDATE gigs SET last_cancelled_artist_id = NULL WHERE id = :gid"),
            {"gid": gig_id}
        )
        # Clear token (also conditional so a token-replay race is a no-op).
        # Audit fix (May 2026 part 5): also delete this artist's row from
        # pending_approval_tokens so the email link can't be reused.
        db.execute(
            text("UPDATE gigs SET approval_token = NULL "
                 "WHERE id = :gid AND approval_token IS NOT NULL"),
            {"gid": gig_id}
        )
        try:
            db.execute(
                text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": artist_id}
            )
        except Exception as _ptcleanup_err:
            # Audit fix (Jun 2026): was silently swallowed. If the row
            # stays, the approval link can be replayed. Log loudly so
            # the failure surfaces in journalctl instead of vanishing.
            logger.warning(
                f"[APPROVE] pending_approval_tokens cleanup failed for "
                f"gig={gig_id} artist={artist_id}: {_ptcleanup_err} — "
                f"approval link MAY be replayable until cleanup succeeds"
            )
        # Check if all slots booked → mark gig booked; otherwise reset to 'open'
        # (parent gig was set to pending_venue_approval during slot booking — must reset it)
        open_slots = db.execute(
            text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
            {"gid": gig_id}
        ).scalar()
        if open_slots == 0:
            db.execute(text("UPDATE gigs SET status = 'booked' WHERE id = :gid"), {"gid": gig_id})
        else:
            # Slots still open — reset parent gig to 'open' so other artists can book them
            db.execute(text("UPDATE gigs SET status = 'open', artist_id = NULL WHERE id = :gid"), {"gid": gig_id})
        db.commit()
        # Create transaction
        effective_pay = float(slot.get("pay") or gig.get("pay") or 0)
        _create_booking_transaction(db, gig_id, gig["venue_id"], artist_id, effective_pay, gig["date"], slot_id=slot["id"])
        # Remove booking artist from waitlist; clean all if gig fully booked
        try:
            from sqlalchemy import text as _t3
            db.execute(_t3("DELETE FROM gig_waitlist WHERE gig_id = :gid AND artist_id = :aid"), {"gid": gig_id, "aid": artist_id})
            db.commit()
        except Exception as _we2:
            logger.warning(f"[BOOK_SLOT] waitlist cleanup error: {_we2}")
        db.commit()
        slot_info_str = f"Slot {slot['slot_number']}: {format_time_12hr(slot['start_time'])} – {format_time_12hr(slot['end_time'])}"
    elif gig["status"] == "pending_venue_approval" and gig["artist_id"] == artist_id:
        # Backstop branch: gig flagged pending_venue_approval but no matching
        # gig_slots row (e.g., legacy bookings made under the pre-2026-05-08 fix
        # where book_gig only stamped the gigs row). Promote the open slot too
        # so transactions get the right slot_id and downstream cancel paths
        # that match by gig_slots.artist_id can find the booking.
        # Audit fix (May 2026): conditional gigs UPDATE so a token-replay race
        # short-circuits before re-firing emails/notifications.
        _gig_claim = db.execute(
            text("""UPDATE gigs SET status = 'booked', approval_token = NULL
                    WHERE id = :gid AND status = 'pending_venue_approval'"""),
            {"gid": gig_id}
        )
        if (_gig_claim.rowcount or 0) == 0:
            db.commit()
            return {"ok": True, "already_approved": True}
        _backstop_open_slot = db.execute(
            text("SELECT id FROM gig_slots WHERE gig_id = :gid AND status = 'open' ORDER BY slot_number ASC LIMIT 1"),
            {"gid": gig_id}
        ).mappings().first()
        if _backstop_open_slot:
            db.execute(
                text("UPDATE gig_slots SET artist_id = :aid, status = 'booked', approval_requested_at = NULL "
                     "WHERE id = :sid AND status = 'open'"),
                {"aid": artist_id, "sid": _backstop_open_slot["id"]}
            )
        db.commit()
        _create_booking_transaction(
            db, gig_id, gig["venue_id"], artist_id, gig.get("pay"), gig["date"],
            slot_id=(_backstop_open_slot["id"] if _backstop_open_slot else None)
        )
        db.commit()
        slot_info_str = ""
    else:
        return {"ok": False, "message": "No pending approval found for this artist"}

    # Fetch names for emails
    names = db.execute(
        text("""
            SELECT g.date, g.start_time, g.end_time, g.pay, g.title,
                   g.venue_id, v.venue_name, a.name as artist_name
            FROM gigs g JOIN venues v ON g.venue_id=v.id JOIN artists a ON a.id=:aid
            WHERE g.id=:gid
        """),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()
    if names:
        try:
            send_approval_decision_emails(db, dict(names), artist_id, approved=True, slot_info=slot_info_str)
            notify_gig_booked(db, dict(names), gig_id, gig["venue_id"], artist_id)
            # Send the standard booking confirmation email to BOTH artist
            # and venue. Audit YELLOW fix (Jul 1 2026): thread the just-
            # approved slot's id through so send_booking_emails only fires
            # for THIS slot on multi-slot gigs — otherwise every previously-
            # booked slot on the same gig re-emails on each approval.
            try:
                # Audit fix (Jul 1 2026 regression sweep): the `dir()` idiom
                # for detecting the backstop-path variable was fragile —
                # a nested refactor would break it silently. locals() gives
                # a definite answer inside the function scope.
                _bs = locals().get("_backstop_open_slot")
                _approved_slot_id = slot["id"] if slot else (_bs["id"] if _bs else None)
                send_booking_emails(db, gig_id, slot_id=_approved_slot_id)
            except Exception as _be:
                logger.error(f"[APPROVE_BOOKING] send_booking_emails error: {_be}")
        except Exception as e:
            logger.error(f"[APPROVE_BOOKING] email error: {e}")

    from fastapi.responses import HTMLResponse
    if token:
        return HTMLResponse('<html>\n<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Booking Approved — GigsFill</title>\n<style>\n  * { box-sizing: border-box; margin: 0; padding: 0; }\n  body { background: linear-gradient(135deg, #0f1419 0%, #1a1f2e 100%); min-height: 100vh;\n         font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif;\n         display: flex; align-items: center; justify-content: center; padding: 20px; }\n  .card { background: linear-gradient(135deg, #1a1f2e, #0f1419); border: 2px solid rgba(34,197,94,0.4);\n           border-radius: 16px; padding: 48px 40px; max-width: 420px; width: 100%;\n           text-align: center; box-shadow: 0 8px 32px rgba(34,197,94,0.15); }\n  .icon { font-size: 3rem; margin-bottom: 20px; }\n  h2 { color: #22c55e; font-size: 1.6rem; font-weight: 700; margin-bottom: 12px; }\n  p { color: #9ca3af; font-size: 1rem; line-height: 1.6; margin-bottom: 28px; }\n  a { display: inline-block; background: linear-gradient(135deg, #7c6bff, #06b6d4);\n       color: #fff; text-decoration: none; padding: 12px 28px; border-radius: 8px;\n       font-weight: 600; font-size: 0.95rem; transition: opacity 0.2s; }\n  a:hover { opacity: 0.85; }\n  .logo { color: #7c6bff; font-size: 1.1rem; font-weight: 700; margin-bottom: 32px; letter-spacing: 0.5px; }\n</style></head>\n<body><div class="card">\n  <div class="logo">🎵 GigsFill</div>\n  <div class="icon">✅</div>\n  <h2>Booking Approved!</h2>\n  <p>The artist has been notified and their booking is confirmed. You\'ll both receive a confirmation email shortly.</p>\n  <a href="/app/venue-create-gigs.html">View Calendar</a>\n</div></body></html>')
    return {"ok": True}


@router.get("/api/gigs/{gig_id}/deny-booking")
@router.post("/api/gigs/{gig_id}/deny-booking")
def deny_booking(gig_id: int, request: Request, db=Depends(get_db), user=Depends(get_optional_user)):
    """Venue denies a pending_venue_approval booking.

    Audit fix (May 2026 part 5): the GET path used to mutate state directly,
    which meant:
      • Corporate email-scanner pre-fetches accidentally denied bookings.
      • Forwarded URLs let a third party torpedo the booking using only the
        token in the URL.
    Token-based GET now renders an "Are you sure?" confirmation page that
    POSTs back to this same endpoint to actually deny. UI/auth path (no
    token) still works normally — they're already logged in, no scanner
    can spoof their session.
    """
    token = request.query_params.get('token')
    artist_id = request.query_params.get('artist_id')
    if not artist_id:
        raise HTTPException(400, "artist_id required")
    artist_id = int(artist_id)

    # GET + token → confirmation page (only POST mutates from email link).
    if request.method == "GET" and token:
        from fastapi.responses import HTMLResponse
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Deny booking · GigsFill</title>
<style>
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#0f1419; margin:0; padding:40px 20px; color:#e4e7eb; }}
  .card {{ max-width:480px; margin:60px auto; background:#1a1f2e;
         border:1px solid #2d3548; border-radius:12px; padding:32px 36px;
         text-align:center; }}
  h1 {{ font-size:1.5rem; margin:0 0 12px;
       background:linear-gradient(135deg,#7c6bff,#06b6d4);
       background-clip:text; -webkit-background-clip:text;
       -webkit-text-fill-color:transparent; }}
  p {{ color:#9ca3af; line-height:1.55; }}
  .btn {{ display:inline-block; padding:12px 28px; border:none;
        border-radius:6px; font-size:0.95rem; font-weight:600;
        cursor:pointer; text-decoration:none; margin:6px 4px; }}
  .btn-deny {{ background:#dc2626; color:#fff; }}
  .btn-back {{ background:#374151; color:#fff; }}
</style></head><body>
<div class="card">
  <div style="font-size:2.6rem;margin-bottom:8px;">⏸️</div>
  <h1>Deny this booking?</h1>
  <p>Clicking <strong>Deny</strong> will reject the artist's request, reopen
  the gig, and email them the decision.</p>
  <form method="POST" action="/api/gigs/{gig_id}/deny-booking?token={token}&artist_id={artist_id}"
        style="margin-top:18px;">
    <button class="btn btn-deny" type="submit">Yes, deny</button>
    <a class="btn btn-back" href="/app/venue-create-gigs.html">Cancel</a>
  </form>
</div></body></html>"""
        return HTMLResponse(content=html)

    _ensure_approval_columns(db)

    gig = db.execute(
        text("SELECT id, venue_id, status, date, start_time, end_time, pay, title, artist_id, approval_token FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")

    if token:
        # Audit fix (May 2026 part 5): prefer per-(gig, artist) token lookup,
        # fall back to legacy single-column token (for in-flight pre-deploy emails).
        # Jul 2026 audit (B-C2): enforce expires_at — same treatment as approve.
        _per_artist = None
        try:
            _per_artist = db.execute(
                text("SELECT artist_id, expires_at FROM pending_approval_tokens WHERE token = :tok AND gig_id = :gid"),
                {"tok": token, "gid": gig_id}
            ).mappings().first()
        except Exception:
            _per_artist = None
        _token_matches_per_artist = False
        if _per_artist and int(_per_artist["artist_id"]) == artist_id:
            _exp = _per_artist.get("expires_at")
            if _exp:
                try:
                    from datetime import datetime as _dt
                    _exp_naive = str(_exp).split(".")[0].split("+")[0].replace("T", " ").strip()
                    _exp_dt = _dt.strptime(_exp_naive, "%Y-%m-%d %H:%M:%S")
                    if _exp_dt >= _dt.utcnow():
                        _token_matches_per_artist = True
                except Exception:
                    _token_matches_per_artist = True
            else:
                _token_matches_per_artist = True
        _token_matches_legacy = bool(gig["approval_token"] and token == gig["approval_token"])
        if not (_token_matches_per_artist or _token_matches_legacy):
            from fastapi.responses import HTMLResponse
            # Check if already denied (slot open, no artist) or approved (booked)
            slot_booked = db.execute(
                text("SELECT id FROM gig_slots WHERE gig_id = :gid AND artist_id = :aid AND status = 'booked'"),
                {"gid": gig_id, "aid": artist_id}
            ).first()
            if slot_booked or gig["status"] == "booked":
                return HTMLResponse(_styled_page('✅', '#22c55e', 'Already Approved', 'This booking was already approved. The artist has been notified.'))
            return HTMLResponse(_styled_page('❌', '#ef4444', 'Already Denied', 'This booking request was already denied. The artist has been notified.'))
    else:
        if not user:
            raise HTTPException(403, "Authentication required")
        # Audit fix (May 2026 part 2): use check_venue_access so entity_users
        # can deny from the in-UI path. Same fix as approve_booking above.
        from backend.utils import check_venue_access
        check_venue_access(db, gig["venue_id"], user.id)

    slot = db.execute(
        text("SELECT id, slot_number, start_time, end_time FROM gig_slots WHERE gig_id = :gid AND artist_id = :aid AND status = 'pending_venue_approval'"),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()

    slot_info_str = ""
    if slot:
        # Audit fix (May 2026): atomic-claim guard so a double-clicked email
        # link or refresh doesn't fire two "denied" emails + double waitlist
        # ping. Conditional UPDATE returns rowcount=0 on the second attempt.
        _claim = db.execute(
            text("UPDATE gig_slots SET artist_id = NULL, status = 'open', approval_requested_at = NULL "
                 "WHERE id = :sid AND status = 'pending_venue_approval'"),
            {"sid": slot["id"]}
        )
        if (_claim.rowcount or 0) == 0:
            db.commit()
            return {"ok": True, "already_denied": True}
        db.execute(
            text("UPDATE gigs SET status = 'open', artist_id = NULL, approval_token = NULL, approval_requested_at = NULL "
                 "WHERE id = :gid"),
            {"gid": gig_id}
        )
        # Audit fix (May 2026 part 5): also drop the per-artist token row.
        try:
            db.execute(
                text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid AND artist_id = :aid"),
                {"gid": gig_id, "aid": artist_id}
            )
        except Exception as _ptcleanup_err:
            # Audit fix (Jun 2026): was silently swallowed. If the row
            # stays, the approval link can be replayed. Log loudly so
            # the failure surfaces in journalctl instead of vanishing.
            logger.warning(
                f"[APPROVE] pending_approval_tokens cleanup failed for "
                f"gig={gig_id} artist={artist_id}: {_ptcleanup_err} — "
                f"approval link MAY be replayable until cleanup succeeds"
            )
        slot_info_str = f"Slot {slot['slot_number']}: {format_time_12hr(slot['start_time'])} – {format_time_12hr(slot['end_time'])}"
    elif gig["status"] == "pending_venue_approval" and gig["artist_id"] == artist_id:
        # Audit fix (May 2026): conditional UPDATE so a token-replay race
        # short-circuits the second pass.
        _gig_claim = db.execute(
            text("UPDATE gigs SET status = 'open', artist_id = NULL, approval_token = NULL, approval_requested_at = NULL "
                 "WHERE id = :gid AND status = 'pending_venue_approval'"),
            {"gid": gig_id}
        )
        if (_gig_claim.rowcount or 0) == 0:
            db.commit()
            return {"ok": True, "already_denied": True}
    else:
        return {"ok": False, "message": "No pending approval found for this artist"}

    db.commit()

    # Notify waitlist — gig is now open again, send sequential offer to next artist
    try:
        from backend.routes.waitlist import notify_waitlist, _has_active_waitlist
        db.expire_all()
        if _has_active_waitlist(db, gig_id):
            notify_waitlist(db, gig_id)
    except Exception as _wle:
        logger.error(f"[DENY_BOOKING] notify_waitlist error: {_wle}")

    names = db.execute(
        text("""
            SELECT g.date, g.start_time, g.end_time, g.pay, g.title,
                   g.venue_id, v.venue_name, a.name as artist_name
            FROM gigs g JOIN venues v ON g.venue_id=v.id JOIN artists a ON a.id=:aid
            WHERE g.id=:gid
        """),
        {"gid": gig_id, "aid": artist_id}
    ).mappings().first()
    if names:
        try:
            send_approval_decision_emails(db, dict(names), artist_id, approved=False, slot_info=slot_info_str)
        except Exception as e:
            logger.error(f"[DENY_BOOKING] email error: {e}")

    from fastapi.responses import HTMLResponse
    if token:
        return HTMLResponse('<html>\n<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Booking Denied — GigsFill</title>\n<style>\n  * { box-sizing: border-box; margin: 0; padding: 0; }\n  body { background: linear-gradient(135deg, #0f1419 0%, #1a1f2e 100%); min-height: 100vh;\n         font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif;\n         display: flex; align-items: center; justify-content: center; padding: 20px; }\n  .card { background: linear-gradient(135deg, #1a1f2e, #0f1419); border: 2px solid rgba(239,68,68,0.4);\n           border-radius: 16px; padding: 48px 40px; max-width: 420px; width: 100%;\n           text-align: center; box-shadow: 0 8px 32px rgba(239,68,68,0.15); }\n  .icon { font-size: 3rem; margin-bottom: 20px; }\n  h2 { color: #ef4444; font-size: 1.6rem; font-weight: 700; margin-bottom: 12px; }\n  p { color: #9ca3af; font-size: 1rem; line-height: 1.6; margin-bottom: 28px; }\n  a { display: inline-block; background: linear-gradient(135deg, #7c6bff, #06b6d4);\n       color: #fff; text-decoration: none; padding: 12px 28px; border-radius: 8px;\n       font-weight: 600; font-size: 0.95rem; transition: opacity 0.2s; }\n  a:hover { opacity: 0.85; }\n  .logo { color: #7c6bff; font-size: 1.1rem; font-weight: 700; margin-bottom: 32px; letter-spacing: 0.5px; }\n</style></head>\n<body><div class="card">\n  <div class="logo">🎵 GigsFill</div>\n  <div class="icon">❌</div>\n  <h2>Booking Denied</h2>\n  <p>The artist has been notified. The gig slot is now open again for other artists to book.</p>\n  <a href="/app/venue-create-gigs.html">View Calendar</a>\n</div></body></html>')
    return {"ok": True}


# CANCEL A SLOT BOOKING (ARTIST OR VENUE)
@router.delete("/api/gigs/{gig_id}/slots/{slot_id}/cancel")
def cancel_slot(
    gig_id: int,
    slot_id: int,
    data: dict,
    db=Depends(get_db),
    user=Depends(get_current_user)
):
    """Cancel a slot booking.

    Two modes:
      - default (remove_slot=False): reset the slot to 'open' so it can be rebooked.
      - remove_slot=True:           after cleanup + cancellation emails, DELETE the
        slot row from the gig and renumber the remaining slots so they stay
        contiguous (1, 2, 3, ...).

    Both modes run the same notification + email + flyer + transaction cleanup
    so the artist is properly notified either way.
    """
    cancelled_by = data.get("cancelled_by", "venue")
    cancellation_reason = data.get("cancellation_reason", "")
    remove_slot = bool(data.get("remove_slot", False))

    slot = db.execute(
        text("""
            SELECT gs.*, a.name as artist_name, a.user_id as artist_user_id
            FROM gig_slots gs
            LEFT JOIN artists a ON gs.artist_id = a.id
            WHERE gs.id = :sid AND gs.gig_id = :gid
        """),
        {"sid": slot_id, "gid": gig_id}
    ).mappings().first()

    if not slot:
        raise HTTPException(404, "Slot not found")

    gig = db.execute(
        text("""
            SELECT g.date, g.venue_id, v.venue_name, v.user_id as venue_user_id
            FROM gigs g JOIN venues v ON g.venue_id = v.id WHERE g.id = :gid
        """),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")

    # ── AUTHORIZATION ──
    # Caller must have access to either the venue or the slot's booked artist.
    # Mirrors cancel_gig's authz pattern (lines 1699-1718). Without this any
    # authenticated user can DELETE any slot booking, wipe transactions, fire
    # cancellation emails, and (with remove_slot=True) delete the slot row or
    # the entire gig.
    has_venue_access = db.execute(
        text("""
            SELECT 1 FROM venues v WHERE v.id = :vid AND (
                v.user_id = :uid
                OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid)
            )
        """),
        {"vid": gig["venue_id"], "uid": user.id}
    ).first()
    has_artist_access = False
    if slot.get("artist_id"):
        has_artist_access = bool(db.execute(
            text("""
                SELECT 1 FROM artists a WHERE a.id = :aid AND (
                    a.user_id = :uid
                    OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid)
                )
            """),
            {"aid": int(slot["artist_id"]), "uid": user.id}
        ).first())
    if not has_venue_access and not has_artist_access:
        raise HTTPException(403, "You don't have access to cancel this slot")

    # Force cancelled_by to match the caller's actual access. Without this a
    # venue user could mislabel a cancellation as artist-initiated (or vice
    # versa) and trigger the wrong email subject template + notification copy.
    if cancelled_by == "artist" and not has_artist_access:
        cancelled_by = "venue"
    elif cancelled_by == "venue" and not has_venue_access:
        cancelled_by = "artist"

    # Clean up transactions/contracts for this artist's slot.
    # Audit fix (May 2026 part 2): refuse if this artist's payout has
    # already moved money (paid / transferred / pending_transfer / etc.).
    # cleanup_gig_records would otherwise raw-DELETE the audit trail.
    # Caller must route through Admin → Payments refund/reverse first.
    if slot.get("artist_id"):
        from backend.services.gig_cleanup import assert_no_charged_transactions
        assert_no_charged_transactions(db, gig_id, int(slot["artist_id"]))
        # Audit fix (May 2026 part 7): mark in-flight gig_contracts rows as
        # 'cancelled' before cleanup_gig_records DELETEs them, so analytics
        # / contract-history queries can see what happened (history was
        # silently dropped without the explicit cancel-status transition).
        try:
            db.execute(
                text("""UPDATE gig_contracts SET status = 'cancelled'
                        WHERE gig_id = :gid AND artist_id = :aid
                          AND status IN ('pending','awaiting_venue_upload','artist_signed')"""),
                {"gid": gig_id, "aid": int(slot["artist_id"])}
            )
        except Exception:
            pass
        cleanup_gig_records(db, gig_id, int(slot["artist_id"]))

    # Reset slot to open — restore pay to gig's original listed pay (clear any override)
    db.execute(
        text("""UPDATE gig_slots
                SET artist_id = NULL, status = 'open', approval_requested_at = NULL,
                    pay = (SELECT g.pay FROM gigs g WHERE g.id = :gid)
                WHERE id = :sid"""),
        {"sid": slot_id, "gid": gig_id}
    )
    
    # Audit fix (Jul 2026 delete-audit): when the slot had no artist
    # (e.g. cancel-with-remove_slot on an already-open slot), the old
    # `slot.get("artist_id") or 0` fallback wrote `last_cancelled_
    # artist_id = 0` on the gig. The blast-cancellation code at
    # `fire_cancelled_gig_blast` reads that as a valid artist to exclude
    # (artist_id=0 doesn't exist so it just no-op'd, but the CASE-WHEN
    # branch on `artist_id = 0` matched no rows anyway). Now use NULL
    # so the CASE-WHEN is a real no-op and `last_cancelled_artist_id`
    # only records real artist cancellations.
    _cancel_aid = slot.get("artist_id")
    # Re-open parent gig if it was fully booked OR pending_contract/awaiting_venue_contract/pending_venue_approval
    db.execute(
        text("""UPDATE gigs SET status = 'open', radius_blast_token = NULL,
                    artist_id = CASE WHEN artist_id = :aid THEN NULL ELSE artist_id END,
                    contract_hold_artist_id = NULL, contract_hold_expires_at = NULL,
                    approval_token = NULL, approval_requested_at = NULL,
                    last_cancelled_artist_id = CASE WHEN :aid IS NOT NULL THEN :aid ELSE last_cancelled_artist_id END
                WHERE id = :gid AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')"""),
        {"gid": gig_id, "aid": _cancel_aid}
    )
    # For multi-slot gigs: gig.status stays 'open' so above WHERE won't match.
    # Clear contract_hold, radius_blast_token, and artist_id for the cancelling artist.
    db.execute(
        text("""UPDATE gigs SET
                    contract_hold_artist_id = NULL, contract_hold_expires_at = NULL,
                    radius_blast_token = NULL,
                    artist_id = CASE WHEN artist_id = :aid THEN NULL ELSE artist_id END
                WHERE id = :gid AND status = 'open'"""),
        {"gid": gig_id, "aid": _cancel_aid}
    )
    # Always record last_cancelled_artist_id so blast emails exclude the canceller
    if slot.get("artist_id"):
        _record_artist_cancellation(db, gig_id, slot["artist_id"])
    # Remove any stale waitlist_offered row for the cancelling artist
    # (they cannot be offered their own cancelled gig)
    if slot.get("artist_id"):
        db.execute(
            text("DELETE FROM waitlist_offered WHERE gig_id = :gid AND artist_id = :aid"),
            {"gid": gig_id, "aid": slot["artist_id"]}
        )
        db.execute(
            text("UPDATE gig_waitlist SET offer_declined = 1 WHERE gig_id = :gid AND artist_id = :aid"),
            {"gid": gig_id, "aid": slot["artist_id"]}
        )

    # Notifications via service
    if slot["artist_id"] and gig:
        slot_time = f"{format_time_12hr(slot['start_time'])} - {format_time_12hr(slot['end_time'])}"
        slot_info = f"Slot {slot['slot_number']} ({slot_time})"
        
        cancel_details = {
            "venue_name": gig["venue_name"],
            "artist_name": slot.get("artist_name", "Artist"),
            "date": gig["date"],  # notify uses raw date - format_email_date applied in send_cancellation_emails
            "artist_user_id": slot.get("artist_user_id"),
            "venue_user_id": gig.get("venue_user_id"),
        }
        notify_gig_cancelled(
            db, cancel_details, gig_id, gig["venue_id"], slot["artist_id"],
            cancelled_by=cancelled_by, cancellation_reason=cancellation_reason,
            slot_info=slot_info
        )

    db.commit()

    # FIX (May 2026): delete flyer ONLY if no bookings remain. Critical for
    # multi-slot gigs: cancelling one slot shouldn't wipe a flyer the venue
    # custom-designed with the other artists' info. When preserved, also
    # strip just this artist's logo from the canvas.
    _delete_flyer_if_no_bookings_remain(db, gig_id, cancelled_artist_id=slot.get("artist_id"))
    
    # Send cancellation emails
    logger.info(f"[CANCEL EMAIL] cancel_slot: slot_artist_id={slot.get('artist_id')}, gig_id={gig_id}")
    if slot["artist_id"] and gig:
        slot_time = f"{format_time_12hr(slot['start_time'])} - {format_time_12hr(slot['end_time'])}"
        email_details = {
            "id": gig_id,
            "artist_name": slot.get("artist_name", "Artist"),
            "venue_name": gig["venue_name"],
            "artist_id": slot["artist_id"],
            "venue_id": gig["venue_id"],
            "date": gig["date"],
            # Include slot times so send_cancellation_emails can compute the correct
            # waitlist offer deadline (defaults to midnight if omitted).
            "start_time": slot.get("start_time") or gig.get("start_time"),
            "end_time": slot.get("end_time") or gig.get("end_time"),
        }
        # Always send venue cancellation email — venue needs to know who cancelled.
        # The blast summary is separate and additional, not a replacement.
        send_cancellation_emails(db, email_details, cancellation_reason=cancellation_reason,
                                 skip_venue_email=False, cancelled_by=cancelled_by)
    else:
        logger.warning(f"[CANCEL EMAIL] skipped — slot artist_id={slot.get('artist_id')} gig={bool(gig)}")

    # Notify waitlisted artists synchronously so has_active_waitlist=1 before API returns.
    # Blast runs in background only if no waitlist.
    try:
        from backend.routes.waitlist import notify_waitlist, _has_active_waitlist
        db.expire_all()
        if _has_active_waitlist(db, gig_id):
            notify_waitlist(db, gig_id)
        else:
            _gid, _vid = gig_id, gig["venue_id"]
            import threading as _th
            def _blast():
                try:
                    from backend.db import SessionLocal as _SL
                    _db = _SL()
                    try: fire_cancelled_gig_blast(_db, _gid, _vid)
                    finally: _db.close()
                except Exception as _e:
                    logger.error(f"Slot cancel blast error: {_e}")
            _th.Thread(target=_blast, daemon=True).start()
    except Exception as e:
        logger.error(f"Slot cancel waitlist/blast error: {e}", exc_info=True)

    # remove_slot mode: after the cancel cleanup + emails have run, delete the
    # slot row entirely and renumber the remaining slots. The "keep open" mode
    # is the default behavior above (slot reset to status='open' for rebooking).
    if remove_slot:
        db.execute(
            text("DELETE FROM gig_slots WHERE id = :sid AND gig_id = :gid"),
            {"sid": slot_id, "gid": gig_id}
        )
        remaining = db.execute(
            text("SELECT id FROM gig_slots WHERE gig_id = :gid ORDER BY start_time ASC, id ASC"),
            {"gid": gig_id}
        ).fetchall()
        for i, row in enumerate(remaining, 1):
            db.execute(
                text("UPDATE gig_slots SET slot_number = :n WHERE id = :sid"),
                {"n": i, "sid": row[0]}
            )
        if not remaining:
            # All slots removed — the gig is empty, delete it completely.
            # BUG FIX (Jul 2026 audit): use delete_gig_completely instead of
            # cleanup_gig_records + raw DELETE FROM gigs. cleanup_gig_records
            # only removes contract-typed notification rows, leaving the
            # `gig_cancelled` row that notify_gig_cancelled just inserted at
            # line 5898 dangling — and the notifications FK to gigs has no
            # ON DELETE CASCADE, so DELETE FROM gigs raised IntegrityError
            # → 500 → rollback → email already fired but nothing persisted.
            # delete_gig_completely handles notifications, gig_messages,
            # gig_waitlist, waitlist_offered, artist_reviews,
            # gig_cancelled_artists, pending_approval_tokens, and the
            # recurring-group survivor cleanup in the right order.
            from backend.services.gig_cleanup import assert_no_charged_transactions, delete_gig_completely
            assert_no_charged_transactions(db, gig_id)
            delete_gig_completely(db, gig_id)
            logger.info(f"cancel_slot: deleted gig {gig_id} (last slot removed)")
        else:
            logger.info(f"cancel_slot: removed slot {slot_id} from gig {gig_id}; {len(remaining)} slots remain")
        db.commit()

    # Audit trail (Jul 2026 delete-audit) — cancel_slot touches money-
    # adjacent state (transactions, contracts). Log so a future incident
    # can reconstruct what was cancelled by whom.
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, user, "gig_slot_cancel",
            target_table="gig_slots", target_id=slot_id,
            metadata={
                "gig_id": int(gig_id),
                "artist_id": _cancel_aid,
                "remove_slot": bool(remove_slot),
            },
        )
    except Exception:
        pass

    return {"ok": True, "removed": remove_slot}


# DELETE A MULTI-SLOT GIG (cascade deletes slots)
@router.delete("/api/gigs/{gig_id}/with-slots")
async def delete_gig_with_slots(gig_id: int, request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    """Delete a multi-slot gig and all its slots, with cancel notifications for booked slots"""
    try:
        from backend.utils import check_venue_access
        gig_row = db.execute(
            text("SELECT venue_id FROM gigs WHERE id = :gid"),
            {"gid": gig_id}
        ).mappings().first()
        if not gig_row:
            raise HTTPException(404, "Gig not found")
        check_venue_access(db, gig_row["venue_id"], user.id)

        # Audit fix (May 2026 part 2): refuse to wipe charged/paid/transferred
        # transactions. Same guard delete_gig got earlier — was missing here
        # even though this is the venue UI's primary "Cancel Gig" target.
        # The bulk DELETE below would silently destroy the audit trail of
        # money already moved. Force the user through the refund flow.
        from backend.services.gig_cleanup import assert_no_charged_transactions
        assert_no_charged_transactions(db, gig_id)

        cancellation_reason = ""
        keep_open = False
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.body()
                if body:
                    import json as _json
                    body_data = _json.loads(body)
                    cancellation_reason = body_data.get("cancellation_reason", "")
                    keep_open = body_data.get("keep_open", False)
            except Exception:
                pass

        # Snapshot booked slots and gig info BEFORE deleting anything.
        # Audit fix (May 2026 part 8): include all in-flight states. Previously
        # only `status='booked'` was snapshotted — artists in pending_contract,
        # awaiting_venue_contract, or pending_venue_approval got NO cancellation
        # email, NO in-app notification, and NO transaction cleanup when venue
        # pressed "Cancel Gig". Silent contract abandonment + dangling money rows.
        try:
            booked_slots = db.execute(
                text("""
                    SELECT gs.slot_number, gs.start_time, gs.end_time, gs.artist_id,
                           a.name as artist_name, a.user_id as artist_user_id
                    FROM gig_slots gs
                    JOIN artists a ON a.id = gs.artist_id
                    WHERE gs.gig_id = :gid
                      AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                """),
                {"gid": gig_id}
            ).mappings().all()
            booked_slots = [dict(s) for s in booked_slots]
        except Exception:
            booked_slots = []

        # FIX (May 2026 audit #9): load gig info OUTSIDE try/except so a DB
        # failure here surfaces to the caller (500) BEFORE any data is mutated.
        # Previously the swallow meant we could proceed with `gig=None`, mutate
        # slots/transactions in the keep_open or delete path, then skip the
        # entire post-cleanup block guarded by `if gig:` — cancelled artists
        # got no email, no notification, no waitlist/blast.
        # Audit fix (May 2026 part 3): include start_time / end_time so the
        # cancellation email's "Time" field and the waitlist deadline math
        # don't fall back to None / midnight when slot rows lack times.
        gig = db.execute(
            text("""
                SELECT g.date, g.title, g.artist_type, g.venue_id,
                       g.start_time, g.end_time,
                       v.venue_name
                FROM gigs g JOIN venues v ON g.venue_id = v.id WHERE g.id = :gid
            """),
            {"gid": gig_id}
        ).mappings().first()
        if not gig:
            raise HTTPException(404, "Gig not found")
        gig = dict(gig)

        if keep_open:
            # Reset slots to open — keep gig on calendar
            try:
                db.execute(text("""UPDATE gig_slots SET status='open', artist_id=NULL,
                    pay=(SELECT g.pay FROM gigs g WHERE g.id=:gid) WHERE gig_id=:gid"""), {"gid": gig_id})
                db.execute(text("""UPDATE gigs SET status='open', artist_id=NULL,
                    contract_hold_artist_id=NULL, contract_hold_expires_at=NULL,
                    radius_blast_token=NULL WHERE id=:gid"""), {"gid": gig_id})
                db.commit()

                # FIX (May 2026): the cleanup that was missing from this endpoint.
                # delete_gig_with_slots is the venue UI's primary cancel path. Without
                # these, transactions persist (artist still sees gig in earnings),
                # flyer persists (custom art remains showing cancelled artist),
                # and last_cancelled_artist_id stays NULL (cancelled artist gets
                # blasted on the re-opened gig).

                # 1. Run cleanup_gig_records per cancelled artist. Audit fix #19
                #    (May 2026): the previous raw `DELETE FROM transactions
                #    WHERE gig_id = :gid` skipped payment_cancellations rows,
                #    contract PDF files on disk, gig_contracts rows, and
                #    contract-related notifications. cleanup_gig_records is
                #    the canonical helper that handles all of those AND fires
                #    _recompute_gig_fees on the parent venue_charge.
                try:
                    if booked_slots:
                        for _bs in booked_slots:
                            _aid_clean = _bs.get("artist_id")
                            if _aid_clean:
                                cleanup_gig_records(db, gig_id, int(_aid_clean))
                    else:
                        # No booked slots — clean any orphan transactions / contracts
                        # at the gig level (e.g., free_trial audit rows).
                        cleanup_gig_records(db, gig_id)
                    db.commit()
                except HTTPException:
                    # Audit fix (May 2026 part 5): re-raise authorization/charged-
                    # transaction guard errors so the caller sees the real failure
                    # instead of a half-cancelled gig (slots open, transactions stuck).
                    raise
                except Exception as _txe:
                    # Audit fix (May 2026 part 5): log with stack trace so FK
                    # violations and table-missing errors surface in journalctl
                    # rather than vanishing into a generic warning line.
                    logger.warning(f"[CANCEL_WITH_SLOTS] cleanup_gig_records failed for gig {gig_id}: {_txe}", exc_info=True)

                # 2. Set last_cancelled_artist_id from the booked slots so the
                #    subsequent blast excludes them. For single-slot, this is
                #    the one cancelled artist. For multi-slot with all slots
                #    cancelled, take the first (best-effort exclusion).
                try:
                    # Audit fix (May 2026 part 9): record EVERY cancelled artist on
                    # this gig, not just the first. Multi-slot venue cancels affect
                    # every booked artist; the legacy single-int column only tracked
                    # one, so blasts re-included the others. Helper writes to the
                    # new gig_cancelled_artists table for set-based exclusion.
                    if booked_slots:
                        for _bs in booked_slots:
                            _aid = _bs.get("artist_id")
                            if _aid:
                                _record_artist_cancellation(db, gig_id, int(_aid))
                        db.commit()
                except Exception as _lce:
                    logger.warning(f"[CANCEL_WITH_SLOTS] last_cancelled_artist_id set failed for gig {gig_id}: {_lce}")

                # 3. Delete the gig-specific flyer if no bookings remain.
                #    Multi-slot gigs with surviving bookings keep their flyer.
                #    FIX (May 2026): when preserved, strip ALL the cancelled artists'
                #    logos. delete_gig_with_slots cancels every artist in `booked_slots`
                #    in one shot (unlike cancel_slot which is per-slot).
                _delete_flyer_if_no_bookings_remain(db, gig_id)
                try:
                    if booked_slots:
                        for _bs in booked_slots:
                            _aid_strip = _bs.get("artist_id")
                            if _aid_strip:
                                _remove_artist_logo_from_flyer(db, gig_id, int(_aid_strip))
                except Exception as _le:
                    logger.warning(f"[CANCEL_WITH_SLOTS] flyer logo strip loop failed for gig {gig_id}: {_le}")

                # Hold cleanup (Jun 2026): venue-UI "Cancel Gig" (keep_open
                # branch) needs to wind down the hold cycle too, or the gig
                # stays hidden from public search and a stale offer email
                # link can still book a slot that was just reset.
                cleanup_hold_records(db, gig_id, delete_rows=False)
                # Audit fix (Jun 2026): drop pending_approval_tokens for
                # the cancelled gig. Mirrors cancel_gig's full-delete
                # branch (line 2990) and the keep_open patch above.
                # Without this, an artist whose slot was reopened via
                # the venue-UI Cancel Gig button could still click a
                # stale email-approval link.
                try:
                    db.execute(
                        text("DELETE FROM pending_approval_tokens WHERE gig_id = :gid"),
                        {"gid": gig_id}
                    )
                except Exception as _ptke:
                    logger.warning(f"[CANCEL_WITH_SLOTS] pending_approval_tokens cleanup failed for gig {gig_id}: {_ptke}")

            except HTTPException:
                # Audit fix (May 2026 part 5): bubble up HTTPException raised by
                # cleanup helpers so the venue gets the real reason (charged
                # transaction, authz, etc) instead of a silent half-cancel.
                db.rollback()
                raise
            except Exception as e:
                # Audit fix (May 2026 part 5): include stack trace so the error
                # is debuggable from journalctl. Rollback so the partial state
                # doesn't leak into subsequent statements on this connection.
                logger.error(f"keep_open reset failed: {e}", exc_info=True)
                db.rollback()
        else:
            # Audit fix (Jul 2026 delete-audit): consolidate the inline
            # cleanup below into a single call to the canonical
            # `delete_gig_completely` helper. Prior inline version
            # missed `gig_cancelled_artists`, `notification_sent_log`,
            # `venue_reviews.gig_id` NULL-out, recurring-series survivor
            # cleanup, and `_recompute_gig_fees` — all of which the
            # helper handles. Two small extras stay inline because they
            # aren't in the helper: gig-scoped flyers + email log +
            # public_activity + payment_cancellations.
            for _s in [
                "DELETE FROM gig_email_log WHERE gig_id = :gid",
                "DELETE FROM public_activity WHERE gig_id = :gid",
                "DELETE FROM flyers WHERE gig_id = :gid AND is_template = 0",
                "DELETE FROM payment_cancellations WHERE transaction_id IN (SELECT id FROM transactions WHERE gig_id=:gid)",
            ]:
                try:
                    db.execute(text(_s), {"gid": gig_id})
                except Exception:
                    pass
            try:
                from backend.services.gig_cleanup import delete_gig_completely
                delete_gig_completely(db, gig_id)
            except Exception as e:
                logger.error(f"Core delete failed for gig {gig_id}: {e}")
                db.rollback()
                raise HTTPException(500, f"Delete failed: {e}")

        # Send notifications (best-effort — never fail the response)
        # BUG FIX (Jul 2026 audit): pass gig_id=None in the delete branch
        # (keep_open=False). notifications.gig_id FKs to gigs(id) with no
        # ON DELETE CASCADE, and the gig row was DELETEd at line 6241 above.
        # Without this fix, every create_notification call raised
        # IntegrityError caught by the outer try/except → every in-app
        # cancellation notification silently dropped. The cancellation email
        # (which doesn't touch notifications) still fired, so the artist got
        # an email but no Activity Center row.
        _notif_gig_id = gig_id if keep_open else None
        if gig:
            event_label = gig.get("artist_type") or gig.get("title") or "Event"
            for s in booked_slots:
                try:
                    if s.get("artist_user_id"):
                        st = s.get("start_time") or ""
                        et = s.get("end_time") or ""
                        slot_time = f"{format_time_12hr(st)} - {format_time_12hr(et)}" if st else ""
                        slot_info = f"Slot {s.get('slot_number','')} ({slot_time}) for {event_label}" if slot_time else event_label
                        reason_suffix = f" - Reason: {cancellation_reason}" if cancellation_reason else ""
                        create_notification(
                            db, s["artist_user_id"], "gig_cancelled", "Gig Cancelled",
                            f"Gig at {gig['venue_name']} on {gig['date']} has been cancelled. {slot_info}{reason_suffix}",
                            gig_id=_notif_gig_id, venue_id=gig["venue_id"], artist_id=s["artist_id"]
                        )
                except Exception as e:
                    logger.warning(f"Artist notification failed for slot: {e}")

            try:
                from backend.utils import get_all_entity_users
                venue_users = get_all_entity_users(db, "venue", gig["venue_id"])
                reason = cancellation_reason or "No reason provided"
                venue_msg = f"{gig['venue_name']} has cancelled the Gig on {gig['date']}. Reason: {reason}"
                for vu in venue_users:
                    if vu.get("user_id"):
                        create_notification(db, vu["user_id"], "gig_cancelled", "Gig Cancelled",
                            venue_msg, gig_id=_notif_gig_id, venue_id=gig["venue_id"])
            except Exception as e:
                logger.warning(f"Venue notification failed: {e}")

            # Cancellation emails
            for s in booked_slots:
                try:
                    if s.get("artist_id"):
                        email_details = {
                            "id": gig_id,
                            "artist_name": s.get("artist_name", "Artist"),
                            "venue_name": gig["venue_name"],
                            "artist_id": s["artist_id"],
                            "venue_id": gig["venue_id"],
                            "date": gig["date"],
                            # Use the cancelled slot's times so email shows the right slot
                            "start_time": s.get("start_time") or gig.get("start_time"),
                            "end_time":   s.get("end_time")   or gig.get("end_time"),
                        }
                        send_cancellation_emails(db, email_details, cancellation_reason=cancellation_reason, cancelled_by="venue")
                except Exception as e:
                    logger.warning(f"Cancellation email failed: {e}")

            # Blast / waitlist for keep_open
            if keep_open:
                try:
                    from backend.routes.waitlist import notify_waitlist, _has_active_waitlist
                    if _has_active_waitlist(db, gig_id):
                        notify_waitlist(db, gig_id)
                    else:
                        fire_cancelled_gig_blast(db, gig_id, gig["venue_id"])
                except Exception as e:
                    logger.warning(f"Waitlist/blast error (keep_open): {e}")

        # Audit trail — this is the venue UI's primary Cancel Gig
        # button. Log so incident review can trace who cancelled what.
        # (Jul 2026 delete-audit.)
        try:
            from backend.utils import log_admin_action
            log_admin_action(
                db, user, "gig_delete_with_slots",
                target_table="gigs", target_id=gig_id,
                metadata={
                    "keep_open": bool(keep_open),
                    "booked_artist_ids": [int(s["artist_id"]) for s in (booked_slots or []) if s.get("artist_id")],
                    "cancellation_reason": (cancellation_reason or "")[:200],
                },
            )
        except Exception:
            pass

        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_gig_with_slots unexpected error gig {gig_id}: {e}", exc_info=True)
        raise HTTPException(500, f"Delete failed: {e}")



def _get_effective_pay_for_artist(db, gig_id: int, venue_id: int, artist_id: int, base_pay: float) -> float:
    """Return max(base_pay, artist's pay override) for a given artist at a venue.

    The override only applies for `status='approved'` preferred-artist
    rows — a pending/denied/revoked row still carries the historical
    override columns but should NOT influence pay. Without the guard,
    a venue who set $20 then revoked the relationship would still send
    blast emails showing $20.

    KNOWN GAP (audit Jun 2026): this helper takes a flat `base_pay` and
    has NO slot/door-deal awareness. All three callers (blast emails at
    lines ~6170/6268/6441) pass `gig.pay` which is the GIG-level pay
    (slot-1 pay for multi-slot gigs). For door-deal slots the override
    is gated by `gig_slots.apply_override` which we can't see at the
    gig level. Net: if a venue blasts a gig whose first slot is a door
    deal, the email will quote the gig-level guarantee dollars without
    "+ Y% door" terms. Acceptable for now — blasts are usually flat-pay
    gigs and the slot terms are visible when the artist opens the gig
    modal. Proper fix: make blast emails slot-aware.
    """
    try:
        row = db.execute(
            text("""SELECT COALESCE(pay_dollars_override,0) + COALESCE(pay_cents_override,0)/100.0 as override_pay
                    FROM preferred_artists
                    WHERE venue_id=:vid AND artist_id=:aid AND status='approved'"""),
            {"vid": venue_id, "aid": artist_id}
        ).mappings().first()
        if row and row["override_pay"] and float(row["override_pay"]) > base_pay:
            return float(row["override_pay"])
    except Exception:
        pass
    return base_pay


def fire_cancelled_gig_blast(db, gig_id: int, venue_id: int, skip_waitlist_check: bool = False,
                              exclude_artist_id: int = None):
    """Immediately email all preferred artists matching gig type if gig is within 7 days and venue has cancelled_blast enabled.
    exclude_artist_id: artist who originally had the gig (canceller) — excluded from blast."""
    import smtplib
    import secrets
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    logger.info(f"[BLAST] fire_cancelled_gig_blast called: gig_id={gig_id}, venue_id={venue_id}, skip_waitlist_check={skip_waitlist_check}")
    # Always read last_cancelled_artist_id from the gig — excludes canceller from ALL blast paths
    if exclude_artist_id is None:
        try:
            _excl = db.execute(
                text("SELECT last_cancelled_artist_id FROM gigs WHERE id = :gid"),
                {"gid": gig_id}
            ).scalar()
            if _excl:
                exclude_artist_id = int(_excl)
                logger.info(f"[BLAST] auto-excluding last_cancelled_artist_id={exclude_artist_id}")
        except Exception:
            pass

    # Safety guard: never blast when there are waitlisted artists to contact first
    # (skip_waitlist_check=True when called from _blast_waitlist_and_nearby which already handled waitlist)
    if not skip_waitlist_check:
        try:
            from backend.routes.waitlist import _has_active_waitlist
            if _has_active_waitlist(db, gig_id):
                logger.info(f"[BLAST] Gig {gig_id} has active waitlist — skipping blast, notifying waitlist instead")
                try:
                    from backend.routes.waitlist import notify_waitlist
                    notify_waitlist(db, gig_id)
                except Exception as _nwe:
                    logger.error(f"[BLAST] notify_waitlist fallback failed: {_nwe}")
                return
        except Exception as _wce:
            logger.warning(f"[BLAST] waitlist check failed: {_wce}")

    # Jul 2026: cancellation blast is now sourced from per-window cancel_notify_*
    # fields on the open_gig_4w/2w/1w/36h rows. Rule: find the SMALLEST window
    # whose remaining-days threshold still covers the gig AND which has either
    # cancel_notify_preferred=1 or cancel_notify_all_enabled=1. That row's
    # cancel_notify_* + waive_frequency drive this send. Each cancellation
    # fires exactly one blast.

    # Load gig date to compute days_until in platform timezone
    _gig_date_raw = db.execute(
        text("SELECT date FROM gigs WHERE id = :gid AND status = 'open'"),
        {"gid": gig_id}
    ).scalar()
    if not _gig_date_raw:
        logger.warning(f"[BLAST] Gig {gig_id} not found or not 'open' — skipping blast")
        return
    try:
        gig_date = datetime.strptime(_gig_date_raw, "%Y-%m-%d").date()
        # Jul 2026 audit (B-H1): use the VENUE's timezone, not the
        # platform's. A Hawaii venue near local midnight can otherwise
        # land in the wrong owning window (36h vs 1w) → wrong
        # cancel_notify_preferred/all/waive_frequency behaviour and
        # possibly wrong recipient set.
        try:
            import pytz as _blast_pytz
            from backend.utils import get_venue_timezone_str
            _blast_tz_str = get_venue_timezone_str(db, int(venue_id))
            _today_local = datetime.now(_blast_pytz.timezone(_blast_tz_str)).date()
        except Exception:
            from datetime import date as _local_date
            _today_local = _local_date.today()
        days_until = (gig_date - _today_local).days
    except Exception as e:
        logger.error(f"[BLAST] Date parse error: {e}")
        return

    if days_until < 0:
        logger.info(f"[BLAST] Gig {gig_id} already past ({days_until}d) — skipping")
        return

    # Windows smallest→largest so first match is the tightest owning window.
    # (36h ≈ 1.5 days as a float boundary.)
    _WINDOWS = [('open_gig_36h', 1.5), ('open_gig_1w', 7), ('open_gig_2w', 14), ('open_gig_4w', 28)]

    _cancel_rows = db.execute(text("""
        SELECT notification_key,
               COALESCE(cancel_notify_preferred, 0)   as cnp,
               COALESCE(cancel_notify_all_enabled, 0) as cnae,
               COALESCE(cancel_notify_all_radius, 20) as cnar,
               COALESCE(waive_frequency, 1)           as wf
        FROM venue_email_notifications
        WHERE venue_id = :vid
          AND notification_key IN ('open_gig_36h','open_gig_1w','open_gig_2w','open_gig_4w')
    """), {"vid": venue_id}).mappings().all()
    _row_by_key = {r["notification_key"]: r for r in _cancel_rows}

    # Per-key defaults for venues that never saved the row (mirrors
    # NOTIFICATION_DEFAULTS in routes/venue_emails.py).
    _DEFAULT_CANCEL = {
        'open_gig_4w':  {'cnp': 0, 'cnae': 0, 'cnar': 20, 'wf': 0},
        'open_gig_2w':  {'cnp': 0, 'cnae': 0, 'cnar': 20, 'wf': 0},
        'open_gig_1w':  {'cnp': 1, 'cnae': 0, 'cnar': 20, 'wf': 1},
        'open_gig_36h': {'cnp': 1, 'cnae': 1, 'cnar': 20, 'wf': 1},
    }

    _owning = None
    for _wkey, _wdays in _WINDOWS:
        if days_until > _wdays:
            continue
        _r = _row_by_key.get(_wkey) or _DEFAULT_CANCEL[_wkey]
        if _r.get("cnp") or _r.get("cnae"):
            _owning = (_wkey, _r)
            break

    if not _owning:
        logger.info(f"[BLAST] Gig {gig_id} at {days_until}d — no covering window has cancel_notify_* enabled, skipping")
        return

    _owning_key, _owning_row = _owning
    cancel_pref    = bool(_owning_row.get("cnp"))
    blast_all_en   = bool(_owning_row.get("cnae"))
    blast_all_mi   = int(_owning_row.get("cnar") or 20)
    waive_freq_own = bool(_owning_row.get("wf", 1))
    logger.info(f"[BLAST] Gig {gig_id} at {days_until}d → owning='{_owning_key}' pref={cancel_pref} all={blast_all_en}@{blast_all_mi}mi waive={waive_freq_own}")

    # Load gig details (venue name / city / state used downstream in template)
    gig = db.execute(text("""
        SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.notes, g.artist_type,
               g.title, g.band_formats, g.styles, g.status,
               v.venue_name, v.city, v.state
        FROM gigs g JOIN venues v ON v.id = g.venue_id
        WHERE g.id = :gid AND g.status = 'open'
    """), {"gid": gig_id}).mappings().first()
    if not gig:
        logger.warning(f"[BLAST] Gig {gig_id} not found or not 'open' — skipping blast")
        return

    logger.info(f"[BLAST] Gig found: date={gig['date']}, artist_type={gig['artist_type']}, venue={gig['venue_name']}")

    # Stamp blast token so calendar updates immediately. frequency_exempt is
    # gated on the owning window's waive_frequency setting.
    blast_token = secrets.token_urlsafe(32)
    if waive_freq_own:
        db.execute(
            text("UPDATE gigs SET radius_blast_token = :token, frequency_exempt = 1 WHERE id = :gid"),
            {"token": blast_token, "gid": gig_id}
        )
    else:
        db.execute(
            text("UPDATE gigs SET radius_blast_token = :token WHERE id = :gid"),
            {"token": blast_token, "gid": gig_id}
        )
    db.commit()
    logger.info(f"[BLAST] radius_blast_token{' + frequency_exempt' if waive_freq_own else ''} set for gig {gig_id}")

    # Load SMTP settings — emails are best-effort after token is stamped
    smtp_row = db.execute(text("""
        SELECT setting_key, setting_value FROM platform_settings
        WHERE setting_key LIKE '%smtp%' OR setting_key LIKE '%email%'
    """)).mappings().all()
    smtp = {r["setting_key"]: r["setting_value"] for r in smtp_row}
    logger.info(f"[BLAST] SMTP keys found: {list(smtp.keys())}")

    smtp_user = smtp.get("platform_email") or smtp.get("smtp_email") or ""
    smtp_pass = smtp.get("platform_email_password") or smtp.get("smtp_password") or ""
    logger.info(f"[BLAST] smtp_user={'SET' if smtp_user else 'MISSING'}, smtp_pass={'SET' if smtp_pass else 'MISSING'}")
    if not smtp_user or not smtp_pass:
        logger.info(f"[BLAST] SMTP not configured — amber is set, emails skipped")
        return

    smtp_server = smtp.get("platform_smtp_server") or smtp.get("smtp_server") or "smtp.gmail.com"
    smtp_port   = int(smtp.get("platform_smtp_port") or smtp.get("smtp_port") or 587)
    logger.info(f"[BLAST] SMTP server={smtp_server}:{smtp_port}")
    _ngb_from_name = smtp.get("platform_email_from_name") or ""
    from email.utils import formataddr as _ngb_formataddr
    smtp_from = _ngb_formataddr((_ngb_from_name, smtp_user)) if _ngb_from_name else smtp_user

    # Load template — try both column names for DB compatibility
    tpl = db.execute(text(
        "SELECT subject, body FROM email_templates WHERE template_key = 'cancelled_gig_preferred_blast' LIMIT 1"
    )).mappings().first()
    if not tpl:
        tpl = db.execute(text(
            "SELECT subject, body FROM email_templates WHERE notification_type = 'cancelled_gig_preferred_blast' LIMIT 1"
        )).mappings().first()
    if not tpl:
        logger.error(f"[BLAST] Template 'cancelled_gig_preferred_blast' not found in DB — run: python3 backend/email_templates.py")
        return

    logger.info(f"[BLAST] Template loaded: subject={tpl['subject'][:60]}")

    # Fetch venue detail vars for template
    _vd = db.execute(text("""
        SELECT venue_size, address_line_1, address_line_2, city, state, postal_code,
               has_stage, stage_width_ft, stage_depth_ft, setup_location_description,
               has_sound_equipment, sound_equipment_description, has_sound_engineer, sound_engineer_details,
               has_lighting, lighting_description, arrival_time_type, arrival_no_earlier_than_hour,
               arrival_no_earlier_than_period, bar_tab_details, food_tab_details
        FROM venues WHERE id = :vid
    """), {"vid": venue_id}).mappings().first() or {}

    # Fetch venue detail vars using shared helper
    from backend.services.email_dispatch import _fetch_venue_detail_vars, format_email_date
    _venue_vars = _fetch_venue_detail_vars(db, venue_id, gig_notes=gig.get("notes"))
    _venue_address      = _venue_vars.get('venue_address', 'Not provided')
    _venue_address_link = _venue_vars.get('venue_address_link', _venue_address)
    _venue_capacity     = _venue_vars.get('venue_capacity', 'Not specified')
    _arrival_info       = _venue_vars.get('arrival_info', 'Flexible')
    _stage_info         = _venue_vars.get('stage_info', 'Not specified')
    _sound_info         = _venue_vars.get('sound_info', 'Not provided')
    _engineer_info      = _venue_vars.get('engineer_info', 'Not provided')
    _lighting_info      = _venue_vars.get('lighting_info', 'Not provided')
    _bar_tab            = _venue_vars.get('bar_tab', 'None')
    _food_tab           = _venue_vars.get('food_tab', 'None')


    # ── Build per-slot rows for multi-slot gigs ──────────────────────────────
    def _build_slots_html(slots, gig_pay, gig_artist_type, gig_band_formats, gig_styles, artist_override_pay=None):
        """Return HTML rows for each slot (or single-gig fallback).
        artist_override_pay: if set, use max(slot_pay, artist_override_pay) for display."""
        ROW = '<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">{label}</td><td style="padding:6px 0;font-size:14px;color:{color};font-weight:{weight};">{value}</td></tr>'
        SEP = '<tr><td colspan="2" style="padding:6px 0;border-top:1px solid #e5e7eb;"></td></tr>'
        HDR = '<tr><td colspan="2" style="padding:8px 0 4px 0;font-size:13px;font-weight:700;color:#374151;">Slot {num}</td></tr>'
        html = ""
        for i, s in enumerate(slots):
            if i > 0:
                html += SEP
            if len(slots) > 1:
                num = s.get("slot_number") or (i + 1)
                html += HDR.format(num=num)
            t_start = format_time_12hr(s.get("start_time") or "")
            t_end   = format_time_12hr(s.get("end_time") or "")
            time_str = f"{t_start} – {t_end}" if t_end else t_start
            base_pay = float(s.get("pay") or gig_pay or 0)
            if artist_override_pay is not None:
                pay_val = str(max(base_pay, artist_override_pay))
                # Format nicely: remove trailing .0 if whole number
                try:
                    pf = float(pay_val)
                    pay_val = f"{pf:,.2f}"
                except Exception:
                    pass
            else:
                pay_val = s.get("pay") or gig_pay or "0"
            atype    = s.get("artist_type") or gig_artist_type or ""
            lineup   = ", ".join(x.strip() for x in (s.get("band_formats") or gig_band_formats or "").split(",") if x.strip())
            styles   = ", ".join(x.strip() for x in (s.get("styles") or gig_styles or "").split(",") if x.strip())
            html += ROW.format(label="Time",  color="#111827", weight="500", value=time_str)
            html += ROW.format(label="Pay",   color="#059669", weight="600", value=f"${pay_val}")
            html += ROW.format(label="Type",  color="#111827", weight="500", value=atype)
            if lineup:
                html += ROW.format(label="Lineup", color="#111827", weight="500", value=lineup)
            if styles:
                html += ROW.format(label="Styles", color="#111827", weight="500", value=styles)
        return html

    _slots = db.execute(text("""
        SELECT start_time, end_time, pay, artist_type, band_formats, styles, status, slot_number
        FROM gig_slots WHERE gig_id = :gid ORDER BY start_time
    """), {"gid": gig_id}).mappings().all()

    # Only show OPEN slots in blast email — booked slots are not available
    _slot_dicts = [dict(s) for s in _slots if s.get("status") == "open"] if _slots else []
    # Fallback to all slots if none are open (shouldn't happen but safety net)
    if not _slot_dicts and _slots:
        _slot_dicts = [dict(s) for s in _slots]
    _gig_pay_base = float(gig.get("pay") or 0)
    _bf_base = ", ".join(x.strip() for x in (gig.get("band_formats") or "").split(",") if x.strip())
    _st_base = ", ".join(x.strip() for x in (gig.get("styles") or "").split(",") if x.strip())
    _end_base = (" – " + format_time_12hr(gig.get("end_time"))) if gig.get("end_time") else ""

    def _build_artist_slots_html(artist_id_for_html):
        """Build slots_html for a specific artist, applying their pay override."""
        effective = _get_effective_pay_for_artist(db, gig_id, venue_id, artist_id_for_html, _gig_pay_base)
        override = effective if effective > _gig_pay_base else None
        if _slot_dicts:
            return _build_slots_html(
                _slot_dicts,
                gig.get("pay"), gig.get("artist_type"),
                gig.get("band_formats"), gig.get("styles"),
                artist_override_pay=override
            )
        else:
            pay_display = f"{effective:,.2f}"
            html  = f'<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">Time</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{format_time_12hr(gig.get("start_time"))}{_end_base}</td></tr>'
            html += f'<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Pay</td><td style="padding:6px 0;font-size:14px;color:#059669;font-weight:600;">${pay_display}</td></tr>'
            html += f'<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Type</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{gig.get("artist_type") or ""}</td></tr>'
            if _bf_base:
                html += f'<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Lineup</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{_bf_base}</td></tr>'
            if _st_base:
                html += f'<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Styles</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{_st_base}</td></tr>'
            return html


    # Get preferred artists — exclude banned and waitlist-declined/timed-out
    # FIX (May 2026): also exclude artists with a blackout date covering this gig.
    artists = db.execute(text("""
        SELECT a.id, a.name, a.artist_type, u.email, u.id as user_id,
               a.band_formats, a.styles,
               COALESCE(a.has_own_equipment, 0) as has_own_equipment
        FROM preferred_artists pa
        JOIN artists a ON a.id = pa.artist_id AND a.deleted_at IS NULL
        JOIN users u ON u.id = a.user_id
        WHERE pa.venue_id = :vid AND pa.status = 'approved'
          AND a.id NOT IN (
              SELECT artist_id FROM venue_artist_bans WHERE venue_id = :vid
          )
          AND a.id NOT IN (
              SELECT artist_id FROM gig_waitlist
              WHERE gig_id = :gid AND offer_declined = 1
          )
          AND a.id NOT IN (
              -- Audit fix (May 2026 part 6): include awaiting_venue_contract +
              -- pending_venue_approval so an artist who already signed (or is
              -- pending venue approval) on one slot isn't blasted as a candidate
              -- for another slot on the same gig.
              SELECT artist_id FROM gig_slots
              WHERE gig_id = :gid
                AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                AND artist_id IS NOT NULL
          )
          AND (:excl_aid IS NULL OR a.id != :excl_aid)
          AND a.id NOT IN (
              -- Audit fix (May 2026 part 9): exclude ALL artists who have ever
              -- cancelled a slot on this gig, not just the most recent (which
              -- is what gigs.last_cancelled_artist_id / the excl_aid bind above
              -- tracks). NOTE do not write bind-style tokens (colon + name)
              -- inside comments; SQLAlchemy text() treats them as real
              -- bindparams and injects an extra binding, causing runtime error.
              SELECT artist_id FROM gig_cancelled_artists WHERE gig_id = :gid
          )
          AND NOT EXISTS (
              SELECT 1 FROM artist_availability aa
              WHERE aa.artist_id = a.id
                AND date(:gdate) BETWEEN date(aa.blackout_start) AND date(aa.blackout_end)
          )
    """), {"vid": venue_id, "gig": gig_id, "gid": gig_id,
           "excl_aid": exclude_artist_id, "gdate": str(gig_date)[:10]}).mappings().all()

    logger.info(f"[BLAST] Found {len(artists)} approved preferred artists for venue {venue_id}")

    # Jul 2026: cancel_notify_preferred=0 on the owning window means the venue
    # wants the "blast to ALL in radius" side to fire (if enabled) WITHOUT
    # emailing preferred artists for this cancellation. Empty the list so the
    # per-artist loop below is a no-op; the blast_all branch downstream still runs.
    if not cancel_pref:
        logger.info(f"[BLAST] Owning window has cancel_notify_preferred=0 — skipping preferred send")
        artists = []

    def render(s, vars_):
        import re as _re
        def _block(m):
            k = m.group(1); inner = m.group(2)
            return inner if vars_.get(k) else ''
        s = _re.sub(r'\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}', _block, s, flags=_re.DOTALL)
        for k, v in vars_.items():
            s = s.replace(f"{{{{{k}}}}}", str(v or ""))
        return s

    # FIX (Jul 2026, revised Jul 1 2026): accepted-types must reflect
    # what the artist could actually BOOK. If any open slot has an
    # explicit artist_type, only those types are accepted (gig-level is
    # ignored — it may match a BOOKED slot but that slot isn't bookable).
    # If open slots are all NULL/empty (or no slot rows), fall back to
    # gig-level type.
    _accepted_types_blast = set()
    _open_slot_types_explicit = db.execute(
        text("""SELECT DISTINCT artist_type FROM gig_slots
                WHERE gig_id = :gid AND status = 'open'
                  AND artist_type IS NOT NULL AND TRIM(artist_type) != ''"""),
        {"gid": gig_id}
    ).fetchall()
    _explicit = [(_st[0] or "").strip().lower() for _st in _open_slot_types_explicit if _st[0]]
    if _explicit:
        _accepted_types_blast.update(_explicit)
        # Also include gig-level type if any open slot inherits (NULL/empty type).
        _has_inherit = db.execute(
            text("""SELECT 1 FROM gig_slots WHERE gig_id = :gid AND status = 'open'
                    AND (artist_type IS NULL OR TRIM(artist_type) = '') LIMIT 1"""),
            {"gid": gig_id}
        ).first()
        if _has_inherit and gig["artist_type"]:
            _accepted_types_blast.add(gig["artist_type"].strip().lower())
    else:
        if gig["artist_type"]:
            _accepted_types_blast.add(gig["artist_type"].strip().lower())

    # Jul 1 2026: pull each open slot's band_formats/styles/equipment
    # so we can require the artist to overlap on at least one open slot
    # they can actually book. Mirrors `_artist_matches_slot` semantics.
    _cb_open_slots = db.execute(text("""
        SELECT artist_type, band_formats, styles, requires_equipment
        FROM gig_slots WHERE gig_id = :gid AND status = 'open'
    """), {"gid": gig_id}).mappings().all()
    _CB_MC = {"open mic mc", "karaoke mc"}

    def _cb_csv_set(_s):
        if not _s:
            return set()
        return {t.strip().lower() for t in str(_s).replace(";", ",").split(",") if t.strip()}

    def _cb_slot_matches(_a_type, _a_fmts, _a_styles, _a_has_equip, _slot):
        _st = (_slot.get("artist_type") or "").strip().lower() or (gig["artist_type"] or "").strip().lower()
        if _a_type and _st and _st != _a_type.strip().lower():
            return False
        if _st not in _CB_MC:
            _sf = _cb_csv_set(_slot.get("band_formats")) or _cb_csv_set(gig.get("band_formats"))
            if _sf and not (_sf & _cb_csv_set(_a_fmts)):
                return False
            _ss = _cb_csv_set(_slot.get("styles")) or _cb_csv_set(gig.get("styles"))
            if _ss and not (_ss & _cb_csv_set(_a_styles)):
                return False
        _req = _slot.get("requires_equipment")
        if _req in (1, True, "1", "true", "True") and not _a_has_equip:
            return False
        return True

    # 2026-08-10: when the owning window has waive_frequency=OFF,
    # preferred artists currently inside their frequency window at this
    # venue CANNOT book this gig anyway (the freq gate would reject
    # them). Filtering them out here means they don't get an email they
    # can't act on. When waive_frequency=ON, everyone can book, so no
    # filter needed. Pre-load the venue default once; per-artist
    # override still checked in the loop.
    _venue_default_freq_days = 0
    if not waive_freq_own:
        _vfd_row = db.execute(
            text("SELECT COALESCE(artist_frequency_days, 0) as fd FROM venues WHERE id = :vid"),
            {"vid": venue_id}
        ).mappings().first()
        _venue_default_freq_days = int((_vfd_row or {}).get("fd") or 0)

    def _artist_under_freq_limit(artist_id_local):
        """Returns True when this artist has a booked slot at this
        venue within the freq-days window of the gig's date, so they
        should be filtered out when waive_frequency is OFF."""
        # Per-artist override wins over venue default.
        _fd_row = db.execute(
            text("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) as fd
                    FROM preferred_artists pa
                    JOIN venues v ON v.id = pa.venue_id
                    WHERE pa.venue_id = :vid AND pa.artist_id = :aid"""),
            {"vid": venue_id, "aid": artist_id_local}
        ).mappings().first()
        _fd = int((_fd_row or {}).get("fd") or _venue_default_freq_days or 0)
        if _fd <= 0:
            return False
        _close = db.execute(text("""
            SELECT g2.date FROM gigs g2
            JOIN gig_slots gs2 ON gs2.gig_id = g2.id AND gs2.artist_id = :aid
            WHERE g2.venue_id = :vid AND g2.id != :gid
              AND gs2.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
            ORDER BY ABS(JULIANDAY(g2.date) - JULIANDAY(:d)) LIMIT 1
        """), {"vid": venue_id, "aid": artist_id_local, "gid": gig_id,
               "d": str(gig["date"])[:10]}).mappings().first()
        if not _close:
            return False
        try:
            from datetime import datetime as _dt_fq
            _d1 = _dt_fq.strptime(str(gig["date"])[:10], "%Y-%m-%d")
            _d2 = _dt_fq.strptime(str(_close["date"])[:10], "%Y-%m-%d")
            return abs((_d1 - _d2).days) <= _fd
        except Exception:
            return False

    sent_count = 0
    for artist in artists:
        # Match artist type — accepted types include gig-level + any
        # open slot type. Skip if artist has no type set, OR if their
        # type isn't in the accepted set.
        if not artist["artist_type"]:
            logger.info(f"[BLAST] Skipping {artist['name']}: artist has no type set")
            continue
        if _accepted_types_blast and artist["artist_type"].strip().lower() not in _accepted_types_blast:
            logger.info(f"[BLAST] Skipping {artist['name']}: type mismatch (accepted={_accepted_types_blast}, artist={artist['artist_type']})")
            continue
        # Jul 1 2026: band_formats + styles + equipment overlap filter.
        if _cb_open_slots:
            if not any(_cb_slot_matches(artist["artist_type"], artist.get("band_formats"),
                                          artist.get("styles"),
                                          bool(artist.get("has_own_equipment")), s)
                        for s in _cb_open_slots):
                logger.info(f"[BLAST] Skipping {artist['name']}: no open slot matches band_formats/styles/equipment")
                continue
        else:
            _sf = _cb_csv_set(gig.get("band_formats"))
            if _sf and not (_sf & _cb_csv_set(artist.get("band_formats"))):
                logger.info(f"[BLAST] Skipping {artist['name']}: band_formats mismatch (gig-level)")
                continue
            _ss = _cb_csv_set(gig.get("styles"))
            if _ss and not (_ss & _cb_csv_set(artist.get("styles"))):
                logger.info(f"[BLAST] Skipping {artist['name']}: styles mismatch (gig-level)")
                continue

        # 2026-08-10: skip preferred artists who couldn't book anyway.
        # When the owning window has waive_frequency=OFF and this artist
        # is currently inside the venue's frequency window, they'd be
        # rejected at the book gate — sending them an email they can't
        # act on is worse than not sending. Skip silently.
        if not waive_freq_own and _artist_under_freq_limit(artist["id"]):
            logger.info(f"[BLAST] Skipping {artist['name']}: inside freq window at this venue and window's waive_frequency is off")
            continue

        # Check email preference — only skip if explicitly disabled
        pref = db.execute(text(
            "SELECT enabled FROM email_preferences WHERE user_id = :uid AND notification_type = 'cancelled_gig_preferred_blast'"
        ), {"uid": artist["user_id"]}).mappings().first()
        if pref and not pref["enabled"]:
            logger.info(f"[BLAST] Skipping {artist['name']}: opted out of this notification type")
            continue

        variables = {
            "artist_name": artist["name"],
            "artist_id": str(artist["id"]),
            "venue_name": gig["venue_name"],
            "date": format_email_date(gig["date"]),
            "start_time": format_time_12hr(gig["start_time"]),
            "end_time":   format_time_12hr(gig["end_time"]),
            # FIX (May 2026): use {:,.2f} so $200 displays as "200.00" not "200.0".
            # The template wraps with "$" prefix so we just format the number.
            "pay": f"{_get_effective_pay_for_artist(db, gig_id, venue_id, artist['id'], float(gig['pay'] or 0)):,.2f}",
            "artist_type": gig["artist_type"] or "",
            "title": gig["title"] or "",
            "band_formats": ", ".join(x.strip() for x in (gig["band_formats"] or "").split(",") if x.strip()),
            "styles": ", ".join(x.strip() for x in (gig["styles"] or "").split(",") if x.strip()),
            "city": gig["city"] or "",
            "state": gig["state"] or "",
            "gig_id": str(gig_id),
            "blast_token": blast_token,
            # 2026-08-10: template conditionally shows the "frequency waived"
            # sentence only when the owning open-gig window actually has
            # waive_frequency enabled. Previously the "no frequency
            # limitation" language was hardcoded, which confused artists
            # who wouldn't have hit a freq conflict anyway. "1" is truthy;
            # empty string is falsy for render_template's {{#var}}...{{/var}}.
            "waive_frequency": "1" if waive_freq_own else "",
            # Venue detail fields
            "venue_address":        _venue_address,
            "venue_address_link":   _venue_address_link,
            "venue_capacity":  _venue_capacity,
            "arrival_info":    _arrival_info,
            "stage_info":      _stage_info,
            "sound_info":      _sound_info,
            "engineer_info":   _engineer_info,
            "lighting_info":   _lighting_info,
            "bar_tab":         _bar_tab,
            "food_tab":        _food_tab,
            "notes_to_artist": gig.get("notes") or "",
            "slots_html":      _build_artist_slots_html(artist["id"]),
            "title":           gig.get("title") or "",
        }
        subject = render(tpl["subject"], variables)
        body    = render(tpl["body"], variables)

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = smtp_from
            msg["To"]   = artist["email"]
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as srv:
                    srv.login(smtp_user, smtp_pass)
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as srv:
                    srv.starttls()
                    srv.login(smtp_user, smtp_pass)
                    srv.send_message(msg)
            logger.info(f"[BLAST] ✅ Sent to {artist['name']} <{artist['email']}>")
            sent_count += 1
        except Exception as e:
            logger.error(f"[BLAST] ❌ Failed to send to {artist['email']}: {e}")

    logger.info(f"[BLAST] Done (preferred) — sent to {sent_count}/{len(artists)} preferred artists for gig {gig_id}")

    # ── Radius blast: non-preferred artists within radius ──────────────────
    # Jul 2026: gated by the OWNING WINDOW's cancel_notify_all_enabled +
    # cancel_notify_all_radius (already loaded above as blast_all_en / blast_all_mi).
    # Was previously reading the legacy `radius_blast` row — which meant the new
    # per-window UI toggle was silently ignored and a brand-new venue (no legacy
    # row) fired non-preferred blasts on every cancellation regardless of intent.
    if not blast_all_en:
        logger.info(f"[BLAST] Owning window has cancel_notify_all_enabled=0 — skipping radius blast")
        return

    radius_miles = blast_all_mi

    # Get venue coordinates
    venue_coords = db.execute(
        text("SELECT latitude, longitude FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).mappings().first()

    if not venue_coords or not venue_coords["latitude"] or not venue_coords["longitude"]:
        logger.info(f"[BLAST] Venue {venue_id} has no coordinates — skipping radius blast")
        return

    vlat, vlon = venue_coords["latitude"], venue_coords["longitude"]

    # Get IDs of preferred artists already emailed — don't double-send
    preferred_ids = {a["id"] for a in artists}

    # Load radius blast template
    radius_tpl = db.execute(text(
        "SELECT subject, body FROM email_templates WHERE template_key = 'cancelled_gig_radius_blast' LIMIT 1"
    )).mappings().first()
    if not radius_tpl:
        logger.warning(f"[BLAST] No 'cancelled_gig_radius_blast' template found — skipping radius blast")
        return

    # Bounding-box pre-filter in SQL — eliminates ~99% of rows before Python haversine.
    # 1 degree lat  ≈ 69 miles;  1 degree lon ≈ 69 * cos(lat) miles
    import math
    lat_delta = radius_miles / 69.0
    lon_delta = radius_miles / (69.0 * math.cos(math.radians(vlat))) if vlat else lat_delta

    lat_min, lat_max = vlat - lat_delta, vlat + lat_delta
    lon_min, lon_max = vlon - lon_delta, vlon + lon_delta

    candidate_artists = db.execute(text("""
        SELECT a.id, a.name, a.artist_type, a.latitude, a.longitude, u.email, u.id as user_id,
               a.band_formats, a.styles,
               COALESCE(a.has_own_equipment, 0) as has_own_equipment
        FROM artists a
        JOIN users u ON u.id = a.user_id
        WHERE a.deleted_at IS NULL
          AND a.latitude  BETWEEN :lat_min AND :lat_max
          AND a.longitude BETWEEN :lon_min AND :lon_max
          AND a.latitude  IS NOT NULL
          AND a.longitude IS NOT NULL
          AND a.id NOT IN (
              SELECT artist_id FROM venue_artist_bans WHERE venue_id = :vid
          )
          AND a.id NOT IN (
              SELECT artist_id FROM gig_waitlist
              WHERE gig_id = :gid AND offer_declined = 1
          )
          AND a.id NOT IN (
              -- Audit fix (May 2026 part 6): include awaiting_venue_contract +
              -- pending_venue_approval so an artist who already signed (or is
              -- pending venue approval) on one slot isn't blasted as a candidate
              -- for another slot on the same gig.
              SELECT artist_id FROM gig_slots
              WHERE gig_id = :gid
                AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                AND artist_id IS NOT NULL
          )
          AND (:excl_aid IS NULL OR a.id != :excl_aid)
          AND a.id NOT IN (
              -- Audit fix (May 2026 part 9): exclude every artist who has cancelled a slot
              -- on this gig, not just the most recent — see gig_cancelled_artists in db.py.
              SELECT artist_id FROM gig_cancelled_artists WHERE gig_id = :gid
          )
          AND NOT EXISTS (
              SELECT 1 FROM artist_availability aa
              WHERE aa.artist_id = a.id
                AND date(:gdate) BETWEEN date(aa.blackout_start) AND date(aa.blackout_end)
          )
    """), {
        "lat_min": lat_min, "lat_max": lat_max,
        "lon_min": lon_min, "lon_max": lon_max,
        "vid": venue_id, "gid": gig_id, "excl_aid": exclude_artist_id,
        "gdate": str(gig_date)[:10],
    }).mappings().all()

    radius_sent = 0
    for ra in candidate_artists:
        # Skip preferred artists — they already got the preferred blast
        if ra["id"] in preferred_ids:
            continue

        # FIX (Jul 2026): Artist type match — accepted set is gig-level
        # OR any open slot's type (handles multi-slot mixed gigs).
        # Reuses _accepted_types_blast computed at the top of this fn.
        if not ra["artist_type"]:
            continue
        if _accepted_types_blast and ra["artist_type"].strip().lower() not in _accepted_types_blast:
            continue
        # Jul 1 2026: band_formats + styles + equipment overlap filter.
        # Reuses the _cb_open_slots + _cb_slot_matches helpers from the
        # preferred branch above.
        if _cb_open_slots:
            if not any(_cb_slot_matches(ra["artist_type"], ra.get("band_formats"),
                                          ra.get("styles"),
                                          bool(ra.get("has_own_equipment")), s)
                        for s in _cb_open_slots):
                continue
        else:
            _sf = _cb_csv_set(gig.get("band_formats"))
            if _sf and not (_sf & _cb_csv_set(ra.get("band_formats"))):
                continue
            _ss = _cb_csv_set(gig.get("styles"))
            if _ss and not (_ss & _cb_csv_set(ra.get("styles"))):
                continue

        # Precise haversine check on the small candidate set
        R = 3958.8
        lat1, lon1 = math.radians(vlat), math.radians(vlon)
        lat2, lon2 = math.radians(ra["latitude"]), math.radians(ra["longitude"])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a_val = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        dist = R * 2 * math.asin(math.sqrt(a_val))
        if dist > radius_miles:
            continue

        # Check email preference
        pref = db.execute(text(
            "SELECT enabled FROM email_preferences WHERE user_id = :uid AND notification_type = 'cancelled_gig_radius_blast'"
        ), {"uid": ra["user_id"]}).mappings().first()
        if pref and not pref["enabled"]:
            continue

        variables = {
            "artist_name":    ra["name"],
            "artist_id":      str(ra["id"]),
            "venue_name":     gig["venue_name"],
            "date":           format_email_date(gig["date"]),
            "start_time":     format_time_12hr(gig["start_time"]),
            "end_time":       format_time_12hr(gig["end_time"]),
            # FIX (May 2026): use {:,.2f} for proper $XX.XX formatting (was showing $200.0)
            "pay":            f"{_get_effective_pay_for_artist(db, gig_id, venue_id, ra['id'], float(gig['pay'] or 0)):,.2f}",
            "artist_type":    gig["artist_type"] or "",
            "city":           gig["city"] or "",
            "state":          gig["state"] or "",
            "radius_miles":   str(radius_miles),
            "gig_id":         str(gig_id),
            "blast_token":    blast_token,
            "venue_address":      _venue_address,
            "venue_address_link": _venue_address_link,
            "venue_capacity": _venue_capacity,
            "arrival_info":   _arrival_info,
            "stage_info":     _stage_info,
            "sound_info":     _sound_info,
            "engineer_info":  _engineer_info,
            "lighting_info":  _lighting_info,
            "bar_tab":        _bar_tab,
            "food_tab":       _food_tab,
            "notes_to_artist": gig.get("notes") or "",
            "slots_html":      _build_artist_slots_html(ra["id"]),
            "title":           gig.get("title") or "",
        }
        subject = render(radius_tpl["subject"], variables)
        body    = render(radius_tpl["body"], variables)

        try:
            msg = MIMEMultipart("alternative")
            msg["From"]    = smtp_from
            msg["To"]      = ra["email"]
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as srv:
                    srv.login(smtp_user, smtp_pass)
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as srv:
                    srv.starttls()
                    srv.login(smtp_user, smtp_pass)
                    srv.send_message(msg)
            logger.info(f"[BLAST] ✅ Radius sent to {ra['name']} <{ra['email']}> ({dist:.1f}mi away)")
            radius_sent += 1
        except Exception as e:
            logger.error(f"[BLAST] ❌ Radius failed to send to {ra['email']}: {e}")

    logger.info(f"[BLAST] Done (radius) — sent to {radius_sent} non-preferred artists within {radius_miles}mi for gig {gig_id}")


@router.post("/api/gigs/{gig_id}/new-gig-blast")
@limiter.limit("10/minute")
def new_gig_blast(request: Request, gig_id: int, data: dict = None, user=Depends(get_current_user), db=Depends(get_db)):
    """Sets radius_blast_token so the calendar blinks. Actual emails sent via /batch-blast.
    Audit fix (May 2026 part 8): rate-limited 10/min so a click-loop doesn't spam blast tokens."""
    import secrets as _secrets
    from backend.utils import check_venue_access
    if data is None:
        data = {}
    gig = db.execute(text("SELECT venue_id, status FROM gigs WHERE id = :gid"), {"gid": gig_id}).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if gig["status"] != "open":
        raise HTTPException(400, "Gig is not open")
    blast_token = _secrets.token_urlsafe(32)
    db.execute(text("UPDATE gigs SET radius_blast_token = :token, frequency_exempt = 1 WHERE id = :gid"),
               {"token": blast_token, "gid": gig_id})
    db.commit()
    return {"ok": True, "sent": 0}


@router.post("/api/venues/{venue_id}/batch-blast")
@limiter.limit("4/minute")
def batch_blast(request: Request, venue_id: int, data: dict, background_tasks: BackgroundTasks,
                user=Depends(get_current_user), db=Depends(get_db)):
    """
    Send one combined email per artist listing all their relevant new gig dates.
    data: {
      gigs: [{id, blast_preferred, blast_all, blast_radius}]
    }

    Audit fix (May 2026 part 8): rate-limited 4/minute per venue. Previously
    unthrottled — a double-click or buggy client could fan out 200+ emails per
    second to artists in the radius. The frontend only fires this once per
    bulk-create, but defense-in-depth.
    """
    import secrets as _secrets, smtplib, re as _re
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from backend.utils import check_venue_access
    from backend.services.email_dispatch import format_email_date, format_time_12hr
    from backend.services.email_dispatch import _fetch_venue_detail_vars

    check_venue_access(db, venue_id, user.id)
    gig_requests = data.get("gigs", [])
    if not gig_requests:
        logger.warning("[BATCH_BLAST] No gig_requests received")
        return {"ok": True, "sent": 0}

    # Load SMTP
    smtp_rows = db.execute(text(
        "SELECT setting_key, setting_value FROM platform_settings WHERE setting_key LIKE '%smtp%' OR setting_key LIKE '%email%'"
    )).mappings().all()
    smtp = {r["setting_key"]: r["setting_value"] for r in smtp_rows}
    smtp_user = smtp.get("platform_email") or smtp.get("smtp_email") or ""
    smtp_pass = smtp.get("platform_email_password") or smtp.get("smtp_password") or ""
    if not smtp_user or not smtp_pass:
            return {"ok": True, "sent": 0, "note": "SMTP not configured"}
    smtp_server = smtp.get("platform_smtp_server") or "smtp.gmail.com"
    smtp_port   = int(smtp.get("platform_smtp_port") or 587)
    smtp_from_name = smtp.get("platform_email_from_name") or ""
    from email.utils import formataddr as _formataddr
    smtp_from = _formataddr((smtp_from_name, smtp_user)) if smtp_from_name else smtp_user

    # Load template — try new batch template first, fall back to preferred blast template
    tpl = db.execute(text(
        "SELECT subject, body FROM email_templates WHERE template_key = 'new_gigs_batch_blast' LIMIT 1"
    )).mappings().first()
    if not tpl:
        # Fallback to existing preferred blast template
        tpl = db.execute(text(
            "SELECT subject, body FROM email_templates WHERE template_key = 'cancelled_gig_preferred_blast' LIMIT 1"
        )).mappings().first()
    if not tpl:
        logger.error("[BATCH_BLAST] No email template found — run email_templates migration")
        return {"ok": True, "sent": 0, "note": "Template not found"}

    # Load venue info
    venue = db.execute(text(
        "SELECT id, venue_name, latitude, longitude FROM venues WHERE id = :vid"
    ), {"vid": venue_id}).mappings().first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    base_url = db.execute(text(
        "SELECT setting_value FROM platform_settings WHERE setting_key = 'site_url'"
    )).scalar() or "https://gigsfill.com"

    venue_vars = _fetch_venue_detail_vars(db, venue_id)
    booking_url = f"{base_url}/app/artist-book-gigs.html"

    # Build per-gig info and set blast tokens
    gig_infos = []
    for gr in gig_requests:
        gig_id = gr.get("id")
        gig = db.execute(text("""
            SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.title,
                   g.artist_type, g.band_formats, g.styles, g.notes, g.status
            FROM gigs g WHERE g.id = :gid AND g.venue_id = :vid
        """), {"gid": gig_id, "vid": venue_id}).mappings().first()
        if not gig or gig["status"] != "open":
            continue
        # Only set blast token if venue actually chose to blast this gig
        do_blast = gr.get("blast_preferred", False) or gr.get("blast_all", False)
        if do_blast:
            blast_token = _secrets.token_urlsafe(32)
            db.execute(text("UPDATE gigs SET radius_blast_token = :tok, frequency_exempt = 1 WHERE id = :gid"),
                       {"tok": blast_token, "gid": gig_id})
        gig_infos.append({
            "id": gig_id,
            "date": gig["date"],
            "start_time": gig["start_time"],
            "end_time": gig["end_time"],
            "pay": gig["pay"],
            "title": gig["title"],
            "artist_type": gig["artist_type"],
            "band_formats": gig["band_formats"],
            "styles": gig["styles"],
            "notes": gig["notes"],
            "blast_preferred": gr.get("blast_preferred", True),
            "blast_all": gr.get("blast_all", False),
            "blast_radius": int(gr.get("blast_radius", 20)),
        })
    db.commit()

    if not gig_infos:
            return {"ok": True, "sent": 0}

    def _render(s, vars_):
        def _block(m):
            k = m.group(1); inner = m.group(2)
            return inner if vars_.get(k) else ""
        s = _re.sub(r'\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}', _block, s, flags=_re.DOTALL)
        for k, v in vars_.items():
            s = s.replace("{{" + k + "}}", str(v or ""))
        return s

    def _row(label, val, color="#111827", weight="500", indent=0):
        # indent = left padding (px) on the label cell, creating the
        # Date → Slot → slot-fields visual hierarchy in the email. The
        # label column width shrinks as indent grows so the value column
        # doesn't drift too far right. (May 2026 part 10d formatting fix.)
        lpad = f"padding:5px 0 5px {indent}px;" if indent else "padding:5px 0;"
        lw = max(70, 120 - indent)
        return (f'<tr><td style="{lpad}font-size:13px;color:#6b7280;'
                f'width:{lw}px;vertical-align:top;">{label}</td>'
                f'<td style="padding:5px 0;font-size:13px;color:{color};'
                f'font-weight:{weight};vertical-align:top;">{val}</td></tr>')

    def _build_gigs_html(gig_subset, pay_overrides=None):
        """pay_overrides: optional dict of {gig_id: effective_pay_float}

        FIX (May 15 2026): mirror the per-slot rendering used by
        scheduler.py:_build_slots_html_for_scheduler (the helper that
        powers the open_gig_*w / open_gig_36h blast emails). For
        multi-slot gigs, each slot can have its own artist_type, lineup,
        styles, and pay — show all of them stacked per slot with a
        separator between slots, then a thicker separator between gigs.
        Single-slot gigs render exactly as before.
        """
        slot_sep = '<tr><td colspan="2" style="padding:4px 0;border-top:1px solid #e5e7eb;"></td></tr>'
        gig_sep  = '<tr><td colspan="2" style="padding:8px 0;border-top:1px solid #fde68a;"></td></tr>'

        def _commas(s):
            return ', '.join(x.strip() for x in (s or '').split(',') if x.strip())

        # Door-deal aware pay formatter. For slots with deal_type='door'
        # this returns "$10.00 guarantee + 50% of door"; for flat slots
        # the legacy "$15.00" format. Mirrors what the scheduler blast
        # builder does in _build_slots_html_for_scheduler — both blast
        # paths need the same door-aware output for consistency.
        from backend.services.email_dispatch import format_pay_summary_with_sign as _door_pay_fmt
        def _fmt_pay(val):
            try:
                return f"${float(val):.2f}"
            except (ValueError, TypeError):
                return f"${val}"

        parts = []
        for i, g in enumerate(gig_subset):
            if i > 0:
                parts.append(gig_sep)

            # Pull this gig's open slots with their per-slot artist_type,
            # band_formats, styles, and pay. status='open' only — booked
            # slots aren't bookable so they're omitted from the email.
            slots = db.execute(
                text("""SELECT start_time, end_time, pay, artist_type,
                               band_formats, styles,
                               deal_type, door_pct, guarantee_cents
                        FROM gig_slots
                        WHERE gig_id = :gid AND status = 'open'
                        ORDER BY slot_number ASC"""),
                {"gid": g["id"]}
            ).mappings().all()

            # Indentation hierarchy (May 2026 part 10d):
            #   Date / Event  → base (indent 0)
            #   Slot N header  → indent 16px under Date
            #   slot fields    → indent 32px under Slot header
            # Single-slot gigs put their fields at indent 16 (one level under
            # Date) since there's no Slot header to nest beneath.
            parts.append(_row("Date", format_email_date(g["date"]), "#d97706", "700"))
            if g.get("title"):
                parts.append(_row("Event", g["title"]))

            override_pay = (pay_overrides or {}).get(g["id"])

            if slots:
                _multi = len(slots) > 1
                field_indent = 32 if _multi else 16
                for j, sl in enumerate(slots):
                    if j > 0:
                        parts.append(slot_sep)
                    if _multi:
                        # Slot N header row, full-width, indented one level under Date.
                        parts.append(
                            '<tr><td colspan="2" style="padding:8px 0 4px 16px;font-size:14px;'
                            'color:#7c6bff;font-weight:700;">'
                            f"Slot {j + 1}"
                            '</td></tr>'
                        )
                    t = format_time_12hr(sl["start_time"] or "")
                    if sl.get("end_time"):
                        t += " – " + format_time_12hr(sl["end_time"])
                    parts.append(_row("Time", t, indent=field_indent))
                    # Door-aware Pay row. When this slot is a door
                    # split (deal_type='door' with non-zero terms), render
                    # "$X guarantee + Y% of door". Otherwise fall back to
                    # flat formatting + the preferred-artist override.
                    if (sl.get("deal_type") or "").lower() == "door":
                        pay_str = _door_pay_fmt({
                            "pay": sl.get("pay"),
                            "deal_type": sl.get("deal_type"),
                            "door_pct": sl.get("door_pct"),
                            "guarantee_cents": sl.get("guarantee_cents"),
                        }, fallback_pay=g.get("pay"))
                        parts.append(_row("Pay", pay_str, "#059669", "600", indent=field_indent))
                    else:
                        sl_pay = override_pay or sl.get("pay") or g.get("pay")
                        if sl_pay:
                            parts.append(_row("Pay", _fmt_pay(sl_pay), "#059669", "600", indent=field_indent))
                    atype  = sl.get("artist_type") or g.get("artist_type")
                    lineup = _commas(sl.get("band_formats") or g.get("band_formats"))
                    styles = _commas(sl.get("styles") or g.get("styles"))
                    if atype:  parts.append(_row("Type",   atype,  indent=field_indent))
                    if lineup: parts.append(_row("Lineup", lineup, indent=field_indent))
                    if styles: parts.append(_row("Styles", styles, indent=field_indent))
            else:
                # Legacy gigs (no gig_slots rows) — umbrella time + parent
                # pay/type, indented one level under Date.
                t = format_time_12hr(g["start_time"] or "")
                if g.get("end_time"):
                    t += " – " + format_time_12hr(g["end_time"])
                parts.append(_row("Time", t, indent=16))
                effective_pay = override_pay or g.get("pay")
                if effective_pay:
                    parts.append(_row("Pay", _fmt_pay(effective_pay), "#059669", "600", indent=16))
                if g.get("artist_type"):
                    parts.append(_row("Type", g["artist_type"], indent=16))

            # Per-gig Notes row (after the slots — notes are gig-level,
            # not slot-level, and the user wants them surfaced in the
            # email like the cancelled_blast / open_gig blast emails do).
            if g.get("notes"):
                parts.append(_row("Notes", g["notes"]))
        return f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"><tbody>{"".join(parts)}</tbody></table>'

    def _send(artist_email, artist_name, artist_id, gig_subset, pay_overrides=None):
        gigs_html = _build_gigs_html(gig_subset, pay_overrides=pay_overrides)
        artist_booking_url = f"{booking_url}?artist_id={artist_id}"
        variables = {
            "artist_name": artist_name,
            "artist_id": str(artist_id),
            "venue_name": venue["venue_name"],
            "venue_id": str(venue_id),
            "gigs_list_html": gigs_html,
            "booking_url": artist_booking_url,
            **venue_vars,
        }
        subject = _render(tpl["subject"], variables)
        body    = _render(tpl["body"], variables)
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = smtp_from
            msg["To"]   = artist_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as sv:
                    sv.login(smtp_user, smtp_pass); sv.send_message(msg)
            else:
                with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as sv:
                    sv.starttls(); sv.login(smtp_user, smtp_pass); sv.send_message(msg)
        except Exception as e:
            logger.error(f"[BATCH_BLAST] Send error: {e}")
    # Collect gigs per artist (preferred)
    artist_gigs: dict = {}  # artist_id -> {info, gigs:[]}

    preferred_gigs = [g for g in gig_infos if g["blast_preferred"]]
    if preferred_gigs:
        preferred = db.execute(text("""
            SELECT a.id, a.name, u.email, u.id as user_id
            FROM preferred_artists pa
            JOIN artists a ON a.id = pa.artist_id AND a.deleted_at IS NULL
            JOIN users u ON u.id = a.user_id
            WHERE pa.venue_id = :vid AND pa.status = 'approved'
              AND a.id NOT IN (
                  SELECT artist_id FROM venue_artist_bans WHERE venue_id = :vid
              )
        """), {"vid": venue_id}).mappings().all()
        for p in preferred:
            pref = db.execute(text(
                "SELECT enabled FROM email_preferences WHERE user_id = :uid AND notification_type = 'cancelled_gig_preferred_blast'"
            ), {"uid": p["user_id"]}).scalar()
            if pref is not None and not pref:
                continue
            if p["id"] not in artist_gigs:
                artist_gigs[p["id"]] = {"email": p["email"], "name": p["name"], "gigs": [], "pay_overrides": {}}
            # Look up this artist's pay override for this venue
            override_row = db.execute(text(
                "SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid AND status = 'approved'"
            ), {"vid": venue_id, "aid": p["id"]}).mappings().first()
            if override_row and override_row["pay_dollars_override"] is not None:
                override_pay = float(override_row["pay_dollars_override"]) + float(override_row["pay_cents_override"] or 0) / 100
                # Apply override: effective pay = max(override, published gig pay)
                for pg in preferred_gigs:
                    pub_pay = float(pg["pay"] or 0)
                    artist_gigs[p["id"]]["pay_overrides"][pg["id"]] = max(override_pay, pub_pay)
            # Add all preferred gigs for this artist
            artist_gigs[p["id"]]["gigs"].extend(preferred_gigs)

    # Collect radius-blast gigs per nearby artist
    radius_gigs = [g for g in gig_infos if g["blast_all"]]
    if radius_gigs and venue["latitude"] and venue["longitude"]:
        # Use the smallest radius among radius-blast gigs (most conservative)
        radius = min(g["blast_radius"] for g in radius_gigs)
        nearby = db.execute(text("""
            SELECT DISTINCT a.id, a.name, u.email
            FROM artists a
            JOIN users u ON u.id = a.user_id
            WHERE a.id NOT IN (
                SELECT artist_id FROM preferred_artists WHERE venue_id = :vid AND status = 'approved'
            )
            AND a.id NOT IN (
                SELECT artist_id FROM venue_artist_bans WHERE venue_id = :vid
            )
            AND (a.latitude IS NULL OR a.longitude IS NULL OR (
                3959 * acos(
                    min(1.0, max(-1.0,
                        cos(radians(:lat)) * cos(radians(a.latitude)) *
                        cos(radians(a.longitude) - radians(:lng)) +
                        sin(radians(:lat)) * sin(radians(a.latitude))
                    ))
                )) <= :radius)
        """), {"vid": venue_id, "lat": venue["latitude"], "lng": venue["longitude"], "radius": radius}).mappings().all()
        for a in nearby:
            if a["id"] not in artist_gigs:
                artist_gigs[a["id"]] = {"email": a["email"], "name": a["name"], "gigs": [], "pay_overrides": {}}
            # Only add radius gigs they don't already have from preferred
            existing_ids = {g["id"] for g in artist_gigs[a["id"]]["gigs"]}
            for rg in radius_gigs:
                if rg["id"] not in existing_ids:
                    artist_gigs[a["id"]]["gigs"].append(rg)

    # Capture artist_gigs snapshot for background task (db session cannot be reused)
    artist_gigs_snapshot = dict(artist_gigs)
    gig_infos_snapshot   = list(gig_infos)
    _venue_id_snap       = venue_id

    def _do_send_emails():
        """Runs outside the request cycle — db session is fresh."""
        from backend.db import SessionLocal as _SL
        _bg_db = _SL()
        _sent = 0
        try:
            for _aid, _info in artist_gigs_snapshot.items():
                if not _info["gigs"]:
                    continue
                _subset = sorted(_info["gigs"], key=lambda g: g["date"])
                _seen = set()
                _subset = [g for g in _subset if g["id"] not in _seen and not _seen.add(g["id"])]
                _send(_info["email"], _info["name"], _aid, _subset,
                      pay_overrides=_info.get("pay_overrides"))
                _sent += 1
            # Log blast
            for _g in gig_infos_snapshot:
                try:
                    _bg_db.execute(text("""
                        INSERT OR IGNORE INTO gig_email_log (gig_id, venue_id, notification_key, recipient_count)
                        VALUES (:gid, :vid, 'new_gig_blast', :cnt)
                    """), {"gid": _g["id"], "vid": _venue_id_snap, "cnt": _sent})
                except Exception:
                    pass
            _bg_db.commit()
            logger.info(f"[BATCH_BLAST] Background send complete: {_sent} emails")
        except Exception as _e:
            logger.error(f"[BATCH_BLAST] Background send error: {_e}")
        finally:
            _bg_db.close()

    background_tasks.add_task(_do_send_emails)
    return {"ok": True, "queued": True, "recipient_count": len(artist_gigs_snapshot)}


# ── GIG INFO FOR FLYER EDITOR (duplicated from flyers.py so it works even if flyers module not loaded) ──
# ── FLYER ENDPOINTS IN GIGS.PY (so flyer display works even if flyers.py not registered) ──

@router.get("/api/gigs/{gig_id}/flyer")
def get_flyer_for_gig(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Check if a flyer exists for this gig (used by flyer editor)."""
    try:
        row = db.execute(text("""
            SELECT f.id, f.name, f.thumbnail_data, f.updated_at, f.venue_id
            FROM flyers f WHERE f.gig_id = :gid AND f.is_template = 0
            ORDER BY f.updated_at DESC LIMIT 1
        """), {"gid": gig_id}).fetchone()
    except Exception:
        return {"exists": False}
    if not row:
        return {"exists": False}
    d = dict(row._mapping)
    d["exists"] = True
    return d

@router.get("/api/gigs/{gig_id}/flyer/public")
def get_public_flyer(gig_id: int, db=Depends(get_db)):
    """Public flyer endpoint — no auth required.
    Priority: thumbnail_data > canvas_data from saved flyer > canvas_data from venue template
    Always returns canvas_data + gig_info when no thumbnail so client can render live.
    """
    def get_gig_info(gid):
        row = db.execute(text("""
            SELECT g.id, g.date, g.start_time, g.end_time,
                   COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                   v.venue_name, v.address_line_1, v.address_line_2, v.city, v.state,
                   a.id as artist_id, a.name as artist_name,
                   (SELECT am.file_path FROM artist_media am
                    WHERE am.artist_id = g.artist_id
                    AND am.media_type IN ('profile','logo')
                    ORDER BY CASE am.media_type WHEN 'profile' THEN 0 ELSE 1 END LIMIT 1
                   ) as artist_picture_url,
                   (SELECT vm.file_path FROM venue_media vm
                    WHERE vm.venue_id = v.id AND vm.media_type = 'profile' LIMIT 1
                   ) as venue_picture_url
            FROM gigs g JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.id = g.artist_id
            WHERE g.id = :gid
        """), {"gid": gid}).fetchone()
        if not row:
            return {}
        info = dict(row._mapping)
        # Always fetch booked slots — needed for multi-slot AND single-slot gigs
        # where g.artist_id may be NULL (open/unbooked slot on the root gig row)
        slots_rows = db.execute(text("""
            SELECT gs.artist_id, a.name as artist_name,
                   (SELECT am.file_path FROM artist_media am
                    WHERE am.artist_id = gs.artist_id
                    AND am.media_type IN ('profile','logo')
                    ORDER BY CASE am.media_type WHEN 'profile' THEN 0 ELSE 1 END LIMIT 1
                   ) as artist_picture_url
            FROM gig_slots gs JOIN artists a ON a.id = gs.artist_id
            WHERE gs.gig_id = :gid AND gs.status = 'booked'
            ORDER BY gs.slot_number
        """), {"gid": gid}).fetchall()
        if slots_rows:
            slots_list = [dict(s._mapping) for s in slots_rows]
            info["slots"] = slots_list
            # Hoist first booked artist to root if not already set
            if not info.get("artist_name"):
                info["artist_name"] = slots_list[0].get("artist_name")
                info["artist_id"] = slots_list[0].get("artist_id")
                info["artist_picture_url"] = slots_list[0].get("artist_picture_url") or info.get("artist_picture_url")
        return info

    def get_template_canvas(venue_id, default_template_id=None, auto_flyers=0):
        """Fetch canvas_data respecting venue's chosen template setting.
        Priority:
        1. If default_template_id set: load that specific template
        2. If auto_flyers=1 but default_template_id=NULL: user chose site-wide Default — skip to step 3
           (do NOT fall through to VenueName_Default Template by name)
        3. Site-wide Default Template (venue_id IS NULL)
        VenueName_Default Template by name is ONLY used as a last resort when auto_flyers is not set.
        """
        # 1. Explicit template chosen
        if default_template_id:
            try:
                row = db.execute(text("""
                    SELECT canvas_data FROM flyers
                    WHERE id = :tid AND is_template = 1
                """), {"tid": int(default_template_id)}).fetchone()
                if row:
                    cd = row._mapping.get("canvas_data")
                    if cd and cd not in ("{}", '{"objects":[]}'):
                        return cd
            except Exception:
                pass
        # 2. auto_flyers=1 but no explicit ID = user picked "Default Template" → skip name-based lookup
        if not auto_flyers:
            # auto_flyers not set: try VenueName_Default Template by name as convenience fallback
            row1 = db.execute(text("""
                SELECT f.canvas_data FROM flyers f
                JOIN venues v ON f.venue_id = v.id
                WHERE f.venue_id = :vid AND f.is_template = 1
                AND f.name = v.venue_name || '_Default Template'
                ORDER BY f.updated_at DESC LIMIT 1
            """), {"vid": venue_id}).fetchone()
            if row1:
                cd = row1._mapping.get("canvas_data")
                if cd and cd not in ("{}", '{"objects":[]}'):
                    return cd
        # 3. Site-wide Default Template
        row2 = db.execute(text("""
            SELECT canvas_data FROM flyers
            WHERE venue_id IS NULL AND is_template = 1
            AND LOWER(name) = 'default template'
            ORDER BY updated_at DESC LIMIT 1
        """)).fetchone()
        if row2:
            cd = row2._mapping.get("canvas_data")
            if cd and cd not in ("{}", '{"objects":[]}'):
                return cd
        return None

    flyer_row = None
    try:
        flyer_row = db.execute(text("""
            SELECT f.id, f.name, f.thumbnail_data, f.canvas_data,
                   f.venue_id, v.default_flyer_template_id,
                   COALESCE(v.auto_flyers, 0) as auto_flyers,
                   g.status as gig_status,
                   COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                   (SELECT COUNT(*) FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')) as booked_slots_count
            FROM flyers f
            JOIN venues v ON f.venue_id = v.id
            JOIN gigs g ON f.gig_id = g.id
            WHERE f.gig_id = :gid AND f.is_template = 0
            ORDER BY
                CASE WHEN f.thumbnail_data IS NOT NULL AND f.thumbnail_data != '' THEN 0 ELSE 1 END,
                CASE WHEN f.canvas_data IS NOT NULL AND f.canvas_data NOT IN ('{}','{"objects":[]}') THEN 0 ELSE 1 END,
                f.updated_at DESC
            LIMIT 1
        """), {"gid": gig_id}).fetchone()
    except Exception:
        pass  # Fall through to venue-only path

    if flyer_row:
        f = dict(flyer_row._mapping)
        gig_status = f.get("gig_status", "")
        booked_slots = f.get("booked_slots_count", 0)
        is_multi_slot = f.get("is_multi_slot", 0)
        gig_is_booked = (gig_status == "booked") or (booked_slots > 0)

        # Best case: manually saved thumbnail — always show regardless of open/booked
        if f.get("thumbnail_data"):
            return {"exists": True, "thumbnail_data": f["thumbnail_data"], "name": f.get("name")}
        # Has real canvas_data saved directly on the flyer record — always show (venue put effort in)
        cd = f.get("canvas_data") or ""
        if cd and cd not in ("{}", '{"objects":[]}'):
            return {"exists": True, "canvas_data": cd, "gig_info": get_gig_info(gig_id), "name": f.get("name")}
        # Empty/auto-created flyer record — only serve template for booked gigs
        if not gig_is_booked:
            return {"exists": False}
        # Gig is booked: cascade through template chain regardless of auto_flyers flag
        # (auto_flyers only gates auto-creation of placeholder records, not public rendering)
        canvas_data = get_template_canvas(f["venue_id"], f.get("default_flyer_template_id"), f.get("auto_flyers", 0))
        if canvas_data:
            return {"exists": True, "canvas_data": canvas_data, "gig_info": get_gig_info(gig_id), "name": f.get("name")}
        return {"exists": True, "use_builtin": True, "gig_info": get_gig_info(gig_id)}

    # No flyer record at all — check venue template cascade for booked gigs
    try:
        venue_row = db.execute(text("""
            SELECT v.id as venue_id, COALESCE(v.auto_flyers, 0) as auto_flyers,
                   v.default_flyer_template_id, g.status as gig_status,
                   COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                   (SELECT COUNT(*) FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')) as booked_slots_count
            FROM gigs g JOIN venues v ON g.venue_id = v.id
            WHERE g.id = :gid
        """), {"gid": gig_id}).fetchone()
        if venue_row:
            v = venue_row._mapping
            gig_status = v.get("gig_status", "")
            booked_slots = v.get("booked_slots_count", 0)
            is_multi_slot = v.get("is_multi_slot", 0)
            gig_is_booked = (gig_status == "booked") or (booked_slots > 0)
            # Only show flyer for booked gigs
            if not gig_is_booked:
                return {"exists": False}
            # Booked gig: cascade through template chain
            canvas_data = get_template_canvas(v["venue_id"], v.get("default_flyer_template_id"), v.get("auto_flyers", 0))
            if canvas_data:
                return {"exists": True, "canvas_data": canvas_data, "gig_info": get_gig_info(gig_id)}
            return {"exists": True, "use_builtin": True, "gig_info": get_gig_info(gig_id)}
    except Exception:
        pass
    return {"exists": False}

@router.get("/api/flyers/site-default-template")
def get_site_default_template_public(db=Depends(get_db)):
    """Public endpoint — no auth required. Returns the admin site-wide Default Template.
    Used by venue flyer editor and public renderers so non-admin users can load it."""
    try:
        row = db.execute(text("""
            SELECT id, name, canvas_data, thumbnail_data, size_preset, width, height
            FROM flyers
            WHERE venue_id IS NULL AND is_template = 1 AND LOWER(name) = 'default template'
            ORDER BY updated_at DESC LIMIT 1
        """)).fetchone()
    except Exception:
        return {"canvas_data": "{}", "name": "Default Template", "id": None}
    if not row:
        return {"canvas_data": "{}", "name": "Default Template", "id": None}
    return dict(row._mapping)


@router.get("/api/flyers/{flyer_id}/detail")
def get_flyer_detail(flyer_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get full flyer data including canvas_data (fallback if flyers.py not registered)."""
    try:
        row = db.execute(text("SELECT * FROM flyers WHERE id = :fid"), {"fid": flyer_id}).fetchone()
    except Exception:
        raise HTTPException(404, "Flyer not found")
    if not row:
        raise HTTPException(404, "Flyer not found")
    return dict(row._mapping)

@router.get("/api/gig-info-for-flyer/{gig_id}")
def get_gig_info_for_flyer_fallback(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Return gig + venue + artist info needed by the flyer editor."""
    row = db.execute(text("""
        SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.status,
               g.artist_id, g.venue_id, COALESCE(g.is_multi_slot, 0) as is_multi_slot,
               a.name as artist_name,
               (SELECT am.file_path FROM artist_media am 
                WHERE am.artist_id = g.artist_id AND am.media_type IN ('profile', 'logo')
                ORDER BY CASE am.media_type WHEN 'profile' THEN 0 ELSE 1 END LIMIT 1) as artist_picture_url,
               v.venue_name, v.address_line_1, v.address_line_2, v.city, v.state, v.postal_code,
               (SELECT vm.file_path FROM venue_media vm 
                WHERE vm.venue_id = v.id AND vm.media_type = 'profile' 
                LIMIT 1) as venue_picture_url
        FROM gigs g JOIN venues v ON g.venue_id = v.id
        LEFT JOIN artists a ON g.artist_id = a.id
        WHERE g.id = :gid
    """), {"gid": gig_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Gig not found")
    result = dict(row._mapping)
    slots = db.execute(text("""
        SELECT gs.slot_number, gs.start_time, gs.end_time, gs.pay,
               gs.artist_id, a.name as artist_name,
               (SELECT am.file_path FROM artist_media am
                WHERE am.artist_id = gs.artist_id AND am.media_type IN ('profile', 'logo')
                ORDER BY CASE am.media_type WHEN 'profile' THEN 0 ELSE 1 END LIMIT 1) as artist_picture_url
        FROM gig_slots gs LEFT JOIN artists a ON gs.artist_id = a.id
        WHERE gs.gig_id = :gid ORDER BY gs.slot_number
    """), {"gid": gig_id}).fetchall()
    result["slots"] = [dict(s._mapping) for s in slots]
    return result


# ══════════════════════════════════════════════════════════════════════════════
# iCAL EXPORT ENDPOINTS
# Returns RFC 5545-compliant .ics files for calendar apps (Google, Apple, Outlook).
# ══════════════════════════════════════════════════════════════════════════════

from fastapi.responses import Response as _Response
import re as _re
from datetime import datetime as _dt, date as _date


def _ical_escape(text: str) -> str:
    """Escape special characters per RFC 5545 §3.3.11."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "")
    return text


def _ical_fold(line: str) -> str:
    """Fold long lines at 75 octets per RFC 5545 §3.1."""
    if len(line.encode("utf-8")) <= 75:
        return line
    result = []
    while len(line.encode("utf-8")) > 75:
        # Cut at 75 bytes, being careful not to split a multi-byte char
        chunk = line.encode("utf-8")[:75].decode("utf-8", errors="ignore")
        result.append(chunk)
        line = line[len(chunk):]
    result.append(line)
    return "\r\n ".join(result)


def _build_dt(date_str: str, time_str: str) -> str:
    """Build iCal DTSTART/DTEND value: YYYYMMDDTHHMMSS (local, no TZ suffix = floating)."""
    try:
        d = str(date_str)[:10].replace("-", "")
        if time_str:
            t = str(time_str)[:5].replace(":", "") + "00"
        else:
            t = "000000"
        return f"{d}T{t}"
    except Exception:
        return ""


def _make_uid(gig_id: int, suffix: str = "") -> str:
    return f"gig-{gig_id}{suffix}@gigsfill.com"


def _build_ical(events: list[dict], calendar_name: str) -> str:
    """Assemble a VCALENDAR string from a list of event dicts."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//GigsFill//GigsFill Calendar//EN",
        f"X-WR-CALNAME:{_ical_escape(calendar_name)}",
        "X-WR-TIMEZONE:America/New_York",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    now_str = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for ev in events:
        dtstart = _build_dt(ev.get("date"), ev.get("start_time"))
        dtend   = _build_dt(ev.get("date"), ev.get("end_time")) or dtstart
        if not dtstart:
            continue
        summary  = _ical_escape(ev.get("summary", "Gig"))
        location = _ical_escape(ev.get("location", ""))
        desc     = _ical_escape(ev.get("description", ""))
        uid      = ev.get("uid", f"gig-{ev.get('id', 'x')}@gigsfill.com")
        lines += [
            "BEGIN:VEVENT",
            _ical_fold(f"UID:{uid}"),
            f"DTSTAMP:{now_str}",
            _ical_fold(f"DTSTART:{dtstart}"),
            _ical_fold(f"DTEND:{dtend}"),
            _ical_fold(f"SUMMARY:{summary}"),
        ]
        if location:
            lines.append(_ical_fold(f"LOCATION:{location}"))
        if desc:
            lines.append(_ical_fold(f"DESCRIPTION:{desc}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@router.get("/api/artists/{artist_id}/calendar.ics")
def artist_ical(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Download an iCal file of all booked/pending gigs for an artist."""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)

    artist = db.execute(
        text("SELECT name FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    artist_name = artist["name"] if artist else "Artist"

    # Door-aware: pull this artist's slot row alongside the gig so the
    # Pay line in the iCal description renders door split terms
    # ("$X guarantee + Y% of door") when the slot has deal_type='door'.
    # gs.* fields are NULL for single-slot legacy gigs that booked via
    # gigs.artist_id directly — the helper falls back to gigs.pay then.
    # 2026-08-08 audit fix (finding #5): the previous outer-level
    # `AND g.status IN ('booked','pending_contract','awaiting_venue_contract')`
    # dropped every multi-slot booking whose parent gig still had open
    # sibling slots (parent stays g.status='open' until the last slot
    # fills — see book_slot). Move the status check inside each OR
    # branch so single-slot uses g.status and multi-slot uses gs.status.
    # Also add 'pending_venue_approval' for consistency with the digest
    # renderer at open_gig_digest.py:204-224.
    _BOOKED_STATUSES = ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval')
    rows = db.execute(
        text("""
            SELECT DISTINCT
                g.id, g.date, g.start_time, g.end_time, g.title, g.notes,
                g.pay, g.status,
                gs.pay              as slot_pay,
                gs.deal_type        as slot_deal_type,
                gs.door_pct         as slot_door_pct,
                gs.guarantee_cents  as slot_guarantee_cents,
                v.venue_name, v.address_line_1, v.city, v.state, v.postal_code
            FROM gigs g
            JOIN venues v ON v.id = g.venue_id
            LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
            WHERE (
                (g.artist_id = :aid AND g.status IN :booked)
                OR (gs.artist_id = :aid AND gs.status IN :booked)
            )
              AND g.date >= date('now', '-1 day')
            ORDER BY g.date ASC
        """).bindparams(bindparam("booked", expanding=True)),
        {"aid": artist_id, "booked": list(_BOOKED_STATUSES)}
    ).mappings().all()

    from backend.services.email_dispatch import format_pay_summary_with_sign

    events = []
    for r in rows:
        parts = []
        if r["venue_name"]:
            parts.append(f"Venue: {r['venue_name']}")
        # Build a probe dict pointing the helper at whichever pay source
        # actually has data — slot_* when this artist has a slot row,
        # gig-level pay otherwise.
        pay_probe = {
            "pay":             r["slot_pay"] if r["slot_pay"] is not None else r["pay"],
            "deal_type":       r["slot_deal_type"],
            "door_pct":        r["slot_door_pct"],
            "guarantee_cents": r["slot_guarantee_cents"],
        }
        if pay_probe["pay"] is not None and float(pay_probe["pay"]) > 0:
            parts.append(f"Pay: {format_pay_summary_with_sign(pay_probe)}")
        if r["notes"]:
            parts.append(f"Notes: {r['notes']}")
        events.append({
            "id": r["id"],
            "uid": _make_uid(r["id"], f"-{artist_id}"),
            "date": r["date"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "summary": r["title"] or r["venue_name"] or "Gig",
            "location": ", ".join(filter(None, [
                r["address_line_1"], r["city"], r["state"], r["postal_code"]
            ])),
            "description": "\n".join(parts),
        })

    ics = _build_ical(events, f"{artist_name} – GigsFill Calendar")
    filename = f"gigsfill-{artist_name.lower().replace(' ', '-')}.ics"
    return _Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/api/venues/{venue_id}/calendar.ics")
def venue_ical(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Download an iCal file of all upcoming gigs for a venue."""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)

    venue = db.execute(
        text("SELECT venue_name, address_line_1, city, state, postal_code FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    venue_name = venue["venue_name"] if venue else "Venue"
    venue_addr = ", ".join(filter(None, [
        venue["address_line_1"], venue["city"], venue["state"], venue["postal_code"]
    ])) if venue else ""

    rows = db.execute(
        text("""
            SELECT g.id, g.date, g.start_time, g.end_time, g.title, g.notes,
                   g.pay, g.status, g.artist_type, g.is_multi_slot,
                   a.name as artist_name
            FROM gigs g
            LEFT JOIN artists a ON g.artist_id = a.id
            WHERE g.venue_id = :vid
              AND g.status NOT IN ('cancelled', 'deleted')
              AND g.date >= date('now', '-1 day')
            ORDER BY g.date ASC
        """),
        {"vid": venue_id}
    ).mappings().all()

    from backend.services.email_dispatch import format_pay_summary_with_sign

    # Door-aware: multi-slot gigs can have different deal terms per slot
    # (one slot flat, another door). Fetch every booked/pending slot
    # in one round-trip then group per gig — one round trip beats N+1
    # SELECTs even when the venue has many gigs. For single-slot gigs
    # this stays a no-op (the helper falls back to gigs.pay below).
    gig_ids = [r["id"] for r in rows]
    slots_by_gig = {}
    if gig_ids:
        slot_rows = db.execute(
            text("""
                SELECT gs.gig_id, gs.slot_number, gs.pay, gs.deal_type,
                       gs.door_pct, gs.guarantee_cents
                FROM gig_slots gs
                WHERE gs.gig_id IN :gids
                  AND gs.status IN ('booked','pending_contract','awaiting_venue_contract')
                ORDER BY gs.gig_id, gs.slot_number
            """).bindparams(__import__('sqlalchemy').bindparam("gids", expanding=True)),
            {"gids": gig_ids}
        ).mappings().all()
        for s in slot_rows:
            slots_by_gig.setdefault(s["gig_id"], []).append(dict(s))

    events = []
    for r in rows:
        parts = []
        if r["artist_name"]:
            parts.append(f"Artist: {r['artist_name']}")
        elif r["artist_type"]:
            parts.append(f"Type: {r['artist_type']}")

        # Pay line(s) — multi-slot gigs render per-slot terms so a
        # mixed flat+door gig shows both: "Pay: Slot 1 $50.00; Slot 2
        # $25.00 guarantee + 20% of door". Single-slot uses the gig.pay
        # fallback inside the helper.
        slots = slots_by_gig.get(r["id"]) or []
        if slots and r.get("is_multi_slot"):
            pay_lines = []
            for s in slots:
                pay_str = format_pay_summary_with_sign(s)
                if pay_str and pay_str != "$0.00":
                    pay_lines.append(f"Slot {s['slot_number']} {pay_str}")
            if pay_lines:
                parts.append("Pay: " + "; ".join(pay_lines))
        else:
            # Single-slot: use the one slot's terms if booked, else the
            # gig-level pay column for the legacy / not-yet-booked case.
            probe = slots[0] if slots else {"pay": r["pay"]}
            if probe.get("pay") is not None and float(probe.get("pay") or 0) > 0:
                parts.append(f"Pay: {format_pay_summary_with_sign(probe)}")
        if r["notes"]:
            parts.append(f"Notes: {r['notes']}")
        status_label = {"open": "Open", "booked": "Booked", "pending_contract": "Pending Contract"}.get(r["status"], r["status"].title())
        parts.append(f"Status: {status_label}")

        summary = r["title"] or (f"{r['artist_name']} @ {venue_name}" if r["artist_name"] else f"{r['artist_type'] or 'Event'} @ {venue_name}")
        events.append({
            "id": r["id"],
            "uid": _make_uid(r["id"], f"-venue{venue_id}"),
            "date": r["date"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "summary": summary,
            "location": venue_addr,
            "description": "\n".join(parts),
        })

    ics = _build_ical(events, f"{venue_name} – GigsFill Calendar")
    filename = f"gigsfill-{venue_name.lower().replace(' ', '-')}.ics"
    return _Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ============================================================
# HOLD GIG — Phase 2: artist-facing accept/decline endpoints
# ============================================================
# Token-based public endpoints (no auth) so the artist can act from
# their email without logging in. Tokens are 24-byte URL-safe random
# (~192 bits entropy), one-use semantics (offer_declined=1 set on
# both accept + decline), expire 24h after the offer is sent.

def _venue_requires_contract_for_booking(db, venue_id: int, artist_id: int) -> bool:
    """True when the venue has an active contract that requires signing
    on booking. Mirrors the check in artist.book-gigs.js Book flow:
    `/api/venues/{vid}/contracts/active` returns has_contract + require_for_booking.
    Used by the email-link Hold-accept endpoints to detect whether a
    booking needs to flow through contract signing (in which case we
    redirect to the in-app calendar where the existing signing modal
    runs) vs. fast-path direct book.
    """
    try:
        row = db.execute(
            text("""SELECT COALESCE(require_for_booking, 0) as require_for_booking
                    FROM venue_contracts
                    WHERE venue_id = :vid AND is_active = 1
                    LIMIT 1"""),
            {"vid": venue_id}
        ).first()
        return bool(row and int(row[0] or 0))
    except Exception as _ce:
        logger.warning(f"[HOLD] contract-required check failed venue={venue_id}: {_ce}")
        return False


def _hold_redirect_to_contract_landing(token: str, gig_id: int, slot_id, venue_name: str,
                                        gig_date: str) -> str:
    """Render an HTML landing page that points the artist to the in-app
    contract signing flow. Used when the email-link path would have
    booked directly but the venue requires a contract.

    The deep link includes `hold_book=<token>:<slot_id>` so the artist
    calendar page can auto-trigger the in-app Book flow (which already
    handles contract signing). artist_id is left out — the in-app page
    derives it from the artist's session + the calendar selector.
    """
    sid_part = f":{int(slot_id)}" if slot_id else ""
    # Audit fix (Jul 1 2026 regression sweep): venue_name is venue-controlled
    # input and was previously interpolated raw — same XSS class the Jul 1
    # frontend batch closed. gig_date is a canonical YYYY-MM-DD string but
    # escape defensively.
    safe_venue = _h_esc(venue_name or 'The venue')
    safe_date = _h_esc(str(gig_date or '')[:10])
    safe_link = _h_esc(f"/app/artist-book-gigs.html#hold_book={token}{sid_part}")
    inner = f"""<h1 style="color:#a78bfa;">Sign your contract to confirm</h1>
<p style="margin:0 0 16px 0;color:#cbd5e1;font-size:0.95rem;">
  <b>{safe_venue}</b> requires a contract on booking for the gig on <b>{safe_date}</b>.
  We've taken you out of the email flow so you can review and sign it inside GigsFill.
</p>
<p style="margin:0 0 20px 0;color:#94a3b8;font-size:0.86rem;">
  Sign in (if you aren't already), then click the gig on your calendar — the contract opens
  automatically. Your hold offer is still in flight; nothing's been booked yet.
</p>
<a href="{safe_link}" style="display:inline-block;background:#7c6bff;color:#fff;text-decoration:none;
   padding:11px 22px;border-radius:6px;font-size:0.92rem;font-weight:600;">
  Open My Calendar to Sign &amp; Book
</a>
<p style="margin:18px 0 0 0;color:#94a3b8;font-size:0.78rem;">
  Or, if you'd rather decline, you can do that from your inbox — the email's red Decline button
  still works.
</p>"""
    return _hold_page_shell(title="Sign your contract to confirm", inner_html=inner, accent="#a78bfa")


@router.get("/hold/respond/{token}", include_in_schema=False)
def hold_respond_landing(token: str, db=Depends(get_db)):
    """Artist clicks the green Accept button in the email. Two flows:
       - Single open slot: book it immediately + show a confirmation page
       - Multi-slot: show a slot picker page so the artist chooses

    Returns HTML directly (not a JSON response) since this is a public
    email-link endpoint, not a JS-driven page.
    """
    from sqlalchemy import text
    from fastapi.responses import HTMLResponse
    from backend.services.gig_hold import (
        respond_to_hold_offer, _list_open_hold_slots,
        _list_matching_open_slots, _fmt_time,
    )

    # Look up the offer to see how many slots are open
    row = db.execute(
        text("""SELECT wl.gig_id, wl.artist_id, wl.offer_expires_at, wl.offer_declined,
                       g.hold_status, g.date, g.title, g.is_multi_slot,
                       g.venue_id, v.venue_name
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                JOIN venues v ON v.id = g.venue_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'
                LIMIT 1"""),
        {"tok": token}
    ).mappings().first()
    if not row:
        return HTMLResponse(_hold_simple_page(
            "Link no longer valid",
            "This offer link isn't valid. It may have been replaced by a newer one — check your inbox for the latest."
        ))
    if row["offer_declined"]:
        return HTMLResponse(_hold_simple_page(
            "Already responded",
            "Thanks — you've already responded to this offer."
        ))
    if row["hold_status"] != "active":
        return HTMLResponse(_hold_simple_page(
            "Offer no longer active",
            "This hold is no longer active. The venue may have cancelled or opened the gig already."
        ))
    # Show ONLY slots this artist can fill. For a mixed-type multi-slot
    # gig (e.g. Slot 1 = Live Band, Slot 2 = DJ), a DJ artist only sees
    # the DJ slot in the picker. Mirrors backend service filter.
    open_slots = _list_matching_open_slots(db, row["gig_id"], row["artist_id"])
    if not open_slots:
        any_open = _list_open_hold_slots(db, row["gig_id"])
        if not any_open:
            return HTMLResponse(_hold_simple_page(
                "All slots booked",
                "Every slot on this gig has already been booked. Thanks for the quick response — sorry we missed you on this one."
            ))
        return HTMLResponse(_hold_simple_page(
            "No matching slots",
            "None of the open slots match your artist type. We've let the venue know — they'll move on to the next artist on their list."
        ))

    # Contract-required redirect (Jun 2026): if the venue requires a
    # contract on booking, the email-link path can't sign one — punt
    # the artist to the in-app calendar where the existing signing
    # modal runs. Done BEFORE the single-slot fast-book to avoid
    # booking before the contract is signed.
    if _venue_requires_contract_for_booking(db, row["venue_id"], row["artist_id"]):
        # Single-slot: pass the slot id in the deep link so the calendar
        # can auto-trigger the right Book button. Multi-slot: omit the
        # slot id and let the artist pick from the in-app Pending Offer
        # panel (which shows all matching slots).
        slot_for_link = int(open_slots[0]["id"]) if len(open_slots) == 1 else None
        return HTMLResponse(_hold_redirect_to_contract_landing(
            token, row["gig_id"], slot_for_link,
            row["venue_name"], str(row["date"])
        ))

    # Single-slot path: book + confirm in one shot
    if len(open_slots) == 1:
        result = respond_to_hold_offer(db, token=token, action="accept", slot_id=int(open_slots[0]["id"]))
        if result.get("ok"):
            s = open_slots[0]
            return HTMLResponse(_hold_simple_page(
                "Booked!",
                f"You're confirmed for <b>{row['venue_name']}</b> on <b>{row['date']}</b> ({_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}). The venue's been notified.",
                color="#059669"
            ))
        return HTMLResponse(_hold_simple_page("Something went wrong", result.get("message") or "Please try again or contact support."))

    # Multi-slot: show a picker — precompute effective pay (override
    # for flat slots) so the picker renders what the artist will actually
    # be paid.
    from backend.services.gig_hold import _effective_pay_for_artist as _heff
    slots_for_picker = []
    for s in open_slots:
        d = dict(s)
        d["effective_pay"] = _heff(db, s, row["venue_id"], row["artist_id"])
        slots_for_picker.append(d)
    return HTMLResponse(_hold_slot_picker_page(token, row, slots_for_picker))


def _resolve_hold_token(db, token: str):
    """Look up a per-gig hold offer-token's artist + venue + date without
    firing the decline. Returns a mapping or None."""
    return db.execute(
        text("""SELECT wl.artist_id, wl.gig_id, wl.offer_declined,
                       g.date, g.venue_id,
                       v.venue_name,
                       a.name as artist_name,
                       a.user_id as artist_user_id
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                JOIN venues v ON v.id = g.venue_id
                JOIN artists a ON a.id = wl.artist_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'
                LIMIT 1"""),
        {"tok": token}
    ).mappings().first()


@router.get("/hold/decline/{token}", include_in_schema=False)
def hold_decline_landing(token: str,
                         user=Depends(get_optional_user),
                         db=Depends(get_db)):
    """Email-link landing for per-gig hold decline.

    Prefetch defense (Jul 1 2026, mirrors the series-hold Y3 fix): Gmail/
    Outlook/Slack auto-GET links in incoming mail to render previews. A
    one-click GET-that-mutates would fire the decline before the artist
    ever clicked. Behavior:

      - Logged-in caller who owns the token's artist → execute decline
        immediately (one-click as before).
      - Anyone else → render a confirmation page whose POST form performs
        the actual decline. GET no longer mutates state in the un-auth'd
        case, so prefetchers can't trigger anything.
    """
    from fastapi.responses import HTMLResponse
    from backend.services.gig_hold import respond_to_hold_offer
    info = _resolve_hold_token(db, token)
    if not info:
        return HTMLResponse(_hold_simple_page(
            "Offer not found",
            "This hold link is invalid or has already been cleaned up.",
        ), status_code=404)
    if int(info["offer_declined"] or 0) == 1:
        return HTMLResponse(_hold_simple_page(
            "Already responded",
            "This offer has already been handled.",
        ), status_code=410)

    artist_owns_caller = bool(user) and int(user.id) == int(info["artist_user_id"] or 0)
    if not artist_owns_caller and user:
        # entity_users fallback (managed-artist users)
        try:
            _euser = db.execute(
                text("""SELECT 1 FROM entity_users
                        WHERE entity_type='artist' AND entity_id=:aid AND user_id=:uid"""),
                {"aid": info["artist_id"], "uid": user.id}
            ).first()
            artist_owns_caller = bool(_euser)
        except Exception:
            pass

    if artist_owns_caller:
        result = respond_to_hold_offer(db, token=token, action="decline")
        if result.get("ok"):
            return HTMLResponse(_hold_simple_page(
                "Thanks for letting us know",
                "We've told the venue — they'll offer the gig to the next artist on their list. Catch you on the next one.",
                color="#6b7280"
            ))
        return HTMLResponse(_hold_simple_page(
            "Already responded",
            result.get("message") or "This offer has already been handled."
        ))

    # Un-auth'd or wrong-artist caller → show a confirmation form. Uses
    # _hold_page_shell directly (rather than _hold_simple_page) because
    # the body contains a <form> which can't nest inside the shell's <p>.
    safe_venue = _h_esc(info.get("venue_name") or "the venue")
    safe_artist = _h_esc(info.get("artist_name") or "you")
    safe_date = _h_esc(str(info.get("date") or "")[:10])
    inner = (
        f"<h1 style='color:#f8fafc;'>Confirm: decline this hold offer?</h1>"
        f"<p style='margin:0 0 14px;color:#cbd5e1;font-size:0.95rem;line-height:1.6;'>"
        f"You're about to decline {safe_venue}'s hold offer for "
        f"<strong>{safe_date}</strong> on behalf of <strong>{safe_artist}</strong>.</p>"
        f"<form method='POST' action='/hold/decline/{_h_esc(token)}' style='margin-top:18px;'>"
        f"  <button type='submit' style='padding:10px 22px;background:#ef4444;color:#fff;border:0;border-radius:6px;font-weight:600;cursor:pointer;font-size:14px;'>Yes, decline this offer</button>"
        f"</form>"
        f"<p style='margin-top:16px;font-size:13px;color:#6b7280;'>If this wasn't you, close this tab — nothing has been declined yet.</p>"
    )
    return HTMLResponse(_hold_page_shell(
        title="Confirm decline",
        inner_html=inner,
        accent="#ef4444",
    ))


@router.post("/hold/decline/{token}", include_in_schema=False)
def hold_decline_submit(token: str, db=Depends(get_db)):
    """Form-POST target for the confirmation landing. Fires the actual
    decline only after the user explicitly clicks the button — GET
    prefetchers can't reach this path."""
    from fastapi.responses import HTMLResponse
    from backend.services.gig_hold import respond_to_hold_offer
    result = respond_to_hold_offer(db, token=token, action="decline")
    if result.get("ok"):
        return HTMLResponse(_hold_simple_page(
            "Thanks for letting us know",
            "We've told the venue — they'll offer the gig to the next artist on their list. Catch you on the next one.",
            color="#6b7280"
        ))
    return HTMLResponse(_hold_simple_page(
        "Already responded",
        result.get("message") or "This offer has already been handled."
    ))


@router.post("/hold/accept/{token}", include_in_schema=False)
def hold_accept_slot(token: str, request: Request,
                     slot_id: int = Query(...), db=Depends(get_db)):
    """Accept a hold offer for a specific slot.

    Dual-mode response based on Accept header:
      - Accept: application/json  → JSON {ok, message} (used by the
        artist banner so it can surface backend error messages like
        "Stripe not set up" / "Frequency conflict" rather than the
        generic "✗ Failed" toast).
      - Anything else → HTML landing page (used by the email link's
        multi-slot picker form which expects a rendered confirmation
        on the next request).
    """
    from fastapi.responses import HTMLResponse, JSONResponse
    from backend.services.gig_hold import respond_to_hold_offer
    wants_json = "application/json" in (request.headers.get("accept") or "").lower()

    # Contract-required redirect (Jun 2026): the multi-slot picker
    # form submits here. Before booking, check if the venue requires
    # a contract on booking. If so:
    #   - JSON caller (in-app banner): the banner already does its
    #     own contract check + signing modal upstream, so it should
    #     never reach this branch. We still respond with a clear
    #     error so any future caller surfaces it.
    #   - HTML caller (email-link picker): redirect to the in-app
    #     calendar so the artist can sign + book through the existing
    #     flow.
    ctx_row = db.execute(
        text("""SELECT wl.gig_id, wl.artist_id,
                       g.venue_id, g.date, v.venue_name
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                JOIN venues v ON v.id = g.venue_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'
                LIMIT 1"""),
        {"tok": token}
    ).mappings().first()
    if ctx_row and _venue_requires_contract_for_booking(db, ctx_row["venue_id"], ctx_row["artist_id"]):
        if wants_json:
            return JSONResponse(
                content={"ok": False, "requires_contract": True,
                         "message": "This venue requires a contract on booking. Use the in-app Pending Offer panel to sign."},
                status_code=409,
            )
        return HTMLResponse(_hold_redirect_to_contract_landing(
            token, ctx_row["gig_id"], int(slot_id),
            ctx_row["venue_name"], str(ctx_row["date"])
        ))

    result = respond_to_hold_offer(db, token=token, action="accept", slot_id=int(slot_id))
    if wants_json:
        # Use the 4xx range on failures so the banner's res.ok branch
        # also flips false (defense-in-depth alongside the explicit
        # {ok: false} body check on the frontend).
        status_code = 200 if result.get("ok") else 400
        return JSONResponse(content=result, status_code=status_code)
    if result.get("ok"):
        return HTMLResponse(_hold_simple_page(
            "Booked!",
            "You're confirmed. The venue's been notified.",
            color="#059669"
        ))
    return HTMLResponse(_hold_simple_page("Could not book", result.get("message") or "Please try again."))


def _hold_page_shell(title: str, inner_html: str, accent: str = "#a78bfa") -> str:
    """Dark-themed landing page matching the site (bg #0a0e17, card
    #151b28, purple accent). Replaced the prior white-on-grey email
    look (Jun 2026) since clicking an email link should land on a
    page that feels like GigsFill, not a transactional-email card."""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>{title} — GigsFill</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  body{{margin:0;padding:0;background:#0a0e17;color:#f8fafc;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;line-height:1.5;}}
  a{{color:{accent};}}
  .hold-card{{max-width:580px;margin:48px auto;background:#151b28;border:1px solid rgba(148,163,184,0.12);border-radius:12px;overflow:hidden;}}
  .hold-card-header{{padding:24px 32px;border-bottom:1px solid rgba(148,163,184,0.12);}}
  .hold-card-body{{padding:28px 32px;}}
  .hold-card-footer{{padding:18px 32px;border-top:1px solid rgba(148,163,184,0.10);text-align:center;}}
  .hold-card-footer p{{margin:0;color:#94a3b8;font-size:12px;}}
  .hold-card-footer a{{color:#a78bfa;text-decoration:none;}}
  h1{{margin:0 0 12px 0;font-size:1.4rem;font-weight:700;color:#f8fafc;}}
  .accent{{color:{accent};font-weight:700;}}
  .muted{{color:#94a3b8;font-size:0.86rem;}}
</style>
</head>
<body>
<div class="hold-card">
  <div class="hold-card-header">
    <img src="https://gigsfill.com/app/static/img/gigsfill-logo.png" alt="GigsFill" width="160" style="display:block;height:auto;">
  </div>
  <div class="hold-card-body">
    {inner_html}
  </div>
  <div class="hold-card-footer">
    <p>&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com">gigsfill.com</a></p>
  </div>
</div>
</body></html>"""


def _hold_simple_page(title: str, body_html: str, color: str = "#a78bfa") -> str:
    """Simple confirmation page in the dark site theme."""
    inner = f"""<h1 style="color:{color};">{title}</h1>
<p style="margin:0;color:#cbd5e1;font-size:0.95rem;line-height:1.6;">{body_html}</p>"""
    return _hold_page_shell(title=title, inner_html=inner, accent=color)


def _hold_slot_picker_page(token: str, row, open_slots) -> str:
    """Multi-slot landing page in the dark site theme. Each slot is a
    purple-bordered card with one 'Book' button + a Decline link at
    the bottom."""
    from backend.services.gig_hold import _fmt_time
    def _esc(s):
        return str(s or '').replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")
    venue_name = _esc(row["venue_name"] or "the venue")
    # Hyperlink the venue name to their public profile so the artist
    # can size up the venue before accepting (per user request).
    venue_id = None
    try:
        from sqlalchemy import text as _vt
        venue_id = row["venue_id"] if "venue_id" in row.keys() else None
    except Exception:
        pass
    if venue_id:
        venue = (f'<a href="https://gigsfill.com/app/venue-profile.html?venue_id={int(venue_id)}" '
                 f'target="_blank" rel="noopener" '
                 f'style="color:#a78bfa;text-decoration:none;border-bottom:1px dashed rgba(167,139,250,0.45);font-weight:700;">'
                 f'{venue_name}</a>')
    else:
        venue = venue_name
    date = _esc(str(row["date"]))
    title_html = ""
    if row.get("title"):
        title_html = f'<div class="muted" style="margin-bottom:4px;">"{_esc(row["title"])}"</div>'
    rows_html = ""
    for s in open_slots:
        time_str = f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}"
        # Unified override rule (Jun 2026): use the precomputed
        # effective_pay (which already applies the override to both
        # flat and door slots) as the dollar amount. Door slots
        # additionally append the door%.
        eff = float(s.get("effective_pay") if s.get("effective_pay") is not None else (s.get("pay") or 0))
        eff_fmt = f"${int(eff)}" if eff == int(eff) else f"${eff:.2f}"
        if (s.get("deal_type") or "flat").lower() == "door":
            pay_html = f"{eff_fmt} + {int(s.get('door_pct') or 0)}% door"
        else:
            pay_html = eff_fmt
        rows_html += f"""
<form method="POST" action="/hold/accept/{token}?slot_id={int(s['id'])}" style="margin:0 0 10px 0;">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;background:rgba(124,107,255,0.08);border:1px solid rgba(124,107,255,0.35);border-radius:8px;padding:14px 16px;">
    <div>
      <div style="font-size:0.95rem;font-weight:600;color:#f8fafc;">Slot {s['slot_number']}</div>
      <div style="font-size:0.86rem;color:#cbd5e1;margin-top:2px;">{time_str}  ·  <span style="color:#22c55e;font-weight:700;">{pay_html}</span></div>
    </div>
    <button type="submit"
      title="Book this slot now."
      style="background:#7c6bff;color:#fff;border:none;padding:9px 18px;border-radius:6px;font-size:0.86rem;font-weight:600;cursor:pointer;">Book this slot</button>
  </div>
</form>"""
    inner = f"""<h1 style="color:#fcd34d;">Pick the slot you want</h1>
<p style="margin:0 0 14px 0;color:#cbd5e1;font-size:0.95rem;">
  <span class="accent">{venue}</span> &middot; <span style="color:#f8fafc;font-weight:600;">{date}</span>
</p>
{title_html}
<div class="muted" style="margin-bottom:18px;">{len(open_slots)} open slot{'s' if len(open_slots) != 1 else ''} — pick the one you want.</div>
{rows_html}
<p style="margin:18px 0 0 0;font-size:0.84rem;color:#94a3b8;">
  Or <a href="/hold/decline/{token}" style="color:#f87171;text-decoration:underline;font-weight:600;"
      title="Decline if you are unable to perform on this day.">decline this offer</a>
  and we'll move on to the next artist.
</p>"""
    return _hold_page_shell(title="Pick the slot you want", inner_html=inner, accent="#fcd34d")


# ============================================================
# HOLD GIG — Phase 4: venue management endpoints
# ============================================================
# Venue sees the live state of the hold + can intervene mid-cycle.
# All require check_venue_access (state-changing — only the venue
# that created the gig can touch the hold).

@router.get("/api/gigs/{gig_id}/hold-status")
def get_hold_status(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Returns the live hold state + waitlist for venue UI rendering.
    Read-only; safe to poll. Per-row state derived from gig_waitlist
    flags + open slot count, so the venue sees who's been offered,
    who declined/expired, who's still queued, who accepted (and into
    which slot)."""
    from backend.utils import check_venue_access
    gig = db.execute(
        text("""SELECT id, venue_id, hold_status, hold_offer_window_hours,
                       date, status, recurring_group_id
                FROM gigs WHERE id = :gid"""),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if not gig["hold_status"]:
        return {"is_held": False}

    # Waitlist with per-row state derivation
    rows = db.execute(
        text("""SELECT wl.artist_id, wl.position, wl.offer_sent,
                       wl.offer_sent_at, wl.offer_expires_at,
                       wl.offer_declined, wl.reminder_sent_at,
                       COALESCE(wl.added_post_creation, 0) as added_post_creation,
                       a.name as artist_name
                FROM gig_waitlist wl
                LEFT JOIN artists a ON a.id = wl.artist_id
                WHERE wl.gig_id = :gid AND wl.source = 'hold'
                ORDER BY wl.position ASC"""),
        {"gid": gig_id}
    ).mappings().all()

    # Which artists actually booked a slot via the hold? Cross-reference
    # gig_slots — any booked slot whose artist_id is in our waitlist
    # was an acceptance.
    slot_rows = db.execute(
        text("""SELECT gs.id, gs.slot_number, gs.start_time, gs.end_time,
                       gs.pay, gs.status, gs.artist_id,
                       a.name as artist_name
                FROM gig_slots gs LEFT JOIN artists a ON a.id = gs.artist_id
                WHERE gs.gig_id = :gid ORDER BY gs.slot_number"""),
        {"gid": gig_id}
    ).mappings().all()
    accepted_artist_ids = set(
        s["artist_id"] for s in slot_rows
        if s["status"] == "booked" and s["artist_id"]
    )

    from datetime import datetime as _dt
    now = _dt.utcnow()
    window_h = int(gig.get("hold_offer_window_hours") or 24)
    # For series gigs, gather every artist who booked ANOTHER gig in the
    # same series so we can label declined rows as "booked elsewhere"
    # (the artist accepted the series, just not THIS specific date).
    series_booked_by_artist = set()
    if gig.get("recurring_group_id"):
        for r in db.execute(
            text("""SELECT DISTINCT gs.artist_id, g2.id as g2_id, g2.date
                    FROM gig_slots gs JOIN gigs g2 ON g2.id = gs.gig_id
                    WHERE g2.recurring_group_id = :rgid
                      AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                      AND gs.artist_id IS NOT NULL
                      AND g2.id != :gid"""),
            {"rgid": gig["recurring_group_id"], "gid": gig_id}
        ).mappings().all():
            series_booked_by_artist.add((int(r["artist_id"]), int(r["g2_id"]), str(r["date"])[:10]))
    waitlist_out = []
    current_offer = None
    for r in rows:
        state = None
        state_reason = None
        if r["artist_id"] in accepted_artist_ids:
            state = "accepted"
        elif not r["offer_sent"]:
            state = "queued"
            # Frequency check for queued artists: compute closest existing
            # booking at this venue (any direction). If within freq_days,
            # surface so the venue knows this artist can't accept even
            # when their position comes up.
            try:
                _fr = db.execute(
                    text("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) AS fd
                            FROM venues v
                            LEFT JOIN preferred_artists pa
                              ON pa.venue_id = v.id AND pa.artist_id = :aid
                            WHERE v.id = :vid"""),
                    {"aid": r["artist_id"], "vid": gig["venue_id"]}
                ).scalar() or 0
                if int(_fr) > 0:
                    _cb = db.execute(
                        text("""SELECT g3.date
                                FROM gigs g3 LEFT JOIN gig_slots gs2 ON gs2.gig_id = g3.id
                                WHERE g3.venue_id = :vid AND g3.id != :gid
                                  AND (g3.artist_id = :aid
                                       OR (gs2.artist_id = :aid
                                           AND gs2.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')))
                                ORDER BY ABS(julianday(g3.date) - julianday(:gdate)) ASC LIMIT 1"""),
                        {"vid": gig["venue_id"], "gid": gig_id, "aid": r["artist_id"],
                         "gdate": str(gig["date"])[:10]}
                    ).mappings().first()
                    if _cb:
                        _d1 = _dt.strptime(str(gig["date"])[:10], "%Y-%m-%d").date()
                        _d2 = _dt.strptime(str(_cb["date"])[:10], "%Y-%m-%d").date()
                        _diff = (_d1 - _d2).days
                        if abs(_diff) <= int(_fr):
                            state_reason = {
                                "kind": "freq_blocked",
                                "days_apart": int(abs(_diff)),
                                "direction": "before" if _diff > 0 else "after",
                                "other_date": str(_cb["date"])[:10],
                                "required": int(_fr),
                            }
            except Exception:
                pass
        elif r["offer_declined"]:
            # Distinguish actual decline / expiry / series-accept-elsewhere.
            # Series first: if the artist booked another gig in the same
            # series, this is "Booked another date" — not a real decline.
            _series_other = None
            if gig.get("recurring_group_id"):
                _other = [(g2id, d) for (aid, g2id, d) in series_booked_by_artist
                          if aid == int(r["artist_id"])]
                if _other:
                    _series_other = _other[0]
            if _series_other:
                state = "booked_elsewhere"
                state_reason = {
                    "kind": "booked_elsewhere",
                    "other_gig_id": _series_other[0],
                    "other_date": _series_other[1],
                }
            else:
                # Expiry vs explicit decline. offer_sent_at + window_h is
                # the original natural expiry. If offer_expires_at is at
                # least 95% of that window from offer_sent_at, it's the
                # natural-expiry case (sweep marked it). Otherwise the
                # artist (or backend cleanup) explicitly consumed it
                # earlier.
                _natural_expiry = False
                try:
                    _sent = _dt.strptime(str(r["offer_sent_at"]).replace("T"," ").split(".")[0], "%Y-%m-%d %H:%M:%S")
                    _exp = _dt.strptime(str(r["offer_expires_at"]).replace("T"," ").split(".")[0], "%Y-%m-%d %H:%M:%S")
                    _gap_h = (_exp - _sent).total_seconds() / 3600
                    if _gap_h >= window_h * 0.95:
                        _natural_expiry = True
                except Exception:
                    pass
                if _natural_expiry:
                    state = "expired"
                else:
                    state = "declined"
        else:
            state = "current_offer"
        hours_remaining = None
        if r["offer_expires_at"] and state == "current_offer":
            try:
                exp = _dt.strptime(str(r["offer_expires_at"]).replace("T"," ").split(".")[0], "%Y-%m-%d %H:%M:%S")
                hours_remaining = max(0, round((exp - now).total_seconds() / 3600, 1))
            except Exception:
                pass
        row_out = {
            "artist_id": r["artist_id"],
            "artist_name": r["artist_name"],
            "position": r["position"],
            "state": state,
            "state_reason": state_reason,
            "offer_sent_at": str(r["offer_sent_at"]) if r["offer_sent_at"] else None,
            "offer_expires_at": str(r["offer_expires_at"]) if r["offer_expires_at"] else None,
            "hours_remaining": hours_remaining,
            "reminder_sent": bool(r["reminder_sent_at"]),
            "added_post_creation": bool(r["added_post_creation"]),
        }
        if state == "current_offer":
            current_offer = row_out
        waitlist_out.append(row_out)

    open_slots = [s for s in slot_rows if s["status"] == "open"]
    booked_slots = [s for s in slot_rows if s["status"] == "booked"]

    # Per-bucket state (Jun 2026 — parallel-per-type rotation, Option A).
    # One bucket per distinct open-slot artist_type. Each bucket's state:
    #   'active'        : has an in-flight offer OR remaining candidates
    #   'exhausted'     : at least one artist of this type WAS queued but
    #                     all of them either declined / expired
    #   'no_candidates' : NO artist of this type was ever queued (venue
    #                     forgot to add anyone of that type)
    # Frontend renders a per-bucket notice for non-active buckets so the
    # venue can act on the dead bucket without waiting for the rest of
    # the rotation to finish.
    buckets = []
    _open_types = sorted({s["artist_type"] for s in open_slots if s.get("artist_type")})
    for _t in _open_types:
        _bucket_slots = [s for s in open_slots if s.get("artist_type") == _t]
        # In-flight offer to an artist of this type, if any
        _in_flight_row = db.execute(
            text("""SELECT wl.artist_id, wl.offer_sent_at, wl.offer_expires_at,
                           wl.reminder_sent_at, a.name as artist_name
                    FROM gig_waitlist wl
                    JOIN artists a ON a.id = wl.artist_id
                    WHERE wl.gig_id = :gid AND wl.source = 'hold'
                      AND wl.offer_sent = 1 AND wl.offer_declined = 0
                      AND a.artist_type = :t
                    LIMIT 1"""),
            {"gid": gig_id, "t": _t}
        ).mappings().first()
        _in_flight = None
        if _in_flight_row:
            _ifh_remaining = None
            if _in_flight_row["offer_expires_at"]:
                try:
                    _exp = _dt.strptime(
                        str(_in_flight_row["offer_expires_at"]).replace("T"," ").split(".")[0],
                        "%Y-%m-%d %H:%M:%S"
                    )
                    _ifh_remaining = max(0, round((_exp - now).total_seconds() / 3600, 1))
                except Exception:
                    pass
            _in_flight = {
                "artist_id": _in_flight_row["artist_id"],
                "artist_name": _in_flight_row["artist_name"],
                "offer_expires_at": str(_in_flight_row["offer_expires_at"]) if _in_flight_row["offer_expires_at"] else None,
                "hours_remaining": _ifh_remaining,
                "reminder_sent": bool(_in_flight_row["reminder_sent_at"]),
            }
        # Remaining (un-offered, un-declined) candidates for this type
        _remaining_cnt = db.execute(
            text("""SELECT COUNT(*) FROM gig_waitlist wl
                    JOIN artists a ON a.id = wl.artist_id
                    WHERE wl.gig_id = :gid AND wl.source = 'hold'
                      AND wl.offer_sent = 0 AND wl.offer_declined = 0
                      AND a.artist_type = :t"""),
            {"gid": gig_id, "t": _t}
        ).scalar() or 0
        # Has any artist of this type ever been queued?
        _ever_queued = db.execute(
            text("""SELECT 1 FROM gig_waitlist wl
                    JOIN artists a ON a.id = wl.artist_id
                    WHERE wl.gig_id = :gid AND wl.source = 'hold'
                      AND a.artist_type = :t LIMIT 1"""),
            {"gid": gig_id, "t": _t}
        ).first()
        if _in_flight or _remaining_cnt > 0:
            _state = "active"
        elif _ever_queued:
            _state = "exhausted"
        else:
            _state = "no_candidates"
        buckets.append({
            "artist_type": _t,
            "slot_count": len(_bucket_slots),
            "slot_ids": [s["id"] for s in _bucket_slots],
            "in_flight": _in_flight,
            "remaining_candidates": int(_remaining_cnt),
            "state": _state,
        })

    return {
        "is_held": True,
        "hold_status": gig["hold_status"],
        "offer_window_hours": gig["hold_offer_window_hours"] or 24,
        "current_offer": current_offer,
        "waitlist": waitlist_out,
        "buckets": buckets,
        "open_slot_count": len(open_slots),
        "booked_slot_count": len(booked_slots),
        "open_slots": [dict(s) for s in open_slots],
        "booked_slots": [dict(s) for s in booked_slots],
        "venue_id": gig["venue_id"],
        # gig date so the venue picker can request the preferred-artists
        # endpoint with for_gig_date and surface per-artist freq status
        # (Jun 2026 — Hold-Gig add-artist UX).
        "date": str(gig["date"])[:10] if gig.get("date") else None,
        # Series membership so the post-create add-artist picker in
        # hold-mgmt.js can render the correct freq disclaimer: in
        # series mode the freq rule is NOT waived (bundled-offer modal
        # greys out conflicting dates), so the "waive for this gig only"
        # copy would be wrong. Truthy = part of a recurring series.
        "recurring_group_id": gig.get("recurring_group_id") or None,
    }


@router.post("/api/gigs/{gig_id}/hold/start")
def start_hold(gig_id: int, data: dict,
               user=Depends(get_current_user), db=Depends(get_db)):
    """Add a Hold to an existing gig the venue created without one.

    Body:
      artist_ids: ordered list of preferred-artist ids (top of list
                  goes first). Required, non-empty.
      hold_email_artists: bool (default True) — send the first offer
                          immediately vs. just stage the waitlist.
      hold_offer_window_hours: int (default 24).

    Refuses if the gig is already held, fully booked, or has no open
    slots. Otherwise delegates to create_hold_waitlist (same code path
    as create-time hold setup).
    """
    from backend.utils import check_venue_access
    from backend.services.gig_hold import create_hold_waitlist
    gig = db.execute(
        text("SELECT id, venue_id, hold_status, status FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if gig["hold_status"]:
        raise HTTPException(400, "This gig already has a hold in progress.")
    if gig["status"] in ("booked", "cancelled"):
        raise HTTPException(400, f"Can't start a hold on a {gig['status']} gig.")

    # Must have at least one OPEN slot — hold offers slots to artists,
    # nothing to offer if every slot is already booked.
    open_count = db.execute(
        text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
        {"gid": gig_id}
    ).scalar() or 0
    if open_count == 0:
        raise HTTPException(400, "This gig has no open slots to hold.")

    artist_ids = data.get("artist_ids") or []
    if not artist_ids:
        raise HTTPException(400, "Pick at least one artist for the hold list.")
    try:
        artist_ids = [int(a) for a in artist_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid artist id in hold list.")

    # Refuse to add an artist who's ALREADY booked into one of this
    # gig's slots — accepting a hold offer for an additional slot
    # would put the same artist on two slots of the same gig (and
    # the per-slot pre-book gates can't catch this since the second
    # slot is genuinely open). Catch it up-front with a clear message.
    already_booked = db.execute(
        text("""SELECT DISTINCT gs.artist_id, a.name
                FROM gig_slots gs
                LEFT JOIN artists a ON a.id = gs.artist_id
                WHERE gs.gig_id = :gid
                  AND gs.status IN ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval')
                  AND gs.artist_id IS NOT NULL"""),
        {"gid": gig_id}
    ).mappings().all()
    booked_ids = {int(r["artist_id"]) for r in already_booked}
    conflicting = [int(a) for a in artist_ids if int(a) in booked_ids]
    if conflicting:
        names = {int(r["artist_id"]): (r["name"] or f"Artist #{r['artist_id']}") for r in already_booked}
        nlist = ", ".join(names.get(a, f"Artist #{a}") for a in conflicting)
        raise HTTPException(400,
            f"Can't add {nlist} to the hold — they're already booked on a slot of this gig.")

    create_hold_waitlist(
        db,
        gig_id=gig_id,
        artist_ids=artist_ids,
        send_email_now=bool(data.get("hold_email_artists", True)),
        offer_window_hours=int(data.get("hold_offer_window_hours") or 24),
    )
    logger.info(f"[HOLD] gig={gig_id} hold STARTED post-creation by user={user.id} with {len(artist_ids)} artist(s)")
    return {"ok": True, "message": "Hold started.", "artist_count": len(artist_ids)}


@router.post("/api/gigs/{gig_id}/hold/release")
def release_hold(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Venue chooses to abandon the hold and open the gig to all.
    Used from BOTH the active-hold state (early release) and the
    exhausted state (post-waitlist 'Open to All' button). Cancels
    any in-flight offer + clears hold_status. Existing booking flow
    handles the now-open gig from here.
    """
    from backend.utils import check_venue_access
    gig = db.execute(
        text("SELECT venue_id, hold_status FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if not gig["hold_status"]:
        return {"ok": True, "noop": True, "message": "This gig isn't held."}
    # Mark all in-flight offers as declined so the scheduler doesn't
    # try to advance after release.
    db.execute(
        text("""UPDATE gig_waitlist SET offer_declined = 1
                WHERE gig_id = :gid AND source = 'hold' AND offer_declined = 0"""),
        {"gid": gig_id}
    )
    db.execute(
        text("UPDATE gigs SET hold_status = NULL WHERE id = :gid"),
        {"gid": gig_id}
    )
    db.commit()
    logger.info(f"[HOLD] gig={gig_id} released to open by venue user={user.id}")
    return {"ok": True, "message": "Hold released. Gig is now open to all artists."}


@router.post("/api/gigs/{gig_id}/hold/skip-current")
def skip_current_offer(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Force-advance: mark the current in-flight offer as declined +
    immediately offer to next artist. Useful when venue talks to the
    current artist offline ('hey can you confirm?' → 'no'), saves the
    24h wait."""
    from backend.utils import check_venue_access
    from backend.services.gig_hold import send_next_hold_offer
    gig = db.execute(
        text("SELECT venue_id, hold_status FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if gig["hold_status"] != "active":
        raise HTTPException(400, "No active offer to skip")
    db.execute(
        text("""UPDATE gig_waitlist SET offer_declined = 1
                WHERE gig_id = :gid AND source = 'hold'
                  AND offer_sent = 1 AND offer_declined = 0"""),
        {"gid": gig_id}
    )
    db.commit()
    # Jun 2026: send_next_hold_offer now returns a LIST (parallel-per-type
    # rotation). Multiple artists may have been offered in one call when
    # the gig has multiple open slot types. For the legacy single-id
    # response shape, surface the first one if any fired.
    next_ids = send_next_hold_offer(db, gig_id) or []
    return {"ok": True,
            "next_artist_id": next_ids[0] if next_ids else None,
            "next_artist_ids": next_ids,
            "message": ("Moved on to next artist." if next_ids
                        else "Waitlist exhausted — see the gig modal for next steps.")}


@router.post("/api/gigs/{gig_id}/hold/reorder")
def reorder_hold_waitlist(gig_id: int, data: dict,
                          user=Depends(get_current_user), db=Depends(get_db)):
    """Sync the venue-added portion of the hold waitlist.

    Originally-locked rows (added_post_creation=0, set at gig
    creation) are NEVER touched — venue picked the initial order
    and it stays put. This endpoint only manages the rows added
    AFTER creation via the '+ Add artist' affordance:

      - For each artist_id in body.queued_ids that already exists
        with added_post_creation=1 → UPDATE position (reorder).
      - For each artist_id NOT yet on the list → INSERT with
        added_post_creation=1 (add).
      - For each existing added_post_creation=1 row NOT in
        queued_ids → DELETE (remove).

    Body: {"queued_ids": [artist_id, artist_id, ...]}  — the desired
    full ordered list of POST-CREATION artists (in their new order).
    """
    from backend.utils import check_venue_access
    gig = db.execute(
        text("SELECT venue_id, hold_status FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if gig["hold_status"] != "active":
        raise HTTPException(400, "Hold not active")
    new_ids = [int(x) for x in (data.get("queued_ids") or [])]

    # All artist IDs already on this gig's hold waitlist (any state).
    # Used to (a) prevent duplicating an originally-locked artist,
    # (b) decide whether each new_id is already in our added pool.
    existing = db.execute(
        text("""SELECT artist_id, added_post_creation, id
                FROM gig_waitlist
                WHERE gig_id = :gid AND source = 'hold'"""),
        {"gid": gig_id}
    ).mappings().all()
    locked_ids = {r["artist_id"] for r in existing if not r["added_post_creation"]}
    added_existing = {r["artist_id"]: r["id"] for r in existing if r["added_post_creation"]}

    # Strip out originally-locked artists from new_ids (they can't be
    # in the post-creation pool by definition).
    new_ids = [aid for aid in new_ids if aid not in locked_ids]

    # Compute the position offset — added rows always live AFTER the
    # locked positions so the offer cycle hits locked first.
    max_locked_pos = db.execute(
        text("""SELECT COALESCE(MAX(position), -1) FROM gig_waitlist
                WHERE gig_id = :gid AND source = 'hold'
                  AND added_post_creation = 0"""),
        {"gid": gig_id}
    ).scalar()
    base_pos = (max_locked_pos or -1) + 1

    # Apply the diff
    seen_added = set()
    for i, aid in enumerate(new_ids):
        seen_added.add(aid)
        new_pos = base_pos + i
        if aid in added_existing:
            # Reorder: just update position
            db.execute(
                text("UPDATE gig_waitlist SET position = :p WHERE id = :id"),
                {"p": new_pos, "id": added_existing[aid]}
            )
        else:
            # Add: new row with added_post_creation=1
            try:
                db.execute(
                    text("""INSERT INTO gig_waitlist
                            (gig_id, artist_id, source, position, notified, added_post_creation)
                            VALUES (:gid, :aid, 'hold', :pos, 0, 1)"""),
                    {"gid": gig_id, "aid": aid, "pos": new_pos}
                )
            except Exception as e:
                logger.warning(f"[HOLD] add skip duplicate gig={gig_id} artist={aid}: {e}")
    # Remove: any added row not in the new list.
    # If the row being removed has an offer IN FLIGHT (offer_sent=1,
    # offer_declined=0, not yet expired) the artist's email link stays
    # valid for the offer window — they could click it and end up
    # booking a slot the venue thought they'd been pulled from. Mark
    # the offer declined first so respond_to_hold_offer's offer-state
    # check (services/gig_hold.py:~160) rejects the click cleanly,
    # then advance the rotation to the next artist.
    to_remove = [aid for aid in added_existing if aid not in seen_added]
    advanced_after_remove = False
    for aid in to_remove:
        _in_flight = db.execute(
            text("""SELECT id FROM gig_waitlist
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND source = 'hold' AND added_post_creation = 1
                      AND offer_sent = 1 AND offer_declined = 0
                      AND offer_expires_at > CURRENT_TIMESTAMP"""),
            {"gid": gig_id, "aid": aid}
        ).first()
        if _in_flight:
            # Soft-invalidate the in-flight offer BEFORE the DELETE so
            # any concurrent /hold/respond click for this token hits
            # the offer-no-longer-active branch in respond_to_hold_offer.
            db.execute(
                text("""UPDATE gig_waitlist
                        SET offer_declined = 1,
                            offer_expires_at = CURRENT_TIMESTAMP
                        WHERE gig_id = :gid AND artist_id = :aid
                          AND source = 'hold'"""),
                {"gid": gig_id, "aid": aid}
            )
            advanced_after_remove = True
        db.execute(
            text("""DELETE FROM gig_waitlist
                    WHERE gig_id = :gid AND artist_id = :aid
                      AND source = 'hold' AND added_post_creation = 1"""),
            {"gid": gig_id, "aid": aid}
        )
    db.commit()
    if advanced_after_remove:
        # Hand the offer to the next artist on the list (idempotent — if
        # no next eligible row exists this just marks the hold exhausted).
        try:
            from backend.services.gig_hold import send_next_hold_offer
            send_next_hold_offer(db, gig_id)
        except Exception as _ne:
            logger.warning(f"[HOLD] reorder→advance failed gig={gig_id}: {_ne}")
    return {"ok": True, "added_count": len(new_ids), "removed_count": len(to_remove)}


@router.post("/api/gigs/{gig_id}/hold/cancel-slots-by-type")
def cancel_hold_slots_by_type(gig_id: int, data: dict,
                              user=Depends(get_current_user), db=Depends(get_db)):
    """Cancel every OPEN slot of a given artist_type on a held gig
    (Jun 2026 — parallel-per-type rotation, Option A).

    Surfaces in the venue hold-management panel for "dead" buckets:
    artist_types where the venue forgot to add anyone of that type, or
    where the whole rotation of that type declined/expired. Booked
    slots of the same type are untouched.

    If cancelling these slots leaves NO open slots of any type, the
    gig becomes fully booked or fully cancelled — and `hold_status`
    clears. Otherwise the other-type rotations keep running.

    Body: `{artist_type: 'Live Band' | 'DJ' | ...}`.
    """
    from backend.utils import check_venue_access
    artist_type = (data.get("artist_type") or "").strip()
    if not artist_type:
        raise HTTPException(400, "artist_type required")

    gig = db.execute(
        text("SELECT venue_id, hold_status, status FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if not gig["hold_status"]:
        raise HTTPException(400, "Gig is not held")

    cancelled_slot_ids = [r[0] for r in db.execute(
        text("""SELECT id FROM gig_slots
                WHERE gig_id = :gid AND status = 'open' AND artist_type = :t"""),
        {"gid": gig_id, "t": artist_type}
    ).fetchall()]
    if not cancelled_slot_ids:
        return {"ok": True, "cancelled_count": 0,
                "message": f"No open {artist_type} slots to cancel."}
    db.execute(
        text("""UPDATE gig_slots SET status = 'cancelled'
                WHERE gig_id = :gid AND status = 'open' AND artist_type = :t"""),
        {"gid": gig_id, "t": artist_type}
    )

    # If no open slots remain across any type, the gig is done — flip
    # gig.status appropriately and clear hold_status (the remaining
    # buckets are also empty, so the rotation has nothing left to do).
    remaining_open = db.execute(
        text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
        {"gid": gig_id}
    ).scalar() or 0
    booked = db.execute(
        text("SELECT COUNT(*) FROM gig_slots WHERE gig_id = :gid AND status = 'booked'"),
        {"gid": gig_id}
    ).scalar() or 0
    if remaining_open == 0:
        new_status = "booked" if booked else "cancelled"
        db.execute(
            text("UPDATE gigs SET status = :s, hold_status = NULL WHERE id = :gid"),
            {"s": new_status, "gid": gig_id}
        )
    # If open slots still exist, hold_status stays as-is — other type
    # buckets are still running.

    db.commit()
    logger.info(f"[HOLD] gig={gig_id} cancelled {len(cancelled_slot_ids)} open '{artist_type}' slot(s) via bucket-resolve")
    return {"ok": True,
            "cancelled_count": len(cancelled_slot_ids),
            "cancelled_slot_ids": cancelled_slot_ids,
            "message": f"Cancelled {len(cancelled_slot_ids)} empty {artist_type} slot(s)."}


@router.post("/api/series/{recurring_group_id}/hold/start")
def start_series_hold_endpoint(recurring_group_id: str,
                               user=Depends(get_current_user), db=Depends(get_db)):
    """Kickoff for a series-wide hold (Jul 2026 — Phase 5).

    Called once by the venue's create-gig frontend after every recurring
    occurrence has been POSTed (each with hold_artist_ids set + the
    deferred-kickoff flag). Fires the FIRST round of bundled offers:
    one parallel offer per artist_type bucket. Each artist gets ONE
    email listing every gig in the series they could fill, with a
    shared offer_token covering all their rows.

    Idempotent — calling on a series whose holds are already in flight
    returns the offered artist_ids without re-firing.
    """
    from backend.utils import check_venue_access
    from backend.services.gig_hold import start_series_hold
    # Authorize: any gig in the series tells us the venue.
    row = db.execute(
        text("""SELECT g.venue_id FROM gigs g
                WHERE g.recurring_group_id = :rgid LIMIT 1"""),
        {"rgid": recurring_group_id}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Series not found")
    check_venue_access(db, row["venue_id"], user.id)
    offered = start_series_hold(db, recurring_group_id) or []
    return {"ok": True, "offered_artist_ids": offered,
            "message": f"Series hold started — {len(offered)} bundled offer(s) sent."}


@router.get("/series-hold/respond/{token}", include_in_schema=False)
def series_hold_landing(token: str, request: Request, db=Depends(get_db)):
    """Landing page for the email-link click. Redirects to the artist
    book-gigs page with a hash that opens the series-hold modal."""
    # Look up the artist + series so the redirect can target the
    # right calendar.
    row = db.execute(
        text("""SELECT wl.artist_id, g.recurring_group_id
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'
                  AND wl.offer_declined = 0
                LIMIT 1"""),
        {"tok": token}
    ).mappings().first()
    if not row:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:40px;text-align:center;'>"
            "<h2>Offer No Longer Active</h2>"
            "<p>This series-hold offer has expired or already been responded to.</p>"
            "</body></html>",
            status_code=410
        )
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        f"/app/artist-book-gigs.html#series-hold={token}",
        status_code=302
    )


def _h_esc(s) -> str:
    """Defensive HTML-escape for venue/artist names embedded in landing
    page templates. Returns empty string for None."""
    import html as _html
    return _html.escape("" if s is None else str(s), quote=True)


def _series_hold_landing_html(title: str, body_html: str, color: str = "#111", status: int = 200):
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        f"<html><body style='font-family:-apple-system,sans-serif;max-width:560px;"
        f"margin:60px auto;padding:24px;text-align:center;color:{color};'>"
        f"<h2 style='color:{color};margin-bottom:8px;'>{title}</h2>"
        f"{body_html}"
        f"<p style='margin-top:24px;'><a href='/app/artist-book-gigs.html' style='color:#7c6bff;'>Back to your calendar</a></p>"
        f"</body></html>",
        status_code=status,
    )


def _resolve_series_hold_token(db, token: str):
    """Look up a series-hold offer-token's artist + venue + date span
    without firing the decline. Returns a mapping or None."""
    return db.execute(
        text("""SELECT wl.artist_id, wl.offer_declined,
                       MIN(g.date) as first_date,
                       MAX(g.date) as last_date,
                       COUNT(DISTINCT g.id) as gig_count,
                       MAX(g.recurring_group_id) as recurring_group_id,
                       MAX(v.venue_name) as venue_name,
                       MAX(a.name) as artist_name,
                       MAX(a.user_id) as artist_user_id
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                JOIN venues v ON v.id = g.venue_id
                JOIN artists a ON a.id = wl.artist_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'
                GROUP BY wl.artist_id"""),
        {"tok": token}
    ).mappings().first()


@router.get("/series-hold/decline/{token}", include_in_schema=False)
def series_hold_decline_landing(token: str, request: Request,
                                user=Depends(get_optional_user),
                                db=Depends(get_db)):
    """Email-link landing for "Decline All".

    Y3 audit fix (Jun 30 2026): defend against link prefetchers (Gmail/
    Outlook/Slack auto-GET URLs in emails to render previews — that would
    silently fire the decline before the artist sees it) and forwarded-
    email recipients. Behavior:

      - Logged-in caller who owns the token's artist → execute decline
        immediately (one-click as before).
      - Anyone else (anonymous OR logged-in-but-wrong-artist) → render a
        confirmation page with a POST form that performs the actual
        decline. GET no longer mutates state in the un-auth'd case.
    """
    info = _resolve_series_hold_token(db, token)
    if not info:
        return _series_hold_landing_html(
            "Offer not found",
            "<p>This series-hold link is invalid or has already been cleaned up.</p>",
            color="#ef4444", status=404,
        )
    if int(info["offer_declined"] or 0) == 1:
        return _series_hold_landing_html(
            "Already responded",
            "<p>This series offer has already been handled.</p>",
            color="#94a3b8", status=410,
        )

    artist_owns_caller = bool(user) and int(user.id) == int(info["artist_user_id"] or 0)
    if not artist_owns_caller:
        try:
            from backend.utils import check_artist_access
            check_artist_access(db, int(info["artist_id"]), user.id) if user else None
            artist_owns_caller = bool(user)
        except Exception:
            artist_owns_caller = False

    if artist_owns_caller:
        from backend.services.gig_hold import respond_to_series_hold_offer
        try:
            result = respond_to_series_hold_offer(db, token=token, action="decline")
        except Exception as e:
            logger.error(f"[SERIES_HOLD] decline-landing failed for token={token[:8]}: {e}")
            result = {"ok": False, "message": "Could not process — please try again."}
        if result.get("ok"):
            return _series_hold_landing_html(
                "Declined — thanks for letting the venue know",
                "<p>The venue has been notified and the next artist on their hold list will be offered the series.</p>",
                color="#22c55e",
            )
        return _series_hold_landing_html(
            "Couldn't process",
            f"<p>{result.get('message', 'The offer may have expired or already been responded to.')}</p>",
            color="#ef4444", status=410,
        )

    # Un-auth'd (or wrong-artist) caller → show a confirmation form.
    # The POST endpoint below fires the actual decline. This stops
    # GET-prefetchers from silently declining 26 dates on the artist's
    # behalf, and a forwarded-email recipient has to take a deliberate
    # click before any state changes.
    safe_venue = _h_esc(info.get("venue_name") or "the venue")
    safe_artist = _h_esc(info.get("artist_name") or "you")
    gig_count = int(info.get("gig_count") or 0)
    first_date = _h_esc(str(info.get("first_date") or "")[:10])
    last_date = _h_esc(str(info.get("last_date") or "")[:10])
    span = f"{first_date}" if first_date == last_date else f"{first_date} → {last_date}"
    body = (
        f"<p style='color:#374151;'>You're about to decline {safe_venue}'s series-hold offer for "
        f"<strong>{gig_count} date{'s' if gig_count != 1 else ''}</strong> ({_h_esc(span)}) on behalf of "
        f"<strong>{safe_artist}</strong>.</p>"
        f"<form method='POST' action='/series-hold/decline/{_h_esc(token)}' style='margin-top:18px;'>"
        f"  <button type='submit' style='padding:10px 22px;background:#ef4444;color:#fff;border:0;border-radius:6px;font-weight:600;cursor:pointer;font-size:14px;'>Yes, decline this offer</button>"
        f"</form>"
        f"<p style='margin-top:16px;font-size:13px;color:#6b7280;'>If this wasn't you, you can safely close this tab — nothing has been declined yet.</p>"
    )
    return _series_hold_landing_html(
        "Confirm: decline this series offer?",
        body,
        color="#111",
    )


@router.post("/series-hold/decline/{token}", include_in_schema=False)
def series_hold_decline_submit(token: str, db=Depends(get_db)):
    """Form-POST target for the confirmation landing above. Performs the
    actual decline only after the user explicitly clicks the button —
    GET prefetchers can't reach this path."""
    from backend.services.gig_hold import respond_to_series_hold_offer
    try:
        result = respond_to_series_hold_offer(db, token=token, action="decline")
    except Exception as e:
        logger.error(f"[SERIES_HOLD] decline-submit failed for token={token[:8]}: {e}")
        result = {"ok": False, "message": "Could not process — please try again."}
    if result.get("ok"):
        return _series_hold_landing_html(
            "Declined — thanks for letting the venue know",
            "<p>The venue has been notified and the next artist on their hold list will be offered the series.</p>",
            color="#22c55e",
        )
    return _series_hold_landing_html(
        "Couldn't process",
        f"<p>{result.get('message', 'The offer may have expired or already been responded to.')}</p>",
        color="#ef4444", status=410,
    )


@router.get("/api/series-hold/offer/{token}")
def get_series_hold_offer(token: str, user=Depends(get_current_user), db=Depends(get_db)):
    """Detail endpoint for the series-hold modal. Returns the offer's
    artist + venue + eligible gigs (open + matching the artist's type)
    + the artist's existing bookings at this venue OUTSIDE the series
    (so the modal can grey dates that would conflict with the venue's
    frequency rule against the artist's pre-existing one-offs).

    Auth (Jul 2026 audit fix #19): caller must own the artist (or be
    an entity_user of the artist) the token resolves to. Was unauth
    before — anyone with the URL could enumerate venue/artist/pay info.
    """
    from backend.services.gig_hold import (
        _list_series_eligible_gigs, _list_artist_other_bookings_at_venue
    )
    row = db.execute(
        text("""SELECT wl.artist_id, wl.offer_expires_at,
                       wl.offer_declined,
                       g.recurring_group_id, g.venue_id,
                       v.venue_name, v.artist_frequency_days,
                       a.name as artist_name
                FROM gig_waitlist wl
                JOIN gigs g ON g.id = wl.gig_id
                JOIN venues v ON v.id = g.venue_id
                JOIN artists a ON a.id = wl.artist_id
                WHERE wl.offer_token = :tok AND wl.source = 'hold'
                LIMIT 1"""),
        {"tok": token}
    ).mappings().first()
    if not row:
        raise HTTPException(404, "Offer not found")
    if int(row["offer_declined"] or 0) == 1:
        raise HTTPException(410, "Offer already responded to")
    # Auth: caller must own this artist
    auth_ok = db.execute(
        text("""SELECT 1 FROM artists a
                WHERE a.id = :aid AND (a.user_id = :uid OR EXISTS (
                  SELECT 1 FROM entity_users eu
                  WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid
                ))"""),
        {"aid": row["artist_id"], "uid": user.id}
    ).first()
    if not auth_ok:
        raise HTTPException(403, "Not your offer")
    # Venue contract requirement
    vc = db.execute(
        text("""SELECT id, contract_type FROM venue_contracts
                WHERE venue_id = :vid AND is_active = 1 AND require_for_booking = 1
                LIMIT 1"""),
        {"vid": row["venue_id"]}
    ).mappings().first()
    eligible = _list_series_eligible_gigs(
        db, row["recurring_group_id"], row["artist_id"]
    )
    # Out-of-series existing bookings for freq greying (#11 fix).
    existing_bookings = _list_artist_other_bookings_at_venue(
        db, row["venue_id"], row["artist_id"],
        exclude_recurring_group_id=row["recurring_group_id"]
    )
    return {
        "ok": True,
        "artist_id": row["artist_id"],
        "artist_name": row["artist_name"],
        "venue_id": row["venue_id"],
        "venue_name": row["venue_name"],
        "recurring_group_id": row["recurring_group_id"],
        "offer_expires_at": str(row["offer_expires_at"]) if row["offer_expires_at"] else None,
        "frequency_days": int(row["artist_frequency_days"] or 0),
        "requires_contract": bool(vc),
        "contract_type": vc["contract_type"] if vc else None,
        "eligible": eligible,
        "existing_bookings": existing_bookings,
    }


@router.post("/api/series-hold/respond/{token}")
def post_series_hold_response(token: str, data: dict, request: Request,
                              user=Depends(get_current_user), db=Depends(get_db)):
    """Bulk-accept (with picks) or decline-all a series hold offer.

    Body: `{action: 'accept' | 'decline', slot_picks?: [{gig_id, slot_id}],
            signature_name?: str}`.
    """
    from backend.services.gig_hold import respond_to_series_hold_offer
    # Auth: token already binds to a specific artist; verify caller
    # owns that artist or is an entity_user.
    row = db.execute(
        text("""SELECT wl.artist_id FROM gig_waitlist wl
                WHERE wl.offer_token = :tok AND wl.source = 'hold' LIMIT 1"""),
        {"tok": token}
    ).first()
    if not row:
        raise HTTPException(404, "Offer not found")
    artist_id = int(row[0])
    auth = db.execute(
        text("""SELECT 1 FROM artists a
                WHERE a.id = :aid AND (a.user_id = :uid OR EXISTS (
                  SELECT 1 FROM entity_users eu
                  WHERE eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                ))"""),
        {"aid": artist_id, "uid": user.id}
    ).first()
    if not auth:
        raise HTTPException(403, "Not your offer")
    action = (data.get("action") or "").lower()
    picks = data.get("slot_picks") or []
    sig = data.get("signature_name")
    ip = request.client.host if request.client else None
    result = respond_to_series_hold_offer(
        db, token=token, action=action,
        slot_picks=picks, signature_name=sig, signature_ip=ip
    )
    if not result.get("ok"):
        # 4xx-friendly: surface message back to client
        return result
    return result


@router.post("/api/gigs/{gig_id}/hold/resolve-exhausted")
def resolve_exhausted_hold(gig_id: int, data: dict,
                           user=Depends(get_current_user), db=Depends(get_db)):
    """Venue's response to the hold_exhausted state. Body action:
       'open_all'     — clear hold_status, gig opens to public for the
                         remaining empty slots (existing booked slots
                         stay booked). Gig.status stays 'open'.
       'cancel_empty' — cancel each empty slot individually via the
                         existing slot cancel path. Booked slots
                         survive. (Note: full-gig cancel uses the
                         existing /cancel endpoint, not this one.)
    """
    from backend.utils import check_venue_access
    gig = db.execute(
        text("SELECT venue_id, hold_status FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    if not gig:
        raise HTTPException(404, "Gig not found")
    check_venue_access(db, gig["venue_id"], user.id)
    if gig["hold_status"] != "exhausted":
        raise HTTPException(400, "Hold is not in exhausted state")
    action = (data.get("action") or "").lower()

    if action == "open_all":
        db.execute(
            text("UPDATE gigs SET hold_status = NULL WHERE id = :gid"),
            {"gid": gig_id}
        )
        db.commit()
        return {"ok": True, "message": "Empty slots are now open to all artists."}

    if action == "cancel_empty":
        # Jul 2026 spec rewrite: DELETE empty slot rows. If the gig has
        # no booked / in-flight slots after the deletion, DELETE the
        # whole gig (via delete_gig_completely so flyers / waitlist /
        # tokens are cleaned up too). If at least one booked slot
        # remains, keep the gig and just clear hold_status.
        from backend.services.gig_cleanup import delete_gig_completely
        open_slot_ids = [r[0] for r in db.execute(
            text("SELECT id FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
            {"gid": gig_id}
        ).fetchall()]
        booked_or_inflight = db.execute(
            text("""SELECT COUNT(*) FROM gig_slots
                    WHERE gig_id = :gid
                      AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')"""),
            {"gid": gig_id}
        ).scalar() or 0
        if booked_or_inflight == 0:
            delete_gig_completely(db, gig_id)
            return {"ok": True,
                    "deleted_count": len(open_slot_ids),
                    "gig_deleted": True,
                    "message": f"Gig deleted (no booked slots; removed {len(open_slot_ids)} empty slot(s))."}
        for sid in open_slot_ids:
            db.execute(text("DELETE FROM gig_slots WHERE id = :sid"), {"sid": sid})
        db.execute(
            text("UPDATE gigs SET hold_status = NULL WHERE id = :gid"),
            {"gid": gig_id}
        )
        db.commit()
        return {"ok": True,
                "deleted_count": len(open_slot_ids),
                "gig_deleted": False,
                "message": f"Deleted {len(open_slot_ids)} empty slot(s); kept the gig with its booked slot(s)."}

    raise HTTPException(400, "Unknown action — expected 'open_all' or 'cancel_empty'")


# ============================================================
# HOLD GIG — Phase 3: artist-facing pending-offers endpoint
# ============================================================
# Powers the "Pending Offers" banner at the top of artist-book-gigs.html.
# Returns active hold offers across ALL artists the user owns or has
# entity_user access to. Each offer carries enough info to render the
# banner card without a second fetch: venue name, gig date, slot list,
# token (for the accept/decline links), expiry.

@router.get("/api/me/hold-offers")
def my_hold_offers(user=Depends(get_current_user), db=Depends(get_db)):
    """Active hold offers for every artist this user has access to.
    Excludes already-responded (declined/expired) offers; only rows
    in flight + not yet expired.

    Includes the per-artist matching-slot filter so a DJ user with
    an offer on a Live Band + DJ multi-slot gig only sees the DJ
    slot in the banner."""
    from backend.services.gig_hold import _list_matching_open_slots, _fmt_time, _effective_pay_for_artist
    # Series-hold rows (Jul 2026 audit RED #8): exclude rows whose
    # offer_token is shared across multiple gigs — those are the
    # bundled series offers. Their sole entry point is the email
    # link → bundled-modal hash deep-link, NOT this banner. Letting
    # them surface here would render 26 banner cards for a 26-week
    # series and clicking any one card would route to the legacy
    # per-gig accept (which only books a single random slot and
    # leaves the other 25 rows orphaned).
    rows = db.execute(
        text("""SELECT wl.artist_id, wl.gig_id, wl.offer_token,
                       wl.offer_sent_at, wl.offer_expires_at,
                       a.name as artist_name,
                       g.date as gig_date, g.title as gig_title,
                       g.is_multi_slot, g.venue_id,
                       v.venue_name
                FROM gig_waitlist wl
                JOIN artists a ON a.id = wl.artist_id
                JOIN gigs g ON g.id = wl.gig_id
                JOIN venues v ON v.id = g.venue_id
                WHERE wl.source = 'hold'
                  AND wl.offer_sent = 1
                  AND wl.offer_declined = 0
                  AND wl.offer_expires_at > CURRENT_TIMESTAMP
                  AND g.hold_status = 'active'
                  AND (a.user_id = :uid
                       OR EXISTS (SELECT 1 FROM entity_users eu
                                  WHERE eu.entity_type = 'artist'
                                    AND eu.entity_id = a.id
                                    AND eu.user_id = :uid))
                  AND g.recurring_group_id IS NULL
                ORDER BY wl.offer_expires_at ASC"""),
        {"uid": user.id}
    ).mappings().all()

    from datetime import datetime as _dt
    now = _dt.utcnow()
    offers = []
    for r in rows:
        # Hours remaining
        hours_remaining = None
        if r["offer_expires_at"]:
            try:
                exp = _dt.strptime(str(r["offer_expires_at"]).replace("T", " ").split(".")[0], "%Y-%m-%d %H:%M:%S")
                hours_remaining = max(0, round((exp - now).total_seconds() / 3600, 1))
            except Exception:
                pass
        # Slots the artist can actually fill (mirrors offer email + picker)
        matching = _list_matching_open_slots(db, r["gig_id"], r["artist_id"])
        slots_out = []
        for s in matching:
            # Apply per-artist pay override for flat slots so the banner
            # shows the same amount the artist will actually be paid.
            # Door slots are intentionally NOT overridden — guarantee
            # floor is final (see _effective_pay_for_artist docstring).
            effective_pay = _effective_pay_for_artist(db, s, r["venue_id"], r["artist_id"])
            slots_out.append({
                "id": int(s["id"]),
                "slot_number": s["slot_number"],
                "time": f"{_fmt_time(s['start_time'])} – {_fmt_time(s['end_time'])}",
                "pay": effective_pay,
                "artist_type": s["artist_type"],
                # Deal info — lets the artist banner render door deals
                # correctly when the venue mid-cycle swaps a slot's
                # deal_type. Without these the banner could only show
                # a flat $ amount.
                "deal_type": s.get("deal_type") or "flat",
                "door_pct": s.get("door_pct") or 0,
                "guarantee_cents": s.get("guarantee_cents") or 0,
                "apply_override": int(s.get("apply_override") or 0),
            })
        offers.append({
            "artist_id": r["artist_id"],
            "artist_name": r["artist_name"],
            "gig_id": r["gig_id"],
            "gig_date": str(r["gig_date"]),
            "gig_title": r["gig_title"],
            "venue_id": r["venue_id"],
            "venue_name": r["venue_name"],
            "is_multi_slot": bool(r["is_multi_slot"]),
            "offer_token": r["offer_token"],
            "offer_expires_at": str(r["offer_expires_at"]) if r["offer_expires_at"] else None,
            "hours_remaining": hours_remaining,
            "slots": slots_out,
        })
    return {"offers": offers}


@router.get("/api/me/series-hold-offers")
def my_series_hold_offers(user=Depends(get_current_user), db=Depends(get_db)):
    """Active series-hold offers for every artist this user can access.
    Sibling to /api/me/hold-offers (which surfaces single-gig offers).
    Series offers are one bundled offer per (token, artist) group;
    clicking opens the series-hold modal via the shared offer_token.
    Added Jul 2026 (audit fix #8 — replaces the per-gig fan-out in
    the legacy banner with a single collapsed entry per series).
    """
    rows = db.execute(
        text("""SELECT wl.artist_id, wl.offer_token,
                       MIN(wl.offer_expires_at) as expires_at,
                       a.name as artist_name,
                       v.id as venue_id, v.venue_name,
                       g.recurring_group_id
                FROM gig_waitlist wl
                JOIN artists a ON a.id = wl.artist_id
                JOIN gigs g ON g.id = wl.gig_id
                JOIN venues v ON v.id = g.venue_id
                WHERE wl.source = 'hold'
                  AND wl.offer_sent = 1
                  AND wl.offer_declined = 0
                  AND wl.offer_expires_at > CURRENT_TIMESTAMP
                  AND g.recurring_group_id IS NOT NULL
                  AND (a.user_id = :uid
                       OR EXISTS (SELECT 1 FROM entity_users eu
                                  WHERE eu.entity_type = 'artist'
                                    AND eu.entity_id = a.id
                                    AND eu.user_id = :uid))
                GROUP BY wl.offer_token, wl.artist_id
                ORDER BY MIN(wl.offer_expires_at) ASC"""),
        {"uid": user.id}
    ).mappings().all()
    from datetime import datetime as _dt
    from backend.services.gig_hold import _list_series_eligible_gigs
    now = _dt.utcnow()
    out = []
    for r in rows:
        hours_remaining = None
        if r["expires_at"]:
            try:
                exp = _dt.strptime(str(r["expires_at"]).replace("T"," ").split(".")[0],
                                    "%Y-%m-%d %H:%M:%S")
                hours_remaining = max(0, round((exp - now).total_seconds() / 3600, 1))
            except Exception:
                pass
        # Use the canonical eligible-gigs computation so the banner count
        # matches what the artist sees when they open the picker. A static
        # COUNT(DISTINCT gig_id) would show 8 dates available for a series
        # even after another artist booked a slot — the bundled-modal would
        # then surface only 7. (Jul 2026 user report.)
        eligible = _list_series_eligible_gigs(db, r["recurring_group_id"], r["artist_id"])
        eligible_gig_count = len({e["gig_id"] for e in eligible})
        if eligible_gig_count == 0:
            # No eligible slots left — the artist will see "no matching
            # dates" if they click into the modal. Skip the banner card
            # entirely so they don't get a confusing dead end.
            continue
        out.append({
            "artist_id": r["artist_id"],
            "artist_name": r["artist_name"],
            "venue_id": r["venue_id"],
            "venue_name": r["venue_name"],
            "recurring_group_id": r["recurring_group_id"],
            "offer_token": r["offer_token"],
            "gig_count": eligible_gig_count,
            "expires_at": str(r["expires_at"]) if r["expires_at"] else None,
            "hours_remaining": hours_remaining,
        })
    return {"offers": out}


@router.get("/api/me/venue-hold-offers")
def my_venue_hold_offers(user=Depends(get_current_user), db=Depends(get_db)):
    """Snapshot of every held gig across every venue this user can manage.
    Powers the venue-side Pending Offers banner — mirrors the artist
    banner but from the venue perspective. Includes current offer
    (artist + countdown), queued/declined/accepted counts, and a
    `needs_action` flag for exhausted gigs that need the venue to
    pick 'Open to All' or 'Cancel Gig'.
    """
    from datetime import datetime as _dt
    # Held gigs across every venue this user has access to (primary +
    # secondary via entity_users). Filtered to today-or-later — a held
    # gig in the past is stale and shouldn't clutter the banner.
    rows = db.execute(
        text("""SELECT g.id as gig_id, g.date as gig_date, g.title as gig_title,
                       g.hold_status, g.venue_id, v.venue_name,
                       g.recurring_group_id
                FROM gigs g JOIN venues v ON v.id = g.venue_id
                WHERE g.hold_status IN ('active', 'exhausted')
                  AND g.date >= date('now')
                  AND (v.user_id = :uid
                       OR EXISTS (SELECT 1 FROM entity_users eu
                                  WHERE eu.entity_type = 'venue'
                                    AND eu.entity_id = v.id
                                    AND eu.user_id = :uid))
                ORDER BY g.date ASC, g.id ASC"""),
        {"uid": user.id}
    ).mappings().all()

    now = _dt.utcnow()
    offers = []
    # For series-aware "booked elsewhere" detection — group by rgid once.
    series_bookings = {}  # rgid -> {aid -> [(gig_id, date)]}
    series_rgids = sorted({r["recurring_group_id"] for r in rows if r["recurring_group_id"]})
    for _rgid in series_rgids:
        series_bookings[_rgid] = {}
        for _rr in db.execute(text("""
            SELECT gs.artist_id, g.id as gig_id, g.date
            FROM gig_slots gs JOIN gigs g ON g.id = gs.gig_id
            WHERE g.recurring_group_id = :rgid
              AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
              AND gs.artist_id IS NOT NULL
        """), {"rgid": _rgid}).mappings().all():
            series_bookings[_rgid].setdefault(int(_rr["artist_id"]), []).append(
                (int(_rr["gig_id"]), str(_rr["date"])[:10])
            )
    for r in rows:
        gid = r["gig_id"]
        rgid = r.get("recurring_group_id")
        venue_id_local = r["venue_id"]
        gig_date_str = str(r["gig_date"])[:10]
        # Window hours for this gig (heuristic for natural-expiry vs decline).
        _winh_row = db.execute(
            text("SELECT COALESCE(hold_offer_window_hours, 24) AS h FROM gigs WHERE id = :gid"),
            {"gid": gid}
        ).scalar()
        win_h = int(_winh_row or 24)
        # Per-gig waitlist snapshot
        wl_rows = db.execute(
            text("""SELECT wl.artist_id, wl.position, wl.offer_sent,
                           wl.offer_sent_at, wl.offer_expires_at, wl.offer_declined,
                           a.name as artist_name, a.artist_type
                    FROM gig_waitlist wl
                    LEFT JOIN artists a ON a.id = wl.artist_id
                    WHERE wl.gig_id = :gid AND wl.source = 'hold'
                    ORDER BY wl.position ASC"""),
            {"gid": gid}
        ).mappings().all()
        # Booked artists via gig_slots — used to derive 'accepted' state
        slot_rows = db.execute(
            text("""SELECT artist_id, status FROM gig_slots WHERE gig_id = :gid"""),
            {"gid": gid}
        ).mappings().all()
        accepted_aids = set(s["artist_id"] for s in slot_rows
                            if s["status"] == "booked" and s["artist_id"])

        queued_count = 0
        declined_count = 0
        accepted_count = 0
        # Per-state artist name lists for truthful hover tooltips
        # (Jul 2026 — replace the misleading "all N declined" summary).
        declined_artists = []
        expired_artists = []
        booked_elsewhere_artists = []
        freq_blocked_artists = []
        current_offer = None
        for w in wl_rows:
            if w["artist_id"] in accepted_aids:
                accepted_count += 1
                continue
            if not w["offer_sent"]:
                queued_count += 1
                # Frequency check for queued artists at this venue
                try:
                    _fd = db.execute(
                        text("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days)
                                FROM venues v LEFT JOIN preferred_artists pa
                                  ON pa.venue_id = v.id AND pa.artist_id = :aid
                                WHERE v.id = :vid"""),
                        {"aid": w["artist_id"], "vid": venue_id_local}
                    ).scalar() or 0
                    if int(_fd) > 0:
                        _cb = db.execute(text("""
                            SELECT g3.date FROM gigs g3
                            LEFT JOIN gig_slots gs2 ON gs2.gig_id = g3.id
                            WHERE g3.venue_id = :vid AND g3.id != :gid
                              AND (g3.artist_id = :aid
                                   OR (gs2.artist_id = :aid AND gs2.status IN
                                       ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')))
                            ORDER BY ABS(julianday(g3.date) - julianday(:gd)) ASC LIMIT 1
                        """), {"vid": venue_id_local, "gid": gid,
                               "aid": w["artist_id"], "gd": gig_date_str}).mappings().first()
                        if _cb:
                            _d1 = _dt.strptime(gig_date_str, "%Y-%m-%d").date()
                            _d2 = _dt.strptime(str(_cb["date"])[:10], "%Y-%m-%d").date()
                            _diff = (_d1 - _d2).days
                            if abs(_diff) <= int(_fd):
                                freq_blocked_artists.append({
                                    "artist_id": w["artist_id"],
                                    "artist_name": w["artist_name"],
                                    "days_apart": int(abs(_diff)),
                                    "direction": "before" if _diff > 0 else "after",
                                    "other_date": str(_cb["date"])[:10],
                                    "required": int(_fd),
                                })
                except Exception:
                    pass
                continue
            if w["offer_declined"]:
                declined_count += 1
                # Series first — did they book another date in the same series?
                if rgid and series_bookings.get(rgid, {}).get(int(w["artist_id"])):
                    other = [(g2, d) for (g2, d) in series_bookings[rgid][int(w["artist_id"])] if g2 != gid]
                    if other:
                        booked_elsewhere_artists.append({
                            "artist_id": w["artist_id"],
                            "artist_name": w["artist_name"],
                            "other_date": other[0][1],
                        })
                        continue
                # Natural expiry vs explicit decline
                natural_expiry = False
                try:
                    _sent = _dt.strptime(str(w["offer_sent_at"]).replace("T"," ").split(".")[0], "%Y-%m-%d %H:%M:%S")
                    _exp = _dt.strptime(str(w["offer_expires_at"]).replace("T"," ").split(".")[0], "%Y-%m-%d %H:%M:%S")
                    _gap_h = (_exp - _sent).total_seconds() / 3600
                    if _gap_h >= win_h * 0.95:
                        natural_expiry = True
                except Exception:
                    pass
                target = expired_artists if natural_expiry else declined_artists
                target.append({"artist_id": w["artist_id"], "artist_name": w["artist_name"]})
                continue
            # Active offer in flight
            hours_remaining = None
            if w["offer_expires_at"]:
                try:
                    exp = _dt.strptime(str(w["offer_expires_at"]).replace("T", " ").split(".")[0], "%Y-%m-%d %H:%M:%S")
                    hours_remaining = max(0, round((exp - now).total_seconds() / 3600, 1))
                except Exception:
                    pass
            current_offer = {
                "artist_id": w["artist_id"],
                "artist_name": w["artist_name"],
                "offer_expires_at": str(w["offer_expires_at"]) if w["offer_expires_at"] else None,
                "hours_remaining": hours_remaining,
            }

        # Slot counts surface in the banner row so the venue sees
        # 'X open / Y booked' next to the date at a glance.
        open_slot_count = sum(1 for s in slot_rows if s["status"] == "open")
        booked_slot_count = sum(1 for s in slot_rows if s["status"] == "booked")
        total_slots = len(slot_rows)

        offers.append({
            "gig_id": gid,
            "gig_date": str(r["gig_date"]),
            "gig_title": r["gig_title"],
            "venue_id": r["venue_id"],
            "venue_name": r["venue_name"],
            "hold_status": r["hold_status"],
            "recurring_group_id": r["recurring_group_id"],
            "current_offer": current_offer,
            "queued_count": queued_count,
            "declined_count": declined_count,
            "accepted_count": accepted_count,
            "total_artists": len(wl_rows),
            "open_slot_count": open_slot_count,
            "booked_slot_count": booked_slot_count,
            "total_slot_count": total_slots,
            # Truthful per-state artist lists (Jul 2026). Powers the
            # per-gig row breakdown + hover tooltips so the venue sees
            # WHICH artists declined / expired / freq-blocked instead of
            # the misleading "all N declined" summary.
            "declined_artists": declined_artists,
            "expired_artists": expired_artists,
            "booked_elsewhere_artists": booked_elsewhere_artists,
            "freq_blocked_artists": freq_blocked_artists,
            # Exhausted = every artist responded (declined/expired) AND
            # at least one slot is still open. Venue needs to act.
            "needs_action": r["hold_status"] == "exhausted",
        })

    return {"offers": offers}


@router.post("/api/series/{recurring_group_id}/hold/resolve-exhausted")
def resolve_exhausted_series_hold(
    recurring_group_id: str, data: dict,
    user=Depends(get_current_user), db=Depends(get_db),
):
    """Bulk resolve every held gig in a recurring series in ONE call.

    Mirrors the per-gig `/api/gigs/{gig_id}/hold/resolve-exhausted`
    endpoint but iterates across every gig in the series. Lets the
    venue "Open to All" or "Cancel empty slots" across 50 dates with
    one click instead of 50 individual API calls (Jul 2026 — Pending
    Holds banner series-grouping).

    Body:
      action: 'open_all' | 'cancel_empty' (required)

    Returns: {ok, gig_count, results: [{gig_id, ok, message}], message}
    """
    from backend.utils import check_venue_access
    action = (data.get("action") or "").lower()
    if action not in ("open_all", "cancel_empty"):
        raise HTTPException(400, "Unknown action — expected 'open_all' or 'cancel_empty'")
    # Authorize the venue (any gig in the series tells us which venue).
    venue_row = db.execute(
        text("""SELECT DISTINCT venue_id FROM gigs
                WHERE recurring_group_id = :rgid LIMIT 1"""),
        {"rgid": recurring_group_id}
    ).mappings().first()
    if not venue_row:
        raise HTTPException(404, "Series not found")
    check_venue_access(db, venue_row["venue_id"], user.id)

    # Find every gig in the series with an active or exhausted hold.
    gig_ids = [r[0] for r in db.execute(
        text("""SELECT id FROM gigs
                WHERE recurring_group_id = :rgid
                  AND hold_status IN ('active', 'exhausted')"""),
        {"rgid": recurring_group_id}
    ).fetchall()]
    if not gig_ids:
        return {"ok": True, "gig_count": 0, "results": [],
                "message": "No held gigs in this series."}

    results = []
    if action == "open_all":
        # Just clear hold_status on every gig — the existing per-gig
        # logic for 'open_all' is one UPDATE. Batch into a single
        # statement.
        db.execute(
            text("""UPDATE gigs SET hold_status = NULL
                    WHERE recurring_group_id = :rgid
                      AND hold_status IN ('active', 'exhausted')"""),
            {"rgid": recurring_group_id}
        )
        db.commit()
        for gid in gig_ids:
            results.append({"gig_id": gid, "ok": True, "message": "Opened to all"})
        return {"ok": True, "gig_count": len(gig_ids), "results": results,
                "message": f"Opened {len(gig_ids)} gig(s) to all artists."}

    # cancel_empty (Jul 2026 spec rewrite): DELETE empty slot rows from
    # gig_slots. If the gig has NO booked / in-flight slots remaining
    # after the deletion, DELETE the whole gig via the canonical
    # delete_gig_completely helper (which cleans up flyers, waitlist,
    # tokens, etc). If at least one booked slot remains, keep the gig
    # and just clear its hold_status.
    from backend.services.gig_cleanup import delete_gig_completely
    total_slots_deleted = 0
    full_gig_deletes = 0
    partial_slot_deletes = 0
    for gid in gig_ids:
        try:
            open_slot_ids = [r[0] for r in db.execute(
                text("SELECT id FROM gig_slots WHERE gig_id = :gid AND status = 'open'"),
                {"gid": gid}
            ).fetchall()]
            booked_or_inflight = db.execute(
                text("""SELECT COUNT(*) FROM gig_slots
                        WHERE gig_id = :gid
                          AND status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')"""),
                {"gid": gid}
            ).scalar() or 0
            if booked_or_inflight == 0:
                # No booked/in-flight slots → delete the gig entirely
                # (and its slots, waitlist, flyers, etc.)
                delete_gig_completely(db, gid)
                total_slots_deleted += len(open_slot_ids)
                full_gig_deletes += 1
                results.append({"gig_id": gid, "ok": True,
                                "deleted": "gig",
                                "message": f"Gig deleted (no booked slots; removed {len(open_slot_ids)} empty slot(s))"})
            else:
                # Multi-slot gig with booked slot(s) — delete just the
                # empty slot rows; keep the gig with the booked ones.
                for sid in open_slot_ids:
                    db.execute(text("DELETE FROM gig_slots WHERE id = :sid"), {"sid": sid})
                # Clear hold_status; gig.status reflects the booked
                # slots' state already.
                db.execute(
                    text("UPDATE gigs SET hold_status = NULL WHERE id = :gid"),
                    {"gid": gid}
                )
                total_slots_deleted += len(open_slot_ids)
                partial_slot_deletes += 1
                results.append({"gig_id": gid, "ok": True,
                                "deleted": "slots",
                                "message": f"Deleted {len(open_slot_ids)} empty slot(s); kept the gig with its booked slot(s)"})
        except Exception as e:
            logger.error(f"[SERIES_RESOLVE] cancel_empty failed gig={gid}: {e}")
            results.append({"gig_id": gid, "ok": False, "message": str(e)})
    db.commit()
    # Build a human-readable summary describing what actually happened.
    bits = []
    if full_gig_deletes:
        bits.append(f"deleted {full_gig_deletes} gig{'s' if full_gig_deletes != 1 else ''} entirely")
    if partial_slot_deletes:
        bits.append(f"removed empty slots from {partial_slot_deletes} gig{'s' if partial_slot_deletes != 1 else ''} with booked slots")
    summary = "; ".join(bits) if bits else "no slots to remove"
    return {"ok": True, "gig_count": len(gig_ids), "results": results,
            "slots_deleted": total_slots_deleted,
            "gigs_deleted": full_gig_deletes,
            "gigs_partial": partial_slot_deletes,
            "message": f"Removed {total_slots_deleted} empty slot(s) — {summary}."}

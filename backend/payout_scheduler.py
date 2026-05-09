"""
Auto-Payout Scheduler
Runs inside the FastAPI app as a background thread.

FLOW:
1. At booking: transaction created with status 'scheduled' (no charge)
2. Day after gig at 5pm: scheduler charges venue card, then transfers to artist
3. If charge fails: retry up to 3 times over 3 days, then suspend venue
"""

import threading
import logging
import time
from datetime import datetime, timedelta
from backend.utils import utcnow_naive
from zoneinfo import ZoneInfo
import sqlite3
from backend.db import get_db_connection as _raw_db_conn, _IS_POSTGRES
import os
from backend.services.email_dispatch import format_email_date
from backend.services.notification_service import format_time_12hr


def _compute_slot_times_sqlite(conn, gig_id: int, artist_id=None) -> str:
    """Sqlite-conn equivalent of email_dispatch.compute_slot_times.
    Returns the artist's slot time when artist_id is given (multi-slot),
    or the gig's full slot summary / overall window otherwise."""
    try:
        rows = conn.execute(
            "SELECT start_time, end_time, artist_id FROM gig_slots "
            "WHERE gig_id = ? AND status = 'booked' ORDER BY slot_number ASC",
            (gig_id,)
        ).fetchall()
        if rows:
            if artist_id is not None:
                for r in rows:
                    if r["artist_id"] == artist_id:
                        return f"{format_time_12hr(r['start_time'])} - {format_time_12hr(r['end_time'])}"
            return " | ".join(
                f"{format_time_12hr(r['start_time'])} - {format_time_12hr(r['end_time'])}"
                for r in rows
            )
        g = conn.execute("SELECT start_time, end_time FROM gigs WHERE id = ?", (gig_id,)).fetchone()
        if g and g["start_time"]:
            if g["end_time"]:
                return f"{format_time_12hr(g['start_time'])} - {format_time_12hr(g['end_time'])}"
            return format_time_12hr(g["start_time"])
    except Exception:
        pass
    return ""
logger = logging.getLogger("gigsfill.payout_scheduler")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend.db")
MAX_CHARGE_ATTEMPTS = 3


def get_platform_timezone():
    try:
        conn = _raw_db_conn()
        cursor = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'"
        )
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return ZoneInfo(row[0])
    except Exception as e:
        logger.error(f"Error reading timezone: {e}")
    return ZoneInfo("America/Los_Angeles")

def get_payout_time():
    try:
        conn = _raw_db_conn()
        cursor = conn.execute(
            "SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('payment_processing_hour', 'payout_time')"
        )
        rows = {r[0]: r[1] for r in cursor.fetchall()}
        conn.close()
        if 'payment_processing_hour' in rows and rows['payment_processing_hour']:
            return int(rows['payment_processing_hour']), 0
        if 'payout_time' in rows and rows['payout_time']:
            parts = rows['payout_time'].strip().split(":")
            return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.error(f"Error reading payout_time: {e}")
    return 17, 0


def process_payouts_now():
    """Process all pending payouts: charge venue card, then transfer to artist"""
    try:
        import stripe

        conn = _raw_db_conn()
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'admin_stripe_secret_key'"
        ).fetchone()
        has_stripe_key = row and row[0]
        if has_stripe_key:
            stripe.api_key = row[0]

        enabled_row = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'payments_enabled'"
        ).fetchone()
        payments_live = enabled_row and enabled_row[0] in ('1', 'true')

        # Use UTC for payout comparison — scheduled_process_at stored as naive UTC
        now = utcnow_naive().strftime('%Y-%m-%d %H:%M:%S')

        # Fetch venue_charge rows due for processing (one per gig, covers all slots)
        # Also fetch legacy 'single' type rows for backwards compatibility
        pending = conn.execute("""
            SELECT t.id, t.gig_id, t.artist_id, t.amount_cents, t.venue_charge_cents,
                   t.artist_payout_cents, t.commission_cents, t.to_user_id, t.from_user_id,
                   t.status, t.stripe_payment_intent_id, COALESCE(t.charge_attempts, 0) as charge_attempts,
                   COALESCE(t.transaction_type, 'single') as transaction_type,
                   g.venue_id, g.date as gig_date
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            WHERE t.status IN ('scheduled', 'test', 'charge_retry')
              AND t.transaction_type IN ('venue_charge', 'single')
              AND t.scheduled_process_at <= ?
            ORDER BY t.scheduled_process_at ASC
        """, (now,)).fetchall()

        if not pending:
            logger.info(f"No pending payouts at {now}")
        else:
            logger.info(f"Processing {len(pending)} payouts...")

        for txn in pending:
            txn_id = txn["id"]
            is_test = txn["status"] == "test"
            venue_id = txn["venue_id"]
            attempts = txn["charge_attempts"] or 0

            # ---- ATOMIC CLAIM: mark 'processing' so concurrent runs can't double-charge ----
            claimed = conn.execute(
                "UPDATE transactions SET status = 'processing' WHERE id = ? AND status = ?",
                (txn_id, txn["status"])
            ).rowcount
            conn.commit()
            if claimed == 0:
                logger.info(f"Txn {txn_id}: already claimed by another process — skipping")
                continue

            # Check free trial FIRST — applies to both test and live modes
            free_trial_check = conn.execute(
                "SELECT payments_suspended FROM venue_payment_overrides WHERE venue_id = ?",
                (venue_id,)
            ).fetchone()
            if free_trial_check and free_trial_check["payments_suspended"]:
                logger.info(f"Txn {txn_id}: Venue {venue_id} on free trial — marking suspended, skipping charge")
                conn.execute(
                    "UPDATE transactions SET status = 'suspended', notes = COALESCE(notes || ' | ', '') || 'Free trial venue — direct payment' WHERE id = ?",
                    (txn_id,)
                )
                conn.commit()
                continue

            if is_test:
                # Mark 'transferred' (not 'paid') so artist sees "Processing" immediately.
                # The poll below will auto-settle test transactions after 2 hours.
                conn.execute(
                    "UPDATE transactions SET status = 'transferred', stripe_transfer_id = ?, processed_at = ? WHERE id = ?",
                    ("test_transfer", utcnow_naive().isoformat(), txn_id)
                )
                conn.commit()
                logger.info(f"Txn {txn_id}: TEST transferred (will settle in 2h) ${txn['artist_payout_cents']/100:.2f}")

                # Send payout email for each child artist_payout row (not the parent
                # venue_charge row). The parent has artist_payout_cents=0 and
                # to_user_id=venue — using it produces wrong amounts and wrong recipient.
                # For legacy 'single' rows there are no children; use the row itself.
                if txn["transaction_type"] == "single":
                    _send_payout_email(conn, txn)
                else:
                    child_rows = conn.execute("""
                        SELECT t.id, t.gig_id, t.artist_id, t.amount_cents,
                               t.venue_charge_cents, t.artist_payout_cents, t.commission_cents,
                               t.to_user_id, t.from_user_id, t.status
                        FROM transactions t
                        WHERE t.parent_transaction_id = ?
                          AND t.transaction_type = 'artist_payout'
                    """, (txn_id,)).fetchall()
                    for child in child_rows:
                        _send_payout_email(conn, child)

                try:
                    from backend.routes.affiliate import accrue_affiliate_earnings
                    from backend.db import SessionLocal as _SL
                    _aff_db = _SL()
                    try:
                        accrue_affiliate_earnings(_aff_db, txn_id)
                    finally:
                        _aff_db.close()
                except Exception as _ae:
                    logger.warning(f"Affiliate accrual error (test) txn {txn_id}: {_ae}")
                continue

            # ---- LIVE: Charge venue card ----
            if not has_stripe_key:
                logger.error(f"Txn {txn_id}: No Stripe key configured — CANNOT PROCESS")
                _send_admin_alert(conn, "No Stripe Key — Payments Cannot Process",
                    f"""<p>The payout scheduler tried to process <strong>Transaction #{txn_id}</strong> but no Stripe secret key is configured.</p>
                    <p><strong>{_get_gig_summary(conn, txn)}</strong></p>
                    <p>Go to Admin → Platform Settings → Payments and enter your Stripe secret key.</p>
                    <p>All scheduled payments are <strong>blocked</strong> until this is fixed.</p>""")
                break  # Don't process any more — they'll all fail

            venue_settings = conn.execute("""
                SELECT stripe_customer_id, stripe_payment_method_id
                FROM entity_payment_settings
                WHERE entity_type = 'venue' AND entity_id = ?
            """, (venue_id,)).fetchone()

            if not venue_settings or not venue_settings["stripe_payment_method_id"]:
                _handle_charge_failure(conn, txn, venue_id, attempts, "No payment card on file", tz)
                _send_admin_alert(conn, f"Venue Card Missing — Charge Failed (Txn #{txn_id})",
                    f"""<p><strong>{_get_gig_summary(conn, txn)}</strong></p>
                    <p>Venue has no payment card on file. Attempt {attempts + 1}/{MAX_CHARGE_ATTEMPTS}.</p>
                    <p>Amount: <strong>${txn['venue_charge_cents']/100:.2f}</strong></p>""")
                continue

            # (Free trial check already done above before test/live branching)

            venue_charge = txn["venue_charge_cents"]
            
            payment_intent_id = None
            try:
                pi = stripe.PaymentIntent.create(
                    amount=venue_charge,
                    currency="usd",
                    customer=venue_settings["stripe_customer_id"],
                    payment_method=venue_settings["stripe_payment_method_id"],
                    off_session=True,
                    confirm=True,
                    idempotency_key=f"gig_{txn['gig_id']}_txn_{txn_id}_charge",  # Prevents duplicate charges if called twice
                    metadata={
                        "gig_id": str(txn["gig_id"]),
                        "transaction_id": str(txn_id),
                        "platform": "gigsfill"
                    },
                    description=f"GigsFill Gig #{txn['gig_id']} - performance fee"
                )
                payment_intent_id = pi.id
                logger.info(f"Txn {txn_id}: Venue charged ${venue_charge/100:.2f} (PI: {payment_intent_id})")

            except stripe.error.CardError as e:
                reason = str(getattr(e, 'user_message', e))
                _handle_charge_failure(conn, txn, venue_id, attempts, reason, tz)
                _send_admin_alert(conn, f"Card Declined — Charge Failed (Txn #{txn_id})",
                    f"""<p><strong>{_get_gig_summary(conn, txn)}</strong></p>
                    <p>Venue card declined: <strong>{reason}</strong></p>
                    <p>Attempt {attempts + 1}/{MAX_CHARGE_ATTEMPTS}. Amount: ${venue_charge/100:.2f}</p>""")
                continue
            except Exception as e:
                reason = str(e)[:200]
                _handle_charge_failure(conn, txn, venue_id, attempts, reason, tz)
                _send_admin_alert(conn, f"Charge Error (Txn #{txn_id})",
                    f"""<p><strong>{_get_gig_summary(conn, txn)}</strong></p>
                    <p>Stripe error: <strong>{reason}</strong></p>
                    <p>Attempt {attempts + 1}/{MAX_CHARGE_ATTEMPTS}. Amount: ${venue_charge/100:.2f}</p>""")
                continue

            # Send venue charged email (one email for all slots combined)
            _send_venue_charged_email(conn, txn, venue_id)

            # Retrieve charge_id for source_transaction (bypasses pending balance)
            # AND the real Stripe processing fee from the charge's balance_transaction.
            # Storing the actual fee makes the admin Accounting page 100% accurate
            # rather than using the 2.9% + $0.30 formula estimate (which truncates
            # rather than rounding the way Stripe does, e.g. $15 → $0.73 vs $0.74).
            charge_id = None
            real_stripe_fee_cents = 0
            try:
                pi_obj = stripe.PaymentIntent.retrieve(
                    payment_intent_id,
                    expand=["latest_charge.balance_transaction"]
                )
                if pi_obj.latest_charge:
                    charge_id = pi_obj.latest_charge.id if hasattr(pi_obj.latest_charge, "id") else pi_obj.latest_charge
                    bt = getattr(pi_obj.latest_charge, "balance_transaction", None)
                    if bt and getattr(bt, "fee", None) is not None:
                        real_stripe_fee_cents = int(bt.fee)
            except Exception as ce:
                logger.warning(f"Txn {txn_id}: Could not retrieve charge_id/fee: {ce}")

            # Mark parent as charged. Persist the real Stripe fee if we got it
            # (0 is the legacy default; admin endpoint falls back to the formula
            # estimate when this column is 0, so unfetched rows still render).
            conn.execute(
                """UPDATE transactions SET status = 'charged',
                   stripe_payment_intent_id = ?, charge_attempts = ?,
                   credit_card_fee_cents = ?
                   WHERE id = ?""",
                (payment_intent_id, attempts + 1, real_stripe_fee_cents, txn_id)
            )
            conn.commit()
            if real_stripe_fee_cents:
                logger.info(f"Txn {txn_id}: real Stripe fee ${real_stripe_fee_cents/100:.2f} captured from balance_transaction")

            # Get all artist_payout children for this gig (covers multi-slot and single)
            # Find children awaiting transfer.
            # FIX (May 2026): Accept BOTH 'scheduled' (initial state for newly-
            # created child rows) and 'pending_transfer' (legitimate retry case
            # where a previous transfer attempt was blocked, e.g., artist not
            # onboarded). Previously this only matched 'pending_transfer', but
            # since we changed children's initial status to 'scheduled', this
            # query needed to expand or post-charge transfers would never fire.
            payout_rows = conn.execute("""
                SELECT t.id, t.gig_id, t.artist_id, t.amount_cents, t.venue_charge_cents,
                       t.artist_payout_cents, t.commission_cents, t.to_user_id, t.from_user_id,
                       t.status, g.venue_id, g.date as gig_date
                FROM transactions t
                JOIN gigs g ON t.gig_id = g.id
                WHERE t.parent_transaction_id = ?
                  AND t.transaction_type = 'artist_payout'
                  AND t.status IN ('scheduled', 'pending_transfer')
            """, (txn_id,)).fetchall()

            # For legacy 'single' transactions, treat the row itself as the payout
            if txn["transaction_type"] == 'single':
                payout_rows = [txn]

            _transfer_to_artists(conn, stripe, payout_rows, charge_id, venue_id, txn_id, venue_charge)

        # ---- RETRY TRANSFERS for previously charged but untransferred transactions ----
        # CRITICAL FIX (May 2026): the retry query previously matched on status alone,
        # which caught FRESHLY-CREATED artist_payout children (they're inserted with
        # status='pending_transfer' at booking time, before the venue is charged).
        # The next scheduler tick would then fire the artist transfer, paying the
        # artist BEFORE the venue had been charged — moving real money on the
        # wrong schedule. Add a join to the parent venue_charge to ensure we only
        # retry children whose parent has actually been charged. For legacy 'single'
        # rows, no parent — but those have a stripe_payment_intent_id set when
        # charged, so check for that.
        stalled = conn.execute("""
            SELECT t.id, t.gig_id, t.artist_id, t.amount_cents, t.venue_charge_cents,
                   t.artist_payout_cents, t.commission_cents, t.to_user_id, t.from_user_id,
                   t.status, t.stripe_payment_intent_id, t.parent_transaction_id,
                   COALESCE(t.transaction_type, 'single') as transaction_type,
                   g.venue_id
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            WHERE t.status IN ('pending_transfer', 'transfer_failed')
              AND t.transaction_type IN ('artist_payout', 'single')
              AND (
                -- artist_payout child: parent must be in charged state (or further along)
                (t.transaction_type = 'artist_payout'
                 AND EXISTS (
                   SELECT 1 FROM transactions p
                   WHERE p.id = t.parent_transaction_id
                     AND p.status IN ('charged', 'paid', 'transferred')
                 ))
                OR
                -- legacy 'single' row: must have a payment_intent_id (i.e., was charged)
                (COALESCE(t.transaction_type, 'single') = 'single'
                 AND t.stripe_payment_intent_id IS NOT NULL
                 AND t.stripe_payment_intent_id != '')
              )
            ORDER BY t.id ASC
        """).fetchall()

        if stalled:
            logger.info(f"Retrying {len(stalled)} stalled transfers...")

        for txn in stalled:
            txn_id = txn["id"]
            artist_row = conn.execute("""
                SELECT eps.stripe_connect_account_id, eps.stripe_connect_onboarding_complete
                FROM entity_payment_settings eps
                JOIN artists a ON a.id = eps.entity_id AND eps.entity_type = 'artist'
                WHERE a.user_id = ?
            """, (txn["to_user_id"],)).fetchone()

            if not artist_row or not artist_row["stripe_connect_account_id"]:
                continue  # No connect account at all

            # If DB shows incomplete, verify live with Stripe before giving up
            onboarding_ok = bool(artist_row["stripe_connect_onboarding_complete"])
            if not onboarding_ok:
                try:
                    acct = stripe.Account.retrieve(artist_row["stripe_connect_account_id"])
                    onboarding_ok = bool(acct.charges_enabled and acct.payouts_enabled)
                    if onboarding_ok:
                        conn.execute(
                            "UPDATE entity_payment_settings SET stripe_connect_onboarding_complete = 1 WHERE stripe_connect_account_id = ?",
                            (artist_row["stripe_connect_account_id"],)
                        )
                        conn.commit()
                except Exception:
                    pass

            if not onboarding_ok:
                continue  # Still not onboarded

            try:
                # Use source_transaction if we have the original PaymentIntent to bypass pending balance issue
                retry_charge_id = None
                pi_id = txn["stripe_payment_intent_id"] if txn["stripe_payment_intent_id"] else None
                if pi_id:
                    try:
                        pi_obj = stripe.PaymentIntent.retrieve(pi_id, expand=["latest_charge"])
                        if pi_obj.latest_charge:
                            retry_charge_id = pi_obj.latest_charge.id if hasattr(pi_obj.latest_charge, "id") else pi_obj.latest_charge
                    except Exception as ce:
                        logger.warning(f"Txn {txn_id}: Retry — could not retrieve charge_id: {ce}")

                if (txn.get("artist_payout_cents") or 0) <= 0:
                    logger.error(f"Retry txn {txn_id}: artist_payout_cents={txn.get('artist_payout_cents')} — SKIPPING zero/negative transfer")
                    continue

                retry_kwargs = dict(
                    amount=txn["artist_payout_cents"],
                    currency="usd",
                    destination=artist_row["stripe_connect_account_id"],
                    metadata={
                        "gig_id": str(txn["gig_id"]),
                        "transaction_id": str(txn_id),
                        "platform": "gigsfill"
                    },
                    description=f"GigsFill Gig #{txn['gig_id']} payout"
                )
                if retry_charge_id:
                    retry_kwargs["source_transaction"] = retry_charge_id
                transfer = stripe.Transfer.create(**retry_kwargs)
                conn.execute(
                    "UPDATE transactions SET status = 'transferred', stripe_transfer_id = ?, processed_at = ?, notes = ? WHERE id = ?",
                    (transfer.id, utcnow_naive().isoformat(), f"Retry succeeded (was {txn['status']})", txn_id)
                )
                conn.commit()
                logger.info(f"Txn {txn_id}: RETRY TRANSFERRED ${txn['artist_payout_cents']/100:.2f} -> {artist_row['stripe_connect_account_id']}")
                _send_payout_email(conn, txn)
                _send_admin_alert(conn, f"✅ Stalled Transfer Resolved (Txn #{txn_id})",
                    f"""<p><strong>{_get_gig_summary(conn, txn)}</strong></p>
                    <p>Previously stalled transfer (<code>{txn['status']}</code>) completed successfully.</p>
                    <p>Artist received: <strong>${txn['artist_payout_cents']/100:.2f}</strong></p>""")
            except Exception as e:
                logger.error(f"Txn {txn_id}: Retry transfer still failing - {e}")

        # ---- POLL STRIPE for 'transferred' transactions that may now be paid ----
        # transfer.paid webhook is incompatible with v2 event destinations, so we poll instead.
        transferred = conn.execute("""
            SELECT t.id, t.stripe_transfer_id,
                   COALESCE(t.transaction_type, 'single') as transaction_type,
                   t.to_user_id, t.artist_id,
                   eps.stripe_connect_account_id
            FROM transactions t
            LEFT JOIN entity_payment_settings eps
              ON eps.entity_type = 'artist' AND eps.entity_id = t.artist_id
            WHERE t.status = 'transferred'
              AND t.stripe_transfer_id IS NOT NULL
              AND t.stripe_transfer_id != ''
              AND t.transaction_type IN ('artist_payout', 'single')
            ORDER BY t.id ASC
        """).fetchall()

        # Test transactions auto-settle via _settle_test_transactions() in scheduler_loop()

        if transferred and has_stripe_key and payments_live:
            logger.info(f"Polling {len(transferred)} transferred txn(s) for bank settlement...")
            for txn in transferred:
                if txn["stripe_transfer_id"] == "test_transfer":
                    continue  # handled by test auto-settle above
                try:
                    connect_acct = txn["stripe_connect_account_id"]

                    # Step 1: confirm the transfer itself is not reversed
                    tr = stripe.Transfer.retrieve(txn["stripe_transfer_id"])
                    if not tr or getattr(tr, "reversed", False):
                        continue

                    # Step 2: check the connected account for a paid payout.
                    # A payout status of 'paid' means the money arrived in the artist's bank.
                    # We look for any paid payout created after the transfer was sent.
                    bank_settled = False
                    if connect_acct:
                        try:
                            payouts = stripe.Payout.list(
                                limit=10,
                                status="paid",
                                stripe_account=connect_acct
                            )
                            if payouts and payouts.data:
                                # Any paid payout on the account means funds have reached the bank
                                bank_settled = True
                        except stripe.error.PermissionError:
                            # Connected account hasn't granted payout read permission —
                            # fall back to checking the platform-side balance_transaction
                            bt_id = getattr(tr, "balance_transaction", None)
                            if bt_id:
                                try:
                                    bt_id_str = bt_id if isinstance(bt_id, str) else bt_id.id
                                    bt = stripe.BalanceTransaction.retrieve(bt_id_str)
                                    # 'available' means settled on platform; add 2-day buffer
                                    if getattr(bt, "status", "") == "available":
                                        created = getattr(bt, "created", 0)
                                        import time as _time
                                        if _time.time() - created >= 2 * 86400:
                                            bank_settled = True
                                except Exception:
                                    pass
                        except Exception as pe:
                            logger.warning(f"Txn {txn['id']}: Payout list failed — {pe}")

                    if bank_settled:
                        conn.execute(
                            "UPDATE transactions SET status = 'paid', notes = COALESCE(notes || ' | ', '') || 'Bank payout confirmed' WHERE id = ?",
                            (txn["id"],)
                        )
                        conn.commit()
                        logger.info(f"Txn {txn['id']}: Bank payout confirmed — marked paid")
                        try:
                            from backend.routes.affiliate import accrue_affiliate_earnings
                            from backend.db import SessionLocal as _SL
                            _aff_db = _SL()
                            try:
                                accrue_affiliate_earnings(_aff_db, txn["id"])
                            finally:
                                _aff_db.close()
                        except Exception as _ae:
                            logger.warning(f"Affiliate accrual error for txn {txn['id']}: {_ae}")
                except Exception as e:
                    logger.warning(f"Txn {txn['id']}: Transfer poll failed — {e}")

        # ---- POLL STRIPE for async PaymentIntent failures ----
        # payment_intent.payment_failed webhooks are v1 events incompatible with v2 destinations.
        # Poll any 'charged' txns whose PI may have failed asynchronously.
        charged_pending = conn.execute("""
            SELECT t.id, t.stripe_payment_intent_id
            FROM transactions t
            WHERE t.status = 'charged'
              AND t.stripe_payment_intent_id IS NOT NULL
              AND t.stripe_payment_intent_id != ''
              AND t.processed_at >= datetime('now', '-3 days')
            ORDER BY t.id ASC
        """).fetchall()

        if charged_pending and has_stripe_key and payments_live:
            for txn in charged_pending:
                try:
                    pi = stripe.PaymentIntent.retrieve(txn["stripe_payment_intent_id"])
                    pi_status = getattr(pi, "status", "")
                    if pi_status in ("canceled", "requires_payment_method"):
                        err = getattr(pi, "last_payment_error", None)
                        reason = (err.get("message") if isinstance(err, dict) else getattr(err, "message", "")) if err else "Async payment failure"
                        conn.execute(
                            "UPDATE transactions SET status = 'charge_retry', notes = COALESCE(notes || ' | ', '') || ? WHERE id = ?",
                            (f"Async PI failed ({pi_status}): {reason}", txn["id"])
                        )
                        conn.commit()
                        logger.warning(f"Txn {txn['id']}: PI {txn['stripe_payment_intent_id']} async failure ({pi_status}) → charge_retry")
                except Exception as e:
                    logger.warning(f"Txn {txn['id']}: PI poll failed — {e}")

        # ---- POLL Stripe Connect accounts for artist payout restrictions ----
        # account.updated webhooks fire as v2 events which our handler may not receive.
        # Re-verify any artist whose transfers are stalled to catch silent account restrictions.
        stalled_artists = conn.execute("""
            SELECT DISTINCT eps.stripe_connect_account_id, a.id as artist_id, a.name as artist_name
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            JOIN artists a ON a.id = t.artist_id
            JOIN entity_payment_settings eps ON eps.entity_id = a.id AND eps.entity_type = 'artist'
            WHERE t.status IN ('transfer_failed', 'pending_transfer')
              AND eps.stripe_connect_account_id IS NOT NULL
              AND eps.stripe_connect_onboarding_complete = 1
        """).fetchall()

        if stalled_artists and has_stripe_key and payments_live:
            for row in stalled_artists:
                try:
                    acct = stripe.Account.retrieve(row["stripe_connect_account_id"])
                    payouts_ok = bool(getattr(acct, "payouts_enabled", False))
                    charges_ok = bool(getattr(acct, "charges_enabled", False))
                    if not payouts_ok:
                        conn.execute(
                            "UPDATE entity_payment_settings SET stripe_connect_onboarding_complete = 0 WHERE stripe_connect_account_id = ?",
                            (row["stripe_connect_account_id"],)
                        )
                        conn.commit()
                        logger.warning(f"Polled: artist {row['artist_id']} ({row['artist_name']}) Connect account payouts disabled — flagged")
                except Exception as e:
                    logger.warning(f"Artist account poll failed for {row['stripe_connect_account_id']}: {e}")

        conn.close()

    except ImportError:
        logger.error("[PayoutScheduler] stripe package not installed — payments CANNOT process")
        try:
            conn2 = _raw_db_conn()
            conn2.row_factory = sqlite3.Row
            _send_admin_alert(conn2, "CRITICAL: Stripe Package Not Installed",
                "<p>The payout scheduler cannot run because the <code>stripe</code> Python package is not installed on the server.</p>"
                "<p>Run: <code>pip install stripe</code> and restart the service.</p>")
            conn2.close()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Payout scheduler error: {e}", exc_info=True)
        try:
            conn2 = _raw_db_conn()
            conn2.row_factory = sqlite3.Row
            _send_admin_alert(conn2, "Payout Scheduler Error",
                f"<p>The payout scheduler encountered an unexpected error:</p>"
                f"<p><code>{str(e)[:500]}</code></p>"
                f"<p>Check server logs for full details.</p>")
            conn2.close()
        except Exception:
            pass


def _transfer_to_artists(conn, stripe, payout_rows, charge_id, venue_id, parent_txn_id, venue_charge_cents):
    """Transfer payout to each artist. Called after venue charge succeeds."""
    smtp_settings = _get_smtp_settings(conn)
    for payout in payout_rows:
        payout_id  = payout["id"]
        to_user_id = payout["to_user_id"]

        artist_row = conn.execute("""
            SELECT eps.stripe_connect_account_id, eps.stripe_connect_onboarding_complete
            FROM entity_payment_settings eps
            JOIN artists a ON a.id = eps.entity_id AND eps.entity_type = 'artist'
            WHERE a.user_id = ?
        """, (to_user_id,)).fetchone()

        if not artist_row or not artist_row["stripe_connect_account_id"] or not artist_row["stripe_connect_onboarding_complete"]:
            conn.execute(
                "UPDATE transactions SET status = 'pending_transfer', notes = ? WHERE id = ?",
                ("Artist has not completed Stripe Connect onboarding", payout_id)
            )
            conn.commit()
            logger.warning(f"Payout {payout_id}: Artist not onboarded — pending_transfer")
            try:
                a_info = conn.execute("SELECT a.id, a.name FROM artists a WHERE a.user_id = ?", (to_user_id,)).fetchone()
                if a_info:
                    for email in _get_entity_emails(conn, 'artist', a_info["id"]):
                        _send_html_email(smtp_settings, email,
                            "Action Required: Complete Payment Setup to Receive Your Pay",
                            f"""<p>Hi {a_info['name'] or 'there'},</p>
                            <p>Payment for your gig has been processed but we can't send your payout yet —
                            your Stripe account is not fully set up.</p>
                            <p>Log into GigsFill, go to your Payments tab, and complete Stripe Connect onboarding.
                            Your payout will be sent automatically once done.</p>
                            <p>— The GigsFill Team</p>""")
            except Exception as e:
                logger.error(f"Artist onboarding notification error: {e}")
            _send_admin_alert(conn, f"Artist Not Onboarded — Transfer Blocked (Payout #{payout_id})",
                f"""<p>Venue charged ${venue_charge_cents/100:.2f} for Gig #{payout['gig_id']} but artist is not onboarded.</p>
                <p>Payout of ${payout['artist_payout_cents']/100:.2f} is held until artist completes Stripe Connect.</p>""")
            continue

        try:
            if (payout.get("artist_payout_cents") or 0) <= 0:
                logger.error(f"Payout {payout_id}: artist_payout_cents={payout.get('artist_payout_cents')} — SKIPPING zero/negative transfer")
                conn.execute(
                    "UPDATE transactions SET status = 'transfer_failed', notes = ? WHERE id = ?",
                    ("Blocked: artist_payout_cents is zero or negative", payout_id)
                )
                conn.commit()
                continue

            transfer_kwargs = dict(
                amount=payout["artist_payout_cents"],
                currency="usd",
                destination=artist_row["stripe_connect_account_id"],
                metadata={
                    "gig_id": str(payout["gig_id"]),
                    "transaction_id": str(payout_id),
                    "parent_transaction_id": str(parent_txn_id),
                    "platform": "gigsfill"
                },
                description=f"GigsFill Gig #{payout['gig_id']} artist payout"
            )
            if charge_id:
                transfer_kwargs["source_transaction"] = charge_id
            transfer = stripe.Transfer.create(**transfer_kwargs)
            conn.execute(
                "UPDATE transactions SET status = 'transferred', stripe_transfer_id = ?, processed_at = ? WHERE id = ?",
                (transfer.id, utcnow_naive().isoformat(), payout_id)
            )
            conn.commit()
            logger.info(f"Payout {payout_id}: TRANSFERRED ${payout['artist_payout_cents']/100:.2f} -> {artist_row['stripe_connect_account_id']}")
            _send_payout_email(conn, payout)
            # Accrue affiliate earnings on transfer (don't wait for poll to confirm 'paid')
            try:
                from backend.routes.affiliate import accrue_affiliate_earnings
                from backend.db import SessionLocal as _SL
                _aff_db = _SL()
                try:
                    accrue_affiliate_earnings(_aff_db, payout_id)
                finally:
                    _aff_db.close()
            except Exception as _ae:
                logger.warning(f"Affiliate accrual error (transferred) txn {payout_id}: {_ae}")

        except Exception as e:
            conn.execute(
                "UPDATE transactions SET status = 'transfer_failed', notes = ? WHERE id = ?",
                (f"Transfer failed: {str(e)[:200]}", payout_id)
            )
            conn.commit()
            logger.error(f"Payout {payout_id}: TRANSFER FAILED - {e}")
            _send_transfer_failed_emails(conn, payout, venue_id)
            _send_admin_alert(conn, f"Transfer Failed — Artist NOT Paid (Payout #{payout_id})",
                f"""<p><strong>{_get_gig_summary(conn, payout)}</strong></p>
                <p>Venue charged ${venue_charge_cents/100:.2f} but transfer to artist failed.</p>
                <p><strong>Error:</strong> {str(e)[:200]}</p>
                <p>Artist payout: ${payout['artist_payout_cents']/100:.2f}</p>""")



def _handle_charge_failure(conn, txn, venue_id, attempts, reason, tz):
    """Handle a failed charge attempt — retry or suspend"""
    txn_id = txn["id"]
    new_attempts = attempts + 1

    if new_attempts >= MAX_CHARGE_ATTEMPTS:
        conn.execute(
            """UPDATE transactions SET status = 'payment_failed',
               charge_attempts = ?, last_charge_attempt_at = ?,
               charge_failure_reason = ? WHERE id = ?""",
            (new_attempts, utcnow_naive().isoformat(), reason, txn_id)
        )
        conn.execute(
            """UPDATE venues SET payment_status = 'suspended',
               payment_suspended_at = ?,
               payment_suspension_reason = ? WHERE id = ?""",
            (utcnow_naive().isoformat(), f"Payment failed: {reason}", venue_id)
        )
        conn.commit()
        logger.error(f"Txn {txn_id}: FAILED final ({new_attempts}/{MAX_CHARGE_ATTEMPTS}) - {reason}, venue {venue_id} suspended")
        _send_charge_failed_email(conn, txn, reason)
        _send_venue_suspended_email(conn, venue_id, reason)
    else:
        retry_at = (datetime.now(tz) + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            """UPDATE transactions SET status = 'charge_retry',
               charge_attempts = ?, last_charge_attempt_at = ?,
               charge_failure_reason = ?, scheduled_process_at = ?
               WHERE id = ?""",
            (new_attempts, utcnow_naive().isoformat(), reason, retry_at, txn_id)
        )
        conn.commit()
        logger.error(f"Txn {txn_id}: Charge failed, retry {new_attempts}/{MAX_CHARGE_ATTEMPTS} scheduled for {retry_at}")
        _send_venue_payment_warning(conn, txn, venue_id, new_attempts)


def _settle_test_transactions():
    """Mark test 'transferred' transactions as 'paid' after 2 hours. Runs every minute."""
    try:
        conn = _raw_db_conn()
        transferred = conn.execute("""
            SELECT id, processed_at FROM transactions
            WHERE status = 'transferred'
              AND stripe_transfer_id = 'test_transfer'
        """).fetchall()
        for row in transferred:
            if row["processed_at"]:
                try:
                    proc = datetime.fromisoformat(str(row["processed_at"]).replace("Z",""))
                    if (utcnow_naive() - proc).total_seconds() >= 7200:
                        conn.execute(
                            "UPDATE transactions SET status = 'paid', notes = COALESCE(notes || ' | ', '') || 'Test: auto-settled after 2h' WHERE id = ?",
                            (row["id"],)
                        )
                        conn.commit()
                        logger.info(f"Txn {row['id']}: TEST auto-settled to paid")
                except Exception:
                    pass
        conn.close()
    except Exception as e:
        logger.warning(f"_settle_test_transactions error: {e}")


def scheduler_loop():
    logger.info("[PayoutScheduler] Started background payout scheduler")
    last_swept_hour = None
    last_affiliate_payout_date = None
    while True:
        try:
            tz = get_platform_timezone()
            now = datetime.now(tz)

            # Always: settle test transactions after 2h (runs every loop = every minute)
            _settle_test_transactions()

            # Hourly sweep. process_payouts_now()'s SQL gate is `scheduled_process_at <= now`
            # (naive UTC), so calling once per hour honors per-venue local payout times within
            # ~1h regardless of the platform timezone, and also retries any stalled
            # transfers / picks up charge_retry rows. See doc Changelog 2026-05-04 + 2026-05-07.
            if last_swept_hour != now.hour:
                logger.info(f"Running payouts sweep at {now.strftime('%Y-%m-%d %H:%M %Z')}")
                process_payouts_now()
                last_swept_hour = now.hour

            # Quarterly affiliate payouts: Apr 1, Jul 1, Oct 1, Dec 31
            # Auto-runs at 9:00 AM platform time: processes Stripe transfers for eligible
            # affiliates, sends each affiliate a summary email, and emails admin a report.
            _aff_dates = {(4, 1), (7, 1), (10, 1), (12, 31)}
            if ((now.month, now.day) in _aff_dates and
                    now.hour == 9 and now.minute < 2 and
                    last_affiliate_payout_date != now.date()):
                logger.info(f"Running quarterly affiliate payouts for {now.strftime('%Y-%m-%d')}")
                try:
                    from backend.routes.affiliate import run_quarterly_affiliate_payouts, send_quarterly_affiliate_reminder
                    from backend.db import SessionLocal as _SL
                    _aff_db = _SL()
                    try:
                        # 1. Auto-run all eligible payouts via Stripe
                        run_quarterly_affiliate_payouts(_aff_db)
                        # 2. Send admin summary email with results
                        send_quarterly_affiliate_reminder(_aff_db)
                    finally:
                        _aff_db.close()
                    last_affiliate_payout_date = now.date()
                except Exception as _ae:
                    logger.error(f"Quarterly affiliate payout error: {_ae}")
        except Exception as e:
            logger.error(f"Loop error: {e}")
        time.sleep(60)


def start_payout_scheduler():
    thread = threading.Thread(target=scheduler_loop, daemon=True, name="PayoutScheduler")
    thread.start()
    logger.info("[PayoutScheduler] Background thread started")


# =====================================================
# EMAIL HELPERS
# =====================================================

def _get_smtp_settings(conn):
    settings = {}
    for row in conn.execute(
        "SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port', 'platform_email_from_name')"
    ).fetchall():
        settings[row["setting_key"]] = row["setting_value"]
    return settings

def _send_html_email(settings, to_email, subject, html_body):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.utils import formataddr

    from_email = settings.get("platform_email", "")
    from_name  = settings.get("platform_email_from_name", "GigsFill")
    email_pass = settings.get("platform_email_password", "")
    smtp_server = settings.get("platform_smtp_server", "")
    smtp_port = int(settings.get("platform_smtp_port", "587"))
    if not from_email or not email_pass:
        return

    styled = f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
    <div style="max-width:600px;margin:20px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <div style="background:#1a1f2e;padding:24px;text-align:center;"><span style="color:#fff;font-size:20px;font-weight:700;letter-spacing:4px;">GIGSFILL</span></div>
    <div style="padding:32px 24px;font-size:14px;color:#333;line-height:1.6;">{html_body}</div>
    </div></body></html>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = formataddr((from_name, from_email))
    msg['To'] = to_email
    msg.attach(MIMEText(styled, 'html'))
    try:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        server.starttls()
        server.login(from_email, email_pass)
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        logger.error(f"SMTP error for {to_email}: {e}")

def _get_entity_emails(conn, entity_type, entity_id):
    if entity_type == 'venue':
        return [r["email"] for r in conn.execute("""
            SELECT u.email FROM users u JOIN venues v ON v.user_id = u.id WHERE v.id = ?
            UNION SELECT u.email FROM users u JOIN entity_users eu ON u.id = eu.user_id
            WHERE eu.entity_type = 'venue' AND eu.entity_id = ?
        """, (entity_id, entity_id)).fetchall()]
    else:
        return [r["email"] for r in conn.execute("""
            SELECT u.email FROM users u JOIN artists a ON a.user_id = u.id WHERE a.id = ?
            UNION SELECT u.email FROM users u JOIN entity_users eu ON u.id = eu.user_id
            WHERE eu.entity_type = 'artist' AND eu.entity_id = ?
        """, (entity_id, entity_id)).fetchall()]


def _get_admin_emails(conn):
    """Get admin alert email — uses admin_alert_email setting, falls back to is_admin users"""
    try:
        row = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'admin_alert_email'"
        ).fetchone()
        if row and row["setting_value"] and row["setting_value"].strip():
            return [row["setting_value"].strip()]
    except Exception:
        pass
    return [r["email"] for r in conn.execute(
        "SELECT email FROM users WHERE is_admin = 'true' OR is_admin = 1"
    ).fetchall()]


def _pref_enabled(conn, entity_type, entity_id, notification_type):
    """Check email_preferences for an entity. Returns True if enabled (or no row = default True)."""
    try:
        if entity_type == 'venue':
            user_row = conn.execute(
                "SELECT user_id FROM venues WHERE id = ?", (entity_id,)
            ).fetchone()
        else:
            user_row = conn.execute(
                "SELECT user_id FROM artists WHERE id = ?", (entity_id,)
            ).fetchone()
        if not user_row:
            return True
        user_id = user_row["user_id"]
        pref = conn.execute(
            "SELECT enabled FROM email_preferences WHERE user_id = ? AND notification_type = ?",
            (user_id, notification_type)
        ).fetchone()
        if pref is None:
            return True  # no row = default enabled
        return bool(pref["enabled"])
    except Exception:
        return True  # fail open


def _send_admin_alert(conn, subject, html_body):
    """Send an alert email to all admin users"""
    try:
        settings = _get_smtp_settings(conn)
        admin_emails = _get_admin_emails(conn)
        if not admin_emails:
            logger.error(f"ADMIN ALERT (no admin emails configured): {subject}")
            return
        for email in admin_emails:
            _send_html_email(settings, email, f"🚨 GigsFill Admin Alert: {subject}", html_body)
        logger.info(f"Admin alert sent to {len(admin_emails)} admin(s): {subject}")
    except Exception as e:
        logger.error(f"Failed to send admin alert: {e}")


def _format_time_12h(time_str):
    """Convert 24h time string (e.g. '19:00') to 12h format (e.g. '7:00 PM')"""
    if not time_str:
        return ''
    try:
        h, m = time_str.strip()[:5].split(':')
        h = int(h)
        suffix = 'AM' if h < 12 else 'PM'
        h = h % 12 or 12
        return f"{h}:{m} {suffix}"
    except:
        return time_str


def _get_gig_summary(conn, txn):
    """Get a formatted summary of a transaction's gig details for emails"""
    gig = conn.execute("""
        SELECT g.date, g.id, v.venue_name
        FROM gigs g
        JOIN venues v ON g.venue_id = v.id
        WHERE g.id = ?
    """, (txn["gig_id"],)).fetchone()
    if not gig:
        return f"Gig #{txn['gig_id']}"

    # Get artist from transaction's artist_id (correct for slot-based gigs)
    artist_name = "Unknown Artist"
    if txn["artist_id"]:
        a = conn.execute("SELECT name FROM artists WHERE id = ?", (txn["artist_id"],)).fetchone()
        if a:
            artist_name = a["name"]

    # Get slot time if available
    time_part = ""
    if txn.get("artist_id"):
        slot = conn.execute("""
            SELECT start_time, end_time FROM gig_slots
            WHERE gig_id = ? AND artist_id = ?
            ORDER BY id DESC LIMIT 1
        """, (txn["gig_id"], txn["artist_id"])).fetchone()
        if slot:
            start = _format_time_12h(slot["start_time"])
            end = _format_time_12h(slot["end_time"])
            if start and end:
                time_part = f" at {start}–{end}"

    return f"Gig #{gig['id']} — {artist_name} at {gig['venue_name']} on {gig['date']}{time_part}"


def _send_payout_email(conn, txn):
    try:
        gig_info = conn.execute("""
            SELECT g.date, g.pay, v.venue_name, a.name as artist_name, a.id as artist_id
            FROM gigs g JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.user_id = ?
            WHERE g.id = ?
        """, (txn["to_user_id"], txn["gig_id"])).fetchone()
        if not gig_info:
            return

        settings = _get_smtp_settings(conn)
        amount = txn["amount_cents"] or txn["artist_payout_cents"]
        payout = txn["artist_payout_cents"]
        fee = amount - payout

        tmpl = conn.execute(
            "SELECT subject, body FROM email_templates WHERE template_key = 'artist_payment_sent'"
        ).fetchone()
        if not tmpl:
            # Fallback: send simple email
            emails = _get_entity_emails(conn, 'artist', gig_info["artist_id"])
            for email in emails:
                _send_html_email(settings, email,
                    f"You've been paid for your gig at {gig_info['venue_name']}!",
                    f"""<p>Hi {gig_info['artist_name'] or 'there'},</p>
                    <p>Great news! Payment for your gig at <strong>{gig_info['venue_name']}</strong>
                    on <strong>{gig_info['date']}</strong> has been processed.</p>
                    <p style="font-size:18px;font-weight:700;color:#10b981;">Payout: ${payout/100:.2f}</p>
                    <p>The funds will appear in your connected Stripe account shortly.</p>
                    <p>— The GigsFill Team</p>""")
            return

        variables = {
            'artist_name': gig_info['artist_name'] or 'Artist',
            'venue_name': gig_info['venue_name'],
            'date': format_email_date(gig_info['date']),
            'pay': f"{amount/100:.2f}",
            'artist_fee': f"{fee/100:.2f}",
            'payout_amount': f"{payout/100:.2f}",
            'slot_times': _compute_slot_times_sqlite(conn, txn["gig_id"], artist_id=gig_info.get("artist_id")),
        }
        subject = tmpl["subject"]
        body = tmpl["body"]
        for k, v in variables.items():
            subject = subject.replace("{{" + k + "}}", str(v))
            body = body.replace("{{" + k + "}}", str(v))

        if _pref_enabled(conn, 'artist', gig_info["artist_id"], 'artist_payment_sent'):
            emails = _get_entity_emails(conn, 'artist', gig_info["artist_id"])
            for email in emails:
                _send_html_email(settings, email, subject, body)

        logger.info(f"Payout email sent for txn {txn['id']}")
    except Exception as e:
        logger.error(f"Payout email error: {e}")


def _send_venue_charged_email(conn, txn, venue_id):
    """Notify venue their card was charged — uses venue_payment_charged email template"""
    try:
        gig_info = conn.execute("""
            SELECT g.id as gig_id, g.date, g.pay, v.id as vid, v.venue_name,
                   a.name as artist_name
            FROM gigs g JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.user_id = ?
            WHERE g.id = ?
        """, (txn["to_user_id"], txn["gig_id"])).fetchone()
        if not gig_info:
            return

        settings = _get_smtp_settings(conn)

        # Load template from DB
        tmpl_row = conn.execute(
            "SELECT subject, body FROM email_templates WHERE template_key = 'venue_payment_charged' LIMIT 1"
        ).fetchone()
        if tmpl_row:
            tmpl = {"subject": tmpl_row[0], "body": tmpl_row[1]}
        else:
            # Fallback subject/body if template missing
            tmpl = {
                "subject": f"Payment processed — {gig_info['artist_name'] or 'Artist'} gig on {gig_info['date']}",
                "body": f"<p>Your card was charged ${txn['venue_charge_cents']/100:.2f} for {gig_info['artist_name'] or 'the artist'}'s gig on {gig_info['date']}.</p>"
            }

        pay = txn["amount_cents"] / 100
        venue_fee = (txn["venue_charge_cents"] - txn["amount_cents"]) / 100
        total_charged = txn["venue_charge_cents"] / 100

        # Build artist list for multi-slot gigs
        artist_name = gig_info["artist_name"] or "Artist"
        if txn.get("transaction_type") == "venue_charge":
            # Get all artists paid in this charge
            payout_rows = conn.execute("""
                SELECT a.name FROM transactions t
                JOIN artists a ON a.id = t.artist_id
                WHERE t.parent_transaction_id = ? AND t.transaction_type = 'artist_payout'
            """, (txn["id"],)).fetchall()
            if payout_rows:
                artist_name = ", ".join(r["name"] for r in payout_rows)
        elif txn["artist_id"]:
            a_row = conn.execute("SELECT name FROM artists WHERE id = ?", (txn["artist_id"],)).fetchone()
            if a_row:
                artist_name = a_row["name"]

        variables = {
            "venue_name": gig_info["venue_name"] or "",
            "artist_name": artist_name,
            "date": gig_info["date"] or "",
            "pay": f"{pay:.2f}",
            "venue_fee": f"{venue_fee:.2f}",
            "total_charged": f"{total_charged:.2f}",
            "slot_times": _compute_slot_times_sqlite(conn, txn["gig_id"]),
            "venue_id": str(gig_info["vid"] or venue_id),
            "gig_id": str(gig_info["gig_id"] or ""),
        }

        subject = tmpl["subject"]
        body = tmpl["body"]
        for k, v in variables.items():
            subject = subject.replace("{{" + k + "}}", str(v))
            body = body.replace("{{" + k + "}}", str(v))

        # Apply conditional block rendering (same as other email functions)
        import re as _re
        def _render_conditional(text, vars_dict):
            def replacer(m):
                key, inner = m.group(1), m.group(2)
                val = vars_dict.get(key, "")
                return inner if val and str(val).strip() not in ("", "None") else ""
            return _re.sub(r'\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}', replacer, text, flags=_re.DOTALL)
        body = _render_conditional(body, variables)

        if _pref_enabled(conn, 'venue', venue_id, 'venue_payment_charged'):
            emails = _get_entity_emails(conn, 'venue', venue_id)
            for email in emails:
                _send_html_email(settings, email, subject, body)

        logger.info(f"Venue charged email sent for txn {txn['id']}")
    except Exception as e:
        logger.error(f"Venue charged email error: {e}")


def _send_charge_failed_email(conn, txn, reason):
    """Notify artist that venue payment failed permanently"""
    try:
        gig_info = conn.execute("""
            SELECT g.date, v.venue_name, a.name as artist_name, a.id as artist_id
            FROM gigs g JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.user_id = ?
            WHERE g.id = ?
        """, (txn["to_user_id"], txn["gig_id"])).fetchone()
        if not gig_info:
            return
        settings = _get_smtp_settings(conn)
        emails = _get_entity_emails(conn, 'artist', gig_info["artist_id"])
        for email in emails:
            _send_html_email(settings, email,
                f"Payment Issue - {gig_info['venue_name']} gig on {gig_info['date']}",
                f"""<p>Hi {gig_info['artist_name'] or 'there'},</p>
                <p>We were unable to collect payment from <strong>{gig_info['venue_name']}</strong>
                for your gig on <strong>{gig_info['date']}</strong> after multiple attempts.</p>
                <p><strong>Reason:</strong> {reason}</p>
                <p>We've notified the venue and suspended their account until payment is resolved.
                We'll continue working to get you paid.</p>
                <p>— The GigsFill Team</p>""")
    except Exception as e:
        logger.error(f"Charge failed email error: {e}")


def _send_venue_payment_warning(conn, txn, venue_id, attempt):
    """Warn venue that their card charge failed - retry coming"""
    try:
        venue_info = conn.execute("SELECT venue_name FROM venues WHERE id = ?", (venue_id,)).fetchone()
        gig_info = conn.execute("SELECT date FROM gigs WHERE id = ?", (txn["gig_id"],)).fetchone()
        if not venue_info:
            return
        remaining = MAX_CHARGE_ATTEMPTS - attempt
        settings = _get_smtp_settings(conn)
        emails = _get_entity_emails(conn, 'venue', venue_id)
        for email in emails:
            _send_html_email(settings, email,
                f"⚠️ Payment Failed - Action Required",
                f"""<p>Hi there,</p>
                <p>We attempted to charge your card for the gig on <strong>{gig_info['date'] if gig_info else 'N/A'}</strong>
                at <strong>{venue_info['venue_name']}</strong>, but the charge was declined.</p>
                <div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:16px;margin:16px 0;">
                <p style="margin:0;color:#92400e;font-weight:600;">Attempt {attempt} of {MAX_CHARGE_ATTEMPTS}.
                {'We will retry tomorrow.' if remaining > 0 else 'This was the final attempt.'}</p>
                </div>
                <p>{'Please update your payment card immediately to avoid service interruption and venue suspension.' if remaining > 0 else 'Your venue has been suspended. Please update your payment card to reactivate.'}</p>
                <p>— The GigsFill Team</p>""")
    except Exception as e:
        logger.error(f"Venue warning email error: {e}")


def _send_venue_suspended_email(conn, venue_id, reason):
    """Notify venue they've been suspended"""
    try:
        venue_info = conn.execute("SELECT venue_name FROM venues WHERE id = ?", (venue_id,)).fetchone()
        if not venue_info:
            return
        settings = _get_smtp_settings(conn)
        emails = _get_entity_emails(conn, 'venue', venue_id)
        for email in emails:
            _send_html_email(settings, email,
                f"🚫 Venue Suspended - {venue_info['venue_name']}",
                f"""<p>Hi there,</p>
                <p>Your venue <strong>{venue_info['venue_name']}</strong> has been
                <span style="color:#dc2626;font-weight:700;">suspended</span> due to payment issues.</p>
                <p><strong>Reason:</strong> {reason}</p>
                <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:16px;margin:16px 0;">
                <p style="margin:0;color:#991b1b;font-weight:600;">While suspended:</p>
                <ul style="color:#991b1b;margin:8px 0;">
                <li>Your venue profile is hidden from artists</li>
                <li>Your gigs are not visible in search results</li>
                <li>Artists with booked gigs have been notified of the payment issue</li>
                </ul>
                </div>
                <p>To reactivate, log in and add a valid payment card in your Payments tab.</p>
                <p>— The GigsFill Team</p>""")
    except Exception as e:
        logger.error(f"Venue suspended email error: {e}")


def _send_transfer_failed_emails(conn, txn, venue_id):
    """Notify artist and venue when transfer fails (admin-editable templates)"""
    try:
        gig_info = conn.execute("""
            SELECT g.date, g.start_time, g.end_time, g.pay, v.venue_name,
                   a.name as artist_name, a.id as artist_id
            FROM gigs g
            JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.user_id = ?
            WHERE g.id = ?
        """, (txn["to_user_id"], txn["gig_id"])).fetchone()
        if not gig_info:
            return

        settings = _get_smtp_settings(conn)
        gig_summary = _get_gig_summary(conn, txn)
        payout = f"{txn['artist_payout_cents']/100:.2f}"
        venue_charge = f"{txn['venue_charge_cents']/100:.2f}"

        # --- Artist email ---
        tmpl = conn.execute(
            "SELECT subject, body FROM email_templates WHERE template_key = 'transfer_failed_artist'"
        ).fetchone()

        variables = {
            'artist_name': gig_info['artist_name'] or 'Artist',
            'venue_name': gig_info['venue_name'],
            'date': format_email_date(gig_info['date']),
            'start_time': _format_time_12h(gig_info['start_time']),
            'end_time': _format_time_12h(gig_info['end_time']),
            'gig_summary': gig_summary,
            'payout_amount': payout,
            'venue_charge': venue_charge,
        }

        if tmpl:
            subject = tmpl["subject"]
            body = tmpl["body"]
            for k, v in variables.items():
                subject = subject.replace("{{" + k + "}}", str(v))
                body = body.replace("{{" + k + "}}", str(v))
        else:
            subject = f"Payment Update — {gig_info['venue_name']} gig on {gig_info['date']}"
            body = f"""<p>Hi {gig_info['artist_name'] or 'there'},</p>
                <p><strong>{gig_summary}</strong></p>
                <p>The venue was successfully charged but the transfer to you failed.</p>
                <p>Artist payout: <strong>${payout}</strong></p>
                <p>The GigsFill team is working on this issue and you will receive your payment as soon as possible.</p>
                <p>— The GigsFill Team</p>"""

        if _pref_enabled(conn, 'artist', gig_info["artist_id"], 'artist_venue_payment_issue'):
            artist_emails = _get_entity_emails(conn, 'artist', gig_info["artist_id"])
            for email in artist_emails:
                _send_html_email(settings, email, subject, body)
        logger.info(f"Transfer failed email sent to artist for txn {txn['id']}")

        # --- Venue email ---
        tmpl = conn.execute(
            "SELECT subject, body FROM email_templates WHERE template_key = 'transfer_failed_venue'"
        ).fetchone()

        if tmpl:
            subject = tmpl["subject"]
            body = tmpl["body"]
            for k, v in variables.items():
                subject = subject.replace("{{" + k + "}}", str(v))
                body = body.replace("{{" + k + "}}", str(v))
        else:
            subject = f"Payment Update — {gig_info['artist_name'] or 'Artist'} gig on {gig_info['date']}"
            body = f"""<p>Hi there,</p>
                <p><strong>{gig_summary}</strong></p>
                <p>You were charged <strong>${venue_charge}</strong> but the transfer to the artist failed.</p>
                <p>Artist payout: <strong>${payout}</strong></p>
                <p>The GigsFill team is working on this issue and the artist will receive their payment as soon as possible.</p>
                <p>— The GigsFill Team</p>"""

        if _pref_enabled(conn, 'venue', venue_id, 'transfer_failed_venue'):
            venue_emails = _get_entity_emails(conn, 'venue', venue_id)
            for email in venue_emails:
                _send_html_email(settings, email, subject, body)
        logger.info(f"Transfer failed email sent to venue for txn {txn['id']}")

    except Exception as e:
        logger.error(f"Transfer failed email error: {e}")
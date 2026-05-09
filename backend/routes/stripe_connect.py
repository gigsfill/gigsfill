"""
Stripe Connect Express - Payment System
Handles:
  - Venue: Save card via SetupIntent, charge at booking
  - Artist: Stripe Connect Express onboarding, receive payouts
  - Platform: Charge venue → hold funds → auto-payout to artist next day 5pm
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
from datetime import datetime, timedelta
from backend.utils import utcnow_naive
import os
import logging
from backend.services.email_dispatch import format_email_date
logger = logging.getLogger("gigsfill.stripe")


router = APIRouter()


def get_stripe():
    """Get configured stripe module"""
    import stripe
    return stripe


def get_stripe_keys(db):
    """Load Stripe keys from platform_settings"""
    rows = db.execute(
        text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key LIKE 'admin_stripe%' OR setting_key IN ('platform_fee_percent', 'platform_min_fee', 'platform_fee_split', 'payments_enabled')")
    ).fetchall()
    keys = {r[0]: r[1] for r in rows}
    return {
        'secret_key': keys.get('admin_stripe_secret_key', ''),
        'publishable_key': keys.get('admin_stripe_publishable_key', ''),
        'webhook_secret': keys.get('admin_stripe_webhook_secret', ''),
        'platform_fee_percent': float(keys.get('platform_fee_percent', '10')),
        'platform_min_fee': float(keys.get('platform_min_fee', '20')),
        'platform_fee_split': keys.get('platform_fee_split', 'split'),
        'payments_enabled': keys.get('payments_enabled', '0') in ('1', 'true'),
    }


def init_stripe(db):
    """Initialize stripe with platform secret key"""
    stripe = get_stripe()
    keys = get_stripe_keys(db)
    if not keys['secret_key']:
        raise HTTPException(500, "Stripe is not configured. Please add your Stripe API keys in Admin Settings.")
    stripe.api_key = keys['secret_key']
    return stripe, keys


# =====================================================
# VENUE: Card Management (Stripe Customer + SetupIntent)
# =====================================================

@router.get("/api/stripe/config")
def get_stripe_config(db=Depends(get_db)):
    """Return publishable key and platform fee for frontend"""
    keys = get_stripe_keys(db)
    total_fee = keys.get('platform_fee_percent', 10)
    fee_split = keys.get('platform_fee_split', 'split')
    if fee_split == 'venue_only':
        venue_fee_pct = total_fee
        artist_fee_pct = 0
    elif fee_split == 'artist_only':
        venue_fee_pct = 0
        artist_fee_pct = total_fee
    else:  # split — 50/50
        venue_fee_pct = total_fee / 2
        artist_fee_pct = total_fee / 2
    return {
        "publishable_key": keys['publishable_key'],
        "platform_fee_percent": venue_fee_pct,
        "artist_fee_percent": artist_fee_pct,
    }


@router.post("/api/stripe/venue/{venue_id}/setup-intent")
def create_venue_setup_intent(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Create a SetupIntent for venue to save a card"""
    stripe, keys = init_stripe(db)
    
    # Verify venue ownership
    venue = db.execute(
        text("SELECT id, venue_name, user_id FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    if not venue or venue["user_id"] != user.id:
        raise HTTPException(403, "Not your venue")
    
    # Get or create Stripe customer
    settings = db.execute(
        text("SELECT stripe_customer_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    
    customer_id = settings["stripe_customer_id"] if settings and settings.get("stripe_customer_id") else None
    
    if not customer_id:
        # Create Stripe Customer
        customer = stripe.Customer.create(
            name=venue["venue_name"],
            metadata={"venue_id": str(venue_id), "platform": "gigsfill"}
        )
        customer_id = customer.id
        
        # Upsert entity_payment_settings
        db.execute(
            text("""
                INSERT INTO entity_payment_settings (entity_type, entity_id, stripe_customer_id, created_at, updated_at)
                VALUES ('venue', :vid, :cid, :now, :now)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET stripe_customer_id = :cid, updated_at = :now
            """),
            {"vid": venue_id, "cid": customer_id, "now": utcnow_naive()}
        )
        db.commit()
    
    # Create SetupIntent
    setup_intent = stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
        metadata={"venue_id": str(venue_id)}
    )
    
    return {"client_secret": setup_intent.client_secret, "customer_id": customer_id}


@router.post("/api/stripe/venue/{venue_id}/save-payment-method")
def save_venue_payment_method(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Save the payment method after SetupIntent completes"""
    stripe, keys = init_stripe(db)
    payment_method_id = data.get("payment_method_id")
    
    if not payment_method_id:
        raise HTTPException(400, "payment_method_id required")
    
    # Verify venue ownership
    venue = db.execute(
        text("SELECT user_id FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    if not venue or venue["user_id"] != user.id:
        raise HTTPException(403, "Not your venue")
    
    # Get customer ID
    settings = db.execute(
        text("SELECT stripe_customer_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    
    if not settings or not settings.get("stripe_customer_id"):
        raise HTTPException(400, "No Stripe customer found. Please try again.")
    
    # Set as default payment method on customer
    stripe.Customer.modify(
        settings["stripe_customer_id"],
        invoice_settings={"default_payment_method": payment_method_id}
    )
    
    # Save in our DB
    db.execute(
        text("""
            UPDATE entity_payment_settings 
            SET stripe_payment_method_id = :pmid, default_payment_method = 'stripe', updated_at = :now
            WHERE entity_type = 'venue' AND entity_id = :vid
        """),
        {"pmid": payment_method_id, "vid": venue_id, "now": utcnow_naive()}
    )
    
    # Reactivate venue if it was suspended
    db.execute(
        text("""UPDATE venues SET payment_status = 'active', 
                payment_suspended_at = NULL, payment_suspension_reason = NULL
                WHERE id = :vid AND payment_status = 'suspended'"""),
        {"vid": venue_id}
    )
    
    db.commit()
    
    return {"ok": True, "reactivated": True}


@router.get("/api/stripe/venue/{venue_id}/payment-method")
def get_venue_payment_method(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get venue's saved card info"""
    stripe, keys = init_stripe(db)
    
    settings = db.execute(
        text("SELECT stripe_customer_id, stripe_payment_method_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    
    if not settings or not settings.get("stripe_payment_method_id"):
        return {"has_card": False}
    
    try:
        pm = stripe.PaymentMethod.retrieve(settings["stripe_payment_method_id"])
        return {
            "has_card": True,
            "brand": pm.card.brand,
            "last4": pm.card.last4,
            "exp_month": pm.card.exp_month,
            "exp_year": pm.card.exp_year,
            "payment_method_id": pm.id
        }
    except Exception:
        return {"has_card": False}


@router.delete("/api/stripe/venue/{venue_id}/payment-method")
def remove_venue_payment_method(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Remove venue's saved card — triggers suspension if booked gigs exist"""
    stripe, keys = init_stripe(db)
    
    venue = db.execute(
        text("SELECT user_id, venue_name FROM venues WHERE id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    if not venue or venue["user_id"] != user.id:
        raise HTTPException(403, "Not your venue")
    
    settings = db.execute(
        text("SELECT stripe_payment_method_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    
    if settings and settings.get("stripe_payment_method_id"):
        try:
            stripe.PaymentMethod.detach(settings["stripe_payment_method_id"])
        except Exception:
            pass
    
    db.execute(
        text("UPDATE entity_payment_settings SET stripe_payment_method_id = NULL, updated_at = :now WHERE entity_type = 'venue' AND entity_id = :vid"),
        {"vid": venue_id, "now": utcnow_naive()}
    )
    
    # Check for future booked gigs — if any exist, suspend the venue
    booked_count = db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM gigs g
            WHERE g.venue_id = :vid AND g.status = 'booked' AND g.date >= DATE('now', 'localtime')
        """),
        {"vid": venue_id}
    ).mappings().first()
    
    booked_slot_count = db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM gig_slots gs
            JOIN gigs g ON gs.gig_id = g.id
            WHERE g.venue_id = :vid AND gs.status = 'booked' AND g.date >= DATE('now', 'localtime')
        """),
        {"vid": venue_id}
    ).mappings().first()
    
    total_booked = (booked_count["cnt"] if booked_count else 0) + (booked_slot_count["cnt"] if booked_slot_count else 0)
    
    if total_booked > 0:
        # Suspend the venue
        db.execute(
            text("""UPDATE venues SET payment_status = 'suspended', 
                    payment_suspended_at = :now, 
                    payment_suspension_reason = 'Payment card removed with booked gigs'
                    WHERE id = :vid"""),
            {"vid": venue_id, "now": utcnow_naive()}
        )
        
        # Notify all artists with booked gigs at this venue
        _notify_artists_payment_issue(db, venue_id, venue["venue_name"])
    else:
        # No booked gigs — just suspend (no public gigs shown without card)
        db.execute(
            text("""UPDATE venues SET payment_status = 'suspended', 
                    payment_suspended_at = :now, 
                    payment_suspension_reason = 'Payment card removed'
                    WHERE id = :vid"""),
            {"vid": venue_id, "now": utcnow_naive()}
        )
    
    db.commit()
    return {"ok": True, "suspended": True, "booked_gigs_affected": total_booked}


# =====================================================
# ARTIST: Stripe Connect Express Onboarding
# =====================================================

@router.post("/api/stripe/artist/{artist_id}/create-connect-account")
def create_artist_connect_account(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Create Stripe Connect Express account and return onboarding URL"""
    stripe, keys = init_stripe(db)
    
    # Audit fix (May 2026): use multi-user-aware access check. Owner-only
    # gating blocked secondary entity_users (co-managers of multi-user artist
    # accounts) from starting onboarding.
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    artist = db.execute(
        text("SELECT id, name, user_id FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    if not artist:
        raise HTTPException(404, "Artist not found")

    # Get user email
    user_row = db.execute(
        text("SELECT email FROM users WHERE id = :uid"),
        {"uid": user.id}
    ).mappings().first()

    # Check if they already have a connect account
    settings = db.execute(
        text("SELECT stripe_connect_account_id FROM entity_payment_settings WHERE entity_type = 'artist' AND entity_id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    
    account_id = settings["stripe_connect_account_id"] if settings and settings.get("stripe_connect_account_id") else None
    
    if not account_id:
        # Create Express account
        account = stripe.Account.create(
            type="express",
            country="US",
            email=user_row["email"] if user_row else None,
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            business_type="individual",
            metadata={"artist_id": str(artist_id), "platform": "gigsfill"}
        )
        account_id = account.id
        
        # Upsert entity_payment_settings
        db.execute(
            text("""
                INSERT INTO entity_payment_settings (entity_type, entity_id, stripe_connect_account_id, created_at, updated_at)
                VALUES ('artist', :aid, :acid, :now, :now)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET stripe_connect_account_id = :acid, updated_at = :now
            """),
            {"aid": artist_id, "acid": account_id, "now": utcnow_naive()}
        )
        db.commit()
    
    # Create onboarding link
    account_link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=f"https://gigsfill.com/app/artist-book-gigs.html?artist_id={artist_id}&tab=payments&stripe_refresh=1",
        return_url=f"https://gigsfill.com/app/artist-book-gigs.html?artist_id={artist_id}&tab=payments&stripe_return=1",
        type="account_onboarding",
    )
    
    return {"url": account_link.url, "account_id": account_id}


@router.get("/api/stripe/artist/{artist_id}/connect-status")
def get_artist_connect_status(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Check artist's Stripe Connect onboarding status"""
    stripe, keys = init_stripe(db)
    
    settings = db.execute(
        text("SELECT stripe_connect_account_id, stripe_connect_onboarding_complete FROM entity_payment_settings WHERE entity_type = 'artist' AND entity_id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    
    if not settings or not settings.get("stripe_connect_account_id"):
        return {"connected": False, "onboarding_complete": False}
    
    try:
        account = stripe.Account.retrieve(settings["stripe_connect_account_id"])
        onboarding_complete = account.charges_enabled and account.payouts_enabled
        
        # Update our DB if status changed
        if onboarding_complete and not settings.get("stripe_connect_onboarding_complete"):
            db.execute(
                text("UPDATE entity_payment_settings SET stripe_connect_onboarding_complete = 1, updated_at = :now WHERE entity_type = 'artist' AND entity_id = :aid"),
                {"aid": artist_id, "now": utcnow_naive()}
            )
            db.commit()
        
        result = {
            "connected": True,
            "onboarding_complete": onboarding_complete,
            "charges_enabled": account.charges_enabled,
            "payouts_enabled": account.payouts_enabled,
            "account_id": account.id,
        }
        
        # Get bank info if available
        if account.external_accounts and account.external_accounts.data:
            bank = account.external_accounts.data[0]
            result["bank_last4"] = bank.get("last4", "")
            result["bank_name"] = bank.get("bank_name", "")
        
        return result
    except Exception as e:
        return {"connected": False, "onboarding_complete": False, "error": str(e)}


@router.post("/api/stripe/artist/{artist_id}/dashboard-link")
def create_artist_dashboard_link(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Create a link to the Stripe Express dashboard for the artist"""
    # Audit fix (May 2026): require artist-access (owner OR entity_users)
    # so co-managers can open the Stripe Express dashboard.
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    stripe, keys = init_stripe(db)

    settings = db.execute(
        text("SELECT stripe_connect_account_id FROM entity_payment_settings WHERE entity_type = 'artist' AND entity_id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    
    if not settings or not settings.get("stripe_connect_account_id"):
        raise HTTPException(400, "No Stripe Connect account found")
    
    login_link = stripe.Account.create_login_link(settings["stripe_connect_account_id"])
    return {"url": login_link.url}


# =====================================================
# CHARGE: Venue charged when an artist books the gig
# =====================================================

@router.post("/api/stripe/charge-booking")
def charge_booking(data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """
    Charge venue when an artist books a gig.
    Fee is split 50/50 between venue and artist.
    Venue pays: gig_pay + half platform fee
    Artist receives: gig_pay - half platform fee
    GigsFill absorbs Stripe processing fees.
    """
    stripe, keys = init_stripe(db)
    
    gig_id = data.get("gig_id")
    slot_id = data.get("slot_id")  # Optional - for multi-slot
    venue_id = data.get("venue_id")
    artist_id = data.get("artist_id")
    amount_cents = data.get("amount_cents")  # Gig pay in cents
    
    if not all([gig_id, venue_id, artist_id, amount_cents]):
        raise HTTPException(400, "Missing required fields")

    # ── Safety guard: verify gig is actually booked by this artist ───────────
    # Prevents phantom charges on open/unbooked gigs and duplicate charges.
    from sqlalchemy import text as _cbt
    _gig_check = db.execute(
        _cbt("SELECT id, venue_id FROM gigs WHERE id = :gid AND venue_id = :vid"),
        {"gid": gig_id, "vid": venue_id}
    ).mappings().first()
    if not _gig_check:
        raise HTTPException(404, "Gig not found for this venue")

    _artist_booked = db.execute(
        _cbt("""
            SELECT 1 FROM gig_slots
            WHERE gig_id = :gid AND artist_id = :aid AND status = 'booked'
            UNION
            SELECT 1 FROM gigs
            WHERE id = :gid AND artist_id = :aid AND status = 'booked'
        """),
        {"gid": gig_id, "aid": artist_id}
    ).first()
    if not _artist_booked:
        raise HTTPException(400, f"Artist {artist_id} is not booked on gig {gig_id} — charge blocked")

    _existing = db.execute(
        _cbt("""
            SELECT id FROM transactions
            WHERE gig_id = :gid
              AND status NOT IN ('payment_cancelled')
              AND (artist_id = :aid
                   OR EXISTS (SELECT 1 FROM transactions c
                              WHERE c.parent_transaction_id = transactions.id
                                AND c.artist_id = :aid))
            LIMIT 1
        """),
        {"gid": gig_id, "aid": artist_id}
    ).first()
    if _existing:
        raise HTTPException(409, f"Transaction already exists for gig {gig_id} artist {artist_id} — duplicate charge blocked")
    # ─────────────────────────────────────────────────────────────────────────

    # Calculate fees — split based on admin setting
    platform_fee_pct = keys['platform_fee_percent'] / 100
    platform_min_fee_cents = int(keys['platform_min_fee'] * 100)
    total_platform_fee_cents = max(int(amount_cents * platform_fee_pct), platform_min_fee_cents)
    
    fee_split = keys.get('platform_fee_split', 'split')
    if fee_split == 'venue_only':
        venue_fee_cents = total_platform_fee_cents
        artist_fee_cents = 0
    elif fee_split == 'artist_only':
        venue_fee_cents = 0
        artist_fee_cents = total_platform_fee_cents
    else:  # 'split' — 50/50
        venue_fee_cents = total_platform_fee_cents // 2
        artist_fee_cents = total_platform_fee_cents - venue_fee_cents
    
    venue_charge_cents = amount_cents + venue_fee_cents
    artist_payout_cents = amount_cents - artist_fee_cents
    
    # Get venue's saved payment method
    venue_settings = db.execute(
        text("SELECT stripe_customer_id, stripe_payment_method_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    
    if not venue_settings or not venue_settings.get("stripe_payment_method_id"):
        raise HTTPException(400, "Venue has no saved payment method. Please add a card first.")
    
    # Verify artist has Connect account
    artist_settings = db.execute(
        text("SELECT stripe_connect_account_id, stripe_connect_onboarding_complete FROM entity_payment_settings WHERE entity_type = 'artist' AND entity_id = :aid"),
        {"aid": artist_id}
    ).mappings().first()
    
    # Check if payments are enabled (kill switch)
    payments_live = keys.get('payments_enabled', False)
    payment_intent_id = None
    
    if payments_live:
        # Create PaymentIntent - charge venue
        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=venue_charge_cents,
                currency="usd",
                customer=venue_settings["stripe_customer_id"],
                payment_method=venue_settings["stripe_payment_method_id"],
                off_session=True,
                confirm=True,
                metadata={
                    "gig_id": str(gig_id),
                    "slot_id": str(slot_id) if slot_id else "",
                    "venue_id": str(venue_id),
                    "artist_id": str(artist_id),
                    "platform": "gigsfill"
                },
                description=f"GigsFill Gig #{gig_id} payment"
            )
            payment_intent_id = payment_intent.id
        except stripe.error.CardError as e:
            raise HTTPException(402, f"Card declined: {e.user_message}")
        except Exception as e:
            logger.error(f"Payment failed: {e}"); raise HTTPException(500, "Payment failed. Please try again.")
    else:
        logging.info(f"[PaymentSkipped] Payments OFF - Gig #{gig_id}, would charge venue {venue_charge_cents} cents")
    
    # Get gig date for scheduled payout
    gig = db.execute(
        text("SELECT date FROM gigs WHERE id = :gid"),
        {"gid": gig_id}
    ).mappings().first()
    
    # Schedule payout for day after gig at configured time
    if gig and gig["date"]:
        from datetime import date as date_type
        gig_date = gig["date"] if isinstance(gig["date"], date_type) else datetime.strptime(str(gig["date"]), "%Y-%m-%d").date()
        payout_date = datetime.combine(gig_date + timedelta(days=1), datetime.min.time().replace(hour=17))
    else:
        payout_date = utcnow_naive() + timedelta(days=2)
    
    # Get user IDs for transaction record
    venue_user = db.execute(text("SELECT user_id FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
    artist_user = db.execute(text("SELECT user_id FROM artists WHERE id = :aid"), {"aid": artist_id}).mappings().first()
    
    # Record transaction
    tx_status = 'charged' if payments_live else 'test'
    db.execute(
        text("""
            INSERT INTO transactions 
                (gig_id, from_user_id, to_user_id, amount_cents, venue_charge_cents, artist_payout_cents, 
                 commission_cents, credit_card_fee_cents, payment_method_type, status, 
                 stripe_payment_intent_id, scheduled_process_at, created_at, notes)
            VALUES 
                (:gig_id, :from_uid, :to_uid, :amount, :venue_charge, :artist_payout,
                 :commission, :cc_fee, 'stripe', :status,
                 :pi_id, :scheduled, :now, :notes)
        """),
        {
            "gig_id": gig_id,
            "from_uid": venue_user["user_id"] if venue_user else 0,
            "to_uid": artist_user["user_id"] if artist_user else 0,
            "amount": amount_cents,
            "venue_charge": venue_charge_cents,
            "artist_payout": artist_payout_cents,
            "commission": total_platform_fee_cents,
            "cc_fee": 0,  # GigsFill absorbs Stripe fees
            "status": tx_status,
            "pi_id": payment_intent_id,
            "scheduled": payout_date,
            "now": utcnow_naive(),
            "notes": f"Slot {slot_id}" if slot_id else None
        }
    )
    db.commit()
    
    return {
        "ok": True,
        "payments_live": payments_live,
        "payment_intent_id": payment_intent_id,
        "venue_charged": venue_charge_cents,
        "artist_payout": artist_payout_cents,
        "platform_fee": total_platform_fee_cents,
        "scheduled_payout": payout_date.isoformat()
    }


# =====================================================
# PAYOUT: Auto-release to artist (called by cron)
# =====================================================

@router.post("/api/stripe/process-payouts")
def process_payouts(user=Depends(get_current_user), db=Depends(get_db)):
    """
    Manually trigger payout processing — admin only.
    Delegates to payout_scheduler which handles charge + transfer + retries.
    """
    # Only admins can manually trigger payouts
    # Audit fix (May 2026): centralized via to_admin_bool helper.
    from backend.utils import to_admin_bool
    user_row = db.execute(
        text("SELECT is_admin FROM users WHERE id = :uid"),
        {"uid": user.id}
    ).mappings().first()
    if not user_row or not to_admin_bool(user_row.get("is_admin")):
        raise HTTPException(403, "Admin access required")
    try:
        from backend.payout_scheduler import process_payouts_now
        process_payouts_now()
        return {"ok": True, "message": "Payout processing triggered"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =====================================================
# CANCEL / REINSTATE GIG PAYMENT
# =====================================================

@router.post("/api/stripe/cancel-gig-payment")
def cancel_gig_payment(data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """
    Venue cancels an artist's gig payment before payout processes.
    - Charges venue ONLY the platform fee (GigsFill's share)
    - Marks transaction as payment_cancelled
    - Sends notifications + emails to both parties
    """
    stripe_mod, keys = init_stripe(db)
    gig_id = data.get("gig_id")
    reason = (data.get("reason") or "").strip()
    
    if not gig_id:
        raise HTTPException(400, "Missing gig_id")
    if not reason:
        raise HTTPException(400, "A reason is required")
    
    # Get transaction (scheduled or test = cancellable)
    txn = db.execute(
        text("""
            SELECT t.*, g.venue_id, g.date as gig_date, g.title as gig_title,
                   v.venue_name, a.name as artist_name, a.id as aid,
                   v.user_id as venue_user_id, a.user_id as artist_user_id
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.id = COALESCE(t.artist_id, g.artist_id)
            WHERE t.gig_id = :gid AND t.status IN ('scheduled', 'test')
            LIMIT 1
        """),
        {"gid": gig_id}
    ).mappings().first()
    
    # If no transaction exists (e.g. gig booked before transaction flow), record cancellation from booked slot
    if not txn:
        gig_row = db.execute(
            text("""
                SELECT g.venue_id, g.date as gig_date, g.title as gig_title,
                       v.venue_name, v.user_id as venue_user_id
                FROM gigs g
                JOIN venues v ON g.venue_id = v.id
                WHERE g.id = :gid
            """),
            {"gid": gig_id}
        ).mappings().first()
        if not gig_row:
            raise HTTPException(404, "Gig not found")
        venue_id = gig_row["venue_id"]
        venue_check = db.execute(
            text("""SELECT 1 FROM venues v WHERE v.id = :vid AND (
                v.user_id = :uid OR EXISTS (
                    SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid
                ))"""),
            {"vid": venue_id, "uid": user.id}
        ).first()
        if not venue_check:
            raise HTTPException(403, "Not authorized")
        # Get first booked slot for this gig (artist + pay)
        slot_row = db.execute(
            text("""
                SELECT gs.artist_id, gs.pay
                FROM gig_slots gs
                WHERE gs.gig_id = :gid AND gs.status = 'booked'
                ORDER BY gs.slot_number ASC LIMIT 1
            """),
            {"gid": gig_id}
        ).mappings().first()
        if not slot_row:
            # Single-slot gig: artist_id and pay may be on gigs table
            slot_row = db.execute(
                text("SELECT artist_id, pay FROM gigs WHERE id = :gid AND status = 'booked'"),
                {"gid": gig_id}
            ).mappings().first()
        if not slot_row or not slot_row.get("artist_id"):
            raise HTTPException(404, "No booked artist or slot found for this gig")
        artist_id = slot_row["artist_id"]
        pay_dollars = float(slot_row.get("pay") or 0)
        # Apply venue's preferred-artist pay override (My Artists tab)
        override_row = db.execute(
            text("SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"),
            {"vid": venue_id, "aid": artist_id}
        ).mappings().first()
        if override_row and override_row.get("pay_dollars_override") is not None:
            override_pay = float(override_row["pay_dollars_override"]) + float(override_row.get("pay_cents_override") or 0) / 100
            if override_pay > pay_dollars:
                pay_dollars = override_pay
        amount_cents = int(round(pay_dollars * 100))
        artist_user = db.execute(
            text("SELECT user_id FROM artists WHERE id = :aid"), {"aid": artist_id}
        ).mappings().first()
        if not artist_user:
            raise HTTPException(404, "Artist not found")
        # Calculate platform fee so it shows correctly on billing page
        _fee_settings = {}
        for _r in db.execute(text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_fee_percent','platform_fee_split','platform_min_fee')")).fetchall():
            _fee_settings[_r[0]] = _r[1]
        _fee_pct = float(_fee_settings.get('platform_fee_percent', '10')) / 100
        _min_fee = int(float(_fee_settings.get('platform_min_fee', '0')) * 100)
        _commission = max(int(amount_cents * _fee_pct), _min_fee)
        _fee_split = _fee_settings.get('platform_fee_split', 'split')
        _venue_charge = amount_cents + (_commission if _fee_split == 'venue_only' else (_commission // 2 if _fee_split == 'split' else 0))

        # Insert a payment_cancelled transaction for audit (no charge was ever scheduled)
        db.execute(
            text("""
                INSERT INTO transactions
                    (gig_id, from_user_id, to_user_id, artist_id, amount_cents, venue_charge_cents,
                     artist_payout_cents, commission_cents, credit_card_fee_cents,
                     payment_method_type, status, cancel_reason, cancelled_at, created_at, notes)
                VALUES
                    (:gig_id, :from_uid, :to_uid, :artist_id, :amount, :venue_charge, 0, :commission, 0,
                     'stripe', 'payment_cancelled', :reason, :now, :now, :notes)
            """),
            {
                "gig_id": gig_id,
                "from_uid": gig_row["venue_user_id"],
                "to_uid": artist_user["user_id"],
                "artist_id": artist_id,
                "amount": amount_cents,
                "venue_charge": _venue_charge,
                "commission": _commission,
                "reason": reason,
                "now": utcnow_naive().isoformat(),
                "notes": "No scheduled charge; venue recorded cancellation (legacy or missing txn)."
            }
        )
        artist_name = db.execute(
            text("SELECT name FROM artists WHERE id = :aid"), {"aid": artist_id}
        ).scalar() or "Artist"
        gig_label = gig_row.get("gig_title") or str(gig_row.get("gig_date") or "a gig")
        venue_name = gig_row.get("venue_name") or "Venue"
        # Notify venue and artist
        db.execute(
            text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                VALUES (:uid, 'payment_cancelled', 'Payment Cancelled', :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
            {"uid": gig_row["venue_user_id"], "msg": f"You recorded cancellation of payment to {artist_name} for {gig_label}. No charge was scheduled.", "gid": gig_id, "vid": venue_id, "aid": artist_id}
        )
        db.execute(
            text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
                VALUES (:uid, 'payment_cancelled', 'Payment Cancelled', :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
            {"uid": artist_user["user_id"], "msg": f"{venue_name} has cancelled your payment for {gig_label}. Reason: {reason}. Please contact the venue directly to resolve.", "gid": gig_id, "vid": venue_id, "aid": artist_id}
        )
        db.commit()
        return {
            "ok": True,
            "message": "Payment intent recorded as cancelled. No charge was scheduled for this gig.",
            "platform_fee_charged": 0,
        }
    
    txn = dict(txn)
    
    # Verify venue access
    venue_id = txn["venue_id"]
    venue_check = db.execute(
        text("""SELECT 1 FROM venues v WHERE v.id = :vid AND (
            v.user_id = :uid OR EXISTS (
                SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid
            ))"""),
        {"vid": venue_id, "uid": user.id}
    ).first()
    if not venue_check:
        raise HTTPException(403, "Not authorized")
    
    # Check timing: must be before payout processing time
    from zoneinfo import ZoneInfo
    try:
        tz_row = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key = 'platform_timezone'")).mappings().first()
        tz = ZoneInfo(tz_row["setting_value"]) if tz_row and tz_row["setting_value"] else ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = ZoneInfo("America/Los_Angeles")
    
    now_local = datetime.now(tz)
    
    if txn.get("scheduled_process_at"):
        sched_str = str(txn["scheduled_process_at"])
        try:
            sched_naive = datetime.fromisoformat(sched_str.replace("Z", ""))
            sched_local = sched_naive.replace(tzinfo=tz)
            if now_local >= sched_local:
                raise HTTPException(400, "Payment cancellation window has closed. The payout has already been processed or is being processed.")
        except (ValueError, TypeError):
            pass
    
    # Calculate venue's share of platform fee
    fee_split = keys.get('platform_fee_split', 'split')
    commission = txn["commission_cents"] or 0
    if fee_split == 'venue_only':
        venue_fee_cents = commission
    elif fee_split == 'artist_only':
        venue_fee_cents = 0
    else:
        venue_fee_cents = commission // 2
    
    # Charge venue's card for platform fee only
    payments_live = keys.get('payments_enabled', False)
    platform_fee_pi_id = None
    
    if payments_live and venue_fee_cents > 0:
        venue_settings = db.execute(
            text("SELECT stripe_customer_id, stripe_payment_method_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
            {"vid": venue_id}
        ).mappings().first()
        
        if venue_settings and venue_settings.get("stripe_payment_method_id"):
            try:
                pi = stripe_mod.PaymentIntent.create(
                    amount=venue_fee_cents,
                    currency="usd",
                    customer=venue_settings["stripe_customer_id"],
                    payment_method=venue_settings["stripe_payment_method_id"],
                    off_session=True,
                    confirm=True,
                    metadata={
                        "gig_id": str(gig_id),
                        "type": "payment_cancellation_platform_fee",
                        "platform": "gigsfill"
                    },
                    description=f"GigsFill platform fee - Gig #{gig_id} (payment cancelled by venue)"
                )
                platform_fee_pi_id = pi.id
            except Exception as e:
                logging.error(f"[CancelPayment] Platform fee charge failed for gig {gig_id}: {e}")
    
    # Update transaction
    db.execute(
        text("""UPDATE transactions SET 
            status = 'payment_cancelled',
            cancel_reason = :reason,
            cancelled_at = :now,
            platform_fee_charged_cents = :fee,
            stripe_payment_intent_id = COALESCE(:pi_id, stripe_payment_intent_id),
            notes = :notes
        WHERE id = :tid"""),
        {
            "reason": reason,
            "now": utcnow_naive().isoformat(),
            "fee": venue_fee_cents,
            "pi_id": platform_fee_pi_id,
            "notes": f"Payment cancelled by venue. Reason: {reason}",
            "tid": txn["id"]
        }
    )
    
    # Create notifications
    gig_label = txn.get("gig_title") or str(txn.get("gig_date") or "a gig")
    venue_name = txn.get("venue_name") or "Venue"
    artist_name = txn.get("artist_name") or "Artist"
    
    # Venue notification
    db.execute(
        text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
            VALUES (:uid, 'payment_cancelled', 'Payment Cancelled', :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
        {"uid": txn["venue_user_id"],
         "msg": f"You cancelled payment to {artist_name} for {gig_label}. Platform fee of ${venue_fee_cents/100:.2f} has been charged.",
         "gid": gig_id, "vid": venue_id, "aid": txn.get("aid")}
    )
    
    # Artist notification
    db.execute(
        text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
            VALUES (:uid, 'payment_cancelled', 'Payment Cancelled', :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
        {"uid": txn["artist_user_id"],
         "msg": f"{venue_name} has cancelled your payment for {gig_label}. Reason: {reason}. Please contact the venue directly to resolve.",
         "gid": gig_id, "vid": venue_id, "aid": txn.get("aid")}
    )
    
    db.commit()
    
    # Send emails
    try:
        from backend.email_service import EmailService
        email_svc = EmailService(db)
        
        # Helper to get all user emails for an entity
        def _get_emails(entity_type, entity_id):
            if entity_type == 'venue':
                rows = db.execute(text("""
                    SELECT u.email, u.id as user_id FROM users u JOIN venues v ON v.user_id = u.id WHERE v.id = :eid
                    UNION SELECT u.email, u.id as user_id FROM users u JOIN entity_users eu ON u.id = eu.user_id
                    WHERE eu.entity_type = 'venue' AND eu.entity_id = :eid
                """), {"eid": entity_id}).mappings().all()
            else:
                rows = db.execute(text("""
                    SELECT u.email, u.id as user_id FROM users u JOIN artists a ON a.user_id = u.id WHERE a.id = :eid
                    UNION SELECT u.email, u.id as user_id FROM users u JOIN entity_users eu ON u.id = eu.user_id
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = :eid
                """), {"eid": entity_id}).mappings().all()
            return [dict(r) for r in rows]
        
        gig_date_str = str(txn.get("gig_date") or "")
        artist_pay = f"${txn['amount_cents']/100:.2f}" if txn.get("amount_cents") else "$0.00"
        fee_str = f"${venue_fee_cents/100:.2f}"
        
        # Email to venue
        venue_html = f"""<p>This confirms that you have cancelled the artist payment for the following gig:</p>
        <p><strong>Gig:</strong> {gig_label}<br>
        <strong>Date:</strong> {gig_date_str}<br>
        <strong>Artist:</strong> {artist_name}<br>
        <strong>Amount Cancelled:</strong> {artist_pay}</p>
        <p><strong>Your reason:</strong> {reason}</p>
        <p>The GigsFill platform fee of <strong>{fee_str}</strong> has been charged to your card on file.</p>
        <p style="font-size:12px;color:#666;margin-top:20px;border-top:1px solid #eee;padding-top:12px;">
        <em>Disclaimer: Payment disputes are between the Venue and Artist. GigsFill is not involved in resolving disputes 
        and is not responsible for the outcome. The artist may contact you directly regarding this matter.</em></p>
        <p>— The GigsFill Team</p>"""
        
        for vu in _get_emails('venue', venue_id):
            try:
                email_svc._send_raw_email(vu["email"], f"Payment Cancelled — {gig_label}", venue_html)
            except Exception:
                pass
        
        # Email to artist
        artist_html = f"""<p>We're writing to let you know that <strong>{venue_name}</strong> has cancelled the payment for your gig:</p>
        <p><strong>Gig:</strong> {gig_label}<br>
        <strong>Date:</strong> {gig_date_str}<br>
        <strong>Amount:</strong> {artist_pay}</p>
        <p><strong>Venue's reason:</strong> <em>{reason}</em></p>
        <p>Please contact the venue directly to discuss or resolve this matter.</p>
        <p style="font-size:12px;color:#666;margin-top:20px;border-top:1px solid #eee;padding-top:12px;">
        <em>Disclaimer: Payment disputes are between the Venue and Artist. GigsFill is not involved in resolving disputes 
        and is not responsible for the outcome.</em></p>
        <p>— The GigsFill Team</p>"""
        
        for au in _get_emails('artist', txn.get("aid")):
            try:
                email_svc._send_raw_email(au["email"], f"Payment Cancelled by {venue_name} — {gig_label}", artist_html)
            except Exception:
                pass
    except Exception as e:
        logging.error(f"[CancelPayment] Email error: {e}")
    
    return {
        "ok": True,
        "platform_fee_charged": venue_fee_cents,
        "platform_fee_pi_id": platform_fee_pi_id
    }


@router.post("/api/stripe/reinstate-gig-payment")
def reinstate_gig_payment(data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """
    Venue reinstates a previously cancelled payment.
    - Charges venue for gig pay (artist amount) ONLY — platform fee already collected
    - Transfers to artist
    - Updates transaction to 'paid'
    """
    stripe_mod, keys = init_stripe(db)
    txn_id = data.get("transaction_id")
    
    if not txn_id:
        raise HTTPException(400, "Missing transaction_id")
    
    txn = db.execute(
        text("""
            SELECT t.*, g.venue_id, g.date as gig_date, g.title as gig_title,
                   v.venue_name, a.name as artist_name, a.id as aid,
                   v.user_id as venue_user_id, a.user_id as artist_user_id
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            JOIN venues v ON g.venue_id = v.id
            LEFT JOIN artists a ON a.id = COALESCE(t.artist_id, g.artist_id)
            WHERE t.id = :tid AND t.status = 'payment_cancelled'
        """),
        {"tid": txn_id}
    ).mappings().first()
    
    if not txn:
        raise HTTPException(404, "No cancelled payment found")
    txn = dict(txn)
    
    venue_id = txn["venue_id"]
    venue_check = db.execute(
        text("""SELECT 1 FROM venues v WHERE v.id = :vid AND (
            v.user_id = :uid OR EXISTS (
                SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid
            ))"""),
        {"vid": venue_id, "uid": user.id}
    ).first()
    if not venue_check:
        raise HTTPException(403, "Not authorized")
    
    # Effective pay: use venue's preferred-artist pay override (My Artists) if higher than stored amount
    amount_cents = txn.get("amount_cents") or 0
    override_row = db.execute(
        text("SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": venue_id, "aid": txn.get("aid") or txn.get("artist_id")}
    ).mappings().first()
    if override_row and override_row.get("pay_dollars_override") is not None:
        override_pay = float(override_row["pay_dollars_override"]) + float(override_row.get("pay_cents_override") or 0) / 100
        override_cents = int(round(override_pay * 100))
        if override_cents > amount_cents:
            amount_cents = override_cents
    # Recalculate fees from current Admin platform settings (Platform Fee & Payout Schedule)
    settings = {}
    for r in db.execute(text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_fee_percent', 'platform_fee_split', 'platform_min_fee')")).fetchall():
        settings[r[0]] = r[1]
    fee_pct = float(settings.get('platform_fee_percent', '10')) / 100
    min_fee_cents = int(float(settings.get('platform_min_fee', '0')) * 100)
    fee_split = settings.get('platform_fee_split', 'split')
    total_fee_cents = max(int(amount_cents * fee_pct), min_fee_cents)
    if fee_split == 'venue_only':
        venue_fee_cents = total_fee_cents
        artist_fee_cents = 0
    elif fee_split == 'artist_only':
        venue_fee_cents = 0
        artist_fee_cents = total_fee_cents
    else:
        venue_fee_cents = total_fee_cents // 2
        artist_fee_cents = total_fee_cents - venue_fee_cents
    venue_charge_cents = amount_cents + venue_fee_cents
    artist_payout_cents = amount_cents - artist_fee_cents

    # Charge venue: full venue_charge minus any platform fee already collected at cancel
    already_charged = txn.get("platform_fee_charged_cents") or 0
    reinstate_charge_cents = venue_charge_cents - already_charged
    
    payments_live = keys.get('payments_enabled', False)
    payment_intent_id = None
    
    if payments_live and reinstate_charge_cents > 0:
        venue_settings = db.execute(
            text("SELECT stripe_customer_id, stripe_payment_method_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
            {"vid": venue_id}
        ).mappings().first()
        
        if not venue_settings or not venue_settings.get("stripe_payment_method_id"):
            raise HTTPException(400, "No payment card on file")
        
        try:
            pi = stripe_mod.PaymentIntent.create(
                amount=reinstate_charge_cents,
                currency="usd",
                customer=venue_settings["stripe_customer_id"],
                payment_method=venue_settings["stripe_payment_method_id"],
                off_session=True,
                confirm=True,
                metadata={
                    "gig_id": str(txn["gig_id"]),
                    "type": "payment_reinstatement",
                    "platform": "gigsfill"
                },
                description=f"GigsFill Gig #{txn['gig_id']} - reinstated payment"
            )
            payment_intent_id = pi.id
        except Exception as e:
            logger.error(f"Card charge failed: {e}"); raise HTTPException(402, "Card charge failed. Please check your payment method.")
    
    # Transfer to artist
    transfer_id = None
    if payments_live:
        artist_settings = db.execute(
            text("""SELECT eps.stripe_connect_account_id, eps.stripe_connect_onboarding_complete
                FROM entity_payment_settings eps WHERE eps.entity_type = 'artist' AND eps.entity_id = :aid"""),
            {"aid": txn.get("aid")}
        ).mappings().first()
        
        if artist_settings and artist_settings.get("stripe_connect_account_id") and artist_settings.get("stripe_connect_onboarding_complete"):
            try:
                transfer = stripe_mod.Transfer.create(
                    amount=artist_payout_cents,
                    currency="usd",
                    destination=artist_settings["stripe_connect_account_id"],
                    metadata={
                        "gig_id": str(txn["gig_id"]),
                        "transaction_id": str(txn_id),
                        "type": "reinstated_payment",
                        "platform": "gigsfill"
                    },
                    description=f"GigsFill Gig #{txn['gig_id']} - reinstated payout"
                )
                transfer_id = transfer.id
            except Exception as e:
                logging.error(f"[ReinstatePayment] Transfer failed: {e}")
    
    # Update transaction with recalculated amounts
    new_status = 'paid' if transfer_id or not payments_live else 'charged'
    db.execute(
        text("""UPDATE transactions SET 
            status = :status,
            venue_charge_cents = :venue_charge,
            artist_payout_cents = :artist_payout,
            commission_cents = :commission,
            stripe_payment_intent_id = COALESCE(:pi_id, stripe_payment_intent_id),
            stripe_transfer_id = :xfer_id,
            processed_at = :now,
            notes = :notes
        WHERE id = :tid"""),
        {
            "status": new_status,
            "venue_charge": venue_charge_cents,
            "artist_payout": artist_payout_cents,
            "commission": total_fee_cents,
            "pi_id": payment_intent_id,
            "xfer_id": transfer_id or ('test_reinstated' if not payments_live else None),
            "now": utcnow_naive().isoformat(),
            "notes": f"Payment reinstated by venue after earlier cancellation.",
            "tid": txn_id
        }
    )
    
    # Notifications
    gig_label = txn.get("gig_title") or str(txn.get("gig_date") or "a gig")
    venue_name = txn.get("venue_name") or "Venue"
    artist_name = txn.get("artist_name") or "Artist"
    artist_payout = f"${artist_payout_cents/100:.2f}"
    
    db.execute(
        text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
            VALUES (:uid, 'payment_reinstated', 'Payment Reinstated', :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
        {"uid": txn["venue_user_id"],
         "msg": f"Payment of {artist_payout} to {artist_name} for {gig_label} has been processed.",
         "gid": txn["gig_id"], "vid": venue_id, "aid": txn.get("aid")}
    )
    
    db.execute(
        text("""INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at)
            VALUES (:uid, 'payment_reinstated', 'Payment Received!', :msg, :gid, :vid, :aid, 0, CURRENT_TIMESTAMP)"""),
        {"uid": txn["artist_user_id"],
         "msg": f"{venue_name} has reinstated your payment of {artist_payout} for {gig_label}.",
         "gid": txn["gig_id"], "vid": venue_id, "aid": txn.get("aid")}
    )
    
    db.commit()
    
    # Send emails
    try:
        from backend.email_service import EmailService
        email_svc = EmailService(db)
        
        def _get_emails(entity_type, entity_id):
            if entity_type == 'venue':
                rows = db.execute(text("""
                    SELECT u.email FROM users u JOIN venues v ON v.user_id = u.id WHERE v.id = :eid
                    UNION SELECT u.email FROM users u JOIN entity_users eu ON u.id = eu.user_id
                    WHERE eu.entity_type = 'venue' AND eu.entity_id = :eid
                """), {"eid": entity_id}).mappings().all()
            else:
                rows = db.execute(text("""
                    SELECT u.email FROM users u JOIN artists a ON a.user_id = u.id WHERE a.id = :eid
                    UNION SELECT u.email FROM users u JOIN entity_users eu ON u.id = eu.user_id
                    WHERE eu.entity_type = 'artist' AND eu.entity_id = :eid
                """), {"eid": entity_id}).mappings().all()
            return [dict(r) for r in rows]
        
        for au in _get_emails('artist', txn.get("aid")):
            try:
                email_svc._send_raw_email(au["email"], 
                    f"Payment Received — {gig_label}",
                    f"""<p>Great news! <strong>{venue_name}</strong> has reinstated your payment for:</p>
                    <p><strong>Gig:</strong> {gig_label}<br>
                    <strong>Date:</strong> {txn.get('gig_date')}<br>
                    <strong>Payout:</strong> <span style="color:#10b981;font-size:18px;font-weight:700;">{artist_payout}</span></p>
                    <p>The funds will appear in your connected Stripe account shortly.</p>
                    <p>— The GigsFill Team</p>""")
            except Exception:
                pass
    except Exception as e:
        logging.error(f"[ReinstatePayment] Email error: {e}")
    
    return {
        "ok": True,
        "status": new_status,
        "artist_payout": artist_payout_cents,
        "charged": reinstate_charge_cents
    }


@router.get("/api/stripe/gig/{gig_id}/transaction-status")
def get_gig_transaction_status(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get the transaction status for a gig (for cancel payment button visibility)"""
    txn = db.execute(
        text("""SELECT t.id, t.status, t.scheduled_process_at, t.amount_cents, t.cancel_reason
            FROM transactions t WHERE t.gig_id = :gid ORDER BY t.id DESC LIMIT 1"""),
        {"gid": gig_id}
    ).mappings().first()
    
    if not txn:
        return {"has_transaction": False}
    
    txn = dict(txn)
    # Ensure scheduled_process_at is ISO string for frontend date parsing
    sched = txn.get("scheduled_process_at")
    if sched is not None:
        if hasattr(sched, "isoformat"):
            txn["scheduled_process_at"] = sched.isoformat()
        else:
            txn["scheduled_process_at"] = str(sched).replace(" ", "T", 1)
    return {"has_transaction": True, **txn}


# =====================================================
# PAYMENT INFO (for venue/artist notice banners)
# =====================================================

def _get_platform_setting(db, key: str, default=None):
    """Helper to get a platform setting value"""
    result = db.execute(
        text("SELECT setting_value FROM platform_settings WHERE setting_key = :key"),
        {"key": key}
    ).mappings().first()
    return result["setting_value"] if result else default

@router.get("/api/payment-info")
def get_payment_info(venue_id: int = 0, user=Depends(get_current_user), db=Depends(get_db)):
    """Get payment processing info and free trial status for a venue"""
    delay_days = _get_platform_setting(db, "payment_processing_delay_days", "1")
    processing_hour = _get_platform_setting(db, "payment_processing_hour", "17")
    
    hour_int = int(processing_hour)
    if hour_int == 0:
        time_str = "12:00 AM"
    elif hour_int < 12:
        time_str = f"{hour_int}:00 AM"
    elif hour_int == 12:
        time_str = "12:00 PM"
    else:
        time_str = f"{hour_int - 12}:00 PM"
    
    result = {
        "delay_days": int(delay_days),
        "processing_hour": hour_int,
        "processing_time_display": time_str,
        "free_trial": False
    }
    
    if venue_id:
        override = db.execute(
            text("SELECT payments_suspended FROM venue_payment_overrides WHERE venue_id = :vid AND payments_suspended = 1"),
            {"vid": venue_id}
        ).first()
        if override:
            result["free_trial"] = True
    
    return result


# =====================================================
# TRANSACTION HISTORY
# =====================================================

@router.get("/api/stripe/venue/{venue_id}/transactions")
def get_venue_transactions(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get venue's payment history"""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    venue = db.execute(text("SELECT user_id FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
    if not venue:
        raise HTTPException(404, "Venue not found")
    
    txns = db.execute(
        text("""
            SELECT t.*,
                   g.date as gig_date, g.start_time as gig_time, g.title as gig_title,
                   -- Multi-slot: comma-separated list of all booked artists.
                   -- Single-slot or no-slots: fall back to the gig's resolved artist name.
                   COALESCE(
                     (SELECT GROUP_CONCAT(a_slot.name, ', ')
                      FROM gig_slots gs
                      JOIN artists a_slot ON a_slot.id = gs.artist_id
                      WHERE gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','pending_venue_approval')),
                     a.name, a2.name
                   ) as artist_name,
                   COALESCE(
                     (SELECT gs.artist_id FROM gig_slots gs
                      WHERE gs.gig_id = g.id AND gs.status IN ('booked','pending_contract','pending_venue_approval')
                      LIMIT 1),
                     t.artist_id, g.artist_id
                   ) as resolved_artist_id,
                   -- FIX (May 2026): for venue_charge parent rows, the parent's
                   -- own status stays 'charged' even after children are paid.
                   -- The "effective" parent status reflects whether the artist
                   -- payout has actually completed:
                   --   - If ALL non-cancelled children are 'paid' → parent shows 'paid'
                   --   - Otherwise keep the parent's own status
                   -- Frontend uses effective_status to display "Paid ✓" only when
                   -- the artist actually got paid out, not just when venue was charged.
                   CASE
                     WHEN COALESCE(t.transaction_type, 'single') = 'venue_charge'
                          AND t.status = 'charged'
                          AND NOT EXISTS (
                            SELECT 1 FROM transactions c
                            WHERE c.parent_transaction_id = t.id
                              AND c.transaction_type = 'artist_payout'
                              AND c.status NOT IN ('paid','payment_cancelled','account_deleted')
                          )
                          AND EXISTS (
                            SELECT 1 FROM transactions c
                            WHERE c.parent_transaction_id = t.id
                              AND c.transaction_type = 'artist_payout'
                              AND c.status = 'paid'
                          )
                     THEN 'paid'
                     ELSE t.status
                   END as effective_status
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            LEFT JOIN artists a ON a.id = t.artist_id
            LEFT JOIN artists a2 ON a2.id = g.artist_id
            WHERE g.venue_id = :vid
              AND COALESCE(t.transaction_type, 'single') IN ('venue_charge', 'single', 'payment_cancelled')
              AND (
                -- Only show transactions for gigs that are actually booked/in-progress
                g.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval','started','completed','paid')
                -- OR historical paid/cancelled transactions regardless of gig status
                OR t.status IN ('paid','transferred','payment_cancelled','suspended')
              )
            ORDER BY g.date DESC, t.id DESC
            LIMIT 50
        """),
        {"vid": venue_id}
    ).mappings().all()
    
    return [dict(t) for t in txns]


def _correct_transaction_amount_if_needed(db, txn, venue_id, artist_id, gig_pay):
    """If txn is scheduled/test and effective pay > stored amount, update transaction to effective pay (venue override)."""
    if txn.get("status") not in ("scheduled", "test"):
        return
    from backend.routes.gigs import _get_effective_pay
    effective = _get_effective_pay(db, venue_id, artist_id, gig_pay)
    amount_cents = int(round(effective * 100))
    if amount_cents <= txn.get("amount_cents", 0):
        return
    settings = {}
    for r in db.execute(text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN ('platform_fee_percent', 'platform_fee_split', 'platform_min_fee')")).fetchall():
        settings[r[0]] = r[1]
    fee_pct = float(settings.get("platform_fee_percent", "10")) / 100
    min_fee_cents = int(float(settings.get("platform_min_fee", "0")) * 100)
    fee_split = settings.get("platform_fee_split", "split")
    total_fee_cents = max(int(amount_cents * fee_pct), min_fee_cents)
    if fee_split == "venue_only":
        venue_fee, artist_fee = total_fee_cents, 0
    elif fee_split == "artist_only":
        venue_fee, artist_fee = 0, total_fee_cents
    else:
        venue_fee = total_fee_cents // 2
        artist_fee = total_fee_cents - venue_fee
    venue_charge_cents = amount_cents + venue_fee
    artist_payout_cents = amount_cents - artist_fee
    db.execute(
        text("""UPDATE transactions SET amount_cents = :amt, venue_charge_cents = :vch, artist_payout_cents = :ap, commission_cents = :comm WHERE id = :tid"""),
        {"amt": amount_cents, "vch": venue_charge_cents, "ap": artist_payout_cents, "comm": total_fee_cents, "tid": txn["id"]}
    )
    db.commit()
    txn["amount_cents"] = amount_cents
    txn["venue_charge_cents"] = venue_charge_cents
    txn["artist_payout_cents"] = artist_payout_cents
    txn["commission_cents"] = total_fee_cents


@router.get("/api/stripe/artist/{artist_id}/transactions")
def get_artist_transactions(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get artist's earnings history. Corrects scheduled/test txns to use venue pay override if stored amount was wrong."""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    artist = db.execute(text("SELECT user_id FROM artists WHERE id = :aid"), {"aid": artist_id}).mappings().first()
    if not artist:
        raise HTTPException(404, "Artist not found")
    
    txns = db.execute(
        text("""
            SELECT t.*, g.date as gig_date,
                   -- FIX (May 2026): for multi-slot gigs, use THIS artist's slot start_time.
                   -- Without this, an artist booked on slot 2 (9pm-11pm) sees the parent
                   -- gig's start_time (7pm) which is actually slot 1's time. Earnings
                   -- History should show the time the artist is performing.
                   COALESCE(
                     (SELECT gs.start_time FROM gig_slots gs
                      WHERE gs.gig_id = g.id AND gs.artist_id = t.artist_id
                      ORDER BY gs.slot_number LIMIT 1),
                     g.start_time
                   ) as gig_time,
                   g.title as gig_title,
                   v.venue_name, g.venue_id as resolved_venue_id, g.pay as gig_pay
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            LEFT JOIN venues v ON v.id = g.venue_id
            WHERE t.artist_id = :aid
              AND t.transaction_type IN ('artist_payout', 'single')
              AND t.status IN ('paid','transferred','payment_cancelled','suspended',
                               'scheduled','test','pending_transfer','charged','charge_retry')
            ORDER BY g.date DESC
            LIMIT 50
        """),
        {"aid": artist_id}
    ).mappings().all()
    
    txns = [dict(t) for t in txns]
    for t in txns:
        vid = t.get("resolved_venue_id")
        aid = t.get("artist_id")
        if vid is not None and aid is not None:
            _correct_transaction_amount_if_needed(db, t, vid, aid, t.get("gig_pay"))
    return txns


@router.get("/api/stripe/venue/{venue_id}/upcoming-charges")
def get_venue_upcoming_charges(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get venue's upcoming/pending charges"""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    venue = db.execute(text("SELECT user_id FROM venues WHERE id = :vid"), {"vid": venue_id}).mappings().first()
    if not venue:
        raise HTTPException(404, "Venue not found")
    
    txns = db.execute(
        text("""
            SELECT t.*, g.date as gig_date, g.title as gig_title,
                   COALESCE(a.name, a2.name, 
                     (SELECT a3.name FROM artists a3 WHERE a3.user_id = t.to_user_id LIMIT 1)
                   ) as artist_name,
                   COALESCE(t.artist_id, g.artist_id,
                     (SELECT a3.id FROM artists a3 WHERE a3.user_id = t.to_user_id LIMIT 1)
                   ) as resolved_artist_id
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            LEFT JOIN artists a ON a.id = t.artist_id
            LEFT JOIN artists a2 ON a2.id = g.artist_id
            WHERE g.venue_id = :vid AND t.status IN ('scheduled', 'charged', 'pending', 'test')
              AND g.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval','started','completed')
            ORDER BY t.scheduled_process_at ASC
        """),
        {"vid": venue_id}
    ).mappings().all()
    
    return [dict(t) for t in txns]


@router.get("/api/stripe/artist/{artist_id}/upcoming-payouts")
def get_artist_upcoming_payouts(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get artist's upcoming payouts"""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    artist = db.execute(text("SELECT user_id FROM artists WHERE id = :aid"), {"aid": artist_id}).mappings().first()
    if not artist:
        raise HTTPException(404, "Artist not found")
    
    txns = db.execute(
        text("""
            SELECT t.*, g.date as gig_date, g.title as gig_title,
                   v.venue_name, g.venue_id as resolved_venue_id
            FROM transactions t
            JOIN gigs g ON t.gig_id = g.id
            LEFT JOIN venues v ON v.id = g.venue_id
            WHERE (t.artist_id = :aid OR g.artist_id = :aid)
              AND t.status IN ('scheduled', 'charged', 'pending', 'test')
            ORDER BY t.scheduled_process_at ASC
        """),
        {"aid": artist_id}
    ).mappings().all()
    
    return [dict(t) for t in txns]


# =====================================================
# VENUE PAYMENT STATUS & SUSPENSION
# =====================================================

@router.get("/api/stripe/venue/{venue_id}/payment-status")
def get_venue_payment_status(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Check venue payment status — used by frontend to show suspension modal"""
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)
    venue = db.execute(
        text("""SELECT id, venue_name, payment_status, payment_suspended_at, payment_suspension_reason 
                FROM venues WHERE id = :vid"""),
        {"vid": venue_id}
    ).mappings().first()
    if not venue:
        raise HTTPException(404, "Venue not found")
    
    # Also check if card is on file
    card = db.execute(
        text("SELECT stripe_payment_method_id FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :vid"),
        {"vid": venue_id}
    ).mappings().first()
    has_card = card and card.get("stripe_payment_method_id")
    
    # Count affected booked gigs
    booked_count = db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM (
                SELECT g.id FROM gigs g WHERE g.venue_id = :vid AND g.status = 'booked' AND g.date >= DATE('now', 'localtime')
                UNION ALL
                SELECT gs.id FROM gig_slots gs JOIN gigs g ON gs.gig_id = g.id 
                WHERE g.venue_id = :vid AND gs.status = 'booked' AND g.date >= DATE('now', 'localtime')
            )
        """),
        {"vid": venue_id}
    ).mappings().first()
    
    # Auto-suspend if no card and status is active
    status = venue["payment_status"] or "active"
    if not has_card and status == "active":
        db.execute(
            text("""UPDATE venues SET payment_status = 'suspended', 
                    payment_suspended_at = :now, payment_suspension_reason = 'No payment card on file'
                    WHERE id = :vid"""),
            {"vid": venue_id, "now": utcnow_naive()}
        )
        db.commit()
        status = "suspended"
    
    return {
        "payment_status": status,
        "has_card": bool(has_card),
        "suspended_at": str(venue["payment_suspended_at"]) if venue["payment_suspended_at"] else None,
        "suspension_reason": venue["payment_suspension_reason"],
        "booked_gigs_affected": booked_count["cnt"] if booked_count else 0
    }


def _notify_artists_payment_issue(db, venue_id, venue_name):
    """Send email notifications to all artists with booked gigs at a suspended venue"""
    try:
        from backend.email_service import EmailService
        from backend.utils import get_all_entity_users
        email_service = EmailService(db)
        
        booked_artists = db.execute(
            text("""
                SELECT DISTINCT g.id as gig_id, COALESCE(g.artist_id, gs.artist_id) as artist_id, g.date
                FROM gigs g
                LEFT JOIN gig_slots gs ON gs.gig_id = g.id AND gs.status = 'booked'
                WHERE g.venue_id = :vid AND g.date >= DATE('now', 'localtime')
                AND (g.status = 'booked' OR gs.status = 'booked')
            """),
            {"vid": venue_id}
        ).mappings().all()

        from backend.services.email_dispatch import compute_slot_times

        for row in booked_artists:
            if not row["artist_id"]:
                continue
            artist_users = get_all_entity_users(db, 'artist', row["artist_id"])
            slot_times = compute_slot_times(db, row["gig_id"], artist_id=row["artist_id"])
            for au in artist_users:
                email_service.send_notification_email(
                    user_email=au["email"],
                    user_id=au["user_id"],
                    notification_type='artist_venue_payment_issue',
                    variables={
                        'venue_name': venue_name,
                        'date': format_email_date(row["date"]),
                        'artist_name': au.get("first_name", "Artist"),
                        'slot_times': slot_times,
                    }
                )
    except Exception as e:
        logger.error(f"Error notifying artists: {e}")



# =====================================================
# STRIPE WEBHOOK
# Handles: transfer.created, charge.dispute.created,
#          payment_intent.payment_failed, account.updated
# =====================================================

def _webhook_get_secret(db):
    """Fetch webhook secret from platform_settings"""
    try:
        row = db.execute(
            text("SELECT setting_value FROM platform_settings WHERE setting_key = 'admin_stripe_webhook_secret'")
        ).mappings().first()
        return (row["setting_value"] if row else "") or ""
    except Exception:
        return ""

def _webhook_get_event_obj(event):
    """Safely extract data.object from a Stripe event (dict or object)"""
    data = event.get("data", {}) if isinstance(event, dict) else getattr(event, "data", {})
    obj = data.get("object", {}) if isinstance(data, dict) else getattr(data, "object", {})
    return obj

def _webhook_get(obj, key):
    """Safely get a field from a Stripe object (dict or object)"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)

def _webhook_sqlite_conn():
    """Open a direct database connection for webhook handlers — works with SQLite and PostgreSQL."""
    from backend.db import get_db_connection as _wh_raw_conn
    return _wh_raw_conn()

def _wh_smtp_settings(conn):
    """Get SMTP settings from platform_settings — mirrors payout_scheduler"""
    settings = {}
    for row in conn.execute(
        "SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN "
        "('platform_email', 'platform_email_password', 'platform_smtp_server', 'platform_smtp_port')"
    ).fetchall():
        settings[row["setting_key"]] = row["setting_value"]
    return settings

def _wh_send_email(settings, to_email, subject, html_body):
    """Send a single email — mirrors payout_scheduler._send_html_email"""
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from_email  = settings.get("platform_email", "")
        email_pass  = settings.get("platform_email_password", "")
        smtp_server = settings.get("platform_smtp_server", "")
        smtp_port   = int(settings.get("platform_smtp_port", "587") or 587)
        if not from_email or not email_pass:
            return
        styled = (
            "<!DOCTYPE html><html><body style=\"margin:0;padding:0;background:#f8f9fa;"
            "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;\">"
            "<div style=\"max-width:600px;margin:20px auto;background:#ffffff;border-radius:8px;"
            "overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);\">"
            "<div style=\"background:#1a1f2e;padding:24px;text-align:center;\">"
            "<span style=\"color:#fff;font-size:20px;font-weight:700;letter-spacing:4px;\">GIGSFILL</span></div>"
            "<div style=\"padding:32px 24px;font-size:14px;color:#333;line-height:1.6;\">"
            + html_body +
            "</div></div></body></html>"
        )
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        _sc_from_name = settings.get("platform_email_from_name", "")
        if _sc_from_name:
            from email.utils import formataddr
            msg["From"] = formataddr((_sc_from_name, from_email))
        else:
            msg["From"] = from_email
        msg["To"] = to_email
        msg.attach(MIMEText(styled, "html"))
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        server.starttls()
        server.login(from_email, email_pass)
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        logger.error(f"Webhook email send error to {to_email}: {e}")
def _wh_admin_emails(conn):
    """Get admin alert email — uses admin_alert_email setting, falls back to is_admin users"""
    try:
        # Prefer explicit admin_alert_email setting
        row = conn.execute(
            "SELECT setting_value FROM platform_settings WHERE setting_key = 'admin_alert_email'"
        ).fetchone()
        if row and row["setting_value"] and row["setting_value"].strip():
            return [row["setting_value"].strip()]
    except Exception:
        pass
    # Fallback: all admin users
    try:
        rows = conn.execute(
            "SELECT email FROM users WHERE (is_admin = 'true' OR is_admin = 1) AND email IS NOT NULL"
        ).fetchall()
        return [r["email"] for r in rows if r["email"]]
    except Exception:
        return []

def _wh_send_email(settings, to_email, subject, html_body):
    """Send a single email via SMTP"""
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        host = settings.get("smtp_host", "")
        port = int(settings.get("smtp_port", 587))
        user = settings.get("smtp_username", "")
        pw   = settings.get("smtp_password", "")
        from_addr = settings.get("smtp_from_email", user)
        if not host or not user:
            return
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(from_addr, [to_email], msg.as_string())
    except Exception as e:
        logger.error(f"Webhook email send error: {e}")

def _wh_admin_alert(conn, subject, html_body):
    """Send admin alert email from webhook handler"""
    try:
        settings = _wh_smtp_settings(conn)
        admins = _wh_admin_emails(conn)
        if not admins:
            logger.error(f"Webhook admin alert (no emails): {subject}")
            return
        for email in admins:
            _wh_send_email(settings, email, f"🚨 GigsFill Alert: {subject}", html_body)
    except Exception as e:
        logger.error(f"Webhook admin alert error: {e}")


@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, db=Depends(get_db)):
    """
    Handle Stripe webhook events:
      - transfer.created           → flip 'transferred' → 'paid' (transfer initiated to artist)
      - charge.dispute.created     → flag transaction, suspend venue, alert admin + artist
      - payment_intent.payment_failed → catch async card failures missed by scheduler
      - account.updated            → detect restricted/deactivated artist Connect accounts
    """
    import json
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # --- Verify signature ---
    # construct_event also enforces replay protection: it checks the
    # timestamp on the Stripe-Signature header and rejects payloads older
    # than ~5 minutes, so an attacker can't capture a real webhook and
    # replay it later.
    #
    # SECURITY (May 2026 audit): the previous code had an `else` branch
    # that fell through to `json.loads(payload)` when webhook_secret was
    # empty — meaning if the secret was ever cleared (admin mistake,
    # migration bug, env var unset), the endpoint silently accepted
    # unsigned webhooks. An attacker could then forge a
    # `payment_intent.succeeded` event to falsely mark a charge as paid.
    # Now: refuse to process any webhook if the secret isn't configured.
    # The fallback was dev-only convenience and is no longer worth the
    # production risk.
    webhook_secret = _webhook_get_secret(db)
    if not webhook_secret:
        logger.error("Stripe webhook hit with NO admin_stripe_webhook_secret configured — refusing to process")
        raise HTTPException(503, "Webhook signature verification not configured")
    try:
        import stripe as stripe_lib
        event = stripe_lib.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        logger.warning(f"Stripe webhook signature failed: {e}")
        raise HTTPException(400, "Invalid webhook signature")

    event_type = _webhook_get(event, "type")
    logger.info(f"Stripe webhook: {event_type}")

    # Use direct SQLite for writes (same pattern as payout_scheduler)
    conn = _webhook_sqlite_conn()

    try:

        # ----------------------------------------------------------------
        # transfer.created — Stripe initiated transfer to artist's Connect account
        # ----------------------------------------------------------------
        if event_type == "transfer.created":
            try:
                obj = _webhook_get_event_obj(event)
                transfer_id = _webhook_get(obj, "id")
                if transfer_id:
                    result = conn.execute("""
                        UPDATE transactions
                        SET status = 'paid',
                            notes = COALESCE(notes || ' | ', '') || 'Settled via Stripe webhook'
                        WHERE stripe_transfer_id = ?
                          AND status = 'transferred'
                    """, (transfer_id,))
                    conn.commit()
                    if result.rowcount:
                        logger.info(f"Webhook transfer.created: {result.rowcount} txn(s) marked paid (transfer {transfer_id})")
                    else:
                        logger.info(f"Webhook transfer.created: no matching transferred txn for {transfer_id}")
            except Exception as e:
                logger.error(f"Webhook transfer.created error: {e}")

        # ----------------------------------------------------------------
        # charge.dispute.created — Venue filed a chargeback with their bank
        # ----------------------------------------------------------------
        elif event_type == "charge.dispute.created":
            try:
                obj = _webhook_get_event_obj(event)
                dispute_id   = _webhook_get(obj, "id")
                charge_id    = _webhook_get(obj, "charge")
                pi_from_evt  = _webhook_get(obj, "payment_intent")  # may be None on older API versions
                amount_cents = _webhook_get(obj, "amount") or 0
                reason       = _webhook_get(obj, "reason") or "unknown"

                # Resolve the PaymentIntent id. Audit fix (May 2026): the
                # previous WHERE clause `IN (SELECT payment_intent FROM ...)`
                # referenced a non-existent column and threw on every dispute
                # — chargebacks always fell to the "Transaction Not Found"
                # branch, txn was never flagged disputed, venue was never
                # suspended, admin alert always misleading.
                pi_id = pi_from_evt
                if not pi_id and charge_id:
                    try:
                        ch = stripe.Charge.retrieve(charge_id)
                        pi_id = getattr(ch, "payment_intent", None)
                    except Exception as _ce:
                        logger.warning(f"Webhook dispute: charge {charge_id} retrieve failed: {_ce}")

                # Prefer the parent venue_charge row (multi-slot has artist_id=NULL
                # on parent, child rows on artist_payout). Match by either PI or
                # the dispute's charge id mapped to our transfer id (artist
                # payout chargeback path).
                txn = conn.execute("""
                    SELECT t.id, t.gig_id, t.artist_id, t.venue_charge_cents, t.artist_payout_cents,
                           t.transaction_type,
                           g.venue_id, g.date, v.venue_name,
                           a.name as artist_name, a.id as aid
                    FROM transactions t
                    JOIN gigs g ON t.gig_id = g.id
                    LEFT JOIN venues v ON v.id = g.venue_id
                    LEFT JOIN artists a ON a.id = t.artist_id
                    WHERE (:pi IS NOT NULL AND t.stripe_payment_intent_id = :pi)
                       OR (:ch IS NOT NULL AND t.stripe_transfer_id = :ch)
                    ORDER BY
                        CASE WHEN t.transaction_type = 'venue_charge' THEN 0 ELSE 1 END,
                        t.id DESC
                    LIMIT 1
                """, {"pi": pi_id, "ch": charge_id}).fetchone()

                # For a venue_charge parent, sum the children's payouts so the
                # admin alert reports real claw-back exposure (parent's own
                # artist_payout_cents is 0 by design on multi-slot).
                clawback_cents = 0
                if txn:
                    if txn["transaction_type"] == "venue_charge":
                        _row = conn.execute(
                            "SELECT COALESCE(SUM(artist_payout_cents),0) AS s "
                            "FROM transactions WHERE parent_transaction_id = ? "
                            "AND transaction_type = 'artist_payout' "
                            "AND status NOT IN ('payment_cancelled','account_deleted')",
                            (txn["id"],)
                        ).fetchone()
                        clawback_cents = (_row and _row["s"]) or 0
                    else:
                        clawback_cents = txn["artist_payout_cents"] or 0

                # Flag the transaction
                if txn:
                    conn.execute("""
                        UPDATE transactions
                        SET status = 'disputed',
                            notes = COALESCE(notes || ' | ', '') || ?
                        WHERE id = ?
                    """, (f"Dispute filed: {dispute_id} reason={reason}", txn["id"]))

                    # Suspend the venue
                    conn.execute("""
                        UPDATE venues SET payment_status = 'suspended',
                        payment_suspended_at = datetime('now'),
                        payment_suspension_reason = ?
                        WHERE id = ?
                    """, (f"Chargeback filed (dispute {dispute_id})", txn["venue_id"]))

                    conn.commit()
                    logger.warning(f"Webhook dispute: txn {txn['id']} flagged, venue {txn['venue_id']} suspended")

                    # Alert admin
                    _wh_admin_alert(conn, f"Chargeback Filed — {txn['venue_name']}",
                        f"""<p>A chargeback has been filed by <strong>{txn['venue_name']}</strong>.</p>
                        <ul>
                        <li><strong>Dispute ID:</strong> {dispute_id}</li>
                        <li><strong>Amount:</strong> ${amount_cents/100:.2f}</li>
                        <li><strong>Reason:</strong> {reason}</li>
                        <li><strong>Gig Date:</strong> {txn['date']}</li>
                        <li><strong>Artist:</strong> {txn['artist_name']}</li>
                        </ul>
                        <p>⚠️ The venue has been <strong>suspended</strong>.
                        The artist payout of <strong>${clawback_cents/100:.2f}</strong>
                        may be clawed back by Stripe. Review immediately in the Stripe dashboard.</p>
                        <p><a href="https://dashboard.stripe.com/disputes/{dispute_id}">View dispute in Stripe →</a></p>""")
                else:
                    conn.commit()
                    _wh_admin_alert(conn, f"Chargeback Filed — Transaction Not Found",
                        f"""<p>A chargeback was filed (dispute {dispute_id}) but no matching transaction was found.</p>
                        <p><strong>Charge ID:</strong> {charge_id}<br>
                        <strong>Amount:</strong> ${amount_cents/100:.2f}<br>
                        <strong>Reason:</strong> {reason}</p>
                        <p><a href="https://dashboard.stripe.com/disputes/{dispute_id}">View in Stripe →</a></p>""")

            except Exception as e:
                logger.error(f"Webhook dispute handler error: {e}")

        # ----------------------------------------------------------------
        # payment_intent.payment_failed — async card failure
        # (catches bank-side declines that come back after scheduler runs)
        # ----------------------------------------------------------------
        elif event_type == "payment_intent.payment_failed":
            try:
                obj = _webhook_get_event_obj(event)
                pi_id  = _webhook_get(obj, "id")
                err    = _webhook_get(obj, "last_payment_error") or {}
                reason = (err.get("message") if isinstance(err, dict) else getattr(err, "message", "")) or "Card declined"

                # Only act if transaction is still in a pre-charged state
                # (avoid double-handling what the scheduler already caught)
                txn = conn.execute("""
                    SELECT t.id, t.gig_id, t.status, g.venue_id, g.date,
                           v.venue_name, a.name as artist_name
                    FROM transactions t
                    JOIN gigs g ON t.gig_id = g.id
                    LEFT JOIN venues v ON v.id = g.venue_id
                    LEFT JOIN artists a ON a.id = t.artist_id
                    WHERE t.stripe_payment_intent_id = ?
                      AND t.status IN ('processing', 'scheduled', 'charge_retry')
                    LIMIT 1
                """, (pi_id,)).fetchone()

                if txn:
                    conn.execute("""
                        UPDATE transactions SET status = 'charge_retry',
                        notes = COALESCE(notes || ' | ', '') || ?
                        WHERE id = ?
                    """, (f"Async payment failed: {reason}", txn["id"]))
                    conn.commit()
                    logger.warning(f"Webhook payment_intent.payment_failed: txn {txn['id']} (PI {pi_id}) → charge_retry")

                    _wh_admin_alert(conn, f"Async Payment Failed — {txn['venue_name']}",
                        f"""<p>A card charge failed asynchronously (caught via webhook).</p>
                        <ul>
                        <li><strong>Venue:</strong> {txn['venue_name']}</li>
                        <li><strong>Gig Date:</strong> {txn['date']}</li>
                        <li><strong>Reason:</strong> {reason}</li>
                        <li><strong>Payment Intent:</strong> {pi_id}</li>
                        </ul>
                        <p>The transaction has been marked for retry. The scheduler will retry on the next run.</p>""")

                    # Audit fix (May 2026): also notify the venue. Synchronous
                    # declines are handled by the scheduler's _handle_charge_failure
                    # (which emails the venue); async webhook-caught declines were
                    # admin-only, so the venue had no idea their card had failed
                    # until the next charge attempt also bounced.
                    try:
                        smtp_settings = _wh_smtp_settings(conn)
                        if smtp_settings and smtp_settings.get("smtp_username"):
                            venue_emails = conn.execute("""
                                SELECT u.email FROM users u
                                JOIN venues v ON v.user_id = u.id
                                WHERE v.id = ?
                                UNION
                                SELECT u.email FROM users u
                                JOIN entity_users eu ON eu.user_id = u.id
                                WHERE eu.entity_type = 'venue' AND eu.entity_id = ?
                            """, (txn["venue_id"], txn["venue_id"])).fetchall()
                            for row in venue_emails:
                                if row and row[0]:
                                    _wh_send_email(smtp_settings, row[0],
                                        f"Card declined — {txn['venue_name']} gig on {txn['date']}",
                                        f"""<p>Hi,</p>
                                        <p>The credit card on file for <strong>{txn['venue_name']}</strong> was declined for the gig on <strong>{txn['date']}</strong>.</p>
                                        <p><strong>Reason:</strong> {reason}</p>
                                        <p>The system will retry the charge automatically on the next scheduled run. To avoid further failures, please update your card in your venue's Payments tab at <a href="https://gigsfill.com/app/venue-create-gigs.html">gigsfill.com</a>.</p>
                                        <p>— The GigsFill Team</p>""")
                    except Exception as _ne:
                        logger.warning(f"Webhook payment_intent.payment_failed: venue-notify error: {_ne}")
                else:
                    logger.info(f"Webhook payment_intent.payment_failed: PI {pi_id} — no actionable transaction found (likely already handled)")

            except Exception as e:
                logger.error(f"Webhook payment_intent.payment_failed error: {e}")

        # ----------------------------------------------------------------
        # account.updated — Artist's Stripe Connect account status changed
        # (catches Stripe-side restrictions/deactivations)
        # ----------------------------------------------------------------
        elif event_type == "account.updated":
            try:
                obj = _webhook_get_event_obj(event)
                connect_account_id = _webhook_get(obj, "id")
                requirements = _webhook_get(obj, "requirements") or {}
                disabled_reason = (
                    requirements.get("disabled_reason") if isinstance(requirements, dict)
                    else getattr(requirements, "disabled_reason", None)
                )
                charges_enabled  = _webhook_get(obj, "charges_enabled")
                payouts_enabled  = _webhook_get(obj, "payouts_enabled")

                # Only act if payouts got disabled
                if payouts_enabled is False or disabled_reason:
                    # Find the artist
                    artist_row = conn.execute("""
                        SELECT a.id, a.name, u.email
                        FROM entity_payment_settings eps
                        JOIN artists a ON a.id = eps.entity_id AND eps.entity_type = 'artist'
                        LEFT JOIN users u ON u.id = a.user_id
                        WHERE eps.stripe_connect_account_id = ?
                        LIMIT 1
                    """, (connect_account_id,)).fetchone()

                    if artist_row:
                        logger.warning(f"Webhook account.updated: Connect account {connect_account_id} payouts disabled (reason: {disabled_reason}) — artist {artist_row['id']} {artist_row['name']}")

                        # Flag in entity_payment_settings
                        conn.execute("""
                            UPDATE entity_payment_settings
                            SET stripe_connect_onboarding_complete = 0,
                                updated_at = datetime('now')
                            WHERE stripe_connect_account_id = ?
                        """, (connect_account_id,))
                        conn.commit()

                        _wh_admin_alert(conn, f"Artist Stripe Account Restricted — {artist_row['name']}",
                            f"""<p>An artist's Stripe Connect account has been restricted or deactivated by Stripe.</p>
                            <ul>
                            <li><strong>Artist:</strong> {artist_row['name']}</li>
                            <li><strong>Connect Account:</strong> {connect_account_id}</li>
                            <li><strong>Payouts Enabled:</strong> {payouts_enabled}</li>
                            <li><strong>Disabled Reason:</strong> {disabled_reason or 'N/A'}</li>
                            </ul>
                            <p>⚠️ Future payouts to this artist will fail.
                            They need to resolve the issue in their Stripe dashboard.
                            Their onboarding status has been reset so they'll be prompted to fix it.</p>
                            <p><a href="https://dashboard.stripe.com/connect/accounts/{connect_account_id}">View account in Stripe →</a></p>""")

                        # Audit fix (May 2026): also notify the artist directly.
                        # Previously only admin was alerted — the artist would
                        # discover the restriction only when their next payout
                        # silently failed. Email them with reconnect guidance.
                        try:
                            smtp_settings = _wh_smtp_settings(conn)
                            if smtp_settings and smtp_settings.get("smtp_username"):
                                artist_emails = conn.execute("""
                                    SELECT u.email FROM users u
                                    JOIN artists a ON a.user_id = u.id
                                    WHERE a.id = ?
                                    UNION
                                    SELECT u.email FROM users u
                                    JOIN entity_users eu ON eu.user_id = u.id
                                    WHERE eu.entity_type = 'artist' AND eu.entity_id = ?
                                """, (artist_row["id"], artist_row["id"])).fetchall()
                                for row in artist_emails:
                                    if row and row[0]:
                                        _wh_send_email(smtp_settings, row[0],
                                            "Action needed — your Stripe payout account is restricted",
                                            f"""<p>Hi {artist_row['name']},</p>
                                            <p>Stripe has placed a restriction on your payout account, which means GigsFill can't send your earnings until it's resolved.</p>
                                            <p><strong>Reason:</strong> {disabled_reason or 'See Stripe dashboard for details'}</p>
                                            <p>To fix this, please reconnect your payout account from your artist profile's Payments tab. You'll be guided through any verification Stripe is requiring.</p>
                                            <p><a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={artist_row['id']}&tab=payments">Update Stripe account →</a></p>
                                            <p>If you don't fix this, payouts for upcoming gigs will fail.</p>
                                            <p>— The GigsFill Team</p>""")
                        except Exception as _ane:
                            logger.warning(f"Webhook account.updated: artist-notify error: {_ane}")
                    else:
                        logger.info(f"Webhook account.updated: Connect account {connect_account_id} not matched to any artist (may be a venue or platform account)")

            except Exception as e:
                logger.error(f"Webhook account.updated error: {e}")

        else:
            logger.info(f"Stripe webhook: unhandled event type '{event_type}' — ignored")

    finally:
        conn.close()

    return {"status": "ok"}


@router.get("/api/stripe/artist/{artist_id}/earnings-summary")
def get_artist_earnings_summary(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Earnings summary: KPI totals + per-venue breakdown."""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)
    artist = db.execute(text("SELECT user_id FROM artists WHERE id = :aid"), {"aid": artist_id}).mappings().first()
    if not artist:
        raise HTTPException(404, "Artist not found")
    uid = artist["user_id"]

    base_where = "(t.artist_id = :aid OR g.artist_id = :aid) AND t.status NOT IN ('payment_cancelled')"

    # All-time total paid out
    row = db.execute(text(f"""
        SELECT COALESCE(SUM(t.artist_payout_cents),0) as total
        FROM transactions t JOIN gigs g ON t.gig_id = g.id
        WHERE {base_where} AND t.status IN ('paid','transferred')
    """), {"aid": artist_id}).mappings().first()
    total_earned = (row["total"] or 0) / 100

    # This month
    row = db.execute(text(f"""
        SELECT COALESCE(SUM(t.artist_payout_cents),0) as total
        FROM transactions t JOIN gigs g ON t.gig_id = g.id
        WHERE {base_where} AND t.status IN ('paid','transferred')
          AND strftime('%Y-%m', g.date) = strftime('%Y-%m', 'now')
    """), {"aid": artist_id}).mappings().first()
    earned_this_month = (row["total"] or 0) / 100

    # This year
    row = db.execute(text(f"""
        SELECT COALESCE(SUM(t.artist_payout_cents),0) as total
        FROM transactions t JOIN gigs g ON t.gig_id = g.id
        WHERE {base_where} AND t.status IN ('paid','transferred')
          AND strftime('%Y', g.date) = strftime('%Y', 'now')
    """), {"aid": artist_id}).mappings().first()
    earned_this_year = (row["total"] or 0) / 100

    # Pending (upcoming)
    row = db.execute(text(f"""
        SELECT COALESCE(SUM(t.artist_payout_cents),0) as total
        FROM transactions t JOIN gigs g ON t.gig_id = g.id
        WHERE {base_where} AND t.status IN ('scheduled','charged','pending','pending_transfer','transferred','test')
    """), {"aid": artist_id}).mappings().first()
    pending_payout = (row["total"] or 0) / 100

    # Gig count — use DISTINCT t.gig_id so multi-slot gigs where the artist
    # took multiple slots count as one gig (each slot creates its own
    # artist_payout transaction). Without DISTINCT, a 2-slot booking by the
    # same artist on one gig inflates the count to 2.
    row = db.execute(text(f"""
        SELECT COUNT(DISTINCT t.gig_id) as n FROM transactions t JOIN gigs g ON t.gig_id = g.id
        WHERE {base_where} AND t.status IN ('paid','transferred')
    """), {"aid": artist_id}).mappings().first()
    gigs_completed = row["n"] or 0

    # Per-venue breakdown — same DISTINCT t.gig_id treatment so a venue
    # where the artist booked two slots on one gig shows gig_count=1, not 2.
    # The total_payout_cents SUM is correct as-is (sum across all transactions
    # is the right earnings figure).
    rows = db.execute(text(f"""
        SELECT v.venue_name, v.id as venue_id,
               COUNT(DISTINCT t.gig_id) as gig_count,
               COALESCE(SUM(t.artist_payout_cents),0) as total_payout_cents,
               MAX(g.date) as last_gig
        FROM transactions t
        JOIN gigs g ON t.gig_id = g.id
        LEFT JOIN venues v ON v.id = g.venue_id
        WHERE {base_where} AND t.status IN ('paid','transferred')
        GROUP BY g.venue_id
        ORDER BY total_payout_cents DESC
    """), {"aid": artist_id}).mappings().all()
    per_venue = [{"venue_name": r["venue_name"] or "Unknown Venue", "venue_id": r["venue_id"],
                  "gig_count": r["gig_count"], "total_earned": (r["total_payout_cents"] or 0) / 100,
                  "last_gig": r["last_gig"]} for r in rows]

    # Earnings by month (last 12)
    rows = db.execute(text(f"""
        SELECT strftime('%Y-%m', g.date) as month,
               COUNT(*) as gigs,
               COALESCE(SUM(t.artist_payout_cents),0) as payout_cents
        FROM transactions t JOIN gigs g ON t.gig_id = g.id
        WHERE {base_where} AND t.status IN ('paid','transferred')
          AND g.date >= date('now','-12 months')
        GROUP BY month ORDER BY month ASC
    """), {"aid": artist_id}).mappings().all()
    by_month = [{"month": r["month"], "gigs": r["gigs"], "earned": (r["payout_cents"] or 0) / 100} for r in rows]

    return {
        "total_earned": total_earned,
        "earned_this_month": earned_this_month,
        "earned_this_year": earned_this_year,
        "pending_payout": pending_payout,
        "gigs_completed": gigs_completed,
        "per_venue": per_venue,
        "by_month": by_month,
    }

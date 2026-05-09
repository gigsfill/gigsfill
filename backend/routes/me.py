from fastapi import APIRouter, Depends, HTTPException, Request, Body
from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.models import Artist, Venue
from sqlalchemy import text
from backend.services.email_dispatch import format_email_date
from backend.utils import utcnow_naive  # required at module scope; delete_account references it inline
from backend.rate_limiter import limiter

router = APIRouter()

@router.get("/api/me")
def get_current_user_info(user=Depends(get_current_user), db=Depends(get_db)):
    _ensure_sms_carrier_column(db)
    from backend.routes.auth import _ensure_email_verified_column
    _ensure_email_verified_column(db)
    
    # get venue_id if user owns a venue
    venue_row = db.execute(text("SELECT id FROM venues WHERE user_id = :uid LIMIT 1"), {"uid": user.id}).first()
    venue_id = venue_row[0] if venue_row else None

    try:
        user_info = db.execute(
            text("SELECT id, email, first_name, last_name, phone, sms_carrier, is_admin, COALESCE(email_verified,0) as email_verified FROM users WHERE id = :uid"),
            {"uid": user.id}
        ).mappings().first()
    except Exception:
        try:
            db.rollback()
        except:
            pass
        user_info = db.execute(
            text("SELECT id, email, first_name, last_name, phone, is_admin FROM users WHERE id = :uid"),
            {"uid": user.id}
        ).mappings().first()
        if not user_info:
            return {}
        result = dict(user_info)
        from backend.utils import to_admin_bool
        result["is_admin"] = to_admin_bool(result.get("is_admin"))
        result['sms_carrier'] = None
        result['email_verified'] = 0
        return result
    
    if not user_info:
        return {}
    result = dict(user_info)
    # Audit fix (May 2026): coerce is_admin to a real bool before serializing
    # to JSON. The column has been migrated from TEXT 'true'/'false' to
    # INTEGER 0/1, but raw SELECT can still return TEXT/INT depending on the
    # SQLite affinity at the moment of read. Frontend defensive checks expect
    # `true` / `false` literals.
    from backend.utils import to_admin_bool
    result["is_admin"] = to_admin_bool(result.get("is_admin"))
    result["venue_id"] = venue_id

    # Also attach artist/venue lists for post-signup redirect
    artists = db.execute(
        text("""SELECT a.id, a.name FROM artists a
                WHERE a.user_id = :uid OR EXISTS (
                    SELECT 1 FROM entity_users eu WHERE eu.entity_type='artist' AND eu.entity_id=a.id AND eu.user_id=:uid
                ) ORDER BY a.id ASC LIMIT 10"""),
        {"uid": user.id}
    ).mappings().all()
    venues = db.execute(
        text("""SELECT v.id, v.venue_name as name FROM venues v
                WHERE v.user_id = :uid OR EXISTS (
                    SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id=v.id AND eu.user_id=:uid
                ) ORDER BY v.id ASC LIMIT 10"""),
        {"uid": user.id}
    ).mappings().all()
    result["artists"] = [dict(a) for a in artists]
    result["venues"] = [dict(v) for v in venues]
    return result

@router.put("/api/me")
@limiter.limit("10/minute")
def update_current_user(request: Request, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Update profile fields. Email change has extra protections — see below.

    Audit fix (May 2026): email change was a silent overwrite — no password
    reconfirmation, no notification to old address, `email_verified` left at 1.
    Stolen-session attacker could swap the email and own the account
    permanently via the forgot-password flow. Now requires `current_password`,
    notifies the old address, resets `email_verified=0`, and re-fires the
    verification email to the NEW address.
    """
    _ensure_sms_carrier_column(db)

    # Read the user's current state so we can detect an email change.
    current = db.execute(
        text("SELECT email, email_verified, password, first_name FROM users WHERE id = :uid"),
        {"uid": user.id}
    ).mappings().first()
    if not current:
        raise HTTPException(404, "User not found")

    new_email = (data.get("email") or "").strip().lower()
    old_email = (current.get("email") or "").strip().lower()
    email_changed = bool(new_email) and new_email != old_email

    if email_changed:
        # Require current password — prevents stolen-session takeover.
        supplied = (data.get("current_password") or "").strip()
        if not supplied:
            raise HTTPException(400, "PASSWORD_REQUIRED: Confirm your current password to change your email.")
        try:
            import bcrypt
            stored = (current.get("password") or "").encode()
            ok = bcrypt.checkpw(supplied.encode(), stored)
        except Exception:
            ok = False
        if not ok:
            raise HTTPException(403, "INVALID_PASSWORD: Current password does not match.")

        # Reject if the new email is already on another account.
        clash = db.execute(
            text("SELECT 1 FROM users WHERE LOWER(email) = :em AND id != :uid"),
            {"em": new_email, "uid": user.id}
        ).first()
        if clash:
            # Generic phrasing to avoid enumeration — paired with C3 fix.
            raise HTTPException(400, "EMAIL_UNAVAILABLE: That email cannot be used.")

    if 'sms_carrier' in data:
        db.execute(
            text("""
                UPDATE users
                SET first_name = :first_name, last_name = :last_name,
                    email = :email, phone = :phone, sms_carrier = :sms_carrier
                WHERE id = :uid
            """),
            {
                "uid": user.id,
                "first_name": data.get("first_name"),
                "last_name": data.get("last_name"),
                "email": data.get("email"),
                "phone": data.get("phone"),
                "sms_carrier": data.get("sms_carrier")
            }
        )
    else:
        db.execute(
            text("""
                UPDATE users
                SET first_name = :first_name, last_name = :last_name,
                    email = :email, phone = :phone
                WHERE id = :uid
            """),
            {
                "uid": user.id,
                "first_name": data.get("first_name"),
                "last_name": data.get("last_name"),
                "email": data.get("email"),
                "phone": data.get("phone")
            }
        )

    # Email-change side effects: reset verification flag + notify both addresses.
    if email_changed:
        db.execute(text("UPDATE users SET email_verified = 0 WHERE id = :uid"), {"uid": user.id})
        db.commit()
        try:
            from backend.email_service import EmailService
            es = EmailService(db)
            if es.enabled:
                first = current.get("first_name") or ""
                # 1. Alert OLD address that the email was changed.
                try:
                    es._send_raw_email(
                        to_email=current["email"],
                        subject="Your GigsFill account email was changed",
                        html_body=(
                            f"<p>Hi {first},</p>"
                            f"<p>The email address on your GigsFill account was changed to "
                            f"<strong>{new_email}</strong>.</p>"
                            f"<p>If this wasn't you, please contact support immediately and reset your password.</p>"
                            f"<p>— The GigsFill Team</p>"
                        ),
                    )
                except Exception as _e1:
                    import logging
                    logging.getLogger("gigsfill.me").warning(f"[EMAIL_CHANGE] old-addr notify error: {_e1}")
                # 2. Re-fire verification on the NEW address.
                try:
                    from backend.routes.auth import _send_verification_email
                    _send_verification_email(db, user.id, new_email, first)
                except Exception as _e2:
                    import logging
                    logging.getLogger("gigsfill.me").warning(f"[EMAIL_CHANGE] new-addr verify-resend error: {_e2}")
        except Exception:
            pass

    db.commit()
    return {"ok": True, "email_changed": email_changed}


def _ensure_sms_carrier_column(db):
    """Add sms_carrier column if missing."""
    try:
        cols = db.execute(text("PRAGMA table_info(users)")).fetchall()
        col_names = [r[1] for r in cols]
        if 'sms_carrier' not in col_names:
            db.execute(text("ALTER TABLE users ADD COLUMN sms_carrier VARCHAR"))
            db.commit()
    except Exception:
        try:
            db.rollback()
        except:
            pass

@router.get("/api/my/artists")
def get_my_artists(user=Depends(get_current_user), db=Depends(get_db)):
    from fastapi.responses import JSONResponse
    from backend.db import get_db_connection as _me_conn
    conn = _me_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT a.id, a.name, a.bio,
               CASE WHEN a.user_id = ? THEN 'owner' ELSE 'member' END as role,
               COALESCE(a.display_order, 999) as display_order
        FROM artists a
        LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = ?
        WHERE a.user_id = ? OR eu.user_id = ?
        ORDER BY display_order ASC, a.id ASC
    """, (user.id, user.id, user.id, user.id))
    
    artists = cursor.fetchall()
    conn.close()
    
    result = [{
        "id": a["id"],
        "name": a["name"],
        "bio": a["bio"],
        "role": a["role"],
        "display_order": a["display_order"]
    } for a in artists]
    
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

@router.get("/api/my/venues")
def get_my_venues(user=Depends(get_current_user), db=Depends(get_db)):
    from fastapi.responses import JSONResponse
    from backend.db import get_db_connection as _me_conn2
    conn = _me_conn2()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT v.id, v.venue_name, v.city, v.state,
               CASE WHEN v.user_id = ? THEN 'owner' ELSE 'member' END as role,
               COALESCE(v.display_order, 999) as display_order
        FROM venues v
        LEFT JOIN entity_users eu ON eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = ?
        WHERE v.user_id = ? OR eu.user_id = ?
        ORDER BY display_order ASC, v.id ASC
    """, (user.id, user.id, user.id, user.id))
    
    venues = cursor.fetchall()
    conn.close()
    
    result = [{
        "id": v["id"],
        "name": v["venue_name"],
        "venue_name": v["venue_name"],
        "city": v["city"],
        "state": v["state"],
        "role": v["role"],
        "display_order": v["display_order"],
    } for v in venues]

    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )

@router.put("/api/my/artists/order")
async def update_artists_order(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    order = data.get('order', [])
    
    
    try:
        for item in order:
            artist_id = item.get('id')
            display_order = item.get('display_order')
            
            result = db.execute(
                text("UPDATE artists SET display_order = :order WHERE id = :aid AND user_id = :uid"),
                {"order": display_order, "aid": artist_id, "uid": user.id}
            )
            
            if result.rowcount > 0:
                pass
            else:
                access = db.execute(
                    text("SELECT 1 FROM entity_users WHERE entity_type = 'artist' AND entity_id = :aid AND user_id = :uid"),
                    {"aid": artist_id, "uid": user.id}
                ).first()
                
                if access:
                    db.execute(
                        text("UPDATE artists SET display_order = :order WHERE id = :aid"),
                        {"order": display_order, "aid": artist_id}
                    )
        
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Operation failed. Please try again.")

@router.put("/api/my/venues/order")
async def update_venues_order(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    data = await request.json()
    order = data.get('order', [])
    
    
    try:
        for item in order:
            venue_id = item.get('id')
            display_order = item.get('display_order')
            
            result = db.execute(
                text("UPDATE venues SET display_order = :order WHERE id = :vid AND user_id = :uid"),
                {"order": display_order, "vid": venue_id, "uid": user.id}
            )
            
            if result.rowcount > 0:
                pass
            else:
                access = db.execute(
                    text("SELECT 1 FROM entity_users WHERE entity_type = 'venue' AND entity_id = :vid AND user_id = :uid"),
                    {"vid": venue_id, "uid": user.id}
                ).first()
                
                if access:
                    db.execute(
                        text("UPDATE venues SET display_order = :order WHERE id = :vid"),
                        {"order": display_order, "vid": venue_id}
                    )
        
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Operation failed. Please try again.")

@router.get("/api/my-artist")
def get_my_artist(user=Depends(get_current_user), db=Depends(get_db)):
    artist = db.query(Artist).filter(Artist.user_id == user.id).first()
    if not artist or not artist.name:
        return None
    return {"id": artist.id, "name": artist.name, "bio": artist.bio}

@router.get("/api/my-venue")
def get_my_venue(user=Depends(get_current_user), db=Depends(get_db)):
    venue = db.query(Venue).filter(Venue.user_id == user.id).first()
    if not venue:
        return None
    return {"id": venue.id, "name": venue.name, "description": venue.description, "booking_frequency_days": venue.booking_frequency_days}

@router.get("/api/me/delete-preview")
def delete_preview(user=Depends(get_current_user), db=Depends(get_db)):
    """Get info needed for delete account modal: owned entities and booked gig counts"""
    user_id = user.id
    
    # Get owned artists with booked gig counts
    artists = db.execute(text("""
        SELECT a.id, a.name, 
            (SELECT COUNT(*) FROM gigs g WHERE g.artist_id = a.id AND g.status = 'booked' AND g.date >= date('now')) as booked_gigs
        FROM artists a WHERE a.user_id = :uid
    """), {"uid": user_id}).mappings().fetchall()
    
    # Get owned venues with booked gig counts
    venues = db.execute(text("""
        SELECT v.id, v.venue_name as name,
            (SELECT COUNT(*) FROM gigs g WHERE g.venue_id = v.id AND g.status = 'booked' AND g.date >= date('now')) as booked_gigs
        FROM venues v WHERE v.user_id = :uid
    """), {"uid": user_id}).mappings().fetchall()
    
    return {
        "artists": [dict(a) for a in artists],
        "venues": [dict(v) for v in venues]
    }

@router.delete("/api/me/delete")
def delete_account(data: dict = Body(default={}), user=Depends(get_current_user), db=Depends(get_db)):
    """Delete user account with optional artist/venue deletion and gig cancellation"""
    import shutil
    from pathlib import Path
    from datetime import datetime
    
    try:
        user_id = user.id
        delete_entity_ids = data.get("delete_entities", [])  # [{ type: "artist"|"venue", id: 123 }, ...]
        
        # ---- Step 1: Cancel booked gigs and send emails for entities being deleted ----
        for entity in delete_entity_ids:
            etype = entity.get("type")
            eid = entity.get("id")
            if not etype or not eid:
                continue
            
            # Find booked gigs
            if etype == "artist":
                booked = db.execute(text("""
                    SELECT g.id, g.date, g.venue_id, v.venue_name, v.user_id as venue_user_id,
                           a.name as artist_name, u_venue.email as venue_email
                    FROM gigs g
                    LEFT JOIN venues v ON g.venue_id = v.id
                    LEFT JOIN artists a ON g.artist_id = a.id
                    LEFT JOIN users u_venue ON v.user_id = u_venue.id
                    WHERE g.artist_id = :eid AND g.status = 'booked'
                """), {"eid": eid}).mappings().fetchall()
            else:  # venue
                booked = db.execute(text("""
                    SELECT g.id, g.date, g.artist_id, g.venue_id, v.venue_name,
                           a.name as artist_name, a.user_id as artist_user_id, u_artist.email as artist_email
                    FROM gigs g
                    LEFT JOIN venues v ON g.venue_id = v.id
                    LEFT JOIN artists a ON g.artist_id = a.id
                    LEFT JOIN users u_artist ON a.user_id = u_artist.id
                    WHERE g.venue_id = :eid AND g.status = 'booked'
                """), {"eid": eid}).mappings().fetchall()
            
            # Send cancellation emails for each booked gig
            try:
                from backend.email_service import EmailService
                email_service = EmailService(db)
                
                for gig in booked:
                    if etype == "artist" and gig.get("venue_email"):
                        # Notify venue that artist is leaving
                        email_service.send_notification_email(
                            user_email=gig["venue_email"],
                            user_id=gig["venue_user_id"],
                            notification_type='venue_gig_cancelled',
                            variables={
                                'user_name': gig['venue_name'],
                                'venue_name': gig['venue_name'],
                                'artist_name': gig.get('artist_name', 'An artist'),
                                'artist_id': str(eid),
                                'venue_id': str(gig['venue_id']),
                                'date': format_email_date(gig['date']),
                                'cancellation_reason': 'Artist account has been deleted'
                            }
                        )
                        # Create notification for venue owner
                        db.execute(text("""
                            INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at, cancellation_reason)
                            VALUES (:uid, 'gig_cancelled', 'Gig Cancelled', :msg, :gid, :vid, :aid, FALSE, :now, :reason)
                        """), {
                            "uid": gig["venue_user_id"], "msg": f"Your gig on {gig['date']} with {gig.get('artist_name', 'an artist')} has been cancelled (artist account deleted).",
                            "gid": gig["id"], "vid": gig["venue_id"], "aid": eid, "now": utcnow_naive(), "reason": "Artist account deleted"
                        })
                    elif etype == "venue" and gig.get("artist_email"):
                        # Notify artist that venue is leaving
                        email_service.send_notification_email(
                            user_email=gig["artist_email"],
                            user_id=gig["artist_user_id"],
                            notification_type='artist_gig_cancelled',
                            variables={
                                'user_name': gig.get('artist_name', 'Artist'),
                                'venue_name': gig['venue_name'],
                                'artist_name': gig.get('artist_name', 'Artist'),
                                'artist_id': str(gig.get('artist_id', '')),
                                'venue_id': str(eid),
                                'date': format_email_date(gig['date']),
                                'cancellation_reason': 'Venue account has been deleted'
                            }
                        )
                        db.execute(text("""
                            INSERT INTO notifications (user_id, notification_type, title, message, gig_id, venue_id, artist_id, is_read, created_at, cancellation_reason)
                            VALUES (:uid, 'gig_cancelled', 'Gig Cancelled', :msg, :gid, :vid, :aid, FALSE, :now, :reason)
                        """), {
                            "uid": gig["artist_user_id"], "msg": f"Your gig on {gig['date']} at {gig['venue_name']} has been cancelled (venue account deleted).",
                            "gid": gig["id"], "vid": eid, "aid": gig.get("artist_id"), "now": utcnow_naive(), "reason": "Venue account deleted"
                        })
            except Exception as e:
                pass  # Emails non-critical
            
            # ---- Step 2: Delete entity data ----
            if etype == "artist":
                # FIX (May 2026): cancel any in-flight transactions for this artist before
                # deleting. Otherwise the payout scheduler would try to process them and
                # transfer money to a Stripe Connect account whose underlying GigsFill
                # user is gone. Set status to 'account_deleted' rather than DELETE so
                # the audit trail is preserved.
                db.execute(text("""
                    UPDATE transactions SET status = 'account_deleted',
                        notes = COALESCE(notes, '') || ' [Artist account deleted]'
                    WHERE artist_id = :eid
                      AND status IN ('scheduled', 'test', 'charge_retry',
                                     'pending_transfer', 'transfer_failed')
                """), {"eid": eid})
                # Reset booked gigs to open so venue can rebook
                db.execute(text("UPDATE gigs SET status = 'open', artist_id = NULL WHERE artist_id = :eid AND status = 'booked'"), {"eid": eid})
                # Remove artist from any other gig references
                db.execute(text("UPDATE gigs SET artist_id = NULL WHERE artist_id = :eid"), {"eid": eid})
                # Same for slot-based bookings
                db.execute(text("UPDATE gig_slots SET status = 'open', artist_id = NULL WHERE artist_id = :eid AND status = 'booked'"), {"eid": eid})
                db.execute(text("UPDATE gig_slots SET artist_id = NULL WHERE artist_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM preferred_artists WHERE artist_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM artist_media WHERE artist_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM entity_users WHERE entity_type = 'artist' AND entity_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM entity_invitations WHERE entity_type = 'artist' AND entity_id = :eid"), {"eid": eid})
                # FIX (May 2026): missing in original — clean up artist's Stripe Connect settings + reviews + targeted messages
                db.execute(text("DELETE FROM entity_payment_settings WHERE entity_type = 'artist' AND entity_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM artist_reviews WHERE artist_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM venue_reviews WHERE artist_id = :eid"), {"eid": eid})
                # Audit fix (May 2026): also clean waitlist rows so future
                # waitlist offers don't FK-reference a deleted artist.
                db.execute(text("DELETE FROM gig_waitlist WHERE artist_id = :eid"), {"eid": eid})
                try:
                    db.execute(text("DELETE FROM waitlist_offered WHERE artist_id = :eid"), {"eid": eid})
                except Exception:
                    pass  # table may not exist on older deployments
                # gig_messages: delete messages targeting this artist
                try:
                    db.execute(text("DELETE FROM gig_messages WHERE target_artist_id = :eid"), {"eid": eid})
                except Exception:
                    pass  # column may not exist on older deployments — gig_messages migration is recent
                db.execute(text("DELETE FROM notifications WHERE artist_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM artists WHERE id = :eid AND user_id = :uid"), {"eid": eid, "uid": user_id})
                
                # Delete media folder
                media_path = Path(f"media/artists/{eid}")
                if media_path.exists():
                    shutil.rmtree(media_path)
                    
            elif etype == "venue":
                # FIX (May 2026): cancel any in-flight transactions for this venue's gigs before
                # deleting. Set status to 'account_deleted' for audit trail.
                db.execute(text("""
                    UPDATE transactions SET status = 'account_deleted',
                        notes = COALESCE(notes, '') || ' [Venue account deleted]'
                    WHERE gig_id IN (SELECT id FROM gigs WHERE venue_id = :eid)
                      AND status IN ('scheduled', 'test', 'charge_retry',
                                     'pending_transfer', 'transfer_failed')
                """), {"eid": eid})
                # First reset booked gigs to open (don't delete venue's gig slots)
                # Actually delete all gigs for this venue since venue is going away
                db.execute(text("DELETE FROM gigs WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM preferred_artists WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM venue_media WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM entity_users WHERE entity_type = 'venue' AND entity_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM entity_invitations WHERE entity_type = 'venue' AND entity_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM venue_email_notifications WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM venue_email_history WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM artist_invitations WHERE venue_id = :eid"), {"eid": eid})
                # FIX (May 2026): missing in original — affiliate referrals + reviews
                db.execute(text("DELETE FROM affiliate_referrals WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM affiliate_earnings WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM venue_reviews WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM artist_reviews WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM notifications WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM entity_payment_settings WHERE entity_type = 'venue' AND entity_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM venue_payment_overrides WHERE venue_id = :eid"), {"eid": eid})
                db.execute(text("DELETE FROM venues WHERE id = :eid AND user_id = :uid"), {"eid": eid, "uid": user_id})
                
                media_path = Path(f"media/venues/{eid}")
                if media_path.exists():
                    shutil.rmtree(media_path)
        
        # ---- Step 3: Delete user-level data ----
        db.execute(text("DELETE FROM email_preferences WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM support_tickets WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM recommendations WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM notifications WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM entity_users WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM payment_methods WHERE user_id = :uid"), {"uid": user_id})
        # FIX (May 2026): missing user-level cleanups — affiliate, sms, reviews authored
        try:
            db.execute(text("DELETE FROM sms_preferences WHERE user_id = :uid"), {"uid": user_id})
        except Exception:
            pass  # table may not exist on older DB
        db.execute(text("DELETE FROM affiliate_recommend_emails WHERE sender_user_id = :uid"), {"uid": user_id})
        # Affiliate-as-user: if this user was an affiliate, drop their referrals/earnings/payouts.
        # We've already deleted affiliate_referrals.venue_id rows above (during venue deletion);
        # this catches cases where user was an affiliate for OTHER people's venues.
        db.execute(text("DELETE FROM affiliate_referrals WHERE affiliate_user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM affiliate_earnings WHERE affiliate_user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM affiliate_payouts WHERE affiliate_user_id = :uid"), {"uid": user_id})
        # Reviews authored by this user (across both directions)
        db.execute(text("DELETE FROM artist_reviews WHERE reviewer_user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM venue_reviews WHERE reviewer_user_id = :uid"), {"uid": user_id})
        # gig_messages sent by this user
        try:
            db.execute(text("DELETE FROM gig_messages WHERE sender_user_id = :uid"), {"uid": user_id})
        except Exception:
            pass
        
        # ---- Step 4: Delete user ----
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        
        # Delete user media folder
        media_path = Path(f"media/user_{user_id}")
        if media_path.exists():
            shutil.rmtree(media_path)
        
        db.commit()
        # Audit fix (May 2026): explicitly clear the session cookie on
        # successful deletion. Subsequent requests would 401 anyway (the
        # user row is gone), but the cookie should be cleared properly so
        # the browser doesn't send a stale token on every request.
        from fastapi.responses import JSONResponse
        resp = JSONResponse({"success": True})
        try:
            from backend.routes.auth import clear_session_cookie
            clear_session_cookie(resp)
        except Exception:
            # Fallback: delete cookie directly if helper isn't importable.
            resp.delete_cookie("session", path="/")
        return resp
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Failed to delete account. Please try again.")

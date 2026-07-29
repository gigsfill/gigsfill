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

    # Audit fix (May 2026 part 7): normalize + validate phone the same way
    # signup does (10-digit US, formatted `(XXX) XXX-XXXX`). Previously PUT
    # wrote whatever the client sent — users could degrade their phone to
    # garbage via the API, breaking SMS + venue booking-contact display.
    if 'phone' in data and data.get('phone') is not None:
        import re as _re_phone
        _raw = str(data.get('phone') or '').strip()
        if _raw:
            _digits = _re_phone.sub(r'\D', '', _raw)
            if len(_digits) == 11 and _digits.startswith('1'):
                _digits = _digits[1:]
            if len(_digits) != 10:
                raise HTTPException(400, "INVALID_PHONE: Phone must be a 10-digit US number.")
            data['phone'] = f"({_digits[0:3]}) {_digits[3:6]}-{_digits[6:10]}"
        else:
            data['phone'] = None

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
    """Add sms_carrier column if missing.

    Audit fix (May 2026 part 6): PRAGMA is SQLite-only — branch on engine."""
    try:
        from backend.db import _IS_POSTGRES
        if _IS_POSTGRES:
            col_names = [r[0] for r in db.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name='users'"
            )).fetchall()]
        else:
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

    # Jul 2026: exclude tombstoned artists (deleted_at IS NOT NULL). The row
    # sticks around so historical joins render "[Deleted] X" correctly in
    # payment history / past-gig lists, but the owner's account picker and
    # entity switcher only show live entities.
    cursor.execute("""
        SELECT DISTINCT a.id, a.name, a.bio,
               CASE WHEN a.user_id = ? THEN 'owner' ELSE 'member' END as role,
               COALESCE(a.display_order, 999) as display_order
        FROM artists a
        LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = ?
        WHERE (a.user_id = ? OR eu.user_id = ?)
          AND a.deleted_at IS NULL
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

    # Jul 2026: exclude tombstoned venues — see get_my_artists rationale.
    cursor.execute("""
        SELECT DISTINCT v.id, v.venue_name, v.city, v.state,
               CASE WHEN v.user_id = ? THEN 'owner' ELSE 'member' END as role,
               COALESCE(v.display_order, 999) as display_order
        FROM venues v
        LEFT JOIN entity_users eu ON eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = ?
        WHERE (v.user_id = ? OR eu.user_id = ?)
          AND v.deleted_at IS NULL
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

    # Booked-gig count must include multi-slot bookings (gig_slots.artist_id),
    # not just legacy single-slot (gigs.artist_id). Without the slot leg the
    # artist sees "0 upcoming gigs" before deletion even when they have
    # multi-slot bookings — they could delete their account thinking nothing
    # will be cancelled, then the venue is left waiting for a ghost.
    # COUNT(DISTINCT g.id) so a gig where the artist took two slots counts once.
    # Jul 2026 refactor: dropped `g.artist_id = a.id` OR-leg. Post-backfill
    # (db.setup_database), every gig has a `gig_slots` row so slot-only
    # covers both shapes. Slot-side status IN filter keeps the "in-flight"
    # bookings that were previously the point of the OR.
    artists = db.execute(text("""
        SELECT a.id, a.name,
            (SELECT COUNT(DISTINCT g.id)
             FROM gigs g
             JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = a.id
                              AND gs.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
             WHERE g.date >= :today) as booked_gigs
        FROM artists a
        WHERE a.user_id = :uid AND a.deleted_at IS NULL
    """), {"uid": user_id, "today": utcnow_naive().date().isoformat()}).mappings().fetchall()

    # Get owned venues with booked gig counts.
    # Audit fix (May 2026 part 6): `date('now')` is SQLite-only; bind a Python
    # date string for cross-engine compat.
    # Audit fix (May 2026 part 7): include awaiting_venue_contract +
    # pending_contract so the delete-preview warns about in-flight contract
    # gigs (previously the user was never told and the gig got stuck after delete).
    venues = db.execute(text("""
        SELECT v.id, v.venue_name as name,
            (SELECT COUNT(*) FROM gigs g WHERE g.venue_id = v.id
                                          AND g.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
                                          AND g.date >= :today) as booked_gigs
        FROM venues v
        WHERE v.user_id = :uid AND v.deleted_at IS NULL
    """), {"uid": user_id, "today": utcnow_naive().date().isoformat()}).mappings().fetchall()
    
    # Jul 2026: also return `other_users_count` per entity so the modal
    # can distinguish solo-owned entities (auto-deleted regardless of the
    # checkbox — see delete_account) from team-shared entities (checkbox
    # is a real choice; unchecked keeps the entity alive under the remaining
    # team). Team members live in `entity_users`; the owner is on the entity
    # row itself, so this count is "additional humans besides me".
    from backend.services.entity_delete import (
        count_other_team_members as _count_others,
        list_live_transactions as _list_live_txns,
    )
    a_out = []
    for a in artists:
        d = dict(a)
        d["other_users_count"] = _count_others(db, "artist", d["id"], user_id)
        # Jul 2026: return live-txn blockers per entity so the delete-account
        # modal can show them upfront instead of firing DELETE and eating a
        # 409. Empty list means "no blockers, safe to tombstone."
        d["live_txns"] = _list_live_txns(db, "artist", d["id"])
        a_out.append(d)
    v_out = []
    for v in venues:
        d = dict(v)
        d["other_users_count"] = _count_others(db, "venue", d["id"], user_id)
        d["live_txns"] = _list_live_txns(db, "venue", d["id"])
        v_out.append(d)

    # Delegated memberships: entities where this user is on `entity_users`
    # (team member) but NOT the owner on the entity row itself. These will
    # NOT be deleted — they stay alive under the remaining owner/team. We
    # surface them in the modal so the user knows what teams they're
    # leaving behind (transparency + preventing surprise for the entity
    # owner who'd otherwise silently lose a team member).
    delegated = []
    for row in db.execute(text("""
        SELECT eu.entity_type, eu.entity_id, eu.role,
               CASE WHEN eu.entity_type = 'artist'
                    THEN (SELECT name       FROM artists WHERE id = eu.entity_id)
                    ELSE (SELECT venue_name FROM venues  WHERE id = eu.entity_id)
               END as name
        FROM entity_users eu
        WHERE eu.user_id = :uid
          AND NOT EXISTS (
              SELECT 1 FROM artists a WHERE a.id = eu.entity_id AND eu.entity_type = 'artist' AND a.user_id = :uid
          )
          AND NOT EXISTS (
              SELECT 1 FROM venues  v WHERE v.id = eu.entity_id AND eu.entity_type = 'venue'  AND v.user_id = :uid
          )
    """), {"uid": user_id}).mappings().all():
        d = dict(row)
        if d.get("name"):
            delegated.append(d)

    return {"artists": a_out, "venues": v_out, "delegated_memberships": delegated}

@router.delete("/api/me/delete")
@limiter.limit("3/hour")
def delete_account(request: Request, data: dict = Body(default={}), user=Depends(get_current_user), db=Depends(get_db)):
    """Delete user account with optional artist/venue deletion and gig cancellation.

    Owner-only for entities. Solo-owned entities (no other team members) are
    auto-added to the delete list so they don't limbo — see below. Standalone
    entity deletion (without deleting the user) is available at
    DELETE /api/artists/{id} and DELETE /api/venues/{id}.

    Audit fix (May 2026 part 7): rate-limited 3/hour so a double-clicked Delete
    button (or a malicious script) can't fire concurrent cascades.

    Jul 2026 (this refactor):
      1. Per-entity ownership guard BEFORE any cleanup runs — fixes the
         pre-existing hole where a client could POST arbitrary entity ids and
         wipe their child data before the guarded final DELETE no-op'd.
      2. Auto-augment `delete_entities` with any owned entity that has NO
         other team members (via entity_users). Rationale: if the only human
         who could use the entity is going away, keeping the row alive
         orphans it forever — nobody can log in to it, no re-owner exists.
      3. Per-entity cascade extracted to services.entity_delete so the
         standalone DELETE endpoints share the exact pipeline.
    """
    import shutil
    from pathlib import Path
    from backend.services.entity_delete import (
        assert_owner as _assert_owner,
        assert_no_charged_transactions as _assert_no_charged,
        count_other_team_members as _count_others,
        delete_artist as _delete_artist,
        delete_venue as _delete_venue,
    )

    try:
        user_id = user.id
        requested = data.get("delete_entities", [])  # [{ type: "artist"|"venue", id: 123 }, ...]

        # ---- Step 0a: normalize + de-duplicate requested list ----
        # Frontend could double-list an entity if the checklist state got weird.
        seen = set()
        norm_requested = []
        for ent in requested:
            etype = ent.get("type")
            eid = ent.get("id")
            if not etype or not eid or etype not in ("artist", "venue"):
                continue
            key = (etype, int(eid))
            if key in seen:
                continue
            seen.add(key)
            norm_requested.append({"type": etype, "id": int(eid)})

        # ---- Step 0b: per-entity ownership guard ----
        # Refuse the whole request if the user isn't the owner of any listed
        # entity. This fires BEFORE any DB mutation so a malicious payload
        # can't wipe a venue's gigs and land on a no-op DELETE FROM venues.
        for ent in norm_requested:
            _assert_owner(db, ent["type"], ent["id"], user_id)

        # ---- Step 0c: auto-augment with solo-owned entities ----
        # Any entity the user owns that has ZERO other team members would
        # limbo after account delete. Fold them into the delete list so the
        # frontend can't accidentally leave a ghost by un-checking the box.
        owned_artists = db.execute(
            text("SELECT id FROM artists WHERE user_id = :uid AND deleted_at IS NULL"),
            {"uid": user_id}
        ).fetchall()
        owned_venues = db.execute(
            text("SELECT id FROM venues WHERE user_id = :uid AND deleted_at IS NULL"),
            {"uid": user_id}
        ).fetchall()
        for (aid,) in owned_artists:
            if ("artist", aid) in seen:
                continue
            if _count_others(db, "artist", aid, user_id) == 0:
                norm_requested.append({"type": "artist", "id": aid})
                seen.add(("artist", aid))
        for (vid,) in owned_venues:
            if ("venue", vid) in seen:
                continue
            if _count_others(db, "venue", vid, user_id) == 0:
                norm_requested.append({"type": "venue", "id": vid})
                seen.add(("venue", vid))

        # ---- Step 0d: refuse if any charged transactions still exist ----
        # Same as before — force refund flow first to preserve audit trail.
        for ent in norm_requested:
            _assert_no_charged(db, ent["type"], ent["id"])

        # ---- Step 1: per-entity cascade (notify counterparties + wipe data) ----
        # services.entity_delete handles: cancel-notify booked-gig
        # counterparties, mark in-flight transactions as `account_deleted`,
        # release the entity's grip on gigs/slots/contracts, wipe every
        # child table, delete the entity row.
        #
        # Audit fix (Jul 2026): each per-entity delete now returns the
        # list of filesystem paths to clean up AFTER the whole delete-
        # account transaction commits. Doing rmtree before commit meant
        # any commit-fail = permanent media loss with entities still
        # live.
        _rm_paths_pending: list[str] = []
        for ent in norm_requested:
            if ent["type"] == "artist":
                _paths = _delete_artist(db, ent["id"], user_id) or []
            else:
                _paths = _delete_venue(db, ent["id"], user_id) or []
            _rm_paths_pending.extend(_paths)

        # ---- Step 3: Delete user-level data ----
        db.execute(text("DELETE FROM email_preferences WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM support_tickets WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM recommendations WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM notifications WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM entity_users WHERE user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM payment_methods WHERE user_id = :uid"), {"uid": user_id})
        # Audit fix (May 2026 part 5): drop pending entity_invitations the
        # deleted user authored. Otherwise the entity's user-management UI
        # would render orphan invitation rows whose inviter_first_name /
        # inviter_last_name FK-pointed at a non-existent user.
        try:
            db.execute(
                text("DELETE FROM entity_invitations WHERE invited_by_user_id = :uid AND status = 'pending'"),
                {"uid": user_id}
            )
        except Exception as _ei:
            import logging as _logging
            _logging.getLogger("gigsfill.me").warning(f"delete_account: entity_invitations cleanup failed: {_ei}")
        # FIX (May 2026): missing user-level cleanups — affiliate, sms, reviews authored
        try:
            db.execute(text("DELETE FROM sms_preferences WHERE user_id = :uid"), {"uid": user_id})
        except Exception:
            pass  # table may not exist on older DB
        db.execute(text("DELETE FROM affiliate_recommend_emails WHERE sender_user_id = :uid"), {"uid": user_id})
        # Affiliate-as-user: if this user was an affiliate, drop their
        # referrals/earnings/payouts. Note (Jul 2026 audit): the previous
        # comment claimed venue-side affiliate_referrals rows are removed
        # during venue deletion above, but entity_delete.delete_venue does
        # NOT touch affiliate_referrals — that's intentional per the
        # tombstone history-preservation policy, so this catches rows for
        # any venue (still-live or tombstoned) that this affiliate
        # referred.
        db.execute(text("DELETE FROM affiliate_referrals WHERE affiliate_user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM affiliate_earnings WHERE affiliate_user_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM affiliate_payouts WHERE affiliate_user_id = :uid"), {"uid": user_id})
        # Reviews authored by this user — PRESERVE the row and NULL out
        # reviewer_user_id (see reviews table schema; the column is nullable).
        # BUG FIX (Jul 2026 audit): the previous DELETE damaged OTHER users'
        # entities. A venue with 20 five-star reviews would lose the ones
        # written by any deleting user, dropping the venue's public rating
        # even though the venue owner did nothing. Now the review's text +
        # rating survive (aggregated into avg_rating/review_count as normal)
        # and only the author link is cleared for privacy. Same policy as
        # entity_delete for tombstoned entities — reviews are historical.
        try:
            db.execute(text("UPDATE artist_reviews SET reviewer_user_id = NULL WHERE reviewer_user_id = :uid"), {"uid": user_id})
            db.execute(text("UPDATE venue_reviews  SET reviewer_user_id = NULL WHERE reviewer_user_id = :uid"), {"uid": user_id})
        except Exception:
            # Column may be NOT NULL on older deployments — fall back to the
            # historical DELETE so account-delete still succeeds. Log so the
            # schema drift is visible.
            import logging as _logging
            _logging.getLogger("gigsfill.me").warning(
                f"delete_account: could not NULL reviewer_user_id (schema may be NOT NULL); falling back to hard delete for user {user_id}"
            )
            db.execute(text("DELETE FROM artist_reviews WHERE reviewer_user_id = :uid"), {"uid": user_id})
            db.execute(text("DELETE FROM venue_reviews WHERE reviewer_user_id = :uid"), {"uid": user_id})
        # BUG FIX (Jul 2026 audit): also wipe user-level W-9 (encrypted TIN +
        # legal name + mailing address for affiliate 1099s). Was left behind
        # after user delete — GDPR/PII data-retention leak.
        try:
            db.execute(text("DELETE FROM w9_forms WHERE entity_type = 'user' AND entity_id = :uid"), {"uid": user_id})
        except Exception:
            pass
        # gig_messages sent by this user
        try:
            db.execute(text("DELETE FROM gig_messages WHERE sender_user_id = :uid"), {"uid": user_id})
        except Exception:
            pass
        # BUG FIX (Jul 2026 audit): user-scoped rows the previous cascade missed.
        # user_settings + user_availability are per-user preference tables that
        # dangle indefinitely after account delete. artist_email_digest_queue
        # holds pending open-gig-digest rows keyed by user_id that would try to
        # send to a nonexistent recipient on the next digest tick.
        for _stmt in (
            "DELETE FROM user_settings WHERE user_id = :uid",
            "DELETE FROM user_availability WHERE user_id = :uid",
            "DELETE FROM artist_email_digest_queue WHERE user_id = :uid",
        ):
            try:
                db.execute(text(_stmt), {"uid": user_id})
            except Exception:
                pass

        # ---- Step 3b: Belt-and-suspenders — release ownership on any
        # pre-existing tombstoned artist/venue this user still owns.
        # 2026-07-26 fix. Before the entity_delete.py tombstone was updated
        # to NULL user_id in the same step, tombstoning left dangling FKs
        # that blocked DELETE FROM users. Rows created before that fix
        # still exist in production; this catches them at delete-account
        # time so no user is stuck in the FK-constraint-fail loop.
        db.execute(text("""
            UPDATE artists SET user_id = NULL
             WHERE user_id = :uid AND deleted_at IS NOT NULL
        """), {"uid": user_id})
        db.execute(text("""
            UPDATE venues  SET user_id = NULL
             WHERE user_id = :uid AND deleted_at IS NOT NULL
        """), {"uid": user_id})

        # ---- Step 4: Delete user ----
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})

        db.commit()

        # ---- Step 5: filesystem cleanup — deferred until AFTER the
        # commit succeeded (Jul 2026 audit fix). If any of these rmtree
        # calls fails, the DB state is already durable so the account is
        # deleted for real; a leaked upload directory is much better
        # than a partial rollback with media gone.
        from backend.services.entity_delete import _rm_tree
        for _p in _rm_paths_pending:
            try:
                _rm_tree(_p)
            except Exception:
                pass
        try:
            media_path = Path(f"media/user_{user_id}")
            if media_path.exists():
                shutil.rmtree(media_path)
        except Exception:
            pass

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
            # Jul 2026 audit fix: cookie name is `session_token`, not `session`
            # (see backend/routes/auth.py:68). Previous fallback would leave
            # the browser with a valid cookie for 7 days if clear_session_cookie
            # failed to import.
            resp.delete_cookie("session_token", path="/")
        return resp
    except HTTPException:
        # Owner-guard (403), refund-guard (409), and any other structured
        # rejections need to reach the client with their real status. The
        # blanket 500 below used to hide these.
        db.rollback()
        raise
    except Exception as e:
        import logging as _log
        _log.getLogger("gigsfill.me").exception(f"delete_account failed for user {user.id}: {e}")
        db.rollback()
        raise HTTPException(500, "Failed to delete account. Please try again.")


# ─────────────────────────────────────────────────────────────────────────────
# Part 10p: Multi-venue artist invitations
# ─────────────────────────────────────────────────────────────────────────────
# One click in the "Invite Artists" modal can target N emails × M venues. The
# backend writes one `artist_invitations` row per (email, venue) pair, all
# sharing the same `token` (so signup or accept-preferred consumes them all
# together) and the same `invite_group_id` (for cross-row queries). Exactly
# ONE email is sent per recipient — it lists every venue the inviter selected
# in the body.

import logging as _inv_log
_inv_logger = _inv_log.getLogger("gigsfill.invite_artists")


def _invite_parse_emails(raw):
    """Parse comma/semicolon/space/newline-separated emails. Returns (valid, invalid)."""
    import re as _re
    parts = _re.split(r'[,;\s\n]+', (raw or "").strip())
    seen, valid, invalid = set(), [], []
    for p in parts:
        e = p.strip().lower()
        if not e:
            continue
        if e in seen:
            continue
        seen.add(e)
        if _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', e):
            valid.append(e)
        else:
            invalid.append(e)
    return valid, invalid


def _invite_render_email(invitee_email, inviter_first_name, venues, message, signup_url, decline_url, login_url, is_existing_user):
    """Render the invite email HTML body. `venues` is a list of dicts with venue_name + optional city/state."""
    import html as _html
    inviter_safe = _html.escape(inviter_first_name or "A venue")
    # Build the "Venue 1, Venue 2, and Venue 3" string
    names = [v["venue_name"] for v in venues if v.get("venue_name")]
    if len(names) == 0:
        venues_phrase = "their venue"
    elif len(names) == 1:
        venues_phrase = _html.escape(names[0])
    elif len(names) == 2:
        venues_phrase = f"{_html.escape(names[0])} and {_html.escape(names[1])}"
    else:
        venues_phrase = ", ".join(_html.escape(n) for n in names[:-1]) + f", and {_html.escape(names[-1])}"
    personal_note = ""
    if message:
        personal_note = (
            '<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:6px;'
            'padding:16px;margin:0 0 24px;font-size:14px;line-height:1.6;color:#374151;font-style:italic;">'
            f'"{_html.escape(message)}"<div style="margin-top:8px;font-style:normal;font-weight:500;color:#0369a1;">'
            f'— {inviter_safe}</div></div>'
        )
    if is_existing_user:
        main_text = (
            f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#4b5563;">'
            f'<strong>{inviter_safe}</strong> from {venues_phrase} wants to connect on GigsFill! '
            f'Log in and you\'ll be asked whether to request preferred-artist status at '
            f'{"all of these venues" if len(names) > 1 else "this venue"} in one click.</p>'
        )
        cta_text = "Log In to GigsFill"
        cta_url = login_url
    else:
        main_text = (
            f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#4b5563;">'
            f'<strong>{inviter_safe}</strong> from {venues_phrase} is using <strong>GigsFill</strong> '
            f'and invited you to join. Create a free artist account and you\'ll be asked whether to '
            f'request preferred-artist status at {"all of these venues" if len(names) > 1 else "this venue"} '
            f'right after signup.</p>'
        )
        cta_text = "Create Your Free Artist Account"
        cta_url = signup_url
    body_html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" style="background:#f8f9fa;padding:40px 20px;">
<tr><td>
<table role="presentation" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<span style="font-size:18px;font-weight:700;letter-spacing:0.15em;color:#1a1a2e;">GIGSFILL</span>
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 16px;font-size:22px;font-weight:600;color:#1a1a2e;">You're Invited! 🎶</h1>
{main_text}
{personal_note}
<div style="text-align:center;margin:32px 0;">
<a href="{cta_url}" style="display:inline-block;background:#06b6d4;color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;">{cta_text}</a>
</div>
<p style="margin:0 0 16px;font-size:13px;color:#9ca3af;text-align:center;">Free to sign up · No commitment required</p>
<p style="margin:24px 0 0;font-size:11px;color:#9ca3af;text-align:center;">Not interested? <a href="{decline_url}" style="color:#9ca3af;text-decoration:underline;">Decline this invitation</a></p>
</td></tr>
<tr><td style="padding:24px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill · <a href="https://gigsfill.com" style="color:#1a1a2e;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr></table>
</body></html>'''
    subject = f"{inviter_first_name or 'A venue'} from {names[0] if len(names) == 1 else f'{len(names)} venues'} invited you to join GigsFill!"
    return subject, body_html


@router.post("/api/me/invite-artists")
def invite_artists_multi_venue(data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """Send one batch of artist invitations across multiple venues that the current
    user controls. One row written per (email, venue) pair, all sharing the same
    token + invite_group_id. Exactly one email sent per recipient — the email
    lists every selected venue."""
    import secrets as _sec, uuid as _uuid
    from email.mime.text import MIMEText as _MT
    from email.mime.multipart import MIMEMultipart as _MM
    from email.utils import formataddr as _fa
    from backend.utils import check_venue_access
    from backend.email_service import _smtp_send
    import smtplib as _smtp

    emails_raw = (data.get("emails") or "").strip()
    venue_ids = data.get("venue_ids") or []
    message = (data.get("message") or "").strip()

    if not emails_raw:
        raise HTTPException(400, "At least one email address is required")
    if not isinstance(venue_ids, list) or len(venue_ids) == 0:
        raise HTTPException(400, "Select at least one venue")
    try:
        venue_ids = [int(v) for v in venue_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid venue_ids — must be integers")

    # Authorization: user must have access to EVERY venue they're inviting on
    # behalf of. Otherwise an attacker could send invites "from" a venue they
    # don't control.
    for vid in venue_ids:
        check_venue_access(db, vid, user.id)

    valid_emails, invalid_emails = _invite_parse_emails(emails_raw)
    if not valid_emails:
        raise HTTPException(400, "No valid email addresses found")

    # Pull venue info for all the venues we're inviting from
    if not venue_ids:
        venues_info = []
    else:
        _params = {f"v{i}": vid for i, vid in enumerate(venue_ids)}
        _placeholders = ", ".join(f":v{i}" for i in range(len(venue_ids)))
        venues_info = db.execute(
            text(f"SELECT id, venue_name, city, state FROM venues WHERE id IN ({_placeholders})"),
            _params
        ).mappings().all()
    venues_info = [dict(v) for v in venues_info]
    if len(venues_info) != len(venue_ids):
        raise HTTPException(404, "One or more venues not found")

    # Inviter display name — first name preferred, fall back to email local-part
    first_name = (getattr(user, "first_name", None) or "").strip()
    if not first_name:
        first_name = (user.email or "").split("@")[0]
    inviter_full = (f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}").strip() or user.email

    # SMTP settings
    _smtp_rows = db.execute(
        text("SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN "
             "('platform_email','platform_email_password','platform_smtp_server','platform_smtp_port','platform_email_from_name')")
    ).fetchall()
    _s = {r[0]: r[1] for r in _smtp_rows}
    smtp_email    = _s.get('platform_email') or ""
    smtp_password = _s.get('platform_email_password') or ""
    smtp_server   = _s.get('platform_smtp_server') or "smtp.gmail.com"
    smtp_port     = int(_s.get('platform_smtp_port') or 587)
    from_name     = _s.get('platform_email_from_name') or "GigsFill"

    # Resolve site_url for token links
    site_url = db.execute(
        text("SELECT setting_value FROM platform_settings WHERE setting_key='site_url'")
    ).scalar() or "https://gigsfill.com"

    # Process each invitee email
    sent, bounced, skipped_already_pending = [], [], []
    for email in valid_emails:
        # Skip if there is already a pending row for this email at ANY of the
        # selected venues that is younger than 24h (anti-spam cooldown). We
        # still allow re-invite to NEW venues; only rows for venues already in
        # the pending set are blocked.
        existing_pending = db.execute(
            text("SELECT DISTINCT venue_id FROM artist_invitations "
                 "WHERE LOWER(invited_email) = LOWER(:e) AND status = 'pending' "
                 "AND sent_at > datetime('now','-1 day')"),
            {"e": email}
        ).fetchall()
        recent_pending_vids = {int(r[0]) for r in existing_pending}
        target_vids = [v for v in venue_ids if v not in recent_pending_vids]
        if not target_vids:
            skipped_already_pending.append(email)
            continue

        target_venues_info = [v for v in venues_info if v["id"] in target_vids]

        # Detect existing GigsFill user (case-insensitive)
        existing_user_row = db.execute(
            text("SELECT id FROM users WHERE LOWER(email) = LOWER(:e)"),
            {"e": email}
        ).mappings().first()
        is_existing = existing_user_row is not None
        existing_uid = existing_user_row["id"] if is_existing else None

        # Generate token + invite_group_id ONCE per email
        token = _sec.token_urlsafe(32)
        invite_group_id = str(_uuid.uuid4())
        # 90-day expiry
        from datetime import datetime as _dt, timedelta as _td
        expires_at = (_dt.utcnow() + _td(days=90)).strftime("%Y-%m-%d %H:%M:%S")

        # Build URLs (decline always works even if signed_up; signup vs login picked client-side)
        signup_url  = f"{site_url}/app/signup-new.html?invite={token}"
        decline_url = f"{site_url}/api/invitations/{token}/decline"
        login_url   = f"{site_url}/app/index.html?invite={token}"

        # Render + send the email FIRST so we can detect bounces before inserting
        # status='pending' (we'd flip to 'bounced' below for any row inserted
        # this iteration if SMTP raises).
        subject, body_html = _invite_render_email(
            email, first_name, target_venues_info, message,
            signup_url, decline_url, login_url, is_existing,
        )
        bounce_reason = None
        if smtp_email and smtp_password:
            try:
                msg = _MM('alternative')
                msg['Subject'] = subject
                msg['From'] = _fa((from_name, smtp_email)) if from_name else smtp_email
                msg['To'] = email
                msg['X-Mailer'] = 'GigsFill'
                msg.attach(_MT(body_html, 'html'))
                _smtp_send(smtp_server, smtp_port, smtp_email, smtp_password, msg)
            except (_smtp.SMTPRecipientsRefused, _smtp.SMTPDataError) as _bounce_e:
                bounce_reason = f"SMTP refused: {str(_bounce_e)[:240]}"
                _inv_logger.warning(f"Invite bounce for {email}: {bounce_reason}")
            except Exception as _send_e:
                # Transient (connection / auth) error — record but treat as
                # "sent" so the admin can retry rather than telling the artist
                # bounce. The audit log catches the warning.
                _inv_logger.warning(f"Invite send transient error for {email}: {_send_e}")

        # Insert one row per (email, venue) — all share token + invite_group_id
        row_status = "bounced" if bounce_reason else ("signed_up" if is_existing else "pending")
        for v in target_venues_info:
            db.execute(text("""
                INSERT INTO artist_invitations
                    (venue_id, venue_name, invited_email, invited_by_user_id, inviter_name,
                     message, status, sent_at, signed_up_at, signed_up_user_id,
                     token, token_expires_at, invite_group_id, bounce_reason)
                VALUES
                    (:vid, :vname, :email, :uid, :iname,
                     :msg, :status, CURRENT_TIMESTAMP, :signed_up_at, :signed_up_uid,
                     :token, :expires, :group, :bounce)
            """), {
                "vid": v["id"],
                "vname": v["venue_name"],
                "email": email,
                "uid": user.id,
                "iname": inviter_full,
                "msg": message or None,
                "status": row_status,
                "signed_up_at": (utcnow_naive().isoformat() if is_existing else None),
                "signed_up_uid": existing_uid,
                "token": token,
                "expires": expires_at,
                "group": invite_group_id,
                "bounce": bounce_reason,
            })

        # Commit AFTER each email's rows are inserted so a crash mid-batch
        # doesn't lose work that's already been completed (and emails that have
        # already been sent). Without this, an exception on email N would roll
        # back rows 1..N-1 even though those emails were successfully sent.
        try:
            db.commit()
        except Exception as _ce:
            _inv_logger.warning(f"Invite commit failed for {email}: {_ce}")
            db.rollback()

        if bounce_reason:
            bounced.append({"email": email, "reason": bounce_reason})
        else:
            sent.append(email)

    # Final no-op commit covers any non-row state (audit log etc.).
    db.commit()

    return {
        "ok": True,
        "sent_count": len(sent),
        "bounced_count": len(bounced),
        "skipped_already_pending_count": len(skipped_already_pending),
        "invalid_count": len(invalid_emails),
        "venues": len(venue_ids),
        "bounced": bounced,
        "skipped_already_pending": skipped_already_pending,
        "invalid": invalid_emails,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Token-based endpoints for artist invitations (Part 10p)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/api/artist-invitations/by-token/{token}")
def get_artist_invitation_by_token(token: str, db=Depends(get_db)):
    """Public — fetches an artist invitation by its single-use token. Returns
    the inviter's name + the list of venues this token covers + whether the
    invited email is already a GigsFill user (so the signup page can redirect
    to login instead of forcing duplicate-account creation).

    Used by:
      - signup-new.html when arriving via ?invite=<token>
      - index.html (login) when redirected from signup as existing user
      - the post-signup / post-login "Request Preferred Status" popup
    """
    if not token or len(token) < 16:
        raise HTTPException(404, "Invitation not found")

    # JOIN venues live for the current venue_name — the stored
    # `ai.venue_name` snapshot goes stale if the venue renames after
    # inviting the artist but before the artist accepts.
    rows = db.execute(text("""
        SELECT ai.id, ai.venue_id,
               COALESCE(v.venue_name, ai.venue_name) AS venue_name,
               ai.invited_email, ai.invited_by_user_id,
               ai.inviter_name, ai.message, ai.status, ai.token_expires_at, ai.invite_group_id,
               ai.signed_up_user_id, ai.preferred_requested_at
        FROM artist_invitations ai
        LEFT JOIN venues v ON v.id = ai.venue_id
        WHERE ai.token = :t
        ORDER BY ai.id ASC
    """), {"t": token}).mappings().all()
    rows = [dict(r) for r in rows]
    if not rows:
        raise HTTPException(404, "Invitation not found")

    # All rows share the same invitee email, token, and inviter — pick from first
    first = rows[0]

    # Expiry check (single source of truth on the row)
    exp = first.get("token_expires_at")
    if exp:
        try:
            from datetime import datetime as _dt
            _exp = _dt.fromisoformat(str(exp).replace("Z", "")) if isinstance(exp, str) else exp
            if _exp and _exp < _dt.utcnow():
                raise HTTPException(410, "This invitation has expired")
        except HTTPException:
            raise
        except Exception:
            pass

    # If invitee already has a GigsFill account, surface that so the client redirects
    # to login. We also tolerate the case where they signed up via the link AFTER the
    # invite — `signed_up_user_id` may have been set on intake or set later.
    invitee_user_id = first.get("signed_up_user_id")
    if not invitee_user_id:
        _u = db.execute(text("SELECT id FROM users WHERE LOWER(email) = LOWER(:e)"),
                        {"e": first["invited_email"]}).mappings().first()
        if _u:
            invitee_user_id = _u["id"]

    # For each venue: is the invitee already preferred there? (skip-checkbox UI)
    venue_info = []
    for r in rows:
        vid = r["venue_id"]
        # Find the artist record for this user (multi-user-aware)
        artist_id = None
        if invitee_user_id:
            _a = db.execute(text("""
                SELECT DISTINCT a.id FROM artists a
                LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
                WHERE a.user_id = :uid OR eu.user_id = :uid
                LIMIT 1
            """), {"uid": invitee_user_id}).mappings().first()
            if _a:
                artist_id = _a["id"]
        pref_status = None
        if artist_id:
            _p = db.execute(text(
                "SELECT status FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid"
            ), {"vid": vid, "aid": artist_id}).mappings().first()
            if _p:
                pref_status = _p["status"]
        venue_info.append({
            "venue_id": vid,
            "venue_name": r["venue_name"],
            "row_status": r["status"],
            "preferred_status": pref_status,  # None | 'approved' | 'pending' | 'denied' | 'revoked'
            "already_preferred": (pref_status == "approved"),
        })

    return {
        "token": token,
        "invited_email": first["invited_email"],
        "inviter_name": first.get("inviter_name") or "A venue",
        "message": first.get("message") or "",
        "invite_group_id": first.get("invite_group_id"),
        "is_existing_user": invitee_user_id is not None,
        "already_requested": bool(first.get("preferred_requested_at")),
        "venues": venue_info,
    }


@router.post("/api/artist-invitations/{token}/accept-preferred")
def accept_preferred_via_token(token: str, data: dict = Body(default={}), user=Depends(get_current_user), db=Depends(get_db)):
    """Authenticated. Batch-creates preferred_artists requests for every venue
    on this token that the logged-in artist isn't already preferred at. Marks
    all sibling rows preferred_requested. The current user MUST be either the
    invited_email account OR a multi-user member of an artist linked to that
    email — otherwise this is rejected.

    Body: { venue_ids: [int, ...] }  → only these venues get requested (defaults
    to "all eligible venues on this token" when omitted or empty).
    """
    from backend.services.notification_service import create_notification

    # JOIN venues live so stale ai.venue_name after a rename doesn't
    # leak into notifications/emails sent from this endpoint.
    rows = db.execute(text("""
        SELECT ai.id, ai.venue_id,
               COALESCE(v.venue_name, ai.venue_name) AS venue_name,
               ai.invited_email, ai.signed_up_user_id, ai.invite_group_id,
               ai.preferred_requested_at, ai.status, ai.token_expires_at
        FROM artist_invitations ai
        LEFT JOIN venues v ON v.id = ai.venue_id
        WHERE ai.token = :t
    """), {"t": token}).mappings().all()
    rows = [dict(r) for r in rows]
    if not rows:
        raise HTTPException(404, "Invitation not found")

    # The logged-in user must own/have-access-to an artist whose email matches the invitee.
    # We accept either case: a) user.email matches invited_email, or b) the artist
    # they own has booking_contact / user_id that maps to the invited email.
    invited_email_norm = (rows[0]["invited_email"] or "").lower().strip()
    if (user.email or "").lower().strip() != invited_email_norm:
        raise HTTPException(403, "This invitation belongs to a different account")

    # Find this user's artist (multi-user-aware)
    artist = db.execute(text("""
        SELECT DISTINCT a.id, a.name FROM artists a
        LEFT JOIN entity_users eu ON eu.entity_type = 'artist' AND eu.entity_id = a.id AND eu.user_id = :uid
        WHERE a.user_id = :uid OR eu.user_id = :uid
        LIMIT 1
    """), {"uid": user.id}).mappings().first()
    if not artist:
        raise HTTPException(400, "No artist profile found for your account")
    artist_id = artist["id"]
    artist_name = artist["name"]

    requested_vids = data.get("venue_ids") or []
    try:
        requested_vids = [int(v) for v in requested_vids]
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid venue_ids")
    requested_set = set(requested_vids) if requested_vids else None

    created, skipped_already_preferred, skipped_existing_request = [], [], []
    for r in rows:
        vid = int(r["venue_id"])
        if requested_set is not None and vid not in requested_set:
            continue

        # Skip if already in preferred_artists in any state
        existing = db.execute(text(
            "SELECT status FROM preferred_artists WHERE venue_id=:vid AND artist_id=:aid"
        ), {"vid": vid, "aid": artist_id}).mappings().first()
        if existing:
            if existing["status"] == "approved":
                skipped_already_preferred.append(r["venue_name"])
            else:
                skipped_existing_request.append({"venue_name": r["venue_name"], "status": existing["status"]})
            continue

        # Insert pending request — mirrors the standalone request endpoint
        db.execute(text(
            "INSERT INTO preferred_artists (venue_id, artist_id, status) VALUES (:vid, :aid, 'pending')"
        ), {"vid": vid, "aid": artist_id})

        # Notify the venue owner + entity users
        venue_info = db.execute(text(
            "SELECT user_id, venue_name FROM venues WHERE id = :vid"
        ), {"vid": vid}).mappings().first()
        if venue_info:
            try:
                create_notification(
                    db,
                    user_id=venue_info["user_id"],
                    notification_type="preferred_request",
                    title="New Preferred-Artist Request",
                    message=f"{artist_name} requested preferred status (via your invite).",
                    venue_id=vid,
                    artist_id=artist_id,
                )
            except Exception:
                pass

        # Stamp this invite row
        db.execute(text("""
            UPDATE artist_invitations
            SET status = 'preferred_requested',
                preferred_requested_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {"id": r["id"]})
        created.append(r["venue_name"])

    db.commit()
    return {
        "ok": True,
        "requested": created,
        "skipped_already_preferred": skipped_already_preferred,
        "skipped_existing_request": skipped_existing_request,
    }


@router.post("/api/artist-invitations/{token}/dismiss")
def dismiss_token_popup(token: str, user=Depends(get_current_user), db=Depends(get_db)):
    """Authenticated. Marks all rows for this token preferred_requested even if
    the artist chose to skip — so the popup doesn't re-fire next login. Doesn't
    create any preferred requests; just records dismissal. Auth check matches
    accept-preferred above.
    """
    rows = db.execute(text(
        "SELECT id, invited_email FROM artist_invitations WHERE token = :t"
    ), {"t": token}).mappings().all()
    if not rows:
        raise HTTPException(404, "Invitation not found")
    if (user.email or "").lower().strip() != (rows[0]["invited_email"] or "").lower().strip():
        raise HTTPException(403, "This invitation belongs to a different account")
    db.execute(text(
        "UPDATE artist_invitations SET preferred_requested_at = CURRENT_TIMESTAMP "
        "WHERE token = :t AND preferred_requested_at IS NULL"
    ), {"t": token})
    db.commit()
    return {"ok": True}


@router.get("/api/me/pending-artist-invite")
def get_my_pending_artist_invite(user=Depends(get_current_user), db=Depends(get_db)):
    """Returns the oldest unconsumed artist-invitation token for the current
    user, so the artist dashboard can fire the 'Request Preferred Status' popup
    on first load. Matches on email (case-insensitive). Returns null when there
    is nothing pending.

    'Unconsumed' = preferred_requested_at IS NULL AND status NOT IN
    ('declined','expired') AND token_expires_at > now.
    """
    row = db.execute(text("""
        SELECT token, invite_group_id
        FROM artist_invitations
        WHERE LOWER(invited_email) = LOWER(:e)
          AND preferred_requested_at IS NULL
          AND status NOT IN ('declined','expired')
          AND (token_expires_at IS NULL OR token_expires_at > CURRENT_TIMESTAMP)
        ORDER BY sent_at ASC
        LIMIT 1
    """), {"e": user.email}).mappings().first()
    if not row:
        return {"pending": False}
    return {"pending": True, "token": row["token"], "invite_group_id": row["invite_group_id"]}


# ─────────────────────────────────────────────────────────────────────────────
# Part 10p Phase 2: Decline link, resend, global "My Invites" listing
# ─────────────────────────────────────────────────────────────────────────────

from fastapi.responses import HTMLResponse as _HTMLResp


@router.get("/api/invitations/{token}/decline", response_class=_HTMLResp)
def decline_artist_invitation_confirm(token: str, db=Depends(get_db)):
    """Public — GET shows a confirmation page with a button that POSTs the
    actual decline. Email-link prefetchers and antivirus scanners trigger GETs
    automatically, so a state-changing GET was being silently fired without
    the invitee ever clicking. Now the GET is a no-op render and the POST does
    the work."""
    valid = False
    if token and len(token) >= 16:
        row = db.execute(text("SELECT id FROM artist_invitations WHERE token = :t LIMIT 1"),
                         {"t": token}).first()
        valid = row is not None
    if not valid:
        return _HTMLResp(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Invitation - GigsFill</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{margin:0;padding:0;background:#0a0a14;color:#e5e7eb;font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}}
.card{{max-width:480px;margin:80px auto;background:#1a1a2e;border:1px solid rgba(124,107,255,0.3);border-radius:10px;padding:32px 36px;text-align:center;}}
h1{{margin:0 0 12px;font-size:22px;color:#06b6d4;}}p{{color:#d1d5db;}}</style>
</head><body><div class="card"><h1>Invitation not found.</h1><p>The invitation link is no longer active.</p></div></body></html>""")
    # Render a tiny page with a single button that POSTs to confirm. JS in the
    # button does the POST so prefetchers (which only trigger GET) can never
    # auto-decline an invitation just by being in the user's inbox.
    return _HTMLResp(f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Decline Invitation - GigsFill</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{margin:0;padding:0;background:#0a0a14;color:#e5e7eb;font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}}
.card{{max-width:480px;margin:80px auto;background:#1a1a2e;border:1px solid rgba(124,107,255,0.3);border-radius:10px;padding:32px 36px;text-align:center;}}
h1{{margin:0 0 12px;font-size:22px;color:#fff;}}p{{color:#d1d5db;}}
.btn{{display:inline-block;margin:24px 8px 0;padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:600;font-size:13px;cursor:pointer;border:none;}}
.btn-primary{{background:#ef4444;color:#fff;}}.btn-ghost{{background:transparent;border:1px solid rgba(255,255,255,0.2);color:#d1d5db;}}
#done{{display:none;color:#86efac;}}
</style></head>
<body><div class="card">
<h1 id="ask">Decline this invitation?</h1>
<p id="askMsg">If you decline, the venue won't be able to invite you again with this link. You can still create a GigsFill account anytime at <a href="https://gigsfill.com" style="color:#06b6d4;">gigsfill.com</a>.</p>
<div id="askBtns">
  <button class="btn btn-ghost" onclick="window.location='https://gigsfill.com'">Cancel</button>
  <button class="btn btn-primary" id="declineBtn" onclick="doDecline()">Yes, Decline</button>
</div>
<h1 id="done" style="display:none;">Got it.</h1>
<p id="doneMsg" style="display:none;">We won't bother you about this invitation again.</p>
</div>
<script>
function doDecline() {{
  var btn = document.getElementById('declineBtn');
  btn.disabled = true;
  btn.textContent = 'Declining…';
  fetch('/api/invitations/{token}/decline', {{ method: 'POST' }})
    .then(function(){{
      document.getElementById('ask').style.display='none';
      document.getElementById('askMsg').style.display='none';
      document.getElementById('askBtns').style.display='none';
      document.getElementById('done').style.display='';
      document.getElementById('doneMsg').style.display='';
    }})
    .catch(function(){{ btn.disabled=false; btn.textContent='Yes, Decline'; alert('Decline failed — please try again.'); }});
}}
</script></body></html>""")


@router.post("/api/invitations/{token}/decline")
def decline_artist_invitation(token: str, db=Depends(get_db)):
    """Public decline endpoint. Serves TWO flows because of a route
    collision (2026-07-26 fix) — FastAPI matches whichever router is
    included first, and this one wins over entity_users.py's identical
    path. We dispatch:

    1. entity_invitations (team-member invite) — mark declined + wipe
       any pending in-app notifications for that user, then return the
       inviter/entity data the invited_user_declined.html frontend needs
       to render the confirmation card.
    2. artist_invitations (preferred-artist invite) — original behavior.

    Both flows return `ok:true` on success; the entity flow also returns
    inviter_first_name / inviter_last_name / entity_name so the
    frontend's showDeclineCard() renders the person's real name instead
    of falling back to "the inviter" / "Unknown".
    """
    if not token or len(token) < 16:
        return {"ok": True, "valid": False}

    # ── Entity invitation (team-member) path ─────────────────────────
    ent = db.execute(text("""
        SELECT id, entity_type, entity_id, entity_name, invited_email,
               inviter_first_name, inviter_last_name, status
          FROM entity_invitations WHERE token = :t
    """), {"t": token}).mappings().first()
    if ent:
        if ent['status'] == 'pending':
            db.execute(text("""
                UPDATE entity_invitations
                   SET status = 'declined', responded_at = :now
                 WHERE token = :t
            """), {"t": token, "now": utcnow_naive()})
            # Clear any lingering in-app notification for this user
            try:
                uid = db.execute(text(
                    "SELECT id FROM users WHERE LOWER(email) = LOWER(:e)"
                ), {"e": ent['invited_email']}).scalar()
                if uid:
                    db.execute(text("""
                        DELETE FROM notifications
                         WHERE user_id = :uid
                           AND notification_type = 'entity_invite'
                           AND entity_type = :etype
                           AND entity_id = :eid
                    """), {"uid": uid, "etype": ent['entity_type'],
                            "eid": ent['entity_id']})
            except Exception:
                pass
            db.commit()
        return {
            "ok": True,
            "valid": True,
            "inviter_first_name": ent['inviter_first_name'],
            "inviter_last_name": ent['inviter_last_name'],
            "entity_name": ent['entity_name'],
            "entity_type": ent['entity_type'],
            "entity_id": ent['entity_id'],
        }

    # ── Artist invitation (preferred-artist) path — original logic ───
    rows = db.execute(text(
        "SELECT id, status FROM artist_invitations WHERE token = :t"
    ), {"t": token}).mappings().all()
    if rows:
        db.execute(text("""
            UPDATE artist_invitations
            SET status = 'declined',
                declined_at = CURRENT_TIMESTAMP
            WHERE token = :t
              AND status NOT IN ('signed_up','preferred_requested','preferred_approved','preferred_denied','declined','bounced','expired')
        """), {"t": token})
        db.commit()
        return {"ok": True, "valid": True}
    return {"ok": True, "valid": False}




@router.get("/api/me/invitations")
def get_my_invitations(user=Depends(get_current_user), db=Depends(get_db)):
    """Aggregated list of artist invitations sent by the current user across
    every venue they control. Used by the global 'My Invites' page.

    Returns one row per (email × venue) invite — the page groups by email/token
    on the client side so a single multi-venue invite shows as one consolidated
    card with per-venue status pills.
    """
    rows = db.execute(text("""
        SELECT ai.id, ai.venue_id,
               COALESCE(v.venue_name, ai.venue_name) AS venue_name,
               ai.invited_email, ai.inviter_name,
               ai.message, ai.status, ai.sent_at, ai.signed_up_at,
               ai.token, ai.invite_group_id, ai.bounce_reason, ai.declined_at,
               ai.preferred_requested_at, ai.last_resent_at, ai.resent_count
        FROM artist_invitations ai
        LEFT JOIN venues v ON v.id = ai.venue_id
        WHERE ai.invited_by_user_id = :uid
        ORDER BY ai.sent_at DESC, ai.id DESC
    """), {"uid": user.id}).mappings().all()
    return [dict(r) for r in rows]


@router.post("/api/artist-invitations/{token}/resend")
def resend_invitation(token: str, user=Depends(get_current_user), db=Depends(get_db)):
    """Re-send the invite email for a given token. 24-hour cooldown per
    invitation enforced via `last_resent_at`. Caller must be the original
    inviter (invited_by_user_id == user.id) to prevent abuse."""
    from datetime import datetime as _dt, timedelta as _td
    from email.mime.text import MIMEText as _MT
    from email.mime.multipart import MIMEMultipart as _MM
    from email.utils import formataddr as _fa
    from backend.email_service import _smtp_send
    import smtplib as _smtp

    rows = db.execute(text("""
        SELECT ai.id, ai.venue_id,
               COALESCE(v.venue_name, ai.venue_name) AS venue_name,
               ai.invited_email, ai.invited_by_user_id, ai.inviter_name,
               ai.message, ai.status, ai.sent_at, ai.token_expires_at,
               ai.last_resent_at, ai.resent_count, ai.signed_up_user_id
        FROM artist_invitations ai
        LEFT JOIN venues v ON v.id = ai.venue_id
        WHERE ai.token = :t
    """), {"t": token}).mappings().all()
    rows = [dict(r) for r in rows]
    if not rows:
        raise HTTPException(404, "Invitation not found")
    if any(int(r["invited_by_user_id"]) != int(user.id) for r in rows):
        raise HTTPException(403, "Not your invitation")

    # 24h cooldown since whichever is most recent: original send OR a previous resend.
    last_action = None
    for r in rows:
        for ts in (r.get("last_resent_at"), r.get("sent_at")):
            if not ts:
                continue
            try:
                _t = _dt.fromisoformat(str(ts).replace("Z", "")) if isinstance(ts, str) else ts
                if not last_action or _t > last_action:
                    last_action = _t
            except Exception:
                pass
    if last_action and (_dt.utcnow() - last_action) < _td(hours=24):
        wait_minutes = int(((_td(hours=24) - (_dt.utcnow() - last_action)).total_seconds()) / 60)
        raise HTTPException(429, f"Please wait about {wait_minutes // 60}h {wait_minutes % 60}m before resending this invitation.")

    # Compose the email — same renderer as the initial send. We need the venue
    # info aggregated and whether they're an existing user.
    venues_info = [{"venue_id": r["venue_id"], "venue_name": r["venue_name"]} for r in rows]
    invited_email = rows[0]["invited_email"]
    inviter_full = rows[0].get("inviter_name") or ""
    first_name = (getattr(user, "first_name", None) or inviter_full.split()[0] if inviter_full else "") or "A venue"
    is_existing = rows[0].get("signed_up_user_id") is not None

    _smtp_rows = db.execute(text(
        "SELECT setting_key, setting_value FROM platform_settings WHERE setting_key IN "
        "('platform_email','platform_email_password','platform_smtp_server','platform_smtp_port','platform_email_from_name')"
    )).fetchall()
    _s = {r[0]: r[1] for r in _smtp_rows}
    smtp_email    = _s.get('platform_email') or ""
    smtp_password = _s.get('platform_email_password') or ""
    smtp_server   = _s.get('platform_smtp_server') or "smtp.gmail.com"
    smtp_port     = int(_s.get('platform_smtp_port') or 587)
    from_name     = _s.get('platform_email_from_name') or "GigsFill"
    site_url = db.execute(text("SELECT setting_value FROM platform_settings WHERE setting_key='site_url'")).scalar() or "https://gigsfill.com"

    signup_url  = f"{site_url}/app/signup-new.html?invite={token}"
    decline_url = f"{site_url}/api/invitations/{token}/decline"
    login_url   = f"{site_url}/app/index.html?invite={token}"

    subject, body_html = _invite_render_email(
        invited_email, first_name, venues_info, rows[0].get("message"),
        signup_url, decline_url, login_url, is_existing,
    )

    bounce_reason = None
    if smtp_email and smtp_password:
        try:
            msg = _MM('alternative')
            msg['Subject'] = subject
            msg['From'] = _fa((from_name, smtp_email)) if from_name else smtp_email
            msg['To'] = invited_email
            msg['X-Mailer'] = 'GigsFill'
            msg.attach(_MT(body_html, 'html'))
            _smtp_send(smtp_server, smtp_port, smtp_email, smtp_password, msg)
        except (_smtp.SMTPRecipientsRefused, _smtp.SMTPDataError) as _be:
            bounce_reason = f"SMTP refused: {str(_be)[:240]}"
        except Exception as _e:
            _inv_logger.warning(f"Resend transient error for {invited_email}: {_e}")

    # Update all sibling rows: bump resent_count, set last_resent_at, set bounce
    new_status_clause = ""
    if bounce_reason:
        new_status_clause = ", status = 'bounced', bounce_reason = :bounce"
    db.execute(text(f"""
        UPDATE artist_invitations
        SET resent_count = COALESCE(resent_count, 0) + 1,
            last_resent_at = CURRENT_TIMESTAMP
            {new_status_clause}
        WHERE token = :t
    """), {"t": token, **({"bounce": bounce_reason} if bounce_reason else {})})
    db.commit()
    return {"ok": True, "bounced": bool(bounce_reason), "bounce_reason": bounce_reason}

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.models import Artist
from backend.us_cities import find_city
from sqlalchemy import text
import os, shutil

router = APIRouter()

# -----------------------------
# CREATE (WITH ARTIST TYPE)
# -----------------------------
@router.post("/api/artists")
def create_artist(data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    # Validate artist_type is provided
    artist_type = data.get("artist_type")
    if not artist_type:
        raise HTTPException(400, "Artist type is required")
    
    # Validate band_formats if Live Band
    band_formats = data.get("band_formats")
    styles = data.get("styles")
    if artist_type == "Live Band":
        if not band_formats:
            raise HTTPException(400, "Lineup selection required for Live Band artists.")
        if not styles:
            raise HTTPException(400, "At least one style is required for Live Band artists.")
    # Jul 1 2026: MC-type artists get an equipment opt-in. Coerce the
    # signup payload to a bool. Non-MC types can send it too — it's
    # stored but doesn't gate matching unless a venue sets
    # requires_equipment on a slot.
    _has_equip_in = data.get("has_own_equipment")
    has_own_equipment = bool(_has_equip_in) if _has_equip_in is not None else False
    
    # v91: Geocode city to get coordinates
    city = data.get("city")
    state = data.get("state")
    latitude = None
    longitude = None
    if city and state:
        city_data = find_city(city, state)
        if city_data:
            latitude = city_data["lat"]
            longitude = city_data["lon"]
        else:
            raise HTTPException(400, "This city is either misspelled or too small for our system. Please enter the closest big city to yours.")
    
    # Check for duplicate name + city + state
    artist_name = (data.get("name") or "").strip()
    if artist_name and city and state:
        existing = db.execute(text("""
            SELECT id FROM artists
            WHERE LOWER(name) = LOWER(:n) AND LOWER(city) = LOWER(:c) AND UPPER(state) = UPPER(:s)
        """), {"n": artist_name, "c": city, "s": state}).first()
        if existing:
            raise HTTPException(409, f"An artist named '{artist_name}' already exists in {city}, {state}. If this is your artist, request access from your profile page.")

    # v81: Include city, state, bio, booking_contact
    # v91: Include latitude, longitude
    # Server-side duplicate guard: same name + city + state = duplicate
    artist_name = data.get("name", "").strip()
    dup = db.execute(text("""
        SELECT a.id, a.name, a.city, a.state
        FROM artists a
        WHERE LOWER(a.name) = LOWER(:n) AND LOWER(a.city) = LOWER(:c) AND UPPER(a.state) = UPPER(:s)
        LIMIT 1
    """), {"n": artist_name, "c": city or "", "s": state or ""}).mappings().first()
    if dup:
        raise HTTPException(409, f"An artist named '{dup['name']}' already exists in {dup['city']}, {dup['state']}. If this is your artist, request access from the profile owner.")

    artist = Artist(
        user_id=user.id,
        name=artist_name,
        artist_type=artist_type,
        band_formats=band_formats,
        styles=styles,
        has_own_equipment=has_own_equipment,
        city=city,
        state=state,
        latitude=latitude,
        longitude=longitude,
        bio=data.get("bio"),
        booking_contact=data.get("booking_contact")
    )
    db.add(artist)
    db.commit()
    db.refresh(artist)
    
    # Add creator as owner in entity_users
    db.execute(
        text("""
            INSERT INTO entity_users (entity_type, entity_id, user_id, role, added_by_user_id, created_at)
            VALUES ('artist', :entity_id, :user_id, 'owner', :user_id, CURRENT_TIMESTAMP)
        """),
        {"entity_id": artist.id, "user_id": user.id}
    )
    db.commit()
    
    return {"id": artist.id}

# -----------------------------
# LIST (PROFILE PAGE) - MOVED TO me.py (supports display_order and entity_users)
# -----------------------------
# @router.get("/api/my/artists")
# def my_artists(user=Depends(get_current_user), db=Depends(get_db)):
#     rows = db.execute(
#         text("SELECT id, name FROM artists WHERE user_id=:uid ORDER BY id DESC"),
#         {"uid": user.id}
#     ).mappings().all()
#     return rows

# v90: SEARCH ALL ARTISTS (for venue search)
@router.get("/api/artists/search")
def search_artists(db=Depends(get_db)):
    """Search all artists - returns all artist data for venue filtering"""
    try:
        # Jul 2026: exclude tombstoned artists — historical joins still see
        # them under the "[Deleted]" name, but they don't show up in venue
        # search / preferred-artist add flows since they can't be booked.
        rows = db.execute(
            text("""
                SELECT
                    id,
                    name,
                    COALESCE(city, '') as city,
                    COALESCE(state, '') as state,
                    latitude,
                    longitude,
                    COALESCE(artist_type, '') as artist_type,
                    COALESCE(band_formats, '') as band_formats,
                    COALESCE(styles, '') as styles
                FROM artists
                WHERE deleted_at IS NULL
                ORDER BY name ASC
            """)
        ).mappings().all()
        return rows
    except Exception as e:
        raise HTTPException(500, "Database error. Please try again.")

# -----------------------------
# GET SINGLE (EDIT PAGE)
# -----------------------------
@router.get("/artists/{artist_id}")
def get_artist(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    # v97: Check ownership OR entity_users access
    row = db.execute(
        text("""
            SELECT
                a.id,
                a.user_id,
                a.name,
                a.city,
                a.state,
                a.bio,
                a.artist_type,
                a.band_formats,
                a.styles,
                a.booking_contact,
                a.spotify_url,
                a.instagram_url,
                a.facebook_url,
                a.youtube_url,
                a.twitter_url,
                a.tiktok_url,
                a.website_url,
                a.social_order,
                a.latitude,
                a.longitude,
                COALESCE(a.has_own_equipment, 0) as has_own_equipment
            FROM artists a
            WHERE a.id = :id
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
        {"id": artist_id, "uid": user.id}
    ).mappings().first()

    if not row:
        raise HTTPException(404)

    return dict(row)

# -----------------------------
# ACCESS CHECK (for frontend — returns 200 if caller owns this artist, 403 if not)
# -----------------------------
@router.get("/api/artists/{artist_id}/access-check")
def check_artist_access(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Returns 200 if the logged-in user owns (or has entity_users access to) this artist.
    Returns 403 otherwise. Used by the frontend to gate artist-specific pages."""
    from sqlalchemy import text as _t
    row = db.execute(
        _t("""
            SELECT 1 FROM artists a
            WHERE a.id = :id
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
        {"id": artist_id, "uid": user.id}
    ).first()
    if not row:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"ok": True}


# GET SINGLE - PUBLIC (for viewing profiles)
# -----------------------------
@router.get("/api/artists/{artist_id}")
def get_artist_public(artist_id: int, db=Depends(get_db)):
    """Public endpoint to view any artist profile.

    Audit fix (Jun 2026): `user_id` was exposed in the response,
    letting an attacker enumerate artist→user mappings from
    public traffic. Owner-only client code uses /api/artists/{id}
    via authed routes (line 134) which still returns user_id —
    only the public endpoint drops it.
    """
    row = db.execute(
        text("""
            SELECT
                id,
                name,
                city,
                state,
                bio,
                artist_type,
                band_formats,
                styles,
                booking_contact,
                spotify_url,
                instagram_url,
                facebook_url,
                youtube_url,
                twitter_url,
                tiktok_url,
                website_url,
                social_order
            FROM artists
            WHERE id=:id
              AND deleted_at IS NULL
        """),
        {"id": artist_id}
    ).mappings().first()

    if not row:
        raise HTTPException(404)

    return dict(row)

# -----------------------------
# UPDATE (AUTOSAVE)
# -----------------------------
@router.put("/artists/{artist_id}")
def update_artist(artist_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    # v97: Check ownership OR entity_users access
    exists = db.execute(
        text("""
            SELECT 1 FROM artists a
            WHERE a.id = :id 
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
        {"id": artist_id, "uid": user.id}
    ).first()

    if not exists:
        raise HTTPException(403)

    # Grab prior name so we can detect a rename after the UPDATE
    # and invalidate cached flyer thumbnails (they bake in the old
    # artist name via canvas-rendered PNGs). Jul 20 2026 addition.
    _prior_artist_name = db.execute(
        text("SELECT name FROM artists WHERE id = :id"), {"id": artist_id}
    ).scalar()

    # Validate city if being updated
    new_city = data.get("city")
    new_state = data.get("state")
    if new_city is not None:
        # Get current state if not in update
        if new_state is None:
            row = db.execute(text("SELECT state FROM artists WHERE id = :id"), {"id": artist_id}).first()
            new_state = row[0] if row else None
        if new_city and new_state:
            city_data = find_city(new_city, new_state)
            if not city_data:
                raise HTTPException(400, "This city is either misspelled or too small for our system. Please enter the closest big city to yours.")

    # Jul 1 2026: has_own_equipment normalized to int (SQLite stores
    # booleans as 0/1). None (field not sent) leaves DB value untouched
    # via COALESCE.
    _hoe_in = data.get("has_own_equipment")
    _hoe = None if _hoe_in is None else (1 if bool(_hoe_in) else 0)
    db.execute(
        text("""
            UPDATE artists SET
                name = COALESCE(:name, name),
                city = COALESCE(:city, city),
                state = COALESCE(:state, state),
                bio = COALESCE(:bio, bio),
                artist_type = COALESCE(:artist_type, artist_type),
                band_formats = COALESCE(:band_formats, band_formats),
                styles = COALESCE(:styles, styles),
                has_own_equipment = COALESCE(:has_own_equipment, has_own_equipment),
                booking_contact = COALESCE(:booking_contact, booking_contact),
                spotify_url = COALESCE(:spotify_url, spotify_url),
                instagram_url = COALESCE(:instagram_url, instagram_url),
                facebook_url = COALESCE(:facebook_url, facebook_url),
                youtube_url = COALESCE(:youtube_url, youtube_url),
                twitter_url = COALESCE(:twitter_url, twitter_url),
                tiktok_url = COALESCE(:tiktok_url, tiktok_url),
                website_url = COALESCE(:website_url, website_url),
                social_order = COALESCE(:social_order, social_order)
            WHERE id = :id

        """),
        {
            "id": artist_id,
            "name": data.get("name"),
            "city": data.get("city"),
            "state": data.get("state"),
            "bio": data.get("bio"),
            "artist_type": data.get("artist_type"),
            "band_formats": data.get("band_formats"),
            "styles": data.get("styles"),
            "has_own_equipment": _hoe,
            "booking_contact": data.get("booking_contact"),
            "spotify_url": data.get("spotify_url"),
            "instagram_url": data.get("instagram_url"),
            "facebook_url": data.get("facebook_url"),
            "youtube_url": data.get("youtube_url"),
            "twitter_url": data.get("twitter_url"),
            "tiktok_url": data.get("tiktok_url"),
            "website_url": data.get("website_url"),
            "social_order": data.get("social_order"),
        }
    )
    db.commit()

    # Invalidate cached flyer thumbnails on rename — every non-template
    # flyer where this artist is booked bakes the old artist name into
    # its rendered PNG thumbnail. Clearing forces the frontend to
    # regenerate from live canvas data on next open. Filename
    # (flyers.name) is left alone — self-heals on next re-save.
    _new_name = data.get("name")
    if _new_name and _new_name != _prior_artist_name:
        try:
            db.execute(
                text("UPDATE flyers SET thumbnail_data = NULL "
                     "WHERE artist_id = :aid AND is_template = 0"),
                {"aid": artist_id}
            )
            db.commit()
        except Exception:
            # Silent — the DB truth is what matters; a stale thumbnail
            # is a tiny cosmetic issue that self-heals on next save.
            pass

        # Jul 2026: auto-migrate vanity slug on rename if the current
        # slug matches the old-name auto-slug (user never customized).
        # Old slug parks as a 90-day redirect. See
        # maybe_update_slug_on_rename in backend/routes/vanity.py.
        try:
            from backend.routes.vanity import maybe_update_slug_on_rename
            maybe_update_slug_on_rename(
                db, "artist", artist_id, _prior_artist_name, _new_name
            )
        except Exception:
            pass

    return {"ok": True}

# Get artist's venues (relationships)
@router.get("/api/artists/{artist_id}/venues")
def get_artist_venues(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get all venues this artist has interacted with"""
    from backend.utils import check_artist_access
    check_artist_access(db, artist_id, user.id)

    # Jul 2026 refactor: collapsed the two separate queries (one for
    # single-slot bookings via gigs.artist_id, one for multi-slot via
    # gig_slots.artist_id) into a single slot-only query. Post-backfill
    # every gig has a `gig_slots` row so this covers both. Reduces the
    # DB round-trips from 2 to 1 and eliminates double-counting when the
    # caller merges the two lists.
    gigs_venues = db.execute(
        text("""
            SELECT DISTINCT
                v.id as venue_id,
                v.venue_name,
                v.address_line_1,
                v.address_line_2,
                v.city,
                v.state,
                COUNT(DISTINCT g.id) as gigs_count,
                MIN(g.date) as next_gig_date,
                'normal' as status
            FROM gig_slots gs
            JOIN gigs g ON gs.gig_id = g.id
            JOIN venues v ON g.venue_id = v.id
            WHERE gs.artist_id = :aid AND gs.status = 'booked'
            GROUP BY v.id
        """),
        {"aid": artist_id}
    ).mappings().all()

    # Legacy slot_venues query retired — same data as gigs_venues above.
    slot_venues = []
    
    # Get preferred status with override fields
    preferred = db.execute(
        text("""
            SELECT 
                venue_id,
                status,
                pay_dollars_override,
                pay_cents_override,
                frequency_days_override
            FROM preferred_artists
            WHERE artist_id = :aid
        """),
        {"aid": artist_id}
    ).mappings().all()
    
    # Get waitlisted gigs venues
    waitlist_venues = db.execute(
        text("""
            SELECT DISTINCT
                v.id as venue_id,
                v.venue_name,
                v.address_line_1,
                v.address_line_2,
                v.city,
                v.state,
                0 as gigs_count,
                g.date as next_gig_date,
                COALESCE(
                    (SELECT gs.start_time FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status = 'open' ORDER BY gs.start_time ASC LIMIT 1),
                    g.start_time
                ) as waitlist_gig_start,
                COALESCE(
                    (SELECT gs.end_time FROM gig_slots gs WHERE gs.gig_id = g.id AND gs.status = 'open' ORDER BY gs.start_time ASC LIMIT 1),
                    g.end_time
                ) as waitlist_gig_end,
                w.id as waitlist_id,
                -- Position + total scoped to active cancellation-waitlist
                -- rows only (Jun 2026). Hold-cycle history (source='hold',
                -- already-declined offers) and stale declined rows must
                -- not inflate the artist-facing "Position N of M" badge.
                -- Without the source + offer_declined filters, an artist
                -- who joined the cancellation waitlist after a hold ran
                -- saw "Position 1 of 2" — the 2 counted the consumed
                -- hold offer for the artist who booked.
                (SELECT COUNT(*) FROM gig_waitlist w2
                 WHERE w2.gig_id = w.gig_id AND w2.id <= w.id
                   AND (w2.offer_declined = 0 OR w2.offer_declined IS NULL)
                   AND (w2.source IS NULL OR w2.source = 'cancellation')) as waitlist_position,
                (SELECT COUNT(*) FROM gig_waitlist w3
                 WHERE w3.gig_id = w.gig_id
                   AND (w3.offer_declined = 0 OR w3.offer_declined IS NULL)
                   AND (w3.source IS NULL OR w3.source = 'cancellation')) as waitlist_total,
                w.gig_id as waitlist_gig_id
            FROM gig_waitlist w
            JOIN gigs g ON g.id = w.gig_id
            JOIN venues v ON v.id = g.venue_id
            WHERE w.artist_id = :aid
              AND (w.source IS NULL OR w.source = 'cancellation')
              AND (w.offer_declined = 0 OR w.offer_declined IS NULL)
              -- Audit fix (May 2026): include 'open' and 'cancelled_blast' so
              -- the artist's "Venues" tab keeps showing their position when a
              -- waitlist-trigger gig is re-listed (was being silently dropped).
              AND g.status IN ('booked', 'pending_contract', 'awaiting_venue_contract', 'pending_venue_approval', 'open', 'cancelled_blast')
              AND g.date >= date('now', '-1 day')
        """),
        {"aid": artist_id}
    ).mappings().all()

    # Merge all venue sources
    venue_dict = {}
    for v in gigs_venues:
        venue_dict[v['venue_id']] = dict(v)
    
    for v in slot_venues:
        vid = v['venue_id']
        if vid in venue_dict:
            # Merge counts (avoid double-counting)
            existing = venue_dict[vid]
            existing['gigs_count'] = existing['gigs_count'] + v['gigs_count']
            if v['next_gig_date'] and (not existing['next_gig_date'] or v['next_gig_date'] < existing['next_gig_date']):
                existing['next_gig_date'] = v['next_gig_date']
        else:
            venue_dict[vid] = dict(v)
    
    # Add waitlist venues
    for v in waitlist_venues:
        vid = v['venue_id']
        vd = dict(v)
        if vid not in venue_dict:
            vd['waitlist_gig_date'] = vd.get('next_gig_date')
            venue_dict[vid] = vd
        else:
            # Merge waitlist info into existing venue entry
            existing = venue_dict[vid]
            if not existing.get('waitlist_gig_id'):
                existing['waitlist_id'] = vd['waitlist_id']
                existing['waitlist_position'] = vd['waitlist_position']
                existing['waitlist_total'] = vd['waitlist_total']
                existing['waitlist_gig_id'] = vd['waitlist_gig_id']
                existing['waitlist_gig_date'] = vd['next_gig_date']
                existing['waitlist_gig_start'] = vd.get('waitlist_gig_start')
                existing['waitlist_gig_end'] = vd.get('waitlist_gig_end')

    for pref in preferred:
        vid = pref['venue_id']
        if vid in venue_dict:
            venue_dict[vid]['status'] = pref['status']
            venue_dict[vid]['preferred_status'] = pref['status']
        else:
            # Venue with preferred status but no gigs yet
            venue_info = db.execute(
                text("SELECT id as venue_id, venue_name, address_line_1, address_line_2, city, state FROM venues WHERE id = :vid"),
                {"vid": vid}
            ).mappings().first()
            if venue_info:
                venue_dict[vid] = dict(venue_info)
                venue_dict[vid]['status'] = pref['status']
                venue_dict[vid]['preferred_status'] = pref['status']
                venue_dict[vid]['gigs_count'] = 0
        
        # Add override/default pay and frequency info
        if vid in venue_dict:
            venue_dict[vid]['pay_dollars_override'] = pref.get('pay_dollars_override')
            venue_dict[vid]['pay_cents_override'] = pref.get('pay_cents_override')
            venue_dict[vid]['frequency_days_override'] = pref.get('frequency_days_override')
    
    # Fetch venue defaults for all venues with preferred status
    pref_venue_ids = [p['venue_id'] for p in preferred]
    if pref_venue_ids:
        # Parameterized IN clause — never interpolate values into SQL
        param_names = [f":vid_{i}" for i in range(len(pref_venue_ids))]
        params = {f"vid_{i}": int(vid) for i, vid in enumerate(pref_venue_ids)}
        venue_defaults = db.execute(
            text(f"SELECT id, default_pay_dollars, default_pay_cents, artist_frequency_days FROM venues WHERE id IN ({','.join(param_names)})"),
            params
        ).mappings().all()
        defaults_map = {v['id']: dict(v) for v in venue_defaults}
        for vid, vdata in venue_dict.items():
            if vid in defaults_map:
                d = defaults_map[vid]
                vdata['venue_default_pay_dollars'] = d.get('default_pay_dollars', 0) or 0
                vdata['venue_default_pay_cents'] = d.get('default_pay_cents', 0) or 0
                vdata['venue_default_freq_days'] = d.get('artist_frequency_days', 0) or 0

    # Fetch avg_rating, review_count, and this artist's existing review for all venues
    all_venue_ids = list(venue_dict.keys())
    if all_venue_ids:
        r_param_names = [f":rvid_{i}" for i in range(len(all_venue_ids))]
        r_params = {f"rvid_{i}": int(vid) for i, vid in enumerate(all_venue_ids)}
        try:
            rating_rows = db.execute(
                text(f"""SELECT venue_id, ROUND(AVG(rating),1) as avg_rating, COUNT(*) as review_count
                         FROM venue_reviews WHERE venue_id IN ({','.join(r_param_names)}) AND is_visible=1
                         GROUP BY venue_id"""),
                r_params
            ).mappings().all()
            ratings_map = {r['venue_id']: dict(r) for r in rating_rows}
        except Exception:
            ratings_map = {}

        # Fetch this artist's own general review for each venue
        try:
            my_review_rows = db.execute(
                text(f"""SELECT venue_id, rating, review_text
                         FROM venue_reviews
                         WHERE gig_id IS NULL AND artist_id = :aid
                           AND venue_id IN ({','.join(r_param_names)})"""),
                {"aid": artist_id, **r_params}
            ).mappings().all()
            my_review_map = {r['venue_id']: dict(r) for r in my_review_rows}
        except Exception:
            my_review_map = {}

        for vid, vdata in venue_dict.items():
            r = ratings_map.get(vid)
            vdata['avg_rating'] = r['avg_rating'] if r else None
            vdata['review_count'] = r['review_count'] if r else 0
            my_r = my_review_map.get(vid)
            vdata['my_review'] = my_r if my_r else None

    # Part 10k: ban detection — a venue that banned this artist should surface
    # under "Banned Venues" regardless of any gig history. Overrides whatever
    # status was set above. (Venues with gigs but no preferred row keep
    # status='normal', which the frontend treats as the Non-Preferred bucket.)
    if venue_dict:
        ban_param_names = [f":bvid_{i}" for i in range(len(venue_dict))]
        ban_params = {f"bvid_{i}": int(vid) for i, vid in enumerate(venue_dict.keys())}
        ban_params["aid"] = artist_id
        banned_venue_ids = {row[0] for row in db.execute(
            text(f"SELECT venue_id FROM venue_artist_bans WHERE artist_id = :aid AND venue_id IN ({','.join(ban_param_names)})"),
            ban_params
        ).all()}
        for vid in banned_venue_ids:
            if vid in venue_dict:
                venue_dict[vid]['status'] = 'banned'
                venue_dict[vid]['preferred_status'] = 'banned'

    return list(venue_dict.values())

@router.get("/api/artists/{artist_id}/venues/{venue_id}/gigs")
def get_artist_venue_gigs(artist_id: int, venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Get all booked gigs for an artist at a specific venue (including slot bookings)"""
    
    # Jul 2026 refactor: collapsed the two-query merge (single-slot via
    # gigs.artist_id + multi-slot via gig_slots.artist_id, then Python
    # seen_ids dedup) into one slot-only query. Post-backfill every gig
    # has ≥1 gig_slots row, so the slot query returns the same rows the
    # gigs.artist_id query used to, without the dedup dance.
    slot_gigs = db.execute(
        text("""
            SELECT DISTINCT
                g.id, g.date, gs.start_time, gs.end_time, gs.pay, g.notes,
                'booked' as status, gs.artist_id, a.name as artist_name,
                g.title, g.artist_type, g.band_formats, g.styles,
                COALESCE(g.is_multi_slot, 0) as is_multi_slot,
                v.venue_name, v.address_line_1, v.address_line_2, v.city, v.state,
                gs.deal_type, gs.door_pct, gs.guarantee_cents,
                gs.door_receipts_cents, gs.settled_pay_cents, gs.settled_at
            FROM gig_slots gs
            JOIN gigs g ON gs.gig_id = g.id
            LEFT JOIN artists a ON gs.artist_id = a.id
            LEFT JOIN venues v ON g.venue_id = v.id
            WHERE gs.artist_id = :artist_id
                AND g.venue_id = :venue_id
                AND gs.status = 'booked'
            ORDER BY g.date ASC
        """),
        {"artist_id": artist_id, "venue_id": venue_id}
    ).mappings().all()

    result = [dict(g) for g in slot_gigs]
    
    # Add effective_pay (venue override for this artist) to each gig.
    # Override only applies when the artist is actively approved at this venue —
    # revoked/denied rows still carry the override columns from before, but the
    # per-artist negotiated rate no longer applies, so we filter status='approved'.
    pref = db.execute(
        text("SELECT pay_dollars_override, pay_cents_override FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid AND status = 'approved'"),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()
    override_val = None
    if pref and pref.get("pay_dollars_override") is not None:
        override_val = float(pref["pay_dollars_override"]) + float(pref.get("pay_cents_override") or 0) / 100
    for g in result:
        pay = float(g.get("pay") or 0)
        if override_val is not None and override_val > pay:
            pay = override_val
        g["effective_pay"] = round(pay, 2)

    # Stamp pay_summary ONLY for door deals — the flat-pay summary
    # would just be "$<listed pay>" and would mask any venue override
    # (the frontend prefers pay_summary when present). Door deals
    # don't honor overrides (the guarantee is the contract), so the
    # door summary is always correct regardless of override state.
    try:
        from backend.routes.gigs import _enrich_pay_summary
        _door_only = [g for g in result
                      if str(g.get('deal_type') or '').lower() == 'door']
        if _door_only:
            _enrich_pay_summary(_door_only)
    except Exception:
        pass

    result.sort(key=lambda x: x.get('date', ''))
    return result

@router.get("/api/artists/{artist_id}/delete-preview")
def delete_artist_preview(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Return everything the frontend needs to render a fully-informed
    delete-artist modal WITHOUT actually deleting:

      - `is_owner`: True only if this user created the artist (delegates get False)
      - `name`, `deleted_at`, `other_users_count`
      - `upcoming_gigs`: [{gig_id, date, venue_name}, ...] the tombstone will cancel
      - `live_txns`: [{status, date, venue_name, ...}, ...] that would BLOCK deletion (409)

    Modal uses `is_owner=False` to grey out the Delete button and show a
    "only the owner can delete" hint. Non-empty `live_txns` blocks and shows
    the mid-flight items so the user can act on them (contact support / wait
    for the payout scheduler / etc.) instead of just seeing a bare 409.
    """
    from backend.utils import utcnow_naive
    from backend.services.entity_delete import (
        count_other_team_members, list_live_transactions,
    )

    row = db.execute(text("""
        SELECT id, name, user_id, deleted_at
        FROM artists WHERE id = :aid
    """), {"aid": artist_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Artist not found")

    # BUG FIX (Jul 2026 audit): access-gate BEFORE the tombstone short-circuit
    # so a random logged-in user can't confirm whether a given id is
    # tombstoned via the response shape. Since check_artist_access itself now
    # rejects tombstones, we bypass it and manually check ownership +
    # entity_users for the tombstone case.
    is_owner = (row["user_id"] == user.id)
    _is_member = bool(db.execute(text(
        "SELECT 1 FROM entity_users WHERE entity_type='artist' AND entity_id=:aid AND user_id=:uid"
    ), {"aid": artist_id, "uid": user.id}).first())
    if not is_owner and not _is_member:
        raise HTTPException(403, "You don't have access to this artist")

    if row["deleted_at"]:
        # Already tombstoned — the modal shouldn't show any Delete button.
        return {
            "id": row["id"], "name": row["name"], "is_owner": False,
            "already_deleted": True,
            "other_users_count": 0, "upcoming_gigs": [], "live_txns": [],
        }

    upcoming = db.execute(text("""
        SELECT DISTINCT g.id as gig_id, g.date, v.venue_name
        FROM gigs g
        JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
                         AND gs.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
        LEFT JOIN venues v ON v.id = g.venue_id
        WHERE g.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
          AND g.date >= :today
        ORDER BY g.date ASC
    """), {"aid": artist_id, "today": utcnow_naive().date().isoformat()}).mappings().all()

    return {
        "id": row["id"], "name": row["name"], "is_owner": is_owner,
        "already_deleted": False,
        "other_users_count": count_other_team_members(db, "artist", artist_id, user.id),
        "upcoming_gigs":    [dict(g) for g in upcoming],
        "live_txns":        list_live_transactions(db, "artist", artist_id),
    }


@router.delete("/api/artists/{artist_id}")
def delete_artist(artist_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """SOFT-delete (tombstone) an artist. See services/entity_delete.delete_artist
    for the exact pipeline. Owner-only: delegated team members get 403.

    Historical rows (past gig_slots, settled transactions, reviews both
    directions) are PRESERVED so venue history keeps rendering "[Deleted]
    <name>" correctly. Future in-flight bookings are cancelled with
    counter-party notifications. Live (mid-flight) transactions block
    deletion with 409 — the frontend can preview these via GET
    /api/artists/{id}/delete-preview.
    """
    from backend.services.entity_delete import delete_artist as _delete_artist, _rm_tree
    try:
        # Audit fix (Jul 2026): _delete_artist returns the list of dirs
        # to remove AFTER commit. Doing it before meant a commit-fail =
        # media gone but artist row still live.
        _rm_paths = _delete_artist(db, artist_id, user.id) or []
        db.commit()
        for _p in _rm_paths:
            try:
                _rm_tree(_p)
            except Exception:
                pass
        return {"success": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        import logging as _log
        _log.getLogger("gigsfill.artists").exception(f"delete_artist failed for artist={artist_id} user={user.id}: {e}")
        db.rollback()
        raise HTTPException(500, "Failed to delete artist. Please try again.")
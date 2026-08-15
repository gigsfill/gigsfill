from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user, get_optional_user
from datetime import datetime
import logging
from backend.utils import utcnow_naive
from backend.rate_limiter import limiter

logger = logging.getLogger("gigsfill.venues")

router = APIRouter()

# CREATE
@router.post("/api/venues")
def create_venue(data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    """v83: Create venue - EXACT copy of signup venue creation logic"""
    from backend.us_cities import find_city
    
    # v83: DEBUG - Log all incoming data
    
    # Get required fields
    venue_name = data.get("venue_name", "").strip()
    if not venue_name:
        raise HTTPException(400, "Venue name required")
    
    address = data.get("address_line_1", "")
    city = data.get("city", "")
    state = data.get("state", "")
    zip_code = data.get("zip_code", "")
    description = data.get("description", "")
    
    # Parse default pay into dollars and cents
    default_pay_str = str(data.get("default_pay", "0"))
    try:
        default_pay_float = float(default_pay_str)
        default_pay_dollars = int(default_pay_float)
        default_pay_cents = int((default_pay_float - default_pay_dollars) * 100)
    except:
        default_pay_dollars = 0
        default_pay_cents = 0
    
    performance_frequency = data.get("performance_frequency_days", 30)
    capacity = data.get("capacity", 0)
    
    # Get amenity fields - EXACT match to signup
    has_stage = data.get("has_stage", 0)
    stage_width_ft = data.get("stage_width") or None
    stage_depth_ft = data.get("stage_depth") or None
    setup_location_description = data.get("setup_location") or None
    has_sound_equipment = data.get("has_sound_equipment", 0)
    sound_equipment_description = data.get("sound_equipment_desc") or None
    has_sound_engineer = data.get("has_sound_engineer", 0)
    sound_engineer_details = data.get("sound_engineer_details") or None
    has_lighting = data.get("has_lighting", 0)
    lighting_description = data.get("lighting_desc") or None
    load_in_out_details = data.get("load_in_out") or None
    bar_tab_details = data.get("bar_tab_details") or None
    food_tab_details = data.get("food_tab_details") or None
    
    # Arrival time fields
    arrival_time_type = data.get("arrival_time_type") or data.get("arrival_type") or "flexible"
    arrival_no_earlier_than_hour = data.get("arrival_no_earlier_than_hour") or data.get("arrival_hour") or None
    arrival_no_earlier_than_period = data.get("arrival_no_earlier_than_period") or data.get("arrival_period") or None
    
    # PRO certification
    pro_certified = 1 if data.get("pro_certified") else 0
    pro_certified_at = utcnow_naive().isoformat() if pro_certified else None
    
    # Geocode city to get coordinates
    latitude = None
    longitude = None
    if city and state:
        city_data = find_city(city, state)
        if city_data:
            latitude = city_data["lat"]
            longitude = city_data["lon"]
        else:
            raise HTTPException(400, "This city is either misspelled or too small for our system. Please enter the closest big city to yours.")
    
    # Check for duplicate venue name + city + state
    if venue_name and city and state:
        existing = db.execute(text("""
            SELECT id FROM venues
            WHERE LOWER(venue_name) = LOWER(:n) AND LOWER(city) = LOWER(:c) AND UPPER(state) = UPPER(:s)
        """), {"n": venue_name, "c": city, "s": state}).first()
        if existing:
            raise HTTPException(409, f"A venue named '{venue_name}' already exists in {city}, {state}. If this is your venue, request access from your profile page.")

    # Server-side duplicate guard: same name + city + state = duplicate
    dup_v = db.execute(text("""
        SELECT v.id, v.venue_name, v.city, v.state
        FROM venues v
        WHERE LOWER(v.venue_name) = LOWER(:n) AND LOWER(v.city) = LOWER(:c) AND UPPER(v.state) = UPPER(:s)
        LIMIT 1
    """), {"n": venue_name, "c": city or "", "s": state or ""}).mappings().first()
    if dup_v:
        raise HTTPException(409, f"A venue named '{dup_v['venue_name']}' already exists in {dup_v['city']}, {dup_v['state']}. If this is your venue, request access from the profile owner.")

    # Create venue profile - EXACT match to signup
    from backend.models import Venue
    venue = Venue(
        user_id=user.id,
        venue_name=venue_name,
        address_line_1=address,
        city=city,
        state=state,
        postal_code=zip_code
    )
    db.add(venue)
    db.commit()
    db.refresh(venue)
    
    # Add ALL fields via raw SQL - EXACT match to signup
    try:
        db.execute(
            text("""
                UPDATE venues 
                SET description = :desc,
                    default_pay_dollars = :pay_dollars,
                    default_pay_cents = :pay_cents,
                    artist_frequency_days = :freq,
                    venue_size = :cap,
                    latitude = :lat,
                    longitude = :lon,
                    has_stage = :has_stage,
                    stage_width_ft = :stage_width,
                    stage_depth_ft = :stage_depth,
                    setup_location_description = :setup_loc,
                    has_sound_equipment = :has_sound,
                    sound_equipment_description = :sound_desc,
                    has_sound_engineer = :has_engineer,
                    sound_engineer_details = :engineer_details,
                    has_lighting = :has_lighting,
                    lighting_description = :lighting_desc,
                    load_in_out_details = :load_details,
                    arrival_time_type = :arrival_type,
                    arrival_no_earlier_than_hour = :arrival_hour,
                    arrival_no_earlier_than_period = :arrival_period,
                    bar_tab_details = :bar_tab,
                    food_tab_details = :food_tab,
                    pro_certified = :pro_cert,
                    pro_certified_at = :pro_cert_at
                WHERE id = :vid
            """),
            {
                "desc": description, 
                "pay_dollars": default_pay_dollars,
                "pay_cents": default_pay_cents,
                "freq": performance_frequency,
                "cap": capacity,
                "lat": latitude,
                "lon": longitude,
                "has_stage": has_stage,
                "stage_width": stage_width_ft,
                "stage_depth": stage_depth_ft,
                "setup_loc": setup_location_description,
                "has_sound": has_sound_equipment,
                "sound_desc": sound_equipment_description,
                "has_engineer": has_sound_engineer,
                "engineer_details": sound_engineer_details,
                "has_lighting": has_lighting,
                "lighting_desc": lighting_description,
                "load_details": load_in_out_details,
                "arrival_type": arrival_time_type,
                "arrival_hour": arrival_no_earlier_than_hour,
                "arrival_period": arrival_no_earlier_than_period,
                "bar_tab": bar_tab_details,
                "food_tab": food_tab_details,
                "pro_cert": pro_certified,
                "pro_cert_at": pro_certified_at,
                "vid": venue.id
            }
        )
        db.commit()
    except Exception as e:
        raise HTTPException(500, "Failed to save venue details. Please try again.")
    
    # Add creator as owner in entity_users
    db.execute(
        text("""
            INSERT INTO entity_users (entity_type, entity_id, user_id, role, added_by_user_id, created_at)
            VALUES ('venue', :entity_id, :user_id, 'owner', :user_id, CURRENT_TIMESTAMP)
        """),
        {"entity_id": venue.id, "user_id": user.id}
    )
    db.commit()
    
    return {"id": venue.id}

# ✅ NEW: Public venue listing for artists
@router.get("/api/venues/public")
@limiter.limit("30/minute")
def list_public_venues(request: Request, db=Depends(get_db)):
    """Public endpoint for artists to discover venues.
    Rate-limited (Jul 1 2026 audit fix): 30/min per IP prevents catalog
    scraping via anonymous traffic. Legitimate browsers hit this once
    per navigation."""
    rows = db.execute(
        text("""
            SELECT
                id,
                venue_name,
                description,
                address_line_1,
                city,
                state,
                venue_size,
                has_stage,
                has_sound_equipment,
                has_lighting,
                has_sound_engineer,
                default_pay_dollars,
                default_pay_cents
            FROM venues
            WHERE COALESCE(payment_status, 'active') != 'suspended'
              AND deleted_at IS NULL
            ORDER BY venue_name ASC
        """)
    ).mappings().all()

    return rows

# v97: Public single venue endpoint for profile viewing
@router.get("/api/venues/{venue_id}/public")
@limiter.limit("60/minute")
def get_venue_public(venue_id: int, request: Request,
                     user=Depends(get_optional_user), db=Depends(get_db)):
    """Public endpoint to view any venue profile.

    Audit fix (Jun 2026): `user_id` was exposed in the response,
    enabling venue→user enumeration from anonymous traffic.
    Authed routes still surface user_id where needed; only the
    public endpoint drops it.

    Rate-limited (Jul 1 2026 audit fix): 60/min per IP. Public profile
    views are common (one per navigation) but bulk enumeration is not.

    2026-08-07: added `viewer_is_artist` flag so the profile page can
    reveal the "Gig Details" tab (stage, sound, lighting, pay, tabs,
    etc.) to logged-in artist users only. Anonymous and venue-only
    visitors see the current lean public view. Uses `get_optional_user`
    (returns None for anonymous) + a one-shot query that returns true
    when the caller owns or is an entity_user of at least one artist.
    """
    row = db.execute(
        text("""
            SELECT
                id,
                venue_name,
                description,
                address_line_1,
                address_line_2,
                city,
                state,
                postal_code,
                venue_size,
                has_stage,
                stage_width_ft,
                stage_depth_ft,
                setup_location_description,
                has_sound_equipment,
                sound_equipment_description,
                has_sound_engineer,
                sound_engineer_details,
                has_lighting,
                lighting_description,
                load_in_out_details,
                arrival_time_type,
                arrival_no_earlier_than_hour,
                arrival_no_earlier_than_period,
                default_pay_dollars,
                default_pay_cents,
                bar_tab_details,
                food_tab_details,
                artist_frequency_days,
                website_url,
                instagram_url,
                facebook_url,
                twitter_url,
                yelp_url,
                google_maps_url,
                social_order,
                pro_certified
            FROM venues
            WHERE id = :id
              AND deleted_at IS NULL
        """),
        {"id": venue_id}
    ).mappings().first()

    if not row:
        raise HTTPException(404)

    viewer_is_artist = False
    if user is not None:
        _hit = db.execute(
            text("""SELECT 1 FROM artists a
                    WHERE a.deleted_at IS NULL
                      AND (a.user_id = :uid OR EXISTS (
                        SELECT 1 FROM entity_users eu
                        WHERE eu.entity_type = 'artist'
                          AND eu.entity_id = a.id
                          AND eu.user_id = :uid
                      ))
                    LIMIT 1"""),
            {"uid": user.id}
        ).first()
        viewer_is_artist = bool(_hit)

    out = dict(row)
    out["viewer_is_artist"] = viewer_is_artist
    # 2026-08-08 audit fix (finding #11): the endpoint was returning the
    # gated fields to every caller and only the UI hid them, defeating
    # the whole "artist-only Gig Details" gate — an anonymous scraper
    # could enumerate /api/venues/{id}/public and harvest the full
    # venue-ops catalog (rig / pay / bar-food tabs / arrival policy).
    # Enforce server-side: when the caller is NOT an artist, strip the
    # sensitive fields. Non-artist authenticated users and anonymous
    # visitors still get the lean public view (name / address / bio /
    # social / PRO flag / capacity) — nothing new is hidden that the
    # site had public before we added viewer_is_artist.
    if not viewer_is_artist:
        for _gated in (
            "has_stage", "stage_width_ft", "stage_depth_ft",
            "setup_location_description",
            "has_sound_equipment", "sound_equipment_description",
            "has_sound_engineer", "sound_engineer_details",
            "has_lighting", "lighting_description",
            "load_in_out_details",
            "arrival_time_type",
            "arrival_no_earlier_than_hour",
            "arrival_no_earlier_than_period",
            "default_pay_dollars", "default_pay_cents",
            "bar_tab_details", "food_tab_details",
            "artist_frequency_days",
        ):
            out.pop(_gated, None)
    return out

# LIST (PROFILE PAGE) - MOVED TO me.py (supports display_order and entity_users)
# @router.get("/api/my/venues")
# def my_venues(user=Depends(get_current_user), db=Depends(get_db)):
#     rows = db.execute(
#         text("""
#             SELECT id, venue_name as name
#             FROM venues
#             WHERE user_id = :uid
#             ORDER BY id DESC
#         """),
#         {"uid": user.id}
#     ).mappings().all()
#
#     return [dict(row) for row in rows]

# GET SINGLE
@router.get("/venues/{venue_id}")
def get_venue(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    # v97: Check ownership OR entity_users access
    row = db.execute(
        text("""
            SELECT v.*
            FROM venues v
            WHERE v.id = :id 
              AND (
                v.user_id = :uid
                OR EXISTS (
                  SELECT 1 FROM entity_users eu 
                  WHERE eu.entity_type = 'venue' 
                  AND eu.entity_id = v.id 
                  AND eu.user_id = :uid
                )
              )
        """),
        {"id": venue_id, "uid": user.id}
    ).mappings().first()

    if not row:
        raise HTTPException(404)

    return dict(row)

# GET SINGLE WITH /api/ PREFIX - FOR FRONTEND
@router.get("/api/venues/{venue_id}")
def get_venue_api(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Frontend uses /api/ prefix - returns ALL venue fields including default_pay_dollars and default_pay_cents"""
    # v97: Check ownership OR entity_users access
    row = db.execute(
        text("""
            SELECT v.*
            FROM venues v
            WHERE v.id = :id 
              AND (
                v.user_id = :uid
                OR EXISTS (
                  SELECT 1 FROM entity_users eu 
                  WHERE eu.entity_type = 'venue' 
                  AND eu.entity_id = v.id 
                  AND eu.user_id = :uid
                )
              )
        """),
        {"id": venue_id, "uid": user.id}
    ).mappings().first()

    if not row:
        raise HTTPException(404)

    return dict(row)

# ✅ NEW: Get venue frequency (PUBLIC - for artists)
@router.get("/api/venues/{venue_id}/frequency")
def get_venue_frequency(venue_id: int, db=Depends(get_db)):
    """Public endpoint to get venue frequency limit"""
    row = db.execute(
        text("""
            SELECT artist_frequency_days
            FROM venues
            WHERE id = :id
        """),
        {"id": venue_id}
    ).mappings().first()

    if not row:
        raise HTTPException(404, "Venue not found")

    # Return 0 if null (no frequency restriction)
    return {
        "artist_frequency_days": row["artist_frequency_days"] or 0
    }

# UPDATE (SAFE AUTOSAVE)
@router.put("/api/venues/{venue_id}")
@router.put("/venues/{venue_id}")  # Keep old route for compatibility
# Audit fix (May 2026 part 3): autosaved on every keystroke (debounced
# ~600ms in venue.edit.js). A misbehaving client could hammer this without
# a limit. 60/minute is generous for normal editing but caps abuse.
@limiter.limit("60/minute")
def update_venue(venue_id: int, data: dict, request: Request,
                 user=Depends(get_current_user), db=Depends(get_db)):
    from backend.us_cities import find_city
    
    
    # v96: Check access via ownership OR entity_users
    # Also pull venue_name + stripe_customer_id so we can detect a
    # rename after the UPDATE and mirror the new name into Stripe
    # (otherwise the Stripe dashboard + hosted invoices + receipts
    # keep the old name indefinitely — see fix Jul 20 2026).
    #
    # Jul 21 2026 hotfix: `stripe_customer_id` lives on
    # `entity_payment_settings`, NOT on `venues` — the earlier
    # `v.stripe_customer_id` reference blew up with `no such column`
    # and 500'd every save (nobody could rename a venue). LEFT JOIN
    # via entity_type + entity_id to grab it.
    current_venue = db.execute(
        text("""
            SELECT v.city, v.state, v.venue_name, eps.stripe_customer_id
              FROM venues v
              LEFT JOIN entity_payment_settings eps
                ON eps.entity_type = 'venue' AND eps.entity_id = v.id
             WHERE v.id = :id
             AND (
                 v.user_id = :uid
                 OR EXISTS (
                     SELECT 1 FROM entity_users eu
                     WHERE eu.entity_type = 'venue'
                     AND eu.entity_id = v.id
                     AND eu.user_id = :uid
                 )
             )
        """),
        {"id": venue_id, "uid": user.id}
    ).first()

    if not current_venue:
        raise HTTPException(403)

    # Use current values if not provided in update
    current_city, current_state, _prior_venue_name, _stripe_customer_id = current_venue

    params = {
        "id": venue_id,
        "venue_name": data.get("venue_name"),
        "description": data.get("description"),
        "address_line_1": data.get("address_line_1"),
        "address_line_2": data.get("address_line_2"),
        "city": data.get("city"),
        "state": data.get("state"),
        "postal_code": data.get("postal_code"),
        "venue_size": data.get("venue_size"),
        "has_stage": data.get("has_stage"),
        "stage_width_ft": data.get("stage_width_ft"),
        "stage_depth_ft": data.get("stage_depth_ft"),
        "setup_location_description": data.get("setup_location_description"),
        "has_sound_equipment": data.get("has_sound_equipment"),
        "sound_equipment_description": data.get("sound_equipment_description"),
        "has_sound_engineer": data.get("has_sound_engineer"),
        "sound_engineer_details": data.get("sound_engineer_details"),
        "has_lighting": data.get("has_lighting"),
        "lighting_description": data.get("lighting_description"),
        "load_in_out_details": data.get("load_in_out_details"),
        "arrival_time_type": data.get("arrival_time_type"),
        "arrival_no_earlier_than_hour": data.get("arrival_no_earlier_than_hour"),
        "arrival_no_earlier_than_period": data.get("arrival_no_earlier_than_period"),
        "default_pay_dollars": data.get("default_pay_dollars"),
        "default_pay_cents": data.get("default_pay_cents"),
        "bar_tab_details": data.get("bar_tab_details"),
        "food_tab_details": data.get("food_tab_details"),
        "artist_frequency_days": data.get("artist_frequency_days"),
        "website_url": data.get("website_url"),
        "facebook_url": data.get("facebook_url"),
        "instagram_url": data.get("instagram_url"),
        "twitter_url": data.get("twitter_url"),
        "yelp_url": data.get("yelp_url"),
        "google_maps_url": data.get("google_maps_url"),
        "social_order": data.get("social_order"),
        "pro_certified": data.get("pro_certified"),
        "pro_certified_at": data.get("pro_certified_at"),
        "auto_flyers": data.get("auto_flyers"),
        "default_flyer_template_id": data.get("default_flyer_template_id"),
        # 2026-08-10: same-day booking approval gate — moved from
        # per-user email pref to per-venue policy. Editable on the
        # venue's Email Notifications tab.
        "require_same_day_approval": data.get("require_same_day_approval"),
    }
    
    # Geocode city to get coordinates
    # Use current values if not in update data
    geocode_city = params["city"] if params["city"] is not None else current_city
    geocode_state = params["state"] if params["state"] is not None else current_state
    
    if geocode_city and geocode_state:
        city_data = find_city(geocode_city, geocode_state)
        if city_data:
            params["latitude"] = city_data["lat"]
            params["longitude"] = city_data["lon"]
        else:
            # Try without state as fallback
            city_data = find_city(geocode_city)
            if city_data:
                params["latitude"] = city_data["lat"]
                params["longitude"] = city_data["lon"]
            else:
                # If city field is being actively updated, reject invalid city
                if params["city"] is not None:
                    raise HTTPException(400, "This city is either misspelled or too small for our system. Please enter the closest big city to yours.")
                params["latitude"] = None
                params["longitude"] = None
    else:
        params["latitude"] = None
        params["longitude"] = None

    db.execute(
    text("""
        UPDATE venues SET
            venue_name = COALESCE(:venue_name, venue_name),
            description = COALESCE(:description, description),
            address_line_1 = COALESCE(:address_line_1, address_line_1),
            address_line_2 = COALESCE(:address_line_2, address_line_2),
            city = COALESCE(:city, city),
            state = COALESCE(:state, state),
            postal_code = COALESCE(:postal_code, postal_code),
            venue_size = COALESCE(:venue_size, venue_size),
            has_stage = COALESCE(:has_stage, has_stage),
            stage_width_ft = COALESCE(:stage_width_ft, stage_width_ft),
            stage_depth_ft = COALESCE(:stage_depth_ft, stage_depth_ft),
            setup_location_description = COALESCE(:setup_location_description, setup_location_description),
            has_sound_equipment = COALESCE(:has_sound_equipment, has_sound_equipment),
            sound_equipment_description = COALESCE(:sound_equipment_description, sound_equipment_description),
            has_sound_engineer = COALESCE(:has_sound_engineer, has_sound_engineer),
            sound_engineer_details = COALESCE(:sound_engineer_details, sound_engineer_details),
            has_lighting = COALESCE(:has_lighting, has_lighting),
            lighting_description = COALESCE(:lighting_description, lighting_description),
            load_in_out_details = CASE WHEN :load_in_out_details_set = 1 THEN :load_in_out_details ELSE load_in_out_details END,
            arrival_time_type = CASE WHEN :arrival_time_type_set = 1 THEN :arrival_time_type ELSE arrival_time_type END,
            arrival_no_earlier_than_hour = CASE WHEN :arrival_hour_set = 1 THEN :arrival_no_earlier_than_hour ELSE arrival_no_earlier_than_hour END,
            arrival_no_earlier_than_period = CASE WHEN :arrival_period_set = 1 THEN :arrival_no_earlier_than_period ELSE arrival_no_earlier_than_period END,
            default_pay_dollars = COALESCE(:default_pay_dollars, default_pay_dollars),
            default_pay_cents = COALESCE(:default_pay_cents, default_pay_cents),
            bar_tab_details = COALESCE(:bar_tab_details, bar_tab_details),
            food_tab_details = COALESCE(:food_tab_details, food_tab_details),
            artist_frequency_days = COALESCE(:artist_frequency_days, artist_frequency_days),
            latitude = COALESCE(:latitude, latitude),
            longitude = COALESCE(:longitude, longitude),
            website_url = COALESCE(:website_url, website_url),
            facebook_url = COALESCE(:facebook_url, facebook_url),
            instagram_url = COALESCE(:instagram_url, instagram_url),
            twitter_url = COALESCE(:twitter_url, twitter_url),
            yelp_url = COALESCE(:yelp_url, yelp_url),
            google_maps_url = COALESCE(:google_maps_url, google_maps_url),
            social_order = COALESCE(:social_order, social_order),
            pro_certified = COALESCE(:pro_certified, pro_certified),
            pro_certified_at = COALESCE(:pro_certified_at, pro_certified_at),
            auto_flyers = COALESCE(:auto_flyers, auto_flyers),
            default_flyer_template_id = COALESCE(:default_flyer_template_id, default_flyer_template_id),
            require_same_day_approval = COALESCE(:require_same_day_approval, require_same_day_approval)
        WHERE id = :id
    """),
    {
        **params,
        # Arrival fields: use CASE/flag pattern so explicit null clears the value
        "arrival_time_type_set":  1 if "arrival_time_type" in data else 0,
        "arrival_hour_set":       1 if "arrival_no_earlier_than_hour" in data else 0,
        "arrival_period_set":     1 if "arrival_no_earlier_than_period" in data else 0,
        "load_in_out_details_set":1 if "load_in_out_details" in data else 0,
    }
)
    
    db.commit()

    # Mirror the venue-name change into Stripe so the Customer.name
    # (visible on the Stripe dashboard, hosted invoices, and any
    # receipt/statement descriptor derived from it) stays in sync with
    # the venue's chosen display name. Best-effort — a Stripe API
    # failure shouldn't roll back the local update. Idempotent: if the
    # name didn't change, we skip the call entirely.
    _new_name = params.get("venue_name")
    if _stripe_customer_id and _new_name and _new_name != _prior_venue_name:
        try:
            import stripe as _stripe_mod
            _stripe_mod.Customer.modify(_stripe_customer_id, name=_new_name)
            logger.info(f"[STRIPE] Customer.name updated for venue #{venue_id}: "
                        f"{_prior_venue_name!r} → {_new_name!r}")
        except Exception as e:
            # Log but don't fail — the DB truth is what matters; Stripe
            # can be reconciled later via retry or admin action.
            logger.warning(f"[STRIPE] Customer.name update failed for venue "
                           f"#{venue_id} (customer {_stripe_customer_id}): {e}")

    # Invalidate cached flyer thumbnails on rename. Every non-template
    # flyer for this venue has a rendered PNG thumbnail baked in with
    # the old venue name; clearing forces the frontend to regenerate
    # from live canvas data on next open. flyers.name (the download
    # filename) is left alone — it self-heals on re-save and is
    # rarely user-visible.
    if _new_name and _new_name != _prior_venue_name:
        try:
            db.execute(
                text("UPDATE flyers SET thumbnail_data = NULL "
                     "WHERE venue_id = :vid AND is_template = 0"),
                {"vid": venue_id}
            )
            db.commit()
        except Exception as e:
            logger.warning(f"[FLYERS] thumbnail invalidation failed for venue "
                           f"#{venue_id} rename: {e}")

        # Jul 2026: auto-migrate the vanity slug when the venue name
        # changes, but only if the user never customized it (i.e. current
        # slug == slugify(old_name)). Old slug parks in vanity_url_redirects
        # for 90 days so previously-shared links keep working. See
        # maybe_update_slug_on_rename in backend/routes/vanity.py.
        try:
            from backend.routes.vanity import maybe_update_slug_on_rename
            maybe_update_slug_on_rename(
                db, "venue", venue_id, _prior_venue_name, _new_name
            )
        except Exception as e:
            logger.warning(f"[VANITY] slug rename skipped for venue #{venue_id}: {e}")

    return {"ok": True}

@router.get("/venues/{venue_id}/preferred-requests")
def list_preferred_requests(
    venue_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    # Audit fix (May 2026 part 3): use check_venue_access so venue
    # managers / bookers added via entity_users can see pending
    # preferred-artist requests in the UI. Previously this owner-only
    # check made the requests invisible to staff users.
    from backend.utils import check_venue_access
    check_venue_access(db, venue_id, user.id)

    rows = db.execute(
        text("""
            SELECT
                pa.artist_id,
                a.name AS artist_name,
                pa.created_at
            FROM preferred_artists pa
            JOIN artists a ON a.id = pa.artist_id
            WHERE pa.venue_id = :vid
            AND pa.status = 'pending'
            ORDER BY pa.created_at ASC
        """),
        {"vid": venue_id}
    ).mappings().all()

    return rows

@router.post("/venues/{venue_id}/preferred-requests/{artist_id}")
def resolve_preferred_request(
    venue_id: int,
    artist_id: int,
    data: dict,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    action = data.get("action")
    pay_dollars = data.get("pay_dollars_override")
    pay_cents = data.get("pay_cents_override")
    frequency_days = data.get("frequency_days_override")

    if action not in ("approved", "denied"):
        raise HTTPException(400, "Invalid action")

    # Verify venue ownership
    venue = db.execute(
        text("SELECT user_id, venue_name FROM venues WHERE id=:id"),
        {"id": venue_id}
    ).mappings().first()

    # Allow entity_users (venue staff) to manage preferred artists too
    from backend.utils import check_venue_access as _cva
    _cva(db, venue_id, user.id)

    if not venue:
        raise HTTPException(403, "Not your venue")

    # Get artist info for notification
    artist_info = db.execute(
        text("SELECT user_id, name FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()

    # Update with custom values
    db.execute(
        text("""
            UPDATE preferred_artists
            SET status = :status,
                pay_dollars_override = :pay_dollars,
                pay_cents_override = :pay_cents,
                frequency_days_override = :freq
            WHERE venue_id = :vid
              AND artist_id = :aid
        """),
        {
            "status": action,
            "pay_dollars": pay_dollars,
            "pay_cents": pay_cents,
            "freq": frequency_days,
            "vid": venue_id,
            "aid": artist_id
        }
    )

    # Create notification for artist
    if artist_info:
        if action == "approved":
            title = "Preferred Status Approved!"
            message = f"{venue['venue_name']} has approved you as a preferred artist. You can now book gigs at this venue!"
            notif_type = "preferred_approved"
        else:
            title = "Preferred Status Denied"
            message = f"{venue['venue_name']} has denied your preferred artist request."
            notif_type = "preferred_denied"
        
        db.execute(
            text("""
                INSERT INTO notifications
                    (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
                VALUES
                    (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
            """),
            {
                "user_id": artist_info["user_id"],
                "type": notif_type,
                "title": title,
                "message": message,
                "venue_id": venue_id,
                "artist_id": artist_id,
                "created_at": utcnow_naive()
            }
        )

    db.commit()
    return {"ok": True}

# CRITICAL FIX: This endpoint MUST accept artist_id from query params
@router.get("/venues/{venue_id}/preferred-status")
def preferred_status(
    venue_id: int,
    request: Request,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    # CRITICAL: Get artist_id from query params
    artist_id = request.query_params.get('artist_id')
    
    if not artist_id:
        # Fallback to first artist (but frontend should always provide it)
        artist = db.execute(
            text("SELECT id FROM artists WHERE user_id = :uid LIMIT 1"),
            {"uid": user.id}
        ).mappings().first()

        if not artist:
            return {"status": None}
        artist_id = int(artist["id"])
    else:
        # v96: Verify user has access via ownership OR entity_users
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
            return {"status": None}
        artist_id = int(artist_id)

    # Get status for THIS specific artist
    row = db.execute(
        text("""
            SELECT status
            FROM preferred_artists
            WHERE venue_id = :vid
              AND artist_id = :aid
        """),
        {
            "vid": venue_id,
            "aid": artist_id
        }
    ).mappings().first()

    return {
        "status": row["status"] if row else None
    }

# v93: Get all preferred artists for a venue (for search filtering)
@router.get("/api/venues/{venue_id}/preferred-artists")
def get_venue_preferred_artists(
    venue_id: int,
    for_gig_date: str = None,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get all preferred artists for a venue.

    Optional `for_gig_date=YYYY-MM-DD` — when provided, adds a `freq_status`
    object to each artist row describing whether they are currently under
    the venue's frequency policy for a hypothetical gig on that date.
    Used by the Hold-gig artist picker so the venue sees a chip next to
    each under-freq artist and is warned that adding them will require
    waiving the rule. Without the param the response shape is unchanged.
    """
    # Verify venue access (ownership OR entity_users)
    access = db.execute(
        text("""
            SELECT 1 FROM venues v
            WHERE v.id = :vid 
            AND (
                v.user_id = :uid
                OR EXISTS (
                    SELECT 1 FROM entity_users eu 
                    WHERE eu.entity_type = 'venue' 
                    AND eu.entity_id = v.id 
                    AND eu.user_id = :uid
                )
            )
        """),
        {"vid": venue_id, "uid": user.id}
    ).scalar()
    
    if not access:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Get all preferred artists with full data + type/format/styles
    # for the Hold-Gig filter on the create modal (Jun 2026).
    preferred = db.execute(
        text("""
            SELECT
                pa.artist_id,
                pa.status,
                pa.pay_dollars_override,
                pa.pay_cents_override,
                a.name as artist_name,
                u.email as artist_email,
                a.city,
                a.state,
                a.artist_type,
                a.band_formats,
                a.styles
            FROM preferred_artists pa
            JOIN artists a ON pa.artist_id = a.id
            JOIN users u ON a.user_id = u.id
            WHERE pa.venue_id = :vid
            ORDER BY a.name
        """),
        {"vid": venue_id}
    ).mappings().all()

    banned_ids = {r[0] for r in db.execute(
        text("SELECT artist_id FROM venue_artist_bans WHERE venue_id = :vid"),
        {"vid": venue_id}
    ).all()}

    result = [dict(row) for row in preferred]
    for r in result:
        r["is_banned"] = r["artist_id"] in banned_ids

    # Also include banned artists not in preferred_artists
    banned_only = db.execute(
        text("""SELECT vab.artist_id, 'banned' as status, NULL as pay_dollars_override,
                       NULL as pay_cents_override, a.name as artist_name, u.email as artist_email,
                       a.city, a.state
                FROM venue_artist_bans vab
                JOIN artists a ON a.id = vab.artist_id
                JOIN users u ON a.user_id = u.id
                WHERE vab.venue_id = :vid
                  AND vab.artist_id NOT IN (
                      SELECT artist_id FROM preferred_artists WHERE venue_id = :vid
                  )"""),
        {"vid": venue_id}
    ).mappings().all()
    for r in banned_only:
        d = dict(r); d["is_banned"] = True
        result.append(d)

    # Per-artist frequency status for the Hold-gig picker (Jun 2026).
    # Returns nothing extra when the caller didn't ask for a specific
    # gig date — keeps existing consumers wire-compatible.
    if for_gig_date:
        try:
            from datetime import datetime as _dt
            _gd = _dt.strptime(for_gig_date[:10], "%Y-%m-%d").date()
            # Single query for the venue-default freq days — used as the
            # fallback when an artist has no per-artist override.
            _venue_freq_default = db.execute(
                text("SELECT artist_frequency_days FROM venues WHERE id = :vid"),
                {"vid": venue_id}
            ).scalar()
            for r in result:
                if r.get("is_banned"):
                    r["freq_status"] = None
                    continue
                # Per-artist override else venue default. status of the
                # preferred_artists row doesn't gate the freq policy —
                # the policy itself is venue-side.
                _row = db.execute(
                    text("""SELECT COALESCE(pa.frequency_days_override, v.artist_frequency_days) as freq_days
                            FROM preferred_artists pa
                            JOIN venues v ON v.id = pa.venue_id
                            WHERE pa.venue_id = :vid AND pa.artist_id = :aid"""),
                    {"vid": venue_id, "aid": r["artist_id"]}
                ).mappings().first()
                _freq_days = (_row["freq_days"] if _row else _venue_freq_default) or 0
                if _freq_days <= 0:
                    # 0 = no restriction
                    r["freq_status"] = {"applies": False, "freq_days": 0}
                    continue
                # Closest booking at this venue, in either direction.
                # Jul 2026 refactor: dropped `g.artist_id = :aid` OR-leg.
                _booked = db.execute(text("""
                    SELECT g.date FROM gigs g
                    JOIN gig_slots gs ON gs.gig_id = g.id AND gs.artist_id = :aid
                    WHERE g.venue_id = :vid
                      AND gs.status IN ('booked','pending_contract','awaiting_venue_contract','pending_venue_approval')
                    ORDER BY ABS(julianday(g.date) - julianday(:date)) ASC
                    LIMIT 1
                """), {"vid": venue_id, "aid": r["artist_id"], "date": for_gig_date}).mappings().first()
                if not _booked:
                    r["freq_status"] = {"applies": True, "freq_days": int(_freq_days), "under_limit": False}
                    continue
                _last_dt = _dt.strptime(str(_booked["date"])[:10], "%Y-%m-%d").date()
                _diff = (_gd - _last_dt).days
                _abs = abs(_diff)
                r["freq_status"] = {
                    "applies": True,
                    "freq_days": int(_freq_days),
                    "under_limit": _abs <= int(_freq_days),
                    "last_gig_date": str(_booked["date"])[:10],
                    "days_between": _diff,
                    "abs_days_between": _abs,
                }
        except Exception as _fse:
            # Don't fail the whole endpoint if the freq enrichment trips
            # over odd data — picker still works, just without chips.
            import logging as _lg
            _lg.getLogger("gigsfill").warning(f"freq_status enrichment failed venue={venue_id} date={for_gig_date}: {_fse}")

    return result

@router.get("/api/venues/{venue_id}/delete-preview")
def delete_venue_preview(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Preview payload for the delete-venue modal. See
    /api/artists/{id}/delete-preview for the shape rationale — same fields,
    upcoming_gigs now lists artist_name of each booked counter-party."""
    from backend.utils import utcnow_naive, check_venue_access
    from backend.services.entity_delete import (
        count_other_team_members, list_live_transactions,
    )

    row = db.execute(text("""
        SELECT id, venue_name, user_id, deleted_at
        FROM venues WHERE id = :vid
    """), {"vid": venue_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Venue not found")

    # BUG FIX (Jul 2026 audit): access-gate BEFORE the tombstone short-circuit.
    is_owner = (row["user_id"] == user.id)
    _is_member = bool(db.execute(text(
        "SELECT 1 FROM entity_users WHERE entity_type='venue' AND entity_id=:vid AND user_id=:uid"
    ), {"vid": venue_id, "uid": user.id}).first())
    if not is_owner and not _is_member:
        raise HTTPException(403, "You don't have access to this venue")

    if row["deleted_at"]:
        return {
            "id": row["id"], "name": row["venue_name"], "is_owner": False,
            "already_deleted": True,
            "other_users_count": 0, "upcoming_gigs": [], "live_txns": [],
        }

    # 2026-08-08 audit fix (same class as #5-7): outer `g.status IN (...)`
    # excluded partial multi-slot gigs (parent 'open' until last slot
    # books), so the venue's delete-preview undercounted future gigs
    # with any booked slot. Filter to gigs with at least one active
    # slot via an EXISTS instead.
    upcoming = db.execute(text("""
        SELECT DISTINCT g.id as gig_id, g.date,
               (SELECT GROUP_CONCAT(a.name, ', ')
                  FROM gig_slots gs2
                  LEFT JOIN artists a ON a.id = gs2.artist_id
                  WHERE gs2.gig_id = g.id
                    AND gs2.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
                    AND gs2.artist_id IS NOT NULL) as artist_names
        FROM gigs g
        WHERE g.venue_id = :vid
          AND g.date >= :today
          AND EXISTS (
              SELECT 1 FROM gig_slots gs
              WHERE gs.gig_id = g.id
                AND gs.status IN ('booked','awaiting_venue_contract','pending_contract','pending_venue_approval')
          )
        ORDER BY g.date ASC
    """), {"vid": venue_id, "today": utcnow_naive().date().isoformat()}).mappings().all()

    return {
        "id": row["id"], "name": row["venue_name"], "is_owner": is_owner,
        "already_deleted": False,
        "other_users_count": count_other_team_members(db, "venue", venue_id, user.id),
        "upcoming_gigs":    [dict(g) for g in upcoming],
        "live_txns":        list_live_transactions(db, "venue", venue_id),
    }


@router.delete("/api/venues/{venue_id}")
def delete_venue(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """SOFT-delete (tombstone) a venue. See services/entity_delete.delete_venue
    for the exact pipeline. Owner-only — even entity_users role='owner'
    co-owners are refused (deletion is reserved to the original creator on
    venues.user_id).

    Past gigs/slots/reviews/settled txns are PRESERVED so artist history
    keeps rendering "[Deleted] <venue>" correctly. Future in-flight
    bookings are cancelled with artist notifications. Live txns block with
    409 — preview via GET /api/venues/{id}/delete-preview.
    """
    from backend.services.entity_delete import delete_venue as _delete_venue, _rm_tree
    try:
        # Audit fix (Jul 2026): delete_venue now returns the list of
        # filesystem paths to clean up AFTER the commit succeeds. Doing
        # rmtree before commit meant a commit failure = permanent media
        # loss with the venue still live.
        _rm_paths = _delete_venue(db, venue_id, user.id) or []
        db.commit()
        for _p in _rm_paths:
            try:
                _rm_tree(_p)
            except Exception:
                pass  # _rm_tree already logs its own failures
        return {"success": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        import logging as _log
        _log.getLogger("gigsfill.venues").exception(f"delete_venue failed for venue={venue_id} user={user.id}: {e}")
        db.rollback()
        raise HTTPException(500, "Failed to delete venue. Please try again.")

# v89: PROACTIVE PREFERRED ARTIST APPROVAL
@router.post("/api/venues/{venue_id}/preferred-artists/{artist_id}/approve")
def proactive_approve_preferred_artist(
    venue_id: int,
    artist_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """
    Venue proactively approves an artist as preferred (doesn't require artist to request first)
    """
    # Verify venue ownership
    venue = db.execute(
        text("SELECT user_id, venue_name, default_pay_dollars, default_pay_cents, artist_frequency_days FROM venues WHERE id=:id"),
        {"id": venue_id}
    ).mappings().first()

    # Allow entity_users (venue staff) to approve preferred artists
    from backend.utils import check_venue_access as _cva2
    _cva2(db, venue_id, user.id)

    if not venue:
        raise HTTPException(403, "Not your venue")

    # Get artist info
    artist_info = db.execute(
        text("SELECT user_id, name FROM artists WHERE id = :aid"),
        {"aid": artist_id}
    ).mappings().first()

    if not artist_info:
        raise HTTPException(404, "Artist not found")

    # Check if preferred_artists record already exists
    existing = db.execute(
        text("SELECT id, status FROM preferred_artists WHERE venue_id = :vid AND artist_id = :aid"),
        {"vid": venue_id, "aid": artist_id}
    ).mappings().first()

    if existing:
        # Update existing record to approved
        db.execute(
            text("""
                UPDATE preferred_artists
                SET status = 'approved',
                    pay_dollars_override = :pay_dollars,
                    pay_cents_override = :pay_cents,
                    frequency_days_override = :freq
                WHERE venue_id = :vid AND artist_id = :aid
            """),
            {
                "pay_dollars": venue["default_pay_dollars"],
                "pay_cents": venue["default_pay_cents"],
                "freq": venue["artist_frequency_days"],
                "vid": venue_id,
                "aid": artist_id
            }
        )
    else:
        # Create new preferred_artists record as approved
        db.execute(
            text("""
                INSERT INTO preferred_artists
                    (venue_id, artist_id, status, pay_dollars_override, pay_cents_override, frequency_days_override)
                VALUES
                    (:vid, :aid, 'approved', :pay_dollars, :pay_cents, :freq)
            """),
            {
                "vid": venue_id,
                "aid": artist_id,
                "pay_dollars": venue["default_pay_dollars"],
                "pay_cents": venue["default_pay_cents"],
                "freq": venue["artist_frequency_days"]
            }
        )

    # Create notification for artist
    db.execute(
        text("""
            INSERT INTO notifications
                (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
            VALUES
                (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
        """),
        {
            "user_id": artist_info["user_id"],
            "type": "preferred_approved",
            "title": "Preferred Status Approved!",
            "message": f"{venue['venue_name']} has approved you as a preferred artist. You can now book gigs at this venue!",
            "venue_id": venue_id,
            "artist_id": artist_id,
            "created_at": utcnow_naive()
        }
    )

    # Create notification for venue owner
    db.execute(
        text("""
            INSERT INTO notifications
                (user_id, notification_type, title, message, venue_id, artist_id, is_read, created_at)
            VALUES
                (:user_id, :type, :title, :message, :venue_id, :artist_id, FALSE, :created_at)
        """),
        {
            "user_id": venue["user_id"],
            "type": "preferred_approved",
            "title": "Preferred Artist Approved",
            "message": f"You approved {artist_info['name']} as a preferred artist.",
            "venue_id": venue_id,
            "artist_id": artist_id,
            "created_at": utcnow_naive()
        }
    )

    # Audit trail for the proactive approve path (Jul 2026 — same
    # rationale as the four /api/preferred-artists/* handlers).
    try:
        from backend.utils import log_admin_action
        log_admin_action(
            db, user, "preferred_proactive_approve",
            target_table="preferred_artists", target_id=None,
            metadata={
                "venue_id": int(venue_id),
                "artist_id": int(artist_id),
                "artist_name": artist_info["name"],
                "venue_name": venue["venue_name"],
            },
        )
    except Exception:
        pass

    db.commit()
    return {"success": True, "message": f"{artist_info['name']} approved as preferred artist"}


# ==========================================
# PRO LICENSES - GET
# ==========================================
@router.get("/api/venues/{venue_id}/pro-licenses")
def get_pro_licenses(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    venue = db.execute(
        text("""SELECT 1 FROM venues v WHERE v.id = :vid AND (v.user_id = :uid
            OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid))"""),
        {"vid": venue_id, "uid": user.id}
    ).first()
    if not venue:
        raise HTTPException(403)
    
    licenses = db.execute(
        text("SELECT * FROM pro_licenses WHERE venue_id = :vid ORDER BY pro_name"),
        {"vid": venue_id}
    ).mappings().fetchall()
    return {"licenses": [dict(l) for l in licenses]}


# ==========================================
# PRO LICENSES - SAVE (upsert all at once)
# ==========================================
@router.put("/api/venues/{venue_id}/pro-licenses")
def save_pro_licenses(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    venue = db.execute(
        text("""SELECT 1 FROM venues v WHERE v.id = :vid AND (v.user_id = :uid
            OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid))"""),
        {"vid": venue_id, "uid": user.id}
    ).first()
    if not venue:
        raise HTTPException(403)
    
    now = utcnow_naive().isoformat()
    licenses = data.get("licenses", [])
    
    for lic in licenses:
        pro_name = lic.get("pro_name", "").strip()
        if not pro_name:
            continue
        license_number = lic.get("license_number", "").strip() or None
        expiration_date = lic.get("expiration_date", "").strip() or None
        
        db.execute(text("""
            INSERT INTO pro_licenses (venue_id, pro_name, license_number, expiration_date, updated_at)
            VALUES (:vid, :pro, :num, :exp, :now)
            ON CONFLICT(venue_id, pro_name) DO UPDATE SET
                license_number = :num, expiration_date = :exp, updated_at = :now
        """), {"vid": venue_id, "pro": pro_name, "num": license_number, "exp": expiration_date, "now": now})
    
    db.commit()
    return {"status": "saved"}


# ==========================================
# PRO LICENSE FILE UPLOAD
# ==========================================
from fastapi import UploadFile, File, Form
import shutil, uuid

@router.post("/api/venues/{venue_id}/pro-licenses/{pro_name}/upload")
def upload_pro_license(
    venue_id: int,
    pro_name: str,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    venue = db.execute(
        text("""SELECT 1 FROM venues v WHERE v.id = :vid AND (v.user_id = :uid
            OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid))"""),
        {"vid": venue_id, "uid": user.id}
    ).first()
    if not venue:
        raise HTTPException(403)

    import os, re as _re_pro
    # Audit fix (May 2026 part 8): four security upgrades.
    # 1. Whitelist `pro_name` to known PRO codes only — prevents path traversal
    #    via crafted pro_name (was used in the filename construction).
    # 2. Extension whitelist (was accepting any extension from the filename).
    # 3. 10 MB size cap (was unlimited → disk exhaustion DoS).
    # 4. Magic-byte check — file must actually be a PDF, not just .pdf extension.
    _PRO_ALLOWED = {'ascap', 'bmi', 'sesac', 'gmr', 'other'}
    _pro_lc = (pro_name or '').strip().lower()
    if _pro_lc not in _PRO_ALLOWED:
        raise HTTPException(400, "Invalid PRO name")

    # Read file content + size check
    content = file.file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "License file too large (max 10 MB)")

    # Extension + magic-byte check (PDFs start with %PDF-)
    raw_ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if raw_ext != "pdf":
        raise HTTPException(400, "License must be a PDF file (.pdf)")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(400, "File content is not a valid PDF")

    folder = f"app/static/uploads/venue/{venue_id}/pro_licenses"
    os.makedirs(folder, exist_ok=True)

    filename = f"{_pro_lc}_{uuid.uuid4().hex[:8]}.pdf"
    filepath = f"{folder}/{filename}"

    with open(filepath, "wb") as buffer:
        buffer.write(content)

    web_path = f"/{filepath}"
    now = utcnow_naive().isoformat()
    
    # Upsert license record with file path
    db.execute(text("""
        INSERT INTO pro_licenses (venue_id, pro_name, license_file_path, updated_at)
        VALUES (:vid, :pro, :path, :now)
        ON CONFLICT(venue_id, pro_name) DO UPDATE SET license_file_path = :path, updated_at = :now
    """), {"vid": venue_id, "pro": pro_name, "path": web_path, "now": now})
    
    db.commit()
    return {"status": "uploaded", "file_path": web_path}


# ── FLYER SETTINGS (in venues.py so they work even if flyers.py not registered) ──

@router.get("/api/venues/{venue_id}/settings/default-template")
def get_default_template_setting(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.execute(text(
        "SELECT default_flyer_template_id FROM venues WHERE id = :vid AND (user_id = :uid OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id = :vid AND eu.user_id = :uid))"
    ), {"vid": venue_id, "uid": user.id}).fetchone()
    if not row:
        raise HTTPException(404)
    tid = row._mapping.get("default_flyer_template_id")
    return {"template_id": tid}

@router.put("/api/venues/{venue_id}/settings/default-template")
async def set_default_template_setting(venue_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.execute(text(
        "SELECT id FROM venues WHERE id = :vid AND (user_id = :uid OR EXISTS (SELECT 1 FROM entity_users eu WHERE eu.entity_type='venue' AND eu.entity_id = :vid AND eu.user_id = :uid))"
    ), {"vid": venue_id, "uid": user.id}).fetchone()
    if not row:
        raise HTTPException(404)
    body = await request.json()
    tid = body.get("template_id") or None
    db.execute(text(
        "UPDATE venues SET default_flyer_template_id = :tid WHERE id = :vid"
    ), {"tid": tid, "vid": venue_id})
    if tid:
        db.execute(text("UPDATE venues SET auto_flyers = 1 WHERE id = :vid"), {"vid": venue_id})
    db.commit()
    return {"ok": True}


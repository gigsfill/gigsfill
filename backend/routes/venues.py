from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
from datetime import datetime
from backend.utils import utcnow_naive

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
def list_public_venues(db=Depends(get_db)):
    """Public endpoint for artists to discover venues"""
    rows = db.execute(
        text("""
            SELECT
                id,
                venue_name,
                description,
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
            ORDER BY venue_name ASC
        """)
    ).mappings().all()

    return rows

# v97: Public single venue endpoint for profile viewing
@router.get("/api/venues/{venue_id}/public")
def get_venue_public(venue_id: int, db=Depends(get_db)):
    """Public endpoint to view any venue profile"""
    row = db.execute(
        text("""
            SELECT
                id,
                user_id,
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
                pro_certified
            FROM venues
            WHERE id = :id
        """),
        {"id": venue_id}
    ).mappings().first()

    if not row:
        raise HTTPException(404)

    return dict(row)

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
def update_venue(venue_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    from backend.us_cities import find_city
    
    
    # v96: Check access via ownership OR entity_users
    current_venue = db.execute(
        text("""
            SELECT v.city, v.state FROM venues v
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
    current_city, current_state = current_venue

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
        "pro_certified": data.get("pro_certified"),
        "pro_certified_at": data.get("pro_certified_at"),
        "auto_flyers": data.get("auto_flyers"),
        "default_flyer_template_id": data.get("default_flyer_template_id"),
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
            pro_certified = COALESCE(:pro_certified, pro_certified),
            pro_certified_at = COALESCE(:pro_certified_at, pro_certified_at),
            auto_flyers = COALESCE(:auto_flyers, auto_flyers),
            default_flyer_template_id = COALESCE(:default_flyer_template_id, default_flyer_template_id)
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
    return {"ok": True}

@router.get("/venues/{venue_id}/preferred-requests")
def list_preferred_requests(
    venue_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    venue = db.execute(
        text("SELECT user_id FROM venues WHERE id=:id"),
        {"id": venue_id}
    ).mappings().first()

    if not venue or venue["user_id"] != user.id:
        raise HTTPException(403)

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
    user=Depends(get_current_user),
    db=Depends(get_db)
):
    """Get all preferred artists for a venue"""
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
    
    # Get all preferred artists with full data
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
                a.state
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

    return result

@router.delete("/api/venues/{venue_id}")
def delete_venue(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Delete venue and all associated data"""
    import shutil
    from pathlib import Path
    
    try:
        # Verify ownership
        venue = db.execute(
            text("SELECT user_id FROM venues WHERE id = :vid"),
            {"vid": venue_id}
        ).first()
        
        if not venue or venue[0] != user.id:
            raise HTTPException(403, "Not authorized")
        
        # Delete venue (cascades to gigs, media, etc)
        db.execute(text("DELETE FROM venues WHERE id = :vid"), {"vid": venue_id})
        
        # Delete media folder
        media_path = Path(f"media/venue_{venue_id}")
        if media_path.exists():
            shutil.rmtree(media_path)
        
        db.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, "Failed to delete. Please try again.")

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
    
    import os
    folder = f"app/static/uploads/venue/{venue_id}/pro_licenses"
    os.makedirs(folder, exist_ok=True)
    
    ext = file.filename.split(".")[-1] if "." in file.filename else "pdf"
    filename = f"{pro_name.lower()}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = f"{folder}/{filename}"
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
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


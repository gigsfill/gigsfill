"""
Flyer Routes — Canvas-based flyer editor for gig promotion.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
import logging, json, os, re, uuid
from sqlalchemy import text
from datetime import datetime, timedelta
from backend.utils import utcnow_naive

logger = logging.getLogger("gigsfill.flyers")
from backend.db import get_db
from backend.routes.auth import get_current_user

router = APIRouter()
UPLOAD_DIR = "app/static/uploads/flyers"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def check_venue_access(db, venue_id: int, user_id: int):
    row = db.execute(text("""
        SELECT 1 FROM venues v WHERE v.id = :vid AND (
            v.user_id = :uid OR EXISTS (
                SELECT 1 FROM entity_users eu
                WHERE eu.entity_type = 'venue' AND eu.entity_id = v.id AND eu.user_id = :uid
            ))
    """), {"vid": venue_id, "uid": user_id}).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Access denied")

def cleanup_old_flyers(db):
    cutoff = (utcnow_naive() - timedelta(days=366)).isoformat()
    db.execute(text("DELETE FROM flyers WHERE is_template = 0 AND created_at < :cutoff"), {"cutoff": cutoff})
    db.commit()

# ── AUTO-CREATE / AUTO-UPDATE HELPERS (called from gigs.py) ──

def auto_create_flyer(db, gig_id: int, venue_id: int):
    """Create a placeholder flyer when a gig is created (if venue has auto_flyers enabled)."""
    try:
        # Check if venue has auto_flyers enabled OR a default template selected
        venue = db.execute(text(
            "SELECT venue_name, COALESCE(auto_flyers, 0) as auto_flyers, default_flyer_template_id FROM venues WHERE id = :vid"
        ), {"vid": venue_id}).fetchone()
        if not venue:
            return
        v_map = venue._mapping
        # Create flyer if auto_flyers is on OR venue has a template selected
        if not v_map.get("auto_flyers") and not v_map.get("default_flyer_template_id"):
            return
        # Check no flyer already exists for this gig
        existing = db.execute(text(
            "SELECT id FROM flyers WHERE gig_id = :gid AND is_template = 0 LIMIT 1"
        ), {"gid": gig_id}).fetchone()
        if existing:
            return
        # Get gig details for naming
        gig = db.execute(text(
            "SELECT date, start_time, end_time FROM gigs WHERE id = :gid"
        ), {"gid": gig_id}).fetchone()
        if not gig:
            return
        g = gig._mapping
        v_name = re.sub(r'[^a-zA-Z0-9 ]', '', v_map.get("venue_name") or "Venue").replace(' ', '_')
        date_part = (g.get("date") or "").replace('-', '_')
        st = (g.get("start_time") or "")[:5].replace(':', '')
        et = (g.get("end_time") or "")[:5].replace(':', '')
        flyer_name = f"{v_name}_{date_part}_{st}_{et}_Open"
        
        # Try to use venue's chosen default template, then named venue default, then empty
        template = None
        # 1. Check if venue has a specific template chosen
        chosen_id = v_map.get("default_flyer_template_id")
        if chosen_id:
            template = db.execute(text("""
                SELECT canvas_data, thumbnail_data, size_preset, width, height FROM flyers 
                WHERE id = :tid AND venue_id = :vid AND is_template = 1
            """), {"tid": chosen_id, "vid": venue_id}).fetchone()
        
        # 2. Fall back to VenueName_Default Template
        if not template:
            venue_tpl_name = f"{v_map.get('venue_name', 'Venue')}_Default Template"
            template = db.execute(text("""
                SELECT canvas_data, thumbnail_data, size_preset, width, height FROM flyers 
                WHERE venue_id = :vid AND is_template = 1 AND name = :tname
                ORDER BY updated_at DESC LIMIT 1
            """), {"vid": venue_id, "tname": venue_tpl_name}).fetchone()
        
        # Store empty canvas_data — the JS always loads the template fresh at open time.
        # This ensures: (a) the title shows the template name not a gig-specific name,
        # (b) template updates apply to all un-customized gigs automatically,
        # (c) hydrateTemplateVars() always runs with current gigInfo.
        size_preset = template._mapping["size_preset"] if template else 'instagram_post'
        width = template._mapping["width"] if template else 1080
        height = template._mapping["height"] if template else 1350
        
        db.execute(text("""
            INSERT INTO flyers (venue_id, gig_id, artist_id, name, canvas_data, thumbnail_data,
                                is_template, size_preset, width, height)
            VALUES (:vid, :gid, NULL, :name, '{}', '', 0, :preset, :w, :h)
        """), {"vid": venue_id, "gid": gig_id, "name": flyer_name,
               "preset": size_preset, "w": width, "h": height})
        db.commit()
        logger.info(f"Auto-created flyer placeholder for gig {gig_id}")
    except Exception as e:
        logger.error(f"auto_create_flyer error for gig {gig_id}: {e}")

def auto_update_flyer_artist(db, gig_id: int, artist_id: int):
    """Update flyer name + artist_id when a gig is booked. If no flyer exists, create one first."""
    try:
        # Get the flyer for this gig
        flyer = db.execute(text(
            "SELECT id, venue_id FROM flyers WHERE gig_id = :gid AND is_template = 0 LIMIT 1"
        ), {"gid": gig_id}).fetchone()
        
        # If no flyer exists, try to create one first
        if not flyer:
            gig_info = db.execute(text(
                "SELECT venue_id FROM gigs WHERE id = :gid"
            ), {"gid": gig_id}).fetchone()
            if gig_info:
                vid = gig_info._mapping["venue_id"]
                # Force-create even if auto_flyers not enabled (artist just booked, make a flyer)
                _force_create_flyer(db, gig_id, vid)
                # Re-fetch
                flyer = db.execute(text(
                    "SELECT id, venue_id FROM flyers WHERE gig_id = :gid AND is_template = 0 LIMIT 1"
                ), {"gid": gig_id}).fetchone()
            if not flyer:
                return
        
        f = flyer._mapping
        # Get artist name + gig details for naming
        info = db.execute(text("""
            SELECT g.date, g.start_time, g.end_time, v.venue_name, a.name as artist_name
            FROM gigs g JOIN venues v ON g.venue_id = v.id LEFT JOIN artists a ON a.id = :aid
            WHERE g.id = :gid
        """), {"gid": gig_id, "aid": artist_id}).fetchone()
        if not info:
            return
        i = info._mapping
        v_name = re.sub(r'[^a-zA-Z0-9 ]', '', i.get("venue_name") or "Venue").replace(' ', '_')
        a_name = re.sub(r'[^a-zA-Z0-9 ]', '', i.get("artist_name") or "Open").replace(' ', '_')
        date_part = (i.get("date") or "").replace('-', '_')
        st = (i.get("start_time") or "")[:5].replace(':', '')
        et = (i.get("end_time") or "")[:5].replace(':', '')
        flyer_name = f"{v_name}_{date_part}_{st}_{et}_{a_name}"
        # Update flyer — keep canvas_data intact so template layout is preserved
        # Frontend hydrateTemplateVars() will swap in new artist logo/name on load
        db.execute(text("""
            UPDATE flyers SET artist_id = :aid, name = :name,
                             updated_at = CURRENT_TIMESTAMP
            WHERE id = :fid
        """), {"aid": artist_id, "name": flyer_name, "fid": f["id"]})
        db.commit()
        logger.info(f"Auto-updated flyer for gig {gig_id} with artist {artist_id}")
    except Exception as e:
        logger.error(f"auto_update_flyer_artist error for gig {gig_id}: {e}")


def _force_create_flyer(db, gig_id: int, venue_id: int):
    """Create a flyer for a gig using venue's template, regardless of auto_flyers setting."""
    try:
        existing = db.execute(text(
            "SELECT id FROM flyers WHERE gig_id = :gid AND is_template = 0 LIMIT 1"
        ), {"gid": gig_id}).fetchone()
        if existing:
            return
        
        venue = db.execute(text(
            "SELECT venue_name, default_flyer_template_id FROM venues WHERE id = :vid"
        ), {"vid": venue_id}).fetchone()
        if not venue:
            return
        v = venue._mapping
        
        gig = db.execute(text(
            "SELECT date, start_time, end_time FROM gigs WHERE id = :gid"
        ), {"gid": gig_id}).fetchone()
        if not gig:
            return
        g = gig._mapping
        v_name = re.sub(r'[^a-zA-Z0-9 ]', '', v.get("venue_name") or "Venue").replace(' ', '_')
        date_part = (g.get("date") or "").replace('-', '_')
        st = (g.get("start_time") or "")[:5].replace(':', '')
        et = (g.get("end_time") or "")[:5].replace(':', '')
        flyer_name = f"{v_name}_{date_part}_{st}_{et}_Open"
        
        # Try venue's chosen template, then named default, then empty
        template = None
        chosen_id = v.get("default_flyer_template_id")
        if chosen_id:
            template = db.execute(text("""
                SELECT canvas_data, thumbnail_data, size_preset, width, height FROM flyers 
                WHERE id = :tid AND venue_id = :vid AND is_template = 1
            """), {"tid": chosen_id, "vid": venue_id}).fetchone()
        
        if not template:
            venue_tpl_name = f"{v.get('venue_name')}_Default Template"
            template = db.execute(text("""
                SELECT canvas_data, thumbnail_data, size_preset, width, height FROM flyers 
                WHERE venue_id = :vid AND is_template = 1 AND name = :tname
                ORDER BY updated_at DESC LIMIT 1
            """), {"vid": venue_id, "tname": venue_tpl_name}).fetchone()
        
        size_preset = template._mapping["size_preset"] if template else 'instagram_post'
        width = template._mapping["width"] if template else 1080
        height = template._mapping["height"] if template else 1350
        
        db.execute(text("""
            INSERT INTO flyers (venue_id, gig_id, artist_id, name, canvas_data, thumbnail_data,
                                is_template, size_preset, width, height)
            VALUES (:vid, :gid, NULL, :name, '{}', '', 0, :preset, :w, :h)
        """), {"vid": venue_id, "gid": gig_id, "name": flyer_name,
               "preset": size_preset, "w": width, "h": height})
        db.commit()
        logger.info(f"Force-created flyer placeholder for gig {gig_id}")
    except Exception as e:
        logger.error(f"_force_create_flyer error for gig {gig_id}: {e}")

# --- LIST FLYERS ---
@router.get("/api/venues/{venue_id}/flyers")
async def list_flyers(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    try: cleanup_old_flyers(db)
    except: pass
    rows = db.execute(text("""
        SELECT f.*, g.date as gig_date, g.start_time, g.end_time, a.name as artist_name
        FROM flyers f LEFT JOIN gigs g ON f.gig_id = g.id LEFT JOIN artists a ON f.artist_id = a.id
        WHERE f.venue_id = :vid AND f.is_template = 0 ORDER BY f.updated_at DESC
    """), {"vid": venue_id}).fetchall()
    return [dict(r._mapping) for r in rows]

# --- SEARCH FLYERS ---
@router.get("/api/venues/{venue_id}/flyers/search")
async def search_flyers(venue_id: int, q: str = "", user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    rows = db.execute(text("""
        SELECT f.id, f.name, f.thumbnail_data, f.updated_at, f.gig_id,
               g.date as gig_date, g.start_time, g.end_time, a.name as artist_name
        FROM flyers f LEFT JOIN gigs g ON f.gig_id = g.id LEFT JOIN artists a ON f.artist_id = a.id
        WHERE f.venue_id = :vid AND f.is_template = 0
          AND (f.name LIKE :q OR COALESCE(a.name,'') LIKE :q OR COALESCE(g.date,'') LIKE :q)
        ORDER BY f.updated_at DESC LIMIT 50
    """), {"vid": venue_id, "q": f"%{q}%"}).fetchall()
    return [dict(r._mapping) for r in rows]

# --- LIST TEMPLATES ---
@router.get("/api/venues/{venue_id}/flyer-templates")
async def list_templates(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    rows = db.execute(text("""
        SELECT id, name, thumbnail_data, updated_at FROM flyers
        WHERE venue_id = :vid AND is_template = 1 ORDER BY updated_at DESC
    """), {"vid": venue_id}).fetchall()
    return [dict(r._mapping) for r in rows]

# --- GIG INFO (with multi-slot) ---
@router.get("/api/venues/{venue_id}/flyers/gig-info/{gig_id}")
async def get_gig_info_for_flyer(venue_id: int, gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    row = db.execute(text("""
        SELECT g.id, g.date, g.start_time, g.end_time, g.pay, g.status,
               g.artist_id, g.is_multi_slot,
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
        WHERE g.id = :gid AND g.venue_id = :vid
    """), {"gid": gig_id, "vid": venue_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Gig not found")
    result = dict(row._mapping)
    if result.get("is_multi_slot"):
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
    else:
        result["slots"] = []
    return result

# --- UPLOAD IMAGE ---
@router.post("/api/venues/{venue_id}/flyers/upload-image")
async def upload_flyer_image(venue_id: int, file: UploadFile = File(...), user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    ext = os.path.splitext(file.filename or "image.png")[1].lower()
    if ext not in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]:
        raise HTTPException(status_code=400, detail="Unsupported image format")
    venue_dir = os.path.join(UPLOAD_DIR, str(venue_id))
    os.makedirs(venue_dir, exist_ok=True)
    fname = f"{uuid.uuid4().hex[:12]}{ext}"
    fpath = os.path.join(venue_dir, fname)
    content = await file.read()
    with open(fpath, "wb") as f:
        f.write(content)
    return {"url": f"/app/static/uploads/flyers/{venue_id}/{fname}", "filename": fname}

# --- UPSERT DEFAULT TEMPLATE (backend handles find + update/create) ---
@router.put("/api/venues/{venue_id}/flyers/default-template")
async def upsert_default_template(venue_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    body = await request.json()
    tpl_name = body.get("name", "Default Template")
    canvas_data = body.get("canvas_data", "{}")
    if isinstance(canvas_data, dict): canvas_data = json.dumps(canvas_data)
    
    # Find existing venue default template (match by name OR legacy 'Default Template')
    existing = db.execute(text("""
        SELECT id FROM flyers WHERE venue_id = :vid AND is_template = 1 
        AND (name = :name OR LOWER(name) = 'default template')
        ORDER BY CASE WHEN name = :name THEN 0 ELSE 1 END, updated_at DESC LIMIT 1
    """), {"vid": venue_id, "name": tpl_name}).fetchone()
    
    if existing:
        db.execute(text("""
            UPDATE flyers SET name = :name, canvas_data = :canvas, thumbnail_data = :thumb,
                   size_preset = :preset, width = :w, height = :h, updated_at = CURRENT_TIMESTAMP
            WHERE id = :fid
        """), {"fid": existing[0], "name": tpl_name, "canvas": canvas_data, "thumb": body.get("thumbnail_data", ""),
               "preset": body.get("size_preset", "instagram_post"), "w": body.get("width", 1080), "h": body.get("height", 1350)})
        db.commit()
        return {"id": existing[0], "message": "Venue default template updated"}
    else:
        result = db.execute(text("""
            INSERT INTO flyers (venue_id, name, canvas_data, thumbnail_data, is_template, size_preset, width, height)
            VALUES (:vid, :name, :canvas, :thumb, 1, :preset, :w, :h)
        """), {"vid": venue_id, "name": tpl_name, "canvas": canvas_data, "thumb": body.get("thumbnail_data", ""),
               "preset": body.get("size_preset", "instagram_post"), "w": body.get("width", 1080), "h": body.get("height", 1350)})
        db.commit()
        return {"id": result.lastrowid, "message": "Venue default template created"}

# --- GET SINGLE FLYER ---
@router.get("/api/venues/{venue_id}/flyers/{flyer_id}")
async def get_flyer(venue_id: int, flyer_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    row = db.execute(text("""
        SELECT f.*, g.date as gig_date, g.start_time, g.end_time, a.name as artist_name
        FROM flyers f LEFT JOIN gigs g ON f.gig_id = g.id LEFT JOIN artists a ON f.artist_id = a.id
        WHERE f.id = :fid AND f.venue_id = :vid
    """), {"fid": flyer_id, "vid": venue_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flyer not found")
    return dict(row._mapping)

# --- CREATE FLYER ---
@router.post("/api/venues/{venue_id}/flyers")
async def create_flyer(venue_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    body = await request.json()
    canvas_data = body.get("canvas_data", "{}")
    if isinstance(canvas_data, dict): canvas_data = json.dumps(canvas_data)
    result = db.execute(text("""
        INSERT INTO flyers (venue_id, gig_id, artist_id, name, canvas_data, thumbnail_data,
                            is_template, size_preset, width, height)
        VALUES (:vid, :gid, :aid, :name, :canvas, :thumb, :tmpl, :preset, :w, :h)
    """), {
        "vid": venue_id, "gid": body.get("gig_id"), "aid": body.get("artist_id"),
        "name": body.get("name", "Untitled Flyer"), "canvas": canvas_data,
        "thumb": body.get("thumbnail_data", ""),
        "tmpl": 1 if body.get("is_template") else 0,
        "preset": body.get("size_preset", "instagram_post"),
        "w": body.get("width", 1080), "h": body.get("height", 1350)
    })
    db.commit()
    return {"id": result.lastrowid, "message": "Flyer created"}

# --- UPDATE FLYER ---
@router.put("/api/venues/{venue_id}/flyers/{flyer_id}")
async def update_flyer(venue_id: int, flyer_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    body = await request.json()
    existing = db.execute(text("SELECT id FROM flyers WHERE id = :fid AND venue_id = :vid"),
                          {"fid": flyer_id, "vid": venue_id}).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Flyer not found")
    fields, params = [], {"fid": flyer_id}
    for key in ["name", "canvas_data", "thumbnail_data", "is_template", "size_preset", "width", "height", "gig_id", "artist_id"]:
        if key in body:
            val = body[key]
            if key == "canvas_data" and isinstance(val, dict): val = json.dumps(val)
            if key == "is_template": val = 1 if val else 0
            fields.append(f"{key} = :{key}")
            params[key] = val
    if fields:
        fields.append("updated_at = CURRENT_TIMESTAMP")
        db.execute(text(f"UPDATE flyers SET {', '.join(fields)} WHERE id = :fid"), params)
        db.commit()
    return {"message": "Flyer updated"}

# --- DELETE FLYER ---
@router.delete("/api/venues/{venue_id}/flyers/{flyer_id}")
async def delete_flyer(venue_id: int, flyer_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    db.execute(text("DELETE FROM flyers WHERE id = :fid AND venue_id = :vid"),
               {"fid": flyer_id, "vid": venue_id})
    db.commit()
    return {"message": "Flyer deleted"}

# --- CHECK FLYER FOR GIG ---
@router.get("/api/gigs/{gig_id}/flyer")
async def get_flyer_for_gig(gig_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    row = db.execute(text("""
        SELECT f.id, f.name, f.thumbnail_data, f.updated_at, f.venue_id
        FROM flyers f WHERE f.gig_id = :gid AND f.is_template = 0
        ORDER BY f.updated_at DESC LIMIT 1
    """), {"gid": gig_id}).fetchone()
    if not row:
        return {"exists": False}
    d = dict(row._mapping)
    d["exists"] = True
    return d

# --- VENUE DEFAULT TEMPLATE SETTING ---
@router.get("/api/venues/{venue_id}/settings/default-template")
async def get_default_template_setting(venue_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    row = db.execute(text(
        "SELECT default_flyer_template_id FROM venues WHERE id = :vid"
    ), {"vid": venue_id}).fetchone()
    tid = row._mapping.get("default_flyer_template_id") if row else None
    return {"template_id": tid}

@router.put("/api/venues/{venue_id}/settings/default-template")
async def set_default_template_setting(venue_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    check_venue_access(db, venue_id, user.id)
    body = await request.json()
    tid = body.get("template_id") or None
    enable_auto = bool(body.get("auto_flyers", False))
    # Choosing any template (including site-wide default = tid NULL) enables auto_flyers
    if tid or enable_auto:
        db.execute(text(
            "UPDATE venues SET default_flyer_template_id = :tid, auto_flyers = 1 WHERE id = :vid"
        ), {"tid": tid, "vid": venue_id})
    else:
        db.execute(text(
            "UPDATE venues SET default_flyer_template_id = NULL, auto_flyers = 0 WHERE id = :vid"
        ), {"vid": venue_id})
    db.commit()
    return {"message": "Default template updated", "template_id": tid}

# NOTE: get_public_flyer (/api/gigs/{gig_id}/flyer/public) is defined in gigs.py
# That version is canonical - returns canvas_data, gig_info, and the full template cascade.
# Do NOT duplicate here; gigs.router is registered first in main.py.

@router.post("/api/flyers/ai-generate")
async def ai_generate_flyer(request: Request, user=Depends(get_current_user)):
    """Generate a flyer layout using Claude AI. API key must be set in ANTHROPIC_API_KEY env var."""
    import os, httpx
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "AI flyer generation is not configured (ANTHROPIC_API_KEY not set)")
    
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt is required")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4000,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
        if not resp.is_success:
            raise HTTPException(502, f"Claude API error: {resp.status_code}")
        data = resp.json()
        result = data.get("content", [{}])[0].get("text", "")
        return {"result": result}
    except httpx.TimeoutException:
        raise HTTPException(504, "Claude API timed out")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/api/flyers/proxy-image")
async def proxy_image(prompt: str, width: int = 1080, height: int = 1350, seed: int = 0):
    """Proxy Pollinations.ai image requests to avoid browser CORS restrictions.
    Uses only stdlib (urllib) — no extra dependencies required."""
    import urllib.parse, urllib.request, asyncio
    from fastapi.responses import Response

    encoded_prompt = urllib.parse.quote(prompt, safe="")
    pollinations_url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={width}&height={height}&seed={seed}&nologo=true"
    )

    def _fetch_sync():
        req = urllib.request.Request(
            pollinations_url,
            headers={"User-Agent": "Mozilla/5.0 GigsFill/1.0"}
        )
        # Pollinations can take 30-60s to generate; generous timeout
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
            ct = resp.headers.get_content_type() or "image/jpeg"
        return data, ct

    try:
        # Run the blocking urllib call in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()
        image_data, content_type = await loop.run_in_executor(None, _fetch_sync)

        if not content_type.startswith("image/"):
            raise HTTPException(502, f"Unexpected content type from Pollinations: {content_type}")

        return Response(
            content=image_data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=3600"}
        )
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(504, "Pollinations timed out — try again")
    except Exception as e:
        raise HTTPException(500, f"Proxy error: {str(e)}")
"""
Gig templates — save / load / apply reusable slot configurations.

Most venues run a small number of recurring "shapes" (e.g. "Friday Night
Live", "Saturday Brunch Acoustic"). Manually re-entering slot times,
pay, artist type, and lineup/styles for every new gig is tedious and
error-prone. Templates let the venue capture a slot config once and
apply it with one click.

Schema lives in db.py — `gig_templates(id, venue_id, name, slots_json,
notes, ...)`. `slots_json` is the same shape the FE sends to gig-create:
a JSON array of objects with start_time, end_time, pay, artist_type,
band_formats, styles.

Endpoints:
    GET    /api/venues/{venue_id}/gig-templates           — list
    POST   /api/venues/{venue_id}/gig-templates           — create
    PUT    /api/venues/{venue_id}/gig-templates/{tpl_id}  — rename / update slots
    DELETE /api/venues/{venue_id}/gig-templates/{tpl_id}  — remove
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.utils import check_venue_access

router = APIRouter()


def _validate_slots(slots_json):
    """Light validation — must be a list of dicts with start/end times.
    Pay is optional, artist_type/band_formats/styles are also optional
    (they default to empty in the FE)."""
    if not isinstance(slots_json, list) or not slots_json:
        raise HTTPException(400, "Template must contain at least one slot")
    if len(slots_json) > 20:
        raise HTTPException(400, "Template cannot contain more than 20 slots")
    for i, s in enumerate(slots_json, 1):
        if not isinstance(s, dict):
            raise HTTPException(400, f"Slot {i} is malformed")
        st = s.get("start_time")
        et = s.get("end_time")
        if not (isinstance(st, str) and isinstance(et, str)):
            raise HTTPException(400, f"Slot {i} needs start_time and end_time")
    return slots_json


@router.get("/api/venues/{venue_id}/gig-templates")
def list_templates(venue_id: int,
                   user=Depends(get_current_user), db=Depends(get_db)):
    """List all templates for a venue (most recent first)."""
    check_venue_access(db, venue_id, user.id)
    rows = db.execute(
        text("""
            SELECT id, name, notes, slots_json, created_at, updated_at
            FROM gig_templates
            WHERE venue_id = :vid
            ORDER BY updated_at DESC, id DESC
        """),
        {"vid": venue_id}
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["slots"] = json.loads(d.pop("slots_json"))
        except Exception:
            d["slots"] = []
            d.pop("slots_json", None)
        out.append(d)
    return {"templates": out}


@router.post("/api/venues/{venue_id}/gig-templates")
def create_template(venue_id: int, data: dict,
                    user=Depends(get_current_user), db=Depends(get_db)):
    """Create a new gig template."""
    check_venue_access(db, venue_id, user.id)
    name = str(data.get("name") or "").strip()[:120]
    notes = str(data.get("notes") or "").strip()[:500]
    slots = _validate_slots(data.get("slots"))
    if not name:
        raise HTTPException(400, "Template name is required")
    # Cap at 50 templates per venue — well above any realistic need.
    cnt = db.execute(
        text("SELECT COUNT(*) FROM gig_templates WHERE venue_id = :vid"),
        {"vid": venue_id}
    ).scalar() or 0
    if cnt >= 50:
        raise HTTPException(400, "Maximum 50 templates per venue — delete an old one first")
    res = db.execute(
        text("""
            INSERT INTO gig_templates (venue_id, name, slots_json, notes, created_by_user_id)
            VALUES (:vid, :name, :slots, :notes, :uid)
        """),
        {"vid": venue_id, "name": name, "slots": json.dumps(slots),
         "notes": notes, "uid": user.id}
    )
    db.commit()
    new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
    return {"ok": True, "id": new_id, "name": name}


@router.put("/api/venues/{venue_id}/gig-templates/{tpl_id}")
def update_template(venue_id: int, tpl_id: int, data: dict,
                    user=Depends(get_current_user), db=Depends(get_db)):
    """Rename, retype notes, or replace the slot config of a template."""
    check_venue_access(db, venue_id, user.id)
    row = db.execute(
        text("SELECT id FROM gig_templates WHERE id = :tid AND venue_id = :vid"),
        {"tid": tpl_id, "vid": venue_id}
    ).first()
    if not row:
        raise HTTPException(404, "Template not found")

    sets = []
    params = {"tid": tpl_id, "vid": venue_id}
    if "name" in data:
        name = str(data["name"] or "").strip()[:120]
        if not name:
            raise HTTPException(400, "Template name cannot be empty")
        sets.append("name = :name")
        params["name"] = name
    if "notes" in data:
        sets.append("notes = :notes")
        params["notes"] = str(data["notes"] or "").strip()[:500]
    if "slots" in data:
        slots = _validate_slots(data["slots"])
        sets.append("slots_json = :slots")
        params["slots"] = json.dumps(slots)
    if not sets:
        raise HTTPException(400, "Nothing to update")
    sets.append("updated_at = CURRENT_TIMESTAMP")
    db.execute(
        text(f"UPDATE gig_templates SET {', '.join(sets)} "
             "WHERE id = :tid AND venue_id = :vid"),
        params
    )
    db.commit()
    return {"ok": True}


@router.delete("/api/venues/{venue_id}/gig-templates/{tpl_id}")
def delete_template(venue_id: int, tpl_id: int,
                    user=Depends(get_current_user), db=Depends(get_db)):
    """Remove a template. Doesn't affect any gigs already created from it."""
    check_venue_access(db, venue_id, user.id)
    db.execute(
        text("DELETE FROM gig_templates WHERE id = :tid AND venue_id = :vid"),
        {"tid": tpl_id, "vid": venue_id}
    )
    db.commit()
    return {"ok": True}

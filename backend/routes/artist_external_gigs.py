"""
Artist-owned external gigs (gigs at venues NOT on GigsFill).
================================================================

The artist adds these from their own calendar so their profile shows a
complete schedule. They're private to the artist (only surfaced on the
artist's own book-gigs calendar + their public artist profile calendar)
— never on venue calendars or the city-wide public calendar.

Auth: uses check_artist_access — owner + any entity_users team member
of the artist can create/edit/delete.

Endpoints:
  GET    /api/artists/{artist_id}/external-gigs?from=YYYY-MM-DD&to=YYYY-MM-DD
  POST   /api/artists/{artist_id}/external-gigs
  PUT    /api/artists/{artist_id}/external-gigs/{ext_id}
  DELETE /api/artists/{artist_id}/external-gigs/{ext_id}

  # 2026-08-02 flyer:
  POST   /api/artists/{artist_id}/external-gigs/{ext_id}/flyer   (multipart, 3MB cap, jpg/png)
  DELETE /api/artists/{artist_id}/external-gigs/{ext_id}/flyer
  GET    /api/artists/{artist_id}/external-gigs/{ext_id}/flyer          (auth)
  GET    /api/public/artists/{artist_id}/external-gigs/{ext_id}/flyer   (gated on flyer_public=1)
"""
import json, os, pathlib, mimetypes
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import text
from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.utils import check_artist_access, utcnow_naive

router = APIRouter()

# Flyer storage config. 3MB is generous for a JPEG at 1080×1350 (Instagram
# portrait aspect). PNG cap is the same — most flyer designs come out of
# Canva at ~500KB compressed, so 3MB is the paranoid upper bound.
_FLYER_ROOT = pathlib.Path("/opt/gigsfill/uploads/ext_flyers")
_FLYER_MAX_BYTES = 3 * 1024 * 1024
_FLYER_ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png"}


def _flyer_dir(artist_id: int) -> pathlib.Path:
    d = _FLYER_ROOT / str(artist_id)
    d.mkdir(parents=True, exist_ok=True)
    return d

# Whitelists so an artist can't inject arbitrary strings into the tag columns.
# Keep in sync with the pill lists in artist-book-gigs.html + the venue Create
# Gig modal (venue.create-gigs.js _VCG_STYLE_OPTIONS).
_ARTIST_TYPES = {"Live Band", "DJ", "Comedian", "Trivia Host", "Open Mic MC", "Karaoke MC"}
_LINEUPS      = {"Solo", "Duo", "Trio", "Full Band"}
_STYLES       = {"Country", "Hip-Hop", "Indie", "Jazz", "Latin", "Pop", "Reggae", "Rock"}


def _clean_tag_list(v, allowed: set) -> str | None:
    """Accept a list-of-strings or a JSON string; filter by allowed set; return
    JSON string suitable for the *_json column, or None when empty."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return None
    if not isinstance(v, list):
        return None
    cleaned = [s for s in v if isinstance(s, str) and s in allowed]
    return json.dumps(cleaned) if cleaned else None


def _validate_payload(data: dict, require_all: bool = True) -> dict:
    """Return a cleaned {col: value} dict; raise 400 on missing/malformed.
    `require_all=True` for POST (all required fields must be present),
    `False` for PUT (any subset of updatable fields).
    """
    out = {}
    # Text fields — trim + length-cap for defensive DB safety
    for col, maxlen in (
        ("venue_name", 200), ("venue_city", 120), ("venue_state", 40),
        ("venue_address", 300), ("notes", 2000),
    ):
        if col in data:
            v = str(data.get(col) or "").strip()[:maxlen]
            out[col] = v or None
    if require_all and not out.get("venue_name"):
        raise HTTPException(400, "Venue name is required")
    if require_all and not out.get("venue_address"):
        raise HTTPException(400, "Address is required")

    # Date — YYYY-MM-DD only. Reject anything else so calendar queries stay safe.
    if "date" in data:
        d = str(data.get("date") or "").strip()
        import re
        if d and not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            raise HTTPException(400, "Date must be YYYY-MM-DD")
        out["date"] = d or None
    if require_all and not out.get("date"):
        raise HTTPException(400, "Date is required")

    # Times — HH:MM optional. Empty → NULL.
    for col in ("start_time", "end_time"):
        if col in data:
            t = str(data.get(col) or "").strip()
            if t:
                import re
                if not re.match(r"^\d{2}:\d{2}$", t):
                    raise HTTPException(400, f"{col} must be HH:MM")
            out[col] = t or None

    # Artist Type — one of the whitelist (or None to clear)
    if "artist_type" in data:
        v = data.get("artist_type")
        if v is None or v == "":
            out["artist_type"] = None
        elif v in _ARTIST_TYPES:
            out["artist_type"] = v
        else:
            raise HTTPException(400, f"Invalid artist_type: {v}")

    # Lineup + Styles — arrays whitelisted then JSON-encoded to the *_json col
    if "lineup" in data:
        out["lineup_json"] = _clean_tag_list(data.get("lineup"), _LINEUPS)
    if "styles" in data:
        out["styles_json"] = _clean_tag_list(data.get("styles"), _STYLES)

    # Flyer public visibility toggle — accepts bool / 0-1 / "true"/"false"
    if "flyer_public" in data:
        v = data.get("flyer_public")
        if isinstance(v, bool):
            out["flyer_public"] = 1 if v else 0
        elif isinstance(v, (int, float)):
            out["flyer_public"] = 1 if v else 0
        elif isinstance(v, str):
            out["flyer_public"] = 1 if v.strip().lower() in ("1", "true", "yes", "on") else 0
        else:
            out["flyer_public"] = 1
    return out


def _row_to_dict(r) -> dict:
    """Expand JSON tag columns back to lists so the front-end doesn't parse.
    Also normalizes flyer fields: `flyer_url` (relative endpoint the front-end
    can use) and boolean `flyer_public` when present.
    """
    d = dict(r)
    d["lineup"] = json.loads(d.pop("lineup_json") or "[]") if "lineup_json" in d else []
    d["styles"] = json.loads(d.pop("styles_json") or "[]") if "styles_json" in d else []
    if "flyer_path" in d:
        # Don't leak the on-disk path — the frontend hits the auth'd or
        # public endpoint by (artist_id, ext_id). Convert to a boolean +
        # URL so callers can just `if (g.flyer_url)`.
        has = bool(d.pop("flyer_path"))
        aid = d.get("artist_id"); eid = d.get("id")
        if has and aid and eid:
            d["flyer_url"] = f"/api/artists/{aid}/external-gigs/{eid}/flyer"
            d["flyer_public_url"] = f"/api/public/artists/{aid}/external-gigs/{eid}/flyer"
        else:
            d["flyer_url"] = None
            d["flyer_public_url"] = None
    if "flyer_public" in d:
        d["flyer_public"] = bool(d["flyer_public"])
    return d


@router.get("/api/artists/{artist_id}/external-gigs")
def list_external_gigs(artist_id: int, from_date: str = "", to_date: str = "",
                       user=Depends(get_current_user), db=Depends(get_db)):
    """List external gigs for the artist. Optional date range filter.
    Returns rows in a shape the calendar renderer can consume alongside
    regular gigs — normalized so the front-end can just merge and render.
    """
    check_artist_access(db, artist_id, user.id)
    where = "WHERE artist_id = :aid"
    params = {"aid": artist_id}
    if from_date:
        where += " AND date >= :fromDate"; params["fromDate"] = from_date
    if to_date:
        where += " AND date <= :toDate";   params["toDate"] = to_date
    rows = db.execute(text(f"""
        SELECT id, artist_id, venue_name, venue_city, venue_state, venue_address,
               date, start_time, end_time, notes, artist_type, lineup_json, styles_json,
               flyer_path, flyer_public, flyer_uploaded_at,
               created_at, updated_at
        FROM artist_external_gigs
        {where}
        ORDER BY date ASC, start_time ASC
    """), params).mappings().all()
    return [_row_to_dict(r) for r in rows]


@router.post("/api/artists/{artist_id}/external-gigs")
def create_external_gig(artist_id: int, data: dict,
                         user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    payload = _validate_payload(data, require_all=True)
    now = utcnow_naive()
    row_id = db.execute(text("""
        INSERT INTO artist_external_gigs
            (artist_id, venue_name, venue_city, venue_state, venue_address,
             date, start_time, end_time, notes,
             artist_type, lineup_json, styles_json,
             created_by_user_id, created_at, updated_at)
        VALUES
            (:aid, :vn, :vc, :vs, :va, :date, :st, :et, :notes,
             :at, :lj, :sj,
             :uid, :now, :now)
        RETURNING id
    """), {
        "aid": artist_id,
        "vn": payload.get("venue_name"), "vc": payload.get("venue_city"),
        "vs": payload.get("venue_state"), "va": payload.get("venue_address"),
        "date": payload["date"],
        "st": payload.get("start_time"), "et": payload.get("end_time"),
        "notes": payload.get("notes"),
        "at": payload.get("artist_type"),
        "lj": payload.get("lineup_json"),
        "sj": payload.get("styles_json"),
        "uid": user.id, "now": now,
    }).scalar()
    db.commit()
    return {"ok": True, "id": row_id}


@router.put("/api/artists/{artist_id}/external-gigs/{ext_id}")
def update_external_gig(artist_id: int, ext_id: int, data: dict,
                         user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    # Assert the row belongs to this artist before mutating.
    existing = db.execute(
        text("SELECT id FROM artist_external_gigs WHERE id = :id AND artist_id = :aid"),
        {"id": ext_id, "aid": artist_id}
    ).scalar()
    if not existing:
        raise HTTPException(404, "External gig not found")
    payload = _validate_payload(data, require_all=False)
    if not payload:
        return {"ok": True, "id": ext_id, "message": "no changes"}
    sets = ", ".join(f"{col} = :{col}" for col in payload)
    payload["id"] = ext_id
    payload["now"] = utcnow_naive()
    db.execute(text(f"""
        UPDATE artist_external_gigs
           SET {sets}, updated_at = :now
         WHERE id = :id
    """), payload)
    db.commit()
    return {"ok": True, "id": ext_id}


@router.delete("/api/artists/{artist_id}/external-gigs/{ext_id}")
def delete_external_gig(artist_id: int, ext_id: int,
                         user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    # Clean up any uploaded flyer file first — DB delete cascades logically
    # but the file on disk is our responsibility.
    for e in _FLYER_ALLOWED.values():
        p = _flyer_dir(artist_id) / f"{ext_id}{e}"
        if p.exists():
            try: p.unlink()
            except Exception: pass
    n = db.execute(
        text("DELETE FROM artist_external_gigs WHERE id = :id AND artist_id = :aid"),
        {"id": ext_id, "aid": artist_id}
    ).rowcount
    db.commit()
    if not n:
        raise HTTPException(404, "External gig not found")
    return {"ok": True}


# Public read endpoint — used by the artist public profile calendar so
# visitors see the same gigs the artist sees. Same rows as the auth'd
# GET but no access check. No PII beyond what the artist chose to enter.
@router.get("/api/public/artists/{artist_id}/external-gigs")
def public_list_external_gigs(artist_id: int, from_date: str = "",
                               to_date: str = "", db=Depends(get_db)):
    where = "WHERE artist_id = :aid"
    params = {"aid": artist_id}
    if from_date:
        where += " AND date >= :fromDate"; params["fromDate"] = from_date
    if to_date:
        where += " AND date <= :toDate";   params["toDate"] = to_date
    # 2026-08-02: notes + venue_address now exposed publicly — the artist
    # explicitly consents when adding an external gig (create-modal
    # placeholder tells them notes are visible to the public).
    # Public listing filters out non-public flyers server-side so a
    # visitor never sees a `flyer_url` for a flyer the artist opted out
    # of publishing. artist_id is included so _row_to_dict can build the
    # /api/public/artists/{aid}/external-gigs/{id}/flyer URL.
    rows = db.execute(text(f"""
        SELECT id, artist_id, venue_name, venue_city, venue_state, venue_address,
               date, start_time, end_time, notes,
               artist_type, lineup_json, styles_json,
               CASE WHEN COALESCE(flyer_public,1) = 1 THEN flyer_path ELSE NULL END AS flyer_path,
               flyer_public, flyer_uploaded_at
        FROM artist_external_gigs
        {where}
        ORDER BY date ASC, start_time ASC
    """), params).mappings().all()
    return [_row_to_dict(r) for r in rows]


# ── CITY-WIDE PUBLIC LISTING ────────────────────────────────────────────────
# 2026-08-05: expose future external gigs on the city public calendar
# so an artist's non-GigsFill gigs show up alongside real bookings when
# someone browses their city. Lat/lon comes from us_cities.find_city()
# on (venue_city, venue_state) — street-level geocoding isn't available
# without a paid API and city-level is close enough for the 20 mile
# default radius the city calendar uses. Artist name + id are joined so
# the frontend can render the artist chip + link to the artist profile.
@router.get("/api/public/external-gigs")
def public_list_all_external_gigs(from_date: str = "",
                                    to_date: str = "", db=Depends(get_db)):
    """Return future external gigs from every artist, with lat/lon
    resolved from `us_cities.find_city(city, state)`. Client-side
    radius filtering on public-gigs.html reuses these coordinates
    against the visitor's chosen city."""
    from backend.us_cities import find_city
    # Default window: today → +90 days, matching /api/gigs/public.
    from datetime import date as _date, timedelta as _td
    if not from_date:
        from_date = _date.today().isoformat()
    if not to_date:
        to_date = (_date.today() + _td(days=90)).isoformat()
    rows = db.execute(text("""
        SELECT eg.id, eg.artist_id, eg.venue_name, eg.venue_city, eg.venue_state,
               eg.venue_address, eg.date, eg.start_time, eg.end_time, eg.notes,
               eg.artist_type, eg.lineup_json, eg.styles_json,
               CASE WHEN COALESCE(eg.flyer_public,1) = 1 THEN eg.flyer_path ELSE NULL END AS flyer_path,
               eg.flyer_public, eg.flyer_uploaded_at,
               a.name        AS artist_name,
               a.band_formats AS artist_band_formats,
               a.styles       AS artist_styles
        FROM artist_external_gigs eg
        JOIN artists a ON a.id = eg.artist_id AND a.deleted_at IS NULL
        WHERE eg.date >= :fromDate AND eg.date <= :toDate
        ORDER BY eg.date ASC, eg.start_time ASC
    """), {"fromDate": from_date, "toDate": to_date}).mappings().all()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        # Address requirement enforced at create time (see _validate_payload
        # — venue_address is required on POST). Any pre-Aug-2026 row
        # without one still comes through — we just render without coords.
        city  = (d.get("venue_city")  or "").strip()
        state = (d.get("venue_state") or "").strip()
        city_row = find_city(city, state) if city else None
        d["venue_lat"] = city_row["lat"] if city_row else None
        d["venue_lon"] = city_row["lon"] if city_row else None
        out.append(d)
    return out


# ── FLYER ENDPOINTS ─────────────────────────────────────────────────────────
def _load_flyer_row(db, artist_id: int, ext_id: int) -> dict:
    row = db.execute(text("""
        SELECT id, artist_id, flyer_path, COALESCE(flyer_public,1) as flyer_public
        FROM artist_external_gigs
        WHERE id = :id AND artist_id = :aid
    """), {"id": ext_id, "aid": artist_id}).mappings().first()
    if not row:
        raise HTTPException(404, "External gig not found")
    return dict(row)


@router.post("/api/artists/{artist_id}/external-gigs/{ext_id}/flyer")
async def upload_external_gig_flyer(
    artist_id: int, ext_id: int, file: UploadFile = File(...),
    user=Depends(get_current_user), db=Depends(get_db),
):
    """Upload a flyer image for an external gig. 3MB cap, JPG/PNG only.
    Overwrites any existing flyer for this gig.
    """
    check_artist_access(db, artist_id, user.id)
    _load_flyer_row(db, artist_id, ext_id)  # 404 if gig missing

    ctype = (file.content_type or "").lower()
    if ctype not in _FLYER_ALLOWED:
        raise HTTPException(400, "Flyer must be a JPG or PNG image")

    # Read in a bounded chunk so a malicious multipart body can't exhaust
    # memory. Read one byte past the limit so we can detect over-limit.
    body = await file.read(_FLYER_MAX_BYTES + 1)
    if len(body) > _FLYER_MAX_BYTES:
        raise HTTPException(400, "Flyer must be 3 MB or smaller")
    if len(body) < 100:
        raise HTTPException(400, "Flyer file appears empty or corrupt")

    ext = _FLYER_ALLOWED[ctype]
    dest = _flyer_dir(artist_id) / f"{ext_id}{ext}"
    # Remove any existing extension variant (e.g. was .png, now .jpg)
    for e in _FLYER_ALLOWED.values():
        stale = _flyer_dir(artist_id) / f"{ext_id}{e}"
        if stale.exists() and stale != dest:
            try: stale.unlink()
            except Exception: pass

    dest.write_bytes(body)
    now = utcnow_naive()
    rel = f"{artist_id}/{ext_id}{ext}"
    db.execute(text("""
        UPDATE artist_external_gigs
           SET flyer_path = :p, flyer_uploaded_at = :now, updated_at = :now
         WHERE id = :id AND artist_id = :aid
    """), {"p": rel, "now": now, "id": ext_id, "aid": artist_id})
    db.commit()
    return {
        "ok": True,
        "flyer_url":         f"/api/artists/{artist_id}/external-gigs/{ext_id}/flyer",
        "flyer_public_url":  f"/api/public/artists/{artist_id}/external-gigs/{ext_id}/flyer",
    }


@router.delete("/api/artists/{artist_id}/external-gigs/{ext_id}/flyer")
def delete_external_gig_flyer(artist_id: int, ext_id: int,
                               user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    row = _load_flyer_row(db, artist_id, ext_id)
    # Remove file (both possible extensions) then clear DB.
    for e in _FLYER_ALLOWED.values():
        p = _flyer_dir(artist_id) / f"{ext_id}{e}"
        if p.exists():
            try: p.unlink()
            except Exception: pass
    db.execute(text("""
        UPDATE artist_external_gigs
           SET flyer_path = NULL, flyer_uploaded_at = NULL, updated_at = :now
         WHERE id = :id AND artist_id = :aid
    """), {"now": utcnow_naive(), "id": ext_id, "aid": artist_id})
    db.commit()
    return {"ok": True}


def _serve_flyer_response(row: dict) -> FileResponse:
    rel = row.get("flyer_path")
    if not rel:
        raise HTTPException(404, "No flyer for this gig")
    path = _FLYER_ROOT / rel
    if not path.exists():
        raise HTTPException(404, "Flyer file missing")
    mt, _ = mimetypes.guess_type(str(path))
    return FileResponse(str(path), media_type=mt or "image/jpeg")


@router.get("/api/artists/{artist_id}/external-gigs/{ext_id}/flyer")
def serve_flyer_auth(artist_id: int, ext_id: int,
                      user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    return _serve_flyer_response(_load_flyer_row(db, artist_id, ext_id))


@router.get("/api/public/artists/{artist_id}/external-gigs/{ext_id}/flyer")
def serve_flyer_public(artist_id: int, ext_id: int, db=Depends(get_db)):
    row = _load_flyer_row(db, artist_id, ext_id)
    if not row.get("flyer_public"):
        raise HTTPException(404, "No flyer for this gig")
    return _serve_flyer_response(row)

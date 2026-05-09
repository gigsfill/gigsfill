from fastapi import APIRouter, UploadFile, File, Cookie, HTTPException, Form, Depends
from backend.db import SessionLocal
from backend.models import ArtistMedia, VenueMedia, Artist, Venue
from backend.routes.auth import verify_session_token, SESSION_COOKIE_NAME, get_current_user
from sqlalchemy import text
import os, shutil, uuid
from pydantic import BaseModel

router = APIRouter()

UPLOAD_ROOT = "app/static/uploads/artist"
VENUE_UPLOAD_ROOT = "app/static/uploads/venue"

# ============================================
# FILE UPLOAD SECURITY
# ============================================

# Allowed file extensions by media type
ALLOWED_EXTENSIONS = {
    "profile": {"jpg", "jpeg", "png", "gif", "webp"},
    "picture": {"jpg", "jpeg", "png", "gif", "webp"},
    "audio":   {"mp3", "wav", "m4a", "ogg", "flac", "aac"},
    "video":   set(),  # Video is URL-based, no file upload
}

# MIME type whitelist
ALLOWED_MIME_TYPES = {
    # Images
    "image/jpeg", "image/png", "image/gif", "image/webp",
    # Audio
    "audio/mpeg", "audio/wav", "audio/mp4", "audio/ogg", "audio/flac",
    "audio/aac", "audio/x-m4a", "audio/mp3",
}

# Max file sizes in bytes
MAX_FILE_SIZES = {
    "profile": 10 * 1024 * 1024,   # 10 MB
    "picture": 10 * 1024 * 1024,   # 10 MB
    "audio":   50 * 1024 * 1024,   # 50 MB
}


def validate_upload(file: UploadFile, media_type: str):
    """Validate an uploaded file for extension, MIME type, and size. Raises HTTPException on failure."""
    if not file or not file.filename:
        raise HTTPException(400, "File required")

    # Check extension
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    allowed = ALLOWED_EXTENSIONS.get(media_type, set())
    if ext not in allowed:
        raise HTTPException(400, f"File type '.{ext}' not allowed. Accepted: {', '.join(sorted(allowed))}")

    # Check MIME type (if provided by client — can be spoofed but still useful as first pass)
    if file.content_type and file.content_type.lower() not in ALLOWED_MIME_TYPES:
        raise HTTPException(400, f"File content type '{file.content_type}' not allowed")

    # Check file size by reading first chunk (don't load entire file into memory)
    max_size = MAX_FILE_SIZES.get(media_type, 10 * 1024 * 1024)
    file.file.seek(0, 2)  # Seek to end
    size = file.file.tell()
    file.file.seek(0)     # Reset to start
    if size > max_size:
        max_mb = max_size / (1024 * 1024)
        raise HTTPException(400, f"File too large. Maximum size: {max_mb:.0f} MB")

    return ext

class VideoCreate(BaseModel):
    video_url: str
    title: str | None = None

def artist_media_path(artist_id: int, media_type: str):
    return f"{UPLOAD_ROOT}/{artist_id}/{media_type}"

def venue_media_path(venue_id: int, media_type: str):
    return f"{VENUE_UPLOAD_ROOT}/{venue_id}/{media_type}"

def user_can_access_artist(db, artist_id: int, user_id: int) -> bool:
    """Check if user owns artist OR has entity_users access"""
    result = db.execute(
        text("""
            SELECT 1 FROM artists a
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
        {"aid": artist_id, "uid": user_id}
    ).scalar()
    return result is not None

def user_can_access_venue(db, venue_id: int, user_id: int) -> bool:
    """Check if user owns venue OR has entity_users access"""
    result = db.execute(
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
        {"vid": venue_id, "uid": user_id}
    ).scalar()
    return result is not None

# -----------------------------------------
# GET ARTIST MEDIA (JSON SAFE)
# -----------------------------------------
@router.get("/api/artists/{artist_id}/media")
def get_artist_media(artist_id: int):
    db = SessionLocal()

    rows = (
        db.query(ArtistMedia)
        .filter(ArtistMedia.artist_id == artist_id)
        .order_by(ArtistMedia.display_order)
        .all()
    )

    db.close()

    # 🚨 MUST RETURN JSON-SERIALIZABLE DATA
    return [
        {
            "id": m.id,
            "artist_id": m.artist_id,
            "media_type": m.media_type,
            "title": m.title,
            "file_path": m.file_path,
            "video_url": m.video_url,
            "display_order": m.display_order,
        }
        for m in rows
    ]

# -----------------------------------------
# UPLOAD PICTURE / AUDIO
# -----------------------------------------

@router.post("/api/artists/{artist_id}/media/{media_type}")
def upload_media(
    artist_id: int,
    media_type: str,
    file: UploadFile | None = File(None),
    video_url: str | None = Form(None),
    title: str | None = Form(None),
    session_token: str | None = Cookie(default=None)
):

    if not session_token:
        raise HTTPException(401, "Not logged in")
    
    user_id = verify_session_token(session_token)

    if media_type not in ["profile", "picture", "audio", "video"]:
        raise HTTPException(400, "Invalid media type")

    db = SessionLocal()

    # Check ownership OR entity_users access
    if not user_can_access_artist(db, artist_id, user_id):
        db.close()
        raise HTTPException(403, "You don't have access to this artist")

    order = (
        db.query(ArtistMedia)
        .filter(ArtistMedia.artist_id == artist_id)
        .count()
    )

    # PROFILE / PICTURE / AUDIO (file-based)
    if media_type != "video":
        # Validate file: extension whitelist, MIME type, size limit
        ext = validate_upload(file, media_type)

        folder = artist_media_path(artist_id, media_type)
        os.makedirs(folder, exist_ok=True)

        filename = f"{uuid.uuid4()}.{ext}"
        path = f"{folder}/{filename}"

        with open(path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        if media_type == "profile":
            # Delete old profile file(s) from disk before replacing
            old_profiles = db.query(ArtistMedia).filter(
                ArtistMedia.artist_id == artist_id,
                ArtistMedia.media_type == "profile"
            ).all()
            for old in old_profiles:
                if old.file_path:
                    old_file = old.file_path.lstrip("/")
                    if os.path.exists(old_file):
                        os.remove(old_file)
            db.query(ArtistMedia).filter(
                ArtistMedia.artist_id == artist_id,
                ArtistMedia.media_type == "profile"
            ).delete()

        media = ArtistMedia(
            artist_id=artist_id,
            media_type=media_type,
            file_path=f"/{path}",
            title=title,
            display_order=order
        )

    # VIDEO (URL-based)
    else:
        if not video_url:
            db.close()
            raise HTTPException(400, "video_url required")

        media = ArtistMedia(
            artist_id=artist_id,
            media_type="video",
            video_url=video_url,
            title=title,
            display_order=order
        )

    db.add(media)
    db.commit()
    db.refresh(media)
    db.close()

    return {
        "id": media.id,
        "artist_id": media.artist_id,
        "media_type": media.media_type,
        "title": media.title,
        "file_path": media.file_path,
        "video_url": media.video_url,
        "display_order": media.display_order,
    }

# -----------------------------------------
# UPDATE MEDIA
# -----------------------------------------
@router.put("/api/media/{media_id}")
def update_media(media_id: int, data: dict, user=Depends(get_current_user)):
    db = SessionLocal()

    m = (
        db.query(ArtistMedia).filter(ArtistMedia.id == media_id).first()
        or
        db.query(VenueMedia).filter(VenueMedia.id == media_id).first()
    )

    if not m:
        db.close()
        raise HTTPException(404)

    # Verify ownership
    if isinstance(m, ArtistMedia):
        if not user_can_access_artist(db, m.artist_id, user.id):
            db.close()
            raise HTTPException(403, "Access denied")
    else:
        if not user_can_access_venue(db, m.venue_id, user.id):
            db.close()
            raise HTTPException(403, "Access denied")

    for k, v in data.items():
        if hasattr(m, k):
            setattr(m, k, v)

    db.commit()
    db.close()
    return {"ok": True}

@router.put("/api/venues/media/{media_id}")
def update_venue_media(media_id: int, data: dict, user=Depends(get_current_user)):
    db = SessionLocal()

    m = db.query(VenueMedia).filter(VenueMedia.id == media_id).first()

    if not m:
        db.close()
        raise HTTPException(404)

    if not user_can_access_venue(db, m.venue_id, user.id):
        db.close()
        raise HTTPException(403, "Access denied")

    for k, v in data.items():
        if hasattr(m, k):
            setattr(m, k, v)

    db.commit()
    db.close()
    return {"ok": True}

@router.delete("/api/venues/media/{media_id}")
def delete_venue_media(media_id: int, user=Depends(get_current_user)):
    db = SessionLocal()

    m = db.query(VenueMedia).filter(VenueMedia.id == media_id).first()

    if not m:
        db.close()
        raise HTTPException(404)

    if not user_can_access_venue(db, m.venue_id, user.id):
        db.close()
        raise HTTPException(403, "Access denied")

    # Delete file from disk if it exists
    if m.file_path:
        file_on_disk = m.file_path.lstrip("/")
        if os.path.exists(file_on_disk):
            os.remove(file_on_disk)

    db.delete(m)
    db.commit()
    db.close()
    return {"ok": True}

# -----------------------------------------
# DELETE MEDIA
# -----------------------------------------
@router.delete("/api/media/{media_id}")
def delete_media(media_id: int, user=Depends(get_current_user)):
    db = SessionLocal()

    m = (
        db.query(ArtistMedia).filter(ArtistMedia.id == media_id).first()
        or
        db.query(VenueMedia).filter(VenueMedia.id == media_id).first()
    )

    if not m:
        db.close()
        raise HTTPException(404)

    # Verify ownership
    if isinstance(m, ArtistMedia):
        if not user_can_access_artist(db, m.artist_id, user.id):
            db.close()
            raise HTTPException(403, "Access denied")
    else:
        if not user_can_access_venue(db, m.venue_id, user.id):
            db.close()
            raise HTTPException(403, "Access denied")

    # Delete file from disk if it exists
    if m.file_path:
        file_on_disk = m.file_path.lstrip("/")
        if os.path.exists(file_on_disk):
            os.remove(file_on_disk)

    db.delete(m)
    db.commit()
    db.close()
    return {"ok": True}

@router.get("/api/venues/{venue_id}/media")
def get_venue_media(venue_id: int):
    db = SessionLocal()

    rows = (
        db.query(VenueMedia)   # ✅ CORRECT
        .filter(VenueMedia.venue_id == venue_id)
        .order_by(VenueMedia.display_order)
        .all()
    )

    db.close()

    return [
        {
            "id": m.id,
            "venue_id": m.venue_id,
            "media_type": m.media_type,
            "title": m.title,
            "file_path": m.file_path,
            "video_url": m.video_url,
            "display_order": m.display_order,
        }
        for m in rows
    ]

@router.post("/api/venues/{venue_id}/media/{media_type}")
def upload_venue_media(
    venue_id: int,
    media_type: str,
    file: UploadFile | None = File(None),
    video_url: str | None = Form(None),
    title: str | None = Form(None),
    session_token: str | None = Cookie(default=None)
):
    if not session_token:
        raise HTTPException(401, "Not logged in")
    
    user_id = verify_session_token(session_token)

    if media_type not in ["profile", "picture", "video"]:
        raise HTTPException(400)

    db = SessionLocal()

    # Check ownership OR entity_users access
    if not user_can_access_venue(db, venue_id, user_id):
        db.close()
        raise HTTPException(403, "You don't have access to this venue")

    order = (
        db.query(VenueMedia)
        .filter(VenueMedia.venue_id == venue_id)
        .count()
    )

    if media_type != "video":
        # Validate file: extension whitelist, MIME type, size limit
        ext = validate_upload(file, media_type)

        folder = f"app/static/uploads/venue/{venue_id}/{media_type}"
        os.makedirs(folder, exist_ok=True)

        filename = f"{uuid.uuid4()}.{ext}"
        path = f"{folder}/{filename}"

        with open(path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        if media_type == "profile":
            # Delete old profile file(s) from disk before replacing
            old_profiles = db.query(VenueMedia).filter(
                VenueMedia.venue_id == venue_id,
                VenueMedia.media_type == "profile"
            ).all()
            for old in old_profiles:
                if old.file_path:
                    old_file = old.file_path.lstrip("/")
                    if os.path.exists(old_file):
                        os.remove(old_file)
            db.query(VenueMedia).filter(
                VenueMedia.venue_id == venue_id,
                VenueMedia.media_type == "profile"
            ).delete()

        media = VenueMedia(
            venue_id=venue_id,
            media_type=media_type,
            file_path=f"/{path}",
            title=title,
            display_order=order
        )

    else:
        media = VenueMedia(
            venue_id=venue_id,
            media_type="video",
            video_url=video_url,
            title=title,
            display_order=order
        )

    db.add(media)
    db.commit()
    db.refresh(media)
    db.close()

    return {"ok": True}


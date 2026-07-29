from fastapi import APIRouter, UploadFile, File, Cookie, HTTPException, Form, Depends
from backend.db import SessionLocal, get_db
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

# Allowed file extensions by media type.
# Audio uploads are MP3-only — other formats (wav, m4a, ogg, flac, aac) are
# generally larger and not consistently supported by the HTML <audio> element
# across browsers. Artists with non-MP3 files can either convert to MP3 or
# link the file via the "Add Audio Link" field (SoundCloud / Bandcamp / etc.).
ALLOWED_EXTENSIONS = {
    "profile": {"jpg", "jpeg", "png", "gif", "webp"},
    "picture": {"jpg", "jpeg", "png", "gif", "webp"},
    "audio":   {"mp3"},
    "video":   set(),  # Video is URL-based, no file upload
}

# MIME type whitelist
ALLOWED_MIME_TYPES = {
    # Images
    "image/jpeg", "image/png", "image/gif", "image/webp",
    # Audio — MP3 only. (Some browsers send audio/mpeg, others audio/mp3.)
    "audio/mpeg", "audio/mp3",
}

# Max file sizes in bytes
MAX_FILE_SIZES = {
    "profile": 10 * 1024 * 1024,   # 10 MB
    "picture": 10 * 1024 * 1024,   # 10 MB
    # 5 MB is enough for a ~5-minute 128 kbps MP3 demo clip; we surface this
    # cap to artists in the upload UI so they encode appropriately rather than
    # uploading a 30-MB master and getting a server rejection.
    "audio":   5  * 1024 * 1024,   # 5 MB
}


def validate_upload(file: UploadFile, media_type: str):
    """Validate an uploaded file for extension, MIME type, size, AND magic bytes.

    Audit fix (Jun 2026): added magic-byte verification. The extension
    and Content-Type are both client-controlled — without inspecting
    bytes, a renamed binary (or .html) could be saved with an .png/.mp3
    extension and served from the uploads directory. Same-origin XSS
    risk closed for images; the audio path now confirms an MP3 frame
    header is present.
    """
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

    # Magic-byte sniff. Read the first 16 bytes, then seek back so the
    # caller can re-read the full file (validate_upload returns the ext
    # and the upload site does shutil.copyfileobj from file.file).
    head = file.file.read(16)
    file.file.seek(0)
    _IMG_MAGIC = {
        "png":  [b"\x89PNG\r\n\x1a\n"],
        "jpg":  [b"\xff\xd8\xff"],
        "jpeg": [b"\xff\xd8\xff"],
        "gif":  [b"GIF87a", b"GIF89a"],
        # webp is RIFF + "WEBP" at offset 8 — RIFF alone would also
        # match .wav / .avi so we require both.
        "webp": None,
    }
    if ext in _IMG_MAGIC:
        if ext == "webp":
            ok = head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP"
        else:
            ok = any(head.startswith(m) for m in _IMG_MAGIC[ext])
        if not ok:
            raise HTTPException(400, "File content doesn't match its extension")
    elif ext == "mp3":
        # MP3: either an ID3 tag header ("ID3") or an MPEG frame sync
        # (0xFF followed by 0xFB / 0xFA / 0xF3 / 0xF2 — common MPEG-1/2
        # Layer III headers). Anything else is rejected.
        ok = (
            head.startswith(b"ID3")
            or (len(head) >= 2 and head[0] == 0xFF and head[1] in (0xFB, 0xFA, 0xF3, 0xF2, 0xE3, 0xE2))
        )
        if not ok:
            raise HTTPException(400, "File content doesn't match its extension")

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
def get_artist_media(artist_id: int, db=Depends(get_db)):
    # Audit fix (Jul 1 2026): swapped to Depends(get_db) which handles
    # teardown via try/finally in db.py:get_db — the previous manual
    # `db = SessionLocal() ... db.close()` pattern leaked the session
    # on any raise before .close() (validation error, ORM error, etc).
    rows = (
        db.query(ArtistMedia)
        .filter(ArtistMedia.artist_id == artist_id)
        .order_by(ArtistMedia.display_order)
        .all()
    )
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
            "caption": m.caption,
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
    user=Depends(get_current_user),
    db=Depends(get_db),  # Audit fix (Jul 1 2026): teardown via get_db.
):
    # BUG FIX (Jul 2026 audit): switched from raw verify_session_token to
    # get_current_user Depends so password-rotation session invalidation
    # (auth._reject_if_password_rotated) fires here. Previously any stolen
    # session token remained valid for artist media uploads even after the
    # owner changed their password.
    user_id = user.id

    if media_type not in ["profile", "picture", "audio", "video", "audio_link"]:
        raise HTTPException(400, "Invalid media type")

    # Check ownership OR entity_users access
    if not user_can_access_artist(db, artist_id, user_id):
        raise HTTPException(403, "You don't have access to this artist")

    # MP3 cap: max 3 audio file uploads per artist (audio_link URLs uncapped)
    if media_type == "audio":
        existing_audio = (
            db.query(ArtistMedia)
            .filter(ArtistMedia.artist_id == artist_id,
                    ArtistMedia.media_type == "audio")
            .count()
        )
        if existing_audio >= 3:
            raise HTTPException(
                400,
                "You've reached the 3 MP3 file limit. Delete an existing MP3 "
                "or add a link to external audio (SoundCloud, etc.) instead."
            )

    order = (
        db.query(ArtistMedia)
        .filter(ArtistMedia.artist_id == artist_id)
        .count()
    )

    # PROFILE / PICTURE / AUDIO (file-based)
    if media_type not in ("video", "audio_link"):
        # Validate file: extension whitelist, MIME type, size limit
        ext = validate_upload(file, media_type)

        folder = artist_media_path(artist_id, media_type)
        os.makedirs(folder, exist_ok=True)

        filename = f"{uuid.uuid4()}.{ext}"
        path = f"{folder}/{filename}"

        # For image uploads: read into memory, downscale + strip EXIF,
        # then write the processed bytes. For audio: stream directly to
        # disk (audio doesn't get resized).
        if media_type in ("profile", "picture") and ext in ("png", "jpg", "jpeg", "gif", "webp"):
            file.file.seek(0)
            raw = file.file.read()
            from backend.services.image_resize import resize_if_needed
            processed = resize_if_needed(raw, ext)
            with open(path, "wb") as buffer:
                buffer.write(processed)
        else:
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

    # VIDEO / AUDIO_LINK (URL-based; both use the video_url column)
    else:
        if not video_url:
            raise HTTPException(400, "video_url required")

        media = ArtistMedia(
            artist_id=artist_id,
            media_type=media_type,
            video_url=video_url,
            title=title,
            display_order=order
        )

    db.add(media)
    db.commit()
    db.refresh(media)

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
def update_media(media_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    m = (
        db.query(ArtistMedia).filter(ArtistMedia.id == media_id).first()
        or
        db.query(VenueMedia).filter(VenueMedia.id == media_id).first()
    )
    if not m:
        raise HTTPException(404)
    # Verify ownership
    if isinstance(m, ArtistMedia):
        if not user_can_access_artist(db, m.artist_id, user.id):
            raise HTTPException(403, "Access denied")
    else:
        if not user_can_access_venue(db, m.venue_id, user.id):
            raise HTTPException(403, "Access denied")
    # Audit fix (Jul 2026 full-site audit): explicit allowlist. Previous
    # `setattr(m, k, v) for k in data if hasattr(m, k)` let a caller
    # overwrite `file_path` / `artist_id` / `venue_id` on media they
    # already owned — then the DELETE handler would `os.remove()` any
    # server file the app process can write, and moving `artist_id`
    # planted content on a victim entity's profile.
    _EDITABLE = {"title", "caption", "display_order", "video_url", "media_type"}
    for k in _EDITABLE:
        if k in data and hasattr(m, k):
            setattr(m, k, data[k])
    db.commit()
    return {"ok": True}

@router.put("/api/venues/media/{media_id}")
def update_venue_media(media_id: int, data: dict, user=Depends(get_current_user), db=Depends(get_db)):
    m = db.query(VenueMedia).filter(VenueMedia.id == media_id).first()
    if not m:
        raise HTTPException(404)
    if not user_can_access_venue(db, m.venue_id, user.id):
        raise HTTPException(403, "Access denied")
    # Same allowlist as /api/media/{id} — see comment there.
    _EDITABLE = {"title", "caption", "display_order", "video_url", "media_type"}
    for k in _EDITABLE:
        if k in data and hasattr(m, k):
            setattr(m, k, data[k])
    db.commit()
    return {"ok": True}

@router.delete("/api/venues/media/{media_id}")
def delete_venue_media(media_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    m = db.query(VenueMedia).filter(VenueMedia.id == media_id).first()
    if not m:
        raise HTTPException(404)
    if not user_can_access_venue(db, m.venue_id, user.id):
        raise HTTPException(403, "Access denied")
    # Delete file from disk if it exists
    if m.file_path:
        file_on_disk = m.file_path.lstrip("/")
        if os.path.exists(file_on_disk):
            os.remove(file_on_disk)
    db.delete(m)
    db.commit()
    return {"ok": True}

# -----------------------------------------
# DELETE MEDIA
# -----------------------------------------
@router.delete("/api/media/{media_id}")
def delete_media(media_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    m = (
        db.query(ArtistMedia).filter(ArtistMedia.id == media_id).first()
        or
        db.query(VenueMedia).filter(VenueMedia.id == media_id).first()
    )
    if not m:
        raise HTTPException(404)
    # Verify ownership
    if isinstance(m, ArtistMedia):
        if not user_can_access_artist(db, m.artist_id, user.id):
            raise HTTPException(403, "Access denied")
    else:
        if not user_can_access_venue(db, m.venue_id, user.id):
            raise HTTPException(403, "Access denied")
    # Delete file from disk if it exists
    if m.file_path:
        file_on_disk = m.file_path.lstrip("/")
        if os.path.exists(file_on_disk):
            os.remove(file_on_disk)
    db.delete(m)
    db.commit()
    return {"ok": True}

@router.get("/api/venues/{venue_id}/media")
def get_venue_media(venue_id: int, db=Depends(get_db)):
    rows = (
        db.query(VenueMedia)
        .filter(VenueMedia.venue_id == venue_id)
        .order_by(VenueMedia.display_order)
        .all()
    )
    return [
        {
            "id": m.id,
            "venue_id": m.venue_id,
            "media_type": m.media_type,
            "title": m.title,
            "file_path": m.file_path,
            "video_url": m.video_url,
            "display_order": m.display_order,
            "caption": m.caption,
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
    user=Depends(get_current_user),
    db=Depends(get_db),  # Audit fix (Jul 1 2026): teardown via get_db.
):
    # BUG FIX (Jul 2026 audit): same fix as upload_media — use get_current_user
    # so password-rotation session invalidation is applied.
    user_id = user.id

    if media_type not in ["profile", "picture", "video"]:
        raise HTTPException(400)

    # Check ownership OR entity_users access
    if not user_can_access_venue(db, venue_id, user_id):
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

        if media_type in ("profile", "picture") and ext in ("png", "jpg", "jpeg", "gif", "webp"):
            file.file.seek(0)
            raw = file.file.read()
            from backend.services.image_resize import resize_if_needed
            processed = resize_if_needed(raw, ext)
            with open(path, "wb") as buffer:
                buffer.write(processed)
        else:
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
    return {"ok": True}


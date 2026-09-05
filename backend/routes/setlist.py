"""
Artist setlist routes.

Public read + owner-only write. Backs the "Setlist" tab on
artist-profile.html and the setlist editor on artist-edit.html.

Schema (see backend/db.py):
    artist_setlist(id, artist_id, song_title, original_artist,
                   display_order, created_at)

Bulk paste: /bulk accepts a big text blob and parses one song per
line using a handful of common separators (tab, ' - ', ' — ',
' by ', ' | ', ','). Lines with no separator are stored as
title-only rows (empty original_artist).
"""

import re
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.db import get_db
from backend.routes.auth import get_current_user
from backend.utils import check_artist_access

logger = logging.getLogger(__name__)
router = APIRouter()

# Cap the set to avoid abuse / runaway pastes. 1000 songs covers even
# the deepest cover-band songbook comfortably.
_MAX_SONGS_PER_ARTIST = 1000
_MAX_TITLE_LEN = 200
_MAX_ARTIST_LEN = 200


class SongIn(BaseModel):
    song_title: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)
    original_artist: Optional[str] = Field(default="", max_length=_MAX_ARTIST_LEN)


class BulkIn(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)  # ~200 KB of paste room


class ReorderIn(BaseModel):
    ids: List[int]


# Parse "Song Title - Artist" style lines. Tried in this order so
# tab-separated (Excel copy-paste) beats a hyphen inside a song title.
# ` — ` (em dash) and ` – ` (en dash) come before ` - ` so a title
# containing "-" doesn't get sliced.
_SEPARATORS = ["\t", " — ", " – ", " -- ", " by ", " | ", " - ", ", ", ","]


# Common header labels for a "Songs" column and an "Artist" column in
# spreadsheets. If the FIRST non-blank pasted line parses to
# {title in _HEADER_TITLE_WORDS, artist in _HEADER_ARTIST_WORDS} it's
# almost certainly a header row copied along with the data — skip it
# silently rather than adding a bogus song named "Song" by "Artist".
# Kept narrow (both cells must be a known header word) so we don't
# ever eat a real song that happens to be titled "Song" or "Track".
_HEADER_TITLE_WORDS = {
    "song", "songs", "title", "titles", "track", "tracks",
    "name", "song title", "song name", "tune",
}
_HEADER_ARTIST_WORDS = {
    "artist", "artists", "band", "bands", "musician", "musicians",
    "performer", "performers", "original artist", "original", "by",
    "cover", "cover of", "author", "composer", "writer",
}


def _looks_like_header(title: str, artist: str) -> bool:
    if not title or not artist:
        return False
    return title.strip().lower() in _HEADER_TITLE_WORDS \
       and artist.strip().lower() in _HEADER_ARTIST_WORDS


def _parse_bulk_line(line: str) -> Optional[dict]:
    """Turn one pasted line into {song_title, original_artist} or None
    if the line is blank / a comment."""
    s = line.strip()
    if not s:
        return None
    # Strip a leading list number/bullet if present ("1. ", "12) ",
    # "- ", "* ") so pasting a numbered list works naturally.
    s = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", s)
    if not s:
        return None
    # Tab is the special case: Excel copies use tab between cells, and
    # a spreadsheet row with 3+ columns (Title | Artist | Year | Genre)
    # should still land as {title, artist} — extra columns dropped, not
    # concatenated into the artist field. partition() would only cut
    # the FIRST tab and jam the rest into the artist. So split fully
    # on tab and take the first two cells.
    if "\t" in s:
        parts = [p.strip().strip('"').strip("'") for p in s.split("\t")]
        title = parts[0]
        artist = parts[1] if len(parts) > 1 else ""
        if title:
            return {
                "song_title": title[:_MAX_TITLE_LEN],
                "original_artist": artist[:_MAX_ARTIST_LEN],
            }
        return None
    # Non-tab separators: partition on first hit (they're likely to
    # appear inside titles too — e.g. a comma in "Man, I Feel Like a
    # Woman" — so we only cut at the FIRST occurrence).
    for sep in _SEPARATORS:
        if sep == "\t":
            continue  # already handled above
        if sep in s:
            title, _, artist = s.partition(sep)
            title = title.strip().strip('"').strip("'")
            artist = artist.strip().strip('"').strip("'")
            if title:
                return {
                    "song_title": title[:_MAX_TITLE_LEN],
                    "original_artist": artist[:_MAX_ARTIST_LEN],
                }
    # No separator found — treat whole line as title-only.
    title = s.strip('"').strip("'")
    return {
        "song_title": title[:_MAX_TITLE_LEN],
        "original_artist": "",
    }


def _next_order(db, artist_id: int) -> int:
    """Return the next display_order value to append at."""
    row = db.execute(text(
        "SELECT COALESCE(MAX(display_order), -1) + 1 FROM artist_setlist WHERE artist_id = :aid"
    ), {"aid": artist_id}).scalar()
    return int(row or 0)


def _count(db, artist_id: int) -> int:
    return int(db.execute(text(
        "SELECT COUNT(*) FROM artist_setlist WHERE artist_id = :aid"
    ), {"aid": artist_id}).scalar() or 0)


@router.get("/api/artists/{artist_id}/setlist")
def get_setlist(artist_id: int, db=Depends(get_db)):
    """Public — anyone can view an artist's setlist."""
    rows = db.execute(text("""
        SELECT id, song_title, original_artist, display_order
        FROM artist_setlist
        WHERE artist_id = :aid
        ORDER BY display_order ASC, id ASC
    """), {"aid": artist_id}).mappings().all()
    return {"songs": [dict(r) for r in rows], "total": len(rows)}


@router.post("/api/artists/{artist_id}/setlist")
def add_song(artist_id: int, data: SongIn,
             user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    if _count(db, artist_id) >= _MAX_SONGS_PER_ARTIST:
        raise HTTPException(400, f"Setlist is at the {_MAX_SONGS_PER_ARTIST}-song cap. Delete something first.")
    new_id = db.execute(text("""
        INSERT INTO artist_setlist (artist_id, song_title, original_artist, display_order)
        VALUES (:aid, :t, :a, :o) RETURNING id
    """), {
        "aid": artist_id,
        "t": data.song_title.strip(),
        "a": (data.original_artist or "").strip(),
        "o": _next_order(db, artist_id),
    }).scalar()
    db.commit()
    return {"ok": True, "id": new_id}


@router.post("/api/artists/{artist_id}/setlist/bulk")
def add_bulk(artist_id: int, data: BulkIn,
             user=Depends(get_current_user), db=Depends(get_db)):
    """Parse a pasted block of text into songs and append them all.
    Returns the count added + any lines that couldn't be parsed."""
    check_artist_access(db, artist_id, user.id)

    existing = _count(db, artist_id)
    room = _MAX_SONGS_PER_ARTIST - existing
    if room <= 0:
        raise HTTPException(400, f"Setlist is at the {_MAX_SONGS_PER_ARTIST}-song cap.")

    parsed = []
    header_skipped = False
    seen_first = False
    for line in data.text.splitlines():
        song = _parse_bulk_line(line)
        if not song:
            continue
        # If the very first parsed row looks like a spreadsheet header
        # (e.g. "Song\tArtist", "Title\tBand", "Track\tPerformer"),
        # drop it silently. Only the FIRST row is eligible so mid-list
        # accidents never eat real songs.
        if not seen_first:
            seen_first = True
            if _looks_like_header(song.get("song_title", ""), song.get("original_artist", "")):
                header_skipped = True
                continue
        parsed.append(song)
        if len(parsed) >= room:
            break

    if not parsed:
        raise HTTPException(400, "Couldn't find any songs in the pasted text.")

    start_order = _next_order(db, artist_id)
    for i, s in enumerate(parsed):
        db.execute(text("""
            INSERT INTO artist_setlist (artist_id, song_title, original_artist, display_order)
            VALUES (:aid, :t, :a, :o)
        """), {
            "aid": artist_id,
            "t": s["song_title"],
            "a": s["original_artist"],
            "o": start_order + i,
        })
    db.commit()

    truncated = (existing + len(parsed)) >= _MAX_SONGS_PER_ARTIST and \
                len(parsed) < len([l for l in data.text.splitlines() if l.strip()])
    return {
        "ok": True,
        "added": len(parsed),
        "total": existing + len(parsed),
        "truncated_at_cap": truncated,
        "header_row_skipped": header_skipped,
    }


@router.put("/api/artists/{artist_id}/setlist/reorder")
def reorder(artist_id: int, data: ReorderIn,
            user=Depends(get_current_user), db=Depends(get_db)):
    """Accept the full ordered list of song ids and rewrite
    display_order to match. Any id not in the list is left alone at
    the end (defensive — shouldn't happen if the frontend sends the
    complete list, but avoids losing songs on a partial payload)."""
    check_artist_access(db, artist_id, user.id)
    if not data.ids:
        return {"ok": True, "reordered": 0}
    # Only reorder ids that actually belong to this artist. Filters
    # out anything a malicious client could try to slip in from
    # another artist.
    placeholders = ",".join(f":i{i}" for i in range(len(data.ids)))
    params = {f"i{i}": v for i, v in enumerate(data.ids)}
    params["aid"] = artist_id
    owned = db.execute(text(
        f"SELECT id FROM artist_setlist WHERE artist_id = :aid AND id IN ({placeholders})"
    ), params).scalars().all()
    owned_set = {int(x) for x in owned}
    for pos, sid in enumerate(data.ids):
        if int(sid) not in owned_set:
            continue
        db.execute(text(
            "UPDATE artist_setlist SET display_order = :o WHERE id = :id AND artist_id = :aid"
        ), {"o": pos, "id": int(sid), "aid": artist_id})
    db.commit()
    return {"ok": True, "reordered": len(owned_set)}


@router.put("/api/artists/{artist_id}/setlist/{song_id}")
def update_song(artist_id: int, song_id: int, data: SongIn,
                user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    row = db.execute(text(
        "SELECT id FROM artist_setlist WHERE id = :sid AND artist_id = :aid"
    ), {"sid": song_id, "aid": artist_id}).first()
    if not row:
        raise HTTPException(404, "Song not found on this artist")
    db.execute(text("""
        UPDATE artist_setlist
           SET song_title = :t, original_artist = :a
         WHERE id = :sid
    """), {
        "t": data.song_title.strip(),
        "a": (data.original_artist or "").strip(),
        "sid": song_id,
    })
    db.commit()
    return {"ok": True}


@router.delete("/api/artists/{artist_id}/setlist/{song_id}")
def delete_song(artist_id: int, song_id: int,
                user=Depends(get_current_user), db=Depends(get_db)):
    check_artist_access(db, artist_id, user.id)
    row = db.execute(text(
        "SELECT id FROM artist_setlist WHERE id = :sid AND artist_id = :aid"
    ), {"sid": song_id, "aid": artist_id}).first()
    if not row:
        raise HTTPException(404, "Song not found on this artist")
    db.execute(text("DELETE FROM artist_setlist WHERE id = :sid"), {"sid": song_id})
    db.commit()
    return {"ok": True}


@router.delete("/api/artists/{artist_id}/setlist")
def clear_setlist(artist_id: int,
                  user=Depends(get_current_user), db=Depends(get_db)):
    """Wipe the whole setlist — used by the 'Clear all' button on the
    edit page. Confirmed by the frontend before firing."""
    check_artist_access(db, artist_id, user.id)
    n = db.execute(text(
        "DELETE FROM artist_setlist WHERE artist_id = :aid"
    ), {"aid": artist_id}).rowcount
    db.commit()
    return {"ok": True, "deleted": n or 0}
